#!/usr/bin/env python3
r"""Soak (long-run) test logger for real-hardware testing. Linux, stdlib only.

Runs or attaches to the thing under test, samples it at a fixed interval, and
records the evidence that backs a TESTING.md entry:

  tests/soak/logs/<ID>-<rig>/
    samples.csv     time series: elapsed, RSS, CPU temp, errors, resets, kernel events
    summary.json    totals, result, RSS growth rate, commit, config
    output.log      scrubbed output of the process (gitignored; attach to releases)

Modes
  --cmd "..."      run a command (e.g. the app, or a serial reader for an MCU)
  --pid N          attach to a running process (no output parsing)
  --follow PATH    additionally tail a log file for error/reset patterns

Typical uses
  # Host software, 72 h, restart on crash and count it
  soak.py --rig A --hours 72 --restart --cmd "./build/app --config rig-a.conf" \
          --error-pattern "overrun|dropped|ERROR"
  # ESP32 over serial: count resets and panics from the boot log
  soak.py --rig B --hours 168 --cmd "cat /dev/ttyACM0" \
          --reset-pattern "rst:0x|Rebooting" --error-pattern "Guru Meditation|E \("
  # Docker container: attach to its main PID
  soak.py --rig C --hours 336 --pid $(docker inspect -f '{{.State.Pid}}' fm-dx)

Stop early with Ctrl-C; the run is still summarised (duration = actual runtime).
With --append, the result row is added to TESTING.md and the cumulative runtime
updated. Results are evidence, not judgement: a human reviews before raising the
project's maturity level.
"""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from scrub import scrub_text  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTING_MD = ROOT / "TESTING.md"
LOG_ROOT = ROOT / "tests" / "soak" / "logs"
DEFAULT_KERNEL_PATTERN = r"usb .*(reset|disconnect)|I/O error|Out of memory|oom-kill|hung_task|thermal.*throttl"


# ---------- system probes ----------

def git_commit():
    try:
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        dirty = subprocess.call(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD"],
                                stderr=subprocess.DEVNULL) != 0
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def max_temp_c():
    temps = []
    for p in list(pathlib.Path("/sys/class/thermal").glob("thermal_zone*/temp")) + \
             list(pathlib.Path("/sys/class/hwmon").glob("hwmon*/temp*_input")):
        try:
            v = int(p.read_text().strip()) / 1000.0
            if -40 < v < 150:
                temps.append(v)
        except Exception:
            pass
    return round(max(temps), 1) if temps else None


def _tree_pids(pid):
    pids, i = [pid], 0
    while i < len(pids):
        p = pids[i]
        i += 1
        try:
            for t in os.listdir(f"/proc/{p}/task"):
                with open(f"/proc/{p}/task/{t}/children") as f:
                    pids += [int(c) for c in f.read().split()]
        except OSError:
            pass
    return pids


def rss_mb(pid):
    total_kb, alive = 0, False
    for p in _tree_pids(pid):
        try:
            with open(f"/proc/{p}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total_kb += int(line.split()[1])
                        alive = True
        except OSError:
            pass
    return round(total_kb / 1024, 1) if alive else None


def pid_alive(pid):
    return os.path.exists(f"/proc/{pid}")


def kernel_events(since_epoch, pattern):
    """Count kernel log lines matching pattern since start. None if unreadable."""
    rx = re.compile(pattern, re.I)
    for cmd in (["journalctl", "-k", "--no-pager", "-o", "cat", f"--since=@{int(since_epoch)}"],):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if out.returncode == 0:
                return sum(1 for l in out.stdout.splitlines() if rx.search(l))
        except Exception:
            pass
    return None


def slope_per_day(points):
    """Least-squares slope of (hours, value) points, in units/day."""
    pts = [(x, y) for x, y in points if y is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in pts) / den * 24, 2)


# ---------- output watching ----------

class Watcher:
    def __init__(self, log_path, error_rx, reset_rx, echo):
        self.log = open(log_path, "a", encoding="utf-8")
        self.error_rx, self.reset_rx, self.echo = error_rx, reset_rx, echo
        self.errors = self.resets = 0
        self.lock = threading.Lock()

    def line(self, raw, source=""):
        text = raw.rstrip("\r\n")
        with self.lock:
            if self.error_rx and self.error_rx.search(text):
                self.errors += 1
            if self.reset_rx and self.reset_rx.search(text):
                self.resets += 1
            stamp = dt.datetime.now().isoformat(timespec="seconds")
            self.log.write(f"{stamp} {source}{scrub_text(text)}\n")
            self.log.flush()
        if self.echo:
            print(text, flush=True)

    def pump(self, stream, source=""):
        for raw in iter(stream.readline, ""):
            self.line(raw, source)

    def follow(self, path, stop):
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"soak: cannot follow {path}: {e}", file=sys.stderr)
            return
        f.seek(0, os.SEEK_END)
        while not stop.is_set():
            line = f.readline()
            if line:
                self.line(line, "[follow] ")
            else:
                time.sleep(0.5)


# ---------- TESTING.md ----------

def next_run_id():
    if not TESTING_MD.exists():
        return "S-001"
    nums = [int(n) for n in re.findall(r"\|\s*S-(\d+)\s*\|", TESTING_MD.read_text())]
    return f"S-{(max(nums) + 1 if nums else 1):03d}"


def append_testing_row(row):
    text = TESTING_MD.read_text()
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("## Soak"))
    except StopIteration:
        print("soak: no '## Soak' section in TESTING.md; row not appended", file=sys.stderr)
        return
    i = start + 1
    while i < len(lines) and not lines[i].startswith("|"):
        i += 1
    while i < len(lines) and lines[i].startswith("|"):
        i += 1
    lines.insert(i, row)
    # recompute cumulative runtime from the Duration column of S- rows
    total = 0.0
    for l in lines:
        m = re.match(r"\|\s*S-\d+\s*\|[^|]*\|\s*([\d.]+)\s*h", l)
        if m:
            total += float(m.group(1))
    lines = [re.sub(r"(\*\*Cumulative real-hardware runtime:\*\*).*", rf"\g<1> {total:.1f} h", l)
             for l in lines]
    TESTING_MD.write_text("\n".join(lines) + "\n")
    print(f"soak: appended to {TESTING_MD.name}; cumulative runtime {total:.1f} h")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cmd", help="command to run and monitor")
    src.add_argument("--pid", type=int, help="attach to an existing process")
    ap.add_argument("--rig", required=True, help="rig ID from TESTING.md")
    ap.add_argument("--hours", type=float, default=0, help="stop after N hours (0 = until Ctrl-C / exit)")
    ap.add_argument("--interval", type=float, default=60, help="sample interval, seconds")
    ap.add_argument("--config", default="", help="short description of configuration for the log")
    ap.add_argument("--error-pattern", help="regex counted as an error in output")
    ap.add_argument("--reset-pattern", help="regex counted as a device reset in output")
    ap.add_argument("--kernel-pattern", default=DEFAULT_KERNEL_PATTERN, help="regex for kernel log events")
    ap.add_argument("--follow", help="also tail this log file for patterns")
    ap.add_argument("--restart", action="store_true", help="restart --cmd if it exits (counted)")
    ap.add_argument("--max-rss-growth", type=float, help="MB/day RSS growth that marks the run degraded")
    ap.add_argument("--append", action="store_true", help="append result row to TESTING.md")
    ap.add_argument("--quiet", action="store_true", help="don't echo process output")
    a = ap.parse_args()

    run_id = next_run_id()
    outdir = LOG_ROOT / f"{run_id}-{a.rig}"
    outdir.mkdir(parents=True, exist_ok=True)
    error_rx = re.compile(a.error_pattern, re.I) if a.error_pattern else None
    reset_rx = re.compile(a.reset_pattern, re.I) if a.reset_pattern else None
    watcher = Watcher(outdir / "output.log", error_rx, reset_rx, not a.quiet)
    stop = threading.Event()

    t0 = time.time()
    started = dt.datetime.now().astimezone()
    commit = git_commit()
    restarts, exits, proc = 0, [], None

    def launch():
        p = subprocess.Popen(shlex.split(a.cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", bufsize=1, start_new_session=True)
        threading.Thread(target=watcher.pump, args=(p.stdout,), daemon=True).start()
        return p

    if a.cmd:
        proc = launch()
        pid = proc.pid
    else:
        pid = a.pid
        if not pid_alive(pid):
            sys.exit(f"soak: pid {pid} not running")
    if a.follow:
        threading.Thread(target=watcher.follow, args=(a.follow, stop), daemon=True).start()

    interrupted = False

    def on_sigint(*_):
        nonlocal interrupted
        interrupted = True
        stop.set()
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)

    print(f"soak: {run_id} rig={a.rig} commit={commit} pid={pid} -> {outdir}", file=sys.stderr)
    samples_f = open(outdir / "samples.csv", "w", newline="")
    w = csv.writer(samples_f)
    w.writerow(["timestamp", "elapsed_h", "rss_mb", "max_temp_c", "errors", "resets",
                "kernel_events", "restarts"])
    rss_points, temps, kernel_n = [], [], None
    unexpected_exit = False
    next_sample = t0

    while not stop.is_set():
        now = time.time()
        elapsed_h = (now - t0) / 3600
        if a.hours and elapsed_h >= a.hours:
            break
        if proc is not None and proc.poll() is not None:
            exits.append(proc.returncode)
            watcher.line(f"*** process exited with code {proc.returncode} ***", "[soak] ")
            if a.restart:
                restarts += 1
                time.sleep(2)
                proc = launch()
                pid = proc.pid
            else:
                unexpected_exit = True
                break
        elif proc is None and not pid_alive(pid):
            watcher.line(f"*** pid {pid} disappeared ***", "[soak] ")
            unexpected_exit = True
            break
        if now >= next_sample:
            rss = rss_mb(pid)
            temp = max_temp_c()
            kernel_n = kernel_events(t0, a.kernel_pattern)
            rss_points.append((elapsed_h, rss))
            if temp is not None:
                temps.append(temp)
            w.writerow([dt.datetime.now().isoformat(timespec="seconds"), f"{elapsed_h:.4f}", rss, temp,
                        watcher.errors, watcher.resets, kernel_n, restarts])
            samples_f.flush()
            next_sample += a.interval
        stop.wait(min(1.0, max(0.05, next_sample - time.time())))

    # stop what we started
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
    stop.set()
    samples_f.close()

    duration_h = round((time.time() - t0) / 3600, 2)
    warm = [p for p in rss_points if p[0] >= duration_h * 0.1]  # skip warm-up
    # growth rate is only meaningful over a reasonable window
    growth = slope_per_day(warm) if duration_h >= 1 else None
    problems = []
    if unexpected_exit:
        problems.append("process exited")
    if watcher.errors:
        problems.append(f"errors={watcher.errors}")
    if watcher.resets:
        problems.append(f"resets={watcher.resets}")
    if restarts:
        problems.append(f"restarts={restarts}")
    if kernel_n:
        problems.append(f"kernel_events={kernel_n}")
    if a.max_rss_growth is not None and growth is not None and growth > a.max_rss_growth:
        problems.append(f"rss_growth={growth} MB/day")
    if unexpected_exit:
        result = "❌ fail"
    elif problems:
        result = "⚠️ degraded"
    else:
        result = "✅ pass"
    if interrupted and not unexpected_exit:
        problems.append("stopped manually")

    summary = {
        "id": run_id, "rig": a.rig, "commit": commit, "config": a.config,
        "start": started.isoformat(timespec="seconds"),
        "end": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_h": duration_h, "planned_h": a.hours or None, "result": result,
        "errors": watcher.errors, "resets": watcher.resets, "restarts": restarts,
        "exit_codes": exits, "kernel_events": kernel_n,
        "max_temp_c": max(temps) if temps else None,
        "rss_start_mb": next((y for _, y in rss_points if y is not None), None),
        "rss_end_mb": next((y for _, y in reversed(rss_points) if y is not None), None),
        "rss_growth_mb_per_day": growth,
        "notes": ", ".join(problems) or "clean run",
        "command": a.cmd, "pid": a.pid, "error_pattern": a.error_pattern,
        "reset_pattern": a.reset_pattern,
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    notes = summary["notes"]
    if summary["max_temp_c"] is not None:
        notes += f"; max {summary['max_temp_c']} °C"
    if growth is not None and "rss_growth" not in notes:
        notes += f"; RSS {growth:+} MB/day"
    notes += f"; logs: tests/soak/logs/{outdir.name}/"
    row = (f"| {run_id} | {started.date()} | {duration_h} h | {a.rig} | `{commit}` | "
           f"{a.config or '-'} | {result} | {notes} |")
    print("\n" + row)
    if a.append:
        append_testing_row(row)
    return 1 if unexpected_exit else 0


if __name__ == "__main__":
    sys.exit(main())
