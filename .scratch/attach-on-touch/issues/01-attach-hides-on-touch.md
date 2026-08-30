# 01 — `❯` hides where the primary pointer is coarse

**Status:** landed — e8b68bd

`rowActions` draws `❯` on every row with an `attach` string
(`web/board.js:502`), and `renderFocus` draws `attach ❯` on the Focus card
(`web/board.js:2091`). Both render on a phone, where the copied `tmux` line has
nowhere to be pasted.

Both buttons take a shared `attach` class, and `board.html` hides that class
under `@media(pointer:coarse)` — beside the `@media(max-width:560px){.fsid…}`
rule that already does this shape.

**CSS, not a `matchMedia` gate in JS.** The stub DOM runs no CSS and has no
`matchMedia`, so a JS gate would make every `iconbtn`-counting test depend on a
device fact. CSS also stays live-reactive, and `display:none` drops the button
from the tab order rather than leaving it for a screen reader.

**`pointer`, never `any-pointer`.** `pointer` reports the *primary* pointer, so a
laptop with a trackpad and a touchscreen reads `fine` and keeps its `❯`. That
device has tmux and the button is for it.

`board.js:3095` currently reads "Per-event, never `matchMedia("(pointer:
coarse)")`" — about the swipe gate, where an event distinguishes a finger from a
mouse on one machine. This slice does exactly what that sentence forbids, for a
different question, and the file must say which is which or a future reader
finds a flat contradiction.

## Acceptance

- Both attach buttons carry the `attach` class; assertable in the stub DOM.
- `board.html` has a `@media(pointer:coarse)` rule hiding `.attach`. `rule()` in
  the test suite anchors on `^<sel>{`, so a rule nested in a one-line `@media`
  needs its own assertion, not `rule(".attach")`.
- `server.py` unchanged. `copyAttach` and `legacyCopy` unchanged.
- `board.js:3095`'s comment names the split: the drag gate is per-event because
  only the event separates finger from mouse on one machine; the Attach rule is
  per-device because the question is whether a terminal exists at all.
- ADR 0011's Consequences bullet — which today asserts *"the button always
  shows"* — states both axes: shown regardless of origin on a pointing device
  (the insecure-origin fallback is why), never shown on a primary-coarse one.
- `CONTEXT.md` unchanged.
- `python3 -m unittest discover -s tests` green.
