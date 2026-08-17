# 02 — Nickname every row, and set one from any of them

**Status:** ready-for-agent

Spec: `.scratch/session-nickname/spec.md` · ADR 0026

**Blocked by:** 01 — Nickname the Focus.

## What to build

The queue is where *which of these three* is actually asked, so this is where
the **Nickname** earns its keep. Every queue row and every **Foreign** row shows
its Session's Nickname in place of the snippet, and you can set one on any of
them by pressing and holding the row.

A row with a Nickname reads Workspace + Nickname. A row without one is exactly
as it is today. On a **Blocked** row the Nickname displaces the **Ask** text
too — the lane badge still says `question` or `approval`, and the full Ask is
still on the Focus.

Press and hold any row — queued, Foreign, rail — and the same inline field from
01 opens on that row. Releasing without moving opens it; the hold must not also
pin the row as the Focus, and must not start a swipe.

Foreign rows are the point of the long-press: a **Foreign Run** never takes the
Focus, so without this the one Session you most want to label could display a
Nickname and never receive one.

## Notes

- `nickname` must now be on every board item — Managed and Foreign — not just
  the focus item.
- The substitution rule lives in `board.js`, once: the server keeps sending
  `one` unchanged, including the Ask-text swap on Blocked lanes.
- Row tap is `setPinned` (`board.js:1968`). A long-press must suppress the click
  that follows it.
- Swipe needs `|dx| > 70` and `|dx| > |dy| * 1.8`; a stationary hold will not
  trip it, but cancel the drag on hold-fire to be safe. `input` is already in
  `SWIPE_BLOCK`.
- No touch listeners exist in `board.js` and the file's own comment
  (`board.js:2500`) says pointer events were chosen over them deliberately —
  stay on pointer events.

## Acceptance criteria

- [ ] Queue rows and Foreign rows show the Nickname in place of `one` when set,
      and are unchanged when not
- [ ] A Blocked row with a Nickname shows the Nickname, not the Ask text; the
      lane badge is unchanged and the Focus still shows the full Ask
- [ ] Press-and-hold on a queue row opens the inline field on that row
- [ ] Press-and-hold does not pin the row, and does not rotate the Focus
- [ ] A Foreign Session can be given a Nickname without being Transferred first
- [ ] A short tap still pins; a swipe still rotates
- [ ] Python and board tests pass: `python3 -m unittest discover -s tests`
