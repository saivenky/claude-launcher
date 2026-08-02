#!/usr/bin/env python3
"""Spawn and manage Claude Code sessions on a Mac from a phone over Tailscale.

Vocabulary (see CONTEXT.md): a **Session** is the durable thread Claude Code
identifies by `sessionId`; a **Run** is one `claude` process executing it. A
**Managed Run** — the only kind the launcher creates and destroys — is
concretely a tmux window (ADR 0010); a **Foreign Run** is one started by hand
in some other terminal, which the launcher sees but cannot reach into
(ADR 0012).

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
import signal
import socket
import subprocess
import sys
import threading
import time
import typing
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


# --- live Run discovery (every `claude`; ours are the tmux windows) -----------

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
    """Resolve a Run UUID to its tmux pane id (`%N`) via one list-panes walk, or ''.

    Only a **Managed Run** can ever resolve: the match is on @cl_run_id, which
    only a pane the Launcher stamped carries. A **Foreign Run** has no pane and
    no id at all (its row's `id` is ''), so every pane-reaching path — close,
    Respond, clear, the pane capture — bottoms out here at '' (ADR 0012).
    """
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

    Deduped by run_id. An Attach (ADR 0011) spins up a *grouped* session that
    shares the base session's windows, so `list-panes -a` emits every pane once
    per session in the group — the same Run's pane twice while anyone is
    attached. The rows are identical (shared window, same tty), so keeping the
    first is safe and keeps a live Run from appearing twice on the Board.
    """
    rows = []
    seen: set[str] = set()
    for line in out.split("\n"):
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        rid, tty, name, tag, window = parts
        rid = rid.strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        rows.append((rid, os.path.basename(tty.strip()),
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


# A skill launch injects its prompt as a first user "message" ("Base directory
# for this skill: …"), and a bare `/slash-command` invocation lands as one too —
# neither is the human's opening ask, so neither should title a Run. The command
# *marker* forms (`<command-name>/foo</command-name>`) and system-reminders start
# with '<' and are already dropped by _msg_text; this covers the two that survive
# it. A message that merely *contains* a slash command ("Let's /ship") is a real
# prompt and is kept.
_SKILL_PREAMBLE = "Base directory for this skill"
_BARE_SLASH_RE = re.compile(r"^/[^\s]+$")


def _is_title_noise(text: str) -> bool:
    """True for a user message that is a skill-injected preamble or a bare
    /slash-command line — not a real prompt, so unfit to title a Session."""
    return text.startswith(_SKILL_PREAMBLE) or bool(_BARE_SLASH_RE.match(text))


def _first_user_msg(session_id: str, base: str = _PROJECTS_STATE) -> str:
    """Opening user prompt — title fallback when the pane title is generic.

    Skips skill preambles and bare /slash-command lines (see _is_title_noise)
    so the title is the first *real* ask, not the plumbing that started the Run.
    """
    path = _transcript_path(session_id, base)
    if not path:
        return ""
    try:
        with open(path) as fh:
            for line in fh:
                t = _msg_text(line, roles=("user",))
                if t and not _is_title_noise(t):
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


# --- Recover: enumerate Resumable Sessions ---------------------------------
# The read side of Recover (ADR 0013): the candidate list the picker renders.
# A Resumable Session has a transcript on disk, a cwd that still exists, and no
# live Run (CONTEXT "Resumable Session"). Unlike _recent_dirs this is
# Session-granularity — one row per *.jsonl, several per dir — and spans every
# dir with NO PROJECTS_ROOT confinement (ADR 0002), so ~/obsidian appears. The
# `…/T/tmp…-vault/` dead-cwd graveyard is hidden entirely, not greyed.
_RECOVERABLE_MAX = 30        # rows served — a phone-sized, newest-first window
_RECOVERABLE_SCAN = 200      # project dirs opened — bounds a huge history


def _recoverable_sessions(base: str = _PROJECTS_STATE,
                          live: "set[str] | None" = None) -> list[dict]:
    """Resumable Sessions, newest-first by transcript mtime — Recover's candidates.

    One row per Session, spanning every dir (no PROJECTS_ROOT filter — ADR 0002).
    Bounded both ways so a huge history stays cheap: only the newest
    _RECOVERABLE_SCAN project dirs are inspected (a dir's mtime bumps when a
    session file lands, mirroring _recent_dirs), their transcripts are ordered by
    their own mtime and opened newest-first only until _RECOVERABLE_MAX rows fill,
    and each open reads just the head for the cwd (_cwd_from_transcript). A
    Session with a live Run is excluded; one whose recorded cwd no longer exists
    is hidden. Recovery-set pre-tick flags are slice 02 — this is the list only.
    """
    if live is None:
        live = _live_session_ids()
    try:
        dirs = [e for e in os.scandir(base) if e.is_dir()]
    except OSError:
        return []
    # Cheap first pass by dir mtime picks which dirs are worth walking; the
    # ceiling caps cost on a huge history (same heuristic as _recent_dirs).
    dirs.sort(key=lambda e: e.stat().st_mtime, reverse=True)

    # A stat-only pass gathers every candidate transcript across the scanned
    # dirs; the file itself is opened only when we reach it in the ranked loop.
    candidates: list[tuple[float, str, str]] = []   # (mtime, path, sessionId)
    for e in dirs[:_RECOVERABLE_SCAN]:
        for path in glob.glob(os.path.join(e.path, "*.jsonl")):
            session_id = os.path.splitext(os.path.basename(path))[0]
            # A non-UUID name could never be resumed, and a live Session is not
            # Resumable — both drop before we pay to read the file.
            if not _UUID_RE.match(session_id) or session_id in live:
                continue
            try:
                candidates.append((os.path.getmtime(path), path, session_id))
            except OSError:
                continue
    candidates.sort(key=lambda c: c[0], reverse=True)

    rows: list[dict] = []
    for mtime, path, session_id in candidates:
        cwd = _cwd_from_transcript(path)
        if not cwd or not os.path.isdir(cwd):
            continue   # no cwd recorded, or a dead-cwd Session — hidden entirely
        rows.append({
            "sessionId": session_id,
            "dir": _display_path(cwd),          # ~ for home, as the board renders
            "title": _first_user_msg(session_id, base) or "claude",
            "mtime": int(mtime),                # epoch seconds; client renders relative
        })
        if len(rows) >= _RECOVERABLE_MAX:
            break
    return rows


# --- Recover: the recovery set (slice 02, ADR 0013) ------------------------
# The Launcher's pre-tick guess at what was live at the last restart: a recency
# cluster over the candidate list's transcript mtimes, recomputed fresh each
# open and never persisted (that is the whole point of ADR 0013). It ONLY
# pre-ticks checkboxes — it resumes nothing; the human edits the ticks first.
# Anchoring on the newest *candidate* (not the newest Session) self-heals: a
# resumed member goes live, leaves the candidate set, and the anchor slides to
# the next still-dead one, so the rest of the batch keeps pre-ticking.
_RECOVERY_GAP = 15 * 60      # G: max gap between adjacent members, seconds
_RECOVERY_SPAN = 90 * 60     # S: max total span anchor->oldest, seconds
_RECOVERY_MAX = 12           # N: pre-tick cap that fits a phone (== _RECENT_DIRS_MAX)


def _recovery_set_size(mtimes: list[int]) -> int:
    """How many of the newest candidates form the recovery set.

    `mtimes` are the candidate transcript mtimes in the served order — newest
    first (as _recoverable_sessions returns them). The set is always a prefix
    of that list (the anchor is the top row; the chain only reaches older), so
    a single count says which rows pre-tick: rows[:k].

    The recency cluster (ADR 0013): anchor on the newest candidate, then chain
    newest->older while each successive gap to the last member added stays
    within G, halting the moment the total span (anchor - oldest) would exceed
    S, and never returning more than N. Empty list -> 0; a lone candidate -> 1
    (it is its own anchor, a cluster of one). Both G and S are inclusive at the
    boundary (gap == G chains, span == S stays); only strictly exceeding stops.
    """
    if not mtimes:
        return 0
    anchor = mtimes[0]
    k = 1                                          # the anchor always pre-ticks
    for prev, cur in zip(mtimes, mtimes[1:]):
        if k >= _RECOVERY_MAX:                     # cap (N) — phone-fit backstop
            break
        if prev - cur > _RECOVERY_GAP:             # gap (G) to the last member added
            break
        if anchor - cur > _RECOVERY_SPAN:          # span leash (S) anchor->oldest
            break
        k += 1
    return k


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


def _foreign_rows(claude: dict[str, int], pane_ttys: set[str],
                  meta: dict[int, dict]) -> list[dict]:
    """Rows for every live `claude` that is not one of our panes — the **Foreign
    Runs** (ADR 0012).

    Free by construction: `claude` (from `_ps_output`) and `meta` (from
    `_run_meta`, which already reads sessions/*.json for every live process) are
    both already in hand for the Managed walk, so detection costs no new
    subprocess call. `_parse_claude_ttys` keeps its `ttys*` filter, which is what
    makes a headless `claude -p` — a **Dispatch**, a script, CI — invisible here:
    it has no tty, it is nobody's Run, and it must never become transferable.
    Widening that filter would put CI jobs on the Board.

    A `claude` sitting in a tmux pane we did not stamp is Foreign too (its pane
    is dropped by `_parse_tmux_panes`, so its tty is not in `pane_ttys`). That is
    the definition holding: Managed vs Foreign is decided by *who started the
    Run*, never by which terminal happens to hold it.

    A foreign process whose sessions/<pid>.json has not landed yet (the ~0.5s
    window after `claude` reaches `ps`) is skipped, not reported as *starting*:
    with no **Session** it is neither transferable (nothing to resume) nor
    guardable (no sessionId to refuse), and unlike a Managed Run we did not just
    launch it, so there is no optimistic row to keep honest.
    """
    rows = []
    for tty, pid in claude.items():
        if tty in pane_ttys:
            continue
        m = meta.get(pid) or {}
        session_id = m.get("sessionId", "")
        if not session_id:
            continue
        rows.append({
            # No pane, so no @cl_run_id to key it by and no window to Attach to.
            # Both stay empty deliberately: `_pane_for_run` matches on the id, so
            # an empty one can never resolve to somebody else's pane.
            "id": "",
            "attach": "",
            "foreign": True,
            # The only handle on a Foreign Run — Transfer kills this pid, and it
            # is the one thing here that does not come from a Session file.
            "pid": pid,
            "sessionId": session_id,
            # No pane title to clean, so go straight to the backstop list_runs
            # already uses for a Run whose title is generic.
            "title": _first_user_msg(session_id) or "claude",
            "dir": _display_path(m.get("cwd", "")) if m.get("cwd") else "",
            "status": m.get("status", ""),
            "remote": m.get("remote", False),
            "bridge": m.get("bridge", ""),
            "updatedAt": m.get("updatedAt"),
            "snippet": _last_msg(session_id),
            "starting": False,
        })
    return rows


def list_runs() -> list[dict]:
    """Every live `claude` **Run**, newest activity first — **Managed** (a tmux
    window we stamped) and **Foreign** (one started by hand elsewhere).

    A Foreign row carries `"foreign": True` and an empty `id`/`attach`; see
    `_foreign_rows`. Almost nothing wants both kinds mixed, which is why
    `cached_runs()` — not this — is what the rest of the module calls.

    ``updatedAt`` ships raw (epoch ms). Formatting it server-side into "47m"
    would make an idle Run's payload change every minute, defeating the ETag
    and every "nothing changed, skip the re-render" check downstream.
    """
    try:
        ps_out = _ps_output()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []   # no `ps`, no way to see a Run of either kind
    try:
        panes = _parse_tmux_panes(_list_panes_raw())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # A dead tmux server takes every Managed Run with it (ADR 0010), but the
        # `claude` someone left running in iTerm is untouched — and still forks
        # its transcript if resume cannot see it. So this degrades to "no Managed
        # Runs", never to a blind resume guard.
        panes = []
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
    # Every tty we own, including one whose `claude` has not reached `ps` yet —
    # otherwise a Managed Run would read as Foreign for the second it takes to
    # start, and flip kind under the Board.
    rows += _foreign_rows(claude, {tty for _, tty, _, _, _ in panes}, meta)
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


def _cached_walk() -> list[dict]:
    """The memoized walk: every live Run, Managed and Foreign. Not called
    directly — go through `cached_runs` or `cached_all_runs`, which say which
    kinds you mean."""
    global _runs_cache
    with _runs_lock:
        stamp, rows = _runs_cache
        if time.monotonic() - stamp < _RUNS_TTL:
            return rows
        rows = list_runs()
        _runs_cache = (time.monotonic(), rows)
        return rows


def cached_runs() -> list[dict]:
    """The live **Managed Runs** — the ones the Launcher started, each with a
    pane behind it.

    The Foreign filter lives here rather than at each call site so that every
    existing caller — close, Respond, clear, the Board's triage lanes — keeps
    meaning exactly what it meant before Foreign Runs existed. A Foreign Run has
    no pane to send keys to, no rendered pane to read a blocker from, and no way
    to answer one, so admitting it into any of those would either crash or make
    the queue lie (ADR 0012). Seeing one must be an explicit ask.
    """
    return [r for r in _cached_walk() if not r.get("foreign")]


def cached_all_runs() -> list[dict]:
    """Every live Run, Managed and Foreign — the one-live-Run-per-Session view.

    Only the resume guard wants this. Anything that *drives* a Run wants
    `cached_runs`.
    """
    return _cached_walk()


def cached_foreign_runs() -> list[dict]:
    """The live **Foreign Runs** — the ones started by hand in another terminal,
    which the Launcher sees but cannot reach into.

    Its own accessor rather than a `foreign` filter at the call site, for the
    same reason `cached_runs` filters here: reaching a Foreign Run must take
    typing a different name, so no driving path can pick one up by accident. The
    Board's quiet section is the only caller — everything else on the Board is
    triage, and there is nothing here to triage with.
    """
    return [r for r in _cached_walk() if r.get("foreign")]


def invalidate_runs() -> None:
    global _runs_cache
    with _runs_lock:
        _runs_cache = (0.0, [])


def _live_session_ids() -> set[str]:
    """sessionIds of Sessions with a live Run — the resume live-guard set.

    Resuming a Session that already has a live Run would put two Runs on one
    transcript, so /api/resume refuses any id in here.

    Counts **Foreign Runs** too: a Session live in another terminal is exactly as
    forkable as one live in a tmux window. Between ADR 0010 and 0012 this set was
    fed from tmux panes alone, so CONTEXT.md's "at most one live Run per Session
    … a transcript is never forked" quietly held only for Managed Runs. Widening
    it *tightens* resume — a Session that was wrongly resumable is now correctly
    refused, which reads as a regression to anyone who relied on the hole.
    """
    return {sid for r in cached_all_runs() if (sid := r.get("sessionId"))}


def _resume_guard(session_id: str) -> tuple[str, str]:
    """Re-run Resume's guards against *current* state and resolve the dir to
    resume in. Returns (workdir, "") to proceed, or ("", message) to refuse.

    The single source of the resume guard sequence — a valid UUID, a transcript
    on disk, no live Run on that Session (Managed or Foreign — _live_session_ids
    counts both), and a cwd that still exists — shared by /api/resume (one
    Session) and /api/recover (a batch). Checked at call time, never trusting a
    picker's earlier GET: a dir can vanish or a Session go live in between. It
    resolves the dir but does NOT spawn — the caller owns launch_run and its
    tmux-error handling, so /api/resume keeps its 500-on-tmux / 400-on-guard
    split and the batch can fold a member's tmux error into a failed row.
    """
    if not _UUID_RE.match(session_id):
        return "", "invalid session id"
    if not _transcript_path(session_id):
        return "", "no such session"
    if session_id in _live_session_ids():
        return "", "already live"
    workdir = _session_cwd(session_id)
    if not workdir or not os.path.isdir(workdir):
        return "", "session's dir is gone"
    return workdir, ""


def _is_managed_run(run_id: str) -> bool:
    """True only for a live **Managed Run** — one the Launcher started, so one
    with a pane to reach into.

    The gate on every driving verb (close, Respond, clear). An id that fails is
    malformed, stale, or names a **Foreign Run**, and all three must no-op: a
    Foreign Run is never closed or driven in place (ADR 0012), and the check is
    made here rather than trusted to the client. `cached_runs` is Managed-only,
    so membership in it *is* the Managed test.
    """
    return bool(_UUID_RE.match(run_id)) and run_id in {r["id"] for r in cached_runs()}


def close_run(run_id: str) -> bool:
    """Close the tmux window for this Run id, but only if it's a live claude one
    the Launcher started — a Foreign Run has no window of ours to close."""
    if not _is_managed_run(run_id):
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


# --- Transfer: end a Foreign Run, resume its Session as a Managed one -------
# One atomic operation — kill, wait for the exit, resume (ADR 0012). Split
# across two client calls, a tap that fails between them leaves a killed Run and
# nothing running, which is strictly worse than not tapping; and the tap comes
# from a phone, so nobody is at the Mac to finish it by hand.
#
# The client sends the **Session**, never a pid. The pid is re-derived here from
# the Launcher's own walk, so the only process this can ever signal is one it has
# just itself identified as a live **Foreign Run**. Taking a pid from the body
# would make this a kill-anything endpoint — which is why the Board's Foreign
# rows drop `pid` rather than blanking it (`_foreign_items`).

# SIGTERM, then SIGKILL. `claude` normally goes within a few hundred ms of a
# TERM; the grace is long enough to cover a slow flush and short enough that an
# AFK tap does not read as a hang. The wait is load-bearing, not politeness: the
# resume guard counts Foreign Runs, so the resume is refused until this Run is
# gone from `ps`.
_TRANSFER_TERM_GRACE = 4.0
_TRANSFER_KILL_GRACE = 2.0
_TRANSFER_POLL = 0.1

# Serialises Transfers, so a double-tap cannot kill once and resume twice. The
# second caller runs the whole sequence *after* the first, finds no Foreign Run
# on that Session any more, and refuses — rather than racing it to two Managed
# Runs on one transcript, which is exactly the fork the guard exists to stop.
_transfer_lock = threading.Lock()


class TransferFailed(Exception):
    """A **Transfer** that did not complete.

    `orphaned` is the field that matters: True means the **Foreign Run** was
    already killed when the failure happened, so the **Session** now has nothing
    running at all and the person who tapped is not at the Mac to notice. The
    Session itself is on disk and recoverable, as always — but that case must be
    reported as itself, never as a generic error (ADR 0012).
    """

    def __init__(self, message: str, *, status: int = 400, orphaned: bool = False):
        super().__init__(message)
        self.status = status
        self.orphaned = orphaned


def _pid_alive(pid: int) -> bool:
    """Signal 0: does this process still exist?

    A PermissionError means it exists and is not ours to signal, which is still
    "alive" as far as the wait is concerned — treating it as gone would let the
    resume through beside a Run that never died.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _foreign_run_for(session_id: str) -> dict | None:
    """The live **Foreign Run** executing this Session, or None.

    Re-walks first (`invalidate_runs`): the memoized walk is up to `_RUNS_TTL`
    old, and everything after this *signals* the pid it returns. A pid that
    exited inside that window is a pid the OS is free to hand to something else,
    so Transfer must never take one on trust from the cache.

    `cached_foreign_runs` rather than a `foreign` filter over the whole walk, for
    the reason that accessor exists: a Managed Run must not be reachable from
    here even by a typo. Closing one of ours is `/api/close`'s job, and Transfer
    may never become a second way to kill a Run we own.
    """
    invalidate_runs()
    for r in cached_foreign_runs():
        pid = r.get("pid")
        # pid > 1 belt-and-braces: 0 and -1 are broadcast targets to `kill(2)`,
        # and a row that somehow carried one would signal every process we own.
        if r.get("sessionId") == session_id and isinstance(pid, int) and pid > 1:
            return r
    return None


def _await_run_gone(pid: int, session_id: str, grace: float) -> bool:
    """Wait until this Run has left the Launcher's own view, or `grace` runs out.

    Two conditions, not one. The process must be gone (`_pid_alive`), *and* the
    Session must have left `_live_session_ids()` — which is what the resume
    actually has to satisfy, and which is fed from `ps` rather than from
    `os.kill`. The two can disagree: a `claude` that has exited but not yet been
    reaped by its terminal is gone to `os.kill` while `ps` may still be catching
    up, and a Session that has a *second* live Run never clears at all. Waiting on
    the weaker condition would hand the resume a refusal it cannot recover from —
    after the kill, which is the one place there is no way back.

    Each pass invalidates the cache before looking: a memoized walk would answer
    with the state from *before* the kill and wave the resume through while the
    old Run is still there, or (once it is gone) keep reporting it for up to a
    TTL and burn the whole grace on a Run that already exited.
    """
    deadline = time.monotonic() + grace
    while True:
        invalidate_runs()
        if not _pid_alive(pid) and session_id not in _live_session_ids():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_TRANSFER_POLL)


def _end_foreign_run(pid: int, session_id: str) -> bool:
    """SIGTERM, then SIGKILL on a short timeout. True once the Run is gone.

    Kills the *process*, never the terminal holding it: the original tab is left
    at a dead shell prompt until a human closes it (ADR 0012). Reaching into a
    GUI terminal to close it is the dependency this whole design refuses.
    """
    for sig, grace in ((signal.SIGTERM, _TRANSFER_TERM_GRACE),
                       (signal.SIGKILL, _TRANSFER_KILL_GRACE)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            # It exited between the walk and the signal — that is the outcome we
            # wanted, so fall through to the wait, which confirms it against `ps`
            # instead of assuming.
            pass
        except PermissionError as e:
            # Nothing was delivered, so nothing died: a refusal, not an orphan.
            raise TransferFailed(
                f"not allowed to end the Foreign Run (pid {pid}) — nothing was "
                f"changed: {e}", status=500) from e
        if _await_run_gone(pid, session_id, grace):
            return True
    return False


def transfer_session(session_id: str) -> str:
    """**Transfer**: end the live **Foreign Run** on this Session and **resume**
    the Session as a **Managed Run**. Returns the new Run id.

    Custody moves; no process does. The old `claude` is killed and a new Run
    replaces it, so the in-flight turn — and any text typed but not sent in the
    other terminal, which is undetectable from here — is lost (ADR 0012). The
    Session is untouched, as always.

    Never refuses a `busy` Run. You tapped deliberately from somewhere else, and
    a refusal would only strand you with no other route onto it; the cost is
    named on the button and in the confirm instead.

    Raises `TransferFailed`, whose `orphaned` flag separates "we changed nothing"
    from "the Run is dead and the resume did not happen".
    """
    if not _UUID_RE.match(session_id):
        raise TransferFailed("invalid session id")
    with _transfer_lock:
        run = _foreign_run_for(session_id)
        if not run:
            # Say which refusal this is. `cached_runs` reads the same fresh walk
            # `_foreign_run_for` just took, so the two answers cannot disagree.
            if any(r.get("sessionId") == session_id for r in cached_runs()):
                raise TransferFailed(
                    "that Session is already a Managed Run — close it with ×, "
                    "not Transfer")
            raise TransferFailed("no live Foreign Run on that Session — it may have ended")
        # Resolve everything the resume needs BEFORE the irreversible step. The
        # dir comes from Claude Code's own state, exactly as /api/resume takes it;
        # finding it gone *after* the kill would be an orphaned Session for a
        # reason that was visible all along.
        workdir = _session_cwd(session_id)
        if not workdir or not os.path.isdir(workdir):
            raise TransferFailed("session's dir is gone")
        pid = run["pid"]
        if not _end_foreign_run(pid, session_id):
            # Phrased as the guard saw it, not as "the pid is still alive": the
            # wait fails either because the process outlived SIGKILL or because
            # that Session has a *second* live Run. Both mean the same thing to
            # whoever tapped — the resume was not safe, so it did not happen.
            raise TransferFailed(
                f"could not end the Foreign Run (pid {pid}) — that Session still "
                f"has a live Run, and nothing was resumed", status=500)
        try:
            run_id = launch_run(workdir, resume_id=session_id)
        except (subprocess.CalledProcessError, OSError) as e:
            raise TransferFailed(
                f"ended the Foreign Run but the resume failed ({e}) — NOTHING IS "
                f"RUNNING on this Session. It is safe on disk: resume {session_id} "
                f"to get it back.", status=500, orphaned=True) from e
        # The Foreign row is gone and a Managed one is starting; both must be true
        # on the very next poll rather than up to a TTL later.
        invalidate_runs()
        return run_id


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
    currently-live **Managed Run** (mirrors close_run); a stale, bogus or
    foreign id no-ops.

    Text is sent literally with `send-keys -l` (which, unlike iTerm's `write
    text`, does NOT bracket-paste), then submitted by a *separate* `send-keys
    Enter` — the submit stays a distinct keystroke (ADR 0010 landmine #3), so a
    newline can never ride inside the literal text and stick unsent in the box.
    Selector keys are bare tmux key names from the fixed map (no `-l`), so the
    client can never inject a raw escape sequence.
    """
    keys = keys or []
    if not _is_managed_run(run_id):
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

    REFUSES in two cases, both of them "a failed or absent reading may not
    produce an action" (ADR 0020):

    - The pane could not be captured at all. The margin is not a safe default
      here: 16 BSpace sent at a screen nobody read is the archetype this rule
      exists to kill, and `_PaneRead.captured` is precisely the bit that tells
      "nobody looked" from "the box is empty".
    - An AskUserQuestion widget owns the screen. There is no input box to empty,
      and clearing anyway is ADR 0020's worst near-miss made real: the false ⚠
      unsent-text warning put a clear button beside a live selector, and this
      function's own over-count margin would have fired BSpace into it.

    NOT on `selector`, deliberately, though it is the same hazard: `_parse_selector`
    has no numbering-run discriminator, so any two consecutive numbered lines of
    Claude's own prose read as a menu — and refusing on that would break the
    clear button on a large share of perfectly ordinary frames. A guess in the
    refusing direction is still a guess. The widget check is structural (the
    checkbox anchor plus a descending run), so it is the one that is trusted;
    when the permission menu gets the same discriminator this should extend to
    it. Until then the exposure is bounded: `pendingInput` — the only thing that
    puts the clear button on screen — is already blank whenever a menu is up.
    """
    if not _is_managed_run(run_id):
        return False
    pane = _pane_for_run(run_id)
    if not pane:
        return False
    pr = _read_pane(_pane_contents(run_id))
    if not pr.captured or pr.widget:
        return False
    content = pr.unsent
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
# The one place the fence shape is written down. Both the block branch below
# and the paragraph collector that must break on it read this.
_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")


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
        # Fenced code first: everything up to the closing fence is literal, so
        # this branch must win over headings, tables and lists — a `#` or a `|`
        # inside a block is code, not markup. Escaped with html.escape rather
        # than _md_inline, because `**` and backticks inside code are also code.
        # No info string is kept: it would be the renderer's only attribute, and
        # nothing styles a language, so it would buy the innerHTML sink (ADR
        # 0006) a new shape for no pixels.
        m = _FENCE_RE.match(ln)
        if m:
            indent, fence = m.group(1), m.group(2)
            # The closer must be at least as long as the opener and the same
            # character, so a 5-backtick fence can carry ``` blocks intact —
            # which is how a transcript quotes markdown at us.
            close = re.compile(rf"^\s*{fence[0]}{{{len(fence)},}}\s*$")
            i += 1
            code = []
            while i < len(lines) and not close.match(lines[i]):
                # An indented fence indents its body too; that indent is the
                # fence's, not the code's.
                code.append(lines[i][len(indent):] if lines[i][:len(indent)] == indent else lines[i])
                i += 1
            i += 1  # the closing fence, or one past the end if it never came
            body = html.escape("\n".join(code))
            out.append(f"<pre><code>{body}</code></pre>")
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
        while i < len(lines) and lines[i].strip() and not (
                re.match(r"^(#{1,4}\s|\s*([-*]|\d+\.)\s)", lines[i])
                or _FENCE_RE.match(lines[i])):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_md_inline(' '.join(buf))}</p>")
    return "\n".join(out)


# Truncation budgets keep the focus card readable on a phone. A command / file /
# plan under approval is transcript text we do not own; an over-long one is
# clipped with an ellipsis. (The run-up prose above it had its own budget until
# ADR 0014 — it is now the **Scrollback**, bounded by _SCROLLBACK_TURNS/_TURN_MAX
# below instead.)
_ASK_MAX = 600     # the command / file / plan being approved

# The **Scrollback**'s bounds, and the only knobs that decide what `/api/board`
# costs. ADR 0014 accepted a bigger body on the condition that it stays bounded
# on purpose. If the body ever becomes the problem, CUT THESE — do not split the
# scrollback back out onto a second endpoint; that is the design ADR 0014
# rejected.
#
# A **work run** costs one slot, not one per call (ADR 0016), so these four
# numbers together are the bound: at most _SCROLLBACK_TURNS entries, each either
# _TURN_MAX characters of prose or _RUN_CALLS calls of _CALL_MAX each. A run's
# worst case (~19KB) stays under a prose turn's, which is what makes charging
# them the same slot honest.
_SCROLLBACK_TURNS = 14   # how many recent entries of the Session the Focus shows
_TURN_MAX = 4000         # per-turn clip on one turn's prose
_RUN_CALLS = 24          # per-run clip on how many calls carry their detail
_CALL_MAX = 200          # per-call clip on what a tool call was doing


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


_WS_RE = re.compile(r"\s+")


def _same_question(rendered: str, question: str) -> bool:
    """Does the pane's rendered question text name this structured question?

    Deliberately not equality. The pane hard-wraps mid-sentence at terminal width
    (those newlines are the renderer's, not the author's) and `_pane_question`
    clips at 200 chars, so a long question reaches us as a PREFIX of the real
    one. Whitespace collapses on both sides and the test is prefix-wise in that
    direction ONLY: a rendered fragment of a longer structured question is the
    clipped case, but structured text the pane never painted is not a match, it
    is a disagreement, and ADR 0020 says disagreement falls back."""
    a, b = _WS_RE.sub(" ", rendered or "").strip(), _WS_RE.sub(" ", question or "").strip()
    return bool(a) and bool(b) and b.startswith(a)


def _current_ask(questions: list, rendered: str, on_screen: bool) -> int | None:
    """Which **Ask** of the **Ask Set** the pane is showing, or None if the pane
    and the transcript cannot be reconciled.

    A Set of one is trivially answered — 326 of the 425 asks on disk hold one
    question, so this is the common path and it must not depend on text matching
    at all.

    `on_screen` says whether a widget was actually read off a pane, and it is the
    difference between a guess and a contradiction:

    - Not on screen (the queue's one-liner reads the transcript and captures no
      pane): the first Ask is the only sane guess, and the caller marks it as
      one — `_ask_set` refuses to emit keystrokes for it.
    - On screen but with no readable prompt: refuse. A widget IS painting one of
      several questions and we cannot say which, so guessing the first would put
      q1's options under q2's question — ADR 0020's opening incident, restored
      by a default. This is the one case where a widget is present and we still
      report `unmatched`.

    Beyond that the match must be UNIQUE. Two questions whose rendered text is
    ambiguous between them is exactly the case where picking one sends a
    keystroke to the wrong Ask, which is the failure this run exists to remove."""
    if len(questions) == 1:
        return 0
    if not rendered:
        return None if on_screen else 0
    hits = [i for i, q in enumerate(questions) if _same_question(rendered, q.get("question", ""))]
    return hits[0] if len(hits) == 1 else None


_CONTRADICTIONS: dict[str, str] = {}


# Why each refusal is worth saying out loud, in the words of what it would mean
# if it were NOT the ordinary race. Keyed by `_ask_set`'s `fallback`; a reason
# absent from this map is not logged, which is how "nobody looked" (`no-pane`)
# stays quiet — it is not a disagreement, it is an absence.
_CONTRADICTION_DETAIL = {
    "no-widget": "the pane shows no widget at all",
    "pane-mismatch": "the pane's rows do not account for the tool's options",
    "no-cursor": "the pane paints no cursor on the widget",
    "unmatched": "no question in the tool matches the one on screen",
}


def _report_contradiction(where: str, reason: str) -> None:
    """Say out loud that the transcript and the rendered pane disagreed, once
    per distinct disagreement.

    ADR 0020's four version-pinned assumptions all failed the same way: quietly.
    `_is_question_widget` returned False for every ask, `_parse_selector`
    returned {}, and nothing anywhere said so — the code degraded to a default
    that looked like a reading, and a human eventually stumbled on it months
    later. So a disagreement is reported to stderr as well as carried in the
    payload's `fallback`: the payload is seen only by whoever is looking at that
    one Run, and the log is what answers "why did taps stop working everywhere
    at once", which is the shape a renderer change actually takes.

    Stated as an observation, NOT as a diagnosis. The ordinary cause is benign
    and frequent — the Ask was answered at the desk in the gap between the
    transcript read and the pane capture, and the next poll will agree again.
    Writing "the renderer moved" for that would dilute the one signal this
    exists to give. What matters is the PATTERN: every Run at once, or one Run
    forever, is a re-fit (see the RENDERER VOCABULARY block); one line and quiet
    after is a race.

    Deduped on the message: the Board polls every few seconds and a stuck widget
    would otherwise write the same line forever. Bounded, because an unbounded
    cache keyed by tool_use id is a leak in a process that runs for weeks."""
    detail = _CONTRADICTION_DETAIL.get(reason)
    if not detail or _CONTRADICTIONS.get(where) == reason:
        return
    if len(_CONTRADICTIONS) > 256:
        _CONTRADICTIONS.clear()
    _CONTRADICTIONS[where] = reason
    sys.stderr.write(
        f"pane/transcript disagree [{sanitize_log(where)}] {sanitize_log(reason)}: "
        f"the transcript has a pending AskUserQuestion but {sanitize_log(detail)} "
        "— usually the Ask was answered at the desk mid-poll; if it persists or "
        "hits every Run, the renderer has moved (see RENDERER VOCABULARY)\n")


def _ask_set(tu: dict | None, pane: "_PaneRead | None" = None) -> dict:
    """The **current Ask** of the **Ask Set** raised by a pending
    `AskUserQuestion`, or {} for anything else (an approval is a Set of one and
    keeps the plain `ask`; it has no question structure to invent).

    ADR 0020: the transcript says WHAT is asked — every question, its `header`,
    every option's label AND `description`, `multiSelect` — and the pane says
    WHERE the widget stands. So nothing here reads a label off the screen; the
    pane supplies only the index of the live question and the cursor.

    Keys:
      index       0-based position of the current Ask in the Set (-1 when the
                  pane and the transcript name different questions).
      count       size of the Set — `index`/`count` is the "Ask 1 of 2" line.
      question    the current Ask's full, unclipped text.
      header      its `header` (the widget's tab label).
      multiSelect its `multiSelect`, so the client knows a tap is a toggle and
                  not an answer.
      options     `{"label", "description", "row", "steps", "checked"}` per
                  option, in the tool's order. `checked` is READ from the pane's
                  toggle box, never remembered locally — ADR 0020 answers every
                  step against a freshly read frame — and is None when the row
                  carries no box (a single-select question, or a fallback where
                  no row could be attributed to this option).
      tappable    may the client send `steps`? False whenever anything below
                  disagreed.
      fallback    why not — every way this can refuse has its own name, because
                  a refusal you cannot tell apart from a different refusal is
                  half a silent failure:
                    ""             everything agreed; `steps` are real.
                    "no-pane"      nobody looked (no capture was taken).
                    "no-widget"    we looked and the screen shows no widget,
                                   while the transcript says one is pending.
                    "unmatched"    a widget is up but no question matched it.
                    "pane-mismatch" the rows do not account for the options.
                    "no-cursor"    rows agree, but the frame paints no cursor,
                                   so no keystroke count can be measured.

    `steps` is signed and measured against the widget's ROWS, not its options:
    positive is that many Down, negative that many Up, then Enter. Counting in
    options is precisely ADR 0020's wrong-answer table — the widget paints
    `Type something.` and `Chat about this` as ordinary numbered rows, so an
    option's index among the OPTIONS lands the cursor on an affordance and
    answers a question nobody asked. `row` is carried alongside so a client
    never has to redo this arithmetic to check it.

    The fallbacks differ in what stays on screen, because they differ in what is
    untrustworthy:

    - The options do not line up with the rows ("pane-mismatch"), the cursor was
      not painted ("no-cursor"), the screen contradicts the transcript
      ("no-widget"), or nobody looked ("no-pane"). WHICH Ask is known, so its
      content is sound and is still rendered — read-only, `row`/`steps` null.
      You lose the tap, not the read.
    - No question matched ("unmatched"). We do not know which Ask is on screen,
      so its content is not sound either: options are dropped entirely rather
      than paint q1's answers over q2's question, which is the original bug.

    `pane` is a `_PaneRead` or None — never a raw pane string, never a bare
    widget dict. See `_PaneRead` for why the old `(widget, rendered)` pair was
    itself an instance of the failure this function exists to prevent.
    """
    if pane is not None and not isinstance(pane, _PaneRead):
        # Loud, not lenient. The predecessor of this argument accepted any `str`
        # and silently produced an optionless Ask Set when handed the wrong one
        # (ADR 0020). A parse layer whose contract is "refuse rather than guess"
        # cannot itself guess what its caller meant.
        raise TypeError("_ask_set(pane=...) takes a _PaneRead from _read_pane(), "
                        f"not {type(pane).__name__}")
    if not tu or tu.get("name") != "AskUserQuestion":
        return {}
    questions = [q for q in ((tu.get("input") or {}).get("questions") or [])
                 if isinstance(q, dict)]
    if not questions:
        return {}
    pane = pane or _NO_PANE
    widget = pane.widget
    tu_id = str(tu.get("id") or "")
    idx = _current_ask(questions, pane.question, bool(widget))
    if idx is None:
        # A pending AskUserQuestion in the transcript and a pane we DID capture
        # that cannot be reconciled with it is not a missing reading — it is two
        # sources saying opposite things, which is how every one of ADR 0020's
        # four bugs presented, and none of them said a word.
        _report_contradiction(tu_id, "unmatched")
        return {"index": -1, "count": len(questions), "question": "", "header": "",
                "multiSelect": False, "options": [], "tappable": False,
                "fallback": "unmatched"}
    q = questions[idx]
    opts = [o for o in (q.get("options") or [])
            if isinstance(o, dict) and o.get("label")]
    rows = widget.get("rows") or []
    cursor = widget.get("cursor")
    # Where each option SITS among the rows the cursor steps through.
    seats = [i for i, r in enumerate(rows) if not r.get("affordance")]
    # The cross-check ADR 0020 requires before trusting a position: the pane's
    # own option rows must account for the tool's options, one for one and in
    # order. Prefix, not equality — the pane truncates a long label at terminal
    # width, so the rendered label is a prefix of the structured one. The cursor
    # is part of the check, not an input to it: `steps` is measured FROM it, so
    # an unread cursor makes every count fiction (it was defaulted to 0 for
    # months, and 0 is right often enough to hide the rest).
    rows_fit = bool(rows) and len(seats) == len(opts) and all(
        opts[j]["label"].startswith(rows[i]["label"]) for j, i in enumerate(seats))
    agree = rows_fit and cursor is not None
    if agree:
        fallback = ""
    elif not widget:
        fallback = "no-widget" if pane.captured else "no-pane"
    elif rows_fit:
        fallback = "no-cursor"
    else:
        fallback = "pane-mismatch"
    # EVERY way of refusing gets said out loud, not merely the missing widget.
    # `pane-mismatch` is the strongest renderer-moved signal there is — the rows
    # on screen no longer account for the options the tool itself sent — and it
    # would otherwise live only in a payload field nobody greps. `no-pane` is
    # filtered out inside: an absence is not a disagreement.
    if fallback:
        _report_contradiction(tu_id, fallback)
    return {
        "index": idx, "count": len(questions),
        "question": str(q.get("question") or "").strip(),
        "header": str(q.get("header") or ""),
        "multiSelect": bool(q.get("multiSelect")),
        "options": [{"label": o["label"],
                     # NOT clipped: median 175 chars, p90 285 (ADR 0020's census),
                     # and the description is where the reasoning that makes the
                     # question decidable lives. Plain text — textContent, never
                     # innerHTML (ADR 0003/0006), as with every other ask field.
                     "description": str(o.get("description") or ""),
                     "row": seats[j] if agree else None,
                     "steps": seats[j] - cursor if agree else None,
                     "checked": rows[seats[j]].get("checked") if agree else None}
                    for j, o in enumerate(opts)],
        "tappable": agree, "fallback": fallback,
    }


def _ask_of(session_id: str, rows: list | None = None,
            pane: "_PaneRead | None" = None) -> tuple:
    """(ask, options, askSet) from the Session's last assistant turn.

    For an approval the `ask` describes the flushed pending tool_use (the Bash
    command, the Edit/Write target, or the ExitPlanMode plan) — what you're being
    asked to approve, and `askSet` is {}: an approval is an **Ask Set** of one
    and needs no question structure. See ADR 0009 and `_approval_detail` for why
    this is structured data, not a pane scrape.

    It used to return the run-up prose as well, for ADR 0006's single
    `contextHtml` field; that field is gone (ADR 0014) and the prose is now the
    **Scrollback**, so the text is only an input to the `?` regex here. It was
    called `_full_context` until it stopped returning any — "context" is
    retired as a name for the Focus's reading surface (CONTEXT.md, *Flagged
    ambiguities*), and a function keeping the word while returning only the
    **Ask** is the exact drift that list exists to catch.

    `rows` lets a caller hand in a tail it has already parsed, so the **Ask** and
    the **Scrollback** cost one file read between them (ADR 0014).

    `pane` is the `_PaneRead` off the ONE capture `_board` already takes —
    passed in for the same reason `rows` is, and for the same ADR 0014 reason:
    no caller pays a second read to learn where the widget is standing. Omitting
    it is honest, not broken: the queue's one-liner has no pane, and gets an
    untappable Ask Set (`fallback: "no-pane"`). It was `(widget, rendered)` until
    a raw pane string passed for `rendered` proved to fail silently — `_PaneRead`
    exists so that mistake raises instead."""
    rows = _tail_rows(session_id) if rows is None else rows
    la = _last_assistant(rows)
    if not la:
        return "", [], {}
    text = "\n".join(b.get("text", "") for b in _blocks(la) if b.get("type") == "text").strip()
    qs = re.findall(r"[^\n?]*\?", text)
    ask = qs[-1].strip()[-200:] if qs else ""
    options = []
    tu = _pending_tool_use(rows)
    aset = _ask_set(tu, pane)
    if aset:
        # ONE Ask's options, never the whole Set's. Concatenating every question's
        # options is ADR 0020's opening incident: the phone showed q1's text above
        # four buttons, two of which answered a question that was never on screen.
        options = [o["label"] for o in aset["options"]]
        # The structured prompt beats the prose `?` regex: the real question lives
        # in the tool input, not necessarily in the assistant's text. When no
        # question could be matched there is no structured text to prefer, and the
        # rendered one is the only thing that is certainly on screen.
        ask = (aset["question"] or (pane or _NO_PANE).question).strip()[:200] or ask
    elif tu:
        # An approval (any non-AskUserQuestion pending tool_use — Bash / Edit /
        # Write / ExitPlanMode …): surface the concrete blocker as the ask. An
        # approval is an **Ask Set** of one and takes this same path with no
        # question structure invented for it — hence `askSet` stays {}.
        ask = _approval_detail(tu) or ask
    return ask, options, aset


# A **tool call**'s detail: what it was DOING, not merely which tool it was.
# `name` -> the one input field worth showing. `_approval_detail` above answers
# the same question for the six approvable tools and answers it for the **Ask**;
# this is that idea generalised to every tool a **Turn** can invoke (ADR 0016).
_CALL_ARG = {
    "Bash": "command", "Read": "file_path", "Write": "file_path",
    "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path",
    "Glob": "pattern", "Grep": "pattern", "WebFetch": "url", "WebSearch": "query",
    "ToolSearch": "query", "Skill": "skill", "Agent": "description",
    "Task": "description", "ExitPlanMode": "plan",
}

# The slash command you typed, off the `<command-name>` row Claude Code writes
# beside it. Deliberately narrow: a leading `/`, then non-space, non-`<`.
_CMD_RE = re.compile(r"<command-name>\s*(/[^<\s]+)")


def _call_of(tu: dict) -> dict:
    """One `tool_use` as `{"name", "detail"}` — the tool, and what it did with it.

    `detail` is PLAIN TEXT and the client sets it with textContent (ADR 0003).
    Unlike a **Turn**'s prose it does NOT go through `_md_to_html`, so it must
    never reach an HTML sink; that asymmetry is the reason this returns a
    separate field rather than more `html`.

    Whitespace is collapsed because a heredoc or a multi-line plan arrives with
    newlines in it and this renders as a single ellipsised line."""
    name = tu.get("name") or "?"
    inp = tu.get("input") if isinstance(tu.get("input"), dict) else {}
    detail = str(inp.get(_CALL_ARG.get(name, ""), "") or "")
    if name in ("Edit", "MultiEdit"):       # the file, plus what it became
        first = str(inp.get("new_string") or "").strip().splitlines()
        if first:
            detail = f"{detail} — {first[0].strip()}"
    elif name == "Grep" and inp.get("path"):
        detail = f"{detail} in {inp['path']}"
    elif name in ("Agent", "Task") and inp.get("subagent_type"):
        detail = f"{inp['subagent_type']}: {detail}"
    elif name == "AskUserQuestion":
        qs = inp.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            detail = str(qs[0].get("question") or "")
    elif name.startswith("mcp__"):          # mcp__intake__write_note -> intake/write_note
        parts = name.split("__")
        if len(parts) > 2:
            name = "/".join(parts[1:])
    return {"name": name, "detail": _clip(" ".join(detail.split()), _CALL_MAX)}


def _scrollback(rows: list) -> list[dict]:
    """The recent entries of a **Session**, oldest first — what the **Focus**
    reads (ADR 0014, reshaped by ADR 0016). One entry is one of three things:

        {"role": "user"|"assistant", "html": …}   prose someone produced
        {"role": "command", "cmd": "/ship"}       a slash command you invoked
        {"role": "work", "calls": [{name, detail}, …], "n": 7}

    Takes an already-parsed tail rather than a `sessionId`, because it shares its
    parse with `_ask_of`: one file read per poll feeds both the scrollback and
    the **Ask**. It is a bounded window on the transcript, never the whole thread.

    `html` is `_md_to_html` of the turn's text blocks, so every turn is rendered
    escape-first exactly as ADR 0006's single `contextHtml` was: the client
    `innerHTML`s N strings instead of one, through the same function, adding no
    new sink. A **work run**'s `calls` are NOT html and never become any.

    Three rules here are ADR 0016's, and each one is a thing the scrollback used
    to get wrong:

    - **A contiguous run of tool calls is ONE entry.** Claude Code emits one
      assistant row per `tool_use` and never mixes prose into it, so a stretch of
      tool work arrived as N separate turns and ate N of the _SCROLLBACK_TURNS
      slots — 5-8 of 14 on a live Session, evicting the prose ADR 0014 exists to
      show. Coalescing is what pays for the detail.
    - **An `isMeta` row is dropped.** A skill's injected body is a 2-7KB `user`
      row nobody typed; rendered as prose it claims you said it.
    - **The `<command-name>` row is kept, not filtered.** With the body gone it
      is the only trace a bare `/ship` leaves, and it is the one line that is
      true: you invoked a skill.
    """
    out: list[dict] = []
    for o in rows:
        # A sidechain row belongs to a subagent's own thread, not to this
        # Session's turns.
        if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
            continue
        blocks = _blocks(o)
        raw = (o.get("message") or {}).get("content")
        if not blocks and isinstance(raw, str) and raw.strip():
            blocks = [{"type": "text", "text": raw}]   # a user turn may be a bare string
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        tus = [b for b in blocks if b.get("type") == "tool_use"]
        if o.get("isMeta"):
            continue                        # an injected skill body is not your turn
        if o["type"] == "user" and not text and any(b.get("type") == "tool_result" for b in blocks):
            continue                        # a tool return is not a human turn
        if text.startswith("<") and text.endswith(">"):
            m = _CMD_RE.search(text)        # the slash command; the rest is plumbing
            if m:
                out.append({"role": "command", "cmd": m.group(1)})
            continue
        if not text and not tus:
            continue
        if text:
            out.append({"role": o["type"], "html": _md_to_html(_clip(text, _TURN_MAX))})
        if tus:
            # Extend the run in progress rather than opening a second one. `n`
            # counts every call; `calls` carries the last _RUN_CALLS of them, so
            # a 200-step stretch still says 200 without weighing 200 details.
            #
            # Handled as its own branch rather than `elif`, so a row carrying
            # BOTH prose and a call yields both entries. A census over 40
            # transcripts found zero such rows — Claude Code splits them — but
            # the alternative to this branch is dropping the call on the floor
            # the day that stops being true.
            prev = out[-1] if out else None
            if not (prev and prev.get("role") == "work"):
                prev = {"role": "work", "calls": [], "n": 0}
                out.append(prev)
            prev["n"] += len(tus)
            prev["calls"] = (prev["calls"] + [_call_of(t) for t in tus])[-_RUN_CALLS:]
    return out[-_SCROLLBACK_TURNS:]


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

# ════════════════════════ RENDERER VOCABULARY ════════════════════════
# THE ONE PLACE TO EDIT WHEN THE CLAUDE CODE TUI CHANGES.
#
# Every literal the TUI paints — cursor glyphs, checkbox and tab glyphs, toggle
# markers, affordance labels, rule and box characters — is defined here and
# nowhere else. The TUI is an UNVERSIONED external dependency, scraped off a
# rendered pane, and it changes with no notice and no changelog. ADR 0020 caught
# FOUR version-pinned assumptions in one sitting (`"add notes"`, the side-panel
# description layout, the `☒` answered tab, the `[✔] ` toggle marker), every one
# of them silent, every one scattered in a different function beside a comment
# asserting it was stable. Scattered, a re-fit is archaeology; gathered, it is a
# diff you can read in one screen.
#
# TO RE-FIT, IN THIS ORDER:
#   1. CAPTURE FIRST — `tools/capture-widget.py <name> --pane %N` writes the
#      `-p` frame, the `-e` frame and the transcript tail into tests/fixtures/,
#      stamped with the Claude Code version it came from. Evidence before edits:
#      all four bugs above survived because the renderer was reasoned about
#      rather than read.
#   2. Edit the constants below — only them.
#   3. `python3 -m unittest discover -s tests`. `PaneFixtureMatrixTests` runs
#      EVERY capture in tests/fixtures/ through every pane parser, so the old
#      renderers must keep parsing while the new one starts to. A capture added
#      in step 1 extends that matrix by existing.
#
# NOT vocabulary, and deliberately left as logic: the descending numbering run
# that separates a menu row from a numbered line of prose (`_widget_rows`) and
# the checkbox-header anchor (`_widget_anchor`). Those are structural claims
# about the widget's shape, not strings it happens to paint — a renderer that
# broke them would need new logic, not a new literal.

# The glyph marking the live row of a menu — and, in the input box, the prompt.
# One tuple because the renderer draws them with one glyph; if it ever stops,
# split it here rather than at the two call sites.
_CURSOR_GLYPHS = ("❯", "›", ">")
# `☒` is an ANSWERED question tab — the state the strip is in immediately after
# you answer Ask 1 of an **Ask Set**, i.e. the ordinary mid-Set frame this whole
# run exists to drive. Missing from this tuple, `_HEADER_RE` did not match, the
# widget went undetected, and the false ⚠ unsent-text warning came straight back
# on the second Ask of every Set. Captured in `tests/fixtures/ask_toggled.pane`.
_CHECKBOX = ("☐", "☒", "☑", "✔", "✓")
# The widget's own rows, as opposed to the tool's options. They are numbered
# menu rows like any other, so nothing but the label tells them apart — and a
# caller that mixes them in steps the cursor into `Chat about this` and answers
# a question nobody was asked (ADR 0020's measured wrong-answer table).
#
# This IS a version-pinned string, and that is the failure class ADR 0020 indicts
# in `"add notes"`. It is kept only because the structural discriminator the ADR
# names — the rows the transcript's own option labels do not account for — is the
# cross-check that lands with the Ask Set, not here. Until then: a renamed
# affordance silently becomes an option, and the fixture matrix is what says so.
# Matched with the trailing full stop stripped: on a multiSelect frame the row
# renders `4. [ ] Type something` — no period — and an affordance read as an
# option is one seat's worth of cursor drift on every row below it.
_AFFORDANCES = ("Type something", "Chat about this")
# The marks a multiSelect toggle box can carry, ticked or blank.
_TOGGLE_MARKS = " ✔✓xX"
# The characters a horizontal rule is drawn from. Rules frame the input box, and
# one of them ends the widget's question block.
_RULE_CHARS = set("─—-═")

# ── Derived from the vocabulary above; the shapes, not the glyphs. ──
# A rendered selector line: an optional cursor glyph, an optional "N." / "N)"
# index, then the label. Claude Code marks the current option with a cursor
# glyph; permission menus and the trust prompt are numbered.
# Groups: the cursor glyph, the index (the widget reader checks it runs
# unbroken), the label.
_OPT_RE = re.compile(r"^\s*([" + re.escape("".join(_CURSOR_GLYPHS)) + r"])?\s*(\d+)[.)]\s+(\S.*?)\s*$")
# Box-drawing glyphs (U+2500–U+257F). The iTerm-era widget painted the
# highlighted option's description in a side panel on the SAME rows as the
# option labels; splitting a label on the first box glyph drops that bleed. The
# current renderer puts descriptions BELOW each label instead — the change that
# voided `_parse_selector`'s contiguity premise (ADR 0020) — but the old shape
# is still under test, so the split stays.
_BOX_RE = re.compile("[─-╿]")
# The checkbox header line of an AskUserQuestion widget, in both shapes it
# renders in. Single-question (326 of the 425 asks on disk — the COMMON case):
# a bare ` ☐ multiSelect`. Multi-question: a tab strip, `←  ☐ Granularity
# ☐ Expand/contract  ✔ Submit  →`, whose first glyph is the arrow, not the box.
# ADR 0020 makes this line the anchor for the whole widget, replacing both the
# `"add notes"` signature (a version-pinned string the current renderer never
# paints, so `_is_question_widget` returned False for EVERY ask) and
# `_parse_selector`'s contiguity premise.
_HEADER_RE = re.compile(r"^\s*(←\s+)?[" + re.escape("".join(_CHECKBOX)) + r"]\s*\S")
# A multiSelect row's toggle box, which the renderer paints INSIDE the label:
# `1. [✔] Row one` / `3. [ ] Row three`. It is the toggle STATE ADR 0020 wanted
# and `_pane_widget` first shipped without, having no capture that showed a
# ticked row; `tests/fixtures/ask_toggled.pane` is that capture. It must come
# off the label before anything compares it to the transcript — the structured
# label is `Row one`, so an unstripped `[✔] Row one` fails the Ask Set's
# prefix cross-check on all 30 multiSelect asks and makes every one untappable.
_TOGGLE_RE = re.compile(r"^\[([" + re.escape(_TOGGLE_MARKS) + r"])\]\s+")
# ══════════════════════ END RENDERER VOCABULARY ══════════════════════


def _pane_contents(run_id: str) -> str:
    """The Run's rendered pane as a single visible frame, or '' if unreadable.

    Resolves the Run UUID to its tmux pane (`_pane_for_run`) and reads it with
    `capture-pane -p`. Unlike iTerm's `contents of session`, this returns ONLY
    the current visible frame — no scrollback — so ADR 0009's "last contiguous
    option run" scrollback guard is now belt-and-suspenders: a single frame has
    exactly one option run, which the guard still picks correctly. Returns ''
    on any failure; callers depend on '' meaning "couldn't read".

    A **Foreign Run** can never be captured here whatever id is passed in: it has
    no @cl_run_id, so nothing resolves for it (see `_pane_for_run`). Observing one
    stops at Claude Code's own state — never the terminal's (ADR 0012).
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
    is the live frame — earlier ones are stale paints.

    `cursor` is None when the frame paints no cursor glyph, and NEVER 0. A
    defaulted 0 is indistinguishable from "the cursor is genuinely on row 0",
    which is precisely how ADR 0020's wrong-answer table went unnoticed for
    months: the payload said `cursor 0`, nobody could tell it was a guess, and
    keystrokes were counted from it. Read it or say you did not.

    {} — not a one-option dict — when fewer than two options were found: a menu
    you cannot choose between is not a menu, and returning a degenerate one puts
    a button on the phone that answers by accident."""
    groups, cur = [], []
    for ln in text.split("\n"):
        m = _OPT_RE.match(ln)
        if m:
            label = _BOX_RE.split(m.group(3), 1)[0].strip()[:80]   # drop any side-panel bleed
            cur.append((bool(m.group(1)), label))
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    live = groups[-1] if groups else []
    options = [lbl for _, lbl in live]
    cursor = next((i for i, (hit, _) in enumerate(live) if hit), None)
    return {"options": options, "cursor": cursor} if len(options) >= 2 else {}


def _pane_input(text: str) -> str:
    """Whatever is currently typed in the Run's input box, or ''.

    Claude Code frames the input between two horizontal rules, just above the
    `📁 …` status line. Reading it lets Respond refuse to blind-append onto a
    reply already sitting there (a half-typed message, or a prior stuck send)
    instead of silently submitting more than the caller meant.

    '' conflates "no box on screen" with "an empty box", and that conflation is
    kept DELIBERATELY: both mean "nothing typed that we would overwrite", and the
    two ways of being wrong are not symmetric. A false '' costs a warning nobody
    needed; a false non-empty '' paints ⚠ over a live question and offers to
    backspace it away (ADR 0020). Everything upstream of the decision — is a
    widget or menu owning the screen at all? — is settled in `_read_pane`, which
    is the only caller that matters, so this reader never has to guess.
    """
    lines = text.split("\n")
    rules = [i for i, ln in enumerate(lines) if ln.strip() and set(ln.strip()) <= _RULE_CHARS]
    if len(rules) < 2:
        return ""
    box = lines[rules[-2] + 1:rules[-1]]
    out = []
    for ln in box:
        s = ln.strip()
        if s[:1] in _CURSOR_GLYPHS:
            s = s[1:].strip()          # drop the prompt glyph on the first line
        out.append(s)
    return "\n".join(out).strip()


def _widget_rows(lines: list[str]) -> list[dict]:
    """The widget's menu rows in visual order, or [] if these lines carry none.

    Scanned BOTTOM-UP for the run numbered N…1, because the descending run is the
    only thing that separates a menu row from a numbered line inside the question
    body. Top-down, the first stray `1.` in assistant text below the header claims
    the slot and every real row shifts by one — which is ADR 0020's wrong-answer
    table (`2 × down + enter` landing on `Type something.`), reintroduced one line
    lower down the frame. Descriptions and the horizontal rule above
    `Chat about this` split the rows apart, so contiguity cannot be the rule; the
    numbering is.
    """
    rows, expect = [], None
    for ln in reversed(lines):
        m = _OPT_RE.match(ln)
        if not m:
            continue
        n = int(m.group(2))
        if expect is None:
            expect = n
        elif n != expect:
            break                       # the run broke: everything above is prose
        label = _BOX_RE.split(m.group(3), 1)[0].strip()[:80]   # drop side-panel bleed
        # The toggle box belongs to the row, not to the label it precedes.
        tog = _TOGGLE_RE.match(label)
        if tog:
            label = label[tog.end():].strip()
        rows.append({"label": label, "affordance": label.rstrip(".") in _AFFORDANCES,
                     # None, not False: a single-select row has no toggle at all,
                     # and "unticked" is a different claim from "cannot be ticked".
                     "checked": (tog.group(1) != " ") if tog else None,
                     "_cursor": bool(m.group(1))})
        expect -= 1
        if not expect:                  # reached row 1 — the whole menu is in hand
            break
    # A run that never reaches 1 is a fragment, not a menu, and its indices would
    # not be the keystroke counts the caller means. Refuse rather than mislead.
    if expect != 0:
        return []
    # An empty label is worse than a missing row. The Ask Set cross-checks the
    # pane against the transcript with `structured.startswith(rendered)`, and
    # `"x".startswith("")` is True — one blank label makes that check pass for
    # ANY option and hands back a tappable Ask whose rows were never really
    # matched. A label that stripped down to nothing (all box art, or a toggle
    # box with no text after it) is a reading we do not have.
    if any(not r["label"] for r in rows):
        return []
    return list(reversed(rows))


def _widget_anchor(lines: list[str]) -> int | None:
    """Index of the AskUserQuestion widget's checkbox header, or None.

    The last header WITH ROWS UNDER IT. `capture-pane -p` returns only the visible
    frame, but one frame still holds more than one candidate: an earlier paint of
    the widget above the live one, and any `✓ …` line of assistant output, which
    is a header by shape. Taking the last header outright hands the anchor to that
    tick and the whole widget goes unseen — which is the false ⚠ this slice
    closes, back again. Requiring rows makes the anchor the widget itself.

    Everything above the anchor is out of scope, which is what stops `_OPT_RE`
    matching assistant PROSE — `tests/fixtures/ask_multi.pane` carries a four-item
    numbered ticket list above the widget, indistinguishable from menu rows by
    shape alone.
    """
    return next((i for i in range(len(lines) - 1, -1, -1)
                 if _HEADER_RE.match(lines[i]) and _widget_rows(lines[i + 1:])), None)


def _pane_widget(text: str) -> dict:
    """The AskUserQuestion widget read off the rendered pane, or {} if the pane
    shows no widget (a permission menu, a plain input box, ordinary output).

    Kept separate from `_parse_selector` deliberately. A permission prompt has no
    checkbox header at all, so it can never satisfy this anchor; folding the two
    would mean one function with two anchors and two option-scan rules, and the
    permission path — which the old contiguity rule still serves correctly — has
    no reason to move. One reader per widget shape.

    Per ADR 0020 the pane is a POSITION sensor, not a content source: the labels
    here exist to be cross-checked against the structured `AskUserQuestion`
    tool_use, which carries the full (untruncated) labels and their descriptions.
    Returned keys:

      rows        every menu row below the anchor, in visual order, each
                  `{"label", "affordance", "checked"}` — the sequence the cursor
                  steps through, so `rows` and not `options` is what a keystroke
                  count is measured against. `checked` is the multiSelect toggle
                  state (True/False), or None on a row that carries no toggle
                  box at all — a single-select row, where "unticked" would be a
                  claim the renderer never makes.
      options     the tool's own options, i.e. the non-affordance rows.
      cursor      index INTO `rows` of the cursor glyph, or None when the frame
                  paints none. NEVER 0 as a default: 0 is a legitimate reading,
                  so a defaulted 0 is a claim about the screen that nobody made,
                  and ADR 0020's wrong answers were counted from exactly that.
                  A caller that cannot handle None must refuse, not substitute.
      tabs        for a multi-question frame, `{"label", "checked"}` per question
                  tab; `[]` for the single-question shape.
      header      the single-question header text (`""` when a tab strip).

    One thing ADR 0020 asks of the pane is deliberately NOT here, so that its
    absence is a stated gap rather than a silently missing key: which tab is
    CURRENT. The renderer marks it with ANSI attributes that `capture-pane -p`
    drops, and ADR 0020 declined to parse ANSI. The caller identifies the current
    Ask by matching the rendered question text against the structured questions
    instead (`_current_ask`).

    Toggle state WAS the second gap — no capture on hand showed a ticked row, and
    guessing its shape is how `"add notes"` happened. `ask_toggled.pane` is now
    that capture: the box renders inside the label (`1. [✔] Row one`), so it is
    stripped off into `checked` rather than guessed.
    """
    lines = text.split("\n")
    hdr = _widget_anchor(lines)
    if hdr is None:
        return {}
    scanned = _widget_rows(lines[hdr + 1:])
    # None, never 0. See `cursor` in the docstring above: an unread cursor that
    # looks like a read one is the ADR 0020 archetype.
    cursor = next((i for i, r in enumerate(scanned) if r["_cursor"]), None)
    rows = [{"label": r["label"], "affordance": r["affordance"], "checked": r["checked"]}
            for r in scanned]
    strip = lines[hdr].strip()
    tabs, header = [], ""
    # A tab strip, not a bare header: more than one checkbox, or the right-hand
    # arrow. NOT the leading `←` on its own — that arrow is absent on the first
    # tab and in a narrow pane, and reading such a strip as a bare header turns a
    # two-Ask Set into no Ask Set at all.
    if sum(strip.count(c) for c in _CHECKBOX) > 1 or strip.endswith("→"):
        for tok in re.split(r"\s{2,}", strip.strip("← →").strip()):
            label = tok[1:].strip()
            # `✔ Submit` is the widget's own submit control, not a question — it
            # sits in the same strip and would otherwise be counted as a third
            # Ask in a two-Ask Set. Told apart by its glyph, which is never the
            # empty box a pending question tab carries.
            if tok[:1] in _CHECKBOX and label and not (tok[:1] != "☐" and label == "Submit"):
                tabs.append({"label": label, "checked": tok[:1] != "☐"})
    else:
        header = strip[1:].strip()
    return {"rows": rows, "options": [r["label"] for r in rows if not r["affordance"]],
            "cursor": cursor, "tabs": tabs, "header": header}


def _is_question_widget(text: str) -> bool:
    """True when the pane shows the AskUserQuestion widget (as opposed to a
    permission menu or a plain input box).

    The signature is the checkbox header, which is structural. The old one was
    the literal `"add notes"` — an affordance the current tmux renderer stopped
    painting, and nothing told us. With this False, the widget's body fell
    through to `_pane_input` and the phone painted **⚠ unsent text already in
    this session's input box** over the question itself, beside a 'clear the box'
    button that would have fired hundreds of BSpace into a live selector.
    """
    return bool(_pane_widget(text))


def _pane_question(text: str) -> str:
    """The prompt read off an AskUserQuestion widget: the lines between its
    checkbox header and the first following numbered option (blank lines and
    the box-art notes panel skipped). Used only when the tool_use hasn't flushed
    to the transcript yet, so the structured question in `_ask_of` is
    unavailable. Anchored on the LAST header — earlier frames are stale.

    '' means "no prompt read", whether the anchor was missing or the body under
    it was blank; the two are not told apart HERE because the distinction only
    matters to the caller that acts on it. `_ask_set` treats an unreadable prompt
    under a widget that IS on screen as `unmatched` rather than "Ask 1", since
    with two questions up "the first one" is a guess, and that guess is precisely
    ADR 0020's opening incident (q1's options drawn under q2's question)."""
    lines = text.split("\n")
    hdr = _widget_anchor(lines)
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


class _PaneRead(typing.NamedTuple):
    """ONE rendered pane, parsed every way the Board needs it — the only value
    type that may cross into the Ask layer as "what the screen shows".

    Every field is a derivation of the SAME `capture-pane` string, so this costs
    no second read (ADR 0014): `_board` captures once, calls `_read_pane` once,
    and hands the result on.

    It exists because the previous shape — `_ask_of(session_id, rows, widget,
    rendered)` — invited the failure class ADR 0020 is about. `rendered` had to
    be `_pane_question(pane)`, the EXTRACTED question text; passing the raw pane
    (the obvious mistake, and the one an agent makes first) is a `str` either
    way, so it type-checked, ran, matched nothing, and quietly produced an
    untappable Ask Set with no options and no complaint. An API where a wrong
    argument is silently absorbed is the same defect as a parser that defaults
    its cursor to 0. Now the argument is a `_PaneRead` or it is nothing, and
    `_ask_set` rejects anything else loudly.

      captured  did a capture-pane actually return a frame? This is the
                distinction between "nobody looked" and "we looked and saw no
                widget" — the second is a CONTRADICTION with a transcript that
                says an AskUserQuestion is pending, and contradictions must be
                reported, not defaulted away.
      widget    `_pane_widget` — the AskUserQuestion widget, or {}.
      question  `_pane_question` — the widget's rendered prompt, or ''. Only
                meaningful when `widget`; that gating lives here rather than at
                every call site.
      selector  `_parse_selector` — a numbered menu (permission prompt or the
                iTerm-era widget), or {}.
      unsent    `_pane_input` — text sitting in the free-text input box. Empty
                whenever a menu or widget owns the screen: their bodies are
                framed by the same rules the input box is, and reading one as
                unsent text is the false ⚠ of ADR 0020. Computed here so no
                caller can forget the guard.
    """

    captured: bool
    widget: dict
    question: str
    selector: dict
    unsent: str


_NO_PANE = _PaneRead(captured=False, widget={}, question="", selector={}, unsent="")


def _read_pane(text: str) -> _PaneRead:
    """Parse one captured frame every way the Board reads it (see `_PaneRead`).

    `text` is `_pane_contents`' output, and '' means the capture failed — which
    is `_NO_PANE`, not an empty screen: nothing was observed, so nothing may be
    concluded."""
    if not text:
        return _NO_PANE
    widget = _pane_widget(text)
    selector = _parse_selector(text)
    return _PaneRead(
        captured=True,
        widget=widget,
        question=_pane_question(text) if widget else "",
        selector=selector,
        unsent="" if (widget or selector) else _pane_input(text),
    )


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
    # cached_runs, never cached_all_runs: a Foreign Run is never Blocked, never
    # the Focus, and never in Rotation — there is no rendered pane to read its
    # blocker from and no Respond to answer it with, so a row here would only
    # make the queue lie (ADR 0012). It reaches the Board by its own path —
    # `_foreign_items`, on its own payload key.
    for r in cached_runs():
        sid = r.get("sessionId", "")
        snoozed = sid and _SNOOZE.get(sid, 0) > now
        lane = "snoozed" if snoozed else _lane_of(r)
        one = r.get("snippet", "")
        if lane in ("question", "approval"):
            ask, _, _ = _ask_of(sid)
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
        # One parse of the tail, two derivations: the **Ask** and the
        # **Scrollback** (ADR 0014 — the scrollback costs no second file read).
        rows = _tail_rows(focus["sessionId"])
        scrollback = _scrollback(rows)
        # None, not 0 — the payload never claims a cursor position nobody read
        # (ADR 0020: `cursor 0` was a default that drove keystrokes for months).
        cursor = None
        # ONE capture, parsed once, every derivation off the same string: the
        # menu, the widget, the rendered question and the input box (ADR 0014).
        pr = _read_pane(_pane_contents(focus["runId"]))
        sel, widget = pr.selector, pr.widget
        ask, options, askset = _ask_of(focus["sessionId"], rows, pr)
        lane = focus["lane"]
        if widget:
            lane = "question"                   # the rendered pane outranks _lane_of's guess
            if not ask:
                ask = pr.question               # tool_use hasn't flushed — read the prompt off-screen
        if sel:
            # Hybrid (ADR 0009): structured tool_use labels win when flushed; the
            # pane supplies them only when they're absent. The live cursor always
            # comes from the pane — the user may have arrow-keyed on the Mac.
            if not options:
                options = sel["options"]
            if len(sel["options"]) == len(options):
                cursor = sel["cursor"]
        if widget:
            # For the AskUserQuestion widget the cursor indexes `rows` — every row
            # the widget paints, its own affordances included — because that is
            # the sequence a Down key steps through. `_parse_selector`'s cursor
            # indexes options and is the wrong space here; `askSet[*].steps` is
            # already measured against this one. null when the frame painted no
            # cursor at all — a consumer must branch on that, never read it as 0
            # (ADR 0020: a defaulted 0 is what drove the wrong keystrokes), and
            # `askSet.tappable` is already False in that case.
            #
            # ONLY when the Ask Set reconciled. `cursor` is one field carrying two
            # incompatible spaces — rows for a widget, options for a menu — and
            # nothing on the wire says which. The Set is what tells a consumer it
            # is looking at row-space, so handing it row-space WITHOUT one is
            # handing it a number it will read in the other space: a stale widget
            # frame on screen while the pending tool_use has already moved on to
            # an approval leaves `askSet` {} and `options` filled from `sel`, and
            # the client's menu path then reads a row index as an option index.
            # That is ADR 0020's wrong-space bug in a transient window, so this
            # refuses rather than narrows it (ADR 0021: an unreadable position is
            # None, never a plausible number).
            cursor = widget["cursor"] if askset else None
        # A menu or widget owns the screen — there is no free-text input box to
        # mistake its body for unsent text (that false ⚠ was the original bug).
        # The guard lives in `_read_pane` so it cannot be forgotten here.
        pending = pr.unsent
        # An **Ask** is the blocker of a **Blocked** Run and nothing else
        # (CONTEXT.md). On an idle Run the prose-`?` regex only restates the last
        # turn, which the **Scrollback** now shows — a second copy, not new
        # information. Gated HERE, after the rendered pane has had its chance to
        # upgrade the lane to `question`, so a real blocker is never dropped.
        #
        # Against the *underlying* lane, not the displayed one: snooze masks the
        # real lane outright (`lane = "snoozed" if snoozed else _lane_of(r)`), so
        # a snoozed Run that is genuinely Blocked would otherwise lose its
        # blocker the moment you pinned it as the Focus — which is exactly when
        # you meant to answer it. Snooze orders the queue; it does not decide
        # whether a Run is Blocked.
        # `status == "waiting"` is precisely what `_lane_of` tests to return
        # question/approval, and it is already on the item — so this reads the
        # underlying lane without the second transcript read `_lane_of` would
        # cost, keeping the Ask and the Scrollback to one parse between them.
        blocked = lane in ("question", "approval") or (
            lane == "snoozed" and focus.get("status") == "waiting")
        if not blocked:
            ask, options, cursor, askset = "", [], None, {}
        # `options` (bare labels) is kept beside `askSet` on purpose: it is the
        # key the client reads today, and it still serves the permission menu,
        # which has no Ask Set. It now carries the CURRENT Ask's options only —
        # never the whole Set's concatenated, which is the bug (ADR 0020).
        focus = dict(focus, lane=lane, aiTitle=_ai_title(focus["sessionId"]),
                     scrollback=scrollback, ask=ask, options=options,
                     cursor=cursor, askSet=askset, pendingInput=pending, pinned=pinned)
    # serverDown distinguishes a dead tmux server (all Runs gone silently) from
    # an ordinary empty board. The web client does not render this yet — plumbing
    # only; the empty-state UI is a documented follow-up (ADR 0010).
    return {"focus": focus, "upnext": other, "watching": working,
            "snoozed": snoozed, "dormant": dormant,
            "foreign": _foreign_items(),
            # Managed-only, deliberately. These three numbers are the summary
            # line's triage arithmetic — "how much of this needs me" — and a
            # Foreign Run needs nothing from the phone, because nothing on the
            # phone can answer it. Its section carries its own count instead.
            "counts": {"needYou": len(order), "watching": len(working),
                       "dormant": len(dormant), "snoozed": len(snoozed)},
            "serverDown": _tmux_server_down()}


def _foreign_items() -> list[dict]:
    """The **Foreign Run** rows, newest activity first — the Board's own quiet
    section (ADR 0012).

    A separate key, never merged into `items`: the lanes above are triage, and a
    Foreign Run has no rendered pane to read a blocker from and no **Respond** to
    answer one with, so it is never **Blocked**, never the **Focus** and never in
    **Rotation**. Keeping it off `items` is what makes that structural rather
    than a rule every lane has to remember.

    The projection is a whitelist, not a copy: every handle onto a pane — `runId`,
    `attach` — is dropped rather than blanked, so a row here cannot be handed to
    anything that drives a Run even by mistake. `bridge` stays, because the
    **Remote Control bridge** is Anthropic's cloud rather than a terminal — the
    one genuine route onto a Foreign Run from a phone — and it is already
    validated against `_BRIDGE_RE` before it can become an href. `status` is
    likewise already whitelisted to `_STATUSES`.

    No `pri` and no snooze: both are triage state keyed by Session, and this
    section is outside the triage surface entirely.
    """
    rows = [{"sessionId": r.get("sessionId", ""),
             # The project, exactly as the queue rows title themselves — falling
             # back to the Session's opening ask when there is no dir to name.
             "title": (r.get("dir") or "").rstrip("/").split("/")[-1] or r.get("title", ""),
             "dir": r.get("dir", ""),
             "status": r.get("status", ""),
             "bridge": r.get("bridge", ""),
             "updatedAt": r.get("updatedAt"),
             "one": r.get("snippet", "")}
            for r in cached_foreign_runs()]
    rows.sort(key=lambda it: -(it["updatedAt"] or 0))
    return rows


def _board_payload(focus_sid: str = "") -> tuple[bytes, str]:
    """Board JSON + ETag. No wall-clock in the body — the client formats ages
    from raw `updatedAt`, so an unchanged board yields a stable ETag/304."""
    body = json.dumps(_board(focus_sid), separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


def _recoverable_payload() -> tuple[bytes, str]:
    """Resumable-Session list + ETag. Served fresh like the board — the set
    shifts as Runs start/stop and cwds come and go — but is stable within one
    open, so the ETag still lets the picker's refetch 304 when nothing changed.

    The recovery set (slice 02) rides along: `_recovery_set_size` clusters the
    candidate mtimes, the top `preselectCount` rows gain `preselect: true` for
    the picker to pre-tick, and the count feeds the intake badge (slice 04).
    One call feeds the whole picker."""
    sessions = _recoverable_sessions()
    count = _recovery_set_size([row["mtime"] for row in sessions])
    for row in sessions[:count]:
        row["preselect"] = True
    body = json.dumps({"sessions": sessions, "preselectCount": count},
                      separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_WEB_FILES = {"board.html": "text/html; charset=utf-8",
              "board.js": "text/javascript; charset=utf-8",
              # Its own file, and served like any other: it runs from the <head>
              # before the body paints, which neither board.js (loaded last) nor
              # an inline <script> (dropped by the CSP above) can do. ADR 0019.
              "theme.js": "text/javascript; charset=utf-8"}


MAX_BODY_BYTES = 4096
_API_POSTS = ("/api/launch", "/api/resume", "/api/recover", "/api/close", "/api/transfer",
              "/api/respond", "/api/clear", "/api/priority", "/api/snooze")


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
        if path == "/theme.js":
            self._serve_web("theme.js")
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
        if path == "/api/recoverable":
            # Read-only, like the board — no token gate. Recover only *lists*
            # here; the resume it feeds is the gated POST (slice 03).
            body, etag = _recoverable_payload()
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
        elif path == "/api/transfer":
            self._handle_transfer(body)
        elif path == "/api/resume":
            self._handle_resume(body)
        elif path == "/api/recover":
            self._handle_recover(body)
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
            # Through `_read_pane`, never `_pane_input` raw: a widget's body sits
            # between the same horizontal rules the input box does, so the raw
            # reader hands back the QUESTION as "unsent text". The phone then
            # asks "this session already has unsent text … send anyway?" over a
            # live Ask, and OK means `force` — text sent into a selector, which
            # ADR 0020 measured as silently discarded and answered by whatever
            # row the cursor was on. `/api/board` already gates `pendingInput`
            # this way; this path was the one that did not, and disagreeing with
            # itself is worse than either answer.
            existing = _read_pane(_pane_contents(run_id)).unsent
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
        workdir, message = _resume_guard(session_id)
        if not workdir:
            self._fail(400, message)
            return
        try:
            run_id = launch_run(workdir, resume_id=session_id)
        except subprocess.CalledProcessError as e:
            self._fail(500, f"tmux failed: {e}")
            return
        invalidate_runs()
        self._json(200, {"ok": True, "runId": run_id, "message": f"resumed {session_id}"})

    def _handle_recover(self, body: dict) -> None:
        """**Recover** (ADR 0013): resume a batch of Sessions as Managed Runs,
        one at a time, returning a per-Session result in input order.

        A fan-out over the same guard + launch_run /api/resume runs for one
        Session, made partial-failure tolerant: each member's guards are
        re-checked fresh (a dir can vanish or a Session go live since the
        picker's GET), and a member that fails — bad id, no transcript, dir
        gone, already live, tmux error — is reported and skipped, never fatal to
        the rest. Sequential on purpose: the server is threaded, so N parallel
        resumes would race on tmux window creation.

        No Focus is grabbed — the endpoint only returns the new Run ids; the
        client leaves them in the queue (Rotation: new work never steals the
        Focus). The request is refused (400) only when it is structurally
        malformed — `sessionIds` missing or not a list; a non-string or
        non-UUID *member* is a failed row, not a 400. The runs cache is
        invalidated once after the loop, never per member.
        """
        session_ids = body.get("sessionIds")
        if not isinstance(session_ids, list):
            self._fail(400, "sessionIds must be a list")
            return
        results = []
        for sid in session_ids:
            session_id = sid.strip() if isinstance(sid, str) else ""
            workdir, message = _resume_guard(session_id)
            if not workdir:
                results.append({"sessionId": session_id, "ok": False, "message": message})
                continue
            try:
                run_id = launch_run(workdir, resume_id=session_id)
            except subprocess.CalledProcessError as e:
                results.append({"sessionId": session_id, "ok": False,
                                "message": f"tmux failed: {e}"})
                continue
            results.append({"sessionId": session_id, "ok": True, "runId": run_id})
        invalidate_runs()   # once after the loop, never per member
        self._json(200, results)

    def _handle_close(self, body: dict) -> None:
        if not close_run(self._str(body, "runId")):
            self._fail(400, "could not close run")
            return
        self._json(200, {"ok": True, "message": "closed"})

    def _handle_transfer(self, body: dict) -> None:
        """**Transfer** a **Foreign Run**: kill it, wait, resume its Session.

        Takes a `sessionId` and never a pid — the pid is re-derived server-side
        from the live walk (see `transfer_session`), which is the whole reason
        this is not a kill-anything endpoint.

        Ungated, like launch / resume / close. The shared secret exists because
        **Respond** can approve tool calls (ADR 0007); Transfer approves nothing —
        it ends one Run and starts another on the same Session.
        """
        session_id = self._str(body, "sessionId")
        try:
            run_id = transfer_session(session_id)
        except TransferFailed as e:
            if e.orphaned:
                # The one failure the server records on its own account: whoever
                # tapped is away from the Mac, and the response may not survive
                # the trip back. The log is then the only trace of a Session left
                # with nothing running.
                sys.stderr.write(f"TRANSFER ORPHANED {sanitize_log(session_id)}: "
                                 f"{sanitize_log(str(e))}\n")
            self._json(e.status, {"ok": False, "orphaned": e.orphaned, "message": str(e)})
            return
        self._json(200, {"ok": True, "runId": run_id,
                         "message": "transferred — now a managed run"})

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
