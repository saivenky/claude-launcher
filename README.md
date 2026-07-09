# claude-launcher

Tiny HTTP server to spawn and manage Claude Code sessions on a Mac from
a phone over Tailscale. Opens a new iTerm2 tab running `claude` with
Remote Control enabled — so you can drive the session from the Claude
app — and lists what's running, each with a tap-to-close button.

The launcher owns *lifecycle* (spawn, list, close); the Claude app owns
everything *inside* a session (typing tasks, approvals, output).
Spawning a new local session is the one thing the app can't do — that's
the gap this fills.

Two words, used precisely throughout (see [CONTEXT.md](CONTEXT.md)):

- a **session** is the durable thread Claude Code identifies by
  `sessionId` — the one the Claude app shows you, the one you resume.
- a **run** is one `claude` process executing a session, concretely an
  iTerm pane. The launcher only ever starts and closes runs. **Closing a
  run never destroys a session.**

## Requirements

- macOS with iTerm2
- Python 3.10+
- Claude Code CLI (`claude`) on `PATH`
- For Remote Control: Claude Code v2.1.51+, a Pro/Max/Team/Enterprise
  plan, and full-scope login (`claude auth login`, not an API key)

First `/launch` will prompt macOS to grant automation access to iTerm2.
Approve it.

## Run

```sh
python3 server.py
```

Open `http://<host>:8765/` in a browser. Type a subdirectory and tap
**launch** to start a run; the page below lists every running
`claude` run — title, dir, last-active time, status
(working/waiting/idle), and a recent-message snippet — sorted newest
first to mirror the Claude app, each with a **×** to close it.

## Named tasks (optional)

Above the generic field you can show one-tap buttons for sessions you
launch often. Copy `tasks.example.py` to `tasks.py` and list them:

```python
TASKS = [
    {"id": "capture", "label": "capture", "workdir": "~/notes",
     "command": "/capture", "input": "text"},
]
```

Each task spawns `cl <command>` in `workdir` (a `/slash-command` is
typical); `input: "text"` adds a seed box whose value is appended to the
command. `tasks.py` is private (gitignored) — without it you just get the
generic launcher. Task runs are tagged (`user.cl_task`) so the live list
shows their label; the list still includes every running `claude` run.

## Resume a session

To get back into a Claude Code session you closed, paste its `sessionId`
into the `$ cl --resume …` line and tap **resume**. The launcher looks up
that session's transcript, finds the directory it ran in, and starts a
fresh run there with `claude --resume <id>` (Remote Control on, so the
Claude app can drive it).

You supply the id — the launcher only lists *live* runs, so read the id
off the Claude app. Notes:

- A session that already has a live run is refused (you're already in it;
  a second run on one transcript would fork it). Close it first.
- Resume is intentionally *not* confined to `PROJECTS_ROOT` — it can only
  reopen a directory where you already ran `claude`, never an arbitrary
  path, so it reaches `~/obsidian` and anything else outside the root. See
  [ADR 0002](docs/adr/0002-resume-spans-all-conversations.md).

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_LAUNCHER_HOST` | `0.0.0.0` | Bind address. Set to `127.0.0.1` for local-only. |
| `CLAUDE_LAUNCHER_PORT` | `8765` | TCP port. |
| `CLAUDE_LAUNCHER_DEFAULT_DIR` | `~` | Used when the form's `dir` field is blank. |
| `CLAUDE_LAUNCHER_PROJECTS_ROOT` | `~/projects` | Allowed parent for `dir`. Anything outside is rejected. |
| `CLAUDE_LAUNCHER_COMMAND` | `cl` | Command run after `cd`. Use `claude` if you don't have a `cl` alias. |
| `CLAUDE_LAUNCHER_REMOTE` | `1` | Append `--remote-control` so the Claude app can drive the session. Set `0` to disable. |

## Security model

**Trusted networks only** — Tailscale, or LAN behind a firewall. There
is no authentication; anyone who can reach the port can trigger a
session.

What the server does enforce:

- Path traversal blocked (`realpath` + prefix check on
  `CLAUDE_LAUNCHER_PROJECTS_ROOT`). Named-task workdirs come from your own
  `tasks.py` (trusted config) and are intentionally *not* confined to
  `PROJECTS_ROOT`; the generic `dir` field still is.
- Shell and AppleScript injection blocked (quoting + control-char
  stripping).
- CSRF blocked (`/launch`, `/resume`, and `/close` are POST-only with
  same-origin `Origin` check).
- `/resume` only accepts a well-formed `sessionId` (validated before it
  touches the shell) that already has a transcript on disk, and refuses ids
  whose run is currently live.
- `/close` only acts on a run id that matches a currently-live `claude`
  run, and only closes iTerm panes — never arbitrary processes or tabs.
- Log injection blocked (CRLF + control chars scrubbed).

If you need access control on top, add a shared-secret token to the
form and check it with `hmac.compare_digest`.

## Auto-start at login (launchd)

A template is in `launchd/com.saivenky.claude-launcher.plist`.

```sh
cp launchd/com.saivenky.claude-launcher.plist ~/Library/LaunchAgents/
# Edit: replace __SERVER_PY__ with the absolute path to server.py
launchctl load   ~/Library/LaunchAgents/com.saivenky.claude-launcher.plist
launchctl unload ~/Library/LaunchAgents/com.saivenky.claude-launcher.plist  # stop
tail -F /tmp/claude-launcher.log                                            # logs
```

## License

MIT — see [LICENSE](LICENSE).
