"""FM radio: fm:MHZ channels, FM_STATIONS, finding the radio node, and the radio stream.

The radio node is found in a fake /sys (as in test_dvb_guard.py); a symlink to /dev/zero
stands in for /dev/radio0 so the real device-number matching runs without hardware.
"""

import argparse
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import FakeV4L2, fixture, make_cfg, ntsc

HW = {"driver": "pvrusb2", "card": "WinTV HVR-1950 Model 751xx", "pixfmt": "MPEG",
      "hw_encoder": True, "inputs": []}
RAW = {"driver": "em28xx", "card": "Pinnacle PCTV HD Pro Stick", "pixfmt": "YUYV",
       "hw_encoder": False, "inputs": []}
USB = "devices/pci0000:00/0000:00:14.0/usb1"


def devno(path):
    rdev = os.stat(path).st_rdev
    return f"{os.major(rdev)}:{os.minor(rdev)}"


class FmChannels(unittest.TestCase):

    def test_parse(self):
        c = ntsc.parse_channel("fm:101.10")
        self.assertEqual((c["id"], c["kind"], c["freq"], c["label"], c["group"]),
                         ("fm:101.1", "radio", 101.1, "FM 101.1", "Radio"))
        self.assertEqual(ntsc.parse_channel("FM:88").get("id"), "fm:88")
        self.assertEqual(ntsc.parse_channel("fm:71.75")["freq"], 71.75)   # OIRT / low band

    def test_parse_rejects(self):
        for cid, msg in (("fm:abc", "in MHz"), ("fm:64.9", "outside 65-108"),
                         ("fm:108.1", "outside"), ("fm:", "in MHz")):
            with self.subTest(cid=cid), self.assertRaisesRegex(ntsc.ChannelError, msg):
                ntsc.parse_channel(cid)

    def test_stations_from_config(self):
        st = ntsc.fm_stations(make_cfg(FM_STATIONS=" 98.7, 101.1=CBS-FM ,, 107.50=WBLS"))
        self.assertEqual([(c["id"], c["label"]) for c in st],
                         [("fm:98.7", "FM 98.7"), ("fm:101.1", "CBS-FM"), ("fm:107.5", "WBLS")])
        self.assertEqual(ntsc.fm_stations(make_cfg()), [])

    def test_stations_errors_name_the_entry(self):
        with self.assertRaisesRegex(ntsc.ChannelError, "'1011=CBS'.*outside"):
            ntsc.fm_stations(make_cfg(FM_STATIONS="98.7,1011=CBS"))
        with self.assertRaisesRegex(ntsc.ChannelError, "101.1 MHz twice"):
            ntsc.fm_stations(make_cfg(FM_STATIONS="101.1,101.10=CBS"))

    def test_playlist_lists_stations_between_tuner_channels_and_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, FM_STATIONS="101.1=CBS-FM")
            ntsc.write_m3u(cfg, {"active": [ntsc.parse_channel("us-bcast:4")]})
            with open(cfg["M3U_FILE"]) as fh:
                lines = fh.read().splitlines()
        self.assertEqual(lines[3:5], [
            '#EXTINF:-1 tvg-id="fm:101.1" tvg-name="CBS-FM" group-title="Radio" '
            'tvh-epg="disable",CBS-FM',
            "pipe:///usr/local/bin/ntsc-tvh stream fm:101.1"])
        self.assertIn("input:composite", lines[6])

    def test_playlist_without_scan_state(self):
        # FM stations alone are enough for `m3u` (no TV scan needed)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, FM_STATIONS="98.7", INPUTS_ALWAYS_ACTIVE="0")
            with redirect_stdout(io.StringIO()):
                ntsc.cmd_m3u(cfg, argparse.Namespace(output=None, stdout=False))
            with open(cfg["M3U_FILE"]) as fh:
                self.assertIn("stream fm:98.7", fh.read())

    def test_list(self):
        out = io.StringIO()
        with redirect_stdout(out):
            ntsc.cmd_list(make_cfg(FM_STATIONS="101.1=CBS-FM"),
                          argparse.Namespace(active_only=False, tables="us-bcast", json=False))
        self.assertIn("fm:101.1", out.getvalue())
        self.assertIn("101.100 MHz   CBS-FM", out.getvalue())


class BuildRadioCmd(unittest.TestCase):

    def test_via_cat(self):
        self.assertEqual(ntsc.build_radio_cmd(make_cfg(), "/dev/radio0"), [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts", "-f", "mpeg",
            "-analyzeduration", "1000000", "-probesize", "500000", "-i", "pipe:0",
            "-map", "0:a:0", "-c:a", "copy",          # audio only: drop the encoder's video
            "-f", "mpegts", "-mpegts_flags", "+resend_headers",
            "-mpegts_service_type", "digital_radio", "pipe:1"])   # TVHeadend: a radio channel

    def test_direct_and_codec(self):
        cmd = ntsc.build_radio_cmd(make_cfg(CAPTURE_VIA_CAT="0", AUDIO_CODEC="ac3"), "/dev/radio1")
        self.assertEqual(cmd[cmd.index("-i") + 1], "/dev/radio1")
        self.assertEqual(cmd[cmd.index("-c:a"):cmd.index("-c:a") + 4], ["-c:a", "ac3", "-b:a", "224k"])


@unittest.skipUnless(sys.platform.startswith("linux"), "uses Linux device numbers")
class RadioDevice(unittest.TestCase):
    """HVR-1950 as on the workbench: video1 and radio0 on USB interface 1-10.7:1.0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys")
        self.dev = os.path.join(self.tmp.name, "dev")
        os.makedirs(self.dev)
        for target, value in (("SYS_ROOT", self.sys), ("DEV_ROOT", self.dev)):
            p = mock.patch.object(ntsc, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.parent = f"{USB}/1-10/1-10.7/1-10.7:1.0"
        self.node("video1", self.parent, "/dev/null")
        self.cfg = make_cfg(self.tmp.name, DEVICE=os.path.join(self.dev, "video1"))

    def node(self, name, parent, standin, dev_name=None):
        """sysfs entry `name` on `parent`, its dev = standin's devno; /dev/dev_name -> standin."""
        real = os.path.join(self.sys, parent, "video4linux", name)
        os.makedirs(real)
        os.symlink(os.path.join(self.sys, parent), os.path.join(real, "device"))
        with open(os.path.join(real, "dev"), "w") as fh:
            fh.write(devno(standin) + "\n")
        cls = os.path.join(self.sys, "class", "video4linux")
        os.makedirs(cls, exist_ok=True)
        os.symlink(real, os.path.join(cls, name))
        if dev_name is not False:
            os.symlink(standin, os.path.join(self.dev, dev_name or name))

    def test_found_on_the_same_card(self):
        self.node("radio0", self.parent, "/dev/zero")
        self.assertEqual(ntsc.radio_device(self.cfg), os.path.join(self.dev, "radio0"))

    def test_matched_by_device_number_in_a_container(self):
        # the host's radio1 is mapped in as /dev/radio0
        self.node("radio1", self.parent, "/dev/zero", dev_name="radio0")
        self.assertEqual(ntsc.radio_device(self.cfg), os.path.join(self.dev, "radio0"))

    def test_other_cards_radio_is_not_used(self):
        self.node("radio0", f"{USB}/1-9/1-9:1.0", "/dev/zero")
        self.assertIsNone(ntsc.radio_device(self.cfg))
        with self.assertRaisesRegex(ntsc.ChannelError, "no FM radio node on the card"):
            ntsc.need_radio(self.cfg)

    def test_in_sysfs_but_not_mapped_into_the_container(self):
        self.node("radio0", self.parent, "/dev/zero", dev_name=False)
        self.assertIsNone(ntsc.radio_device(self.cfg))
        with self.assertRaisesRegex(ntsc.ChannelError, r"radio0 in sysfs.*map it in"):
            ntsc.need_radio(self.cfg)

    def test_radio_device_setting_wins(self):
        cfg = dict(self.cfg, RADIO_DEVICE="/dev/radio7")
        self.assertEqual(ntsc.radio_device(cfg), "/dev/radio7")

    def test_raw_card_refused(self):
        self.node("radio0", self.parent, "/dev/zero")
        with self.assertRaisesRegex(ntsc.ChannelError, "raw frames"):
            ntsc.need_radio(self.cfg, RAW)
        self.assertEqual(ntsc.need_radio(self.cfg, HW), os.path.join(self.dev, "radio0"))


class TuneAndSignal(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeV4L2()
        self.fake.nosignal = fixture("v4l2-get-tuner-radio-stereo.txt")
        for target, kw in (("run", {"new": self.fake}),
                           ("radio_device", {"return_value": "/dev/radio0"}),
                           ("check_dvb_idle", {})):
            p = mock.patch.object(ntsc, target, **kw)
            self.mocks = getattr(self, "mocks", {})
            self.mocks[target] = p.start()
            self.addCleanup(p.stop)
        p = mock.patch("ntsc_tvh.time.sleep")
        p.start()
        self.addCleanup(p.stop)
        self.cfg = make_cfg(self.tmp.name)

    def test_tune_uses_the_radio_node_only(self):
        ntsc.tune(self.cfg, ntsc.parse_channel("fm:101.1"))
        self.assertEqual([c[2] for c in self.fake.calls], ["/dev/radio0", "/dev/radio0"])
        self.assertEqual(self.fake.opts(), ["--set-freq=101.1000", "--get-freq"])
        self.mocks["check_dvb_idle"].assert_called_once()     # same tuner as the DVB side

    def test_stereo_flag(self):
        self.assertTrue(ntsc.get_stereo(self.cfg, "/dev/radio0"))
        self.fake.nosignal = fixture("v4l2-get-tuner-radio-mono.txt")
        self.assertFalse(ntsc.get_stereo(self.cfg, "/dev/radio0"))
        self.fake.nosignal = "Tuner 0:\n\tSignal strength/AFC  : 40%/0\n"   # says nothing
        self.assertIsNone(ntsc.get_stereo(self.cfg, "/dev/radio0"))

    def test_signal_command(self):
        out = io.StringIO()
        with mock.patch.object(ntsc, "get_radio_signal_live", return_value=(53, 0, True)), \
                redirect_stdout(out):
            ntsc.cmd_signal(self.cfg, argparse.Namespace(channel="fm:98.7", wait=None))
        self.assertEqual(out.getvalue(), "fm:98.7  98.700 MHz  signal=53%  stereo\n")

    def test_live_signal_reads_the_radio_while_measuring(self):
        started = []

        class Cat:
            def __init__(self, cmd, **kw):
                started.append(cmd)
            def poll(self):
                return None
            def kill(self):
                pass
            def wait(self):
                return 0
        with mock.patch("ntsc_tvh.subprocess.Popen", Cat):
            self.assertEqual(ntsc.get_radio_signal_live(self.cfg, "/dev/radio0"), (53, 0, True))
        self.assertEqual(started, [["cat", "/dev/radio0"]])


class TTY(io.StringIO):
    def isatty(self):
        return True


class CmdStreamRadio(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeV4L2()
        self.supervised = []
        for target, kw in (("run", {"new": self.fake}),
                           ("device_info", {"return_value": HW}),
                           ("radio_device", {"return_value": "/dev/radio0"}),
                           ("check_dvb_idle", {}),
                           ("supervise", {"side_effect": self.fake_supervise})):
            p = mock.patch.object(ntsc, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("ntsc_tvh.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def fake_supervise(self, cfg, cmd, lock, via_cat, watch=None, device=None):
        self.supervised.append({"cmd": cmd, "via_cat": via_cat, "device": device, "watch": watch})
        return 143

    def stream(self, channel="fm:101.1", **cfg):
        self.cfg = make_cfg(self.tmp.name, **cfg)
        err = TTY()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            ntsc.cmd_stream(self.cfg, argparse.Namespace(channel=channel, wait=None))
        self.stderr = err.getvalue()
        return cm.exception.code

    def test_streams_the_radio_node(self):
        self.assertEqual(self.stream(VIDEO_BITRATE="6000000"), 143)
        (run,) = self.supervised
        self.assertEqual(run["device"], "/dev/radio0")             # cat reads the radio node
        self.assertTrue(run["via_cat"])
        self.assertEqual(run["cmd"], ntsc.build_radio_cmd(self.cfg, "/dev/radio0"))
        self.assertIsNotNone(run["watch"])                         # DVB guard stays on mid-stream
        self.assertNotIn("--set-input", " ".join(self.fake.opts()))
        self.assertFalse(any("video_bitrate" in o for o in self.fake.opts()))
        self.assertIn("streaming fm:101.1 (FM 101.100 MHz on /dev/radio0) via HW-MPEG",
                      self.stderr)

    def test_tv_channels_still_stream_the_video_node(self):
        self.stream("us-bcast:4")
        self.assertIsNone(self.supervised[0]["device"])

    def test_raw_card_refused_before_tuning(self):
        with mock.patch.object(ntsc, "device_info", return_value=RAW), \
                self.assertRaisesRegex(ntsc.ChannelError, "raw frames"):
            ntsc.cmd_stream(make_cfg(self.tmp.name),
                            argparse.Namespace(channel="fm:101.1", wait=None))
        self.assertEqual(self.supervised, [])
        self.assertEqual(self.fake.calls, [])


class DoctorRadio(unittest.TestCase):

    def check(self, radio, info=HW, **cfg):
        results, out = [], io.StringIO()
        with mock.patch.object(ntsc, "radio_device", return_value=radio), \
                mock.patch.object(ntsc, "_radio_nodes", return_value=[]), redirect_stdout(out):
            ntsc.doctor_radio(make_cfg(**cfg), info,
                              lambda label, good, detail="": results.append((label, good, detail)))
        return results, out.getvalue()

    def test_stations_and_node(self):
        res, _ = self.check("/dev/radio0", FM_STATIONS="98.7,101.1")
        self.assertEqual(res, [("FM radio node", True, "/dev/radio0, 2 station(s) in FM_STATIONS")])

    def test_stations_without_node_fail(self):
        (res,), _ = self.check(None, FM_STATIONS="98.7")
        self.assertEqual(res[:2], ("FM radio node", False))

    def test_bad_stations_fail(self):
        (res,), _ = self.check("/dev/radio0", FM_STATIONS="98.7,abc")
        self.assertEqual(res[:2], ("FM_STATIONS", False))

    def test_no_stations_just_mentions_the_node(self):
        res, out = self.check("/dev/radio0")
        self.assertEqual(res, [])
        self.assertIn("radio:   /dev/radio0", out)
        self.assertEqual(self.check(None), ([], ""))


if __name__ == "__main__":
    unittest.main()
