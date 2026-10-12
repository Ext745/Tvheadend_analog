"""tvh-setup --atsc: network from the undated mux list, tuner, guide, dead muxes, channels."""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import make_cfg, ntsc

VERSION = "4.3-2891~ga87be591f"
UNDATED = "atsc-t/us/atsc-t_us-ATSC-center-frequencies-8VS"
LISTS = [{"key": UNDATED, "val": "United States: us-ATSC-center-frequencies-8VSB"},
         {"key": UNDATED + "B-07202", "val": "United States: us-ATSC-center-frequencies-8VSB-072020"}]
# what the air carries: MHz -> services found by a scan
AIR = {497: 7, 515: 6}
MUX_LIST = [497, 503, 509, 515]


class FakeAtsc:
    """TVHeadend with ATSC tuners, answering TvhApi.call(path, **form) like 4.3-2891."""

    def __init__(self, tuners=1, air=AIR):
        self.air = air
        self.nets, self.classes, self.muxes, self.services, self.channels = {}, {}, {}, {}, {}
        self.tuners = {f"fe{i}": {"enabled": False, "networks": [], "ota_epg": True,
                                  "powersave": False, "displayname": f"Tuner {i} : ATSC-T #0",
                                  "fe_path": f"/dev/dvb/adapter{i}/frontend0"}
                       for i in range(tuners)}
        self.psip_enabled = True
        self.ota_timeout = 600
        self.writes = []
        self.n = 0

    def uuid(self, kind):
        self.n += 1
        return f"{kind}{self.n}"

    def add_net(self, name, freqs=MUX_LIST, scanned=False, cls=ntsc.ATSC_CLASS):
        u = self.uuid("net")
        self.nets[u] = {"uuid": u, "networkname": name, "enabled": True}
        self.classes[u] = cls
        for f in freqs:
            m = self.uuid("mux")
            self.muxes[m] = {"uuid": m, "network_uuid": u, "frequency": f * 1000000,
                             "enabled": 1, "scan_state": 0, "scan_result": 0, "num_svc": 0}
        if scanned:
            self.scan(u)
            for s in list(self.services):
                self.map(s)
        return u

    def scan(self, net):
        for m in self.muxes.values():
            if m["network_uuid"] != net or not m["enabled"]:
                continue
            n = self.air.get(round(m["frequency"] / 1e6), 0)
            m["scan_result"] = 1 if n else 2
            if n and not m["num_svc"]:
                for _ in range(n):
                    s = self.uuid("svc")
                    self.services[s] = {"uuid": s, "multiplex_uuid": m["uuid"], "channel": []}
                m["num_svc"] = n

    def map(self, s):
        c = self.uuid("ch")
        self.channels[c] = {"uuid": c, "name": c, "services": [s], "tags": []}
        self.services[s]["channel"] = [c]

    def __call__(self, path, **form):
        if path in ("mpegts/network/create", "idnode/save", "idnode/delete",
                    "mpegts/network/scan", "service/mapper/save", "epggrab/config/save",
                    "epggrab/ota/trigger"):
            self.writes.append((path, form))
        if path == "serverinfo":
            return {"sw_version": VERSION}
        if path == "mpegts/network/builders":
            return {"entries": [{"class": ntsc.ATSC_CLASS, "props": [
                {"id": f} for f in ntsc.ATSC_NET_FIELDS]}]}
        if path == "mpegts/network/grid":
            return {"entries": list(self.nets.values())}
        if path == "idnode/load":
            return {"entries": [{"class": self.classes[form["uuid"]]}]}
        if path == "dvb/scanfile/list":
            return {"entries": LISTS}
        if path == "hardware/tree":
            if form["uuid"] == "root":
                return [{"uuid": f"ad{u}"} for u in self.tuners]
            u = form["uuid"][2:]
            return [{"uuid": "c" + u, "class": "linuxdvb_frontend_atsc_c", "params": []},
                    {"uuid": u, "class": ntsc.ATSC_FE_CLASS, "params": [
                        {"id": k, "value": v} for k, v in self.tuners[u].items()]}]
        if path == "mpegts/mux/grid":
            return {"entries": list(self.muxes.values())}
        if path == "mpegts/service/grid":
            return {"entries": list(self.services.values())}
        if path == "channel/grid":
            return {"entries": list(self.channels.values())}
        if path == "channeltag/grid":
            return {"entries": []}
        if path == "epggrab/module/list":
            return {"entries": [{"uuid": "psip", "title": "Over-the-air: PSIP: ATSC Grabber",
                                 "status": "epggrabmodEnabled" if self.psip_enabled
                                 else "epggrabmodNone"}]}
        if path == "epggrab/config/load":
            return {"entries": [{"params": [{"id": "ota_timeout", "value": self.ota_timeout},
                                            {"id": "ota_cron", "value": "# x\n4 2 * * *\n"}]}]}
        if path == "epg/events/grid":
            return {"totalCount": 0}
        if path == "mpegts/network/create":
            return {"uuid": self.add_net(form["conf"]["networkname"])}
        if path == "idnode/save":
            node = dict(form["node"])
            u = node.pop("uuid")
            if u == "psip":
                self.psip_enabled = node["enabled"]
            else:
                (self.tuners.get(u) or self.nets.get(u) or self.muxes[u]).update(node)
            return {}
        if path == "epggrab/config/save":
            self.ota_timeout = form["node"]["ota_timeout"]
            return {}
        if path == "mpegts/network/scan":
            self.scan(form["uuid"])
            return {}
        if path == "service/mapper/save":
            for s in form["node"]["services"]:
                self.map(s)
            return {}
        if path == "epggrab/ota/trigger":
            return {}
        if path == "idnode/delete":
            u = form["uuid"]
            self.nets.pop(u, None)
            self.channels.pop(u, None)
            return {}
        raise AssertionError(f"unexpected call {path}")


def args(**kw):
    a = dict(url="http://tvh:9981", user=None, password=None, network_name=None,
             m3u_url=None, status=False, teardown=False, dry_run=False, atsc=True,
             tuner=None, mux_list=None, rescan=False)
    a.update(kw)
    return argparse.Namespace(**a)


class AtscTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.tvh = FakeAtsc()
        for target, kw in (("ntsc_tvh.TvhApi.call",
                            {"side_effect": lambda path, **f: self.tvh(path, **f)}),
                           ("ntsc_tvh.time.sleep", {}),
                           ("ntsc_tvh.card_adapters", {"return_value": {}})):
            p = mock.patch(target, **kw)
            self.mocks = getattr(self, "mocks", {})
            self.mocks[target] = p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            ntsc.cmd_tvh_setup(self.cfg, args(**kw))
        return out.getvalue()

    def written(self, path):
        return [f for p, f in self.tvh.writes if p == path]

    def test_fresh_setup(self):
        out = self.run_cmd()
        create = self.written("mpegts/network/create")
        self.assertEqual(len(create), 1)
        self.assertEqual(create[0]["class"], "dvb_network_atsc_t")
        self.assertEqual(create[0]["conf"]["scanfile"], UNDATED)      # the undated list
        net = next(iter(self.tvh.nets))
        fe = self.tvh.tuners["fe0"]
        self.assertTrue(fe["enabled"])
        self.assertEqual(fe["networks"], [net])
        self.assertFalse(fe["powersave"])           # not an analog card
        self.assertEqual(self.tvh.ota_timeout, 120)
        # 503 and 509 carry nothing: switched off; the stations got channels
        off = sorted(m["frequency"] // 1000000 for m in self.tvh.muxes.values()
                     if not m["enabled"])
        self.assertEqual(off, [503, 509])
        self.assertEqual(len(self.tvh.channels), 13)
        self.assertEqual(self.written("epggrab/ota/trigger"), [{"trigger": 5}])
        self.assertIn("muxes:        4: 2 with stations, 2 switched off", out)
        self.assertIn("channels:     13", out)

    def test_second_run_changes_nothing(self):
        self.run_cmd()
        self.tvh.writes.clear()
        out = self.run_cmd()
        self.assertEqual(self.tvh.writes, [])
        self.assertIn("every mux has been scanned", out)

    def test_no_signal_switches_nothing_off(self):
        self.tvh.air = {}
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd()
        self.assertIn("no station on any of the 4 muxes", str(cm.exception))
        self.assertTrue(all(m["enabled"] for m in self.tvh.muxes.values()))

    def test_hybrid_tuner_gets_power_save(self):
        self.mocks["ntsc_tvh.card_adapters"].return_value = {0: "'800e'"}
        out = self.run_cmd()
        self.assertTrue(self.tvh.tuners["fe0"]["powersave"])
        self.assertIn("also the analog tuner '800e'", out)

    def test_two_tuners_need_a_choice(self):
        self.tvh = FakeAtsc(tuners=2)
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd()
        self.assertIn("pick the tuner for ATSC with --tuner N", str(cm.exception))
        self.run_cmd(tuner="0")         # both names end in "ATSC-T #0": the number is the adapter
        self.assertTrue(self.tvh.tuners["fe0"]["enabled"])
        self.assertFalse(self.tvh.tuners["fe1"]["enabled"])

    def test_two_atsc_networks_is_an_error(self):
        self.tvh.add_net("ATSC")
        self.tvh.add_net("ATSC 2")
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd(status=True)
        self.assertIn("One antenna needs one network", str(cm.exception))

    def test_adopts_existing_network_and_ignores_analog_ones(self):
        self.tvh.add_net("Analog (ntsc-tvh)", freqs=[], cls="iptv_auto_network")
        u = self.tvh.add_net("My antenna", scanned=True)
        self.tvh.tuners["fe0"].update(enabled=True, networks=[u])
        self.run_cmd()
        self.assertEqual(self.written("mpegts/network/create"), [])
        self.assertEqual(self.written("mpegts/network/scan"), [])

    def test_duplicate_twin_is_switched_off_before_mapping(self):
        u = self.tvh.add_net("ATSC", freqs=[497, 497.028, 515])
        self.tvh.scan(u)                  # both 497 MHz muxes find the same 7 stations
        self.tvh.tuners["fe0"].update(enabled=True, networks=[u])
        self.run_cmd()
        off = [m for m in self.tvh.muxes.values() if not m["enabled"]]
        self.assertEqual(len(off), 1)
        self.assertEqual(len(self.tvh.channels), 13)     # 7 + 6, not 7 twice

    def test_duplicate_twins_both_mapped_are_left_to_a_person(self):
        u = self.tvh.add_net("ATSC", freqs=[497, 497.028, 515], scanned=True)
        self.tvh.tuners["fe0"].update(enabled=True, networks=[u])
        out = self.run_cmd()
        self.assertTrue(all(m["enabled"] for m in self.tvh.muxes.values()))
        self.assertIn("497 MHz: two muxes each, both with channels", out)

    def test_rescan_turns_switched_off_muxes_back_on(self):
        self.run_cmd()
        self.tvh.air = {**AIR, 503: 2}        # a new station came on air
        self.run_cmd(rescan=True)
        on = sorted(m["frequency"] // 1000000 for m in self.tvh.muxes.values() if m["enabled"])
        self.assertEqual(on, [497, 503, 515])
        self.assertEqual(len(self.tvh.channels), 15)

    def test_dry_run_sends_no_writes(self):
        out = self.run_cmd(dry_run=True)
        self.assertEqual(self.tvh.writes, [])
        self.assertIn("would POST http://tvh:9981/api/mpegts/network/create", out)


if __name__ == "__main__":
    unittest.main()
