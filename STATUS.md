# Status

**Updated:** 2026-11-06 · **Maturity:** `experimental` · **Visibility:** public

## Summary
v0.1.0 is released, and the tool streams and scans on both tested cards. Hardware results so far come from short sessions and 4-hour soaks that predate the soak logger, so no soak time is logged yet. The same HVR-1950 also passed 148 h of analog and FM soaks in the FM patch work, driving the card directly rather than through this tool (see `TESTING.md`).

## Verified on real hardware
All *backfilled, unverified*, from README §2b and `CHANGELOG.md`. See `TESTING.md`. Maturity stays `experimental` until the owner sets it.
- HVR-1950: analog streaming, scan, every `analog-*` and `digital-*` profile, DVR configs.
- Pinnacle 800e: analog streaming, scan, composite and S-Video stream paths.
- FM radio on the HVR-1950, with the pvrusb2 FM patches.

## Implemented, not hardware-tested
- README "Getting started" walkthrough: pending, to be run by the owner.
- `install-timer` periodic rescan: *inferred*, no hardware record found.

## Known broken / limitations
See README §11 and §12. Stuck-encoder messages and SAP misdetection on the HVR-1950 are driver behaviour.

## In progress
- Repo kit retrofit, PR open.

## Next steps
1. Owner runs the README "Getting started" walkthrough, then this file and the README get updated with the result.
2. Log soak runs with `make soak`.
3. Tag the next release when `CHANGELOG.md` "Unreleased" is ready.

## Session log
- 2026-11-06: added the FM driver soaks to `TESTING.md` as indirect evidence. Docs only. By: agent: Claude Opus 5.5 via Claude Code
- 2026-11-06: repo kit retrofit: added AGENTS.md, Makefile, tools, hooks, hygiene CI, GOAL, STATUS, TESTING, decisions. `ntsc-tvh` gained one comment marker only. Unit tests and scrub-check pass. No hardware run. By: agent: Claude Opus 5.5 via Claude Code
- 2026-10-26: last code changes, `b18b67e` and `e471d46`, from `git log`. By: human + agent: Claude via Claude Code (from commit trailers)
