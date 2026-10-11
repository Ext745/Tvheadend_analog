"""Shared test helpers: load the ntsc-tvh script as a module and fake v4l2-ctl.

ntsc-tvh has no .py extension (it is installed as /usr/local/bin/ntsc-tvh), so it
is loaded with SourceFileLoader and registered as sys.modules["ntsc_tvh"]; patch
targets are therefore spelled "ntsc_tvh.<name>".
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "ntsc-tvh")
FIXTURES = os.path.join(REPO, "tests", "fixtures")


def load_ntsc():
    if "ntsc_tvh" in sys.modules:
        return sys.modules["ntsc_tvh"]
    loader = importlib.machinery.SourceFileLoader("ntsc_tvh", SCRIPT)
    spec = importlib.util.spec_from_loader("ntsc_tvh", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ntsc_tvh"] = mod
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True   # no __pycache__/ntsc-tvh.*.pyc next to the script
    try:
        loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = dont_write
    return mod


ntsc = load_ntsc()


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return fh.read()


def completed(cmd, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def make_cfg(tmpdir=None, **overrides):
    """DEFAULTS with state/lock/m3u paths moved under tmpdir, plus overrides."""
    cfg = dict(ntsc.DEFAULTS)
    if tmpdir:
        cfg["STATE_FILE"] = os.path.join(tmpdir, "state", "state.json")
        cfg["M3U_FILE"] = os.path.join(tmpdir, "state", "ntsc.m3u")
        cfg["LOCK_FILE"] = os.path.join(tmpdir, "state", "ntsc-tvh.lock")
    cfg["SELF_PATH"] = "/usr/local/bin/ntsc-tvh"
    cfg.update(overrides)
    return cfg


class FakeV4L2:
    """Stands in for ntsc.run(): answers v4l2-ctl calls from fixtures and records them.

    `signal_for` maps a tuned frequency (MHz, float) to the --get-tuner fixture text;
    untuned/unknown frequencies get the no-signal fixture. `stuck=True` makes --set-freq
    succeed without moving the tuner (an xc3028 with no firmware reads back 0 MHz).
    """

    def __init__(self, all_fixture="v4l2-all-hvr1950.txt",
                 inputs_fixture="v4l2-list-inputs-hvr1950.txt",
                 signal_for=None, fail=(), stuck=False):
        self.all_out = fixture(all_fixture)
        self.inputs_out = fixture(inputs_fixture)
        self.locked = fixture("v4l2-get-tuner-locked.txt")
        self.nosignal = fixture("v4l2-get-tuner-nosignal.txt")
        self.signal_for = signal_for or {}
        self.fail = set(fail)        # option prefixes that should return rc=1
        self.stuck = stuck
        self.freq_out = None         # set to override the --get-freq output text
        self.calls = []
        self.freq = None

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        opt = cmd[3] if len(cmd) > 3 else ""
        if any(opt.startswith(f) for f in self.fail):
            return completed(cmd, returncode=1, stderr=f"{opt}: failed")
        if opt == "--all":
            return completed(cmd, self.all_out)
        if opt == "--list-inputs":
            return completed(cmd, self.inputs_out)
        if opt.startswith("--set-freq="):
            if not self.stuck:
                self.freq = float(opt.split("=", 1)[1])
            return completed(cmd)
        if opt == "--get-freq":
            if self.freq_out is not None:
                return completed(cmd, self.freq_out)
            mhz = self.freq or 0.0
            return completed(cmd, f"Frequency for tuner 0: {round(mhz * 16)} ({mhz:.6f} MHz)\n")
        if opt == "--get-tuner":
            hit = self.signal_for.get(round(self.freq, 4)) if self.freq is not None else None
            return completed(cmd, hit or self.nosignal)
        return completed(cmd)

    def opts(self):
        """The v4l2-ctl option of every call, in order (drops the '-d DEV' prefix)."""
        return [c[3] for c in self.calls if len(c) > 3]
