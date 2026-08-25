# 02 — Priority orders watching and dormant, never snoozed

**Status:** ready-for-agent
**Blocked by:** 01

`watching`, `dormant` and `snoozed` sort purely by recency / wake time today
(`server.py:2996-2998`); `pri` is ignored in all three except for the
dormancy exemption.

- `watching` and `dormant` gain `pri` as their leading sort key, ahead of
  recency — one rule everywhere the queue is drawn.
- `snoozed` does **not**. Its order is *when it wakes*. Priority overriding
  that would surface a Run you explicitly deferred, which is the one thing
  snooze promises not to do. Say so in a comment so the asymmetry reads as a
  decision rather than an oversight.

Note `web/board.js`'s `sortsBefore.rest` already approximates snoozed by
recency rather than wake time (an acknowledged approximation at :2340-2345) —
leave that alone, but `rest` must now lead with `pri` for the two zones that
gained it.

## Acceptance

- A `high` working Run leads `watching` regardless of recency.
- A `high` Run never appears in `dormant` at all (the existing exemption).
- `snoozed` order is unchanged by priority; a test pins that.
