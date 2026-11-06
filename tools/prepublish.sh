#!/usr/bin/env bash
# Pre-publication check. Run before making a private repo public, and before each release.
# Git history goes public along with the repo: anything ever committed is exposed,
# even if it was deleted later. This checks the whole history, not just the current files.
#
# Optional: list extra forbidden strings or regexes, one per line, in .prepublish-deny
# (internal hostnames, private repo names, your callsign + address, etc.).
# That file is itself gitignored.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0; warn=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
note() { printf '  \033[33mWARN\033[0m %s\n' "$1"; warn=1; }
good() { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
EXCL=(':!tools/scrub.py' ':!*lock.json' ':!*.lock')

step "Secrets in full git history (gitleaks)"
if command -v gitleaks >/dev/null; then
  if gitleaks git --log-opts="--all" --redact --no-banner . ; then good "no secrets found"; else bad "gitleaks findings above"; fi
else
  bad "gitleaks not installed (https://github.com/gitleaks/gitleaks/releases)"
fi

step "Personal data in current files"
mapfile -t files < <(git grep -Il '' -- . "${EXCL[@]}")
if ((${#files[@]})) && ! python3 tools/scrub.py --check "${files[@]}"; then bad "scrub findings above"; else good "clean"; fi

step "Personal data anywhere in history"
hist=$(python3 tools/scrub.py --git-history . 2>/dev/null)
if [[ -z "$hist" ]]; then
  good "clean"
else
  bad "findings in history (commit  file: kind):"; echo "$hist" | sed 's/^/       /'
fi

step "Forbidden strings (.prepublish-deny)"
if [[ -f .prepublish-deny ]]; then
  hits=0
  while IFS= read -r pat; do
    [[ -z "$pat" || "$pat" == \#* ]] && continue
    if git grep -qiE -- "$pat" $(git rev-list --all) -- . ':!.prepublish-deny' 2>/dev/null; then
      bad "found in history: $pat"; hits=1
    fi
  done < .prepublish-deny
  ((hits)) || good "none found"
else
  note "no .prepublish-deny file (optional)"
fi

step "Large files in history (>5 MB)"
big=$(git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectsize) %(rest)' \
      | awk '$1=="blob" && $2>5242880 {printf "%.1f MB  %s\n", $2/1048576, $3}')
if [[ -n "$big" ]]; then note "large blobs (attach to releases instead?):"; echo "$big" | sed 's/^/       /'; else good "none"; fi

step "Repo hygiene"
[[ -f LICENSE ]] && good "LICENSE present" || bad "no LICENSE"
grep -q '<Project Name>' README.md 2>/dev/null && bad "README still has template placeholders" || good "README filled in"
grep -q '^\*\*Maturity:\*\*' README.md 2>/dev/null && good "maturity line present" || bad "README has no maturity line"
[[ -f TEMPLATE_USAGE.md ]] && note "TEMPLATE_USAGE.md still present (delete it)"
grep -q '<YYYY-MM-DD>' STATUS.md 2>/dev/null && note "STATUS.md not filled in"
git ls-files | grep -qiE '(^|/)\.env$|\.pem$|\.key$|id_rsa|wg[0-9]*\.conf$' && bad "secret-looking file is tracked"
if git remote -v | grep -q .; then :; else note "no git remote configured"; fi

printf '\n'
if ((fail)); then
  cat <<'EOF'
NOT READY TO PUBLISH.
If something is in history, deleting it in a new commit is NOT enough. Options:
  a) Rewrite history:  git filter-repo --replace-text <rules.txt>   (then force-push; rotate any leaked secret anyway)
  b) Publish fresh history: copy the cleaned tree into a new repo with a single initial commit.
Any secret that was ever committed must be treated as leaked and rotated.
EOF
  exit 1
fi
((warn)) && echo "Ready, with warnings above." || echo "Ready to publish."
