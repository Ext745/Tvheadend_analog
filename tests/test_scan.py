"""Scan: activity detection (signal / video / both), luma std-dev, and the scan -> state -> M3U flow."""

import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from helpers import FakeV4L2, fixture, make_cfg, ntsc

FRAME = 160 * 120
HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": []}


class VideoVariance(unittest.TestCase):

    def setUp(self):
        self.cat = mock.Mock()
        p = mock.patch("ntsc_tvh.subprocess.Popen", return_value=self.cat)
        self.popen = p.start()
        self.addCleanup(p.stop)

    def variance(self, data, info=HW, **cfg):
        result = subprocess.CompletedProcess([], 0, stdout=data, stderr=b"")
        with mock.patch("ntsc_tvh.subprocess.run", return_value=result) as run:
            v = ntsc.video_variance(make_cfg(**cfg), info)
        self.cmd = run.call_args[0][0]
        self.stdin = run.call_args[1]["stdin"]
        return v

    def test_flat_no_sync_screen_is_zero(self):
        self.assertEqual(self.variance(bytes([16]) * FRAME * 6), 0.0)

    def test_half_black_half_white(self):
        frame = bytes([0]) * (FRAME // 2) + bytes([255]) * (FRAME // 2)
        self.assertAlmostEqual(self.variance(frame), 127.5)

    def test_returns_max_over_frames_and_ignores_partial_tail(self):
        flat = bytes([128]) * FRAME
        busy = bytes([0, 255]) * (FRAME // 2)
        tail = bytes(range(256)) * 10
        self.assertAlmostEqual(self.variance(flat + busy + flat + tail), 127.5)

    def test_empty_capture_is_zero(self):
        self.assertEqual(self.variance(b""), 0.0)

    def test_timeout_is_zero_and_stops_cat(self):
        with mock.patch("ntsc_tvh.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 8)):
            self.assertEqual(ntsc.video_variance(make_cfg(), HW), 0.0)
        self.cat.kill.assert_called_once_with()
        self.cat.wait.assert_called_once_with()

    def test_capture_command(self):
        self.variance(b"")
        self.assertEqual(self.cmd[self.cmd.index("-vf") + 1], "fps=2,scale=160:120,format=gray")
        self.assertEqual(self.cmd[-5:], ["-frames:v", "6", "-f", "rawvideo", "pipe:1"])

    def test_hw_reads_via_cat_like_stream(self):
        # ffmpeg opening the MPEG node directly is empty on some builds (see CAPTURE_VIA_CAT).
        self.variance(b"")
        self.popen.assert_called_once_with(["cat", "/dev/video0"], stdout=subprocess.PIPE,
                                           preexec_fn=ntsc._die_with_parent)
        self.assertEqual(self.cmd[4:8], ["-f", "mpeg", "-i", "pipe:0"])
        self.assertIs(self.stdin, self.cat.stdout)
        self.cat.kill.assert_called_once_with()
        self.cat.wait.assert_called_once_with()
        self.cat.stdout.close.assert_called_once_with()

    def test_hw_direct_read_when_cat_disabled(self):
        self.variance(b"", CAPTURE_VIA_CAT="0")
        self.popen.assert_not_called()
        self.assertEqual(self.cmd[4:8], ["-f", "mpeg", "-i", "/dev/video0"])
        self.assertIsNone(self.stdin)

    def test_raw_device_reads_v4l2_directly(self):
        self.variance(b"", info=dict(HW, hw_encoder=False))
        self.popen.assert_not_called()
        self.assertEqual(self.cmd[4:8], ["-f", "v4l2", "-i", "/dev/video0"])


class IsActive(unittest.TestCase):

    def check(self, method, sig, var, thr=35, var_min=12):
        with mock.patch.object(ntsc, "get_signal", return_value=(sig, 5)), \
             mock.patch.object(ntsc, "video_variance", return_value=var) as vv:
            result = ntsc.is_active(make_cfg(), HW, None, method, thr, var_min)
        self.video_probed = vv.called
        return result

    def test_signal_method(self):
        self.assertEqual(self.check("signal", 35, 0), (True, {"signal": 35, "afc": 5}))
        self.assertEqual(self.check("signal", 34, 99)[0], False)
        self.assertEqual(self.check("signal", None, 99)[0], False)
        self.assertFalse(self.video_probed)

    def test_video_method(self):
        self.assertEqual(self.check("video", 0, 12.04),
                         (True, {"signal": 0, "afc": 5, "var": 12.0}))
        self.assertEqual(self.check("video", 100, 11.9)[0], False)

    def test_both_method(self):
        self.assertTrue(self.check("both", 80, 40)[0])
        self.assertFalse(self.check("both", 80, 3)[0])
        self.assertFalse(self.check("both", 10, 40)[0])


class CmdScan(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        locked = fixture("v4l2-get-tuner-locked.txt")   # 87 %
        self.fake = FakeV4L2(signal_for={67.25: locked, 175.25: locked, 217.25: locked})
        for target, kw in (("run", {"new": self.fake}), ("device_info", {"return_value": HW})):
            p = mock.patch.object(ntsc, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("ntsc_tvh.time.sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def scan(self, tables="us-bcast", method=None, threshold=None, dwell=None, wait=None,
             no_m3u=False, **cfg):
        self.cfg = make_cfg(self.tmp.name, **cfg)
        args = argparse.Namespace(tables=tables, method=method, threshold=threshold,
                                  dwell=dwell, wait=wait, no_m3u=no_m3u)
        self.err = io.StringIO()
        with redirect_stderr(self.err):
            ntsc.cmd_scan(self.cfg, args)
        with open(self.cfg["STATE_FILE"]) as fh:
            return json.load(fh)

    def test_finds_active_channels_and_writes_state_and_m3u(self):
        state = self.scan()
        self.assertEqual([c["id"] for c in state["active"]], ["us-bcast:4", "us-bcast:7"])
        self.assertEqual(state["active"][0]["signal"], 87)
        self.assertEqual(state["active"][0]["afc"], -12)
        self.assertEqual((state["device"], state["card"], state["method"], state["threshold"],
                          state["tables"]),
                         ("/dev/video0", HW["card"], "signal", 35, ["us-bcast"]))
        self.assertTrue(state["scanned_at"].endswith("+00:00"))
        with open(self.cfg["M3U_FILE"]) as fh:
            m3u = fh.read()
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh stream us-bcast:7\n", m3u)
        self.assertIn("Found 2 active channel(s)", self.err.getvalue())

    def test_tunes_every_channel_once_under_the_lock(self):
        self.scan()
        freqs = [o for o in self.fake.opts() if o.startswith("--set-freq=")]
        self.assertEqual(len(freqs), 68)
        self.assertEqual(freqs[0], "--set-freq=55.2500")
        self.assertEqual(self.sleep.call_count, 68)
        self.sleep.assert_called_with(1.2)

    def test_tables_from_config_and_args(self):
        state = self.scan(tables=None, SCAN_TABLES="us-cable, us-bcast")
        self.assertEqual(state["tables"], ["us-cable", "us-bcast"])
        self.assertEqual([c["id"] for c in state["active"]],
                         ["us-cable:4", "us-cable:7", "us-cable:23",
                          "us-bcast:4", "us-bcast:7"])

    def test_threshold_and_dwell_overrides(self):
        state = self.scan(threshold=90, dwell=0.1)
        self.assertEqual(state["active"], [])
        self.assertEqual(state["threshold"], 90)
        self.sleep.assert_called_with(0.1)

    def test_no_m3u(self):
        self.scan(no_m3u=True)
        self.assertFalse(os.path.exists(self.cfg["M3U_FILE"]))

    def test_unknown_table_exits(self):
        with self.assertRaises(SystemExit):
            self.scan(tables="us-bcast,eu-pal")
        self.assertEqual(self.fake.calls, [])

    def test_tuner_that_does_not_tune_aborts_and_keeps_previous_state(self):
        # Saving an empty scan here would rewrite the M3U without any channels.
        previous = self.scan()
        self.fake.stuck = True
        self.fake.freq = None
        with self.assertRaises(SystemExit) as cm:
            self.scan()
        self.assertIn("did not tune", str(cm.exception.code))
        with open(self.cfg["STATE_FILE"]) as fh:
            self.assertEqual(json.load(fh), previous)
        self.assertEqual(len([o for o in self.fake.opts() if o.startswith("--set-freq")]), 68 + 1)

    def test_busy_tuner_aborts_without_touching_state(self):
        cfg = make_cfg(self.tmp.name)
        with ntsc.TunerLock(cfg["LOCK_FILE"]):
            with self.assertRaises(SystemExit) as cm:
                self.scan(wait=0)
        self.assertIn("busy", str(cm.exception.code))
        self.assertFalse(os.path.exists(cfg["STATE_FILE"]))


class TunerLock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "new", "dir", "ntsc-tvh.lock")

    def test_creates_state_dir_and_is_exclusive(self):
        with ntsc.TunerLock(self.path):
            self.assertTrue(os.path.exists(self.path))
            with self.assertRaises(ntsc.ChannelError):
                with ntsc.TunerLock(self.path, wait=0):
                    pass
        with ntsc.TunerLock(self.path, wait=0):
            pass

    def test_waits_up_to_deadline(self):
        with ntsc.TunerLock(self.path):
            with mock.patch("ntsc_tvh.time.sleep") as sleep, \
                 mock.patch("ntsc_tvh.time.time", side_effect=[0.0, 0.5, 1.0, 1.5, 2.1]):
                with self.assertRaises(ntsc.ChannelError):
                    with ntsc.TunerLock(self.path, wait=2):
                        pass
        self.assertEqual(sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
