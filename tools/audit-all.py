#!/usr/bin/env python3
"""Audit every repository you own for secrets and personal data in FULL git history.

Read-only: it never changes, pushes or rewrites anything. It mirror-clones each repo
(all branches and tags) into a work directory, runs gitleaks and the personal-data
scrubber over every commit, and writes one report sorted by severity.

Requirements: git, gitleaks, and the GitHub CLI (`gh auth login` done) unless
you only use --local.

Usage
  audit-all.py                                  all repos of the logged-in gh user
  audit-all.py --owner Ext745 --public-only
  audit-all.py --local ~/workbench/ai_projects  local checkouts (a repo, or a folder of repos)
  audit-all.py --local . --no-github            just the repo you're in

Severity
  HIGH    secrets: gitleaks hits, credentials, WireGuard/base64 keys, private keys
  MEDIUM  personal identifiers: IMEI/IMSI, ICCID, phone numbers, public IP addresses
  LOW     emails and MAC addresses in file content

Exit code 1 if any PUBLIC repo has HIGH findings.
The report contains file paths and commit IDs, never the values themselves.
Keep it out of your repos anyway.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from scrub import scan_git_history  # noqa: E402

HIGH = {"gitleaks", "credential", "at-credentials", "wireguard-key", "pem-private-key"}
MEDIUM = {"imei-imsi", "iccid", "phone", "public-ip"}
LOW = {"email", "mac"}
MAX_ROWS = 50


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"audit: '{tool}' not found on PATH")


def severity(kind):
    return "HIGH" if kind in HIGH else "MEDIUM" if kind in MEDIUM else "LOW"


# ---------- discovery ----------

def github_repos(owner, public_only, forks, archived):
    if not owner:
        r = run(["gh", "api", "user", "--jq", ".login"])
        if r.returncode:
            sys.exit(f"audit: gh not logged in? {r.stderr.strip()}")
        owner = r.stdout.strip()
    r = run(["gh", "repo", "list", owner, "--limit", "1000",
             "--json", "nameWithOwner,visibility,isFork,isArchived"])
    if r.returncode:
        sys.exit(f"audit: gh repo list failed: {r.stderr.strip()}")
    repos = []
    for x in json.loads(r.stdout):
        vis = x["visibility"].lower()
        if public_only and vis != "public":
            continue
        if x["isFork"] and not forks:
            continue
        if x["isArchived"] and not archived:
            continue
        repos.append({"name": x["nameWithOwner"], "visibility": vis, "fork": x["isFork"],
                      "archived": x["isArchived"], "source": "github"})
    return owner, repos


def is_git_repo(p):
    return run(["git", "-C", str(p), "rev-parse", "--git-dir"]).returncode == 0


def local_repos(paths):
    found = []
    for raw in paths:
        p = pathlib.Path(raw).expanduser().resolve()
        candidates = [p] if is_git_repo(p) else sorted(c for c in p.iterdir() if c.is_dir() and is_git_repo(c)) \
            if p.is_dir() else []
        if not candidates:
            print(f"audit: no git repos at {p}", file=sys.stderr)
        for c in candidates:
            origin = run(["git", "-C", str(c), "remote", "get-url", "origin"]).stdout.strip()
            found.append({"name": str(c), "visibility": "local", "fork": False, "archived": False,
                          "source": "local", "path": str(c), "origin": origin or "-"})
    return found


# ---------- fetching ----------

def _last_line(r, default):
    text = (r.stderr or r.stdout).strip()
    return text.splitlines()[-1] if text else default


def mirror(repo, workdir):
    dest = workdir / (repo["name"].replace("/", "__") + ".git")
    if not dest.exists():
        r = run(["gh", "repo", "clone", repo["name"], str(dest), "--", "--mirror", "--quiet"])
        if r.returncode:
            raise RuntimeError(_last_line(r, "clone failed"))
    # gh adds an "upstream" remote to forks. Its branches aren't in this repo, so drop
    # them, or a fork's report counts upstream's commits as the owner's. Older mirrors
    # may already hold those refs; removing the remote and pruning cleans them too.
    run(["git", "-C", str(dest), "remote", "remove", "upstream"])
    r = run(["git", "-C", str(dest), "fetch", "--prune", "origin"])
    if r.returncode:
        raise RuntimeError(_last_line(r, "fetch failed"))
    return dest


def fork_count(name):
    r = run(["gh", "api", f"repos/{name}", "--jq", ".forks_count"])
    return r.stdout.strip() if r.returncode == 0 else "?"


# ---------- scanning ----------

def gitleaks_scan(path):
    """Full-history gitleaks scan. Uses the repo's own .gitleaks.toml from HEAD if it has one."""
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "r.json")
        cmd = ["gitleaks", "git", str(path), "--log-opts=--all", "--redact", "--no-banner",
               "--exit-code", "0", "--report-format", "json", "--report-path", report,
               "--log-level", "error"]
        cfg = run(["git", "-C", str(path), "show", "HEAD:.gitleaks.toml"])
        if cfg.returncode == 0:
            cfg_path = os.path.join(tmp, "gitleaks.toml")
            pathlib.Path(cfg_path).write_text(cfg.stdout)
            cmd += ["--config", cfg_path]
        r = run(cmd)
        if r.returncode:
            raise RuntimeError(f"gitleaks failed: {r.stderr.strip()[-300:]}")
        try:
            data = json.loads(pathlib.Path(report).read_text() or "[]")
        except (OSError, json.JSONDecodeError):
            data = []
    return [{"kind": "gitleaks", "rule": d.get("RuleID", "?"), "file": d.get("File", "?"),
             "line": d.get("StartLine", ""), "commit": (d.get("Commit") or "")[:10]} for d in data]


def audit_one(repo, workdir):
    path = pathlib.Path(repo["path"]) if repo["source"] == "local" else mirror(repo, workdir)
    findings = gitleaks_scan(path)
    findings += [dict(f, rule=f["kind"], line="") for f in scan_git_history(str(path))]
    for f in findings:
        f["severity"] = severity(f["kind"])
    commits = run(["git", "-C", str(path), "rev-list", "--all", "--count"]).stdout.strip()
    return findings, commits


# ---------- reporting ----------

def status(repo, counts):
    public = repo["visibility"] == "public"
    if repo.get("error"):
        return "⚠️ not scanned"
    if counts["HIGH"]:
        return "🔴 ACT NOW: public, secrets exposed" if public else "🟠 fix before publishing"
    if counts["MEDIUM"]:
        return "🟡 review: personal data exposed" if public else "🟡 review before publishing"
    if counts["LOW"]:
        return "⚪ minor"
    return "✅ clean"


def rank(item):
    repo, counts = item
    public = repo["visibility"] == "public"
    return (repo.get("error") is None, -(counts["HIGH"] * 1000 + counts["MEDIUM"] * 10 + counts["LOW"]) * (2 if public else 1))


def write_report(path, owner, results):
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    gl = run(["gitleaks", "version"]).stdout.strip()
    L = [f"# Repository audit — {owner or 'local'}", "",
         f"Generated {now} · gitleaks {gl} · full history, all refs · values redacted", "",
         "## Summary", "",
         "| Repo | Visibility | Fork | Forks | Commits | HIGH | MED | LOW | Status |",
         "|---|---|---|---|---|---|---|---|---|"]
    items = []
    for repo, findings in results:
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            counts[f["severity"]] += 1
        items.append((repo, counts))
    for repo, c in sorted(items, key=rank):
        L.append(f"| {repo['name']} | {repo['visibility']} | {'yes' if repo['fork'] else ''} | "
                 f"{repo.get('forks', '')} | {repo.get('commits', '')} | {c['HIGH']} | {c['MEDIUM']} | "
                 f"{c['LOW']} | {status(repo, c)} |")

    L += ["", "## Details", ""]
    any_detail = False
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for repo, findings in results:
        if repo.get("error"):
            L += [f"### {repo['name']}", "", f"Not scanned: {repo['error']}", ""]
            any_detail = True
            continue
        if not findings:
            continue
        any_detail = True
        L += [f"### {repo['name']} ({repo['visibility']})", ""]
        if repo["fork"]:
            L += ["*Fork: history includes upstream commits. Some findings may be upstream's, not yours.*", ""]
        if repo["source"] == "local":
            L += [f"Origin: {repo.get('origin', '-')}", ""]
        L += ["| Severity | Type | File | Line | Commit |", "|---|---|---|---|---|"]
        rows = sorted(findings, key=lambda f: (order[f["severity"]], f["file"]))
        for f in rows[:MAX_ROWS]:
            L.append(f"| {f['severity']} | {f['rule']} | `{f['file']}` | {f['line']} | `{f['commit']}` |")
        if len(rows) > MAX_ROWS:
            L.append(f"| … | {len(rows) - MAX_ROWS} more | | | |")
        L.append("")
    if not any_detail:
        L += ["No findings.", ""]

    L += ["## What to do", "",
          "Nothing in this audit changed any repo. For each finding, in this order:", "",
          "1. **Real secret (HIGH)?** Rotate or revoke it **first**: regenerate keys, change passwords and APN credentials, revoke tokens. "
          "Public exposure means it may already have been scraped, so rotation is the only real fix.",
          "2. **Personal identifier (MEDIUM)?** IMEI/ICCID/phone/home IP can't really be rotated. Decide whether it matters, then clean it up.",
          "3. **False positive?** Add `scrub:allow` to the line, or a gitleaks allowlist entry, and note it in the commit.",
          "4. **Rewrite history:** `git filter-repo --replace-text rules.txt` (or `--path <file> --invert-paths`), then force-push all branches and tags.",
          "5. **Public repo:** ask GitHub Support to purge cached views of the old commits and any PR refs. Check Insights → Traffic → Git clones and the forks list.",
          "6. **Re-run this audit** to confirm.",
          "", "Turn on GitHub secret scanning and push protection for public repos (Settings → Advanced Security).", ""]
    path.write_text("\n".join(L))
    os.chmod(path, 0o600)
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", help="GitHub user/org (default: logged-in gh user)")
    ap.add_argument("--public-only", action="store_true")
    ap.add_argument("--no-forks", action="store_true", help="skip forks of other people's repos")
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument("--local", nargs="+", metavar="PATH", help="also audit local repos (or folders of repos)")
    ap.add_argument("--no-github", action="store_true", help="don't query GitHub (use with --local)")
    ap.add_argument("--workdir", default="~/.cache/repo-audit", help="where mirrors and the report go")
    a = ap.parse_args()

    need("git")
    need("gitleaks")
    workdir = pathlib.Path(a.workdir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    os.chmod(workdir, 0o700)

    owner, repos = None, []
    if not a.no_github:
        need("gh")
        owner, repos = github_repos(a.owner, a.public_only, not a.no_forks, a.include_archived)
    if a.local:
        repos += local_repos(a.local)
    if not repos:
        sys.exit("audit: nothing to audit")

    results = []
    for i, repo in enumerate(repos, 1):
        print(f"[{i}/{len(repos)}] {repo['name']} ({repo['visibility']}) ...", end=" ", flush=True)
        try:
            if repo["source"] == "github" and repo["visibility"] == "public":
                repo["forks"] = fork_count(repo["name"])
            findings, repo["commits"] = audit_one(repo, workdir)
            n = {s: sum(f["severity"] == s for f in findings) for s in ("HIGH", "MEDIUM", "LOW")}
            print(f"HIGH {n['HIGH']}  MED {n['MEDIUM']}  LOW {n['LOW']}")
        except Exception as e:  # keep going; report it
            repo["error"] = str(e)
            findings = []
            print(f"ERROR: {e}")
        results.append((repo, findings))

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    report = workdir / f"audit-{stamp}.md"
    items = write_report(report, owner, results)
    public_high = [r["name"] for r, c in items if r["visibility"] == "public" and c["HIGH"]]
    print(f"\nReport: {report}")
    if public_high:
        print(f"🔴 PUBLIC repos with secrets: {', '.join(public_high)}. Rotate those secrets now.")
        return 1
    print("No secrets found in public repos." if any(r["visibility"] == "public" for r, _ in items)
          else "Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
