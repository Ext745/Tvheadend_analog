"""`ntsc-tvh stream` as a supervisor, with real processes and real signals (Linux only).

The script runs in a subprocess with a fake ffmpeg (tests/fakebin/ffmpeg) that can ignore
SIGTERM like ffmpeg 6.1 does on an em28xx ALSA input, a fake v4l2-ctl that answers from the
fixtures, and the real `cat` reading /dev/zero (which, like a capture node, never ends).
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import REPO, SCRIPT, ntsc

FAKEBIN = os.path.join(REPO, "tests", "fakebin")


def alive(pid):
    """True while `pid` exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def children(ppid):
    """[(pid, comm)] of live processes whose parent is `ppid`."""
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat.rsplit(")", 1)[1].split()
        if int(fields[1]) == ppid and fields[0] != "Z":
            out.append((int(name), comm))
    return out


def wait_until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc and PR_SET_PDEATHSIG")
class Supervisor(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "ffmpeg.json")
        self.lock = os.path.join(self.tmp.name, "state", "ntsc-tvh.lock")
        self.env = {k: v for k, v in os.environ.items() if k not in ntsc.DEFAULTS}
        self.env.update(
            STATE_FILE=os.path.join(self.tmp.name, "state", "state.json"),
            M3U_FILE=os.path.join(self.tmp.name, "state", "ntsc.m3u"),
            LOCK_FILE=self.lock,
            DEVICE="/dev/zero",
            FFMPEG=os.path.join(FAKEBIN, "ffmpeg"),
            V4L2CTL=os.path.join(FAKEBIN, "v4l2-ctl"),
            RAW_AUDIO_DEV="hw:9,0",
            STOP_GRACE="0.5",
            SCAN_LOCK_WAIT="0",
            FAKE_LOG=self.log,
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.pids = []
        self.addCleanup(self.kill_leftovers)

    def kill_leftovers(self):
        for pid in self.pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

    def start(self, channel="us-bcast:7", ffmpeg="stubborn", card="hvr1950", **env):
        self.env.update(FAKE_FFMPEG=ffmpeg, FAKE_CARD=card, **env)
        proc = subprocess.Popen([sys.executable, SCRIPT, "stream", channel], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stderr.close)
        self.pids.append(proc.pid)
        if not wait_until(lambda: os.path.exists(self.log) or proc.poll() is not None):
            self.fail("fake ffmpeg never started")
        if proc.poll() is not None and not os.path.exists(self.log):
            self.fail(f"stream exited early: {proc.stderr.read().decode()}")
        with open(self.log) as fh:
            self.ff = json.load(fh)
        self.pids.append(self.ff["pid"])
        # the fake ffmpeg runs as "python3"; name it by the pid it logged
        self.kids = {pid: "ffmpeg" if pid == self.ff["pid"] else comm
                     for pid, comm in children(proc.pid)}
        self.pids.extend(self.kids)
        return proc

    def signal(self, proc, sig):
        t0 = time.monotonic()
        proc.send_signal(sig)
        rc = proc.wait(timeout=10)
        return rc, time.monotonic() - t0

    def lock_is_held(self):
        try:
            with ntsc.TunerLock(self.lock, wait=0):
                return False
        except ntsc.ChannelError:
            return True

    def assert_all_gone(self):
        for pid in self.kids:
            self.assertTrue(wait_until(lambda: not alive(pid), 3), f"child {pid} survived")
        self.assertFalse(self.lock_is_held())

    def test_stream_reaches_stdout_and_holds_the_lock(self):
        proc = self.start(ffmpeg="polite")
        self.assertTrue(self.lock_is_held())
        self.signal(proc, signal.SIGTERM)
        self.assertEqual(proc.stdout.read(), b"FAKE-TS")

    def test_sigterm_kills_an_ffmpeg_that_ignores_it(self):
        proc = self.start(ffmpeg="stubborn")
        rc, elapsed = self.signal(proc, signal.SIGTERM)
        self.assertEqual(rc, 128 + signal.SIGTERM)
        self.assertLess(elapsed, 0.5 + 1.5)        # STOP_GRACE + slack, not forever
        self.assertGreaterEqual(elapsed, 0.4)      # it did get its grace period
        self.assert_all_gone()

    def test_polite_ffmpeg_exits_without_waiting_for_grace(self):
        self.env["STOP_GRACE"] = "5"
        proc = self.start(ffmpeg="polite")
        rc, elapsed = self.signal(proc, signal.SIGTERM)
        self.assertEqual(rc, 128 + signal.SIGTERM)
        self.assertLess(elapsed, 2)
        self.assert_all_gone()

    def test_sigint_and_sighup(self):
        for sig in (signal.SIGINT, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                if os.path.exists(self.log):
                    os.remove(self.log)
                proc = self.start(ffmpeg="stubborn")
                rc, _ = self.signal(proc, sig)
                self.assertEqual(rc, 128 + sig)
                self.assert_all_gone()

    def test_sigkill_of_supervisor_takes_children_with_it(self):
        # TVHeadend's last resort. PR_SET_PDEATHSIG makes the kernel kill ffmpeg and cat.
        proc = self.start(ffmpeg="stubborn")
        self.assertIn("cat", self.kids.values())
        proc.kill()
        proc.wait(timeout=5)
        self.assert_all_gone()

    def test_ffmpeg_exiting_on_its_own_ends_the_stream(self):
        proc = self.start(ffmpeg="exit3")
        self.assertEqual(proc.wait(timeout=5), 3)
        self.assert_all_gone()

    def test_cat_feeds_ffmpeg_and_only_ffmpeg_inherits_the_lock(self):
        proc = self.start(ffmpeg="stubborn")
        self.assertEqual(sorted(self.kids.values()), ["cat", "ffmpeg"])
        self.assertIn("pipe:0", self.ff["argv"])
        self.assertGreater(self.ff["stdin_bytes"], 0)                 # /dev/zero via cat
        self.assertIn(self.lock, self.ff["fds"].values())
        (cat,) = [pid for pid, comm in self.kids.items() if comm == "cat"]
        cat_fds = [os.readlink(f"/proc/{cat}/fd/{fd}") for fd in os.listdir(f"/proc/{cat}/fd")]
        self.assertNotIn(self.lock, cat_fds)
        self.signal(proc, signal.SIGTERM)
        self.assert_all_gone()

    def test_raw_card_runs_ffmpeg_alone(self):
        proc = self.start("input:composite", ffmpeg="stubborn", card="800e")
        self.assertEqual(list(self.kids.values()), ["ffmpeg"])
        self.assertIn("v4l2", self.ff["argv"])
        self.assertIn("hw:9,0", self.ff["argv"])
        self.assertIsNone(self.ff["stdin_bytes"])
        rc, elapsed = self.signal(proc, signal.SIGTERM)
        self.assertLess(elapsed, 2)
        self.assert_all_gone()


if __name__ == "__main__":
    unittest.main()
