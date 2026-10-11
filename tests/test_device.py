"""v4l2-ctl output parsing and tuning sequence, driven by canned output in tests/fixtures/."""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FakeV4L2, completed, fixture, make_cfg, ntsc


class DeviceTestCase(unittest.TestCase):

    def use(self, fake):
        p = mock.patch.object(ntsc, "run", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


class DeviceInfo(DeviceTestCase):

    def test_hvr1950_is_hw_encoder(self):
        self.use(FakeV4L2())
        info = ntsc.device_info(make_cfg())
        self.assertEqual(info["driver"], "pvrusb2")
        self.assertEqual(info["card"], "WinTV HVR-1950 Model 751xx")
        self.assertEqual(info["pixfmt"], "MPEG")
        self.assertTrue(info["hw_encoder"])
        self.assertEqual(info["inputs"], [(0, "television"), (1, "radio"),
                                          (2, "s-video"), (3, "composite")])

    def test_pinnacle_800e_is_raw(self):
        # Captured from a real 800e (em28xx card=17); it powers up in PAL 720x576.
        self.use(FakeV4L2(all_fixture="v4l2-all-800e.txt",
                          inputs_fixture="v4l2-list-inputs-800e.txt"))
        info = ntsc.device_info(make_cfg())
        self.assertEqual(info["driver"], "em28xx")
        self.assertEqual(info["card"], "Pinnacle PCTV HD Pro Stick")
        self.assertEqual(info["pixfmt"], "YUYV")
        self.assertFalse(info["hw_encoder"])
        self.assertEqual(info["inputs"], [(0, "Television"), (1, "Composite"), (2, "S-Video")])

    def test_probe_failure_raises(self):
        self.use(FakeV4L2(fail=["--all"]))
        with self.assertRaises(ntsc.ChannelError):
            ntsc.device_info(make_cfg())

    def test_uses_configured_binary_and_device(self):
        fake = self.use(FakeV4L2())
        ntsc.device_info(make_cfg(V4L2CTL="/opt/v4l2-ctl", DEVICE="/dev/video3"))
        self.assertEqual(fake.calls[0][:3], ["/opt/v4l2-ctl", "-d", "/dev/video3"])


class Inputs(DeviceTestCase):

    def test_list_inputs_failure_is_empty(self):
        self.use(FakeV4L2(fail=["--list-inputs"]))
        self.assertEqual(ntsc.list_inputs(make_cfg()), [])

    def test_resolve(self):
        self.use(FakeV4L2())
        cfg = make_cfg()
        self.assertEqual(ntsc.resolve_input_index(cfg, "television"), 0)
        self.assertEqual(ntsc.resolve_input_index(cfg, "svideo"), 2)
        self.assertEqual(ntsc.resolve_input_index(cfg, "composite"), 3)

    def test_resolve_800e(self):
        self.use(FakeV4L2(inputs_fixture="v4l2-list-inputs-800e.txt"))
        cfg = make_cfg()
        self.assertEqual(ntsc.resolve_input_index(cfg, "television"), 0)
        self.assertEqual(ntsc.resolve_input_index(cfg, "composite"), 1)
        self.assertEqual(ntsc.resolve_input_index(cfg, "svideo"), 2)

    def test_resolve_alternate_names(self):
        fake = self.use(FakeV4L2())
        fake.inputs_out = ("\tInput : 0\n\tName : Composite1\n"
                           "\tInput : 1\n\tName : S-Video\n"
                           "\tInput : 2\n\tName : Tuner 1\n")
        cfg = make_cfg()
        self.assertEqual(ntsc.resolve_input_index(cfg, "television"), 2)
        self.assertEqual(ntsc.resolve_input_index(cfg, "svideo"), 1)
        self.assertEqual(ntsc.resolve_input_index(cfg, "composite"), 0)

    def test_no_inputs_falls_back_to_zero(self):
        self.use(FakeV4L2(fail=["--list-inputs"]))
        self.assertEqual(ntsc.resolve_input_index(make_cfg(), "composite"), 0)

    def test_missing_line_input_raises(self):
        fake = self.use(FakeV4L2())
        fake.inputs_out = "\tInput : 0\n\tName : television\n"
        with self.assertRaises(ntsc.ChannelError):
            ntsc.resolve_input_index(make_cfg(), "svideo")


class Signal(DeviceTestCase):

    def test_parses_strength_and_afc(self):
        fake = self.use(FakeV4L2(signal_for={175.25: fixture("v4l2-get-tuner-locked.txt")}))
        fake.freq = 175.25
        self.assertEqual(ntsc.get_signal(make_cfg()), (87, -12))

    def test_no_signal(self):
        self.use(FakeV4L2())
        self.assertEqual(ntsc.get_signal(make_cfg()), (0, 0))

    def test_800e_without_firmware(self):
        fake = self.use(FakeV4L2())
        fake.nosignal = fixture("v4l2-get-tuner-800e-nofw.txt")
        self.assertEqual(ntsc.get_signal(make_cfg()), (0, 0))

    def test_unparseable_or_failed(self):
        fake = self.use(FakeV4L2())
        fake.nosignal = "Tuner 0:\n\tName : something else\n"
        self.assertEqual(ntsc.get_signal(make_cfg()), (None, None))
        self.use(FakeV4L2(fail=["--get-tuner"]))
        self.assertEqual(ntsc.get_signal(make_cfg()), (None, None))


class Tune(DeviceTestCase):

    def test_tuner_channel_sequence(self):
        fake = self.use(FakeV4L2())
        ntsc.tune(make_cfg(), ntsc.parse_channel("us-cable-hrc:5"))
        self.assertEqual(fake.opts(), ["--list-inputs", "--set-input=0",
                                       "--set-standard=ntsc", "--set-freq=78.0000",
                                       "--get-freq"])

    def test_line_input_does_not_set_frequency(self):
        fake = self.use(FakeV4L2())
        ntsc.tune(make_cfg(), ntsc.parse_channel("input:composite"))
        self.assertIn("--set-input=3", fake.opts())
        self.assertFalse(any(o.startswith("--set-freq") for o in fake.opts()))

    def test_tuner_that_does_not_move_raises(self):
        # e.g. 800e with xc3028-v27.fw missing: --set-freq exits 0, --get-freq says 0 MHz.
        self.use(FakeV4L2(stuck=True))
        with self.assertRaisesRegex(ntsc.ChannelError,
                                    r"asked for 175\.250 MHz, device reports 0\.000 MHz.*firmware"):
            ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))

    def test_readback_within_one_tuner_step_is_accepted(self):
        fake = self.use(FakeV4L2())
        fake.freq_out = "Frequency for tuner 0: 2805 (175.312500 MHz)\n"   # +62.5 kHz
        ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))
        fake.freq_out = "Frequency for tuner 0: 2806 (175.375000 MHz)\n"   # +125 kHz
        with self.assertRaises(ntsc.ChannelError):
            ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))

    def test_unreportable_frequency_trusts_set_freq(self):
        # Drivers that can't report the frequency must not be broken by the check.
        self.use(FakeV4L2(stuck=True, fail=["--get-freq"]))
        ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))
        fake = self.use(FakeV4L2(stuck=True))
        fake.freq_out = "something unexpected\n"
        ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))

    def test_set_freq_failure_raises(self):
        self.use(FakeV4L2(fail=["--set-freq"]))
        with self.assertRaises(ntsc.ChannelError):
            ntsc.tune(make_cfg(), ntsc.parse_channel("us-bcast:7"))

    def test_standard_spelling_fallback(self):
        tried = []

        def fake(cmd, **kw):
            tried.append(cmd[3])
            return completed(cmd, returncode=0 if cmd[3] == "--set-standard=NTSC-M" else 1)

        self.use(fake)
        self.assertTrue(ntsc.set_standard(make_cfg()))
        self.assertEqual(tried, ["--set-standard=ntsc", "--set-standard=ntsc-m",
                                 "--set-standard=NTSC-M"])


class CmdSignal(DeviceTestCase):

    def signal(self, channel):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        buf = io.StringIO()
        with mock.patch("ntsc_tvh.time.sleep"), redirect_stdout(buf):
            ntsc.cmd_signal(make_cfg(tmp.name), argparse.Namespace(channel=channel, wait=None))
        return buf.getvalue()

    def test_tuner_channel(self):
        self.use(FakeV4L2(signal_for={175.25: fixture("v4l2-get-tuner-locked.txt")}))
        self.assertEqual(self.signal("us-bcast:7"),
                         "us-bcast:7  175.250 MHz  signal=87%  afc=-12 Hz\n")

    def test_tuner_that_does_not_move(self):
        self.use(FakeV4L2(stuck=True))
        with self.assertRaisesRegex(ntsc.ChannelError, "did not tune"):
            self.signal("us-bcast:7")

    def test_line_input_selects_input_without_measuring(self):
        fake = self.use(FakeV4L2())
        self.assertEqual(self.signal("input:composite"),
                         "input:composite: selected; line inputs have no RF signal to measure\n")
        self.assertIn("--set-input=3", fake.opts())
        self.assertNotIn("--get-tuner", fake.opts())


if __name__ == "__main__":
    unittest.main()
