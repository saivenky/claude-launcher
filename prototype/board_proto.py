#!/usr/bin/env python3
"""Render a Board UI prototype from live reader.py classification.

FIRST CUT — a starting point to react to, not a spec. Controls are mock
(no wiring). Injects two synthetic Blocked runs so the Respond affordances
are visible even when nothing is actually blocked right now.

    python3 board_proto.py > board.html
"""
import html
import reader

LANE_META = {
    "WAITING:QUESTION": ("BLOCKED · question", "q"),
    "WAITING:PERMISSION": ("BLOCKED · approval", "p"),
    "YOUR-MOVE": ("YOUR MOVE", "m"),
    "WORKING": ("WORKING", "w"),
    "UNKNOWN": ("UNKNOWN", "u"),
}
ORDER = ["WAITING:QUESTION", "WAITING:PERMISSION", "YOUR-MOVE", "WORKING", "UNKNOWN"]

MOCK = [
    {"lane": "WAITING:PERMISSION", "pid": 40111, "sid": "9a1b2c3d",
     "dir": "~/projects/api", "status": "waiting", "stop": "tool_use", "mock": True,
     "blocker": "approve? Bash: rm -rf ./build && npm run clean",
     "opts": ["Yes", "Yes, don't ask again", "No"]},
    {"lane": "WAITING:QUESTION", "pid": 40222, "sid": "7f8e9d0c",
     "dir": "~/notes", "status": "waiting", "stop": "tool_use", "mock": True,
     "blocker": "Q: Which store should assignment history live in?",
     "opts": ["Postgres (Recommended)", "Event log", "Both"]},
]


def controls(r):
    lane = r["lane"]
    if lane in ("WAITING:QUESTION", "WAITING:PERMISSION"):
        btns = "".join(f'<button class="opt">{html.escape(o)}</button>'
                       for o in r.get("opts", ["Yes", "No"]))
        return (f'<div class="ctl">{btns}'
                f'<input class="ti" placeholder="…or type a reply">'
                f'<button class="send">respond</button></div>')
    if lane == "YOUR-MOVE":
        return ('<div class="ctl"><input class="ti" placeholder="type your next instruction">'
                '<button class="send">send</button></div>')
    if lane == "WORKING":
        return ('<div class="ctl dim"><input class="ti" placeholder="queue a message for the next turn">'
                '<button class="send">queue</button></div>')
    return ""


def card(r):
    mock = ' <span class="tag">mock</span>' if r.get("mock") else ""
    return f'''<div class="card lane-{LANE_META[r['lane']][1]}">
  <div class="top"><span class="dir">{html.escape(r['dir'])}</span>
    <span class="chip st-{r['status'] or 'na'}">{r['status'] or '—'}</span>
    <span class="sid">{r['sid']}{mock}</span></div>
  <div class="blk">→ {html.escape(r['blocker'])}</div>
  {controls(r)}
</div>'''


def main():
    rows = [reader.classify(x) for x in reader.live_runs()] + MOCK
    by = {}
    for r in rows:
        by.setdefault(r["lane"], []).append(r)
    n_block = len(by.get("WAITING:QUESTION", [])) + len(by.get("WAITING:PERMISSION", []))
    n_move = len(by.get("YOUR-MOVE", []))

    sections = ""
    for lane in ORDER:
        rs = by.get(lane)
        if not rs:
            continue
        label = LANE_META[lane][0]
        sections += (f'<h2 class="lh lane-{LANE_META[lane][1]}">{label}'
                     f'<span class="ct">{len(rs)}</span></h2>'
                     + "".join(card(r) for r in rs))

    print(f'''<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>claude board — prototype</title>
<style>
:root{{--bg:#0d0f12;--panel:#14181d;--fg:#d6dde3;--dim:#6b7681;--line:#232a31;
--accent:#e6b450;--q:#7fd1c4;--p:#e6b450;--m:#8ab4f8;--w:#6b7681;--red:#e06c75;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
.wrap{{max-width:760px;margin:0 auto;padding:16px}}
header{{display:flex;align-items:baseline;gap:12px;padding:4px 0 12px;
border-bottom:1px solid var(--line);margin-bottom:14px}}
h1{{font-size:15px;margin:0;letter-spacing:.5px}}
.sub{{color:var(--dim);font-size:12px}}
.sub b{{color:var(--accent)}}
h2.lh{{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--dim);
margin:18px 0 8px;display:flex;align-items:center;gap:8px}}
h2 .ct{{background:var(--line);color:var(--fg);border-radius:9px;padding:0 7px;font-size:11px}}
.lane-q{{color:var(--q)}} .lane-p{{color:var(--p)}} .lane-m{{color:var(--m)}} .lane-w{{color:var(--w)}}
.card{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);
border-radius:8px;padding:10px 12px;margin-bottom:8px}}
.card.lane-q{{border-left-color:var(--q)}} .card.lane-p{{border-left-color:var(--p)}}
.card.lane-m{{border-left-color:var(--m)}}
.top{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.dir{{font-weight:600}}
.chip{{font-size:10px;padding:1px 6px;border-radius:4px;background:var(--line);color:var(--dim)}}
.st-busy{{color:#8ab4f8}} .st-waiting{{color:var(--accent)}} .st-idle{{color:var(--dim)}}
.sid{{margin-left:auto;color:var(--dim);font-size:11px}}
.tag{{background:var(--red);color:#000;border-radius:3px;padding:0 4px;font-size:9px;margin-left:4px}}
.blk{{color:var(--fg);opacity:.85;font-size:12.5px;margin:7px 0 9px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ctl{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}}
.ctl.dim{{opacity:.55}}
.opt{{background:transparent;border:1px solid var(--line);color:var(--fg);
border-radius:6px;padding:4px 9px;font:inherit;font-size:12px;cursor:pointer}}
.opt:hover{{border-color:var(--accent);color:var(--accent)}}
.ti{{flex:1;min-width:130px;background:#0a0c0f;border:1px solid var(--line);color:var(--fg);
border-radius:6px;padding:5px 8px;font:inherit;font-size:12px}}
.send{{background:var(--accent);color:#000;border:0;border-radius:6px;padding:5px 12px;
font:inherit;font-size:12px;font-weight:600;cursor:pointer}}
.note{{color:var(--dim);font-size:11px;margin-top:20px;border-top:1px solid var(--line);padding-top:10px}}
</style></head><body><div class=wrap>
<header><h1>◆ claude board</h1>
<span class=sub><b>{n_block}</b> blocked · <b>{n_move}</b> your move · prototype</span></header>
{sections}
<div class=note>first-cut mock · controls are not wired · red "mock" tags are synthetic
Blocked runs (nothing is actually blocked right now) · everything else is your live sessions.</div>
</div></body></html>''')


if __name__ == "__main__":
    main()
