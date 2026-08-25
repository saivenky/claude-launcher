# 03 — Set priority from a queue row

**Status:** ready-for-agent
**Blocked by:** 01

Priority is settable only from the Focus today — the `.prisel` span in the
Focus card's action strip (`web/board.js:2035-2043`), cycling
high → low → normal. A queue row shows `⚑` for high (`web/board.js:2124`)
and offers no control.

With priority as the outer ordering key, triage means walking the queue and
marking things. Each mark currently costs a focus-change, and a focus-change
is precisely what **Rotation** is built to protect.

Give `qrow` a dedicated tap target in the `⚑` slot:

- always present, dim when the Run is `normal`, so the slot does not reflow
  as levels change and the affordance is discoverable without a gesture
- `stopPropagation` — the row's own tap still means "make this the Focus"
- cycles the same high → low → normal the Focus's `.prisel` does. One rule,
  two places; share the cycle rather than restating it
- `low` gains its own dim glyph beside `high`'s `⚑`, so all three levels are
  legible on a row

No long-press: it has no discoverability and no desktop equivalent, and the
rail is a monitor surface.

No tier subheads in the `upnext` zone — the glyphs carry it.

## Acceptance

- Tapping the control on a queue row changes that Run's priority and does
  **not** move the Focus.
- Tapping elsewhere on the row still focuses it.
- The row re-sorts on the next poll, per 01.
- A `normal` row renders the control dim rather than absent.
