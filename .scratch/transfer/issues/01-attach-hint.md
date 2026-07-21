# 01 — The `❯` Attach line says how to leave

**What to build:** The **Board**'s per-Run `❯` copies a `tmux … new-session` line (ADR 0011). Paste it and you land in a full-screen TUI with no visible exit — the one place the tmux substrate meets a user who may not know tmux. Add the escape route where it is needed.

- The `❯` toast (or the affordance next to it) names the exit: paste in a terminal, `Ctrl-b d` to leave, and the **Run** keeps going.
- README gets the same sentence next to the Attach/terminal-handoff material.

Closing the terminal window is already safe — `destroy-unattached on` evaporates the throwaway view and the Run survives (ADR 0011) — so this is legibility only, no behaviour change.

Unrelated to **Transfer**; shipped first because it is small and independent.

**Blocked by:** None

**Status:** ready-for-agent
