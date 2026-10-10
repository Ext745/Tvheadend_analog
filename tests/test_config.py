"""Config precedence: built-in defaults < /etc/ntsc-tvh.conf < environment."""

import os
import re
import tempfile
import unittest
from unittest import mock

from helpers import REPO, ntsc


def clean_env(**extra):
    """os.environ without any ntsc-tvh keys, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if k not in ntsc.DEFAULTS}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class LoadConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, "ntsc-tvh.conf")
        p = mock.patch.object(ntsc, "CONF_PATH", self.conf)
        p.start()
        self.addCleanup(p.stop)

    def write(self, text):
        with open(self.conf, "w") as fh:
            fh.write(text)

    def test_defaults_when_no_file(self):
        with clean_env():
            self.assertEqual(ntsc.load_config(), ntsc.DEFAULTS)

    def test_v2_defaults(self):
        # Production-validated v2 defaults (see README changelog); change deliberately.
        d = ntsc.DEFAULTS
        self.assertEqual(d["LOCK_FILE"], "/var/lib/ntsc-tvh/ntsc-tvh.lock")
        self.assertEqual(d["CAPTURE_VIA_CAT"], "1")
        self.assertEqual(d["SCAN_LOCK_WAIT"], "5")

    def test_file_overrides_defaults(self):
        self.write("# comment\n"
                   "\n"
                   "DEVICE=/dev/video1\n"
                   "  STANDARD = 'ntsc-m'  \n"
                   'SCAN_TABLES="us-cable-hrc"\n'
                   "FFMPEG=/opt/ff=1/ffmpeg\n"
                   "NOT_A_KEY=ignored\n"
                   "garbage line without equals\n")
        with clean_env():
            cfg = ntsc.load_config()
        self.assertEqual(cfg["DEVICE"], "/dev/video1")
        self.assertEqual(cfg["STANDARD"], "ntsc-m")
        self.assertEqual(cfg["SCAN_TABLES"], "us-cable-hrc")
        self.assertEqual(cfg["FFMPEG"], "/opt/ff=1/ffmpeg")
        self.assertNotIn("NOT_A_KEY", cfg)
        self.assertEqual(cfg["M3U_FILE"], ntsc.DEFAULTS["M3U_FILE"])

    def test_inline_comments_are_stripped(self):
        self.write("SCAN_THRESHOLD=40           # signal-strength %\n"
                   "VIDEO_BITRATE=              # blank = driver default\n"
                   'DEVICE="/dev/video1"  # quoted\n'
                   "RAW_AUDIO_DEV=hw:1,0\t# tab before the comment\n"
                   "FFMPEG=/opt/ff#1/ffmpeg\n")
        with clean_env():
            cfg = ntsc.load_config()
        self.assertEqual(cfg["SCAN_THRESHOLD"], "40")
        self.assertEqual(cfg["VIDEO_BITRATE"], "")
        self.assertEqual(cfg["DEVICE"], "/dev/video1")
        self.assertEqual(cfg["RAW_AUDIO_DEV"], "hw:1,0")
        self.assertEqual(cfg["FFMPEG"], "/opt/ff#1/ffmpeg")   # '#' without whitespace is data

    def test_env_overrides_file(self):
        self.write("DEVICE=/dev/video1\nSCAN_DWELL=2\n")
        with clean_env(DEVICE="/dev/video9", NOT_A_KEY="x"):
            cfg = ntsc.load_config()
        self.assertEqual(cfg["DEVICE"], "/dev/video9")
        self.assertEqual(cfg["SCAN_DWELL"], "2")
        self.assertNotIn("NOT_A_KEY", cfg)

    def test_empty_env_value_still_overrides(self):
        self.write("VIDEO_BITRATE=6000000\n")
        with clean_env(VIDEO_BITRATE=""):
            self.assertEqual(ntsc.load_config()["VIDEO_BITRATE"], "")

    def test_non_regular_conf_path_is_ignored(self):
        os.mkdir(self.conf)   # isfile() is False for a directory -> skipped
        with clean_env():
            self.assertEqual(ntsc.load_config(), ntsc.DEFAULTS)


class ExampleConfig(unittest.TestCase):
    """examples/ntsc-tvh.conf documents every key with its default, commented out."""

    path = os.path.join(REPO, "examples", "ntsc-tvh.conf")

    def documented(self):
        out = {}
        with open(self.path) as fh:
            for line in fh:
                m = re.match(r"#\s*([A-Z_0-9]+)=(.*)$", line.rstrip("\n"))
                if m:
                    out[m.group(1)] = m.group(2)
        return out

    def test_documents_only_real_keys(self):
        # Every user-facing key is shown; FFMPEG/V4L2CTL (binary paths) are env-only.
        self.assertEqual(set(ntsc.DEFAULTS) - set(self.documented()), {"FFMPEG", "V4L2CTL"})

    def documented_values(self):
        return {k: v.split("#", 1)[0].strip() for k, v in self.documented().items()}

    def test_documented_values_match_defaults(self):
        # SELF_PATH defaults to "" (autodetect); the example shows the usual install path.
        for key, value in self.documented_values().items():
            with self.subTest(key=key):
                self.assertEqual(value, "/usr/local/bin/ntsc-tvh" if key == "SELF_PATH"
                                 else ntsc.DEFAULTS[key])

    def test_uncommented_example_lines_load_as_documented(self):
        # Uncommenting a line of the example (inline comment and all) must not change it.
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "ntsc-tvh.conf")
            with open(conf, "w") as fh:
                fh.writelines(f"{k}={v}\n" for k, v in self.documented().items())
            with mock.patch.object(ntsc, "CONF_PATH", conf), clean_env():
                self.assertEqual(ntsc.load_config(),
                                 dict(ntsc.DEFAULTS, **self.documented_values()))


if __name__ == "__main__":
    unittest.main()
