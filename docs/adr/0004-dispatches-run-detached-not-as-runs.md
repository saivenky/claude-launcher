# A Dispatch runs detached and is not a Run

`tasks.py` gains a second button kind. A **Task** carries `command` and starts an
ordinary **Run** — an iTerm pane running `claude` on a fresh **Session**. A
**Dispatch** carries `exec` and starts a plain process: no `claude`, no Session,
no pane. The launcher spawns it and forgets it.

## Context

The launcher's whole model is that it owns *lifecycle* — spawn, **observe**,
close — over things that are Runs. Every verb in `CONTEXT.md` assumes one:
`list_runs` filters iTerm panes down to those where `ps` shows `claude`; `close`
refuses anything that isn't a live Run; `resume` needs a `sessionId`.

The thing that broke the model is an unattended agent. One such intake agent
takes one brief thought and expands it — researching, then booking a calendar
event, drafting a reply, or writing an honestly-estimated todo. It is explicitly
fire-and-forget: it never asks a question, and it queues its input to disk before
returning, so a caller has nothing to wait for. The phone-shaped need is "send
this thought and walk away," and the button that used to serve it launched
`/capture-task` — an interactive Claude session, which then sat open on a Mac,
in the run list, waiting to be closed by someone who had already put their phone
down.

Three ways to fit it were available.

**Spawn a `claude` session that runs the agent.** Preserves the model at absurd
cost: an interactive Claude Code session whose entire job is to shell out to a
Python script that is itself an agent. It also inherits the problem — a pane to
close.

**Spawn a pane that runs the script.** Reuses `launch_iterm` and gives you the
output. But `list_runs` would not see it (no `claude` in `ps`), so it would be
unlisted *and* uncloseable — a pane that accumulates on the Mac every time the
phone is tapped. A Run you cannot close is worse than no Run.

**Spawn it detached.** No pane, no Run, no lifecycle. Chosen.

## Decision

A `tasks.py` entry with `exec` is a **Dispatch**: `subprocess.Popen(argv,
cwd=workdir, start_new_session=True)`, output appended to an optional `log`, and
an immediate `{"ok": true}` with **no `runId`**.

`exec` is a **list**, exec'd with no shell. The seed is appended as one further
argv element, so it cannot be word-split, globbed, or interpolated. This is a
stronger guarantee than a Task gets: a Task's seed is `shell_quote`d into a
command line, which is safe but relies on quoting being right. A Dispatch's seed
never touches a shell at all — and it is the field a phone types freely into.

`start_new_session` detaches the child from the launcher's process group, so it
survives a launcher restart and never receives a signal meant for the server. We
never `wait()`: `subprocess` reaps exited children on its next `Popen`, which the
four-second run poll guarantees.

## Consequences

- **A Dispatch is invisible to every other verb**, by construction. It cannot be
  observed, closed, or resumed, and it never appears in the run list. The toast is
  the only feedback the launcher gives; the agent's own trace (a note, a log) is
  the real one. `watch(runId)` already no-ops on a missing id, so the client needed
  no special case.
- **The glossary grows a word rather than stretching one.** Calling this a Task
  would have made "a Task starts a Run" false, and that sentence is load-bearing
  in three places.
- **Failure is silent unless `log` is set.** A Dispatch that dies leaves nothing on
  the page. This is acceptable only because the agent it was built for guarantees
  its own trace — a failed expansion still records the seed verbatim. A Dispatch
  that cannot make that promise should set `log`.
- **The launcher now spawns non-`claude` processes.** Its security posture is
  unchanged in kind (trusted network, trusted `tasks.py`), but `exec` is a new
  place where a typo in private config becomes an arbitrary command. It is
  validated at import — a list of strings, never alongside `command` — so a
  mistake fails at startup rather than on a tap.
- **A `textarea` seed becomes worth having.** A prompt for a session is a
  filename-shaped thing; a thought for an agent is a sentence or three. `input:
  "textarea"` renders a real box, which in turn made a multi-line seed reachable
  for the first time — and the agent had to learn to flatten it.
