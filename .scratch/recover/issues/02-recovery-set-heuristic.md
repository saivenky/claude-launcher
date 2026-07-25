# 02 — The recovery set (pre-tick heuristic)

**What to build:** Flag which rows from slice 01 belong to the **recovery
set** — the Launcher's guess at what was live at the last restart — so the
picker can pre-tick them. A `preselect: true` on each qualifying row, plus the
count in the payload for the badge (slice 04).

**The recency-cluster** (ADR 0013), computed over the candidate list's mtimes:

1. **Anchor** on the newest *candidate* (the top row) — not the newest Session
   overall. This self-heals across re-opens: a resumed member goes live and
   leaves the candidate set, sliding the anchor to the next-newest still-dead
   one, so the rest of the batch keeps pre-ticking. Once you start unrelated new
   work, the anchor moves to *that* and the stale batch stops — correct.
2. **Chain** newest→older, including the next candidate while its gap to the
   last one added is **≤ G**.
3. **Leash:** stop the moment the cluster's total span (anchor − oldest) would
   exceed **S** — this is what stops a string of small gaps running away.
4. **Cap** the pre-ticked count at **N** (phone-fit backstop, pure safety).

Constants, plainly named, in code — not a config file, not persisted (that's
the point of ADR 0013): `G = 15min`, `S = 90min`, `N = 12` (matches
`_RECENT_DIRS_MAX`).

- **Heuristic, never an action.** `preselect` only pre-ticks a checkbox; the
  human edits before anything is resumed. Under-selecting (a Run left open but
  idle before the crash — its mtime predates the cluster) and over-reaching (an
  unrelated Session idly touched in-window) are both fixed by a tap. Do not try
  to be clever past the cluster.
- Fold into the `/api/recoverable` payload from slice 01 — one call feeds the
  whole picker.

**Blocked by:** 01

**Status:** ready-for-agent
