#!/usr/bin/env python3
"""Capture the live Runs on a tmux socket, so a socket rename can restore them.

tmux cannot move a window between servers, so renaming the socket can only
close Runs and re-create them. That is safe because Runs are disposable and
Sessions are durable (ADR 0002) -- but only if we know which Sessions were
running. This writes that list down before anything is drained.

Read-only. It never kills, signals, or sends keys to a pane; one of those
panes is usually the session doing the work.

    python3 tools/run-manifest.py [socket] > .scratch/<effort>/manifest.md
"""

import glob
import json
import os
import subprocess
import sys

RUNS_DIR = os.path.expanduser("~/.claude/sessions")


def _tty_pids() -> dict[str, int]:
    """tty -> pid of the `claude` process on it.

    A pane's own pid is the shell; the Run is a child of it, so the tty is
    what joins a pane to the Claude Code process metadata.
    """
    out = subprocess.run(
        ["ps", "-Ao", "tty=,pid=,comm="], capture_output=True, text=True
    ).stdout
    ttys: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        tty, pid_s, comm = parts
        if "claude" not in os.path.basename(comm):
            continue
        try:
            ttys.setdefault("/dev/" + tty, int(pid_s))
        except ValueError:
            continue
    return ttys


def _run_meta() -> dict[int, dict]:
    """pid -> Run metadata, as Claude Code writes it into ~/.claude/sessions."""
    meta: dict[int, dict] = {}
    for path in glob.glob(os.path.join(RUNS_DIR, "*.json")):
        try:
            with open(path) as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        pid = j.get("pid")
        if isinstance(pid, int):
            meta[pid] = j
    return meta


def capture(socket: str) -> list[dict]:
    fmt = "#{pane_id}\t#{window_id}\t#{pane_tty}\t#{pane_current_path}"
    out = subprocess.run(
        ["tmux", "-L", socket, "list-panes", "-a", "-F", fmt],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"run-manifest: cannot read socket {socket!r}: {out.stderr.strip()}")

    ttys, meta = _tty_pids(), _run_meta()
    runs: dict[str, dict] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        pane, window, tty, cwd = line.split("\t")
        pid = ttys.get(tty)
        m = meta.get(pid, {}) if pid else {}
        runs[pane] = {
            "pane": pane,
            "window": window,
            "cwd": m.get("cwd") or cwd,
            "sessionId": m.get("sessionId", ""),
        }
    return sorted(runs.values(), key=lambda r: r["cwd"])


def main() -> None:
    socket = sys.argv[1] if len(sys.argv) > 1 else "attsd"
    runs = capture(socket)
    print(f"# Live Runs on socket `{socket}`\n")
    print(f"{len(runs)} Runs. Resume each with `claude --resume <sessionId>` from its cwd.\n")
    print("This is a snapshot. Runs come and go, so re-run this immediately")
    print("before draining and use *that* output, not a committed copy.\n")
    print("| pane | cwd | sessionId |")
    print("| --- | --- | --- |")
    for r in runs:
        print(f"| `{r['pane']}` | `{r['cwd']}` | `{r['sessionId'] or '(none — Run had not registered)'}` |")


if __name__ == "__main__":
    main()
