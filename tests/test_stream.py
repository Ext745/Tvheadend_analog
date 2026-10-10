"""The `stream` path TVHeadend's pipe:// mux invokes: ffmpeg argv, cat|ffmpeg wiring, lock handoff.

Nothing here execs or opens a device: os.execvp, os.dup2 and subprocess.Popen are faked.
"""

import argparse
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from helpers import FakeV4L2, completed, fixture, make_cfg, ntsc

HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": []}
RAW = {"driver": "em28xx", "card": "Pinnacle PCTV HD Pro Stick", "pixfmt": "YUYV",
       "hw_encoder": False, "inputs": []}


class BuildFfmpegCmd(unittest.TestCase):

    def test_hw_via_cat_reads_stdin(self):
        self.assertEqual(ntsc.build_ffmpeg_cmd(make_cfg(), HW), [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts", "-f", "mpeg", "-i", "pipe:0",
            "-c:v", "copy", "-c:a", "copy",
            "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"])

    def test_hw_direct_reads_device(self):
        cmd = ntsc.build_ffmpeg_cmd(make_cfg(CAPTURE_VIA_CAT="0", DEVICE="/dev/video2"), HW)
        self.assertEqual(cmd[cmd.index("-i") + 1], "/dev/video2")
        self.assertNotIn("pipe:0", cmd)

    def test_hw_never_transcodes_video(self):
        for acodec in ("copy", "mp2", "ac3", "bogus"):
            with self.subTest(acodec=acodec):
                cmd = ntsc.build_ffmpeg_cmd(make_cfg(AUDIO_CODEC=acodec), HW)
                self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
                self.assertNotIn("mpeg2video", cmd)

    def test_audio_codec_choice(self):
        def audio(acodec):
            cmd = ntsc.build_ffmpeg_cmd(make_cfg(AUDIO_CODEC=acodec), HW)
            i = cmd.index("-c:a")
            return cmd[i:cmd.index("-f", i)]
        self.assertEqual(audio("copy"), ["-c:a", "copy"])
        self.assertEqual(audio("MP2"), ["-c:a", "mp2", "-b:a", "224k"])
        self.assertEqual(audio("ac3"), ["-c:a", "ac3", "-b:a", "224k"])
        self.assertEqual(audio("bogus"), ["-c:a", "copy"])

    def test_raw_software_encode_with_alsa(self):
        cfg = make_cfg(RAW_AUDIO_DEV="hw:1,0", RAW_VBITRATE="4000000")
        self.assertEqual(ntsc.build_ffmpeg_cmd(cfg, RAW), [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-i", "/dev/video0",
            "-f", "alsa", "-ac", "2", "-ar", "48000", "-i", "hw:1,0",
            "-c:v", "mpeg2video", "-b:v", "4000000", "-filter:v", "yadif=0:-1:0",
            "-c:a", "mp2", "-b:a", "224k",       # "copy" is meaningless for ALSA PCM
            "-f", "mpegts", "pipe:1"])

    def test_raw_without_audio_device(self):
        with mock.patch.object(ntsc, "autodetect_alsa", return_value=""):
            cmd = ntsc.build_ffmpeg_cmd(make_cfg(), RAW)
        self.assertNotIn("alsa", cmd)
        self.assertEqual(cmd.count("-i"), 1)


class AutodetectAlsa(unittest.TestCase):

    def test_prefers_card_matching_tuner_name(self):
        with mock.patch.object(ntsc, "run", return_value=completed([], fixture("arecord-l.txt"))):
            self.assertEqual(ntsc.autodetect_alsa(make_cfg(), RAW), "hw:1,0")

    def test_falls_back_to_first_card(self):
        with mock.patch.object(ntsc, "run", return_value=completed([], fixture("arecord-l.txt"))):
            self.assertEqual(ntsc.autodetect_alsa(make_cfg(), dict(RAW, card="?")), "hw:0,0")

    def test_no_arecord(self):
        with mock.patch.object(ntsc, "run", return_value=completed([], returncode=1)):
            self.assertEqual(ntsc.autodetect_alsa(make_cfg(), RAW), "")


class Exec(Exception):
    """Raised by the fake os.execvp in place of replacing the test process."""


class CmdStream(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events = []
        self.locks = []
        self.fake = FakeV4L2()
        locks = self.locks

        class RecordingLock(ntsc.TunerLock):
            def __enter__(self):
                locks.append(self)
                return super().__enter__()

        self.cat = mock.Mock()
        self.cat.stdout.fileno.return_value = 99
        self.cat.stdout.close.side_effect = lambda: self.events.append("close")

        for target, kw in (
            ("run", {"new": self.fake}),
            ("TunerLock", {"new": RecordingLock}),
            ("device_info", {"return_value": HW}),
        ):
            p = mock.patch.object(ntsc, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        for target, kw in (
            ("ntsc_tvh.time.sleep", {}),
            ("ntsc_tvh.os.dup2", {"side_effect": lambda a, b: self.events.append(("dup2", a, b))}),
            ("ntsc_tvh.os.execvp", {"side_effect": self.fake_execvp}),
            ("ntsc_tvh.subprocess.Popen", {"side_effect": self.fake_popen}),
        ):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.close_locks)

    def close_locks(self):
        for lock in self.locks:
            if lock.fh and not lock.fh.closed:
                lock.fh.close()

    def fake_popen(self, argv, **kw):
        self.events.append(("popen", argv, kw.get("stdout")))
        return self.cat

    def fake_execvp(self, file, argv):
        self.events.append(("exec", file, list(argv)))
        lock = self.locks[-1]
        # At exec time the tuner lock must still be held, by an fd that survives exec().
        self.lock_inheritable = os.get_inheritable(lock.fh.fileno())
        self.lock_held = self.lock_is_held(lock.path)
        raise Exec()

    def lock_is_held(self, path):
        try:
            with ntsc.TunerLock(path, wait=0):
                return False
        except ntsc.ChannelError:
            return True

    def stream(self, channel="us-bcast:7", wait=None, **cfg):
        cfg = make_cfg(self.tmp.name, **cfg)
        args = argparse.Namespace(channel=channel, wait=wait)
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                ntsc.cmd_stream(cfg, args)
            except Exec:
                pass
        self.stderr = err.getvalue()
        return cfg

    def test_cat_pipeline_wiring(self):
        cfg = self.stream()
        self.assertEqual(self.events, [
            ("popen", ["cat", "/dev/video0"], subprocess.PIPE),
            ("dup2", 99, 0),
            "close",
            ("exec", "ffmpeg", ntsc.build_ffmpeg_cmd(cfg, HW)),
        ])
        self.assertIn("pipe:0", self.events[-1][2])
        self.assertTrue(self.lock_held)
        self.assertTrue(self.lock_inheritable)
        self.assertIn("--set-freq=175.2500", self.fake.opts())
        self.assertIn("streaming us-bcast:7 (tuner 175.250 MHz) via HW-MPEG", self.stderr)

    def test_direct_exec_without_cat(self):
        self.stream(CAPTURE_VIA_CAT="0")
        self.assertEqual(len(self.events), 1)
        kind, file, argv = self.events[0]
        self.assertEqual((kind, file), ("exec", "ffmpeg"))
        self.assertIn("/dev/video0", argv)
        self.assertTrue(self.lock_held)
        self.assertTrue(self.lock_inheritable)

    def test_raw_device_never_uses_cat(self):
        with mock.patch.object(ntsc, "device_info", return_value=RAW):
            self.stream(RAW_AUDIO_DEV="hw:1,0")
        self.assertEqual([e[0] for e in self.events], ["exec"])
        self.assertIn("via raw-encode", self.stderr)

    def test_line_input(self):
        self.stream("input:svideo")
        self.assertIn("--set-input=2", self.fake.opts())
        self.assertFalse(any(o.startswith("--set-freq") for o in self.fake.opts()))
        self.assertIn("streaming input:svideo (svideo)", self.stderr)

    def test_video_bitrate_only_on_hw(self):
        self.stream(VIDEO_BITRATE="6000000")
        self.assertIn("--set-ctrl=video_bitrate=6000000", self.fake.opts())
        self.close_locks()   # the first "ffmpeg" exits, releasing the tuner
        self.fake.calls.clear()
        with mock.patch.object(ntsc, "device_info", return_value=RAW):
            self.stream(VIDEO_BITRATE="6000000", RAW_AUDIO_DEV="hw:1,0")
        self.assertFalse(any("video_bitrate" in o for o in self.fake.opts()))

    def test_tune_failure_releases_lock_and_does_not_exec(self):
        self.fake.fail.add("--set-freq")
        with self.assertRaises(ntsc.ChannelError):
            self.stream()
        self.assertEqual(self.events, [])
        self.assertFalse(self.lock_is_held(self.locks[0].path))

    def test_busy_tuner(self):
        cfg = make_cfg(self.tmp.name)
        with ntsc.TunerLock(cfg["LOCK_FILE"]):
            with self.assertRaisesRegex(ntsc.ChannelError, "busy"):
                self.stream(wait=0)
        self.assertEqual(self.events, [])
        self.assertEqual(self.fake.opts(), [])

    def test_bad_channel_fails_before_touching_device(self):
        with self.assertRaises(ntsc.ChannelError):
            self.stream("us-bcast:99")
        self.assertEqual(self.events, [])
        self.assertEqual(self.locks, [])


if __name__ == "__main__":
    unittest.main()
