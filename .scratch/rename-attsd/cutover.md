# Cutover runbook — ticket 06

Run this **by hand**, from a terminal **outside tmux** (Terminal.app or iTerm,
a fresh window, not attached to any tmux session). Step 2 kills the tmux server
that every Claude Code Run on this machine lives in, including the one that
wrote this file, and step 4 renames the directory those Runs are sitting in.
Nothing inside can survive to finish the job.

Do not paste the whole thing at once. Run a step, read its check, then move on.

## Before you start

- `ship/rename-attsd` is merged into `main` and the ship worktree is removed.
  `git -C ~/projects/claude-launcher worktree list` should show one line.
- Ticket 05 is done: the GitHub repo is renamed to `attsd`.
- Nothing important is mid-flight in the nine live Runs. They all die in step 2
  and come back in step 9, but an unsaved edit in a pane does not.

## 1. Capture the manifest, outside the repo

The repo is about to move, so the manifest must not live in it.

```
python3 ~/projects/claude-launcher/tools/run-manifest.py claude-launcher > /tmp/attsd-manifest.md
cat /tmp/attsd-manifest.md
```

**Check:** every row has a `sessionId`. A row reading `(none — Run had not
registered)` is a Run you cannot resume automatically — note its cwd and
restart it by hand later.

## 2. Drain every Run

```
tmux -L claude-launcher kill-server
```

**Check:** `tmux -L claude-launcher ls` prints "no server running". Your agent
sessions are now gone; you are on your own until step 9.

## 3. Unload the old LaunchAgent

```
launchctl unload ~/Library/LaunchAgents/com.saivenky.claude-launcher.plist
rm ~/Library/LaunchAgents/com.saivenky.claude-launcher.plist
```

**Check:** `launchctl list | grep saivenky` prints nothing.

## 4. Rename the repo

```
mv ~/projects/claude-launcher ~/projects/attsd
```

**Check:** `ls ~/projects/attsd/server.py` exists and `~/projects/claude-launcher`
is gone.

## 5. Rename the Claude project-history slug

Claude Code derives this directory's name from the cwd. If you skip it, this
repo's history splits across two slugs and `--resume` shows you only half of
it — 27 session files' worth.

```
mv ~/.claude/projects/-Users-skandallu-projects-claude-launcher \
   ~/.claude/projects/-Users-skandallu-projects-attsd
```

**Check:** `ls ~/.claude/projects/-Users-skandallu-projects-attsd | wc -l`
reports the same count it had before (27 at the time of writing).

## 6. Repoint the pinned permission paths

`.claude/settings.local.json` is untracked live state, not a repo file, which
is why it was not changed with the rest of the rename.

```
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / "projects/attsd/.claude/settings.local.json"
t = p.read_text().replace("/Users/skandallu/projects/claude-launcher",
                          "/Users/skandallu/projects/attsd")
json.loads(t)          # fail loudly rather than write broken JSON
p.write_text(t)
print("ok")
PY
```

**Check:** it prints `ok`, and `grep projects/ ~/projects/attsd/.claude/settings.local.json`
shows only `projects/attsd`.

## 7. Repoint the git remote

GitHub redirects the old URL, so this is hygiene rather than a rescue.

```
git -C ~/projects/attsd remote set-url origin https://github.com/saivenky/attsd.git
git -C ~/projects/attsd fetch
```

**Check:** `git -C ~/projects/attsd remote -v` shows `attsd`, and the fetch is
silent.

## 8. Install and load the new LaunchAgent

The plist ships as a template with two placeholders.

```
mkdir -p ~/Library/Logs
sed -e "s|__SERVER_PY__|$HOME/projects/attsd/server.py|" \
    -e "s|__HOME__|$HOME|" \
    ~/projects/attsd/launchd/com.saivenky.attsd.plist \
    > ~/Library/LaunchAgents/com.saivenky.attsd.plist
launchctl load ~/Library/LaunchAgents/com.saivenky.attsd.plist
```

**Check:** `launchctl list | grep attsd` shows the job; after a few seconds
`tail ~/Library/Logs/attsd.log` shows the server announcing its port, and
loading the board in a browser works. No `__` placeholder should survive:
`grep __ ~/Library/LaunchAgents/com.saivenky.attsd.plist` prints nothing.

## 9. Resume the Runs

For each row in `/tmp/attsd-manifest.md`, from that row's cwd:

```
claude --resume <sessionId>
```

The Runs whose cwd was `~/projects/claude-launcher` now live at
`~/projects/attsd`; resume those from the new path so their history lands in
the renamed slug.

**Check:** `tmux -L attsd list-panes -a` shows the Runs back, and the board
lists them. The old socket should not reappear:
`tmux -L claude-launcher ls` still says no server running.

## 10. Hand back

Ticket 07 (re-capturing the ask fixtures on socket `attsd`) is unblocked once
this is done, and can go back to an agent.
