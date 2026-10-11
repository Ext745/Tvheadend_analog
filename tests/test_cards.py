"""--card NAME: a second tuner with its own config, state, playlist, lock and timer."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import SCRIPT, make_cfg, ntsc


class Cards(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cards = os.path.join(self.tmp.name, "ntsc-tvh.d")
        os.makedirs(self.cards)
        env = {k: v for k, v in os.environ.items() if k not in ntsc.DEFAULTS}
        for target, attr, value in ((ntsc, "CARD_DIR", self.cards),
                                    (ntsc, "CONF_PATH", os.path.join(self.tmp.name, "none")),
                                    (os, "environ", env)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def card(self, name, text):
        with open(os.path.join(self.cards, f"{name}.conf"), "w") as fh:
            fh.write(text)

    def test_card_file_sets_device_and_moves_state_lock_and_playlist(self):
        self.card("800e", "DEVICE=/dev/video1\n")
        cfg = ntsc.load_config("800e")
        self.assertEqual(cfg["DEVICE"], "/dev/video1")
        self.assertEqual(cfg["CARD"], "800e")
        self.assertEqual(cfg["STATE_FILE"], "/var/lib/ntsc-tvh/800e/state.json")
        self.assertEqual(cfg["M3U_FILE"], "/var/lib/ntsc-tvh/800e/ntsc.m3u")
        self.assertEqual(cfg["LOCK_FILE"], "/var/lib/ntsc-tvh/800e/ntsc-tvh.lock")

    def test_paths_the_card_file_names_are_kept(self):
        self.card("hvr", "DEVICE=/dev/video0\nLOCK_FILE=/run/hvr.lock\n")
        cfg = ntsc.load_config("hvr")
        self.assertEqual(cfg["LOCK_FILE"], "/run/hvr.lock")
        self.assertEqual(cfg["STATE_FILE"], "/var/lib/ntsc-tvh/hvr/state.json")

    def test_card_file_beats_the_environment(self):
        # A DEVICE set for the whole container must not send the 800e's URLs to video0.
        self.card("800e", "DEVICE=/dev/video1\n")
        with mock.patch.dict(os.environ, {"DEVICE": "/dev/video0", "STOP_GRACE": "3"}):
            cfg = ntsc.load_config("800e")
        self.assertEqual(cfg["DEVICE"], "/dev/video1")
        self.assertEqual(cfg["STOP_GRACE"], "3")         # env still applies to the rest

    def test_no_card_is_unchanged(self):
        self.assertEqual(ntsc.load_config(), ntsc.DEFAULTS)
        self.assertNotIn("CARD", ntsc.load_config())

    def test_missing_card_file(self):
        with self.assertRaisesRegex(ntsc.ChannelError, r"card 'nope'.*DEVICE=/dev/videoN"):
            ntsc.load_config("nope")

    def test_bad_card_name(self):
        for name in ("../etc", "Big", "a b", ""):
            with self.subTest(name=name), self.assertRaises(ntsc.ChannelError):
                ntsc.load_config(name)


class CardPlaylist(unittest.TestCase):

    def m3u(self, **cfg):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_cfg(tmp, **cfg)
            ntsc.write_m3u(c, None)
            with open(c["M3U_FILE"]) as fh:
                return fh.read()

    def test_card_urls_ids_and_labels(self):
        out = self.m3u(CARD="800e")
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh --card 800e stream input:composite\n", out)
        self.assertIn('tvg-id="800e/input:composite" tvg-name="Composite In (800e)"', out)

    def test_single_card_urls_stay_byte_identical(self):
        # The URL is TVHeadend's mux identity: existing setups must not see new muxes.
        out = self.m3u()
        self.assertIn("pipe:///usr/local/bin/ntsc-tvh stream input:composite\n", out)
        self.assertIn('tvg-id="input:composite" tvg-name="Composite In"', out)
        self.assertNotIn("--card", out)

    def test_timer_unit_per_card(self):
        self.assertEqual(ntsc.unit_name(make_cfg()), "ntsc-tvh-scan")
        self.assertEqual(ntsc.unit_name(make_cfg(CARD="800e")), "ntsc-tvh-scan-800e")


class CardCLI(unittest.TestCase):

    def test_unknown_card_is_a_clean_error(self):
        r = subprocess.run([sys.executable, SCRIPT, "--card", "nosuchcard", "list"],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("error: card 'nosuchcard': can't read /etc/ntsc-tvh.d/nosuchcard.conf",
                      r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
