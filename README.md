# AttSD

**Assistant to the Software Developer.** Not the assistant — *assistant to*. It doesn't write your code; it watches the ones that do, and taps you when they need a decision.

Spawn, observe, and answer local Claude Code Runs from your phone.

It is a tiny HTTP server on the Mac, reached over Tailscale. It opens a
detached tmux window running `claude` with Remote Control enabled — so you can
drive the session from the Claude app — and it owns *lifecycle* (spawn, list,
close), the one thing the Claude app can't do.

It also **manages and responds** to running sessions from a phone, on its
[session board](#session-board): one screen that surfaces whichever live
session needs you and lets you answer it in place.

Two words, used precisely throughout (see [CONTEXT.md](CONTEXT.md)):

- a **session** is the durable thread Claude Code identifies by
  `sessionId` — the one the Claude app shows you, the one you resume.
- a **run** is one `claude` process executing a session, concretely a
  tmux window. The server only ever starts and closes runs. **Closing a
  run never destroys a session.**

## Requirements

- `tmux` on `PATH` (the Run substrate; see
  [ADR 0010](docs/adr/0010-tmux-as-the-run-substrate.md)) — macOS or Linux
- Python 3.10+
- Claude Code CLI (`claude`) on `PATH`
- For Remote Control: Claude Code v2.1.51+, a Pro/Max/Team/Enterprise
  plan, and full-scope login (`claude auth login`, not an API key)

Runs live in a single detached `tmux -L attsd` server, so there is
no GUI app to focus and nothing to approve — a Run never steals the Mac's
foreground.

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

**The launch page requires JavaScript.** It's a small client over a JSON API
(`GET /api/runs`, `POST /api/launch|resume|close`) — see
[ADR 0003](docs/adr/0003-launcher-page-runs-javascript.md).

## Session board

`/board` is a phone-first screen for *managing and responding to* every
live session, not just spawning them. It shows **one session at a time** —
the one that most needs you — with the rest queued behind it as a curated
round-robin, and resurfaces a session automatically when it next needs
attention, so none is forgotten. Long-idle sessions park as *dormant*;
working ones sit in *watching* until they block again.

Each focus card carries the session's summary, its recent run-up context
(rendered from the transcript), and what it's blocked on — a question, a
permission prompt, or just "your move" — with inline controls to
**respond**: type a reply or pick an option, injected straight into the
run. A permission menu is read off the rendered pane so its options are the
real ones. You can also set a per-session **priority**, **snooze** a
session, or **skip** to the next; those persist across restarts.

Each row carries the two handoffs onto a live run: `↗` deep-links it into
the Claude app, and `❯` copies a `tmux … new-session` line for a local
terminal. Paste that in a terminal to drive the run by hand; `Ctrl-b d` to
leave, and the run keeps going. See
[ADR 0011](docs/adr/0011-terminal-handoff-attaches-to-the-live-run.md).

Respond types into a live session — and can approve a permission — so it is
gated by a shared secret: set `ATTSD_TOKEN` and enter it once in
the browser. Without a token the board is read-only. Before typing, it
refuses to append onto a box that already holds unsent text, showing you
what's there with a one-tap clear.

The board's UI is served from files on disk (`web/board.html`,
`web/board.js`) and re-read per request, so it ships without an AttSD server
restart; the `/api/*` surface behind it is the stable contract. See ADRs
[0005](docs/adr/0005-ui-hot-served-from-disk.md) (hot-served UI),
[0006](docs/adr/0006-board-context-rendered-server-side.md) (context
rendering), and [0007](docs/adr/0007-respond-requires-auth.md) (Respond +
auth).

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
generic launch field. Task runs are tagged (`user.cl_task`) so the live list
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

**The spawned command inherits the AttSD server's environment**, which under
launchd is bare. If it needs more (a `PATH` entry, `USER`), set it in a
wrapper script rather than relying on what happens to be inherited.

## Resume a session

To get back into a Claude Code session you closed, paste its `sessionId`
into the `$ cl --resume …` line and tap **resume**. The server looks up
that session's transcript, finds the directory it ran in, and starts a
fresh run there with `claude --resume <id>` (Remote Control on, so the
Claude app can drive it).

You supply the id — the server only lists *live* runs, so read the id
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
| `ATTSD_HOST` | `0.0.0.0` | Bind address. Set to `127.0.0.1` for local-only. |
| `ATTSD_PORT` | `8765` | TCP port. |
| `ATTSD_DEFAULT_DIR` | `~` | Used when the form's `dir` field is blank. |
| `ATTSD_PROJECTS_ROOT` | `~/projects` | Allowed parent for `dir`. Anything outside is rejected. |
| `ATTSD_COMMAND` | `cl` | Command run after `cd`. Use `claude` if you don't have a `cl` alias. |
| `ATTSD_REMOTE` | `1` | Append `--remote-control` so the Claude app can drive the session. Set `0` to disable. |
| `ATTSD_TOKEN` | *(unset)* | Shared secret for the board's **Respond**. Unset disables Respond; the board stays read-only. |

## Security model

**Trusted networks only** — Tailscale, or LAN behind a firewall. Lifecycle
(spawn / resume / close) has no authentication; anyone who can reach the
port can trigger a session, and the board serves session context over the
network. The board's **Respond** is the exception: because it types into a
live session and can approve a permission, it requires `ATTSD_TOKEN`
(checked with `hmac.compare_digest`) and is disabled until one is set. See
[ADR 0007](docs/adr/0007-respond-requires-auth.md).

What the server does enforce:

- Path traversal blocked (`realpath` + prefix check on
  `ATTSD_PROJECTS_ROOT`). Named-task workdirs come from your own
  `tasks.py` (trusted config) and are intentionally *not* confined to
  `PROJECTS_ROOT`; the generic `dir` field still is.
- Shell injection blocked: the launch line is single-quoted, and Respond
  types text literally with `tmux send-keys -l` while selector keys come from
  a fixed key map — a client can never inject a raw escape sequence.
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
  run, and only closes tmux windows the AttSD server created — never arbitrary
  processes or windows.
- `/api/transfer` takes a `sessionId`, never a pid. The pid it signals is
  re-derived server-side from the live-run walk and must belong to a
  currently-live *foreign* `claude` (one started by hand elsewhere); a
  session naming a server-created run is refused, because closing those
  is `/api/close`'s job. So the set of processes this can ever kill is
  exactly the set the server already lists.
- Log injection blocked (CRLF + control chars scrubbed).

If you need access control on top, add a shared-secret token to the JSON
body and check it with `hmac.compare_digest`.

## Auto-start at login (launchd)

A template is in `launchd/com.saivenky.attsd.plist`.

```sh
cp launchd/com.saivenky.attsd.plist ~/Library/LaunchAgents/
# Edit: replace __SERVER_PY__ with the absolute path to server.py
launchctl load   ~/Library/LaunchAgents/com.saivenky.attsd.plist
launchctl unload ~/Library/LaunchAgents/com.saivenky.attsd.plist  # stop
tail -F ~/Library/Logs/attsd.log                                            # logs
```

## License

MIT — see [LICENSE](LICENSE).
