#!/usr/bin/env python3
"""Trigger a new iTerm2 tab running `cl` (interactive Claude). Bound to all
interfaces so a Tailscale device (e.g. phone) can hit it."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import os
import re
import subprocess
import sys

if sys.platform != "darwin":
    sys.exit("claude-launcher: macOS only (uses AppleScript + iTerm2)")

HOST = os.environ.get("CLAUDE_LAUNCHER_HOST", "0.0.0.0")
PORT = int(os.environ.get("CLAUDE_LAUNCHER_PORT", "8765"))
DEFAULT_DIR = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_DEFAULT_DIR", "~"))
PROJECTS_ROOT = os.path.expanduser(os.environ.get("CLAUDE_LAUNCHER_PROJECTS_ROOT", "~/projects"))
COMMAND = os.environ.get("CLAUDE_LAUNCHER_COMMAND", "cl")


def resolve_dir(dir_param: str | None) -> str:
    if not dir_param:
        return DEFAULT_DIR
    candidate = os.path.realpath(os.path.join(PROJECTS_ROOT, dir_param))
    projects_real = os.path.realpath(PROJECTS_ROOT)
    if not candidate.startswith(projects_real + os.sep) or not os.path.isdir(candidate):
        raise ValueError(f"dir must be an existing subdir of {PROJECTS_ROOT}")
    return candidate


def launch_iterm(workdir: str) -> None:
    cmd = f"cd {shell_quote(workdir)} && {COMMAND}"
    script = f'''
tell application "iTerm"
    activate
    if (count of windows) = 0 then
        create window with default profile
    else
        tell current window to create tab with default profile
    end if
    tell current session of current window to write text {applescript_quote(cmd)}
end tell
'''
    subprocess.run(["osascript", "-e", script], check=True)


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def applescript_quote(s: str) -> str:
    s = _CONTROL_CHAR_RE.sub("", s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + s + '"'


def sanitize_log(s: str) -> str:
    return _CONTROL_CHAR_RE.sub("?", s).replace("\n", "?").replace("\r", "?")


INDEX_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'">
<title>cl</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:2rem;
  display:flex;flex-direction:column;gap:1rem;align-items:center;
  background:#111;color:#eee;min-height:100vh;justify-content:center}
button,a.btn{font-size:1.4rem;padding:1.2rem 2rem;border-radius:12px;
  border:none;background:#e85d3a;color:#fff;text-decoration:none;
  width:100%;max-width:360px;text-align:center;cursor:pointer}
input{font-size:1.1rem;padding:0.8rem 1rem;border-radius:10px;border:1px solid #444;
  background:#222;color:#eee;width:100%;max-width:360px;box-sizing:border-box}
.row{display:flex;flex-direction:column;gap:0.6rem;align-items:center;width:100%}
small{color:#888}
</style></head>
<body>
  <h2 style="margin:0">claude-launcher</h2>
  <small>opens iTerm2 tab and runs <code>cl</code></small>
  <form class="row" method="post" action="/launch">
    <input name="dir" placeholder="subdir of projects root (blank = default dir)" autocomplete="off">
    <button type="submit">go</button>
  </form>
</body></html>
"""


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
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/launch":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(404, "not found\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/launch":
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
        self._handle_launch(qs)

    def _handle_launch(self, qs: dict) -> None:
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

    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        sys.stderr.write(f"{self.address_string()} - {sanitize_log(msg)}\n")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"claude-launcher listening on {HOST}:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
