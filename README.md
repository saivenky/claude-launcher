# claude-launcher

Tiny HTTP server that opens a new iTerm2 tab and launches the Claude
Code CLI in a chosen directory. Built for triggering Claude sessions on
a Mac from a phone over Tailscale.

## Requirements

- macOS with iTerm2
- Python 3.10+
- Claude Code CLI (`claude`) on `PATH`

First `/launch` will prompt macOS to grant automation access to iTerm2.
Approve it.

## Run

```sh
python3 server.py
```

Open `http://<host>:8765/` in a browser, optionally type a subdirectory,
tap **go**.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_LAUNCHER_HOST` | `0.0.0.0` | Bind address. Set to `127.0.0.1` for local-only. |
| `CLAUDE_LAUNCHER_PORT` | `8765` | TCP port. |
| `CLAUDE_LAUNCHER_DEFAULT_DIR` | `~` | Used when the form's `dir` field is blank. |
| `CLAUDE_LAUNCHER_PROJECTS_ROOT` | `~/projects` | Allowed parent for `dir`. Anything outside is rejected. |
| `CLAUDE_LAUNCHER_COMMAND` | `cl` | Command run after `cd`. Use `claude` if you don't have a `cl` alias. |

## Security model

**Trusted networks only** — Tailscale, or LAN behind a firewall. There
is no authentication; anyone who can reach the port can trigger a
session.

What the server does enforce:

- Path traversal blocked (`realpath` + prefix check on
  `CLAUDE_LAUNCHER_PROJECTS_ROOT`).
- Shell and AppleScript injection blocked (quoting + control-char
  stripping).
- CSRF blocked (`/launch` is POST-only with same-origin `Origin` check).
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
