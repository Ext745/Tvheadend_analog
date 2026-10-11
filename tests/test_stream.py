"""The `stream` path TVHeadend's pipe:// mux invokes: ffmpeg argv, ALSA lookup, tuning + lock.

Nothing here starts a process or opens a device; see test_supervisor.py for the real-process tests.
"""

import argparse
import io
import os
import shutil
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
            "-c:v", "mpeg2video", "-b:v", "4000000", "-pix_fmt", "yuv420p",  # Main, not 4:2:2
            "-filter:v", "yadif=0:-1:0",
            "-aspect", "4:3",                      # 720x480 analog is 4:3, not 3:2
            "-c:a", "mp2", "-b:a", "224k",       # "copy" is meaningless for ALSA PCM
            "-f", "mpegts", "pipe:1"])

    def test_raw_without_audio_device(self):
        with mock.patch.object(ntsc, "autodetect_alsa", return_value=""):
            cmd = ntsc.build_ffmpeg_cmd(make_cfg(), RAW)
        self.assertNotIn("alsa", cmd)
        self.assertEqual(cmd.count("-i"), 1)


USB = "devices/pci0000:00/0000:00:14.0/usb1"
HDA = "devices/pci0000:00/0000:00:1f.3"


class AutodetectAlsa(unittest.TestCase):
    """Sound card lookup for the raw path. The sysfs tree mirrors a real 800e on a
    Tiger Lake laptop: video0 and ALSA card1 both under USB interface 1-1:1.0, and
    the host's HDA codec as card0 (the card the old name-guessing picked)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys")
        for target, kw in (("SYS_ROOT", {"new": self.sys}),
                           ("run", {"return_value": completed([], fixture("arecord-l-800e.txt"))})):
            p = mock.patch.object(ntsc, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.dev = os.path.join(self.tmp.name, "dev")
        os.makedirs(self.dev)
        self.video("video0", f"{USB}/1-1/1-1:1.0")
        self.card(0, HDA, [0, 2])

    def video(self, node, parent):
        self.link(f"class/video4linux/{node}", f"{parent}/video4linux/{node}")
        os.symlink(os.path.join(self.sys, parent), os.path.join(self.sys, parent,
                                                                "video4linux", node, "device"))
        open(os.path.join(self.dev, node), "w").close()

    def card(self, n, parent, capture_devs):
        cdir = self.link(f"class/sound/card{n}", f"{parent}/sound/card{n}")
        os.symlink(os.path.join(self.sys, parent), os.path.join(cdir, "device"))
        for d in capture_devs:
            os.mkdir(os.path.join(cdir, f"pcmC{n}D{d}c"))
        os.mkdir(os.path.join(cdir, f"pcmC{n}D0p"))    # playback nodes are ignored

    def link(self, cls, real):
        real = os.path.join(self.sys, real)
        os.makedirs(real, exist_ok=True)
        cls = os.path.join(self.sys, cls)
        os.makedirs(os.path.dirname(cls), exist_ok=True)
        os.symlink(real, cls)
        return cls

    def detect(self, node="video0", info=None):
        cfg = make_cfg(DEVICE=os.path.join(self.dev, node))
        return ntsc.autodetect_alsa(cfg, info or RAW)

    def test_800e_card_on_same_usb_interface(self):
        self.card(1, f"{USB}/1-1/1-1:1.0", [0])
        self.assertEqual(self.detect(), "hw:1,0")

    def test_audio_on_sibling_usb_interface(self):
        # e.g. snd-usb-audio bound to interface 1.1 of the same stick
        self.card(1, f"{USB}/1-1/1-1:1.1", [0])
        self.assertEqual(self.detect(), "hw:1,0")

    def test_other_usb_device_does_not_match(self):
        self.card(1, f"{USB}/1-2/1-2:1.0", [0])       # a USB headset on another port
        with mock.patch.object(ntsc, "run", return_value=completed([], returncode=1)):
            self.assertEqual(self.detect(), "")

    def test_pci_capture_card(self):
        self.video("video1", "devices/pci0000:00/0000:03:00.0")
        self.card(2, "devices/pci0000:00/0000:03:00.0", [1, 0])
        self.assertEqual(self.detect("video1"), "hw:2,0")

    def test_device_given_as_by_id_symlink(self):
        self.card(1, f"{USB}/1-1/1-1:1.0", [0])
        link = os.path.join(self.dev, "usb-Pinnacle_Systems_PCTV_800e-video-index0")
        os.symlink(os.path.join(self.dev, "video0"), link)
        self.assertEqual(self.detect(os.path.basename(link)), "hw:1,0")

    def test_falls_back_to_arecord_name_match(self):
        # No sysfs (e.g. a container without /sys/class/sound): match card or driver name.
        shutil.rmtree(os.path.join(self.sys, "class", "sound"))
        self.assertEqual(self.detect(), "hw:1,0")     # "Em28xxAudio" ~ driver em28xx

    def test_never_guesses_the_hosts_sound_chip(self):
        # Before the fix this returned hw:0,0 (the HDA codec) and doctor said "all good".
        shutil.rmtree(os.path.join(self.sys, "class", "sound"))
        self.assertEqual(self.detect(info=dict(RAW, driver="cx231xx", card="Hauppauge USB")), "")

    def test_no_arecord(self):
        shutil.rmtree(os.path.join(self.sys, "class", "sound"))
        with mock.patch.object(ntsc, "run", return_value=completed([], returncode=1)):
            self.assertEqual(self.detect(), "")


class TTY(io.StringIO):
    def isatty(self):
        return True


class CmdStream(unittest.TestCase):
    """Tuning and lock handling around the supervisor (supervise() itself is faked here;
    test_supervisor.py runs the real one against real processes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeV4L2()
        self.supervised = []
        for target, kw in (("run", {"new": self.fake}),
                           ("device_info", {"return_value": HW}),
                           ("supervise", {"side_effect": self.fake_supervise})):
            p = mock.patch.object(ntsc, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("ntsc_tvh.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def fake_supervise(self, cfg, cmd, lock, via_cat):
        self.supervised.append({"cmd": cmd, "via_cat": via_cat,
                                "lock_held": self.lock_is_held(lock.path),
                                "tuned_first": any(o.startswith("--set-") for o in self.fake.opts())})
        return 143

    def lock_is_held(self, path):
        try:
            with ntsc.TunerLock(path, wait=0):
                return False
        except ntsc.ChannelError:
            return True

    def stream(self, channel="us-bcast:7", wait=None, tty=True, **cfg):
        self.cfg = make_cfg(self.tmp.name, **cfg)
        err = TTY() if tty else io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            ntsc.cmd_stream(self.cfg, argparse.Namespace(channel=channel, wait=wait))
        self.stderr = err.getvalue()
        return cm.exception.code

    def test_tunes_then_supervises_under_the_lock(self):
        self.assertEqual(self.stream(), 143)             # supervise()'s code is the exit code
        (run,) = self.supervised
        self.assertTrue(run["lock_held"])
        self.assertTrue(run["tuned_first"])
        self.assertTrue(run["via_cat"])
        self.assertEqual(run["cmd"], ntsc.build_ffmpeg_cmd(self.cfg, HW))
        self.assertIn("--set-freq=175.2500", self.fake.opts())
        self.assertIn("streaming us-bcast:7 (tuner 175.250 MHz) via HW-MPEG", self.stderr)
        self.assertFalse(self.lock_is_held(self.cfg["LOCK_FILE"]))   # released after

    def test_status_line_only_on_a_terminal(self):
        # Under TVHeadend stderr is a pipe, and every line of it is logged at ERROR level.
        self.stream(tty=False)
        self.assertEqual(self.stderr, "")
        self.assertEqual(len(self.supervised), 1)

    def test_cat_only_for_hw_encoder_with_capture_via_cat(self):
        self.stream(CAPTURE_VIA_CAT="0")
        with mock.patch.object(ntsc, "device_info", return_value=RAW):
            self.stream(RAW_AUDIO_DEV="hw:1,0")
        self.assertEqual([r["via_cat"] for r in self.supervised], [False, False])
        self.assertIn("via raw-encode", self.stderr)

    def test_line_input(self):
        self.stream("input:svideo")
        self.assertIn("--set-input=2", self.fake.opts())
        self.assertFalse(any(o.startswith("--set-freq") for o in self.fake.opts()))
        self.assertIn("streaming input:svideo (svideo)", self.stderr)

    def test_video_bitrate_only_on_hw(self):
        self.stream(VIDEO_BITRATE="6000000")
        self.assertIn("--set-ctrl=video_bitrate=6000000", self.fake.opts())
        self.fake.calls.clear()
        with mock.patch.object(ntsc, "device_info", return_value=RAW):
            self.stream(VIDEO_BITRATE="6000000", RAW_AUDIO_DEV="hw:1,0")
        self.assertFalse(any("video_bitrate" in o for o in self.fake.opts()))

    def test_tune_failure_releases_lock_and_does_not_supervise(self):
        self.fake.fail.add("--set-freq")
        cfg = make_cfg(self.tmp.name)
        with self.assertRaises(ntsc.ChannelError):
            ntsc.cmd_stream(cfg, argparse.Namespace(channel="us-bcast:7", wait=None))
        self.assertEqual(self.supervised, [])
        self.assertFalse(self.lock_is_held(cfg["LOCK_FILE"]))

    def test_busy_tuner(self):
        cfg = make_cfg(self.tmp.name)
        with ntsc.TunerLock(cfg["LOCK_FILE"]):
            with self.assertRaisesRegex(ntsc.ChannelError, "busy"):
                ntsc.cmd_stream(cfg, argparse.Namespace(channel="us-bcast:7", wait=0))
        self.assertEqual(self.supervised, [])
        self.assertEqual(self.fake.opts(), [])

    def test_bad_channel_fails_before_touching_device(self):
        with self.assertRaises(ntsc.ChannelError):
            ntsc.cmd_stream(make_cfg(self.tmp.name),
                            argparse.Namespace(channel="us-bcast:99", wait=None))
        self.assertEqual(self.supervised, [])
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
