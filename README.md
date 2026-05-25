# claude-launcher

Tiny HTTP server that opens a new iTerm2 tab and launches the Claude Code CLI
(`cl` / `claude`) in a chosen directory. Built for triggering Claude sessions
on a Mac from a phone over Tailscale.

## Requirements

- macOS (uses AppleScript to drive iTerm2)
- [iTerm2](https://iterm2.com)
- Python 3.10+ (uses PEP 604 union syntax)
- Claude Code CLI installed and on `PATH` (the `claude` binary)

The first `/launch` request will prompt macOS to grant the calling
process automation access to iTerm2. Approve it.

## Run

```sh
python3 server.py
```

Listens on `0.0.0.0:8765`. Open `http://<host>:8765/` in a browser, type an
optional project subdirectory, tap **go**.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_LAUNCHER_HOST` | `0.0.0.0` | Bind address. Set to `127.0.0.1` to disallow network access. |
| `CLAUDE_LAUNCHER_PORT` | `8765` | TCP port. |
| `CLAUDE_LAUNCHER_DEFAULT_DIR` | `~` | Directory used when the `dir` form field is blank. |
| `CLAUDE_LAUNCHER_PROJECTS_ROOT` | `~/projects` | Allowed parent for the `dir` form field. Requests outside this subtree are rejected. |
| `CLAUDE_LAUNCHER_COMMAND` | `cl` | Command typed into the new iTerm2 tab after `cd`-ing. Set to `claude` if you don't have a `cl` alias. |

## Security model

**This server is intended for trusted networks only** — a Tailscale
tailnet, or a LAN behind a firewall. There is no authentication.

The server does enforce:

- **Path traversal blocked** — the `dir` form field is resolved with
  `realpath` and rejected unless it lives under `$CLAUDE_LAUNCHER_PROJECTS_ROOT`.
- **Shell injection blocked** — the directory path is single-quote escaped
  before being interpolated into the `cd` command sent to iTerm2.
- **AppleScript injection blocked** — backslashes, quotes, and control
  characters are escaped or stripped before being passed to `osascript`.
- **CSRF blocked** — `/launch` accepts `POST` only, and rejects requests
  whose `Origin` header doesn't match the server's `Host`. A
  Content-Security-Policy header on the index page prevents loading any
  external resources.
- **Log injection blocked** — control characters in request lines are
  scrubbed before being written to stderr.

What this does **not** defend against:

- Anyone on the same network reaching the server. If your Tailscale
  tailnet is shared, anyone on it can fire the launcher.
- Compromise of the local user account.

If you need access control beyond network-level trust, add a shared
secret token to the form and check it with `hmac.compare_digest` before
running `_handle_launch`.

## Auto-start at login (launchd)

A template is provided in `launchd/com.saivenky.claude-launcher.plist`.
Copy and edit it:

```sh
cp launchd/com.saivenky.claude-launcher.plist ~/Library/LaunchAgents/
# edit the file: set the absolute path to server.py and (if needed)
# the python3 binary
launchctl load ~/Library/LaunchAgents/com.saivenky.claude-launcher.plist
```

Stop it with `launchctl unload`.

## License

MIT — see [LICENSE](LICENSE).
