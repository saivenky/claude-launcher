# 03 — correct the record where the claim lives

**Status:** ready-for-agent
**Blocked by:** 02

No ADR has ever owned the swipe. The reasoning being reversed —

> POINTER events, never touch-only. The prototype's first cut listened on
> `touchstart`/`touchend`, which fires nothing under a mouse: on a desktop the
> gesture did not exist at all, and the design could not be judged there.

— is a comment block at `web/board.js:2828-2842`. That is where the correction
goes. No new ADR: the decision has always lived next to the code, and a comment
there cannot drift away from what it describes.

Rewrite that block to say what is now true and *why the old reason expired*: the
desktop needed the gesture to exist there in order to judge it, and `wheel` and
←/→ now carry that, so pointer-on-mouse costs a text selection and buys nothing.
Say that the thresholds are still about the vertical read, and that the drag now
has an armed state the thresholds are the boundary of.

`CONTEXT.md`: **Focus** and **Rotation** both list "a swipe" among the ways you
move the Focus. Neither says it is a touch route with two desktop equivalents,
nor that it now commits on an armed release rather than at any release. Say it
in the glossary's voice — no implementation detail, no thresholds, no CSS.

## Acceptance

- The `board.js` comment block no longer claims the gesture is deliberately not
  touch-only, and explains what changed rather than silently reversing.
- `CONTEXT.md`'s **Focus** and **Rotation** entries name the swipe as a touch
  gesture and account for the desktop routes.
- No file added under `docs/adr/`.
- No behaviour change; the suite stays green.
