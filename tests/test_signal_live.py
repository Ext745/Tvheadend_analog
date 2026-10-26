"""Raw cards (the 800e) only report signal while capturing: read it during a capture."""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import os

from helpers import FIXTURES, FakeV4L2, make_cfg, ntsc

HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": []}
RAW = {"driver": "em28xx", "card": "Pinnacle PCTV HD Pro Stick", "pixfmt": "YUYV",
       "hw_encoder": False, "inputs": []}


def gray(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


class FakeCapture:
    """A throwaway ffmpeg capture that runs until killed."""
    def __init__(self, *a, **kw):
        self.killed = False
    def poll(self):
        return 0 if self.killed else None
    def kill(self):
        self.killed = True
    def wait(self):
        return 0


class Live(unittest.TestCase):

    def setUp(self):
        for target, attr, value in ((ntsc, "SIGNAL_FIRST_POLL", 0),
                                    (ntsc.time, "sleep", lambda s: None)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def test_signal_while_returns_the_first_non_zero_reading(self):
        readings = iter([(0, 0), (0, 0), (44, 0), (60, 0)])
        with mock.patch.object(ntsc, "get_signal", side_effect=lambda cfg: next(readings)):
            self.assertEqual(ntsc.signal_while(make_cfg(), lambda: True, float("inf")), (44, 0))

    def test_signal_while_without_a_capture_reads_nothing(self):
        with mock.patch.object(ntsc, "get_signal") as gs:
            self.assertEqual(ntsc.signal_while(make_cfg(), lambda: False, float("inf")),
                             (None, None))
        gs.assert_not_called()

    def test_hw_card_is_read_idle(self):
        with mock.patch.object(ntsc, "get_signal", return_value=(100, 0)), \
             mock.patch.object(ntsc.subprocess, "Popen") as popen:
            self.assertEqual(ntsc.get_signal_live(make_cfg(), HW), (100, 0))
        popen.assert_not_called()

    def test_raw_card_is_read_during_a_capture_that_is_then_stopped(self):
        readings = iter([(0, 0), (44, 0)])
        cap = FakeCapture()
        with mock.patch.object(ntsc, "get_signal", side_effect=lambda cfg: next(readings)), \
             mock.patch.object(ntsc.subprocess, "Popen", return_value=cap) as popen:
            self.assertEqual(ntsc.get_signal_live(make_cfg(DEVICE="/dev/video1"), RAW), (44, 0))
        self.assertEqual(popen.call_args[0][0][-7:],
                         ["-f", "v4l2", "-i", "/dev/video1", "-f", "null", "-"])
        self.assertTrue(cap.killed)

    def test_raw_card_with_no_carrier_reads_zero(self):
        with mock.patch.object(ntsc, "get_signal", return_value=(0, 0)), \
             mock.patch.object(ntsc.subprocess, "Popen", return_value=FakeCapture()), \
             mock.patch.object(ntsc, "SIGNAL_CAPTURE_SECONDS", 0.05):
            self.assertEqual(ntsc.get_signal_live(make_cfg(), RAW), (0, 0))

    def test_raw_card_without_ffmpeg_falls_back_to_the_idle_reading(self):
        with mock.patch.object(ntsc, "get_signal", return_value=(0, 0)), \
             mock.patch.object(ntsc.subprocess, "Popen", side_effect=FileNotFoundError("ffmpeg")):
            self.assertEqual(ntsc.get_signal_live(make_cfg(), RAW), (0, 0))

    def test_signal_command_reports_the_live_reading_on_a_raw_card(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(ntsc, "run", FakeV4L2(all_fixture="v4l2-all-800e.txt",
                                                    inputs_fixture="v4l2-list-inputs-800e.txt")), \
             mock.patch.object(ntsc, "get_signal_live", return_value=(44, 0)) as live, \
             redirect_stdout(out):
            ntsc.cmd_signal(make_cfg(tmp, DVB_GUARD="0", SCAN_DWELL="0"),
                            argparse.Namespace(channel="us-bcast:4", wait=None))
        self.assertEqual(out.getvalue(), "us-bcast:4  67.250 MHz  signal=44%  afc=0 Hz\n")
        self.assertFalse(live.call_args[0][1]["hw_encoder"])


class AutoOnARawCard(unittest.TestCase):
    """The trap: the 800e reads 0 % idle everywhere but 44 % while capturing a picture.
    auto must keep judging every channel, not decide 'this card reports signal' from
    the capture-time reading and then skip channels whose idle reading is 0 %."""

    def test_every_channel_is_still_judged(self):
        live = (67.25, 175.25, 211.25)
        fake = FakeV4L2(all_fixture="v4l2-all-800e.txt",
                        inputs_fixture="v4l2-list-inputs-800e.txt")
        grabs = []
        def capture_with_signal(cfg, info):
            grabs.append(fake.freq)
            pic = fake.freq in live
            data = gray("colorbars-800e-ch4.gray" if pic else "snow-800e-ch7.gray")
            return data, (44 if pic else 0), 0
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(ntsc, "run", fake), \
             mock.patch.object(ntsc, "device_info", return_value=RAW), \
             mock.patch.object(ntsc, "capture_with_signal", side_effect=capture_with_signal), \
             mock.patch("ntsc_tvh.time.sleep"), \
             mock.patch("sys.stderr", io.StringIO()):
            cfg = make_cfg(tmp, DVB_GUARD="0")
            ntsc.cmd_scan(cfg, argparse.Namespace(tables="us-bcast", method="auto",
                                                  threshold=None, dwell=0, wait=0,
                                                  no_m3u=True))
            state = ntsc.load_state(cfg)
        self.assertEqual(len(grabs), 68 + 3)          # + the rechecks of the 3 pictures
        self.assertEqual([c["id"] for c in state["active"]],
                         ["us-bcast:4", "us-bcast:7", "us-bcast:13"])
        self.assertEqual(state["active"][0]["signal"], 44)     # the live reading is kept


if __name__ == "__main__":
    unittest.main()
