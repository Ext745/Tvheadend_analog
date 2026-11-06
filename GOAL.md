# Goal

*Inferred from `README.md` and the commit history. The README stays the user guide; if the two disagree, raise it.*

## What I want to achieve
Get analog TV and FM radio working on Linux with these tuners: over-the-air, analog cable, composite, S-Video and FM. TVHeadend is the vehicle for delivering them, not the point of the project. `ntsc-tvh` tunes with `v4l2-ctl`, selects channels by name, scans for live channels, and streams through `pipe://` muxes.

## Background
It replaces the old ivtv-utils pipe-mux script, which depended on `ivtv-tune`. It was designed against the Hauppauge WinTV-HVR-1950, whose hardware MPEG-2 encoder means no software video encoding.

## Constraints
- Hardware: V4L2 analog tuners. Tested on the HVR-1950 and Pinnacle 800e (README §2b).
- Platform: Linux, Python 3, one script with no pip installs. `ffmpeg` and `v4l-utils` where TVHeadend runs.
- Tuner: one analog stream per tuner, and analog and DVB can't share a front-end safely.

## Non-goals
From README §12:
- PAL, SECAM or European channel plans
- Concurrent streams on one tuner
- Touching the digital (ATSC/QAM) side while streaming
- Set-top box control: parked, see `docs/future-ir-blaster.md`

## Acceptance criteria
| # | Criterion | How it's verified | Done |
|---|---|---|---|
| 1 | Unit tests pass with no tuner, on Python 3.10 to 3.14 | `test.yml` in CI | ☑ |
| 2 | Analog TV and composite work on the HVR-1950 and 800e | Hardware runs, README §2b | ☑ *backfilled, unverified* |
| 3 | FM radio works on the HVR-1950, with the pvrusb2 FM patches | Hardware runs, README §9b and `TESTING.md` | ☑ *backfilled, unverified* |
| 4 | Both reach a viewer through TVHeadend | Hardware runs, README §2b | ☑ *backfilled, unverified* |
| 5 | A newcomer gets working channels by following README "Getting started" | Walkthrough, pending, to be run by the owner | ☐ |
| 6 | Runs without encoder or tuner faults over soak runs | Soak log in `TESTING.md` | ☑ *backfilled, unverified*, driver-level so far |

## References
- [Original script discussion](https://tvheadend.org/d/3480-script-for-analog-v4l2-device-support-in-tvh-via-pipe-mux)
- `README.md` §2b, §11 and §12
