# Transfer a Foreign Run into the Launcher

A `claude` started by hand in another terminal (iTerm, Terminal.app, VS Code, a
plain tmux socket) is invisible to the Board and unreachable from the phone.
**Transfer** makes it reachable: one tap kills it and **resumes** its Session as
a Managed Run in tmux.

See ADR 0012 for the decisions and rejected alternatives, and CONTEXT.md for
**Foreign Run** / **Transfer**.

## Why

- **Access.** A Run left going at the Mac cannot be driven from the phone.
- **A broken invariant.** `_live_session_ids()` walks tmux panes only, so a
  Session live in another terminal is not in the resume guard — resuming it
  today forks the transcript. CONTEXT.md promises this cannot happen.

## Shape

- Detection is free: a Foreign Run is a `claude` tty in `_ps_output()` that is
  not one of our panes. `_run_meta()` already supplies its Session, cwd, status.
- Foreign Runs are **listed, never driven**: no Respond, no Attach, no close.
- Foreign Runs stay out of triage: never **Blocked**, never the **Focus**, never
  in **Rotation**.
- Transfer is **on demand** (a tap), **atomic** (one endpoint), and **ungated**.
- Accepted losses: the in-flight turn, unsent typed text (silent), and a dead
  tab left behind in the original terminal.

## Out of scope

- Any second terminal backbone. Rejected — see ADR 0012.
- A recent-Sessions quick-pick on the resume box. Worth building; not this.
- Unattended reaping of foreign `claude` processes. Rejected — self-DoS.

## Slices — all shipped

1. `01-attach-hint` — unrelated tidy-up shipped first (see its ticket). `f93c18b`
2. `02-foreign-run-detection` — server-side detection + fork-guard fix. `d6ba445`
3. `03-board-lists-foreign-runs` — the quiet section, outside triage. `dbe559d`
4. `04-transfer-endpoint` — kill → wait → resume, plus the button. `adc3624`

Verified end to end against a real hand-started `claude`: it surfaced in the
quiet section, transferred, and came back as a Managed Run. The iTerm tab
survived at a dead shell prompt, as ADR 0012 says it must.
