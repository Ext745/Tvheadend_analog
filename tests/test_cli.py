"""End-to-end CLI runs of the real script in a subprocess (no device needed)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import SCRIPT, ntsc


class CLI(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = os.path.join(self.tmp.name, "state")
        self.env = {k: v for k, v in os.environ.items() if k not in ntsc.DEFAULTS}
        self.env.update(STATE_FILE=os.path.join(state, "state.json"),
                        M3U_FILE=os.path.join(state, "ntsc.m3u"),
                        LOCK_FILE=os.path.join(state, "ntsc-tvh.lock"),
                        SELF_PATH="/usr/local/bin/ntsc-tvh",
                        DEVICE=os.path.join(self.tmp.name, "no-such-video0"),
                        V4L2CTL="false", FFMPEG="false",
                        PYTHONDONTWRITEBYTECODE="1")

    def cli(self, *args, rc=0):
        r = subprocess.run([sys.executable, SCRIPT, *args], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, rc, msg=f"stdout={r.stdout!r} stderr={r.stderr!r}")
        return r

    def test_help_lists_every_subcommand(self):
        out = self.cli("--help").stdout
        for cmd in ("doctor", "list", "tune", "signal", "stream", "scan", "m3u",
                    "install-timer", "status"):
            self.assertIn(cmd, out)

    def test_no_subcommand_is_usage_error(self):
        self.assertIn("usage:", self.cli(rc=2).stderr)

    def test_list_table(self):
        lines = self.cli("list", "--tables", "us-bcast").stdout.splitlines()
        self.assertEqual(len(lines), 68 + 2)
        self.assertEqual(lines[0].split(), ["us-bcast:2", "55.250", "MHz", "Broadcast", "2"])
        self.assertEqual(lines[-1].split(), ["input:svideo", "line", "input", "S-Video", "In"])

    def test_list_unknown_table_is_clean_error(self):
        r = self.cli("list", "--tables", "us-bcast,bogus", rc=1)
        self.assertEqual(r.stdout, "")
        self.assertIn("error: unknown table 'bogus'", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_list_ignores_empty_table_names(self):
        lines = self.cli("list", "--tables", "us-bcast,").stdout.splitlines()
        self.assertEqual(len(lines), 68 + 2)

    def test_list_all_tables_json(self):
        chans = json.loads(self.cli("list", "--json").stdout)
        self.assertEqual(len(chans), 68 + 3 * 135 + 2)
        self.assertEqual(len({c["id"] for c in chans}), len(chans))

    def test_list_active_only_before_scan(self):
        self.env["INPUTS_ALWAYS_ACTIVE"] = "0"
        self.assertIn("no active channels", self.cli("list", "--active-only").stdout)

    def test_m3u_without_scan(self):
        out = self.cli("m3u", "--stdout").stdout
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh stream input:composite\n", out)
        self.assertTrue(os.path.exists(self.env["M3U_FILE"]))

    def test_status_before_scan(self):
        self.assertIn("no scan has been run yet", self.cli("status").stdout)

    def test_bad_channel_is_clean_error(self):
        r = self.cli("tune", "us-bcast:99", rc=1)
        self.assertEqual(r.stderr.strip(), "error: channel 99 is not defined in table us-bcast")

    def test_doctor_reports_missing_device(self):
        out = self.cli("doctor", rc=1).stdout
        self.assertIn("[!!] device node exists", out)
        self.assertIn("issues found", out)


if __name__ == "__main__":
    unittest.main()
