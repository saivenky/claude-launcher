# Priority outranks lane

## The report

> Launcher doesn't seem to respect priority correctly. Round robin goes thru
> up next at the top priority level until that level is exhausted (i.e. all
> are still running / working) before moving on to the next.

## What is actually true today

`server.py` builds the triage list as `order = blocked + recent`, and sorts
each half by `(pri, recency)`. So the nesting is:

    zone (watching vs up-next) → blocked-vs-idle → priority

A `low`-priority **Blocked** Run therefore outranks a `high`-priority **idle**
one. Priority is a tiebreak *inside* a lane and never a tier of its own.

`web/board.js` (`sortsBefore.upnext`) re-implements the same rule client-side
so it can splice the **Focus** into its own zone — so the rule is written
twice and both copies have to agree.

`Priority` has no `CONTEXT.md` entry at all. There is no written answer to
what it means, which is why the report reads as "or perhaps I don't
understand how it's currently used".

## The model we want

    zone (watching vs up-next) → priority → blocked-vs-idle → recency

The outer split — `watching` vs `upnext` — is already how the code works and
does not change. The middle swap is the whole fix.

Consequences, all accepted deliberately:

- A Run that goes **working** leaves `upnext` entirely, so it does not hold
  its priority level open. A level empties as its members go working, and
  only then does the queue descend. That is exactly "until that level is
  exhausted".
- A `low` **Blocked** Run sits below a `normal` **idle** one. There is no
  floor under Blocked. `low` is the "I know it's asking, I don't care yet"
  case and it has to be able to say so.
- Recency still flips direction by lane: Blocked oldest-first (urgency),
  idle freshest-first (staleness ≈ done).

## Decisions taken alongside

- Priority also orders `watching` and `dormant`. It does **not** order
  `snoozed` — snooze order is *when it wakes*, and priority overriding that
  would surface a Run you explicitly deferred.
- Priority becomes settable from a queue row, not only from the Focus.
  Priority-as-outer-key makes triage mean "walk the queue marking things",
  and today each mark costs a focus-change — the one thing **Rotation**
  exists to protect.
- `skip →` comes to mean "next in Up Next after me", wrapping within
  `upnext`, rather than `upnext[0]`. Under tiers, "head" and "next" diverge
  permanently. The *swipe* keeps the full ring.
- No tier subheads in the `upnext` zone. The `⚑` marks high and gains a dim
  twin for low; three headers over a queue that is often two rows is chrome
  the phone cannot pay for.
- `Priority` gets a `CONTEXT.md` entry, and **Rotation**'s "curated
  round-robin names this queue's order" line is rewritten to say which
  order that now is.
