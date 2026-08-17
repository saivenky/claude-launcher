# 03 — Nickname the Ask, the Recover picker, and the toast

**Status:** landed — f438a31

Spec: `.scratch/session-nickname/spec.md` · ADR 0026 · ADR 0023

**Blocked by:** 02 — Nickname every row.

## What to build

The last three surfaces that name a **Run**, so the Nickname is everywhere the
Workspace is.

**The Ask block.** ADR 0023 stamped the Workspace on the Ask because approving a
tool call is the one irreversible act on the Board and the header may be slid
out of view while you read. The Nickname joins it, for the harder version of the
same reason: *which project* is the question the Workspace could not answer.

**The Recover picker.** This is what nicknaming is for — a list of near-identical
rows from one repo. A row with a Nickname leads with it; the opening prompt keeps
its line underneath, demoted rather than deleted. A row without one is unchanged.

**The reply toast.** `board.js:328` reads `f.title`, a field ADR 0023 removed
from the payload, so every successful reply has been toasting the literal string
`"✓ sent — session is now working"`. It should name the Run: Nickname if there
is one, Workspace otherwise.

## Notes

- The Recover picker's rows come from `_recoverable_sessions` (`server.py:744`),
  which is a different payload from the board — it needs `nickname` too.
- Recover rows are Sessions with no live Run; the store is keyed by `sessionId`,
  so nothing special is required to look one up.
- The Ask stamp is lane-coloured today; the Nickname sits with the Workspace
  there, same treatment.

## Acceptance criteria

- [ ] The Ask block shows the Nickname beside the Workspace when one is set
- [ ] A Recover row with a Nickname leads with it and keeps the opening prompt
      on a second line; one without is unchanged
- [ ] The reply toast names the Run — Nickname, else Workspace — and never the
      literal string "session"
- [ ] No remaining reference to `f.title` on the board payload
- [ ] Python and board tests pass: `python3 -m unittest discover -s tests`
