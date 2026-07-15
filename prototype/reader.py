#!/usr/bin/env python3
"""Prototype: deep-observe classifier for the triage board.

For every live Run (from ~/.claude/sessions/<pid>.json) it reads the
transcript TAIL and classifies the Run into a board lane, extracting the
concrete blocker. This is the READ half of "manage/assist/unblock" — pure
read, safe to run against real sessions.

Lanes (priority order):
  WAITING:QUESTION     AskUserQuestion pending  -> render questions/options
  WAITING:PERMISSION   tool_use pending + waiting -> "approve <tool> <summary>"
  WORKING              status=busy               -> leave alone
  YOUR-MOVE            end_turn / idle           -> free-text respond
  DEAD/UNKNOWN         couldn't classify
"""
import glob
import json
import os

SESS = os.path.expanduser("~/.claude/sessions")
PROJ = os.path.expanduser("~/.claude/projects")
WINDOW = 262144


def live_runs():
    out = []
    for fn in glob.glob(os.path.join(SESS, "*.json")):
        try:
            j = json.load(open(fn))
        except (OSError, ValueError):
            continue
        if isinstance(j.get("pid"), int):
            out.append(j)
    return out


def transcript(session_id):
    m = glob.glob(os.path.join(PROJ, "*", session_id + ".jsonl"))
    return m[0] if m else ""


def tail_rows(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > WINDOW:
                fh.seek(size - WINDOW)
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


def blocks(o):
    c = (o.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def last_assistant(rows):
    for o in reversed(rows):
        if o.get("type") == "assistant" and blocks(o):
            return o
    return None


def resolved_tool_ids(rows):
    """tool_use_ids that already have a tool_result (i.e. not pending)."""
    done = set()
    for o in rows:
        if o.get("type") == "user":
            for b in blocks(o):
                if b.get("type") == "tool_result" and b.get("tool_use_id"):
                    done.add(b["tool_use_id"])
    return done


def summarize_tool(b):
    name = b.get("name", "?")
    inp = b.get("input") or {}
    if name == "Bash":
        return f"Bash: {inp.get('command', '')[:80]}"
    if name in ("Edit", "Write", "Read"):
        return f"{name}: {inp.get('file_path', '')}"
    key = next((k for k in ("command", "path", "url", "pattern") if k in inp), None)
    return f"{name}: {str(inp.get(key, ''))[:80]}" if key else name


def question_text(b):
    inp = b.get("input") or {}
    qs = inp.get("questions") or []
    out = []
    for q in qs:
        opts = [o.get("label", "?") for o in (q.get("options") or [])]
        out.append(f"Q: {q.get('question', '')[:90]}  opts={opts}")
    return " | ".join(out) or "AskUserQuestion (no parsed questions)"


def classify(run):
    sid = run.get("sessionId", "")
    status = run.get("status", "")
    path = transcript(sid)
    rows = tail_rows(path) if path else []
    la = last_assistant(rows)
    stop = ((la or {}).get("message") or {}).get("stop_reason")
    done = resolved_tool_ids(rows)

    pending_tu = None
    if la:
        for b in blocks(la):
            if b.get("type") == "tool_use" and b.get("id") not in done:
                pending_tu = b

    # last conversational text (for YOUR-MOVE preview)
    last_text = ""
    if la:
        for b in blocks(la):
            if b.get("type") == "text" and b.get("text", "").strip():
                last_text = b["text"].strip().replace("\n", " ")

    if pending_tu and pending_tu.get("name") == "AskUserQuestion":
        lane, blocker = "WAITING:QUESTION", question_text(pending_tu)
    elif pending_tu and status == "waiting":
        lane, blocker = "WAITING:PERMISSION", "approve? " + summarize_tool(pending_tu)
    elif pending_tu:
        # dangling tool_use but not flagged waiting -> mid-execution
        lane, blocker = "WORKING", summarize_tool(pending_tu)
    elif status == "busy":
        lane, blocker = "WORKING", "(running)"
    elif stop == "end_turn" or status == "idle":
        q = " [ends on '?']" if last_text.rstrip().endswith("?") else ""
        lane, blocker = "YOUR-MOVE", (last_text[:100] + q) if last_text else "(idle)"
    else:
        lane, blocker = "UNKNOWN", f"status={status} stop={stop}"

    return {
        "lane": lane,
        "pid": run.get("pid"),
        "sid": sid[:8],
        "dir": run.get("cwd", "").replace(os.path.expanduser("~"), "~"),
        "status": status,
        "stop": stop,
        "blocker": blocker,
    }


PRIORITY = {"WAITING:QUESTION": 0, "WAITING:PERMISSION": 1, "YOUR-MOVE": 2,
            "WORKING": 3, "UNKNOWN": 4, "DEAD": 5}


def main():
    rows = [classify(r) for r in live_runs()]
    rows.sort(key=lambda r: PRIORITY.get(r["lane"], 9))
    print(f"{'LANE':<18} {'pid':>6} {'sid':<9} {'status':<8} dir / blocker")
    print("-" * 100)
    for r in rows:
        print(f"{r['lane']:<18} {r['pid']:>6} {r['sid']:<9} {r['status']:<8} {r['dir']}")
        print(f"{'':<18} {'':>6} {'':<9} {'':<8} -> {r['blocker']}")


if __name__ == "__main__":
    main()
