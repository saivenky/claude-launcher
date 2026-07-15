#!/usr/bin/env python3
"""Board prototype, take 2: FOCUS MODE.

One hero card in focus (session + full run-up context rendered as markdown +
the ask + reply), everything else in an "up next" queue. Context is the real,
untruncated last assistant message. Markdown -> HTML is done server-side in
Python (no JS dep, CSP-safe) — the same path the real Board would use.

    python3 focus_proto.py > focus.html
"""
import html
import json
import re
import reader

MOCK_QUEUE = [
    {"lane": "WAITING:PERMISSION", "dir": "~/projects/api", "sid": "9a1b2c3d",
     "one": "approve? Bash: rm -rf ./build && npm run clean", "kind": "approval", "mock": True},
    {"lane": "WAITING:QUESTION", "dir": "~/notes", "sid": "7f8e9d0c",
     "one": "Which store should assignment history live in?", "kind": "3 options", "mock": True},
]

# mock per-session priority (real feature: a UI control, stored by sessionId).
# high floats to the top of its tier and stretches its Dormant clock.
PRI = {"api": 0, "docs": 2}   # 0 high · 1 normal · 2 low
PRI_LABEL = {0: "high", 1: "normal", 2: "low"}


# --- tiny markdown -> HTML (headers, bold, code, lists, tables, paragraphs) ---
def _inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(text):
    lines = text.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl+2}>{_inline(m.group(2))}</h{lvl+2}>")
            i += 1
            continue
        # table: header row + separator
        if "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and "|" in lines[i]:
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            rows = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table>")
            continue
        # list
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            lis = "".join(f"<li>{_inline(it)}</li>" for it in items)
            out.append(f"<ul>{lis}</ul>")
            continue
        # paragraph (gather until blank)
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\s*([-*]|\d+\.)\s)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)


def ai_title(run):
    """One-line 'what this session is about' — Claude Code's aiTitle, else opening prompt."""
    sid = run.get("sessionId", "")
    path = reader.transcript(sid)
    title = ""
    if path:
        try:
            for line in open(path):
                if '"aiTitle"' in line:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("type") == "ai-title" and o.get("aiTitle"):
                        title = o["aiTitle"]
        except OSError:
            pass
    return title or reader._first_user_msg(sid)


def full_context(run):
    path = reader.transcript(run.get("sessionId", ""))
    rows = reader.tail_rows(path) if path else []
    la = reader.last_assistant(rows)
    if not la:
        return "", ""
    txt = "\n".join(b.get("text", "") for b in reader.blocks(la) if b.get("type") == "text").strip()
    # the ask = last sentence/line ending in '?'
    qs = re.findall(r"[^\n?]*\?", txt)
    ask = qs[-1].strip()[-160:] if qs else ""
    return txt, ask


def opt_letters(text):
    return re.findall(r"\*\*\(([A-D])\)", text) or re.findall(r"\(([A-D])\)\s", text)


def title_of(run):
    return run.get("cwd", "").rstrip("/").split("/")[-1] or "~"


def main():
    runs = [(r, reader.classify(r)) for r in reader.live_runs()]
    # focus = a real run that ends on a question (best demo of long context)
    focus = None
    for run, c in runs:
        ctx, ask = full_context(run)
        if ask and c["lane"] == "YOUR-MOVE":
            focus = (run, c, ctx, ask)
            break
    if not focus:
        run, c = runs[0]
        ctx, ask = full_context(run)
        focus = (run, c, ctx, ask)
    frun, fc, fctx, fask = focus
    ftitle = ai_title(frun)

    letters = opt_letters(fctx)
    optbtns = "".join(f'<button class="opt">{l}</button>' for l in letters)

    # rotation: needs-you (up next) vs watching (working, resurfaces later)
    entries = list(MOCK_QUEUE)
    for run, c in runs:
        if run is frun:
            continue
        ctx, ask = full_context(run)
        entries.append({"lane": c["lane"], "dir": c["dir"], "sid": c["sid"],
                        "one": (ask or c["blocker"]), "kind": "reply" if c["lane"] == "YOUR-MOVE" else "", "mock": False})
    for e in entries:
        e["pri"] = PRI.get(title_of({"cwd": e["dir"]}), 1)
    tier = {"WAITING:QUESTION": 0, "WAITING:PERMISSION": 0, "YOUR-MOVE": 1, "WORKING": 3}
    upnext = sorted([e for e in entries if e["lane"] != "WORKING"],
                    key=lambda q: (tier.get(q["lane"], 9), q["pri"]))   # tier, then priority
    watching = [e for e in entries if e["lane"] == "WORKING"]

    def qrow(q):
        badge = {"WAITING:QUESTION": "question", "WAITING:PERMISSION": "approval",
                 "YOUR-MOVE": "your move", "WORKING": "working"}.get(q["lane"], "")
        cls = {"WAITING:QUESTION": "q", "WAITING:PERMISSION": "p",
               "YOUR-MOVE": "m", "WORKING": "w"}.get(q["lane"], "w")
        mock = ' <span class="tag">mock</span>' if q.get("mock") else ""
        flag = ' <span class="flag">⚑</span>' if q.get("pri") == 0 else ""
        kind = f'<span class="kind">{q["kind"]}</span>' if q.get("kind") else ""
        return (f'<button class="qrow lane-{cls}"><span class="qbadge">{badge}</span>'
                f'<span class="qdir">{flag}{html.escape(title_of({"cwd": q["dir"]}))}{mock}</span>'
                f'<span class="qone">{html.escape(q["one"][:70])}</span>{kind}</button>')

    up_html = "".join(qrow(q) for q in upnext)
    watch_html = "".join(qrow(q) for q in watching)
    blocked = sum(1 for q in upnext if q["lane"].startswith("WAITING")) + (1 if fc["lane"].startswith("WAITING") else 0)

    print(f'''<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>claude board — focus</title>
<style>
:root{{--bg:#0c0e11;--panel:#14181d;--panel2:#0a0c0f;--fg:#dde3e9;--dim:#6b7681;
--line:#232a31;--accent:#e6b450;--q:#7fd1c4;--p:#e6b450;--m:#8ab4f8;--w:#5b646d;--red:#e06c75;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}}
.wrap{{max-width:640px;margin:0 auto;padding:14px 14px 40px}}
header{{display:flex;align-items:baseline;gap:10px;padding-bottom:12px}}
h1{{font-size:14px;margin:0;letter-spacing:.5px}}
.sub{{color:var(--dim);font-size:12px;margin-left:auto}}
.sub b{{color:var(--accent)}}

.focus{{background:var(--panel);border:1px solid var(--line);border-top:2px solid var(--m);
border-radius:10px;overflow:hidden}}
.fhead{{display:flex;align-items:center;gap:8px;padding:11px 14px;border-bottom:1px solid var(--line)}}
.fdir{{font-weight:600;font-size:14px}}
.fbadge{{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:var(--m);
border:1px solid var(--line);border-radius:4px;padding:1px 6px}}
.fmeta{{margin-left:auto;color:var(--dim);font-size:11px}}
.about{{padding:8px 14px;border-bottom:1px solid var(--line);background:var(--panel2);
font-size:12.5px;color:var(--fg)}}
.albl{{color:var(--dim);text-transform:uppercase;font-size:9px;letter-spacing:1px;margin-right:9px}}

.ctx{{max-height:46vh;overflow-y:auto;padding:6px 16px 12px;font-size:13px;
border-bottom:1px solid var(--line);background:linear-gradient(var(--panel),var(--panel))}}
.ctx h3,.ctx h4,.ctx h5,.ctx h6{{font-size:12px;letter-spacing:.4px;color:var(--accent);
margin:14px 0 6px;text-transform:none}}
.ctx p{{margin:8px 0}}
.ctx strong{{color:#fff}}
.ctx code{{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
padding:0 4px;font-size:12px;color:var(--q)}}
.ctx ul{{margin:6px 0;padding-left:18px}} .ctx li{{margin:3px 0}}
.ctx table{{border-collapse:collapse;margin:8px 0;font-size:12px;width:100%}}
.ctx th,.ctx td{{border:1px solid var(--line);padding:3px 7px;text-align:left}}
.ctx th{{color:var(--dim);font-weight:600}}

.ask{{padding:12px 16px;background:var(--panel2)}}
.ask .lbl{{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--accent)}}
.ask .qtext{{font-size:13.5px;margin-top:4px;color:#fff}}

.respond{{padding:12px 16px}}
.opts{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}}
.opt{{background:transparent;border:1px solid var(--line);color:var(--fg);border-radius:6px;
padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}}
.opt:hover{{border-color:var(--accent);color:var(--accent)}}
.actions{{display:flex;align-items:center;gap:8px;margin-top:9px}}
.grow{{flex:1}}
.prisel{{font-size:11.5px;color:var(--dim);cursor:pointer}}
.prisel b{{color:var(--accent)}}
.ghost{{background:transparent;border:1px solid var(--line);color:var(--dim);border-radius:6px;
padding:4px 10px;font:inherit;font-size:11.5px;cursor:pointer}}
.ghost:hover{{border-color:var(--dim);color:var(--fg)}}
.flag{{color:var(--accent)}}
.replyrow{{display:flex;gap:6px}}
.ti{{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:7px;padding:8px 10px;font:inherit;font-size:13px}}
.send{{background:var(--accent);color:#000;border:0;border-radius:7px;padding:8px 16px;
font:inherit;font-size:13px;font-weight:700;cursor:pointer}}

.qhead{{display:flex;align-items:center;gap:8px;margin:22px 4px 8px;
font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--dim)}}
.qhead .ct{{background:var(--line);border-radius:9px;padding:0 7px}}
.qrow{{display:flex;align-items:center;gap:9px;width:100%;text-align:left;
background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--w);
border-radius:8px;padding:9px 11px;margin-bottom:6px;color:var(--fg);font:inherit;cursor:pointer}}
.qrow:hover{{border-color:var(--dim)}}
.qrow.lane-q{{border-left-color:var(--q)}} .qrow.lane-p{{border-left-color:var(--p)}}
.qrow.lane-m{{border-left-color:var(--m)}}
.qbadge{{font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);
min-width:58px}}
.lane-q .qbadge{{color:var(--q)}} .lane-p .qbadge{{color:var(--p)}} .lane-m .qbadge{{color:var(--m)}}
.qdir{{font-weight:600;font-size:12.5px;white-space:nowrap}}
.qone{{color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}}
.kind{{color:var(--dim);font-size:10.5px}}
.tag{{background:var(--red);color:#000;border-radius:3px;padding:0 4px;font-size:9px;margin-left:3px}}
.watchzone{{opacity:.5}}
.note{{color:var(--dim);font-size:11px;margin-top:22px;border-top:1px solid var(--line);padding-top:10px}}
.note b{{color:var(--m)}}
</style></head><body><div class=wrap>
<header><h1>◆ claude board</h1>
<span class=sub><b>{blocked}</b> need you now · round-robin · nothing forgotten</span></header>

<div class=focus>
  <div class=fhead>
    <span class=fdir>{html.escape(title_of(frun))}</span>
    <span class=fbadge>your move</span>
    <span class=fmeta>{fc['sid']} · idle 12m</span>
  </div>
  <div class=about><span class=albl>session</span>{html.escape(ftitle)}</div>
  <div class=ctx>{md_to_html(fctx)}</div>
  <div class=ask><div class=lbl>the ask</div><div class=qtext>{html.escape(fask) or "(no explicit question — your move)"}</div></div>
  <div class=respond>
    <div class=opts>{optbtns}</div>
    <div class=replyrow>
      <input class=ti placeholder="type your reply…">
      <button class=send>respond →</button>
    </div>
    <div class=actions>
      <span class=prisel>⚑ priority <b>normal</b> ▾</span>
      <span class=grow></span>
      <button class=ghost>snooze ▾</button>
      <button class=ghost>skip →</button>
    </div>
  </div>
</div>

<div class=qhead>up next · curated round-robin<span class=ct>{len(upnext)}</span></div>
{up_html}

<div class=qhead>snoozed<span class=ct>1</span></div>
<div class=watchzone><button class="qrow lane-w"><span class=qbadge>snoozed</span>
<span class=qdir>api-refactor</span><span class=qone>wakes in 2h10m · then rejoins rotation</span>
<span class=kind>you set this</span></button></div>

<div class=qhead>watching · resurfaces when it needs you<span class=ct>{len(watching)}</span></div>
<div class=watchzone>{watch_html}</div>

<div class=note>curated round-robin · you respond → it drops back to <b>watching</b> → auto-resurfaces
when it next needs you, so no session is forgotten · context is the real untruncated last message
(markdown rendered server-side) · red "mock" = synthetic Blocked runs · controls unwired.</div>
</div></body></html>''')


if __name__ == "__main__":
    main()
