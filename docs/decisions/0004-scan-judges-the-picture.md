# 0004 — Scan judges the picture, not only signal strength

**Date:** 2026-10-26 or earlier · **Status:** accepted · **Proposed by:** not recorded (*inferred*)

*Backfilled from commits `7292d99`, `7f4996f` and `e471d46`.*

## Context
Signal strength finds nothing on cards that always read 0 %, such as the 800e. Contrast alone counts snow as a picture.

## Decision
The default `auto` method judges contrast, noise and motion from captured frames, skips the grab on channels reading 0 % once a card has shown it reports signal, and needs a picture to pass twice.

## Alternatives considered
- `signal` only: fast, but blind on some cards.
- `video` on every channel: right on every card, but slow.

## Consequences
Activity detection stays heuristic (README §12), and thresholds may need tuning for other cards.
