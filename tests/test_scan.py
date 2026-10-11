"""Scan: activity detection (signal / video / both), luma std-dev, and the scan -> state -> M3U flow."""

import argparse
import io
import json
import os
import random
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from helpers import FIXTURES, FakeV4L2, fixture, make_cfg, ntsc

FRAME = 160 * 120
HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": []}


class CaptureGray(unittest.TestCase):

    def setUp(self):
        self.cat = mock.Mock()
        p = mock.patch("ntsc_tvh.subprocess.Popen", return_value=self.cat)
        self.popen = p.start()
        self.addCleanup(p.stop)

    def capture(self, data=b"", info=HW, **cfg):
        result = subprocess.CompletedProcess([], 0, stdout=data, stderr=b"")
        with mock.patch("ntsc_tvh.subprocess.run", return_value=result) as run:
            out = ntsc.capture_gray(make_cfg(**cfg), info)
        self.cmd = run.call_args[0][0]
        self.stdin = run.call_args[1]["stdin"]
        return out

    def test_returns_ffmpeg_output(self):
        self.assertEqual(self.capture(b"frames"), b"frames")

    def test_timeout_is_empty_and_stops_cat(self):
        with mock.patch("ntsc_tvh.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("ffmpeg", 8)):
            self.assertEqual(ntsc.capture_gray(make_cfg(), HW), b"")
        self.cat.kill.assert_called_once_with()
        self.cat.wait.assert_called_once_with()

    def test_capture_command(self):
        self.capture()
        self.assertEqual(self.cmd[self.cmd.index("-vf") + 1], "fps=2,scale=160:120,format=gray")
        self.assertEqual(self.cmd[-5:], ["-frames:v", "6", "-f", "rawvideo", "pipe:1"])

    def test_hw_reads_via_cat_like_stream(self):
        # ffmpeg opening the MPEG node directly is empty on some builds (see CAPTURE_VIA_CAT).
        self.capture()
        self.popen.assert_called_once_with(["cat", "/dev/video0"], stdout=subprocess.PIPE,
                                           preexec_fn=ntsc._die_with_parent)
        self.assertEqual(self.cmd[4:8], ["-f", "mpeg", "-i", "pipe:0"])
        self.assertIs(self.stdin, self.cat.stdout)
        self.cat.kill.assert_called_once_with()
        self.cat.wait.assert_called_once_with()
        self.cat.stdout.close.assert_called_once_with()

    def test_hw_direct_read_when_cat_disabled(self):
        self.capture(CAPTURE_VIA_CAT="0")
        self.popen.assert_not_called()
        self.assertEqual(self.cmd[4:8], ["-f", "mpeg", "-i", "/dev/video0"])
        self.assertIsNone(self.stdin)

    def test_raw_device_reads_v4l2_directly(self):
        self.capture(info=dict(HW, hw_encoder=False))
        self.popen.assert_not_called()
        self.assertEqual(self.cmd[4:8], ["-f", "v4l2", "-i", "/dev/video0"])


W, H = 160, 120


def scene(shift=0, box=0):
    """A smooth synthetic picture: diagonal gradient, a bright box and a dark disc.
    `shift` pans the whole scene horizontally, `box` moves only the box."""
    px = bytearray(W * H)
    for y in range(H):
        for x in range(W):
            sx = (x + shift) % W
            v = 40 + (sx + y) * 150 // (W + H)
            if 30 + box <= sx < 70 + box and 40 <= y < 80:
                v = 220
            if (sx - 115) ** 2 + (y - 60) ** 2 < 400:
                v = 15
            px[y * W + x] = v
    return bytes(px)


def game_board(score=0):
    """Dark board framed by a ring of bulb dots, with a score panel that changes: real
    content (a WWOR game show) whose fine dot pattern scores like noise spatially -- only
    its stillness gives it away."""
    px = bytearray([20]) * (W * H)
    for y in range(H):
        for x in range(W):
            if (x < 10 or x >= W - 10 or y < 10 or y >= H - 10) and (x + y) % 2:
                px[y * W + x] = 250
            elif 70 <= x < 80 and 55 <= y < 65:
                px[y * W + x] = 60 + 30 * score
    return bytes(px)


def noise_frames(n, seed=7):
    rnd = random.Random(seed)
    return b"".join(bytes(rnd.randrange(256) for _ in range(W * H)) for _ in range(n))


def fixture_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


NOISE_MAX = float(ntsc.DEFAULTS["SCAN_NOISE_MAX"])
MOTION_MAX = float(ntsc.DEFAULTS["SCAN_MOTION_MAX"])


class PictureMetrics(unittest.TestCase):
    """The video heuristic: flat screens and snow are not pictures; real images are."""

    def judge(self, data):
        m = ntsc.picture_metrics(data, 12.0)
        return ntsc.looks_like_picture(m, make_cfg()), m

    def test_real_800e_snow_is_not_a_picture(self):
        # Before: std-dev alone (~42 here vs SCAN_VAR_MIN 12) made every empty channel "active".
        ok, m = self.judge(fixture_bytes("snow-800e-ch7.gray"))
        self.assertFalse(ok, m)
        self.assertGreater(m["var"], 30)
        self.assertGreater(m["noise"], NOISE_MAX)
        self.assertGreater(m["motion"], MOTION_MAX)

    def test_unconnected_svideo_noise_is_not_a_picture(self):
        ok, m = self.judge(fixture_bytes("noise-800e-svideo-unconnected.gray"))
        self.assertFalse(ok, m)

    def test_random_noise_is_not_a_picture(self):
        self.assertFalse(self.judge(noise_frames(6))[0])

    def test_flat_screen_is_not_a_picture(self):
        ok, m = self.judge(bytes([16]) * (W * H) * 6)
        self.assertFalse(ok)
        self.assertEqual(m, {"var": 0.0, "noise": None, "motion": None})

    def test_still_picture(self):
        ok, m = self.judge(b"".join(scene(box=i) for i in range(6)))
        self.assertTrue(ok, m)
        self.assertLess(m["noise"], 0.3)
        self.assertLess(m["motion"], 0.5)

    def test_fast_pan_passes_on_smoothness(self):
        ok, m = self.judge(b"".join(scene(shift=40 * i) for i in range(6)))
        self.assertTrue(ok, m)
        self.assertGreater(m["motion"], MOTION_MAX)  # moves a lot ...
        self.assertLess(m["noise"], NOISE_MAX)       # ... but is locally smooth

    def test_busy_still_picture_passes_on_stillness(self):
        ok, m = self.judge(b"".join(game_board(i) for i in range(6)))
        self.assertTrue(ok, m)
        self.assertGreater(m["noise"], NOISE_MAX)    # looks like noise pixel to pixel ...
        self.assertLess(m["motion"], 0.2)            # ... but barely changes

    def test_repeated_frames_do_not_count_as_stillness(self):
        # The fps filter sometimes repeats a frame byte-for-byte; a repeat of snow is still
        # snow. (Live video, even a still scene, is never byte-identical frame to frame.)
        snow = noise_frames(1)
        ok, m = self.judge(snow * 6)
        self.assertFalse(ok, m)
        self.assertIsNone(m["motion"])

    def test_contrast_frames_only(self):
        # A fade to black: only the frames with contrast are judged.
        ok, m = self.judge(bytes([16]) * (W * H) * 4 + scene() + scene(box=2))
        self.assertTrue(ok, m)

    def test_partial_tail_and_empty_capture(self):
        self.assertEqual(ntsc.picture_metrics(b"", 12.0), {"var": 0.0, "noise": None, "motion": None})
        m = ntsc.picture_metrics(scene() + b"\x00" * 100, 12.0)
        self.assertIsNotNone(m["noise"])

    def test_thresholds_come_from_config(self):
        m = ntsc.picture_metrics(b"".join(scene(box=i) for i in range(6)), 12.0)
        self.assertFalse(ntsc.looks_like_picture(m, make_cfg(SCAN_NOISE_MAX="0.01",
                                                             SCAN_MOTION_MAX="0.01")))
        self.assertFalse(ntsc.looks_like_picture(m, make_cfg(SCAN_VAR_MIN="200")))


class IsActive(unittest.TestCase):

    def check(self, method, sig, metrics, thr=35):
        with mock.patch.object(ntsc, "get_signal", return_value=(sig, 5)), \
             mock.patch.object(ntsc, "capture_gray", return_value=b"") as cap, \
             mock.patch.object(ntsc, "picture_metrics", return_value=metrics):
            result = ntsc.is_active(make_cfg(), HW, None, method, thr)
        self.video_probed = cap.called
        return result

    PIC = {"var": 40.04, "noise": 0.3333, "motion": 0.5}
    SNOW = {"var": 42.5, "noise": 0.83, "motion": 1.64}

    def test_signal_method(self):
        self.assertEqual(self.check("signal", 35, self.SNOW), (True, {"signal": 35, "afc": 5}))
        self.assertEqual(self.check("signal", 34, self.PIC)[0], False)
        self.assertEqual(self.check("signal", None, self.PIC)[0], False)
        self.assertFalse(self.video_probed)

    def test_video_method_reports_all_metrics(self):
        self.assertEqual(self.check("video", 0, self.PIC),
                         (True, {"signal": 0, "afc": 5, "var": 40.0, "noise": 0.33, "motion": 0.5}))
        self.assertFalse(self.check("video", 100, self.SNOW)[0])
        flat = {"var": 0.1, "noise": None, "motion": None}
        self.assertEqual(self.check("video", 0, flat)[1],
                         {"signal": 0, "afc": 5, "var": 0.1, "noise": None, "motion": None})

    def test_both_method(self):
        self.assertTrue(self.check("both", 80, self.PIC)[0])
        self.assertFalse(self.check("both", 80, self.SNOW)[0])
        self.assertFalse(self.check("both", 10, self.PIC)[0])


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
