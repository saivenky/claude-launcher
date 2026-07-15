#!/usr/bin/env python3
"""Spawn a throwaway claude Run and observe its rendered TUI via AppleScript.

Proves two capabilities in one shot:
  - can we READ a pane's rendered screen (not just the transcript)?
  - does the pane map + spawn round-trip work for a fresh Run?

Usage: probe_live.py spawn <dir>      -> opens claude, prints new pane id
       probe_live.py read <pane_id>   -> dump visible screen contents
"""
import subprocess
import sys
import time


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr)
    return r.stdout.strip()


def spawn(workdir):
    script = f'''
    tell application "iTerm"
      set w to (create window with default profile)
      tell current session of w
        write text "cd {workdir} && exec claude"
        return id
      end tell
    end tell
    '''
    return osa(script)


def read(pane_id):
    # iTerm won't address `session id "X"` directly (-1728); iterate + match.
    script = f'''
    tell application "iTerm"
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (id of s) is "{pane_id}" then return (contents of s)
          end repeat
        end repeat
      end repeat
    end tell
    return "PANE-NOT-FOUND"
    '''
    return osa(script)


def main():
    cmd = sys.argv[1]
    if cmd == "spawn":
        pid = spawn(sys.argv[2])
        print(f"PANE={pid}")
        # poll until the screen shows something claude-ish
        for _ in range(20):
            time.sleep(1)
            c = read(pid)
            if c and ("claude" in c.lower() or "trust" in c.lower() or "?" in c or ">" in c):
                print("---- screen (settled) ----")
                print(c)
                return
        print("---- screen (timeout) ----")
        print(read(pid))
    elif cmd == "read":
        print(read(sys.argv[2]))


if __name__ == "__main__":
    main()
