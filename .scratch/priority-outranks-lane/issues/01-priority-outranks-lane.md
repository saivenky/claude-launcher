# 01 — Priority outranks lane inside Up Next

**Status:** landed — 5555960

Re-nest the triage sort from `zone → lane → priority` to
`zone → priority → lane → recency`.

## Server (`server.py:2986-3000`)

`order = blocked + recent` becomes one `upnext` list sorted by
`(pri, lane_rank, recency)`, where `lane_rank` puts Blocked before idle and
`recency` still flips direction by lane — Blocked oldest-first, idle
freshest-first. The `dormant`/`recent` split (`_BOARD_DORMANT_MS`, and "high
priority never dorms") keeps working exactly as it does now; only the sort
of the survivors changes.

`counts.needYou` must keep meaning what it means: the number of Runs that
want you. Nothing that does not want you joins the list.

## Client (`web/board.js:2330-2346`)

`sortsBefore.upnext` re-implements the server's rule so the **Focus** can be
spliced into its own zone at the index the server would have given it. It has
to be re-nested identically. A mismatch puts the Focus in the wrong slot and
makes swipes oscillate — the thing the comment at :2321-2329 exists to
prevent.

## Acceptance

- A `high` **idle** Run sorts above a `normal` **Blocked** one.
- A `low` **Blocked** Run sorts below a `normal` **idle** one.
- Within one priority level, Blocked precedes idle; Blocked ties break
  oldest-first, idle ties freshest-first.
- Python tests cover the sort directly. There is currently **no** server-side
  test of `(pri, updatedAt)` ordering nor of "high priority never dorms"
  (`server.py:2992`) — add both; the only existing coverage is the client's
  "no cut-in" assertion (`tests/test_board.js:460`), which tests the opposite
  concern.
- `tests/test_board.js`'s existing Focus-discipline assertions still pass:
  urgency orders the *queue*, never the card in front of you. A high-priority
  Blocked Run arriving still must not take the Focus.
