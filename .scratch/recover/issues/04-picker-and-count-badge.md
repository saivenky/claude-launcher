# 04 — The Recover picker + empty-board count badge

**What to build:** The client surface — an intake affordance that opens the
Recover picker, and the count nudge that makes it findable on an empty
post-reboot Board.

- **Entry: one intake button carrying the recovery-set count** — `resume ⁵`
  when `/api/recoverable` reports a non-empty set, plain `resume…` otherwise.
  On the empty Board (no live Runs — the exact post-restart state) this badge is
  what draws the eye. **No auto-open, no auto-resume** — the count is a nudge,
  not an action (ADR 0013, consent ethos).
- **The picker:** the candidate list from `/api/recoverable`, newest-first,
  each row `dir · title · relative-last-active` with a checkbox. `preselect`
  rows come **pre-ticked**; the rest unticked but tickable. Render
  `relative-last-active` from the row `mtime` — it's the field that explains why
  a row is or isn't pre-ticked.
- **Resume N:** POST the ticked `sessionIds` to `/api/recover`, toast the
  summary from its result array, close the picker, refresh the board. **Do not**
  `watch()` any returned run into Focus — they queue (slice 03).
- Keep the existing paste-`sessionId` box for the long tail (older than the
  ~30 the picker lists). The picker augments it; it doesn't replace it.
- UI is hot-served from disk (ADR 0005) — ship `board.html` / `board.js`, no
  relaunch. This slice is the one worth a human eye for phone layout of the
  picker list.

**Blocked by:** 01, 02, 03

**Status:** ready-for-human
