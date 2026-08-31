# 03 — Runtime identifiers move to `attsd`

**What to build:** every machine-readable name the AttSD server uses becomes
`attsd`. The environment variable is `ATTSD_TMUX_SOCKET`; the default tmux
socket it names is `attsd`; the LaunchAgent's label is `com.saivenky.attsd`;
and its log moves from `/tmp/claude-launcher.log` to
`~/Library/Logs/attsd.log`, because a LaunchAgent's log should survive a reboot
and `/tmp` is world-writable. Messages the server prints on startup and on
failure say AttSD. The three absolute paths pinned in
`.claude/settings.local.json` point at `~/projects/attsd`.

The plist's own load instructions must match the new label and path, since
those comments are what the cutover runbook follows.

This ticket changes *names in code*; it does not move anything on the running
machine. The test suite must stay green while the live socket is still called
`claude-launcher` — the new names bind at cutover, in ticket 06.

**Blocked by:** None — can start immediately. It touches files disjoint from
tickets 01 and 02.

**Status:** landed — f27cc8e

- [x] `ATTSD_TMUX_SOCKET` is the env var; `attsd` the default socket
- [x] LaunchAgent label is `com.saivenky.attsd`, log path `~/Library/Logs/attsd.log`, plist filename renamed to match
- [x] Plist load instructions reference the new label, log path, and `~/projects/attsd`
- [x] Server startup and failure messages say AttSD
- [x] `python -m unittest discover -s tests` and `ruff check .` pass with the old socket still live
