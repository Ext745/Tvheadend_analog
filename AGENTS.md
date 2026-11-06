# AGENTS.md: Operating Manual for AI Agents

Read this before doing anything. `CLAUDE.md` is a symlink to this file.

## Project context (read, don't duplicate)
| File | Purpose |
|---|---|
| `GOAL.md` | Goal, constraints, **non-goals**, acceptance criteria |
| `STATUS.md` | Current state. **Read at session start, update at session end.** |
| `TESTING.md` | Real-hardware test rigs and soak log |
| `README.md` | User guide. §1 how it fits together, §2b tested hardware, §11 troubleshooting, §12 design notes and limits |
| `docs/decisions/` | Decision records. Don't reverse one without raising it first. |
| [APPROACH.md](https://github.com/Ext745/.github/blob/main/APPROACH.md) | How these projects are run (central) |
| [AI_POLICY.md](https://github.com/Ext745/.github/blob/main/AI_POLICY.md) | AI usage policy (central) |

## Commands
```bash
make build        # build
make test         # unit/integration tests (also run in CI)
make hil          # hardware-in-the-loop, needs target hardware attached
make scrub-check  # personal-data scan of tracked files
make prepublish   # full pre-publication check (history included)
# Long runs are started by the human: make soak RIG=A HOURS=72 SOAK_CMD="..."
```
- `make build`: checks `ntsc-tvh` is executable and byte-compiles it.
- `make test`: scrubber self-test, then the `unittest` suite. Needs no tuner and no pip installs.
- `make hil`: `./ntsc-tvh doctor`, needs a tuner attached. For a channel with a real picture, also run `examples/analog-check`.

## Working rules
1. **Start** by reading `STATUS.md` and `GOAL.md`.
2. Work toward the acceptance criteria. Respect the non-goals.
3. Bold or unconventional approaches are welcome. Explain them. If you change direction or make a non-obvious choice, add a decision record in `docs/decisions/`.
4. Exploratory or abandoned work goes in `lab/<YYYY-MM-DD>-<topic>/` with a `NOTES.md`. Don't delete anything from `lab/`.
5. **Never claim hardware verification.** You may report: "builds", "unit tests pass", "not tested on hardware". Maturity levels and TESTING.md results come from real runs and are set by the human. Don't edit soak rows.
6. State plainly what you couldn't verify, and why.
7. Note any new dependency and the reason for it in STATUS.md or a decision record.
8. **End** every session by updating `STATUS.md`: what changed, what's untested, next steps, and **which model/tool you are** (e.g. "agent: Claude Opus 5.5 via Claude Code").
9. Commits: short imperative subject line. AI-assisted commits include a `Co-Authored-By` trailer.

## Personal data and secrets
This repo may be private now, but assume it will be public. **Git history goes public with it.**
- Never commit keys, passwords, APN credentials, WireGuard configs, `.env` files, or tokens.
- Before committing logs or raw output (to `lab/` or `tests/`), run `python3 tools/scrub.py --in-place <file>`. Modem logs contain IMEI/IMSI/ICCID and phone numbers. Network logs contain keys and public IPs.
- If the pre-commit scrub check flags something, fix the data. Don't bypass the hook. Mark a line `scrub:allow` only for genuine false positives, and say so in the commit message.

## Safety
*Inferred:* receive-only USB tuners, with no high voltage or stored energy. The hazards here are to the hardware: see README §11 for tuner conditions that wedge a card until it loses power.
Any change affecting these must be flagged **SAFETY REVIEW NEEDED** in the commit message and in STATUS.md, citing the relevant datasheet or reference. It can't be applied to hardware until the human has reviewed it.

## Out of bounds
- No PRs, issues, or comments on other people's repositories unless explicitly told to (see AI_POLICY).
- Don't rewrite git history on shared branches.
- Don't change repo visibility or settings.

## Code conventions
One Python 3 script, `ntsc-tvh`, standard library only. Tests use `unittest` with fakes in `tests/fakebin/` and `tests/fixtures/`.

Before pushing, run the tests a second time with a PATH holding only `python3`, `sh`, `cat`, `env`, `sleep`, `true` and `false`. CI has no `ffmpeg`, `arecord` or `v4l2-ctl`, so an unguarded call to one passes locally and fails there. Any new call to an external tool needs an `OSError` fallback.
