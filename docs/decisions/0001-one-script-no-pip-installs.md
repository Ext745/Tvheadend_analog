# 0001 — One Python script with no pip installs

**Date:** 2026-10-22 or earlier · **Status:** accepted · **Proposed by:** not recorded (*inferred*)

*Backfilled from README §14 and `git log`. The reasoning is inferred.*

## Context
`ntsc-tvh` runs inside TVHeadend's environment, often a container, and TVHeadend calls it directly through `pipe://` URLs.

## Decision
Ship one executable Python 3 script using only the standard library. Tests use `unittest` with fakes.

## Alternatives considered
None recorded.

## Consequences
Installing is copying a file. The script is large, and external tools (`v4l2-ctl`, `ffmpeg`, `arecord`) need fallbacks when missing.
