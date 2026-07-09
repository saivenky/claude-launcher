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

tasks.py is optional and private; without it you just get the generic
launcher (and resume)."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import glob
import html
import json
import os
import re
import subprocess
import sys
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
                 resume_id: str | None = None) -> None:
    """Open an iTerm tab running the launch command in workdir.

    Named tasks pass their slash-command as ``prompt`` and their id as
    ``task_id``; the id is stamped on the pane as user.cl_task so the live
    list can label it. Resume passes ``resume_id`` (a Session's sessionId)
    to spawn ``cl --resume``. Generic launches pass none of them.
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
    end tell
end tell
'''
    subprocess.run(["osascript", "-e", script], check=True)


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
        sid, tty, name, tag = parts
        rows.append((sid.strip(), os.path.basename(tty.strip()), name.strip(), tag.strip()))
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
        meta[pid] = {
            "cwd": j.get("cwd", ""),
            "status": j.get("status", ""),
            "remote": bool(j.get("bridgeSessionId")),
            "sessionId": j.get("sessionId", ""),
            "updatedAt": j.get("updatedAt"),
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
    """Opening user prompt — title fallback when the tab-title is generic."""
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
    """Most-recent message text — the 'where is this session' preview the web shows.

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


def _last_active(updated_at: object) -> str:
    """updatedAt (ms epoch) -> relative last-activity: now / 47m / 2h / 4d.

    Mirrors the recency the Claude web UI shows, so rows line up with it.
    """
    if not isinstance(updated_at, (int, float)):
        return ""
    mins = (time.time() - updated_at / 1000) / 60
    if mins < 1:
        return "now"
    if mins < 60:
        return f"{int(mins)}m"
    if mins < 1440:
        return f"{int(mins / 60)}h"
    return f"{int(mins / 1440)}d"


def _clean_title(name: str) -> str:
    """Strip iTerm's status glyph + trailing '(profile)' from a pane title."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return re.sub(r"^[\W_]+", "", s).strip()


def _osascript(script: str) -> str:
    return subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=True
    ).stdout


def list_runs() -> list[dict]:
    """Live `claude` Runs visible as iTerm panes, in tab order."""
    try:
        sessions = _parse_iterm_panes(_osascript(_LIST_SCRIPT))
        ps_out = subprocess.run(
            ["ps", "-axo", "pid=,tty=,command="],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    claude = _parse_claude_ttys(ps_out)
    meta = _run_meta()
    rows = []
    for sid, tty, name, tag in sessions:
        pid = claude.get(tty)
        if pid is None:
            continue
        m = meta.get(pid, {})
        if tag:
            title = TASK_LABELS.get(tag, tag)
        else:
            title = _clean_title(name)
            if not title or title == "Claude Code":
                title = _first_user_msg(m.get("sessionId", "")) or title or "claude"
        updated = m.get("updatedAt")
        rows.append({
            "id": sid,
            "title": title,
            "dir": m.get("cwd", ""),
            "status": m.get("status", ""),
            "remote": m.get("remote", False),
            "active": _last_active(updated),
            "snippet": _last_msg(m.get("sessionId", "")),
            "sessionId": m.get("sessionId", ""),
            "_updated": updated if isinstance(updated, (int, float)) else 0,
        })
    rows.sort(key=lambda r: r["_updated"], reverse=True)
    return rows


def _live_session_ids() -> set[str]:
    """sessionIds of Sessions with a live Run — the resume live-guard set.

    Resuming a Session that already has a live Run would put two Runs on one
    transcript, so /resume refuses any id in here.
    """
    return {sid for s in list_runs() if (sid := s.get("sessionId"))}


def close_run(run_id: str) -> bool:
    """Close the iTerm pane with this Run id, but only if it's a live claude one."""
    if not _UUID_RE.match(run_id):
        return False
    if run_id not in {r["id"] for r in list_runs()}:
        return False
    try:
        return _osascript(_CLOSE_SCRIPT % run_id).strip() == "ok"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


INDEX_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'">
<title>cl</title>
<style>
:root{--bg:#0e0f12;--fg:#d6d6d6;--dim:#6b7280;--prompt:#7fcd9b;--accent:#e8b65a;--input:#1a1c20}
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
.task .input{flex:1 1 auto;min-width:8ch;background:var(--input);color:var(--accent);
  border:0;border-bottom:1px dashed var(--dim);font:inherit;padding:.4rem .6rem;outline:0;
  caret-color:var(--accent)}
.task .input::placeholder{color:#4b5563}
.task .input:focus{border-bottom-color:var(--accent)}
.task .go{margin-top:0}
.orsep{color:var(--dim);font-size:12px;margin:1.5rem 0 1.1rem;padding-top:1.2rem;
  border-top:1px solid #1f2227;letter-spacing:.05em}
.go{margin-top:1.5rem;background:transparent;border:1px solid var(--fg);color:var(--fg);
  font:inherit;padding:.5rem 1.5rem;cursor:pointer;letter-spacing:.05em}
.go:hover,.go:active{background:var(--fg);color:var(--bg)}
.hint{color:var(--dim);margin-top:1rem;font-size:13px}
.hint code{color:var(--fg)}
.sessions{margin-top:2.5rem}
.shead{color:var(--dim);font-size:13px;margin-bottom:.5rem;letter-spacing:.05em}
.empty{color:var(--dim);font-size:13px;margin-top:2.5rem}
.srow{display:flex;align-items:flex-start;gap:.6rem;padding:.5rem 0;border-top:1px solid #1f2227}
.srow form{margin:0}
.x{background:transparent;border:1px solid var(--dim);color:var(--dim);font:inherit;
  line-height:1;padding:.15rem .55rem;cursor:pointer;border-radius:2px}
.x:hover,.x:active{border-color:#e06c6c;color:#e06c6c}
.smeta{min-width:0;flex:1}
.sname{color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sdir{color:var(--dim);font-size:12px;margin-top:.15rem}
.ssnip{color:var(--dim);font-size:12px;margin-top:.3rem;opacity:.8;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sage{color:var(--prompt)}
.st-busy{color:var(--prompt)}
.st-waiting{color:var(--accent)}
.st-idle{color:var(--dim)}
.rc{color:var(--prompt);font-size:11px;border:1px solid #2a2d33;border-radius:2px;
  padding:0 .35rem;margin-left:.5rem;vertical-align:middle}
</style></head>
<body>
<main>
  <div class="label"><b>claude-launcher</b> &middot; launch &amp; manage sessions</div>
  {tasks}
  <form method="post" action="/launch">
    <div class="cmd"><span class="prompt">$ </span>cd {projects_root}/<input class="input" name="dir" autocomplete="off" placeholder="subdir"> &amp;&amp; cl</div>
    <button class="go" type="submit">launch</button>
    <div class="hint">blank &rarr; <code>{default_dir}</code></div>
  </form>
  <form method="post" action="/resume">
    <div class="cmd"><span class="prompt">$ </span>cl --resume <input class="input" name="session_id" autocomplete="off" placeholder="sessionId"></div>
    <button class="go" type="submit">resume</button>
    <div class="hint">a closed session's id &mdash; from the Claude app</div>
  </form>
  <section class="sessions">{sessions}</section>
</main>
</body></html>
"""

INDEX_TEMPLATE = (
    INDEX_HTML
    .replace("{projects_root}", html.escape(_display_path(PROJECTS_ROOT)))
    .replace("{default_dir}", html.escape(_display_path(DEFAULT_DIR)))
)


def _render_tasks() -> str:
    """One-tap task buttons (and inline text inputs) from tasks.py, or '' if no
    tasks are configured. Ends with a divider setting them off from the generic
    launcher below."""
    if not TASKS:
        return ""
    out = []
    for t in TASKS:
        tid = html.escape(t["id"])
        label = html.escape(t["label"])
        out.append('<form class="task" method="post" action="/launch">')
        out.append(f'<input type="hidden" name="task" value="{tid}">')
        if t.get("input") == "text":
            out.append(
                f'<input class="input" name="input" autocomplete="off" '
                f'placeholder="{label}…">'
            )
        out.append(f'<button class="go" type="submit">{label}</button></form>')
    out.append('<div class="orsep">or launch a dir</div>')
    return "".join(out)


def _render_runs() -> str:
    rows = list_runs()
    if not rows:
        return '<div class="empty">no live runs</div>'
    out = ['<div class="shead">open runs &middot; tap &times; to close</div>']
    for s in rows:
        rid = html.escape(s["id"])
        head = html.escape(s["title"])
        st = s.get("status", "")
        label = {"busy": "working", "waiting": "waiting", "idle": "idle"}.get(st, st)
        rc = ' <span class="rc">remote</span>' if s.get("remote") else ""
        bits = []
        if s["dir"]:
            bits.append(html.escape(_display_path(s["dir"])))
        if s.get("active"):
            bits.append(f'<span class="sage">{html.escape(s["active"])}</span>')
        if st:
            bits.append(f'<span class="st st-{html.escape(st)}">{html.escape(label)}</span>')
        meta = " &middot; ".join(bits)
        snip = html.escape(s.get("snippet", ""))
        snip_html = f'<div class="ssnip">{snip}</div>' if snip else ""
        out.append(
            '<div class="srow">'
            '<form method="post" action="/close">'
            f'<input type="hidden" name="run_id" value="{rid}">'
            '<button class="x" type="submit" title="close">&times;</button></form>'
            f'<div class="smeta"><div class="sname">{head}{rc}</div>'
            f'<div class="sdir">{meta}</div>{snip_html}</div>'
            '</div>'
        )
    return "".join(out)


def _render_index() -> str:
    return (
        INDEX_TEMPLATE
        .replace("{tasks}", _render_tasks())
        .replace("{sessions}", _render_runs())
    )


MAX_BODY_BYTES = 4096


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _same_origin_ok(self) -> tuple[bool, str]:
        origin = self.headers.get("Origin")
        if not origin:
            return True, ""
        if origin == "null":
            return False, "origin=null"
        host = (self.headers.get("Host") or "").lower()
        allowed = {f"http://{host}", f"https://{host}"}
        if origin.lower() in allowed:
            return True, ""
        return False, f"origin={origin!r} host={host!r}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, _render_index(), "text/html; charset=utf-8")
            return
        if parsed.path in ("/launch", "/resume", "/close"):
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(404, "not found\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/launch", "/close", "/resume"):
            self._send(404, "not found\n")
            return
        ok, detail = self._same_origin_ok()
        if not ok:
            dbg = " ".join(
                f"{h}={self.headers.get(h)!r}"
                for h in ("Origin", "Host", "Referer", "Sec-Fetch-Site", "Sec-Fetch-Mode", "User-Agent")
                if self.headers.get(h)
            )
            sys.stderr.write(f"403 cross-origin: {sanitize_log(detail)} | {sanitize_log(dbg)}\n")
            self._send(403, f"cross-origin blocked ({detail})\n")
            return
        body_qs: dict = {}
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(400, "bad content-length\n")
            return
        if length > MAX_BODY_BYTES:
            self._send(413, "body too large\n")
            return
        if length > 0:
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype and ctype != "application/x-www-form-urlencoded":
                self._send(415, "unsupported media type\n")
                return
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            body_qs = parse_qs(raw)
        qs = parse_qs(parsed.query)
        for k, v in body_qs.items():
            qs.setdefault(k, v)
        if parsed.path == "/close":
            self._handle_close(qs)
        elif parsed.path == "/resume":
            self._handle_resume(qs)
        else:
            self._handle_launch(qs)

    def _handle_launch(self, qs: dict) -> None:
        task_id = (qs.get("task") or [""])[0]
        if task_id:
            self._launch_task(task_id, qs)
            return
        dir_param = (qs.get("dir") or [None])[0]
        try:
            workdir = resolve_dir(dir_param)
        except ValueError as e:
            self._send(400, f"{e}\n")
            return
        try:
            launch_iterm(workdir)
        except subprocess.CalledProcessError as e:
            self._send(500, f"osascript failed: {e}\n")
            return
        self._send(200, f"launched in {workdir}\n")

    def _launch_task(self, task_id: str, qs: dict) -> None:
        task = TASKS_BY_ID.get(task_id)
        if not task:
            self._send(400, "unknown task\n")
            return
        prompt = task["command"]
        if task.get("input") == "text":
            seed = (qs.get("input") or [""])[0].strip()
            if seed:
                prompt = f"{prompt} {seed}"
        workdir = os.path.expanduser(task["workdir"])
        if not os.path.isdir(workdir):
            self._send(400, f"workdir does not exist: {workdir}\n")
            return
        try:
            launch_iterm(workdir, prompt, task_id=task["id"])
        except subprocess.CalledProcessError as e:
            self._send(500, f"osascript failed: {e}\n")
            return
        self._send(200, f"launched {task['id']}\n")

    def _handle_resume(self, qs: dict) -> None:
        session_id = (qs.get("session_id") or [""])[0].strip()
        if not _UUID_RE.match(session_id):
            self._send(400, "invalid session id\n")
            return
        if not _transcript_path(session_id):
            self._send(400, "no such session\n")
            return
        if session_id in _live_session_ids():
            self._send(400, "already live\n")
            return
        workdir = _session_cwd(session_id)
        if not workdir or not os.path.isdir(workdir):
            self._send(400, "session's dir is gone\n")
            return
        try:
            launch_iterm(workdir, resume_id=session_id)
        except subprocess.CalledProcessError as e:
            self._send(500, f"osascript failed: {e}\n")
            return
        self._send(200, f"resumed {session_id}\n")

    def _handle_close(self, qs: dict) -> None:
        run_id = (qs.get("run_id") or [""])[0]
        if not close_run(run_id):
            self._send(400, "could not close run\n")
            return
        # 303 -> GET / so the refreshed list reflects the close.
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

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
