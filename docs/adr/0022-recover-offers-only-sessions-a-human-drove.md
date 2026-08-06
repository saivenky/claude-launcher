# Recover offers only Sessions a human drove

The **Recover** picker excludes a **Headless Session** — one whose transcript
records no interactive `entrypoint`, written by the Agent SDK or a `claude -p`
with no human at a terminal at any point. The discriminator is *origin*, read
from the transcript, never *location* on disk. Recover's other guards are
unchanged, and so is **Resume**: a Headless Session is still a **Resumable
Session**, and pasting its `sessionId` still works.

## Context

`_recoverable_sessions` listed every `~/.claude/projects/*/<uuid>.jsonl` past
four filters: a scan cap, a UUID name, not in `_live_session_ids`, and a cwd
that still exists. Nothing about how the Session started.

An unattended agent in another repo — a seed-expanding intake agent running the
Claude Agent SDK with `cwd=~/obsidian` — writes a transcript per invocation and
runs many times an hour. Those transcripts are the worst case for all four
filters at once:

- `~/obsidian` exists, so the dead-cwd filter never fires.
- The SDK child has no tty, and `_parse_claude_ttys` requires `ttys*`. So it is
  in nobody's Run list and never in `_live_session_ids` — **even while it is
  running**. The live-Run filter never fires either.
- They finish about a minute apart, well inside the recovery set's gap
  tolerance, so the recency cluster chains straight through them and pre-ticks
  up to its cap of twelve.

On the machine this was found on, 48 of the 60 newest transcripts were
`sdk-py`. The picker read as a dozen pre-ticked rows all titled with the same
kickoff prose, and the real work it exists to bring back was pushed out of the
window beneath them.

The Board had already made this call, for processes rather than transcripts:
`_foreign_rows` keeps the `ttys*` filter precisely so a headless `claude -p` —
a **Dispatch**, a script, CI — stays invisible there, because "it is nobody's
Run, and it must never become transferable." Nobody had carried the thought to
the picker, which reads only files.

## Considered options

- **Match the prompt text.** Drop rows whose title is the agent's kickoff
  string. Rejected: a filter, not a model. It rots the first time that prompt is
  edited, it is a fact about one agent in one other repo, and it leaves the next
  headless writer to be discovered the same way — by the picker filling up.
- **Deny by working directory.** Exclude `~/obsidian`, or confine the scan to
  `PROJECTS_ROOT`. Rejected twice over: ADR 0002 chose deliberately that resume
  spans every dir, and location is the wrong axis anyway — a terminal Session
  you genuinely ran in `~/obsidian` would be hidden, while the same agent
  pointed somewhere else next month would reappear.
- **Widen the live-Run walk to see headless processes.** Rejected: it treats a
  symptom, would make the picker's answer flip while the agent runs, and it is
  the exact widening `_foreign_rows` refuses because it would put CI jobs on the
  Board.
- **Read the recorded `entrypoint`.** Chosen. It is already in the data, on the
  same row `_cwd_from_transcript` opens and parses, so it costs no extra read.
  It names a real property of the Session — nobody was ever at a terminal for it
  — rather than a coincidence of its prompt or its path.

## Decision

A **Headless Session** is a Session whose transcript records an `entrypoint`
other than `cli`. `_recoverable_sessions` drops one, and the exclusion happens
in the ranked loop — **upstream** of `_recovery_set_size` — so the recency
cluster forms over the surviving rows only and a burst of headless transcripts
cannot chain the pre-tick set through unrelated work.

An **absent** `entrypoint` counts as interactive, and the row is shown. Old and
third-party transcripts may not carry the field, and the two failure modes are
not symmetric: a spurious row in the picker is an annoyance, while a Session
silently missing after a restart is the exact failure Recover exists to
prevent. The filter fails toward showing.

`_resume_guard` is deliberately **not** narrowed. Only what Recover *offers*
changes; a `sessionId` typed in still resumes. A guard that refused the paste
would remove the escape hatch on the day you do want to reopen one, and would
put Recover's convenience heuristic in the path of an explicit instruction.

## Consequences

- Two filters now encode one idea on two substrates: `_foreign_rows` by tty for
  live processes, `_recoverable_sessions` by `entrypoint` for transcripts. They
  must not drift, and each carries a comment pointing at the other. If a future
  entrypoint value means "a human was here", it belongs in both places at once.
- A Session started headlessly and later resumed by hand keeps its original
  recorded entrypoint, so it stays out of the picker even after a human has
  driven it. Accepted: **Resume** by `sessionId` is the route back, and the
  alternative is re-reading the whole transcript per candidate to find out
  whether anyone ever showed up.
- The picker still cannot tell a *running* headless Session from a finished
  one, because neither is in `_live_session_ids`. That hole is now unreachable
  through Recover, but it is not closed: any other no-tty writer that records
  `entrypoint: cli` would still be offered while live, in violation of the
  one-live-Run-per-Session guard.
