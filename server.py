#!/usr/bin/env python3
"""Spawn and manage Claude Code sessions on a Mac from a phone over Tailscale.

Vocabulary (see CONTEXT.md): a **Session** is the durable thread Claude Code
identifies by `sessionId`; a **Run** is one `claude` process executing it,
concretely an iTerm pane. The launcher only ever creates and destroys Runs.

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
from urllib.parse import urlparse
import glob
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time

try:
    from tasks import TASKS
except ImportError:
    TASKS = []  # no private task config -> generic launcher only

HOST = os.environ.get("CLAUDE_LAUNCHER_HOST", "0.0.0.0")
PORT = int(os.environ.get("CLAUDE_LAUNCHER_PORT", "8765"))
DEFAULT_DIR = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_DEFAULT_DIR", "~"))
PROJECTS_ROOT = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_PROJECTS_ROOT", "~/projects"))
COMMAND = os.environ.get("CLAUDE_LAUNCHER_COMMAND", "cl")
REMOTE = os.environ.get("CLAUDE_LAUNCHER_REMOTE", "1").strip().lower() not in (
    "0", "false", "no", "off", "",
)

TASKS_BY_ID = {t["id"]: t for t in TASKS}
TASK_LABELS = {t["id"]: t["label"] for t in TASKS}

# A seed is typed on a phone, not piped. Anything longer is a paste accident, and
# a NUL cannot cross an argv boundary at all.
#
# Kept well under MAX_BODY_BYTES (4096): a seed of N characters can reach 4N bytes
# as UTF-8, so 800 always fits inside the body cap. Otherwise a long jot would trip
# a confusing "body too large" before this check ever ran.
MAX_SEED_CHARS = 800


def _validate_tasks(tasks) -> None:
    """Fail at import, not on tap, when tasks.py has a typo."""
    for t in tasks:
        if "exec" in t and not (isinstance(t["exec"], list) and t["exec"]
                                and all(isinstance(a, str) for a in t["exec"])):
            raise ValueError(f"task {t.get('id')!r}: exec must be a non-empty list of strings")
        if "exec" in t and "command" in t:
            raise ValueError(f"task {t.get('id')!r}: a Dispatch has exec, a Task has command — not both")


_validate_tasks(TASKS)


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


def launch_iterm(workdir: str, prompt: str | None = None, task_id: str | None = None,
                 resume_id: str | None = None) -> str:
    """Open an iTerm pane running the launch command in workdir; return its Run id.

    Named tasks pass their slash-command as ``prompt`` and their id as
    ``task_id``; the id is stamped on the pane as user.cl_task so the live
    list can label it. Resume passes ``resume_id`` (a Session's sessionId)
    to spawn ``cl --resume``. Generic launches pass none of them.

    The AppleScript returns `id of current session` — the new pane's UUID,
    which is the same id `list_runs` keys rows by. The client needs it to
    paint an optimistic row: a Run is not visible to `list_runs` until
    `claude` shows up in `ps` (1-3s later), so without this correlation key
    a launch looks like it did nothing.
    """
    cmd = _resume_cmd(workdir, resume_id) if resume_id else _launch_cmd(workdir, prompt)
    stamp = ""
    if task_id:
        stamp = f'\n        set variable named "user.cl_task" to {applescript_quote(task_id)}'
    script = f'''
tell application "iTerm"
    activate
    if (count of windows) = 0 then
        create window with default profile
    else
        tell current window to create tab with default profile
    end if
    tell current session of current window
        write text {applescript_quote(cmd)}{stamp}
        return id
    end tell
end tell
'''
    run_id = _osascript(script).strip()
    return run_id if _UUID_RE.match(run_id) else ""


def dispatch(workdir: str, argv: list[str], seed: str = "", log: str | None = None) -> None:
    """Run a preset command detached, appending `seed` as one argv element.

    A **Dispatch** is not a **Run**: no `claude`, no Session, no iTerm pane —
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


def applescript_quote(s: str) -> str:
    s = _CONTROL_CHAR_RE.sub("", s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + s + '"'


def sanitize_log(s: str) -> str:
    return _CONTROL_CHAR_RE.sub("?", s).replace("\n", "?").replace("\r", "?")


# --- live Run discovery (iTerm panes running `claude`) ------------------------

# Both a Run id (an iTerm pane) and a Session id (Claude's sessionId) are
# 36-char UUIDs. This checks *shape only* and belongs to neither: the two are
# distinguished by which field they arrive in, never by their format.
_UUID_RE = re.compile(r"^[0-9A-Fa-f-]{36}$")

# The only statuses Claude Code writes. Whitelisted because `status` lands in
# a `st-<status>` CSS class on the client — it becomes structure, not text, so
# it is the one field `textContent` cannot make safe.
_STATUSES = ("busy", "waiting", "idle")

# Emit one line per iTerm pane: id <US> tty <US> name <US> cl_task. US (0x1f)
# can't appear in any field, so splitting is unambiguous. An unset user.cl_task
# comes back as `missing value`, so coerce it to "" first.
_LIST_SCRIPT = """
if application "iTerm" is running then
  tell application "iTerm"
    set sep to (ASCII character 31)
    set out to ""
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          tell s
            set tag to (variable named "user.cl_task")
            if tag is missing value then set tag to ""
            set out to out & (id) & sep & (tty) & sep & (name) & sep & tag & linefeed
          end tell
        end repeat
      end repeat
    end repeat
    return out
  end tell
end if
return ""
"""

_CLOSE_SCRIPT = """
if application "iTerm" is running then
  tell application "iTerm"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is "%s" then
            close s
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
end if
return "notfound"
"""


def _parse_iterm_panes(out: str) -> list[tuple[str, str, str, str]]:
    """(run_id, tty_basename, name, cl_task) for each _LIST_SCRIPT line."""
    rows = []
    for line in out.split("\n"):
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        rid, tty, name, tag = parts
        rows.append((rid.strip(), os.path.basename(tty.strip()), name.strip(), tag.strip()))
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
        meta[pid] = {
            "cwd": j.get("cwd", ""),
            "status": status if status in _STATUSES else "",
            "remote": bool(j.get("bridgeSessionId")),
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


def _clean_title(name: str) -> str:
    """Strip iTerm's status glyph + trailing '(profile)' from a pane title."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return re.sub(r"^[\W_]+", "", s).strip()


def _osascript(script: str) -> str:
    return subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=True
    ).stdout


def _ps_output() -> str:
    return subprocess.run(
        ["ps", "-axo", "pid=,tty=,command="], capture_output=True, text=True, check=True
    ).stdout


def list_runs() -> list[dict]:
    """Live `claude` Runs visible as iTerm panes, newest activity first.

    ``updatedAt`` ships raw (epoch ms). Formatting it server-side into "47m"
    would make an idle Run's payload change every minute, defeating the ETag
    and every "nothing changed, skip the re-render" check downstream.
    """
    try:
        panes = _parse_iterm_panes(_osascript(_LIST_SCRIPT))
        ps_out = _ps_output()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    claude = _parse_claude_ttys(ps_out)
    meta = _run_meta()
    rows = []
    for rid, tty, name, tag in panes:
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
            "updatedAt": m.get("updatedAt"),
            "snippet": _last_msg(session_id),
            "starting": starting,
        })
    # Starting Runs first — they are the newest thing that happened, and they
    # have no updatedAt to sort by. Then most-recently-active, as the Claude
    # app orders them.
    rows.sort(key=lambda r: (not r["starting"], -(r["updatedAt"] or 0)))
    return rows


# One AppleScript walk costs ~140ms (84ms iTerm + 53ms `ps`). Memoize briefly so
# the burst-poll after a launch, the periodic poll, and a second open tab all
# collapse into one walk. Mutations invalidate it — a closed Run must vanish on
# the very next poll, not up to a TTL later.
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
    """Close the iTerm pane with this Run id, but only if it's a live claude one."""
    if not _UUID_RE.match(run_id):
        return False
    if run_id not in {r["id"] for r in cached_runs()}:
        return False
    try:
        ok = _osascript(_CLOSE_SCRIPT % run_id).strip() == "ok"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    if ok:
        invalidate_runs()
    return ok


# --- page ---------------------------------------------------------------------

CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; "
       "style-src 'unsafe-inline'; base-uri 'none'")

INDEX_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cl</title>
<style>
:root{--bg:#0e0f12;--fg:#d6d6d6;--dim:#6b7280;--prompt:#7fcd9b;--accent:#e8b65a;--input:#1a1c20;
  --err:#e06c6c}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1rem;font:15px/1.5 "SF Mono","Menlo","Consolas",ui-monospace,monospace;
  background:var(--bg);color:var(--fg);min-height:100vh}
main{max-width:520px;margin:0 auto}
.label{color:var(--dim);margin-bottom:1.25rem}
.label b{color:var(--fg);font-weight:600}
.cmd{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:0;row-gap:.25rem;white-space:pre}
.prompt{color:var(--prompt)}
.input{flex:1 1 12ch;min-width:8ch;background:var(--input);color:var(--accent);
  border:0;border-bottom:1px dashed var(--dim);font:inherit;padding:.15rem .4rem;outline:0;
  caret-color:var(--accent)}
.input::placeholder{color:#4b5563}
.input:focus{border-bottom-color:var(--accent)}
.task{display:flex;align-items:stretch;gap:.5rem;margin-bottom:.75rem}
.task .input{flex:1 1 auto;min-width:8ch;padding:.4rem .6rem}
.task .go{margin-top:0}
/* A seed for a fire-and-forget agent is a sentence or three, not a filename, so
   it gets a real box and the button drops below it. */
.task.multiline{flex-direction:column;align-items:stretch;margin-bottom:1.1rem}
.task.multiline .go{align-self:flex-start}
textarea.input{min-height:5.5rem;resize:vertical;line-height:1.5;white-space:pre-wrap;
  border:1px solid #262a30;border-bottom:1px dashed var(--dim);border-radius:2px}
.orsep{color:var(--dim);font-size:12px;margin:1.5rem 0 1.1rem;padding-top:1.2rem;
  border-top:1px solid #1f2227;letter-spacing:.05em}
.go{margin-top:1.5rem;background:transparent;border:1px solid var(--fg);color:var(--fg);
  font:inherit;padding:.5rem 1.5rem;cursor:pointer;letter-spacing:.05em}
.go:hover,.go:active{background:var(--fg);color:var(--bg)}
.go:disabled{opacity:.5;cursor:default}
.hint{color:var(--dim);margin-top:1rem;font-size:13px}
.hint code{color:var(--fg)}
.sessions{margin-top:2.5rem}
.shead{color:var(--dim);font-size:13px;margin-bottom:.5rem;letter-spacing:.05em}
.empty{color:var(--dim);font-size:13px;margin-top:2.5rem}
.srow{display:flex;align-items:flex-start;gap:.6rem;padding:.5rem 0;border-top:1px solid #1f2227}
.x{background:transparent;border:1px solid var(--dim);color:var(--dim);font:inherit;
  line-height:1;padding:.15rem .55rem;cursor:pointer;border-radius:2px}
.x:hover,.x:active{border-color:var(--err);color:var(--err)}
.x:disabled{opacity:.4;cursor:default}
.smeta{min-width:0;flex:1}
.sname{color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sdir{color:var(--dim);font-size:12px;margin-top:.15rem}
.ssnip{color:var(--dim);font-size:12px;margin-top:.3rem;opacity:.8;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pending .sname{color:var(--dim)}
.sage{color:var(--prompt)}
.st-busy{color:var(--prompt)}
.st-waiting{color:var(--accent)}
.st-idle{color:var(--dim)}
.rc{color:var(--prompt);font-size:11px;border:1px solid #2a2d33;border-radius:2px;
  padding:0 .35rem;margin-left:.5rem;vertical-align:middle}
#toast{position:fixed;left:1rem;right:1rem;bottom:1rem;max-width:520px;margin:0 auto;
  padding:.7rem .9rem;font-size:13px;border-radius:3px;cursor:pointer;
  background:var(--input);border:1px solid var(--dim);color:var(--fg)}
#toast.err{border-color:var(--err);color:var(--err)}
#toast[hidden]{display:none}
</style></head>
<body>
<main>
  <div class="label"><b>claude-launcher</b> &middot; launch &amp; manage runs</div>
  <noscript><div class="empty">this page needs JavaScript &mdash; it drives a JSON API.</div></noscript>
  {tasks}
  <div class="cmd"><span class="prompt">$ </span>cd {projects_root}/<input class="input" id="dir" autocomplete="off" placeholder="subdir"> &amp;&amp; cl</div>
  <button class="go" id="launch" type="button">launch</button>
  <div class="hint">blank &rarr; <code>{default_dir}</code></div>
  <div class="cmd" style="margin-top:1.5rem"><span class="prompt">$ </span>cl --resume <input class="input" id="sid" autocomplete="off" placeholder="sessionId"></div>
  <button class="go" id="resume" type="button">resume</button>
  <div class="hint">a closed session's id &mdash; from the Claude app</div>
  <section class="sessions" id="runs"></section>
</main>
<div id="toast" hidden></div>
<script src="app.js"></script>
</body></html>
"""

INDEX_TEMPLATE = (
    INDEX_HTML
    .replace("{projects_root}", html.escape(_display_path(PROJECTS_ROOT)))
    .replace("{default_dir}", html.escape(_display_path(DEFAULT_DIR)))
)

# The client never assigns innerHTML. Every value from the API enters the DOM as
# a text node, so no input can escape into markup — a structural guarantee, not a
# per-field escaping habit. `status` is the sole exception (it becomes a CSS
# class), which is why the server whitelists it against _STATUSES.
APP_JS = r"""
"use strict";
const $ = (id) => document.getElementById(id);
const LABELS = {busy: "working", waiting: "waiting", idle: "idle"};
const POLL_MS = 4000;      // steady cadence while the page is visible
const BURST_MS = 400;      // while waiting for a just-launched Run to appear
const START_DEADLINE = 10000;

let lastEtag = null;
let lastRuns = [];
let timer = null;
let inflight = false;
const pending = new Map();   // runId -> true, Runs launched but not yet in `ps`

function ago(ms) {
  if (!ms) return "";
  const m = (Date.now() - ms) / 60000;
  if (m < 1) return "now";
  if (m < 60) return Math.floor(m) + "m";
  if (m < 1440) return Math.floor(m / 60) + "h";
  return Math.floor(m / 1440) + "d";
}

let toastTimer = null;
function toast(msg, ok) {
  const t = $("toast");
  t.textContent = msg;
  t.className = ok ? "" : "err";
  t.hidden = false;
  clearTimeout(toastTimer);
  // Success fades; errors stay until tapped, because you are about to look
  // at the list and wonder why nothing spawned.
  if (ok) toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function rowNode(r) {
  // Two flavours of not-yet-running look identical on purpose: `pending` is a
  // Run we launched but the server cannot see yet, `starting` is one it sees
  // but Claude Code has not registered. Both are "starting…".
  const starting = r.pending || r.starting;
  const row = el("div", starting ? "srow pending" : "srow");

  const x = el("button", "x", "×");
  x.type = "button";
  x.title = "close";
  // A pending Run has no pane the server will admit to yet, so /api/close
  // would 400. Once it lists the pane, closing works even while starting.
  if (r.pending) x.disabled = true;
  else x.addEventListener("click", () => closeRun(r.id));
  row.appendChild(x);

  const meta = el("div", "smeta");
  const name = el("div", "sname");
  name.appendChild(document.createTextNode(starting ? "starting…" : (r.title || "claude")));
  if (r.remote) name.appendChild(el("span", "rc", "remote"));
  meta.appendChild(name);

  const bits = [];
  if (r.dir) bits.push(el("span", null, r.dir));
  if (r.updatedAt) {
    const age = el("span", "sage", ago(r.updatedAt));
    age.dataset.at = String(r.updatedAt);
    bits.push(age);
  }
  if (r.status) bits.push(el("span", "st st-" + r.status, LABELS[r.status] || r.status));
  const sub = el("div", "sdir");
  bits.forEach((b, i) => {
    if (i) sub.appendChild(document.createTextNode(" · "));
    sub.appendChild(b);
  });
  meta.appendChild(sub);

  if (r.snippet) meta.appendChild(el("div", "ssnip", r.snippet));
  row.appendChild(meta);
  return row;
}

function render(runs) {
  const host = $("runs");
  const shown = runs.slice();
  const live = new Set(runs.map((r) => r.id));
  for (const id of pending.keys()) {
    if (!live.has(id)) shown.unshift({id: id, title: "starting…", pending: true});
  }
  host.replaceChildren();
  if (!shown.length) {
    host.appendChild(el("div", "empty", "no live runs"));
    return;
  }
  host.appendChild(el("div", "shead", "open runs · tap × to close"));
  shown.forEach((r) => host.appendChild(rowNode(r)));
}

// A 304 means the data is unchanged, but wall-clock moved: only the relative
// ages need touching, and only they get touched.
function refreshAges() {
  document.querySelectorAll("[data-at]").forEach((n) => {
    n.textContent = ago(Number(n.dataset.at));
  });
}

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    const headers = lastEtag ? {"If-None-Match": lastEtag} : {};
    const r = await fetch("api/runs", {headers: headers});
    if (r.status === 304) { refreshAges(); return; }
    if (!r.ok) return;
    lastEtag = r.headers.get("ETag");
    const body = await r.json();
    lastRuns = body.runs || [];
    lastRuns.forEach((r2) => pending.delete(r2.id));
    render(lastRuns);
  } catch (e) {
    // network blip; the next tick retries
  } finally {
    inflight = false;
  }
}

// Chained timeout, never setInterval: a 140ms server behind a slow cellular RTT
// would otherwise stack overlapping requests. Burst until nothing is starting.
function settling() {
  return pending.size > 0 || lastRuns.some((r) => r.starting);
}

function schedule() {
  clearTimeout(timer);
  if (document.hidden) return;
  timer = setTimeout(tick, settling() ? BURST_MS : POLL_MS);
}

async function tick() {
  await poll();
  schedule();
}

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  try {
    return await r.json();
  } catch (e) {
    return {ok: false, message: "http " + r.status};
  }
}

// A Run is invisible to the server until `claude` reaches `ps`. Paint it
// optimistically, burst-poll until it materialises, give up loudly.
function watch(runId) {
  if (!runId) return;
  pending.set(runId, true);
  render(lastRuns);
  setTimeout(() => {
    if (pending.delete(runId)) {
      toast("run failed to start", false);
      render(lastRuns);
    }
  }, START_DEADLINE);
  schedule();
}

function afterLaunch(res, seedInput) {
  toast(res.message, !!res.ok);
  if (!res.ok) return;                 // keep the seed text so it can be fixed
  if (seedInput) seedInput.value = "";
  watch(res.runId);
}

async function launchDir() {
  afterLaunch(await api("api/launch", {dir: $("dir").value.trim()}), null);
}

async function launchTask(button) {
  const seed = button.parentElement.querySelector("input, textarea");
  const body = {task: button.dataset.task};
  if (seed) body.input = seed.value.trim();
  afterLaunch(await api("api/launch", body), seed);
}

async function resumeSession() {
  const box = $("sid");
  const res = await api("api/resume", {sessionId: box.value.trim()});
  afterLaunch(res, box);
}

async function closeRun(runId) {
  const res = await api("api/close", {runId: runId});
  toast(res.message, !!res.ok);
  await poll();      // closing a pane is synchronous; no burst needed
  schedule();
}

$("launch").addEventListener("click", launchDir);
$("resume").addEventListener("click", resumeSession);
$("toast").addEventListener("click", () => { $("toast").hidden = true; });
document.querySelectorAll("[data-task]").forEach((b) => {
  b.addEventListener("click", () => launchTask(b));
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else tick();
});

tick();
"""


def _render_tasks() -> str:
    """One-tap task buttons (and inline seed boxes) from tasks.py, or '' if no
    tasks are configured. Ends with a divider setting them off from the generic
    launcher below."""
    if not TASKS:
        return ""
    out = []
    for t in TASKS:
        tid = html.escape(t["id"])
        label = html.escape(t["label"])
        kind = t.get("input", "none")
        placeholder = html.escape(t.get("placeholder", f"{t['label']}…"))
        multiline = kind == "textarea"
        out.append(f'<div class="task{" multiline" if multiline else ""}">')
        if multiline:
            out.append(f'<textarea class="input" rows="4" autocomplete="off" '
                       f'placeholder="{placeholder}"></textarea>')
        elif kind == "text":
            out.append(f'<input class="input" autocomplete="off" placeholder="{placeholder}">')
        out.append(f'<button class="go" type="button" data-task="{tid}">{label}</button></div>')
    out.append('<div class="orsep">or launch a dir</div>')
    return "".join(out)


def _render_index() -> str:
    return INDEX_TEMPLATE.replace("{tasks}", _render_tasks())


def _runs_payload() -> tuple[bytes, str]:
    """Serialized Run list plus its ETag."""
    body = json.dumps({"runs": cached_runs()}, separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


MAX_BODY_BYTES = 4096
_API_POSTS = ("/api/launch", "/api/resume", "/api/close")


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
            self._send(200, _render_index().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, APP_JS.encode("utf-8"), "text/javascript; charset=utf-8")
            return
        if path == "/api/runs":
            body, etag = _runs_payload()
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", "application/json; charset=utf-8", {"ETag": etag})
                return
            self._send(200, body, "application/json; charset=utf-8", {"ETag": etag})
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
            run_id = launch_iterm(workdir)
        except subprocess.CalledProcessError as e:
            self._fail(500, f"osascript failed: {e}")
            return
        invalidate_runs()
        self._json(200, {"ok": True, "runId": run_id,
                         "message": f"launched in {_display_path(workdir)}"})

    def _seed(self, task: dict, body: dict) -> str | None:
        """The seed for a task that takes one, or None when it is unusable."""
        if task.get("input", "none") == "none":
            return ""
        seed = self._str(body, "input")
        if len(seed) > MAX_SEED_CHARS or "\x00" in seed:
            return None
        return seed

    def _launch_task(self, task_id: str, body: dict) -> None:
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
            run_id = launch_iterm(workdir, prompt, task_id=task["id"])
        except subprocess.CalledProcessError as e:
            self._fail(500, f"osascript failed: {e}")
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
            run_id = launch_iterm(workdir, resume_id=session_id)
        except subprocess.CalledProcessError as e:
            self._fail(500, f"osascript failed: {e}")
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
    if sys.platform != "darwin":
        sys.exit("claude-launcher: macOS only (uses AppleScript + iTerm2)")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"claude-launcher listening on {HOST}:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
