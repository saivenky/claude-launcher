# Resume spans every Session, not just those under PROJECTS_ROOT

**Resume** (start a new **Run** on an existing **Session** via
`claude --resume <sessionId>`) accepts *any* `sessionId` with a transcript
on disk, and `cd`s to that Session's own recorded cwd — even when that cwd
is outside `CLAUDE_LAUNCHER_PROJECTS_ROOT`. This deliberately relaxes the
boundary that confines the *generic* launch.

## Context

The generic "type a subdir" launch is confined to `PROJECTS_ROOT`: it lets
a caller start fresh work anywhere under that root, so the root is the
blast radius. Resume is a different capability — it cannot start work at an
attacker-chosen path. It can only reopen a cwd where the *user* already ran
`claude`, identified by a 36-char UUID that is validated before it touches
the shell and must already have a transcript on disk. A caller cannot guess
a UUID, cannot pick the cwd, and cannot reach any path the user hasn't
already worked in. The marginal capability over the existing live-Run
surface is "continue one of my own past Sessions."

Confining resume to `PROJECTS_ROOT` would therefore buy little security
while hiding the dirs the user lives in most — notably `~/obsidian`, which
sits outside the root — making the feature useless for its main case.

## Considered options

- **Confine resume to `PROJECTS_ROOT`** — consistent with the generic
  launch, but excludes `~/obsidian` and everything else outside the root.
  Rejected: costs the primary use case to defend against a path the caller
  can't choose anyway.
- **A separate `CLAUDE_LAUNCHER_RESUME_ROOT`** — an explicit opt-in wider
  boundary. Rejected as premature: adds config for a boundary whose value
  is unclear given the UUID + on-disk-transcript gates already bound the
  set to the user's own history.

## Decision

Resume is bounded by "a transcript for this `sessionId` exists on disk,"
not by `PROJECTS_ROOT`. The cwd comes from the transcript itself (first
line carrying a `cwd`), which is authoritative; the munged project-dir name
is only a fallback because its dash-encoding is lossy. Resuming a
`sessionId` whose **Run** is *currently live* is refused — one Session, one
live Run at a time — so resume never forks an in-flight transcript.

## Escape hatch

If resume ever needs tightening (e.g. the server is exposed to a less
trusted network), reintroduce a `CLAUDE_LAUNCHER_RESUME_ROOT` gate on the
resolved cwd — the check has one obvious home, right after the cwd is read
from the transcript.

## Note on terminology

Written when the glossary called the durable thread a *Conversation* and
the live process a *Session*. Renamed when **Session** was realigned with
Claude Code's `sessionId` (the thread) and the live process became a
**Run**. The decision itself is unchanged.
