# 05 — CONTEXT.md: Priority, and Rotation rewritten

**Status:** ready-for-agent
**Blocked by:** 01, 02, 03, 04

`Priority` has no glossary entry, despite being per-Session state you set
from the **Board** and that survives a restart. That absence is half the
original report: "or perhaps I don't understand how it's currently used."

Add a **Priority** term. It is a glossary entry, not a spec — say what
priority *is* and what it orders, not how the sort is written. It should
carry:

- three levels, `high` / `normal` / `low`, per **Session**, persisted
- it is the thing *you typed*, where a lane is inferred — which is why it
  now outranks one
- it orders the queue's tiers; a level is exhausted when every Run in it has
  gone **working**, and only then does the queue descend
- `high` never dorms
- it does not reorder **snoozed**
- an `_Avoid_` line: urgency (that is the lane), importance, rank, weight

Then rewrite **Rotation**'s line — "The 'curated round-robin' names this
queue's order, not a clock" — to say *which* order that now is. Rotation
itself is unchanged: still consent-based, still moves only when you act or
when the Focus resolves. Only the sentence describing the queue's order is
stale.

Check **Focus**'s "urgency orders the queue, never the Focus" and **Blocked**'s
"The **Board**'s top priority" against the new model — the second in
particular now reads as a claim about priority that is no longer true.

Offer an ADR only if it clears all three bars in the domain-modeling skill.
The re-nesting is cheap to reverse and lives in two well-commented places;
it probably does not.
