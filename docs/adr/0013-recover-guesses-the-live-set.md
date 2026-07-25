# Recover reconstructs the live set by heuristic, not by persisting it

**Recover** brings back the Sessions that were live before a restart by
*guessing* — a recency-cluster over transcript mtimes (the **recovery set**),
pre-ticked for you to edit — rather than by the Launcher persisting a snapshot
of which Runs were live. The Launcher keeps no memory across a restart; each
Session's own `.jsonl` is the only state.

## Context

A machine restart kills every **Run** — the tmux server and every `claude`
process — while every **Session** survives on disk. "Bring back what I was
running" could be answered two ways: reconstruct the exact live set from a
snapshot the Launcher writes while alive, or discover recently-active
**Resumable Sessions** and let the human pick, pre-selecting a best guess.

## Considered options

- **Fidelity — persist the live set.** Snapshot live Runs' `sessionId`s
  periodically so that after a reboot the exact set can be restored. Rejected:
  it adds a moving part that can go stale or wrong; a **Foreign Run**'s
  in-flight turn is unrecoverable regardless (ADR 0012), so "fidelity" would be
  partly a lie; and auto-restoring a fixed set fights the Board's consent-based
  ethos — you choose the **Focus**, you walk the queue (see **Rotation**).
- **Discovery — guess from mtimes, human curates.** A recency-cluster of
  Resumable Sessions, pre-ticked and freely editable. Chosen.

## Decision

Recover owns no persisted state. The recovery set is a heuristic: the newest
Resumable Session, chaining to older ones while each successive gap stays
within a tolerance and the cluster's total span within a leash, capped to a
phone-sized count — computed fresh from transcript mtimes each time the picker
opens. The human edits the ticks before resuming. Every member is resumed
exactly as **Resume** would, one new Managed Run apiece.

## Consequences

- The recovery set is a guess, so it can mis-select: it misses a Run left open
  but idle before the crash (its mtime predates the cluster) and can over-reach
  if you had idly touched unrelated Sessions in the same window. Both are
  corrected by editing the ticks — Recover never resumes anything on its own.
- The cluster's knobs — gap tolerance, span leash, count cap — live in code as
  plain constants, expected to change with use; keeping them out of any
  persisted format is part of the point.
