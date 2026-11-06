# Testing

Code in this repo is verified by running it on **real target hardware for extended periods**, not just by compiling or passing unit tests. This file is the evidence. Every soak entry links to its logs.

## Test rigs
| Rig | Hardware | Firmware / OS | Peripherals / setup | Notes |
|---|---|---|---|---|
| A | Hauppauge WinTV-HVR-1950 (`pvrusb2`) on a Tiger Lake x86 PC | Linux kernel 7.0, TVHeadend 4.3 in Docker | USB 2 path via a hub, RF modulator on us-bcast:4 | See README §2 for the USB path |
| B | Pinnacle PCTV HD Pro Stick 800e (`em28xx`) on the same PC | Linux kernel 7.0 | Shared RF feed | Raw frames, software encode |

## Soak / long-run log
Rows are added by `tools/soak.py --append` (or by hand for runs made before the tool existed; mark those *backfilled*).

| ID | Start | Duration | Rig | Commit | Configuration | Result | Notes |
|---|---|---|---|---|---|---|---|
| S-001 | 2026-10-14 | 72.0 h | A | FM patches v1 | With the stereo firmware file, hourly cycles | ✅ pass | Driver soak, *backfilled, unverified*. Card driven directly, not through `ntsc-tvh`. Each cycle ran ATSC, FM, analog TV and composite. Encoder `stuck` warning ~3 per cycle, all recovered. 72/72 cycles |
| S-002 | 2026-10-17 | 72.0 h | A | FM patches v1 | Without the firmware file, hourly cycles | ✅ pass | Driver soak, *backfilled, unverified*. Card driven directly, not through `ntsc-tvh`. Each cycle ran ATSC, FM, analog TV and composite. Encoder `stuck` warning ~3 per cycle, all recovered. 72/72 cycles |
| S-003 | 2026-10-22 | 4.0 h | A | FM patches v2 | With the firmware file, 15 min cycles | ✅ pass | Driver soak, *backfilled, unverified*. Card driven directly, not through `ntsc-tvh`. Each cycle ran ATSC, FM, analog TV and composite. Encoder `stuck` warning ~3 per cycle, all recovered. 16/16 cycles |

**Cumulative real-hardware runtime:** 148.0 h

S-001 to S-003 come from the pvrusb2 FM patch testing (README §9b) and predate this log. The 4-hour soaks of `ntsc-tvh` in the table below had no recorded dates or counts, so they don't count here.

Result key: ✅ pass (clean) · ⚠️ degraded (completed, with errors / resets / restarts / leak) · ❌ fail (process died)

## Sessions before this file existed
*Backfilled, unverified.* Copied from `README.md` §2b, `CHANGELOG.md` and commit messages. No raw logs were checked and nothing was re-run.

| When | Rig | Commit | What ran | Recorded result |
|---|---|---|---|---|
| Before public release | A | `524ebc0` | End to end in Dockerized TVHeadend | Working |
| Not recorded | A | Not recorded | 4-hour soak tests | Encoder recovered every time once nothing shared the USB bandwidth (README §2b) |
| Not recorded | A | `7f4996f` | us-bcast scan with a test pattern on ch4 | `auto` 2 min 12 s, `video` 5 min 50 s, `signal` 2 min; same single channel |
| 2026-10-26 | B | `e471d46` | Regression check: `doctor`, `analog-check`, composite and S-Video, all `analog-*` profiles | Passed; two fixes came out of it |

## Issues found by real-hardware testing
Problems that only showed up on hardware or over long runs. *Backfilled, unverified.*

| Found in | Symptom | Cause | Fixed in |
|---|---|---|---|
| Rig B | Every empty channel counted as active, 68/68 | Snow has contrast; the scan judged contrast alone | `7292d99` |
| Rig B | Snow counted as a channel now and then | Noise score wanders between grabs | `e471d46` |
| Rig B | Tuner's I2C bus wedged | Composite capture while idle TVHeadend held the frontend | `3eeb0da` |
| Rig A | `firmware2 upload transfer failure` | USB bandwidth shared with another device | Documented, README §2 and §11 |

## Automated tests
- `make test`: scrubber self-test and the unit tests (also run in CI)
- `make hil`: `ntsc-tvh doctor`, needs rig A or B
- `make soak RIG=A HOURS=4 SOAK_CMD="..."`: long run with logging, see `tools/soak.py --help`

## Maturity criteria for this project
Criteria vary by project, and there's no single fixed value. The minimum for `soak-tested` is days of logged runtime across the different tests normally run for the project. The owner sets the level.
- `bench-tested`: `doctor` and `analog-check` pass on rigs A and B
- `soak-tested`: days of logged runtime in total across TV, FM and scan cycles, with no encoder or tuner fault that needs a power cycle. Mode transitions matter more here than single long runs.
- `stable`: soak-tested, in regular use, no open critical issues
