# 04 — Capture the live-Run manifest

**What to build:** a record of every Run alive on the socket, captured *before*
anything is drained, so the cutover can bring them all back instead of losing
the seven that belong to other repos. For each live pane the manifest holds
enough to resume it: the `sessionId`, the working directory, and the pane it
occupied. It is committed, so it survives the drain that destroys its subject.

At capture time the socket holds eight Runs — this repo plus `caddy` x2, `jot`
x2, `tempo` x2, and `strength-log`. Seven of them are collateral: tmux cannot
move a window between servers, so a socket rename can only close and re-create
them. The manifest is what makes that safe.

The capture must be read-only. Nothing in this ticket may kill, signal, or
send keys to a pane — one of them is the session doing the work.

**Blocked by:** None — can start immediately, and must run before ticket 06.

**Status:** landed

- [x] A committed manifest lists every live Run with its `sessionId`, cwd, and pane id
- [x] All eight current Runs appear
- [x] The capture is read-only — no pane is killed, signalled, or written to
- [x] The manifest's format is directly usable by the ticket 06 runbook
- [x] `python -m unittest discover -s tests` and `ruff check .` pass
