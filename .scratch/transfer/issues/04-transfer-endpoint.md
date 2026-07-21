# 04 — Transfer: kill the Foreign Run, resume its Session

**What to build:** The **Transfer** verb — one tap on a **Foreign Run** that kills it and **resumes** its **Session** as a Managed Run in tmux.

**One atomic endpoint** (`/api/transfer`): kill → wait for exit → resume → return the new run id. Split across two client calls, an AFK tap that fails between them leaves a killed Run and nothing running — strictly worse than not tapping. The wait is load-bearing, not politeness: the slice-02 resume guard refuses until the old pid is gone. `SIGTERM`, then `SIGKILL` on a short timeout.

Both inputs are already in `~/.claude/sessions/<pid>.json` — cwd and `sessionId`. `_resume_cmd` already builds the launch line.

- **Ungated** (no shared secret), consistent with launch / resume / close. The token exists because **Respond** can approve tool calls (ADR 0007); Transfer cannot approve anything.
- **Never refuse a `working` Run.** Show its status on the button and name the cost in the confirm — "this Run is mid-turn; the current turn will be lost." A refusal just strands you when you are away from the Mac.
- If the resume fails after the kill, **report it loudly**. The Session is on disk so it is recoverable, but the user must know they now have nothing running.
- Kill the *process*, not the terminal. The original terminal keeps a dead tab until a human closes it (ADR 0012) — reaching into a GUI app is the dependency this design refuses.

**Blocked by:** 02, 03

**Status:** ready-for-agent
