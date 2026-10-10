"""Channel IDs and RF frequency plans (FCC 73.603 broadcast, EIA-542 STD/IRC/HRC cable)."""

import unittest

from helpers import ntsc


class FrequencyAnchors(unittest.TestCase):
    """Picture-carrier values that are published in the plans; any change is a regression."""

    def check(self, table, expected):
        fn = ntsc.TABLES[table]["freq"]
        for ch, mhz in expected.items():
            with self.subTest(table=table, ch=ch):
                self.assertEqual(fn(ch), mhz)

    def test_broadcast(self):
        self.check("us-bcast", {2: 55.25, 3: 61.25, 4: 67.25, 5: 77.25, 6: 83.25,
                                7: 175.25, 13: 211.25, 14: 471.25, 36: 603.25, 69: 801.25,
                                1: None, 70: None})

    def test_cable_std(self):
        self.check("us-cable", {1: 73.25, 2: 55.25, 5: 77.25, 6: 83.25, 7: 175.25,
                                13: 211.25, 14: 121.25, 22: 169.25, 23: 217.25, 36: 295.25,
                                94: 643.25, 95: 91.25, 99: 115.25, 100: 649.25,
                                135: 859.25, 0: None, 136: None})

    def test_cable_irc(self):
        # Identical to STD except channels 5 and 6.
        self.check("us-cable-irc", {5: 79.25, 6: 85.25, 4: 67.25, 7: 175.25, 23: 217.25})

    def test_cable_hrc(self):
        # 1.25 MHz below STD, except 5/6 which are 0.75 MHz above.
        self.check("us-cable-hrc", {1: 72.0, 2: 54.0, 4: 66.0, 5: 78.0, 6: 84.0,
                                    7: 174.0, 14: 120.0, 23: 216.0, 135: 858.0})


class FrequencyPlanProperties(unittest.TestCase):

    def freqs(self, table):
        spec = ntsc.TABLES[table]
        return {ch: spec["freq"](ch) for ch in spec["range"]}

    def test_every_channel_in_range_is_defined(self):
        counts = {"us-bcast": 68, "us-cable": 135, "us-cable-irc": 135, "us-cable-hrc": 135}
        for table, n in counts.items():
            with self.subTest(table=table):
                f = self.freqs(table)
                self.assertEqual(len(f), n)
                self.assertNotIn(None, f.values())

    def test_no_two_channels_share_a_carrier(self):
        for table in ntsc.TABLES:
            with self.subTest(table=table):
                f = list(self.freqs(table).values())
                self.assertEqual(len(f), len(set(f)))

    def test_carriers_sit_on_the_6mhz_comb(self):
        # IRC carriers are 55.25 + 6k MHz and HRC carriers 54.00 + 6k MHz, every channel.
        # STD is the same comb except 5/6 (77.25/83.25), which is why IRC/HRC move them.
        origins = {"us-cable": 55.25, "us-cable-irc": 55.25, "us-cable-hrc": 54.0}
        for table, origin in origins.items():
            for ch, f in self.freqs(table).items():
                if table == "us-cable" and ch in (5, 6):
                    self.assertNotAlmostEqual((f - origin) % 6, 0)
                    continue
                with self.subTest(table=table, ch=ch):
                    self.assertAlmostEqual((f - origin) % 6, 0, places=6)

    def test_all_channels_matches_tables(self):
        chans = ntsc.all_channels(["us-bcast", "us-cable"])
        self.assertEqual(len(chans), 68 + 135)
        self.assertEqual(chans[0]["id"], "us-bcast:2")
        self.assertEqual(chans[-1]["id"], "us-cable:135")


class ParseChannel(unittest.TestCase):

    def test_tuner_channel(self):
        c = ntsc.parse_channel("us-bcast:7")
        self.assertEqual(c, {"id": "us-bcast:7", "kind": "tuner", "input": "television",
                             "label": "Broadcast 7", "group": "Broadcast",
                             "table": "us-bcast", "ch": 7, "freq": 175.25})

    def test_id_is_normalized(self):
        # The id ends up in the pipe:// URL, so equivalent spellings must collapse.
        self.assertEqual(ntsc.parse_channel("  US-Cable:023 ")["id"], "us-cable:23")
        self.assertEqual(ntsc.parse_channel("input:SVideo")["id"], "input:svideo")

    def test_line_inputs(self):
        for name, label in (("composite", "Composite In"), ("svideo", "S-Video In")):
            with self.subTest(name=name):
                c = ntsc.parse_channel(f"input:{name}")
                self.assertEqual(c["kind"], "input")
                self.assertEqual(c["input"], name)
                self.assertEqual(c["label"], label)
                self.assertEqual(c["group"], "Inputs")
                self.assertIsNone(c["freq"])
                self.assertIsNone(c["ch"])

    def test_groups(self):
        self.assertEqual(ntsc.parse_channel("us-cable-irc:5")["group"], "Cable IRC")
        self.assertEqual(ntsc.parse_channel("us-cable-hrc:2")["group"], "Cable HRC")

    def test_rejects_bad_ids(self):
        for bad in ("7", "", "nope:3", "us-bcast:", "us-bcast:seven", "us-bcast:1",
                    "us-bcast:70", "us-cable:0", "input:hdmi"):
            with self.subTest(cid=bad):
                with self.assertRaises(ntsc.ChannelError):
                    ntsc.parse_channel(bad)


if __name__ == "__main__":
    unittest.main()
