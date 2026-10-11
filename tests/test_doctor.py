"""doctor's capture test: catches a card that tunes but streams nothing."""

import argparse
import fcntl
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FIXTURES, make_cfg, ntsc

HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": [(0, "television"), (1, "composite"), (2, "s-video")]}
RAW = {"driver": "em28xx", "card": "Pinnacle PCTV HD Pro Stick", "pixfmt": "YUYV",
       "hw_encoder": False, "inputs": [(0, "Television"), (1, "Composite"), (2, "S-Video")]}
FRAMES = ntsc.CAPTURE_TEST_FRAMES * ntsc.FRAME_W * ntsc.FRAME_H


def gray(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()[:FRAMES]


class DoctorCapture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        dev = os.path.join(self.tmp.name, "video0")
        open(dev, "w").close()
        self.cfg = make_cfg(self.tmp.name, DEVICE=dev, DVB_GUARD="0", RAW_AUDIO_DEV="hw:1,0")
        p = mock.patch.object(ntsc.shutil, "which", side_effect=lambda name: "/usr/bin/" + name)
        p.start()
        self.addCleanup(p.stop)

    def doctor(self, info=HW, frames=b"", no_capture=False):
        out = io.StringIO()
        with mock.patch.object(ntsc, "device_info", return_value=info), \
             mock.patch.object(ntsc, "capture_gray", return_value=frames) as cap, \
             redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            ntsc.cmd_doctor(self.cfg, argparse.Namespace(no_capture=no_capture))
        self.cap = cap
        return cm.exception.code, out.getvalue()

    def test_picture(self):
        rc, out = self.doctor(frames=gray("colorbars-800e-ch4.gray"))
        self.assertEqual(rc, 0)
        self.assertIn("[OK] capture test  4 frames in", out)
        self.assertIn("showing a picture", out)
        self.cap.assert_called_once_with(self.cfg, HW, frames=4)

    def test_no_signal_is_ok_but_said(self):
        for name, seen in (("black-hvr1950-ch12.gray", "a flat screen"),
                           ("snow-800e-ch7.gray", "snow/noise")):
            with self.subTest(name):
                rc, out = self.doctor(frames=gray(name))
                self.assertEqual(rc, 0)
                self.assertIn(f"showing {seen} (no signal on the current input/channel?)", out)

    def test_hw_encoder_with_no_video_fails_with_the_firmware_hint(self):
        # What a firmware2 upload failure on an xHCI port looks like: tunes, streams nothing.
        rc, out = self.doctor(frames=b"")
        self.assertEqual(rc, 1)
        self.assertIn("[!!] capture test  the hardware encoder sent no video", out)
        self.assertIn("firmware2 upload transfer failure", out)
        self.assertIn("issues found", out)

    def test_raw_card_with_no_frames_fails(self):
        rc, out = self.doctor(info=RAW, frames=gray("snow-800e-ch7.gray")[:1000])
        self.assertEqual(rc, 1)
        self.assertIn("[!!] capture test  no frames from the device", out)

    def test_busy_tuner_is_skipped_not_failed(self):
        os.makedirs(os.path.dirname(self.cfg["LOCK_FILE"]), exist_ok=True)
        with open(self.cfg["LOCK_FILE"], "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            rc, out = self.doctor(frames=b"")
        self.assertEqual(rc, 0)
        self.assertIn("capture: skipped -- the tuner is in use", out)
        self.cap.assert_not_called()

    def test_no_capture_flag(self):
        rc, out = self.doctor(frames=b"", no_capture=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("capture", out)
        self.cap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
