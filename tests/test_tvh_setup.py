"""tvh-setup: the TVHeadend API calls, idempotency, dry run, teardown, version checks."""

import argparse
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import REPO, SCRIPT, make_cfg, ntsc

VERSION = "4.3-2891~ga87be591f"
NET_PROPS = ["enabled", "networkname", "url", "max_streams", "scan_create", "skipinitscan",
             "refetch_period"]


class FakeTvh:
    """A TVHeadend in memory, answering TvhApi.call(path, **form) like 4.3-2891 does.

    Creating an auto network reads the playlist into muxes (EPG as the M3U says); a
    network scan gives each mux a service; the mapper gives each service a channel."""

    def __init__(self, playlist=("Composite In", "S-Video In"), props=NET_PROPS):
        self.playlist = list(playlist)
        self.props = props
        self.nets, self.muxes, self.services, self.channels, self.tags = {}, {}, {}, {}, {}
        self.classes = {}
        self.writes = []
        self.n = 0

    def uuid(self, kind):
        self.n += 1
        return f"{kind}{self.n}"

    def add_net(self, name, url, cls="iptv_auto_network", scanned=True, mapped=True, epg=1,
                **kw):
        u = self.uuid("net")
        self.nets[u] = dict({"uuid": u, "networkname": name, "url": url, "enabled": True,
                             "max_streams": 1}, **kw)
        self.classes[u] = cls
        for label in self.playlist:
            m = self.uuid("mux")
            self.muxes[m] = {"uuid": m, "network_uuid": u, "iptv_sname": label, "epg": epg,
                             "scan_state": 0, "scan_result": 1 if scanned else 0,
                             "num_svc": 1 if scanned else 0}
            if scanned:
                s = self.add_service(m)
                if mapped:
                    self.map(s, label)
        return u

    def add_service(self, mux):
        s = self.uuid("svc")
        self.services[s] = {"uuid": s, "multiplex_uuid": mux, "channel": []}
        self.muxes[mux]["num_svc"] = 1
        return s

    def map(self, svc, name, tags=()):
        c = self.uuid("ch")
        self.channels[c] = {"uuid": c, "name": name, "services": [svc], "tags": list(tags)}
        self.services[svc]["channel"] = [c]

    def __call__(self, path, **form):
        if path in ("mpegts/network/create", "idnode/save", "idnode/delete",
                    "mpegts/network/scan", "service/mapper/save"):
            self.writes.append((path, form))
        if path == "serverinfo":
            return {"sw_version": VERSION, "api_version": 20}
        if path == "mpegts/network/builders":
            return {"entries": [{"class": "iptv_network", "props": []},
                                {"class": "iptv_auto_network",
                                 "props": [{"id": p} for p in self.props]}]}
        if path == "mpegts/network/grid":
            return {"entries": list(self.nets.values())}
        if path == "mpegts/mux/grid":
            return {"entries": list(self.muxes.values())}
        if path == "mpegts/service/grid":
            return {"entries": list(self.services.values())}
        if path == "channel/grid":
            return {"entries": list(self.channels.values())}
        if path == "channeltag/grid":
            return {"entries": list(self.tags.values())}
        if path == "idnode/load":
            return {"entries": [{"uuid": form["uuid"], "class": self.classes[form["uuid"]]}]}
        if path == "mpegts/network/create":
            conf = form["conf"]
            u = self.add_net(conf["networkname"], conf["url"], scanned=False,
                             epg=0, max_streams=conf["max_streams"])
            return {"uuid": u}
        if path == "idnode/save":
            node = dict(form["node"])
            u = node.pop("uuid")
            (self.nets.get(u) or self.muxes[u]).update(node)
            return {}
        if path == "idnode/delete":
            u = form["uuid"]
            if u in self.nets:
                del self.nets[u]
                for m in [m for m in self.muxes.values() if m["network_uuid"] == u]:
                    del self.muxes[m["uuid"]]
                    for s in [s for s in self.services.values()
                              if s["multiplex_uuid"] == m["uuid"]]:
                        del self.services[s["uuid"]]
            for d in (self.channels, self.tags):
                d.pop(u, None)
            return {}
        if path == "mpegts/network/scan":
            for m in self.muxes.values():
                if m["network_uuid"] == form["uuid"] and not m["num_svc"]:
                    self.add_service(m["uuid"])
                    m["scan_result"] = 1
            return {}
        if path == "service/mapper/save":
            for s in form["node"]["services"]:
                mux = self.muxes[self.services[s]["multiplex_uuid"]]
                self.map(s, mux["iptv_sname"])
            return {}
        raise AssertionError(f"unexpected call {path}")


def args(**kw):
    a = dict(url="http://tvh:9981", user=None, password=None, network_name=None,
             m3u_url=None, status=False, teardown=False, dry_run=False, atsc=False,
             tuner=None, mux_list=None, rescan=False)
    a.update(kw)
    return argparse.Namespace(**a)


class TvhSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        self.url = "file://" + self.cfg["M3U_FILE"]
        os.makedirs(os.path.dirname(self.cfg["M3U_FILE"]))
        with open(self.cfg["M3U_FILE"], "w") as fh:
            fh.write('#EXTM3U\n#EXTINF:-1 tvg-id="input:composite",Composite In\npipe://x\n'
                     '#EXTINF:-1 tvg-id="input:svideo",S-Video In\npipe://y\n')
        self.tvh = FakeTvh()
        for target in ("ntsc_tvh.TvhApi.call",):
            p = mock.patch(target, side_effect=lambda path, **f: self.tvh(path, **f))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("ntsc_tvh.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def run_cmd(self, cfg=None, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            ntsc.cmd_tvh_setup(cfg or self.cfg, args(**kw))
        return out.getvalue()

    def test_creates_network_scans_and_maps(self):
        out = self.run_cmd()
        create = [f for p, f in self.tvh.writes if p == "mpegts/network/create"]
        self.assertEqual(len(create), 1)
        self.assertEqual(create[0]["class"], "iptv_auto_network")
        self.assertEqual(create[0]["conf"]["url"], self.url)
        self.assertEqual(create[0]["conf"]["max_streams"], 1)
        self.assertEqual(create[0]["conf"]["networkname"], "Analog (ntsc-tvh)")
        self.assertEqual(sorted(c["name"] for c in self.tvh.channels.values()),
                         ["Composite In", "S-Video In"])
        self.assertIn("channels:   2: Composite In, S-Video In", out)

    def test_second_run_changes_nothing(self):
        self.run_cmd()
        self.tvh.writes.clear()
        out = self.run_cmd()
        self.assertEqual(self.tvh.writes, [])
        self.assertEqual(len(self.tvh.nets), 1)
        self.assertIn("already set", out)

    def test_existing_network_gets_fixed_in_place(self):
        u = self.tvh.add_net("Analog (ntsc-tvh)", self.url, max_streams=0, epg=1)
        self.run_cmd()
        self.assertEqual(len(self.tvh.nets), 1)
        self.assertEqual(self.tvh.nets[u]["max_streams"], 1)
        self.assertTrue(all(m["epg"] == 0 for m in self.tvh.muxes.values()))
        self.assertFalse([p for p, _ in self.tvh.writes
                          if p in ("mpegts/network/scan", "service/mapper/save")])

    def add_to_playlist(self, label):
        with open(self.cfg["M3U_FILE"], "a") as fh:
            fh.write(f'#EXTINF:-1 tvg-id="fm:101.1",{label}\npipe://z\n')

    def test_waits_for_tvheadend_to_read_new_playlist_channels(self):
        u = self.tvh.add_net("Analog (ntsc-tvh)", self.url, epg=0)
        self.add_to_playlist("FM 101.1")

        def reread(secs):       # TVHeadend re-reads the file during the wait
            if not any(m["iptv_sname"] == "FM 101.1" for m in self.tvh.muxes.values()):
                m = self.tvh.uuid("mux")
                self.tvh.muxes[m] = {"uuid": m, "network_uuid": u, "iptv_sname": "FM 101.1",
                                     "epg": 0, "scan_state": 0, "scan_result": 0, "num_svc": 0}
        with mock.patch("ntsc_tvh.time.sleep", side_effect=reread):
            out = self.run_cmd()
        self.assertIn("waiting for TVHeadend to read 1 new playlist channel(s) (FM 101.1)", out)
        self.assertIn("3. scan 1 mux(es)", out)
        self.assertIn("FM 101.1", sorted(c["name"] for c in self.tvh.channels.values()))
        self.assertNotIn("not in TVHeadend yet", out)

    def test_dry_run_only_says_it_would_wait(self):
        self.tvh.add_net("Analog (ntsc-tvh)", self.url, epg=0)
        self.add_to_playlist("FM 101.1")
        out = self.run_cmd(dry_run=True)
        self.assertIn("(would wait for TVHeadend to read 1 new playlist channel(s))", out)
        self.assertEqual(self.tvh.writes, [])

    def test_dry_run_sends_no_writes(self):
        self.tvh.add_net("Analog (ntsc-tvh)", self.url, epg=1, scanned=False)
        out = self.run_cmd(dry_run=True)
        self.assertEqual(self.tvh.writes, [])
        self.assertIn('would POST http://tvh:9981/api/idnode/save  node={"uuid": "mux', out)
        self.assertIn("would POST http://tvh:9981/api/mpegts/network/scan  uuid=net", out)

    def test_card_gets_its_own_network(self):
        cfg = dict(self.cfg, CARD="800e")
        self.run_cmd(cfg)
        self.assertEqual([n["networkname"] for n in self.tvh.nets.values()],
                         ["Analog 800e (ntsc-tvh)"])

    def test_adopts_a_hand_made_network_reading_the_playlist(self):
        self.tvh.add_net("AnalogTV", self.url)
        out = self.run_cmd(status=True)
        self.assertIn("'AnalogTV' (found by its playlist URL)", out)
        self.run_cmd()
        self.assertEqual(len(self.tvh.nets), 1)

    def test_two_networks_with_the_name_is_an_error(self):
        self.tvh.add_net("Analog (ntsc-tvh)", self.url)
        self.tvh.add_net("Analog (ntsc-tvh)", self.url)
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd()
        self.assertIn("2 networks named 'Analog (ntsc-tvh)'", str(cm.exception))

    def test_wont_manage_a_plain_iptv_network(self):
        self.tvh.add_net("Analog (ntsc-tvh)", self.url, cls="iptv_network")
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd()
        self.assertIn("is a iptv_network, not an Automatic IPTV Network", str(cm.exception))
        self.assertEqual(self.tvh.writes, [])

    def test_missing_field_names_it_and_the_version(self):
        self.tvh.props = [p for p in NET_PROPS if p != "max_streams"]
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd()
        self.assertIn(f"TVHeadend {VERSION}", str(cm.exception))
        self.assertIn("no field 'max_streams'", str(cm.exception))

    def test_status_plain_language(self):
        self.tvh.add_net("Analog (ntsc-tvh)", self.url, max_streams=0, epg=1)
        out = self.run_cmd(status=True)
        self.assertIn("streams:    unlimited at a time  <- should be 1", out)
        self.assertIn("OTA EPG:    ON for 2 of 2 mux(es)", out)
        self.assertIn("playlist:   " + self.url + ": 2 channel(s)", out)
        self.assertEqual(self.tvh.writes, [])

    def test_teardown_removes_only_its_own(self):
        own = self.tvh.add_net("Analog (ntsc-tvh)", self.url)
        self.tvh.tags["tag1"] = {"uuid": "tag1", "name": "Analog (ntsc-tvh)"}
        other = self.tvh.add_net("ATSC", "")
        # a channel carrying one service from each network must stay
        shared_svc = next(s for s in self.tvh.services.values()
                          if self.tvh.muxes[s["multiplex_uuid"]]["network_uuid"] == own)
        atsc_ch = next(c for c in self.tvh.channels.values()
                       if self.tvh.muxes[self.tvh.services[c["services"][0]]["multiplex_uuid"]]
                       ["network_uuid"] == other)
        atsc_ch["services"].append(shared_svc["uuid"])
        self.run_cmd(teardown=True)
        self.assertEqual(list(self.tvh.nets), [other])
        self.assertIn(atsc_ch["uuid"], self.tvh.channels)
        self.assertEqual(len(self.tvh.channels), 2)        # the two ATSC-network channels
        self.assertEqual(self.tvh.tags, {})


class DigestTest(unittest.TestCase):
    """TVHeadend offers MD5, SHA-256 and SHA-512-256 digest logins; Python 3.12 (in the
    linuxserver image) only knows MD5 and SHA-1, so tvh-setup adds the other two."""

    def test_algorithms(self):
        import hashlib
        h = ntsc._tvh_digest_handler()()
        for algo, name in (("MD5", "md5"), ("SHA-256", "sha256"),
                           ("SHA-512-256", "sha512_256")):
            H, KD = h.get_algorithm_impls(algo)
            self.assertEqual(H("a:b"), hashlib.new(name, b"a:b").hexdigest())
            self.assertEqual(KD("s", "d"), hashlib.new(name, b"s:d").hexdigest())


class IsolationTest(unittest.TestCase):
    def test_core_does_not_import_urllib(self):
        code = ("import importlib.machinery,importlib.util,sys;"
                f"l=importlib.machinery.SourceFileLoader('n',{SCRIPT!r});"
                "m=importlib.util.module_from_spec(importlib.util.spec_from_loader('n',l));"
                "l.exec_module(m);print('urllib.request' in sys.modules)")
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "False", r.stderr)


if __name__ == "__main__":
    unittest.main()
