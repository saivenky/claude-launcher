#!/usr/bin/env python3
"""Prototype: inject input into a live Run's iTerm pane (the WRITE half).

Resolves a claude pid (or sessionId prefix) -> its tty -> its iTerm session
id, then sends input via AppleScript. This is the only lever the Launcher
has to "respond": it rides the Launcher transport (local AppleScript),
independent of the Remote Control bridge.

Usage:
  inject.py --pid 12345 --text "your answer here"     # type a line + submit
  inject.py --sid abcd1234 --text "..." --no-submit    # type, don't submit
  inject.py --pid 12345 --key down --key down --key enter   # drive a selector
  inject.py --list                                     # show pid -> pane map
"""
import argparse
import subprocess
import sys

LIST_SCRIPT = r'''
if application "iTerm" is running then
  tell application "iTerm"
    set sep to (ASCII character 31)
    set out to ""
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          tell s to set out to out & (id) & sep & (tty) & linefeed
        end repeat
      end repeat
    end repeat
    return out
  end tell
end if
return ""
'''

KEYS = {
    "enter": '(ASCII character 13)',
    "return": '(ASCII character 13)',
    "esc": '(ASCII character 27)',
    "down": '(ASCII character 27) & "[B"',
    "up": '(ASCII character 27) & "[A"',
    "right": '(ASCII character 27) & "[C"',
    "left": '(ASCII character 27) & "[D"',
    "space": '" "',
    "tab": '(ASCII character 9)',
}


def osascript(script):
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True).stdout


def pane_map():
    """tty_basename -> iTerm session id."""
    out = osascript(LIST_SCRIPT)
    m = {}
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) != 2:
            continue
        rid, tty = parts
        m[tty.strip().split("/")[-1]] = rid.strip()
    return m


def claude_ttys():
    """tty_basename -> pid for claude processes."""
    out = subprocess.run(["ps", "-Ao", "pid=,tty=,comm="],
                         capture_output=True, text=True).stdout
    m = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, tty, comm = parts
        if not tty.startswith("ttys"):
            continue
        if comm.split("/")[-1] != "claude" and "claude" not in comm:
            continue
        try:
            m[tty] = int(pid_s)
        except ValueError:
            pass
    return m


def resolve(pid=None, sid=None):
    """-> (session_id, pid, tty) or (None, ...)."""
    import glob
    import json
    import os
    if sid and not pid:
        for fn in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
            try:
                j = json.load(open(fn))
            except Exception:
                continue
            if str(j.get("sessionId", "")).startswith(sid):
                pid = j.get("pid")
                break
    panes = pane_map()
    for tty, p in claude_ttys().items():
        if p == pid:
            return panes.get(tty), pid, tty
    return None, pid, None


def _act(session_id, action):
    # iTerm won't address `session id "X"` directly (-1728); iterate + match.
    return osascript(f'''
    tell application "iTerm"
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (id of s) is "{session_id}" then
              tell s to {action}
              return "ok"
            end if
          end repeat
        end repeat
      end repeat
    end tell
    return "notfound"''')


def send_text(session_id, text, submit=True):
    nl = "" if submit else " newline no"
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    _act(session_id, f'write text "{esc}"{nl}')


def send_key(session_id, key):
    _act(session_id, f'write text ({KEYS[key]}) newline no')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--sid")
    ap.add_argument("--text")
    ap.add_argument("--no-submit", action="store_true")
    ap.add_argument("--key", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        panes = pane_map()
        for tty, pid in sorted(claude_ttys().items()):
            print(f"pid={pid:>6} tty={tty:<12} pane={panes.get(tty, '?')}")
        return

    session_id, pid, tty = resolve(a.pid, a.sid)
    if not session_id:
        print(f"could not resolve pane (pid={pid} tty={tty})", file=sys.stderr)
        sys.exit(1)
    print(f"target: pid={pid} tty={tty} pane={session_id}")
    if a.text is not None:
        send_text(session_id, a.text, submit=not a.no_submit)
        print(f"sent text ({'submit' if not a.no_submit else 'no-submit'}): {a.text!r}")
    for k in a.key:
        send_key(session_id, k)
        print(f"sent key: {k}")


if __name__ == "__main__":
    main()
