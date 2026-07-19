#!/usr/bin/env python3
"""Spawn and manage Claude Code sessions on a Mac from a phone over Tailscale.

Vocabulary (see CONTEXT.md): a **Session** is the durable thread Claude Code
identifies by `sessionId`; a **Run** is one `claude` process executing it,
concretely a tmux window (ADR 0010). The launcher only ever creates and
destroys Runs.

Three ways to start a Run, on one page:
  - generic: type a subdir under PROJECTS_ROOT -> runs `cl` there
  - named tasks: one-tap buttons from tasks.py -> runs `cl <slash-command>` in
    a fixed workdir, stamped with user.cl_task so the live list can label it
  - resume: paste a past Session's sessionId -> runs `cl --resume <id>`
    in that Session's own dir (found from its transcript)

A fourth button kind starts no Run at all: a **Dispatch** (ADR 0004) is a
tasks.py entry with `exec`, a preset command run detached — no Session, no
pane, nothing to observe or close. It is how a fire-and-forget agent is
triggered from a phone.

The page is a small JS client over a JSON API (ADR 0003): actions post JSON,
show a toast, and refresh the Run list in place instead of navigating away.

tasks.py is optional and private; without it you just get the generic
launcher (and resume)."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import glob
import hashlib
import hmac
import html
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

HOST = os.environ.get("CLAUDE_LAUNCHER_HOST", "0.0.0.0")
PORT = int(os.environ.get("CLAUDE_LAUNCHER_PORT", "8765"))
DEFAULT_DIR = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_DEFAULT_DIR", "~"))
PROJECTS_ROOT = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_PROJECTS_ROOT", "~/projects"))
COMMAND = os.environ.get("CLAUDE_LAUNCHER_COMMAND", "cl")
REMOTE = os.environ.get("CLAUDE_LAUNCHER_REMOTE", "1").strip().lower() not in (
    "0", "false", "no", "off", "",
)

# The dedicated tmux socket AND session name a Run lives in (ADR 0010). One
# detached `tmux -L <socket>` server holds a single session of this name; each
# Run is a window in it. Env-overridable in the spirit of CLAUDE_LAUNCHER_COMMAND.
TMUX_SOCKET = os.environ.get("CLAUDE_LAUNCHER_TMUX_SOCKET", "claude-launcher")

# Respond (typing into a live Run, incl. approving a permission) removes the
# human-in-the-loop backstop, so it is gated by a shared secret — checked with
# hmac.compare_digest, sent by the client in the JSON body. Unset => Respond is
# disabled entirely; the read-only Board still works. See ADR 0007.
TOKEN = os.environ.get("CLAUDE_LAUNCHER_TOKEN", "")
MAX_RESPOND_CHARS = 2000

# A seed is typed on a phone, not piped. Anything longer is a paste accident, and
# a NUL cannot cross an argv boundary at all.
#
# Kept well under MAX_BODY_BYTES (4096): a seed of N characters can reach 4N bytes
# as UTF-8, so 800 always fits inside the body cap. Otherwise a long jot would trip
# a confusing "body too large" before this check ever ran.
MAX_SEED_CHARS = 800

_TASKS_PATH = Path(__file__).resolve().parent / "tasks.py"
_TASKS_LOCK = threading.Lock()


def _buttons(task: dict) -> list:
    """The buttons a task renders. A task with an explicit `buttons` list shows
    several buttons over one shared seed box; a plain task is its own single button.
    Each button appends `args` to the task's `exec` (or overrides a field), so one
    Dispatch can offer variants — jot vs. log — differing only by a flag."""
    return task.get("buttons") or [{"id": task["id"], "label": task["label"]}]


def _resolve(task: dict, button: dict) -> dict:
    """A button flattened into a complete task spec the launch handler understands:
    the task's shared fields, the button's own fields folded in, and the button's
    `args` appended to `exec`. Keyed by the button id, so the handler never learns
    about groups."""
    if "id" not in button or "label" not in button:
        raise ValueError(f"task {task.get('id')!r}: each button needs an id and a label")
    spec = {k: v for k, v in task.items() if k != "buttons"}
    spec.update({k: v for k, v in button.items() if k != "args"})
    if "exec" in spec and button.get("args"):
        spec["exec"] = [*spec["exec"], *button["args"]]
    return spec


def _validate_tasks(tasks) -> None:
    """Reject a malformed task. Fail loud at startup; a live reload catches this and
    keeps the last-good config instead (see refresh_tasks)."""
    for t in tasks:
        if "exec" in t and not (isinstance(t["exec"], list) and t["exec"]
                                and all(isinstance(a, str) for a in t["exec"])):
            raise ValueError(f"task {t.get('id')!r}: exec must be a non-empty list of strings")
        if "exec" in t and "command" in t:
            raise ValueError(f"task {t.get('id')!r}: a Dispatch has exec, a Task has command — not both")


def _load_tasks() -> tuple[list, dict, dict]:
    """Import tasks.py from disk and flatten its button groups. Returns
    (TASKS, TASKS_BY_ID, TASK_LABELS); an absent tasks.py is the generic launcher,
    not an error. Validates, so the caller can fail loud (startup) or catch it
    (live reload)."""
    try:
        import tasks
        importlib.reload(tasks)   # pick up any edits made since the last import
        raw = list(tasks.TASKS)
    except ImportError:
        raw = []  # no private task config -> generic launcher only
    by_id = {b["id"]: _resolve(t, b) for t in raw for b in _buttons(t)}
    _validate_tasks(list(by_id.values()))
    labels = {bid: spec["label"] for bid, spec in by_id.items()}
    return raw, by_id, labels


def _tasks_mtime():
    try:
        return _TASKS_PATH.stat().st_mtime
    except OSError:
        return None


# Fail loud at startup: booting on a broken tasks.py is a misconfiguration, not a
# transient mid-edit.
TASKS, TASKS_BY_ID, TASK_LABELS = _load_tasks()
_TASKS_MTIME = _tasks_mtime()


def refresh_tasks() -> None:
    """Reload tasks.py when it has changed on disk, so an edit goes live without a
    launcher restart. mtime-gated: an unchanged file costs one stat per request, not
    a re-exec. A reload that fails to import or validate keeps the last-good config
    and logs to stderr — a half-typed save degrades to the previous buttons rather
    than breaking a tap. Tests never write tasks.py, so its mtime is stable and this
    never overwrites a directly-injected TASKS_BY_ID."""
    global TASKS, TASKS_BY_ID, TASK_LABELS, _TASKS_MTIME
    mtime = _tasks_mtime()
    if mtime == _TASKS_MTIME:
        return
    with _TASKS_LOCK:
        if mtime == _TASKS_MTIME:   # another thread already reloaded
            return
        try:
            TASKS, TASKS_BY_ID, TASK_LABELS = _load_tasks()
        except Exception as exc:  # noqa: BLE001 - a bad edit must not break a tap
            print(f"tasks.py reload failed, keeping previous config: {exc}", file=sys.stderr)
        _TASKS_MTIME = mtime   # either way, don't re-attempt the same file each request


def resolve_dir(dir_param: str | None) -> str:
    if not dir_param:
        return DEFAULT_DIR
    candidate = os.path.realpath(os.path.join(PROJECTS_ROOT, dir_param))
    projects_real = os.path.realpath(PROJECTS_ROOT)
    if not candidate.startswith(projects_real + os.sep) or not os.path.isdir(candidate):
        raise ValueError(f"dir must be an existing subdir of {PROJECTS_ROOT}")
    return candidate


def _launch_cmd(workdir: str, prompt: str | None = None) -> str:
    """The `cd <workdir> && cl [prompt] [--remote-control]` shell line.

    Prompt goes BEFORE --remote-control: that flag takes an optional [name]
    arg, so a trailing prompt would be swallowed as the session name (the
    session then opens idle instead of running the prompt).
    """
    run = COMMAND
    if prompt:
        run += " " + shell_quote(prompt)
    if REMOTE:
        run += " --remote-control"
    return f"cd {shell_quote(workdir)} && {run}"


def _resume_cmd(workdir: str, session_id: str) -> str:
    """The `cd <workdir> && cl --resume <id> [--remote-control]` shell line.

    The id goes BEFORE --remote-control for the same reason a prompt does:
    that flag takes an optional [name] arg, so a trailing token would be
    swallowed as the session name.
    """
    run = COMMAND + " --resume " + shell_quote(session_id)
    if REMOTE:
        run += " --remote-control"
    return f"cd {shell_quote(workdir)} && {run}"


def _attach_cmd(window_id: str) -> str:
    """The copy-to-clipboard `tmux … new-session … select-window` line that opens
    a live Run's own window in a local terminal (ADR 0011). '' when there is no
    window, so the Board hides the button.

    A *grouped* session (`new-session -t`), never a plain `attach`: clients on the
    one shared session share the active window, so a second terminal would yank
    the first onto its Run. A grouped view keeps its own active window, and
    `destroy-unattached on` deletes it the instant you detach, so `tmux ls` never
    accumulates. `select-window` is required — a bare attach lands on whatever
    window is *active*, not this Run. Only server-owned config is interpolated
    (the socket/session name and a tmux window id), never user input, so the line
    carries no injection surface and needs no token gate — parity with `bridge`.
    """
    if not window_id:
        return ""
    return (f"tmux -L {TMUX_SOCKET} new-session -t {TMUX_SOCKET} "
            f"\\; set destroy-unattached on \\; select-window -t {window_id}")


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one `tmux -L <socket> …` command, list-form argv, never a shell."""
    return subprocess.run(
        ["tmux", "-L", TMUX_SOCKET, *args],
        capture_output=True, text=True, check=check,
    )


def _ensure_tmux() -> None:
    """Idempotently ensure the detached claude-launcher server+session exist.

    The tmux analogue of iTerm's `activate` + create-window-if-none. When the
    session is absent we create it, then pin the render geometry: `default-size
    120x40` + global `window-size latest` so every later `new-window` is safe.
    Width is only ever pinned per-window (`window-size manual` on each Run's own
    window) — setting it globally crashes the server on the next `new-window`
    (observed on 3.6a; ADR 0010).
    """
    if _tmux("has-session", "-t", TMUX_SOCKET, check=False).returncode == 0:
        return
    _tmux("new-session", "-d", "-s", TMUX_SOCKET, "-x", "120", "-y", "40")
    _tmux("set", "-g", "default-size", "120x40")
    _tmux("set", "-g", "window-size", "latest")


def launch_run(workdir: str, prompt: str | None = None, task_id: str | None = None,
               resume_id: str | None = None) -> str:
    """Open a tmux window running the launch command in workdir; return its Run id.

    Named tasks pass their slash-command as ``prompt`` and their id as
    ``task_id``; the id is stamped on the pane as @cl_task so the live list
    can label it. Resume passes ``resume_id`` (a Session's sessionId) to spawn
    ``cl --resume``. Generic launches pass none of them.

    We mint the Run id ourselves — a lowercase UUID — and stamp it on the pane
    as @cl_run_id (tmux's own `%N` pane ids are reused across a server restart,
    so a stale phone id could drive an unrelated Session; ADR 0010). The client
    needs the returned id to paint an optimistic row: a Run is not visible to
    `list_runs` until `claude` shows up in `ps` (1-3s later), so without this
    correlation key a launch looks like it did nothing.

    The window is created **bare** and the launch line is *typed* with
    `send-keys -l`, never passed as a `new-window` argument: `cl` is a zsh
    function, and `new-window '…'` runs via `/bin/sh -c`, which never sourced
    `.zshrc` and would fail with `cl: not found` (ADR 0010). Submit is a
    separate Enter keystroke, distinct from the literal text.
    """
    _ensure_tmux()
    run_id = str(uuid.uuid4())
    cmd = _resume_cmd(workdir, resume_id) if resume_id else _launch_cmd(workdir, prompt)
    pane = _tmux("new-window", "-d", "-t", TMUX_SOCKET, "-P", "-F", "#{pane_id}").stdout.strip()
    # Pin THIS window's width so a narrow client that merely attaches to look
    # cannot reflow the Run and corrupt the parse frame. Addressing the pane
    # resolves to its window for `set -w`.
    _tmux("set", "-w", "-t", pane, "window-size", "manual")
    _tmux("set", "-p", "-t", pane, "@cl_run_id", run_id)
    if task_id:
        _tmux("set", "-p", "-t", pane, "@cl_task", task_id)
    _tmux("send-keys", "-t", pane, "-l", cmd)
    _tmux("send-keys", "-t", pane, "Enter")
    return run_id


def dispatch(workdir: str, argv: list[str], seed: str = "", log: str | None = None) -> None:
    """Run a preset command detached, appending `seed` as one argv element.

    A **Dispatch** is not a **Run**: no `claude`, no Session, no tmux window —
    nothing to observe or close (ADR 0004). It exists for fire-and-forget agents
    that take one input and leave their own trace elsewhere.

    `argv` is a list, so it is exec'd directly with no shell. That makes the seed
    inert by construction: it cannot be word-split, globbed, or interpolated,
    which matters far more here than for `cl`, where the seed is a prompt rather
    than the command line.

    `start_new_session` detaches the child from the launcher's process group, so
    it survives a launcher restart and never takes a Ctrl-C meant for the server.
    We never `wait()`; `subprocess` reaps exited children on its next Popen, which
    the 4-second run poll guarantees.
    """
    cmd = [*argv, seed] if seed else list(argv)
    sink = subprocess.DEVNULL
    handle = None
    if log:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        handle = open(log, "a", buffering=1)  # noqa: SIM115 - closed below, after the child dups it
        sink = handle
    try:
        subprocess.Popen(
            cmd, cwd=workdir, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
    finally:
        if handle:
            handle.close()


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _display_path(p: str) -> str:
    home = os.path.expanduser("~")
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_log(s: str) -> str:
    return _CONTROL_CHAR_RE.sub("?", s).replace("\n", "?").replace("\r", "?")


# --- live Run discovery (tmux windows running `claude`) -----------------------

# Both a Run id (our own UUID, stamped as the pane option @cl_run_id) and a
# Session id (Claude's sessionId) are 36-char UUIDs. This checks *shape only*
# and belongs to neither: the two are distinguished by which field they arrive
# in, never by their format.
_UUID_RE = re.compile(r"^[0-9A-Fa-f-]{36}$")

# The only statuses Claude Code writes. Whitelisted because `status` lands in
# a `st-<status>` CSS class on the client — it becomes structure, not text, so
# it is the one field `textContent` cannot make safe.
_STATUSES = ("busy", "waiting", "idle")

# The Remote Control bridge id (Claude Code's `bridgeSessionId`). It becomes a
# `https://claude.ai/code/<id>` deep link on the client — a URL, not plain text
# — so it is whitelisted here the way `status` is, before it can reach an href.
_BRIDGE_RE = re.compile(r"^session_[A-Za-z0-9]+$")

# One line per pane, US-separated (0x1f, unusable in any field so splitting is
# unambiguous): @cl_run_id <US> pane_tty <US> pane_title <US> @cl_task <US>
# window_id. An unset pane option renders empty, so a pane the Launcher didn't
# create comes back with a blank @cl_run_id and is dropped in `_parse_tmux_panes`.
# window_id (`@N`) is the Attach target — the window a copied `tmux … attach`
# line selects (ADR 0011); appended last so the existing four positions are fixed.
_PANE_FMT = "#{@cl_run_id}\x1f#{pane_tty}\x1f#{pane_title}\x1f#{@cl_task}\x1f#{window_id}"


def _list_panes_raw() -> str:
    """One `tmux list-panes -a -F` over every pane in the server, US-separated."""
    return _tmux("list-panes", "-a", "-F", _PANE_FMT).stdout


def _pane_for_run(run_id: str) -> str:
    """Resolve a Run UUID to its tmux pane id (`%N`) via one list-panes walk, or ''."""
    try:
        out = _tmux("list-panes", "-a", "-F", "#{@cl_run_id}\x1f#{pane_id}").stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    for line in out.split("\n"):
        rid, _, pane = line.partition("\x1f")
        if rid.strip() == run_id:
            return pane.strip()
    return ""


def _parse_tmux_panes(out: str) -> list[tuple[str, str, str, str, str]]:
    """(run_id, tty_basename, name, cl_task, window_id) for each `list-panes` line.

    Rows whose @cl_run_id is empty — a pane not created by the Launcher — are
    dropped, so `list_runs` only ever sees Runs it owns.
    """
    rows = []
    for line in out.split("\n"):
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        rid, tty, name, tag, window = parts
        if not rid.strip():
            continue
        rows.append((rid.strip(), os.path.basename(tty.strip()),
                     name.strip(), tag.strip(), window.strip()))
    return rows


def _parse_claude_ttys(out: str) -> dict[str, int]:
    """tty_basename -> pid for processes whose command is `claude`."""
    result: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, tty, command = parts
        if not tty.startswith("ttys"):
            continue
        if command != "claude" and not command.startswith("claude "):
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        result.setdefault(tty, pid)
    return result


_RUNS_DIR = os.path.expanduser("~/.claude/sessions")


def _run_meta(base: str = _RUNS_DIR) -> dict[int, dict]:
    """pid -> {cwd, status, remote, sessionId, updatedAt}.

    Claude Code writes one of these per live process, in a directory it calls
    `sessions/`. They describe Runs; the `sessionId` inside points at the
    Session the Run is executing.
    """
    meta: dict[int, dict] = {}
    try:
        names = os.listdir(base)
    except OSError:
        return meta
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, fn)) as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        pid = j.get("pid")
        if not isinstance(pid, int):
            continue
        status = j.get("status", "")
        updated = j.get("updatedAt")
        bridge = j.get("bridgeSessionId")
        meta[pid] = {
            "cwd": j.get("cwd", ""),
            "status": status if status in _STATUSES else "",
            "remote": bool(bridge),
            # The validated bridge id, or "" — the client turns a non-empty one
            # into a claude.ai/code deep link, so it must be a clean URL segment.
            "bridge": bridge if isinstance(bridge, str) and _BRIDGE_RE.match(bridge) else "",
            "sessionId": j.get("sessionId", ""),
            "updatedAt": updated if isinstance(updated, (int, float)) else None,
        }
    return meta


_PROJECTS_STATE = os.path.expanduser("~/.claude/projects")


def _transcript_path(session_id: str, base: str = _PROJECTS_STATE) -> str:
    if not _UUID_RE.match(session_id):
        return ""
    matches = glob.glob(os.path.join(base, "*", session_id + ".jsonl"))
    return matches[0] if matches else ""


def _msg_text(line: str, roles: tuple = ("user", "assistant")) -> str:
    """Human-readable text from a transcript line, or '' (tool-results, meta)."""
    try:
        o = json.loads(line)
    except ValueError:
        return ""
    if o.get("type") not in roles:
        return ""
    content = (o.get("message") or {}).get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text = b.get("text", "")
                break
    text = text.strip()
    if not text or text[0] == "<" or text.startswith("Caveat:"):
        return ""
    return text.replace("\n", " ")


def _first_user_msg(session_id: str, base: str = _PROJECTS_STATE) -> str:
    """Opening user prompt — title fallback when the pane title is generic."""
    path = _transcript_path(session_id, base)
    if not path:
        return ""
    try:
        with open(path) as fh:
            for line in fh:
                t = _msg_text(line, roles=("user",))
                if t:
                    return t[:90]
    except OSError:
        return ""
    return ""


def _last_msg(session_id: str, base: str = _PROJECTS_STATE, window: int = 131072) -> str:
    """Most-recent message text — the 'where is this Run' preview the page shows.

    Tails the last ``window`` bytes so big transcripts stay cheap to read.
    """
    path = _transcript_path(session_id, base)
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > window:
                fh.seek(size - window)
                fh.readline()  # drop the partial first line
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        t = _msg_text(line)
        if t:
            return t[:160]
    return ""


def _session_cwd(session_id: str, base: str = _PROJECTS_STATE) -> str:
    """Directory to resume a Session in, or '' if it has no transcript.

    The transcript's own ``cwd`` (first line carrying one) is authoritative.
    Un-munging the project-dir name ('/' -> '-') is a lossy fallback — a '-'
    in a real dir name is indistinguishable from a path separator — so it is
    only reached when the transcript is too short to record a cwd. Callers
    still verify the result is an existing dir.
    """
    path = _transcript_path(session_id, base)
    if not path:
        return ""
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                cwd = o.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        return ""
    return os.path.basename(os.path.dirname(path)).replace("-", "/")


# --- Recent dirs: the launch input's quick-pick list -----------------------
# The compose bar's dir input takes a subdir under PROJECTS_ROOT; offer the
# folders you have actually run Claude in as a datalist. One
# `~/.claude/projects/<slug>` dir == one cwd, so we read a single (newest)
# transcript per project dir for its authoritative cwd — far cheaper than
# walking every session file, and the folder is already the dedup unit. Slug
# collisions (two real paths munging to the same name) are rare and still
# yield a real cwd, so they are harmless.
_RECENT_DIRS_MAX = 12        # dropdown length that fits a phone
_RECENT_DIRS_SCAN = 200      # project dirs inspected — bounds a huge history


def _cwd_from_transcript(path: str) -> str:
    """The authoritative ``cwd`` recorded in a transcript, or '' if none."""
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                cwd = o.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        return ""
    return ""


def _recent_dirs(base: str = _PROJECTS_STATE) -> list[str]:
    """Recently-used working dirs under PROJECTS_ROOT, newest-first, as the
    root-relative subdir strings the launch input expects."""
    projects_real = os.path.realpath(PROJECTS_ROOT)
    prefix = projects_real + os.sep
    try:
        entries = [e for e in os.scandir(base) if e.is_dir()]
    except OSError:
        return []
    # Cheap first pass: a project dir's own mtime bumps when a session file is
    # added, so it picks which dirs are worth opening; the ceiling caps cost on
    # a huge history. Final order is by newest-transcript mtime, computed below.
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)

    found: list[tuple[float, str]] = []
    seen: set[str] = set()
    for e in entries[:_RECENT_DIRS_SCAN]:
        transcripts = glob.glob(os.path.join(e.path, "*.jsonl"))
        if not transcripts:
            continue
        newest = max(transcripts, key=os.path.getmtime)
        cwd = _cwd_from_transcript(newest)
        if not cwd:
            continue
        real = os.path.realpath(cwd)
        # only genuine subdirs of PROJECTS_ROOT that still exist on disk (a
        # deleted project leaves a stale transcript behind)
        if real == projects_real or not real.startswith(prefix) \
                or not os.path.isdir(real):
            continue
        rel = real[len(prefix):]
        if rel in seen:
            continue
        seen.add(rel)
        found.append((os.path.getmtime(newest), rel))
    found.sort(key=lambda t: t[0], reverse=True)
    return [rel for _, rel in found[:_RECENT_DIRS_MAX]]


# tmux titles a fresh pane with the host's own name (e.g. "Mac-mini.local", or
# its short form "Mac-mini") until `claude` overrides it via a title escape
# sequence. That default is not a Run title, so it is treated as no title —
# whereupon list_runs falls through to the _first_user_msg backstop (ADR 0010).
_HOST_DEFAULT_TITLES = {socket.gethostname(), socket.gethostname().split(".", 1)[0]}


def _clean_title(name: str) -> str:
    """A usable Run title from a tmux pane title, or '' when there is none.

    Strips a leading status glyph and a trailing '(profile)' — iTerm's tab shape,
    harmless to run on a plain tmux title. A hostname-default title (what tmux
    shows before `claude` sets its own) is treated as no title, so the caller
    backstops with _first_user_msg (see list_runs)."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    s = re.sub(r"^[\W_]+", "", s).strip()
    return "" if s in _HOST_DEFAULT_TITLES else s


def _ps_output() -> str:
    return subprocess.run(
        ["ps", "-axo", "pid=,tty=,command="], capture_output=True, text=True, check=True
    ).stdout


def list_runs() -> list[dict]:
    """Live `claude` Runs visible as tmux windows, newest activity first.

    ``updatedAt`` ships raw (epoch ms). Formatting it server-side into "47m"
    would make an idle Run's payload change every minute, defeating the ETag
    and every "nothing changed, skip the re-render" check downstream.
    """
    try:
        panes = _parse_tmux_panes(_list_panes_raw())
        ps_out = _ps_output()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    claude = _parse_claude_ttys(ps_out)
    meta = _run_meta()
    rows = []
    for rid, tty, name, tag, window in panes:
        pid = claude.get(tty)
        if pid is None:
            continue  # pane exists, `claude` not in ps yet -> not a Run
        m = meta.get(pid, {})
        session_id = m.get("sessionId", "")
        # `claude` reaches `ps` ~0.5s before it writes sessions/<pid>.json. In
        # that window the Run has no Session, no cwd and no updatedAt, and its
        # pane is still titled by the shell ("login"). Report it as *starting*
        # rather than as a half-empty Run that sorts to the bottom of the list.
        starting = not session_id
        if tag:
            title = TASK_LABELS.get(tag, tag)
        else:
            title = _clean_title(name)
            if not title or title == "Claude Code":
                title = _first_user_msg(session_id) or title or "claude"
        rows.append({
            "id": rid,
            "sessionId": session_id,
            "title": title,
            "dir": _display_path(m.get("cwd", "")) if m.get("cwd") else "",
            "status": m.get("status", ""),
            "remote": m.get("remote", False),
            "bridge": m.get("bridge", ""),
            "attach": _attach_cmd(window),
            "updatedAt": m.get("updatedAt"),
            "snippet": _last_msg(session_id),
            "starting": starting,
        })
    # Starting Runs first — they are the newest thing that happened, and they
    # have no updatedAt to sort by. Then most-recently-active, as the Claude
    # app orders them.
    rows.sort(key=lambda r: (not r["starting"], -(r["updatedAt"] or 0)))
    return rows


# Memoize the runs list briefly so the burst-poll after a launch, the periodic
# poll, and a second open tab all collapse into one walk. The memoize predates
# tmux — it hid a ~140ms AppleScript walk (84ms iTerm + 53ms `ps`); the tmux
# `list-panes` walk is ~2-5ms, so it is now near-redundant and kept only for
# parity (removing it is a follow-up; ADR 0010). Mutations invalidate it — a
# closed Run must vanish on the very next poll, not up to a TTL later.
_RUNS_TTL = 0.75
_runs_lock = threading.Lock()
_runs_cache: tuple[float, list[dict]] = (0.0, [])


def cached_runs() -> list[dict]:
    global _runs_cache
    with _runs_lock:
        stamp, rows = _runs_cache
        if time.monotonic() - stamp < _RUNS_TTL:
            return rows
        rows = list_runs()
        _runs_cache = (time.monotonic(), rows)
        return rows


def invalidate_runs() -> None:
    global _runs_cache
    with _runs_lock:
        _runs_cache = (0.0, [])


def _live_session_ids() -> set[str]:
    """sessionIds of Sessions with a live Run — the resume live-guard set.

    Resuming a Session that already has a live Run would put two Runs on one
    transcript, so /api/resume refuses any id in here.
    """
    return {sid for r in cached_runs() if (sid := r.get("sessionId"))}


def close_run(run_id: str) -> bool:
    """Close the tmux window for this Run id, but only if it's a live claude one."""
    if not _UUID_RE.match(run_id):
        return False
    if run_id not in {r["id"] for r in cached_runs()}:
        return False
    pane = _pane_for_run(run_id)
    if not pane:
        return False
    try:
        _tmux("kill-window", "-t", pane)   # a pane target resolves to its window
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    invalidate_runs()
    return True


# --- Respond: inject input into a live Run's pane --------------------------
# The Launcher's own driving channel — `tmux send-keys` into the Run's pane —
# independent of the Remote Control bridge. The Run UUID resolves to its pane
# via `_pane_for_run` (the same walk `close` uses). Keys come from a fixed map
# of tmux key names, so a client can drive a selector (a permission menu, an
# AskUserQuestion) without ever supplying a raw escape sequence.
_RESPOND_KEYS = {
    "enter": "Enter",
    "esc": "Escape",
    "up": "Up",
    "down": "Down",
    "right": "Right",
    "left": "Left",
    "tab": "Tab",
    "space": "Space",
}


def respond_run(run_id: str, text: str = "", keys: list | None = None) -> bool:
    """Inject a reply and/or keys into a live Run's pane. Acts only on a
    currently-live claude Run (mirrors close_run); a stale or bogus id no-ops.

    Text is sent literally with `send-keys -l` (which, unlike iTerm's `write
    text`, does NOT bracket-paste), then submitted by a *separate* `send-keys
    Enter` — the submit stays a distinct keystroke (ADR 0010 landmine #3), so a
    newline can never ride inside the literal text and stick unsent in the box.
    Selector keys are bare tmux key names from the fixed map (no `-l`), so the
    client can never inject a raw escape sequence.
    """
    keys = keys or []
    if not _UUID_RE.match(run_id) or run_id not in {r["id"] for r in cached_runs()}:
        return False
    pane = _pane_for_run(run_id)
    if not pane:
        return False

    def send(*args: str) -> bool:
        try:
            return _tmux("send-keys", "-t", pane, *args).returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    ok = False
    if text:
        if send("-l", text):        # literal text, no bracketed paste
            send("Enter")           # submit is a SEPARATE keystroke (landmine #3)
            ok = True
    for k in keys:
        if k in _RESPOND_KEYS and send(_RESPOND_KEYS[k]):
            ok = True
    if ok:
        invalidate_runs()   # the Run is now busy; reflect it on the next poll
    return ok


def clear_input(run_id: str) -> bool:
    """Empty a live Run's input box by deleting exactly what is typed in it.

    Reads the current box content (a half-composed message, or a prior stuck
    send) and sends that many `send-keys BSpace` backspaces plus a small margin.
    Deterministic — it does not rely on any clear-line keybinding working — and
    safe: a backspace at the start of the input is a no-op, so an over-count can
    never reach the prompt or the scrollback above it.
    """
    if not _UUID_RE.match(run_id) or run_id not in {r["id"] for r in cached_runs()}:
        return False
    pane = _pane_for_run(run_id)
    if not pane:
        return False
    content = _pane_input(_pane_contents(run_id))
    n = min(len(content) + 16, MAX_RESPOND_CHARS + 32)
    try:
        _tmux("send-keys", "-t", pane, *(["BSpace"] * n))   # n backspaces
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    invalidate_runs()
    return True


# --- page ---------------------------------------------------------------------

CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; "
       "style-src 'unsafe-inline'; base-uri 'none'")


def _tasks_data() -> list:
    """Task definitions for the Board's intake buttons, as data rather than
    markup: a hot-served static page can no longer inline them server-side
    (ADR 0008), so the client fetches this and builds the buttons itself. One
    entry per tasks.py task — its shared seed box (input kind + placeholder)
    and its buttons (id + label). Labels and placeholders reach the DOM as
    textContent on the client, so the old per-field html.escape is now a
    structural guarantee rather than a habit."""
    refresh_tasks()   # pick up any tasks.py edit since the last fetch
    out = []
    for t in TASKS:
        kind = t.get("input", "none")
        out.append({
            "input": kind if kind in ("text", "textarea") else "none",
            "placeholder": t.get("placeholder") or f'{t.get("label") or t["id"]}…',
            "buttons": [{"id": b["id"], "label": b["label"]} for b in _buttons(t)],
        })
    return out


def _tasks_payload() -> tuple[bytes, str]:
    """Serialized task defs (plus the compose bar's projects-root label) and
    their ETag. They change only when tasks.py is edited, so the ETag stays
    stable across the client's periodic refetch."""
    body = json.dumps({"tasks": _tasks_data(), "root": _display_path(PROJECTS_ROOT)},
                      separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


# --- Board: the rotation read-path -----------------------------------------
# The Board classifies live Runs into lanes, orders them as a curated
# round-robin — (tier, per-session priority, waiting-since) — and enriches the
# focus Run with its run-up context. Blocked always outranks your-move; within
# a tier high priority floats up; Blocked breaks ties oldest-first (urgency),
# your-move freshest-first (staleness ≈ done). Idle past the Dormant horizon
# parks out of rotation. See docs/adr/0005 (hot-served UI) and 0006 (context).
_BOARD_DORMANT_MS = 36 * 3600 * 1000     # idle longer than this parks as Dormant
_TAIL_WINDOW = 262144

# Per-session state you set from the Board — priority reorders the rotation and
# stretches the Dormant clock; snooze hides a Session until its wake time. Both
# are keyed by sessionId and persisted to disk so they survive a restart. These
# are benign (they reorder a view, they cannot drive a Run), so they ride the
# same-origin + JSON CSRF defense but are NOT token-gated like Respond.
_PRIORITY: dict[str, int] = {}           # sessionId -> 0 high / 1 normal / 2 low
_SNOOZE: dict[str, float] = {}           # sessionId -> wake epoch ms
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".board-state.json")
_STATE_LOCK = threading.Lock()


def _pri(session_id: str) -> int:
    return _PRIORITY.get(session_id, 1)


def _load_state() -> None:
    global _PRIORITY, _SNOOZE
    try:
        with open(_STATE_FILE) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return
    _PRIORITY = {k: int(v) for k, v in (d.get("priority") or {}).items() if int(v) in (0, 1, 2)}
    now = time.time() * 1000
    _SNOOZE = {k: float(v) for k, v in (d.get("snooze") or {}).items() if float(v) > now}


def _save_state() -> None:
    with _STATE_LOCK:
        try:
            tmp = _STATE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"priority": _PRIORITY, "snooze": _SNOOZE}, fh)
            os.replace(tmp, _STATE_FILE)   # atomic, never a half-written file
        except OSError as exc:
            sys.stderr.write(f"board state save failed: {exc}\n")


def set_priority(session_id: str, level: int) -> bool:
    if not _UUID_RE.match(session_id) or level not in (0, 1, 2):
        return False
    if level == 1:
        _PRIORITY.pop(session_id, None)   # normal is the default; don't store it
    else:
        _PRIORITY[session_id] = level
    _save_state()
    return True


def set_snooze(session_id: str, minutes: float) -> bool:
    if not _UUID_RE.match(session_id):
        return False
    if minutes <= 0:
        _SNOOZE.pop(session_id, None)     # 0 (or less) un-snoozes
    else:
        _SNOOZE[session_id] = time.time() * 1000 + minutes * 60000
    _save_state()
    return True


def _tail_rows(session_id: str) -> list:
    """Parsed JSON lines from the tail of a Session's transcript."""
    path = _transcript_path(session_id)
    if not path:
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > _TAIL_WINDOW:
                fh.seek(size - _TAIL_WINDOW)
                fh.readline()
            raw = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in raw:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _blocks(o: dict) -> list:
    c = (o.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def _last_assistant(rows: list):
    for o in reversed(rows):
        if o.get("type") == "assistant" and _blocks(o):
            return o
    return None


def _pending_tool_use(rows: list):
    """A tool_use in the last assistant turn with no matching tool_result yet."""
    done = set()
    for o in rows:
        if o.get("type") == "user":
            for b in _blocks(o):
                if b.get("type") == "tool_result" and b.get("tool_use_id"):
                    done.add(b["tool_use_id"])
    la = _last_assistant(rows)
    if not la:
        return None
    for b in _blocks(la):
        if b.get("type") == "tool_use" and b.get("id") not in done:
            return b
    return None


def _ai_title(session_id: str) -> str:
    """Claude Code's own one-line session summary, else the opening prompt."""
    path = _transcript_path(session_id)
    title = ""
    if path:
        try:
            with open(path) as fh:
                for line in fh:
                    if '"aiTitle"' in line:
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        if o.get("type") == "ai-title" and o.get("aiTitle"):
                            title = o["aiTitle"]
        except OSError:
            pass
    return title or _first_user_msg(session_id)


# Escape-first markdown. The focus context is transcript prose we do not own,
# so every text run is html.escaped BEFORE any markup is emitted and only a
# fixed tag set is produced. The client innerHTMLs the result — a bounded,
# deliberate exception to the no-innerHTML rule of ADR 0003, made safe here by
# escape-first. See ADR 0006.
def _md_inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _md_to_html(text: str) -> str:
    lines = text.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lvl = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{lvl}>{_md_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and "|" in lines[i]:
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{_md_inline(c)}</th>" for c in head)
            tr = "".join("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>")
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_md_inline(it)}</li>" for it in items) + "</ul>")
            continue
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\s*([-*]|\d+\.)\s)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_md_inline(' '.join(buf))}</p>")
    return "\n".join(out)


# Truncation budgets keep the focus card readable on a phone. A command / file /
# plan under approval, and the run-up prose above it, are all transcript text we
# do not own; an over-long one is clipped with an ellipsis.
_ASK_MAX = 600     # the command / file / plan being approved
_CTX_MAX = 1200    # the recent conversation context shown above it


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _approval_detail(tu: dict) -> str:
    """What a flushed approval tool_use is asking you to approve, as plain text:
    the Bash command, the Edit/Write target (+ a concise change summary when the
    diff is present), or the ExitPlanMode plan. Per ADR 0009 an approval always
    leaves a flushed pending tool_use, so this reads structured transcript data,
    not the rendered pane. The result is carried in the card's plain-text `ask`
    field (rendered with textContent, never innerHTML — ADR 0003/0006), so this
    untrusted `input` text is inert and cannot inject."""
    name = tu.get("name") or ""
    inp = tu.get("input") if isinstance(tu.get("input"), dict) else {}
    if name == "Bash":
        return _clip(str(inp.get("command") or ""), _ASK_MAX)
    if name in ("Edit", "MultiEdit"):
        lead = f"Edit {str(inp.get('file_path') or '')}".strip()
        summary = str(inp.get("new_string") or "").strip().splitlines()
        if summary:                       # concise change summary, when available
            lead = f"{lead} — {summary[0].strip()}"
        return _clip(lead, _ASK_MAX)
    if name == "Write":
        return _clip(f"Write {str(inp.get('file_path') or '')}".strip(), _ASK_MAX)
    if name == "NotebookEdit":
        return _clip(f"Edit {str(inp.get('notebook_path') or '')}".strip(), _ASK_MAX)
    if name == "ExitPlanMode":
        return _clip(str(inp.get("plan") or ""), _ASK_MAX)
    return name        # any other tool put up for approval — name it, never blank


def _full_context(session_id: str) -> tuple:
    """(context_text, ask, options) from the Session's last assistant turn.

    For an approval the `ask` describes the flushed pending tool_use (the Bash
    command, the Edit/Write target, or the ExitPlanMode plan) — what you're being
    asked to approve — and the run-up prose above it is clipped. See ADR 0009 and
    `_approval_detail` for why this is structured data, not a pane scrape."""
    rows = _tail_rows(session_id)
    la = _last_assistant(rows)
    if not la:
        return "", "", []
    text = "\n".join(b.get("text", "") for b in _blocks(la) if b.get("type") == "text").strip()
    qs = re.findall(r"[^\n?]*\?", text)
    ask = qs[-1].strip()[-200:] if qs else ""
    options = []
    tu = _pending_tool_use(rows)
    if tu and tu.get("name") == "AskUserQuestion":
        questions = (tu.get("input") or {}).get("questions", [])
        for q in questions:
            for o in q.get("options", []):
                if o.get("label"):
                    options.append(o["label"])
        # The structured prompt beats the prose `?` regex: the real question
        # lives in the tool input, not necessarily in the assistant's text.
        if questions and questions[0].get("question"):
            ask = questions[0]["question"].strip()[:200]
    elif tu:
        # An approval (any non-AskUserQuestion pending tool_use — Bash / Edit /
        # Write / ExitPlanMode …): surface the concrete blocker as the ask, and
        # clip the run-up context that renders above it.
        ask = _approval_detail(tu) or ask
        text = _clip(text, _CTX_MAX)
    return text, ask, options


def _lane_of(run: dict) -> str:
    """Coarse lane from status; 'waiting' is refined against the transcript."""
    if run.get("starting") or run.get("status") == "busy":
        return "working"
    if run.get("status") == "waiting":
        tu = _pending_tool_use(_tail_rows(run.get("sessionId", "")))
        # A permission / plan approval always leaves a flushed pending tool_use
        # (the Bash / Edit / ExitPlanMode the human must approve). A pending
        # AskUserQuestion often never reaches the transcript, so "waiting with
        # nothing pending" reads as a question, not an approval (ADR 0009).
        return "approval" if (tu and tu.get("name") != "AskUserQuestion") else "question"
    return "yourmove"


# A permission prompt is a live TUI dialog that often never reaches the
# transcript — so for that lane the concrete blocker is read from the *rendered
# pane* instead. This is Observe reading the screen (see CONTEXT.md); it costs
# one capture-pane read, so it runs only for the focus, and only when Blocked.

# A rendered selector line: an optional cursor glyph, an optional "N." / "N)"
# index, then the label. Claude Code marks the current option with a cursor
# glyph; permission menus and the trust prompt are numbered.
_OPT_RE = re.compile(r"^\s*([❯›>])?\s*\d+[.)]\s+(\S.*?)\s*$")
# Box-drawing glyphs (U+2500–U+257F). The AskUserQuestion widget paints the
# highlighted option's description in a side panel on the SAME rows as the
# option labels; splitting a label on the first box glyph drops that bleed.
_BOX_RE = re.compile("[─-╿]")


def _pane_contents(run_id: str) -> str:
    """The Run's rendered pane as a single visible frame, or '' if unreadable.

    Resolves the Run UUID to its tmux pane (`_pane_for_run`) and reads it with
    `capture-pane -p`. Unlike iTerm's `contents of session`, this returns ONLY
    the current visible frame — no scrollback — so ADR 0009's "last contiguous
    option run" scrollback guard is now belt-and-suspenders: a single frame has
    exactly one option run, which the guard still picks correctly. Returns ''
    on any failure; callers depend on '' meaning "couldn't read".
    """
    if not _UUID_RE.match(run_id):
        return ""
    pane = _pane_for_run(run_id)
    if not pane:
        return ""
    try:
        return _tmux("capture-pane", "-p", "-t", pane).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _parse_selector(text: str) -> dict:
    """Options + cursor index parsed from a rendered numbered selector, or {}.
    Grounds Respond: option i is reached by stepping the cursor from where it
    actually sits, not by assuming it starts at the top.

    `contents of session` returns the scrollback, so a widget that re-rendered
    can appear several times over. Only the LAST contiguous run of option lines
    is the live frame — earlier ones are stale paints."""
    groups, cur = [], []
    for ln in text.split("\n"):
        m = _OPT_RE.match(ln)
        if m:
            label = _BOX_RE.split(m.group(2), 1)[0].strip()[:80]   # drop any side-panel bleed
            cur.append((bool(m.group(1)), label))
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    live = groups[-1] if groups else []
    options = [lbl for _, lbl in live]
    cursor = next((i for i, (hit, _) in enumerate(live) if hit), 0)
    return {"options": options, "cursor": cursor} if len(options) >= 2 else {}


_RULE_CHARS = set("─—-═")


def _pane_input(text: str) -> str:
    """Whatever is currently typed in the Run's input box, or ''.

    Claude Code frames the input between two horizontal rules, just above the
    `📁 …` status line. Reading it lets Respond refuse to blind-append onto a
    reply already sitting there (a half-typed message, or a prior stuck send)
    instead of silently submitting more than the caller meant.
    """
    lines = text.split("\n")
    rules = [i for i, ln in enumerate(lines) if ln.strip() and set(ln.strip()) <= _RULE_CHARS]
    if len(rules) < 2:
        return ""
    box = lines[rules[-2] + 1:rules[-1]]
    out = []
    for ln in box:
        s = ln.strip()
        if s[:1] in ("❯", "›", ">"):
            s = s[1:].strip()          # drop the prompt glyph on the first line
        out.append(s)
    return "\n".join(out).strip()


def _is_question_widget(text: str) -> bool:
    """True when the pane shows the AskUserQuestion widget (as opposed to a
    permission menu or a plain input box). Its notes affordance — absent from
    every other prompt — is the stable signature."""
    return "add notes" in text


_CHECKBOX = ("☐", "☑", "✔", "✓")


def _pane_question(text: str) -> str:
    """The prompt read off an AskUserQuestion widget: the lines between its
    checkbox header and the first following numbered option (blank lines and
    the box-art notes panel skipped). Used only when the tool_use hasn't flushed
    to the transcript yet, so the structured question in `_full_context` is
    unavailable. Anchored on the LAST header — earlier frames are stale."""
    lines = text.split("\n")
    hdr = next((i for i in range(len(lines) - 1, -1, -1)
                if lines[i].strip()[:1] in _CHECKBOX), None)
    if hdr is None:
        return ""
    q = []
    for ln in lines[hdr + 1:]:
        if _OPT_RE.match(ln):
            break
        s = ln.strip()
        if s and set(s) <= _RULE_CHARS:           # a horizontal rule → end of the header block
            break
        if s:
            q.append(s)
    return " ".join(q)[:200]


def _tmux_server_down() -> bool:
    """True when the claude-launcher tmux server isn't running.

    A dead detached server takes every Run with it, so list_runs returns [] —
    indistinguishable from 'server up, zero Runs'. `has-session` exits non-zero
    only when the server/session is absent (it returns cleanly when up), so the
    Board can surface a distinct 'no tmux server' empty state rather than an
    ordinary empty list (ADR 0010). Never raises: a missing tmux binary — the
    launcher's own substrate gone — also reads as down.
    """
    try:
        return _tmux("has-session", "-t", TMUX_SOCKET, check=False).returncode != 0
    except (FileNotFoundError, OSError):
        return True


def _board(focus_sid: str = "") -> dict:
    now = time.time() * 1000
    items = []
    for r in cached_runs():
        sid = r.get("sessionId", "")
        snoozed = sid and _SNOOZE.get(sid, 0) > now
        lane = "snoozed" if snoozed else _lane_of(r)
        one = r.get("snippet", "")
        if lane in ("question", "approval"):
            _, ask, _ = _full_context(sid)
            one = ask or one
        proj = (r.get("dir") or "").rstrip("/").split("/")[-1]
        items.append({"runId": r.get("id"), "sessionId": sid, "title": proj or r.get("title", ""),
                      "dir": r.get("dir", ""), "status": r.get("status", ""), "bridge": r.get("bridge", ""),
                      "attach": r.get("attach", ""),
                      "updatedAt": r.get("updatedAt"), "lane": lane, "pri": _pri(sid), "one": one})

    blocked = [it for it in items if it["lane"] in ("question", "approval")]
    working = [it for it in items if it["lane"] == "working"]
    snoozed = [it for it in items if it["lane"] == "snoozed"]
    dormant, recent = [], []
    for it in (it for it in items if it["lane"] == "yourmove"):
        old = it["updatedAt"] and now - it["updatedAt"] >= _BOARD_DORMANT_MS
        (dormant if old and it["pri"] != 0 else recent).append(it)   # high priority never dorms

    blocked.sort(key=lambda it: (it["pri"], it["updatedAt"] or 0))        # oldest-first (urgency)
    recent.sort(key=lambda it: (it["pri"], -(it["updatedAt"] or 0)))      # freshest-first
    working.sort(key=lambda it: -(it["updatedAt"] or 0))
    dormant.sort(key=lambda it: -(it["updatedAt"] or 0))
    snoozed.sort(key=lambda it: _SNOOZE.get(it["sessionId"], 0))

    order = blocked + recent
    # Sticky focus: the client can pin any listed Session (a row tap). A pin
    # wins over the rotation head and survives polls until cleared. An unknown
    # or vanished id silently falls back to the rotation head.
    focus = next((it for it in items if it["sessionId"] == focus_sid), None) if focus_sid else None
    pinned = focus is not None
    if focus is None:
        focus = order[0] if order else None
    other = [it for it in order if it is not focus]
    working = [it for it in working if it is not focus]
    dormant = [it for it in dormant if it is not focus]
    snoozed = [it for it in snoozed if it is not focus]
    if focus:
        text, ask, options = _full_context(focus["sessionId"])
        cursor = 0
        pane = _pane_contents(focus["runId"])   # one read: box + any selector/widget
        sel = _parse_selector(pane)             # a numbered menu (permission or question)
        widget = _is_question_widget(pane)      # the AskUserQuestion widget specifically
        lane = focus["lane"]
        if widget:
            lane = "question"                   # the rendered pane outranks _lane_of's guess
            if not ask:
                ask = _pane_question(pane)      # tool_use hasn't flushed — read the prompt off-screen
        if sel:
            # Hybrid (ADR 0009): structured tool_use labels win when flushed; the
            # pane supplies them only when they're absent. The live cursor always
            # comes from the pane — the user may have arrow-keyed on the Mac.
            if not options:
                options = sel["options"]
            if len(sel["options"]) == len(options):
                cursor = sel["cursor"]
        # A menu or widget owns the screen — there is no free-text input box to
        # mistake its body for unsent text (that false ⚠ was the original bug).
        pending = "" if (sel or widget) else _pane_input(pane)
        focus = dict(focus, lane=lane, aiTitle=_ai_title(focus["sessionId"]),
                     contextHtml=_md_to_html(text), ask=ask, options=options,
                     cursor=cursor, pendingInput=pending, pinned=pinned)
    # serverDown distinguishes a dead tmux server (all Runs gone silently) from
    # an ordinary empty board. The web client does not render this yet — plumbing
    # only; the empty-state UI is a documented follow-up (ADR 0010).
    return {"focus": focus, "upnext": other, "watching": working,
            "snoozed": snoozed, "dormant": dormant,
            "counts": {"needYou": len(order), "watching": len(working),
                       "dormant": len(dormant), "snoozed": len(snoozed)},
            "serverDown": _tmux_server_down()}


def _board_payload(focus_sid: str = "") -> tuple[bytes, str]:
    """Board JSON + ETag. No wall-clock in the body — the client formats ages
    from raw `updatedAt`, so an unchanged board yields a stable ETag/304."""
    body = json.dumps(_board(focus_sid), separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_WEB_FILES = {"board.html": "text/html; charset=utf-8",
              "board.js": "text/javascript; charset=utf-8"}


MAX_BODY_BYTES = 4096
_API_POSTS = ("/api/launch", "/api/resume", "/api/close", "/api/respond",
              "/api/clear", "/api/priority", "/api/snooze")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        # no-store everywhere: the client tracks the Run-list ETag itself and
        # revalidates explicitly, so it never needs the browser HTTP cache.
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, code: int, obj: dict, extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _serve_web(self, name: str) -> None:
        """Serve a UI file fresh from disk — edit it, refresh, no relaunch.
        The name set is a fixed whitelist, so there is no path to traverse."""
        ctype = _WEB_FILES.get(name)
        if not ctype:
            self._fail(404, "not found")
            return
        try:
            with open(os.path.join(WEB_DIR, name), "rb") as fh:
                data = fh.read()
        except OSError:
            self._fail(404, f"{name} not found (create web/{name})")
            return
        self._send(200, data, ctype)

    def _fail(self, code: int, message: str) -> None:
        self._json(code, {"ok": False, "message": message})

    def _same_origin_ok(self) -> tuple[bool, str]:
        """Fail closed. Every caller is our own fetch, which always sends Origin
        on a POST; a missing header now means something else is calling."""
        origin = self.headers.get("Origin")
        if not origin:
            return False, "origin missing"
        if origin == "null":
            return False, "origin=null"
        host = (self.headers.get("Host") or "").lower()
        allowed = {f"http://{host}", f"https://{host}"}
        if origin.lower() in allowed:
            return True, ""
        return False, f"origin={origin!r} host={host!r}"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_web("board.html")   # the Board is the only page now (ADR 0008)
            return
        if path == "/board.js":
            self._serve_web("board.js")
            return
        if path == "/api/board":
            focus_sid = (parse_qs(urlparse(self.path).query).get("focus") or [""])[0]
            body, etag = _board_payload(focus_sid if _UUID_RE.match(focus_sid) else "")
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", "application/json; charset=utf-8", {"ETag": etag})
                return
            self._send(200, body, "application/json; charset=utf-8", {"ETag": etag})
            return
        if path == "/api/tasks":
            body, etag = _tasks_payload()
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", "application/json; charset=utf-8", {"ETag": etag})
                return
            self._send(200, body, "application/json; charset=utf-8", {"ETag": etag})
            return
        if path == "/api/dirs":
            # Recent dirs change as you work, so serve fresh (no-store) at point
            # of use — the client fetches this on focusing the launch input.
            self._json(200, {"dirs": _recent_dirs()})
            return
        if path in _API_POSTS:
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._fail(404, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in _API_POSTS:
            self._fail(404, "not found")
            return
        ok, detail = self._same_origin_ok()
        if not ok:
            dbg = " ".join(
                f"{h}={self.headers.get(h)!r}"
                for h in ("Origin", "Host", "Referer", "Sec-Fetch-Site", "Sec-Fetch-Mode", "User-Agent")
                if self.headers.get(h)
            )
            sys.stderr.write(f"403 cross-origin: {sanitize_log(detail)} | {sanitize_log(dbg)}\n")
            self._fail(403, f"cross-origin blocked ({detail})")
            return
        # Requiring JSON makes a cross-origin POST a preflighted request, and the
        # preflight fails because no CORS headers are sent. CSRF is then blocked
        # structurally, not merely by inspecting Origin.
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._fail(415, "expected application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._fail(400, "bad content-length")
            return
        if length > MAX_BODY_BYTES:
            self._fail(413, "body too large")
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
        except ValueError:
            self._fail(400, "bad json")
            return
        if not isinstance(body, dict):
            self._fail(400, "expected a json object")
            return
        if path == "/api/close":
            self._handle_close(body)
        elif path == "/api/resume":
            self._handle_resume(body)
        elif path == "/api/respond":
            self._handle_respond(body)
        elif path == "/api/clear":
            self._handle_clear(body)
        elif path == "/api/priority":
            self._handle_priority(body)
        elif path == "/api/snooze":
            self._handle_snooze(body)
        else:
            self._handle_launch(body)

    @staticmethod
    def _str(body: dict, key: str) -> str:
        v = body.get(key, "")
        return v.strip() if isinstance(v, str) else ""

    def _handle_launch(self, body: dict) -> None:
        task_id = self._str(body, "task")
        if task_id:
            self._launch_task(task_id, body)
            return
        try:
            workdir = resolve_dir(self._str(body, "dir"))
        except ValueError as e:
            self._fail(400, str(e))
            return
        try:
            run_id = launch_run(workdir)
        except subprocess.CalledProcessError as e:
            self._fail(500, f"tmux failed: {e}")
            return
        invalidate_runs()
        self._json(200, {"ok": True, "runId": run_id,
                         "message": f"launched in {_display_path(workdir)}"})

    def _handle_respond(self, body: dict) -> None:
        # Auth gate first: no token configured => Respond is off entirely.
        if not TOKEN:
            self._fail(403, "respond disabled: set CLAUDE_LAUNCHER_TOKEN")
            return
        if not hmac.compare_digest(self._str(body, "token"), TOKEN):
            self._fail(401, "bad token")
            return
        run_id = self._str(body, "runId")
        text = self._str(body, "text")
        keys = body.get("keys") or []
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            self._fail(400, "keys must be a list of strings")
            return
        if len(text) > MAX_RESPOND_CHARS or "\x00" in text:
            self._fail(400, "text too long")
            return
        # Don't blind-append: if the box already holds unsent text (a half-typed
        # message, or a prior stuck send), refuse and hand it back so the caller
        # sees exactly what would be sent. `force` sends anyway (appends).
        if text and not bool(body.get("force")):
            existing = _pane_input(_pane_contents(run_id))
            if existing:
                self._json(409, {"ok": False, "message": "input box already has unsent text",
                                 "existing": existing[:500]})
                return
        if not respond_run(run_id, text, keys):
            self._fail(400, "respond failed: not a live run, or nothing to send")
            return
        self._json(200, {"ok": True})

    def _handle_clear(self, body: dict) -> None:
        # Modifies a live Run's input, so it is token-gated like Respond.
        if not TOKEN:
            self._fail(403, "respond disabled: set CLAUDE_LAUNCHER_TOKEN")
            return
        if not hmac.compare_digest(self._str(body, "token"), TOKEN):
            self._fail(401, "bad token")
            return
        if not clear_input(self._str(body, "runId")):
            self._fail(400, "clear failed: not a live run")
            return
        self._json(200, {"ok": True})

    def _handle_priority(self, body: dict) -> None:
        levels = {"high": 0, "normal": 1, "low": 2}
        level = levels.get(self._str(body, "level"))
        if level is None:
            self._fail(400, "level must be high|normal|low")
            return
        if not set_priority(self._str(body, "sessionId"), level):
            self._fail(400, "bad sessionId")
            return
        self._json(200, {"ok": True})

    def _handle_snooze(self, body: dict) -> None:
        minutes = body.get("minutes", 0)
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not 0 <= minutes <= 525600:
            self._fail(400, "minutes must be a number in [0, 525600]")
            return
        if not set_snooze(self._str(body, "sessionId"), minutes):
            self._fail(400, "bad sessionId")
            return
        self._json(200, {"ok": True})

    def _seed(self, task: dict, body: dict) -> str | None:
        """The seed for a task that takes one, or None when it is unusable."""
        if task.get("input", "none") == "none":
            return ""
        seed = self._str(body, "input")
        if len(seed) > MAX_SEED_CHARS or "\x00" in seed:
            return None
        return seed

    def _launch_task(self, task_id: str, body: dict) -> None:
        refresh_tasks()   # a button added to tasks.py works without a restart
        task = TASKS_BY_ID.get(task_id)
        if not task:
            self._fail(400, "unknown task")
            return
        workdir = os.path.expanduser(task["workdir"])
        if not os.path.isdir(workdir):
            self._fail(400, f"workdir does not exist: {workdir}")
            return
        seed = self._seed(task, body)
        if seed is None:
            self._fail(400, f"seed must be non-null and under {MAX_SEED_CHARS} characters")
            return

        if task.get("exec"):
            self._dispatch_task(task, workdir, seed)
            return

        prompt = f"{task['command']} {seed}" if seed else task["command"]
        try:
            run_id = launch_run(workdir, prompt, task_id=task["id"])
        except subprocess.CalledProcessError as e:
            self._fail(500, f"tmux failed: {e}")
            return
        invalidate_runs()
        self._json(200, {"ok": True, "runId": run_id, "message": f"launched {task['id']}"})

    def _dispatch_task(self, task: dict, workdir: str, seed: str) -> None:
        """A Dispatch starts no Run, so it returns no runId — the client's
        optimistic row is skipped and the toast is all the feedback there is."""
        if task.get("input", "none") != "none" and not seed:
            self._fail(400, f"{task['id']} needs a seed")
            return
        log = task.get("log")
        try:
            dispatch(workdir, task["exec"], seed, os.path.join(workdir, log) if log else None)
        except OSError as e:
            self._fail(500, f"dispatch failed: {e}")
            return
        self._json(200, {"ok": True, "message": f"dispatched {task['id']}"})

    def _handle_resume(self, body: dict) -> None:
        session_id = self._str(body, "sessionId")
        if not _UUID_RE.match(session_id):
            self._fail(400, "invalid session id")
            return
        if not _transcript_path(session_id):
            self._fail(400, "no such session")
            return
        if session_id in _live_session_ids():
            self._fail(400, "already live")
            return
        workdir = _session_cwd(session_id)
        if not workdir or not os.path.isdir(workdir):
            self._fail(400, "session's dir is gone")
            return
        try:
            run_id = launch_run(workdir, resume_id=session_id)
        except subprocess.CalledProcessError as e:
            self._fail(500, f"tmux failed: {e}")
            return
        invalidate_runs()
        self._json(200, {"ok": True, "runId": run_id, "message": f"resumed {session_id}"})

    def _handle_close(self, body: dict) -> None:
        if not close_run(self._str(body, "runId")):
            self._fail(400, "could not close run")
            return
        self._json(200, {"ok": True, "message": "closed"})

    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        sys.stderr.write(f"{self.address_string()} - {sanitize_log(msg)}\n")


def main() -> None:
    if not shutil.which("tmux"):
        sys.exit("claude-launcher: tmux not found on PATH — it is the Run substrate (ADR 0010)")
    _load_state()   # restore per-session priority + snooze from the last run
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"claude-launcher listening on {HOST}:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
