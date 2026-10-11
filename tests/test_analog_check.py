"""examples/analog-check: neighbour selection, the picture verdicts, and what stops a run."""

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FIXTURES, REPO, FakeV4L2, make_cfg, ntsc

SCRIPT = os.path.join(REPO, "examples", "analog-check")


def load():
    loader = importlib.machinery.SourceFileLoader("analog_check", SCRIPT)
    spec = importlib.util.spec_from_loader("analog_check", loader)
    mod = importlib.util.module_from_spec(spec)
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = dont_write
    return mod


ac = load()


def gray(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


class Empties(unittest.TestCase):

    def ids(self, channel):
        return [c["id"] for c in ac.default_empties(ntsc, ntsc.parse_channel(channel))]

    def test_rf_neighbours(self):
        self.assertEqual(self.ids("us-bcast:4"), ["us-bcast:3", "us-bcast:5"])

    def test_edge_of_table_has_one_neighbour(self):
        self.assertEqual(self.ids("us-bcast:2"), ["us-bcast:3"])

    def test_line_input_has_none(self):
        self.assertEqual(self.ids("input:composite"), [])


class Verdicts(unittest.TestCase):
    """The same verdict `scan --method video` reaches, on frames from real tuners."""

    def judge(self, name):
        return ac.judge_picture(ntsc, make_cfg(), gray(name))[0]

    def test_real_colour_bars_are_a_picture(self):
        # 800e on us-bcast:4 from an RF modulator (colour bars + "NO SIGNAL" caption).
        self.assertTrue(self.judge("colorbars-800e-ch4.gray"))

    def test_snow_and_black_are_not(self):
        self.assertFalse(self.judge("snow-800e-ch7.gray"))
        self.assertFalse(self.judge("black-hvr1950-ch12.gray"))

    def test_no_frames(self):
        self.assertEqual(ac.judge_picture(ntsc, make_cfg(), b""), (False, None))


class Run(unittest.TestCase):
    """main() against a fake tuner: picture on 67.25 MHz (us-bcast:4), snow elsewhere."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name, DVB_GUARD="0", SCAN_DWELL="0")
        self.fake = FakeV4L2()
        self.stream = mock.Mock()
        for target, attr, value in ((ntsc, "run", self.fake),
                                    (ntsc, "load_config", lambda: dict(self.cfg)),
                                    (ntsc, "capture_gray", self.capture),
                                    (ac, "find_ntsc", lambda arg: "/fake/ntsc-tvh"),
                                    (ac, "load_ntsc", lambda path: ntsc),
                                    (ac, "check_stream", self.stream)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def capture(self, cfg, info):
        return gray("colorbars-800e-ch4.gray" if self.fake.freq == 67.25 else "snow-800e-ch7.gray")

    def main(self, *args):
        out = os.path.join(self.tmp.name, "report")
        with redirect_stdout(io.StringIO()):
            rc = ac.main([*args, "--out", out])
        with open(os.path.join(out, "summary.json")) as fh:
            return rc, json.load(fh)["results"]

    def test_picture_and_empty_neighbours_pass(self):
        rc, results = self.main("us-bcast:4")
        self.assertEqual(rc, 0)
        scans = {r["name"]: r["ok"] for r in results if r["step"] == "scan"}
        self.assertEqual(scans, {"us-bcast:4": True, "us-bcast:3": True, "us-bcast:5": True})
        self.stream.assert_called_once()
        self.assertEqual(self.fake.freq, 67.25)          # left tuned to the channel

    def test_no_picture_fails_but_still_streams(self):
        # A black/snowy stream says "the signal", not "the streaming", is the problem.
        rc, results = self.main("us-bcast:7", "--no-empty")
        self.assertEqual(rc, 1)
        self.assertEqual([(r["step"], r["ok"]) for r in results if r["ok"] is False],
                         [("scan", False)])
        self.stream.assert_called_once()

    def test_tuner_that_does_not_tune_stops_the_run(self):
        self.fake.stuck = True                           # e.g. xc3028 firmware missing
        rc, results = self.main("us-bcast:4", "--no-empty")
        self.assertEqual(rc, 1)
        self.assertIn(("tune", False), [(r["step"], r["ok"]) for r in results])
        self.stream.assert_not_called()

    def test_explicit_empty_list(self):
        rc, results = self.main("us-bcast:4", "--empty", "us-cable:95")
        self.assertEqual(rc, 0)
        self.assertEqual([r["name"] for r in results if r["step"] == "scan"],
                         ["us-bcast:4", "us-cable:95"])

    def test_tvh_options_go_together(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            ac.main(["us-bcast:4", "--tvh", "http://localhost:9981"])


if __name__ == "__main__":
    unittest.main()
