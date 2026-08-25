# 04 — skip walks Up Next, not the head

**Status:** landed — 6e8380b
**Blocked by:** 01

`skip →` jumps to `upnext[0]` — the queue head (`web/board.js:2058`,
`nextUp` at :2951-2959). You just skipped, so the head may well be the Run
you skipped past, or re-become it on the next poll. Under priority tiers the
head and "the next one" diverge permanently.

`skip` becomes "the next Run in Up Next after me", wrapping within `upnext`.

It stays **inside** `upnext`. The ring is
`watching → upnext → snoozed → dormant` (`ringGroups`, :2296-2317), and "next
after me" taken literally would walk a triaged Focus into `snoozed` and
`dormant` — Runs you explicitly deferred. Skip is a triage verb; the *swipe*
is navigation and keeps the full ring untouched.

## Acceptance

- `skip` from the Focus lands on the following Up Next row, not the head.
- `skip` from the last Up Next row wraps to the first.
- `skip` never lands on a `snoozed`, `dormant` or Foreign Run.
- The swipe's ring order and wrap behaviour are unchanged
  (`tests/test_board.js:1922-1955` still passes).
