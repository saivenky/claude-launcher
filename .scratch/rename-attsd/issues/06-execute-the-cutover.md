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
6. Repoint the untracked `.claude/settings.local.json` permission paths.
7. Resume every Session from the manifest onto socket `attsd`.

The agent delivers the runbook — the exact commands, in order, with what to
check between each. A human runs it with their own eyes on it. A detached
script that renames its own working directory, kills the socket it was launched
from, and reloads launchd is precisely the shape of thing that fails halfway
and leaves no tmux, no LaunchAgent, and no agent left to repair it.

**Blocked by:** 03, 04, 05.

**Runbook:** `.scratch/rename-attsd/cutover.md` — written and ready; the
remaining work is a human running it.

**Status:** landed

- [x] A runbook exists with the exact commands in order and a check after each
- [x] All eight Runs are drained and then resumed on socket `attsd`
- [x] Both directories renamed; no path still says `claude-launcher`
- [x] `--resume` from `~/projects/attsd` shows the full session history
- [x] The new LaunchAgent is loaded and the server is reachable from the phone
- [x] Logs are landing in `~/Library/Logs/attsd.log`

## Comments

Run by hand from Terminal.app. Repo, remote and the 28-file project slug all moved; the old socket is dead. Two things the runbook got wrong and the cutover exposed: `launchctl load` registers a job without honouring `RunAtLoad`, so the server sat at `runs = 0` until `kickstart` (README now says `bootstrap`/`kickstart`); and deleting the old LaunchAgent destroyed the only copy of `ATTSD_DEFAULT_DIR` and `ATTSD_TOKEN`, which the repo template never carried. DEFAULT_DIR restored, TOKEN rotated.
