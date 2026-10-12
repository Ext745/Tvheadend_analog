"""M3U playlist + state. The pipe:// URL is the mux identity in TVHeadend: if it changes,
existing channel mappings are orphaned. These tests pin its exact form."""

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import make_cfg, ntsc


def active(*cids, **metrics):
    out = []
    for cid in cids:
        c = ntsc.parse_channel(cid)
        c.update(metrics)
        out.append(c)
    return out


GOLDEN = """\
#EXTM3U
#EXTINF:-1 tvg-id="us-bcast:4" tvg-name="Broadcast 4" group-title="Broadcast" tvh-epg="disable",Broadcast 4
pipe:///usr/local/bin/ntsc-tvh stream us-bcast:4
#EXTINF:-1 tvg-id="us-cable-hrc:23" tvg-name="Cable HRC 23" group-title="Cable HRC" tvh-epg="disable",Cable HRC 23
pipe:///usr/local/bin/ntsc-tvh stream us-cable-hrc:23
#EXTINF:-1 tvg-id="input:composite" tvg-name="Composite In" group-title="Inputs" tvh-epg="disable",Composite In
pipe:///usr/local/bin/ntsc-tvh stream input:composite
#EXTINF:-1 tvg-id="input:svideo" tvg-name="S-Video In" group-title="Inputs" tvh-epg="disable",S-Video In
pipe:///usr/local/bin/ntsc-tvh stream input:svideo
"""


class M3UTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)

    def m3u(self, state, **cfg):
        cfg = dict(self.cfg, **cfg)
        ntsc.write_m3u(cfg, state)
        with open(cfg["M3U_FILE"]) as fh:
            return fh.read()


class WriteM3U(M3UTestCase):

    def test_golden(self):
        state = {"active": active("us-bcast:4", "us-cable-hrc:23", signal=80, afc=0)}
        self.assertEqual(self.m3u(state), GOLDEN)

    def test_urls_are_stable_across_rescans(self):
        a = {"scanned_at": "2026-01-01T00:00:00+00:00", "method": "signal",
             "active": active("us-bcast:4", "us-cable-hrc:23", signal=80, afc=0)}
        b = {"scanned_at": "2026-10-10T04:12:55+00:00", "method": "both",
             "active": active("us-bcast:4", "us-cable-hrc:23", signal=41, afc=-250, var=33.1)}
        self.assertEqual(self.m3u(a), self.m3u(b))

    def test_url_is_independent_of_how_the_channel_was_spelled(self):
        state = {"active": [ntsc.parse_channel(" US-BCAST:04 ")]}
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh stream us-bcast:4\n", self.m3u(state))

    def test_self_path_autodetect_uses_realpath_of_argv0(self):
        link = os.path.join(self.tmp.name, "ntsc-tvh-link")
        os.symlink("/usr/local/bin/ntsc-tvh", link)
        with mock.patch.object(ntsc.sys, "argv", [link]):
            out = self.m3u({"active": []}, SELF_PATH="")
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh stream input:composite", out)

    def test_inputs_can_be_hidden(self):
        out = self.m3u({"active": active("us-bcast:4")}, INPUTS_ALWAYS_ACTIVE="0")
        self.assertNotIn("input:", out)
        self.assertEqual(out.count("pipe://"), 1)

    def test_no_state_lists_only_inputs(self):
        out = self.m3u(None)
        self.assertEqual(out.splitlines()[0], "#EXTM3U")
        self.assertEqual(out.count("pipe://"), 2)

    def test_write_is_atomic(self):
        self.m3u(None)
        self.assertFalse(os.path.exists(self.cfg["M3U_FILE"] + ".tmp"))


class State(M3UTestCase):

    def test_round_trip(self):
        state = {"device": "/dev/video0", "active": active("us-cable:23", signal=60, afc=1)}
        ntsc.save_state(self.cfg, state)
        self.assertEqual(ntsc.load_state(self.cfg), state)
        self.assertFalse(os.path.exists(self.cfg["STATE_FILE"] + ".tmp"))

    def test_missing_or_corrupt_state_is_none(self):
        self.assertIsNone(ntsc.load_state(self.cfg))
        os.makedirs(os.path.dirname(self.cfg["STATE_FILE"]))
        with open(self.cfg["STATE_FILE"], "w") as fh:
            fh.write("{not json")
        self.assertIsNone(ntsc.load_state(self.cfg))


class CmdM3U(M3UTestCase):

    def run_cmd(self, output=None, stdout=False, **cfg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ntsc.cmd_m3u(dict(self.cfg, **cfg), argparse.Namespace(output=output, stdout=stdout))
        return buf.getvalue()

    def test_from_saved_state(self):
        ntsc.save_state(self.cfg, {"active": active("us-bcast:4", "us-cable-hrc:23")})
        self.assertEqual(self.run_cmd(stdout=True), GOLDEN)

    def test_output_override(self):
        out = os.path.join(self.tmp.name, "elsewhere", "x.m3u")
        self.assertEqual(self.run_cmd(output=out), f"wrote {out}\n")
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(self.cfg["M3U_FILE"]))

    def test_no_state_and_no_inputs_exits(self):
        with self.assertRaises(SystemExit):
            self.run_cmd(INPUTS_ALWAYS_ACTIVE="0")


if __name__ == "__main__":
    unittest.main()
