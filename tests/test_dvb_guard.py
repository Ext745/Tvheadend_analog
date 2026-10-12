"""DVB_GUARD: refuse analog use while the DVB side of the same hybrid tuner is busy.

Fake /sys mirrors a Pinnacle 800e (video0 and dvb0.* on USB interface 1-1:1.0). Fake /proc
fd links point at real character devices standing in for the DVB nodes -- /dev/null for
the frontend, /dev/zero for the demux, /dev/full for dvr -- so the real device-number
matching is exercised without hardware.
"""

import argparse
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FakeV4L2, make_cfg, ntsc

USB = "devices/pci0000:00/0000:00:14.0/usb1"
STAND_IN = {"frontend": "/dev/null", "demux": "/dev/zero", "dvr": "/dev/full"}


def devno(path):
    rdev = os.stat(path).st_rdev
    return f"{os.major(rdev)}:{os.minor(rdev)}"


@unittest.skipUnless(sys.platform.startswith("linux"), "uses Linux device numbers")
class DvbGuard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys")
        self.proc = os.path.join(self.tmp.name, "proc")
        self.dev = os.path.join(self.tmp.name, "dev")
        os.makedirs(self.proc)
        os.makedirs(self.dev)
        for target, value in (("SYS_ROOT", self.sys), ("PROC_ROOT", self.proc)):
            p = mock.patch.object(ntsc, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.video("video0", f"{USB}/1-1/1-1:1.0")
        self.adapter(0, f"{USB}/1-1/1-1:1.0")
        self.cfg = make_cfg(self.tmp.name, DEVICE=os.path.join(self.dev, "video0"))

    def link(self, cls, real):
        real = os.path.join(self.sys, real)
        os.makedirs(real, exist_ok=True)
        cls = os.path.join(self.sys, cls)
        os.makedirs(os.path.dirname(cls), exist_ok=True)
        os.symlink(real, cls)
        return real

    def video(self, node, parent):
        real = self.link(f"class/video4linux/{node}", f"{parent}/video4linux/{node}")
        os.symlink(os.path.join(self.sys, parent), os.path.join(real, "device"))
        open(os.path.join(self.dev, node), "w").close()

    def adapter(self, n, parent, nodes=STAND_IN):
        for kind, standin in nodes.items():
            real = self.link(f"class/dvb/dvb{n}.{kind}0", f"{parent}/dvb/dvb{n}.{kind}0")
            os.symlink(os.path.join(self.sys, parent), os.path.join(real, "device"))
            with open(os.path.join(real, "dev"), "w") as fh:
                fh.write(devno(standin) + "\n")

    def process(self, pid, comm, *kinds, raw=()):
        fd = os.path.join(self.proc, str(pid), "fd")
        os.makedirs(fd)
        with open(os.path.join(self.proc, str(pid), "comm"), "w") as fh:
            fh.write(comm + "\n")
        targets = [STAND_IN[k] for k in kinds] + list(raw)
        for i, target in enumerate(targets, start=3):
            os.symlink(target, os.path.join(fd, str(i)))

    def test_nothing_open(self):
        self.process(100, "bash")
        self.assertEqual(ntsc.dvb_users(self.cfg), {})
        ntsc.check_dvb_idle(self.cfg)

    def test_finds_users_by_device_number(self):
        self.process(200, "tvheadend", "frontend", "demux", "demux", "dvr")
        self.process(201, "dvbv5-zap", "frontend")
        self.assertEqual(ntsc.dvb_users(self.cfg), {
            "frontend": [(200, "tvheadend"), (201, "dvbv5-zap")],
            "demux": [(200, "tvheadend")],
            "dvr": [(200, "tvheadend")],
        })

    def test_idle_frontend_blocks_rf_and_line_inputs(self):
        # Seen live: an idle TVHeadend holding only frontend0 wedged the 800e on an RF retune,
        # and again on a composite capture.
        self.process(200, "tvheadend", "frontend")
        with self.assertRaisesRegex(ntsc.ChannelError, r"tvheadend \(pid 200\) has the DVB frontend open"):
            ntsc.check_dvb_idle(self.cfg)

    def test_active_reception_blocks(self):
        # Seen live: composite streaming during an ATSC EPG grab wedged the 800e.
        self.process(200, "tvheadend", "frontend", "demux", "dvr")
        with self.assertRaisesRegex(ntsc.ChannelError, "Power save"):
            ntsc.check_dvb_idle(self.cfg)

    def test_dvb_on_another_device_is_ignored(self):
        self.adapter(1, f"{USB}/1-2/1-2:1.0", nodes={"frontend": "/dev/null"})
        # only adapter1 (another stick) is "open": same stand-in device, so drop adapter0's
        # entry to keep the numbers unambiguous
        os.remove(os.path.join(self.sys, "class/dvb/dvb0.frontend0"))
        self.process(300, "tvheadend", "frontend")
        self.assertEqual(ntsc.dvb_users(self.cfg), {})
        ntsc.check_dvb_idle(self.cfg)

    def test_no_dvb_side_or_no_sysfs(self):
        self.process(200, "tvheadend", "frontend")
        with mock.patch.object(ntsc, "SYS_ROOT", os.path.join(self.tmp.name, "nowhere")):
            self.assertEqual(ntsc.dvb_users(self.cfg), {})
        shutil.rmtree(os.path.join(self.sys, "class", "dvb"))
        self.assertEqual(ntsc.dvb_users(self.cfg), {})

    def test_unreadable_processes_are_skipped(self):
        self.process(400, "other-user")
        os.chmod(os.path.join(self.proc, "400", "fd"), 0)
        self.addCleanup(os.chmod, os.path.join(self.proc, "400", "fd"), 0o755)
        self.process(401, "dangling", raw=[os.path.join(self.tmp.name, "gone")])
        self.assertEqual(ntsc.dvb_users(self.cfg), {})

    def test_guard_can_be_disabled(self):
        self.process(200, "tvheadend", "frontend", "demux", "dvr")
        ntsc.check_dvb_idle(dict(self.cfg, DVB_GUARD="0"))

    def test_tune_refuses_before_touching_the_device(self):
        self.process(200, "tvheadend", "frontend")
        fake = FakeV4L2()
        with mock.patch.object(ntsc, "run", fake):
            with self.assertRaises(ntsc.ChannelError):
                ntsc.tune(self.cfg, ntsc.parse_channel("us-bcast:7"))
            # Seen live: a composite capture with only frontend0 open wedged the 800e too.
            with self.assertRaises(ntsc.ChannelError):
                ntsc.tune(self.cfg, ntsc.parse_channel("input:composite"))
            self.assertEqual(fake.calls, [])

    def no_device_access(self):
        """Patch everything that would open the analog node; fail the test if it is used."""
        boom = mock.Mock(side_effect=AssertionError("opened the analog device"))
        return mock.patch.multiple(ntsc, device_info=boom, run=boom)

    def test_stream_and_scan_refuse_before_opening_the_analog_node(self):
        # Seen live: just running v4l2-ctl on /dev/video0 during ATSC reception switched
        # the em28xx to analog mode and broke the DVB stream.
        self.process(200, "tvheadend", "frontend", "demux", "dvr")
        with self.no_device_access():
            for channel in ("us-bcast:7", "input:composite"):
                with self.subTest(channel=channel), self.assertRaises(ntsc.ChannelError):
                    ntsc.cmd_stream(self.cfg, argparse.Namespace(channel=channel, wait=0))
            args = argparse.Namespace(tables="us-bcast", method="signal", threshold=None,
                                      dwell=0, wait=0, no_m3u=True)
            with self.assertRaises(ntsc.ChannelError):
                ntsc.cmd_scan(self.cfg, args)

    def test_doctor_skips_probe_while_dvb_is_open(self):
        for kinds in (("frontend",), ("frontend", "demux", "dvr")):
            with self.subTest(kinds=kinds):
                self.process(200, "tvheadend", *kinds)
                out = io.StringIO()
                with self.no_device_access(), redirect_stdout(out), self.assertRaises(SystemExit):
                    ntsc.cmd_doctor(self.cfg, argparse.Namespace())
                self.assertIn("[!!] DVB side of this tuner idle", out.getvalue())
                self.assertIn("device probe was skipped", out.getvalue())
                shutil.rmtree(os.path.join(self.proc, "200"))

    def test_scan_aborts_when_dvb_grabs_the_tuner_mid_scan(self):
        fake = FakeV4L2()
        calls = {"n": 0}
        real_check = ntsc.check_dvb_idle

        def check(cfg):
            calls["n"] += 1
            if calls["n"] == 5:   # check 1 is before the probe, 2.. before each channel:
                                  # TVHeadend starts an EPG grab before channel 4
                self.process(200, "tvheadend", "frontend", "demux")
            return real_check(cfg)

        args = argparse.Namespace(tables="us-bcast", method="signal", threshold=None,
                                  dwell=0, wait=0, no_m3u=False)
        with mock.patch.object(ntsc, "run", fake), \
             mock.patch.object(ntsc, "device_info", return_value={"card": "800e", "hw_encoder": False}), \
             mock.patch.object(ntsc, "check_dvb_idle", side_effect=check), \
             mock.patch("ntsc_tvh.time.sleep"), redirect_stdout(io.StringIO()), \
             mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                ntsc.cmd_scan(self.cfg, args)
        self.assertIn("DVB", str(cm.exception.code))
        self.assertEqual(len([o for o in fake.opts() if o.startswith("--set-freq")]), 3)
        self.assertFalse(os.path.exists(self.cfg["STATE_FILE"]))


if __name__ == "__main__":
    unittest.main()
