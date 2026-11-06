# 0003 — Refuse analog while the tuner's DVB side is open

**Date:** 2026-10-12 · **Status:** accepted · **Proposed by:** not recorded (*inferred*)

*Backfilled from commits `3eeb0da` and `58a0f17`, and README §10 and §11.*

## Context
A composite capture while an idle TVHeadend held the 800e's frontend wedged the stick's I2C bus. TVHeadend can also open a hybrid card's DVB side mid-stream.

## Decision
`DVB_GUARD=1` by default: refuse analog, including line inputs, while the same tuner's DVB side is busy. Stop a running stream if the DVB side opens.

## Alternatives considered
Exempting line inputs, as they don't use the tuner. Dropped after the wedge above.

## Consequences
It narrows the window but can't stop TVHeadend's first tune, and README §11 says so.
