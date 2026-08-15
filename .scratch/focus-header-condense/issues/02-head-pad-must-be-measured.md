# 02 — HEAD_PAD must be measured, not written down

**Status:** landed — d42d814

Blocked by: 01

Spec: `.scratch/focus-header-condense/spec.md`

## What

`board.js:1165` — `HEAD_PAD = 52` is a hand-maintained copy of the sticky
header's height, used to park a scroll so the target never rises under the
header. The real height is ~81px at `--fs:1` and ~88px at the default `--fs`, so
every landing that uses it is already ~30px off.

Issue 01 makes the constant untenable rather than merely wrong: a condensing
header has two heights, and which one applies depends on the chrome state at the
moment of the scroll.

## Change

Read the header's height from the element at scroll time —
`fhead.getBoundingClientRect().height` plus the existing hair — instead of the
literal. `SEAM_PEEK` (line 1164) is a genuine design constant and stays.

## Acceptance

- Landing on the seam parks it clear of the header at `--fs:1` and at `--fs:1.25`.
- Landing while the chrome is condensed parks against the condensed height, not
  the full one.
- No literal in `board.js` restates a height that CSS owns.
