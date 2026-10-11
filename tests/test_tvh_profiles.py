"""examples/tvh-analog-profiles: API calls, the speed gate, and the spawn command lines."""

import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import REPO

SCRIPT = os.path.join(REPO, "examples", "tvh-analog-profiles")


def load():
    loader = importlib.machinery.SourceFileLoader("tvh_analog_profiles", SCRIPT)
    spec = importlib.util.spec_from_loader("tvh_analog_profiles", loader)
    mod = importlib.util.module_from_spec(spec)
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = dont_write
    return mod


prof = load()


class FakeApi:
    """Stands in for Tvh.call(): a TVHeadend with some profiles already present."""

    def __init__(self, codecs=(), streams=()):
        self.nodes = {f"c{i}": n for i, n in enumerate(codecs)}
        self.nodes.update({f"s{i}": n for i, n in enumerate(streams)})
        self.calls = []

    def __call__(self, path, **form):
        self.calls.append((path, form))
        if path == "codec_profile/list":
            return {"entries": [{"uuid": u} for u in self.nodes if u.startswith("c")]}
        if path == "profile/list":
            return {"entries": [{"key": u} for u in self.nodes if u.startswith("s")]}
        if path == "idnode/load":
            return {"entries": [{"params": [{"id": "name", "value": self.nodes[form["uuid"]]}]}]}
        return {}

    def writes(self):
        return [(p, f) for p, f in self.calls
                if p in ("codec_profile/create", "profile/create", "idnode/save", "idnode/delete")]


class Profiles(unittest.TestCase):

    def run_main(self, *argv, api=None, speed=10.0):
        api = api or FakeApi()
        out = io.StringIO()
        with mock.patch.object(prof.Tvh, "call", side_effect=api), \
             mock.patch.object(prof, "bench", return_value=speed) as bench, \
             mock.patch.object(sys, "argv", ["tvh-analog-profiles", *argv]), redirect_stdout(out):
            prof.main()
        self.bench = bench
        return api, out.getvalue()

    def created(self, api):
        return [(p, f.get("class"), f["conf"]["name"]) for p, f in api.writes() if p.endswith("create")]

    def test_creates_everything_on_a_fast_machine(self):
        api, _ = self.run_main()
        self.assertEqual(self.created(api), [
            ("codec_profile/create", "libx264", "analog-h264"),
            ("codec_profile/create", "aac", "analog-aac"),
            ("profile/create", "profile-transcode", "analog-h264-aac-matroska"),
            ("profile/create", "profile-mpegts-spawn", "analog-h264-aac-mpegts"),
            ("profile/create", "profile-mpegts-spawn", "analog-mobile"),
            ("profile/create", "profile-mpegts-spawn", "analog-h265-aac-mpegts"),
            ("profile/create", "profile-mpegts-spawn", "analog-av1-opus-webm"),
            ("profile/create", "profile-mpegts-spawn", "analog-h264-720p60"),
        ])
        self.assertEqual(self.bench.call_count, 3)          # only the gated ones are timed

    def test_slow_machine_gets_only_the_base_profiles(self):
        api, out = self.run_main(speed=1.5)
        names = [n for _, _, n in self.created(api)]
        self.assertNotIn("analog-h265-aac-mpegts", names)
        self.assertNotIn("analog-h264-720p60", names)
        self.assertIn("analog-mobile", names)
        self.assertIn("skip   stream profile analog-av1-opus-webm: 1.5x real time (needs 2.0x)", out)

    def test_min_speed_and_all(self):
        api, _ = self.run_main("--min-speed", "1.0", speed=1.5)
        self.assertEqual(len(self.created(api)), 8)
        api, _ = self.run_main("--all", speed=0.1)
        self.assertEqual(len(self.created(api)), 8)
        self.assertEqual(self.bench.call_count, 0)

    def test_no_ffmpeg_skips_gated_profiles(self):
        api, out = self.run_main(speed=None)
        self.assertEqual(len(self.created(api)), 5)
        self.assertIn("run inside the TVHeadend container", out)

    def test_existing_profiles_are_updated_in_place(self):
        api = FakeApi(codecs=["analog-h264", "webtv-h264"], streams=["analog-mobile", "pass"])
        api, out = self.run_main(api=api)
        saves = {f["node"]["name"]: f["node"]["uuid"] for p, f in api.writes() if p == "idnode/save"}
        self.assertEqual(saves, {"analog-h264": "c0", "analog-mobile": "s0"})
        self.assertNotIn("analog-mobile", [n for _, _, n in self.created(api)])

    def test_transcode_save_names_every_list_field(self):
        # The API resets list fields left out of a save (seen live: an adapter lost its networks).
        api = FakeApi(streams=["analog-h264-aac-matroska"])
        api, _ = self.run_main(api=api)
        (node,) = [f["node"] for p, f in api.writes()
                   if p == "idnode/save" and f["node"]["name"] == "analog-h264-aac-matroska"]
        for field in ("src_vcodec", "src_acodec", "src_scodec"):
            self.assertEqual(node[field], [])

    def test_dry_run_writes_nothing(self):
        api, out = self.run_main("--dry-run")
        self.assertEqual(api.writes(), [])
        self.assertIn("would create stream profile analog-h264-720p60", out)

    def test_remove(self):
        api = FakeApi(codecs=["analog-h264", "analog-aac", "webtv-h264"],
                      streams=["analog-mobile", "analog-av1-opus-webm", "pass"])
        api, _ = self.run_main("--remove", api=api)
        self.assertEqual([f["uuid"] for p, f in api.writes()], ["s0", "s1", "c0", "c1"])


class SpawnCommands(unittest.TestCase):

    def cmd(self, name):
        return prof.spawn_cmdline(next(p for p in prof.SPAWN if p["name"] == name))

    def test_reads_stdin_writes_stdout(self):
        for p in prof.SPAWN:
            with self.subTest(p["name"]):
                cmd = prof.spawn_cmdline(p)
                self.assertIn("-i pipe:0", cmd)
                self.assertTrue(cmd.endswith(f"-f {p['fmt']} pipe:1"))
                self.assertNotIn("'", cmd)          # TVHeadend splits on spaces, no quoting
                self.assertNotIn('"', cmd)

    def test_deinterlace_only_flagged_frames_except_field_rate(self):
        self.assertIn("-vf bwdif=0:-1:1 ", self.cmd("analog-h264-aac-mpegts"))
        self.assertIn("-vf bwdif=1:-1:1,scale=960:720", self.cmd("analog-h264-720p60"))

    def test_svt_av1_banner_silenced(self):
        self.assertTrue(self.cmd("analog-av1-opus-webm").startswith("env SVT_LOG=1 ffmpeg "))
        self.assertTrue(self.cmd("analog-h265-aac-mpegts").startswith("ffmpeg "))

    def test_bench_parses_speed_and_forces_deinterlace(self):
        p = next(p for p in prof.SPAWN if p["name"] == "analog-h264-720p60")
        result = mock.Mock(returncode=0, stderr="frame= 1 speed=1.2x\rframe= 9 speed=4.75x \n")
        with mock.patch.object(prof.subprocess, "run", return_value=result) as run:
            self.assertEqual(prof.bench(p), 4.75)
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("-vf") + 1], "bwdif=1:-1:0,scale=960:720:flags=lanczos,setsar=1")
        with mock.patch.object(prof.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(prof.bench(p))


if __name__ == "__main__":
    unittest.main()
