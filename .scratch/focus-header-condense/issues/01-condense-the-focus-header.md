# 01 — Condense the Focus header instead of hiding it

Status: ready-for-agent

Spec: `.scratch/focus-header-condense/spec.md`

## What

Replace the hidden state of `.fhead` so it collapses to a single Workspace-only
row rather than transforming out of view while keeping its box.

## Changes

`web/board.html`

- Replace `.fhead.hid{transform:translateY(-110%);opacity:0}` (line 140) with a
  condensed state: reduced vertical padding, `.fdir` at `0.75rem`/`var(--fg3)`,
  and `display:none` on `.fbadge`, `.grow`, `.fmeta`, `.zbtn`, `.iplus`.
- Hide the `.about` band while the header is condensed.
- Move the session title inline: `.about`'s content trails the Workspace on
  `.fdir`'s row, dim, `var(--face)`, so the resting header is one band not two.
- Drop any height-changing property from `.fhead`'s `transition`. Transform and
  opacity only.
- `overflow-anchor:none` on `html` and `body`.
- Correct the comment at line 117: the "reserve NO layout" rule describes
  `.respond`. The header's rule is that it never leaves — it gets smaller.

`web/board.js`

- `syncChrome` (line ~2340): add a settle window after each chrome toggle that
  re-baselines the reference scroll position once layout has settled, and split
  `CHROME_STEP` into asymmetric hide/show thresholds (28 / 64).

## Acceptance

- Scroll a long Focus down until the chrome hides, then return to the top. No
  blank band at any point; the Workspace is legible throughout.
- Drag slowly through the hide threshold in both directions. No flicker between
  condensed and full.
- A Focus too short to scroll is unchanged.
- `.respond` keeps its existing behaviour — it is bottom-docked and its rule was
  never wrong.

## Notes

The three flicker fixes were not bisected in the prototype; ship them together.
`overflow-anchor:none` is the one to keep if any is dropped. See the spec.
