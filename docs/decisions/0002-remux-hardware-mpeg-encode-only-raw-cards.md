# 0002 — Remux hardware MPEG, encode only for raw-frame cards

**Date:** 2026-10-22 or earlier · **Status:** accepted · **Proposed by:** not recorded (*inferred*)

*Backfilled from the README introduction and §12.*

## Context
The HVR-1950's analog node already emits an MPEG-2 program stream. Raw-frame cards such as the 800e don't.

## Decision
Remux the hardware stream to MPEG-TS with no transcode. Auto-detect raw-frame cards and encode them in software.

## Alternatives considered
None recorded.

## Consequences
Near-zero CPU on the hardware path, at the on-board encoder's quality. There are two capture paths to test.
