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
**launch** to start a run; the page below lists every running `claude`
run — title, dir, last-active time, status (working/waiting/idle), and a
recent-message snippet — sorted newest first to mirror the Claude app,
each with a **×** to close it.

Nothing navigates away. Tapping a button posts JSON, shows a toast, and
refreshes the run list in place; the list polls every 4s while the page
is visible and pauses when it isn't. A freshly launched run shows as
`starting…` for the second or so before `claude` registers itself.

**The page requires JavaScript.** It's a small client over a JSON API
(`GET /api/runs`, `POST /api/launch|resume|close`) — see
[ADR 0003](docs/adr/0003-launcher-page-runs-javascript.md).

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

## Dispatches (optional)

A task with `exec` instead of `command` is a **Dispatch**: a preset command
run *detached* — no `claude`, no session, no pane, nothing in the run list
and nothing to close. It exists for fire-and-forget agents you want to
trigger from a phone without opening a session to babysit.

```python
TASKS = [
    {"id": "jot", "label": "jot", "workdir": "~/projects/my-agents",
     "exec": ["/bin/bash", "scripts/run_jot.sh"], "log": "logs/jot.log",
     "input": "textarea", "placeholder": "a thought…"},
]
```

`exec` is a list, exec'd with no shell, and the seed is appended as one
further argv element — so it can never be word-split or interpolated.
`input: "textarea"` gives a multi-line box, because a thought for an agent
is a sentence or three rather than a filename. `log` (relative to `workdir`)
collects stdout+stderr; omit it and output is discarded.

A Dispatch returns no `runId`, so the page just toasts and moves on. Its
own output is its only trace — set `log`, or use it only for a command that
records its own results. See [ADR 0004](docs/adr/0004-dispatches-run-detached-not-as-runs.md).

**The spawned command inherits the launcher's environment**, which under
launchd is bare. If it needs more (a `PATH` entry, `USER`), set it in a
wrapper script rather than relying on what happens to be inherited.

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
  [ADR 0002](docs/adr/0002-resume-spans-all-sessions.md).

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
- CSRF blocked structurally: every `/api/*` action is POST-only and must
  send `Content-Type: application/json`, which is not a CORS "simple
  request" — a cross-origin `fetch` has to preflight, and the preflight
  fails because no CORS headers are sent. The `Origin` header is also
  required and must be same-origin (it fails *closed* when absent, so
  `curl` must send one too).
- XSS blocked structurally: the API returns raw JSON and the client only
  ever writes it into the DOM as text (`textContent`, never `innerHTML`),
  so transcript snippets cannot become markup. `status` is whitelisted
  server-side because it alone becomes a CSS class.
- `/api/resume` only accepts a well-formed `sessionId` (validated before it
  touches the shell) that already has a transcript on disk, and refuses ids
  whose run is currently live.
- `/api/close` only acts on a run id that matches a currently-live `claude`
  run, and only closes iTerm panes — never arbitrary processes or tabs.
- Log injection blocked (CRLF + control chars scrubbed).

If you need access control on top, add a shared-secret token to the JSON
body and check it with `hmac.compare_digest`.

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
