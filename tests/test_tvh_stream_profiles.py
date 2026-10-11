"""examples/tvh-stream-profiles: API calls, the speed gate, and the spawn command lines."""

import importlib.machinery
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import REPO

SCRIPT = os.path.join(REPO, "examples", "tvh-stream-profiles")


def load():
    loader = importlib.machinery.SourceFileLoader("tvh_stream_profiles", SCRIPT)
    spec = importlib.util.spec_from_loader("tvh_stream_profiles", loader)
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
    """Stands in for Tvh.call(): a TVHeadend with some profiles already present, and a
    default DVR profile (name "") whose storage/pathname new DVR profiles copy."""

    DEFAULT_DVR = {"storage": "/recordings/", "pathname": "$t/$t -$ e -$ s$n.$x"}

    def __init__(self, codecs=(), streams=(), dvrs=()):
        self.nodes = {f"c{i}": n for i, n in enumerate(codecs)}
        self.nodes.update({f"s{i}": n for i, n in enumerate(streams)})
        self.nodes["d-default"] = ""
        self.nodes.update({f"d{i}": n for i, n in enumerate(dvrs)})
        self.calls = []

    def __call__(self, path, **form):
        self.calls.append((path, form))
        if path == "codec_profile/list":
            return {"entries": [{"uuid": u} for u in self.nodes if u.startswith("c")]}
        if path == "profile/list":
            return {"entries": [{"key": u} for u in self.nodes if u.startswith("s")]}
        if path == "dvr/config/grid":
            return {"entries": [{"uuid": u, "name": n} for u, n in self.nodes.items()
                                if u.startswith("d")]}
        if path == "profile/create":       # remember it, so DVR profiles can refer to it
            self.nodes[f"s-new{len(self.nodes)}"] = form["conf"]["name"]
        if path == "idnode/load":
            params = [{"id": "name", "value": self.nodes[form["uuid"]]}]
            if form["uuid"] == "d-default":
                params += [{"id": k, "value": v} for k, v in self.DEFAULT_DVR.items()]
            return {"entries": [{"params": params}]}
        return {}

    def writes(self):
        return [(p, f) for p, f in self.calls
                if p in ("codec_profile/create", "profile/create", "dvr/config/create",
                         "idnode/save", "idnode/delete")]


class Profiles(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.gpu = os.path.join(tmp.name, "renderD128")      # stands in for /dev/dri/...
        open(self.gpu, "w").close()

    def run_main(self, *argv, api=None, speed=10.0, bench=None, clip=True, make_clip=None):
        api = api or FakeApi()
        out = io.StringIO()
        if not any(a.startswith("--vaapi-device") for a in argv):
            argv = (*argv, "--vaapi-device", self.gpu)
        bench = bench or (lambda v, dev, clip: speed)
        with mock.patch.object(prof.Tvh, "call", side_effect=api), \
             mock.patch.object(prof, "bench", side_effect=bench) as b, \
             mock.patch.object(prof, "make_clip",
                               side_effect=make_clip or (lambda path, source: clip)), \
             mock.patch.object(sys, "argv", ["tvh-stream-profiles", *argv]), redirect_stdout(out):
            prof.main()
        self.bench = b
        return api, out.getvalue()

    def cmdline(self, api, name):
        return next(f["conf"]["cmdline"] for p, f in api.writes()
                    if p == "profile/create" and f["conf"]["name"] == name)

    def created(self, api):
        return [(p, f.get("class"), f["conf"]["name"]) for p, f in api.writes() if p.endswith("create")]

    def test_creates_everything_on_a_fast_machine(self):
        api, _ = self.run_main()
        created = self.created(api)
        self.assertEqual(created[:3], [
            ("codec_profile/create", "libx264", "analog-h264"),
            ("codec_profile/create", "aac", "analog-aac"),
            ("profile/create", "profile-transcode", "analog-h264-aac-matroska"),
        ])
        spawn = [(p, c, n) for p, c, n in created if c == "profile-mpegts-spawn"]
        self.assertEqual([n for _, _, n in spawn], [p["name"] for p in prof.SPAWN])
        self.assertEqual([n for p, _, n in created if p == "dvr/config/create"],
                         [d["name"] for d in prof.DVR])
        self.assertEqual(self.bench.call_count, sum(p["gated"] for p in prof.SPAWN))

    def test_sources_option(self):
        api, out = self.run_main("--sources", "digital")
        names = [n for _, _, n in self.created(api)]
        self.assertTrue(names and all(n.startswith(("digital-", "Digital ")) for n in names), names)
        self.assertNotIn("DVR profile Analog", out)
        api, _ = self.run_main("--sources", "analog")
        names = [n for _, _, n in self.created(api)]
        self.assertIn("analog-h264", names)
        self.assertFalse([n for n in names if "igital" in n])
        with self.assertRaises(SystemExit):
            self.run_main("--sources", "analog,dvb-s")

    def test_each_source_is_tested_on_its_own_clip(self):
        clips, seen = [], []
        self.run_main(make_clip=lambda path, source: clips.append(source) or True,
                      bench=lambda v, dev, clip: seen.append(os.path.basename(clip)) or 10.0)
        self.assertEqual(clips, ["analog", "digital"])           # each made once
        self.assertEqual(set(seen), {"analog.ts", "digital.ts"})

    def test_slow_machine_gets_only_the_base_profiles(self):
        api, out = self.run_main(speed=1.5)
        names = [n for _, _, n in self.created(api)]
        self.assertNotIn("analog-h265-aac-mpegts", names)
        self.assertNotIn("analog-h264-720p60", names)
        self.assertIn("analog-mobile", names)
        self.assertIn("skip   stream profile analog-av1-opus-webm: 1.5x real time, needs 2.0x", out)

    def test_min_speed_and_all(self):
        everything = 3 + len(prof.SPAWN) + len(prof.DVR)
        api, _ = self.run_main("--min-speed", "1.0", speed=1.5)
        self.assertEqual(len(self.created(api)), everything)
        api, _ = self.run_main("--all", speed=0.1)
        self.assertEqual(len(self.created(api)), everything)
        self.assertEqual(self.bench.call_count, 0)

    def test_no_ffmpeg_skips_gated_profiles(self):
        api, out = self.run_main(clip=False)
        names = [n for _, _, n in self.created(api)]
        # 2 codec + 4 analog stream (incl. recording) + 1 DVR; every digital one is gated
        self.assertEqual(len(names), 7)
        self.assertIn("Analog H.264", names)
        self.assertNotIn("Analog H.264 (GPU)", names)
        self.assertNotIn("Digital H.264", names)
        self.assertIn("run inside the TVHeadend container", out)
        self.assertEqual(self.bench.call_count, 0)

    def test_no_gpu_device_skips_vaapi_profiles(self):
        api, out = self.run_main("--vaapi-device", self.gpu + "-missing")
        names = [n for _, _, n in self.created(api)]
        self.assertFalse([n for n in names if "vaapi" in n])
        self.assertIn("analog-h264-720p60", names)
        self.assertIn("devices: /dev/dri:/dev/dri", out)

    def test_dvr_profile_records_through_its_stream_profile(self):
        api, _ = self.run_main()
        (conf,) = [f["conf"] for p, f in api.writes()
                   if p == "dvr/config/create" and f["conf"]["name"] == "Analog H.264"]
        self.assertEqual(api.nodes[conf["profile"]], "analog-h264-rec")
        self.assertEqual(conf["storage"], "/recordings/")             # copied from default
        self.assertEqual(conf["pathname"], "$t/$t -$ e -$ s$n.$x")

    def test_existing_dvr_profile_keeps_its_storage_and_pathname(self):
        api, _ = self.run_main(api=FakeApi(dvrs=["Analog H.264"]))
        (node,) = [f["node"] for p, f in api.writes()
                   if p == "idnode/save" and f["node"]["name"] == "Analog H.264"]
        self.assertEqual(node["uuid"], "d0")
        self.assertNotIn("storage", node)
        self.assertNotIn("pathname", node)

    def test_gpu_dvr_profile_needs_the_gpu_recording_profile(self):
        api, out = self.run_main("--vaapi-device", self.gpu + "-missing")
        self.assertNotIn("Analog H.264 (GPU)", [n for _, _, n in self.created(api)])
        self.assertIn("skip   DVR profile Analog H.264 (GPU): stream profile analog-h264-vaapi-rec "
                      "not created", out)

    def test_vaapi_falls_back_to_software_deinterlace(self):
        # e.g. a GPU that decodes/encodes but can't run deinterlace_vaapi
        def bench(v, dev, clip):
            return None if v.get("label") == "all-GPU" else 20.0
        api, out = self.run_main(bench=bench)
        cmd = self.cmdline(api, "analog-h264-vaapi-mpegts")
        self.assertIn(f"-vaapi_device {self.gpu} -i pipe:0", cmd)
        self.assertIn("-vf bwdif=0:-1:1,format=nv12,hwupload -c:v h264_vaapi", cmd)
        self.assertIn("(software deinterlace + GPU encode)", out)

    def test_codec_the_gpu_cant_encode_is_skipped(self):
        def bench(v, dev, clip):
            return None if "av1_vaapi" in v["venc"] else 20.0
        api, out = self.run_main(bench=bench)
        self.assertNotIn("analog-av1-vaapi-webm", [n for _, _, n in self.created(api)])
        self.assertIn("skip   stream profile analog-av1-vaapi-webm: all-GPU: not supported", out)

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
                      streams=["analog-mobile", "analog-av1-opus-webm", "pass"],
                      dvrs=["Analog H.264", "My own profile"])
        api, _ = self.run_main("--remove", api=api)
        # DVR profiles first (they use the stream profiles); the default and others stay
        self.assertEqual([f["uuid"] for p, f in api.writes()], ["d0", "s0", "s1", "c0", "c1"])


class SpawnCommands(unittest.TestCase):

    def cmd(self, name):
        p = next(p for p in prof.SPAWN if p["name"] == name)
        return prof.spawn_cmdline(p, prof.variants(p)[0], "/dev/dri/renderD128")

    def test_reads_stdin_writes_stdout(self):
        for p in prof.SPAWN:
            for v in prof.variants(p):
                with self.subTest(p["name"], variant=v["label"]):
                    cmd = prof.spawn_cmdline(p, v, "/dev/dri/renderD128")
                    self.assertIn("-i pipe:0", cmd)
                    self.assertTrue(cmd.endswith(f"-f {p['fmt']} pipe:1"))
                    self.assertNotIn("'", cmd)      # TVHeadend splits on spaces, no quoting
                    self.assertNotIn('"', cmd)
                    self.assertNotIn("{dev}", cmd)

    def test_vaapi_all_gpu_command(self):
        p = next(p for p in prof.SPAWN if p["name"] == "analog-h264-vaapi-720p60")
        cmd = prof.spawn_cmdline(p, prof.variants(p)[0], "/dev/dri/renderD129")
        self.assertIn("-hwaccel vaapi -hwaccel_device /dev/dri/renderD129 "
                      "-hwaccel_output_format vaapi -i pipe:0", cmd)
        self.assertIn("-vf deinterlace_vaapi=rate=field:auto=1,scale_vaapi=w=960:h=720 "
                      "-c:v h264_vaapi", cmd)
        self.assertNotIn("  ", cmd)

    def test_deinterlace_only_flagged_frames_except_field_rate(self):
        self.assertIn("-vf bwdif=0:-1:1 ", self.cmd("analog-h264-aac-mpegts"))
        self.assertIn("-vf bwdif=1:-1:1,scale=960:720", self.cmd("analog-h264-720p60"))

    def test_gpu_encoders_leave_out_caption_sei(self):
        # h264_vaapi/hevc_vaapi fail on captioned ATSC when asked to copy A/53 captions
        for p in prof.SPAWN:
            for v in prof.variants(p):
                with self.subTest(p["name"], variant=v["label"]):
                    if "h264_vaapi" in v["venc"]:
                        self.assertIn("-sei identifier+timing+recovery_point", v["venc"])
                    if "hevc_vaapi" in v["venc"]:
                        self.assertIn("-sei hdr", v["venc"])

    def test_digital_ac3_profiles_copy_the_main_track(self):
        for p in prof.SPAWN:
            if p.get("source") == "digital":
                with self.subTest(p["name"]):
                    cmd = prof.spawn_cmdline(p, prof.variants(p)[0], "/dev/dri/renderD128")
                    self.assertIn("-map 0:a:0?", cmd)
                    if "-ac3-" in p["name"] or p["name"].endswith("-rec"):
                        self.assertIn("-c:a copy", cmd)

    def test_svt_av1_banner_silenced(self):
        self.assertTrue(self.cmd("analog-av1-opus-webm").startswith("env SVT_LOG=1 ffmpeg "))
        self.assertTrue(self.cmd("analog-h265-aac-mpegts").startswith("ffmpeg "))

    def test_bench_runs_the_variant_on_the_clip(self):
        p = next(p for p in prof.SPAWN if p["name"] == "analog-h264-vaapi-mpegts")
        v = prof.variants(p)[0]
        result = mock.Mock(returncode=0, stderr="frame= 1 speed=1.2x\rframe= 9 speed=4.75x \n")
        with mock.patch.object(prof.subprocess, "run", return_value=result) as run:
            self.assertEqual(prof.bench(v, "/dev/dri/renderD128", "/tmp/clip.ts"), 4.75)
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("-i") - 1], "vaapi")          # -hwaccel_output_format
        self.assertEqual(argv[argv.index("-i") + 1], "/tmp/clip.ts")
        self.assertEqual(argv[argv.index("-vf") + 1], "deinterlace_vaapi=auto=1")
        failed = mock.Mock(returncode=218, stderr="Function not implemented")
        with mock.patch.object(prof.subprocess, "run", return_value=failed):
            self.assertIsNone(prof.bench(v, "/dev/dri/renderD128", "/tmp/clip.ts"))
        with mock.patch.object(prof.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(prof.bench(v, "/dev/dri/renderD128", "/tmp/clip.ts"))


if __name__ == "__main__":
    unittest.main()
