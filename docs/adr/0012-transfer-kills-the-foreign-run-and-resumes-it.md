# Transfer kills the Foreign Run and resumes its Session

A **Foreign Run** — a `claude` started by hand in any other terminal — is
listed on the **Board** but never driven. The one thing you can do to it is
**Transfer**: one tap that kills the process and **resumes** its **Session**
as a Managed Run in tmux. Custody moves; the process does not.

## Context

ADR 0010 made a Run a tmux window stamped `@cl_run_id`, and noted as a
consequence that a pane created outside the Launcher is invisible to
`list_runs`. That was accepted as "arguably correct — the Launcher only
manages Runs it created."

It has a second effect that was not noticed at the time. `_live_session_ids()`
is derived from `cached_runs()`, which walks tmux panes, so a **Session** live
in iTerm is not in the resume guard set. CONTEXT.md promises "at most one live
Run per Session — a transcript is never forked," and since 0010 that has been
true only of Managed Runs. Resuming a Session that is live in another terminal
forks it today. So the Launcher needs to *see* foreign `claude` processes
regardless of whether it can act on them — this ADR's visibility half fixes a
broken invariant, not just an ergonomic gap.

The motivating case is access, not hygiene: a `claude` left running in a
terminal at home is unreachable from the phone, because the Board cannot see
it and `Respond` has no pane to write to. The manual workaround — quit it,
find the 36-character `sessionId`, type it into the resume box — is exactly
the thing you cannot do while away from the Mac.

Detection turned out to be free. `_run_meta()` already reads
`~/.claude/sessions/*.json` for every live `claude`, and `_ps_output()` is
already fetched on every walk, so a Foreign Run is just a `claude` tty that is
not one of our panes. No new subprocess calls, no new endpoint to list them.
`_parse_claude_ttys` requires a `ttys*` tty, so headless `claude -p` — a
**Dispatch**, a script, CI — is invisible and can never be transferred.

## Decision

**Kill and resume; never drive in place.** A Foreign Run gets no **Respond**,
no **Attach**, no close. Driving one where it lives would mean a
terminal-specific driver (AppleScript for iTerm, something else for every
other terminal) for the sake of one verb — reintroducing precisely the
dependency ADR 0010 removed, through a side door, and only ever for one
terminal.

**On demand, never a background sweep.** The Board offers a per-Run button;
nothing reaps automatically. The only thing that *creates* a Foreign Run is a
human at the Mac deliberately typing `claude`, so a sweeper's steady state is
killing sessions out from under the person starting them — a self-DoS that
would make working in a terminal impossible. On-demand needs no background
thread (the server is purely request-driven), no reap policy, and no undo.

**One atomic endpoint.** `/api/transfer` kills, waits for exit, resumes, and
returns the new run id. Split across two client calls, an AFK tap that fails
between them leaves a killed Run and nothing running — strictly worse than not
tapping. The wait is load-bearing, not politeness: the resume guard refuses
until the old pid is gone. SIGTERM, then SIGKILL on a short timeout.

**Foreign Runs stay out of the triage surface.** Never **Blocked**, never the
**Focus**, never in **Rotation**. A Foreign Run has no rendered pane to detect
a blocker from — only `status: waiting`, which CONTEXT.md already calls a lossy
proxy — and no way to answer one. Admitting rows you cannot act on would make
the queue lie, and would append "unless it's foreign" to every rule in the
glossary. The cost: a Foreign Run sitting on a permission prompt is silent on
the phone. That is correct — you started it by hand at the Mac.

**Transfer is ungated**, consistent with launch / resume / close. The shared
secret exists because **Respond** can approve tool calls (ADR 0007); Transfer
cannot approve anything.

## Considered options

- **A second, configurable backbone (iTerm alongside tmux).** The thread that
  led here started as "the tmux swap should have been configurable." Rejected:
  the phone user never sees a terminal at all, so the option buys them nothing,
  and an iTerm backend cannot deliver the detachment that motivated ADR 0010 —
  it would be a second implementation of every Run verb, plus a second parse
  frame for ADR 0009, that is strictly worse at the job. The real losses were
  legibility at the `❯` **Attach** handoff (a one-line hint) and visibility of
  foreign `claude` processes (this ADR).
- **Surface Foreign Runs read-only and stop there.** Cheap, and it fixes the
  fork guard. Rejected as the whole answer: seeing a Run you cannot reach from
  the phone is the original complaint, not a fix for it.
- **A recent-Sessions quick-pick on the resume box.** Collapses "find the
  sessionId, type it" into one tap, reusing the dir quick-pick popover and
  `_live_session_ids()` as the filter. Rejected as insufficient alone — it
  still requires a human at the Mac to quit the Foreign Run first, which is the
  half that cannot be done AFK. Worth building anyway, independently.
- **Refuse Transfer on a `working` Run** to protect the in-flight turn.
  Rejected: you tapped deliberately from somewhere else, and a refusal strands
  you with no other route onto the Run. The status is shown on the button and
  named in the confirm instead.

## Consequences

- **Unsent typed text is lost silently.** Text typed at the foreign prompt but
  never submitted dies with the process, and unlike the in-flight turn it is
  undetectable — reading it would need `capture-pane`, which is exactly what a
  Foreign Run does not have. Accepted deliberately. It is the one argument for
  refusing Transfer on an `idle` Run (where you are most likely mid-sentence),
  and that argument was heard and rejected.
- **"Foreign" is decided by who started the Run, not by which terminal holds
  it.** A `claude` sitting in a tmux pane the Launcher did not stamp is Foreign
  too. So Transfer can one day kill a process and leave a live *tmux* pane
  behind — the same shape as the dead tab below, in the substrate we chose.
- **The resume guard must outlive the tmux server.** `list_runs` used to treat
  a `ps` failure and a `list-panes` failure alike and return nothing. It cannot:
  a dead tmux server takes every Managed Run with it (ADR 0010) but leaves a
  hand-started `claude` running, and that Session still forks if the guard
  cannot see it — precisely when you are most likely to be resuming. A tmux
  failure now degrades to "no Managed Runs"; only a `ps` failure blinds the walk.
- **The dead terminal tab survives.** Transfer kills the process, not the
  terminal, so iTerm keeps a tab at a dead shell prompt until a human closes
  it. Closing it would require reaching into a GUI app the Launcher does not
  own — the dependency this whole design refuses. The tab pile-up ADR 0010
  complained about is therefore *not* fully solved for hand-started sessions;
  it is merely never *caused* by the Launcher.
- **`Run` is no longer synonymous with "tmux window."** CONTEXT.md widens Run
  to "one `claude` process executing a Session," with Managed and Foreign as
  the two kinds. Code that assumed a Run always has a pane — anything reaching
  for `_pane_for_run` — must now handle the empty case rather than treat it as
  an error.
- **The resume fork guard changes meaning.** `_live_session_ids()` must be fed
  from all live `claude` processes, not just tmux panes. This *tightens* resume:
  Sessions that were wrongly resumable become correctly refused, which will look
  like a regression to anyone who relied on the hole.
