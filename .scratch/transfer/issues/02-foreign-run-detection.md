# 02 — Detect Foreign Runs, and stop resume forking transcripts

**What to build:** Server-side detection of **Foreign Runs** — a live `claude` the Launcher did not start — plus the fork-guard fix that depends on it. No UI in this slice.

A Foreign Run is a `claude` tty present in `_ps_output()` that is not one of our tmux panes. `_run_meta()` already reads `~/.claude/sessions/*.json` for every live `claude`, and `_ps_output()` is already fetched on every walk, so this costs **no new subprocess calls**. `_parse_claude_ttys` requires a `ttys*` tty, so headless `claude -p` (a **Dispatch**, a script, CI) is correctly invisible and must stay that way.

**The bug this fixes:** `_live_session_ids()` derives from `cached_runs()`, which walks tmux panes only. A **Session** live in another terminal is therefore not in the resume guard set, so `/api/resume` on it forks the transcript — while CONTEXT.md promises "at most one live **Run** per **Session** … a transcript is never forked." Feed the guard from *all* live `claude` processes, Managed and Foreign.

Note this *tightens* resume: Sessions that were wrongly resumable become correctly refused.

Foreign Runs carry Session, dir, status, and last message (all from Claude Code's own state, never the terminal's) — but no pane, so no rendered-pane data and no `@cl_run_id`. They must never be mistaken for Managed Runs by anything that reaches for a pane.

**Blocked by:** None

**Status:** ready-for-agent
