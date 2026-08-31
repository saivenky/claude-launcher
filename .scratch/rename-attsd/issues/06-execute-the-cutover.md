# 06 — Execute the cutover

**What to build:** the machine actually becomes AttSD. Run by hand, from a
terminal **outside tmux**, because every step below destroys something the
agent session depends on: it is a pane on the socket being drained, sitting in
the directory being renamed.

The sequence, in order:

1. Drain every Run on socket `claude-launcher` — all eight, per the ticket 04
   manifest.
2. Unload the old LaunchAgent (`com.saivenky.claude-launcher`).
3. Rename `~/projects/claude-launcher` to `~/projects/attsd`.
4. Rename `~/.claude/projects/-Users-skandallu-projects-claude-launcher` to the
   slug matching the new path, carrying its 27 session files. Claude Code
   derives that directory name from the cwd, so skipping this splits the repo's
   history across two slugs and `--resume` shows only half of it.
5. Install and load the new LaunchAgent (`com.saivenky.attsd`).
6. Resume every Session from the manifest onto socket `attsd`.

The agent delivers the runbook — the exact commands, in order, with what to
check between each. A human runs it with their own eyes on it. A detached
script that renames its own working directory, kills the socket it was launched
from, and reloads launchd is precisely the shape of thing that fails halfway
and leaves no tmux, no LaunchAgent, and no agent left to repair it.

**Blocked by:** 03, 04, 05.

**Status:** ready-for-human

- [ ] A runbook exists with the exact commands in order and a check after each
- [ ] All eight Runs are drained and then resumed on socket `attsd`
- [ ] Both directories renamed; no path still says `claude-launcher`
- [ ] `--resume` from `~/projects/attsd` shows the full session history
- [ ] The new LaunchAgent is loaded and the server is reachable from the phone
- [ ] Logs are landing in `~/Library/Logs/attsd.log`
