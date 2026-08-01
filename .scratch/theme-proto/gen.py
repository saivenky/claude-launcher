#!/usr/bin/env python3
"""Generate web/proto.html from web/board.html.

THROWAWAY. The prototype exists to answer two questions on a real phone with
real Runs: does a light Board work, and how big does the type want to be. It is
board.html with three mechanical transforms and a knob panel bolted on:

  1. every hardcoded colour literal -> a token, so a theme is one block to swap
  2. every font-size px -> rem, with html{font-size:16px * --fs}, so one
     multiplier moves the whole type scale at once
  3. the prose face and the mono face -> var(--face) / var(--mono)

plus an appended density block that multiplies the line-heights and the
vertical rhythm by --lhx.

Nothing here is meant to ship. What ships is whatever the knobs settle on,
ported back into board.html by hand as tokens.

    python3 .scratch/theme-proto/gen.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "web", "board.html")
DST = os.path.join(ROOT, "web", "proto.html")

# --- 1. colour literals -> tokens ------------------------------------------
# The left column is every literal that leaks past board.html's existing token
# block (grep found 34 of them). Each one gets a name so the light palette has
# something to bind. Order matters: longest first, so #171c22 is not eaten by a
# shorter prefix match.
COLOURS = [
    # scrims and shadows
    ("rgba(0,0,0,.75)", "var(--shadow-strong)"),
    ("rgba(0,0,0,.62)", "var(--scrim)"),
    ("rgba(0,0,0,.6)", "var(--shadow-sheet)"),
    ("rgba(0,0,0,.5)", "var(--shadow-mid)"),
    ("rgba(0,0,0,.45)", "var(--shadow-soft)"),
    ("rgba(224,108,117,.08)", "var(--red-wash)"),
    # the Record / run-up hairlines, quieter than --line
    ("#171c22", "var(--line2)"),
    ("#151a1f", "var(--line3)"),
    # the prose ramp: four steps of foreground between --fg and --dim
    ("#cfd8e1", "var(--fg1)"),   # .nask .md   your prompt, standing in it
    ("#ccd6e0", "var(--fg1)"),   # .rv.ru      a Record's title
    ("#b9c4ce", "var(--fg2)"),   # .md         body prose
    ("#b3bec9", "var(--fg2)"),   # .rv         a Record's value
    ("#98a4b0", "var(--fg3)"),   # .rv.rs      the reply, a shade back
    # the label gutter
    ("#5d6a7a", "var(--lbl-you)"),
    ("#4c5660", "var(--lbl)"),
    # a Record that closed on a question
    ("#9fd8ce", "var(--q-soft)"),
    # ink on an accent fill
    ("#000", "var(--on-accent)"),
    ("#fff", "var(--fg-max)"),
]

# --- 2. the two faces ------------------------------------------------------
FACES = [
    ('-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif',
     "var(--face)"),
    ("ui-monospace,SFMono-Regular,Menlo,monospace", "var(--mono)"),
    ("ui-monospace,Menlo,monospace", "var(--mono)"),
]

# --- 3. the token block ----------------------------------------------------
# Replaces board.html's :root opening line. The dark values are byte-identical
# to today's, so `dark` on the prototype is the Board you already have.
TOKENS = """:root{
/* ---- PROTOTYPE KNOBS (see the panel, bottom-left) ---- */
--fs:1;      /* type scale: one multiplier on the root, everything is rem */
--lhx:1;     /* density: one multiplier on every line-height and gap */
--face:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace;

/* ---- DARK: byte-identical to board.html today ---- */
--bg:#0c0e11;--panel:#14181d;--panel2:#0a0c0f;--fg:#dde3e9;--dim:#6b7681;
--line:#232a31;--accent:#e6b450;--q:#7fd1c4;--p:#e6b450;--m:#8ab4f8;--w:#5b646d;--red:#e06c75;
--line2:#171c22;--line3:#151a1f;
--fg-max:#fff;--fg1:#ccd6e0;--fg2:#b9c4ce;--fg3:#98a4b0;
--lbl:#4c5660;--lbl-you:#5d6a7a;--q-soft:#9fd8ce;--on-accent:#000;
--scrim:rgba(0,0,0,.62);--shadow-strong:rgba(0,0,0,.75);--shadow-sheet:rgba(0,0,0,.6);
--shadow-mid:rgba(0,0,0,.5);--shadow-soft:rgba(0,0,0,.45);--red-wash:rgba(224,108,117,.08);
"""

# The light palette. Not an inversion — an inverted dark theme reads grey and
# muddy, because the dark one spends its contrast budget on a handful of bright
# accents against near-black. Here the page is warm off-white (#f7f6f3, not
# #fff: a pure-white full-screen read is glare), the surfaces step UP toward
# white rather than down toward black, and every accent is darkened until it
# holds against a light field. Amber is the big one: #e6b450 on white is ~1.7:1
# and unreadable, so the accent becomes a dark amber and the fills invert (dark
# ink on amber becomes white ink on dark amber).
LIGHT = """
html[data-theme=light]{
--bg:#f4f2ee;--panel:#fffefb;--panel2:#eceae4;--fg:#1c2126;--dim:#6a7078;
--line:#dcd8d0;--accent:#96690a;--q:#0d7565;--p:#96690a;--m:#2f5fbf;--w:#9aa0a8;--red:#b83a30;
--line2:#e6e3dc;--line3:#eceae4;
--fg-max:#000;--fg1:#2a3038;--fg2:#3c434b;--fg3:#5a626b;
--lbl:#9aa0a8;--lbl-you:#7c848d;--q-soft:#0f6a5c;--on-accent:#fff;
--scrim:rgba(28,33,38,.34);--shadow-strong:rgba(28,33,38,.18);--shadow-sheet:rgba(28,33,38,.16);
--shadow-mid:rgba(28,33,38,.14);--shadow-soft:rgba(28,33,38,.10);--red-wash:rgba(184,58,48,.07);
}
/* The Focus's top rule and the swipe edge are 2-3px of pure lane colour; at
   light-theme saturation they hold, but the gradient seam rule needs help. */
html[data-theme=light] .seamrule{opacity:.6}
/* .dim is opacity:.5 — fine on black, washes out to nothing on white. */
html[data-theme=light] .dim{opacity:.62}
html[data-theme=light] .fgbox{opacity:.85}
"""

# --- 4. density ------------------------------------------------------------
# Appended last so it wins the cascade on ties. Every value here is the one
# board.html already declares, multiplied. Line-height first (that is most of
# what legibility means at these sizes), then the vertical rhythm, so `roomy`
# does not just space the lines and leave the blocks jammed together.
DENSITY = """
/* ---- PROTOTYPE: density, one multiplier over the vertical rhythm ---- */
body{line-height:calc(1.6*var(--lhx))}
.md{line-height:calc(1.54*var(--lhx))}
.live .md{line-height:calc(1.5*var(--lhx))}
.nask .md{line-height:calc(1.42*var(--lhx))}
.rv{line-height:calc(1.4*var(--lhx))}
.wline{line-height:calc(1.75*var(--lhx))}
.md p{margin:0 0 calc(8px*var(--lhx))}
.turn{margin-bottom:calc(15px*var(--lhx))}
.cm{margin-bottom:calc(10px*var(--lhx))}
.cm:last-child{margin-bottom:0}
.live>:last-child{margin-bottom:0}
.work{margin-bottom:calc(10px*var(--lhx))}
.rhd{padding:calc(6px*var(--lhx)) 0 calc(7px*var(--lhx))}
.inrow .rhd{padding:calc(4px*var(--lhx)) 0}
.sb{padding:calc(7px*var(--lhx)) 15px calc(14px*var(--lhx))}
.qrow{padding:calc(9px*var(--lhx)) 11px}
.recovrow{padding:calc(11px*var(--lhx)) 8px}
"""

# --- 5. the knob panel -----------------------------------------------------
# Bottom-LEFT: the composer's send button owns the bottom-right, and the swipe
# hint owns the bottom strip until it is dismissed.
#
# THE DOT IS DELIBERATELY LOUD, and the first cut was not — which is why this
# comment exists. It was `background:var(--panel)` with a `--line` hairline,
# on the reasoning that a prototype control should cost the read nothing. But
# it sits 66px up the left edge, which on a phone is directly over the sticky
# composer, and .respond's background is `var(--panel)` too: a #14181d circle
# on a #14181d field, hairlined in #232a31. Present in the DOM, invisible on
# the glass. Accent fill, and it is the one thing on the page that is allowed
# to shout — it is not part of the Board and should never be mistaken for it.
PANEL_CSS = """
/* ================= PROTOTYPE KNOBS — NOT PART OF THE BOARD ================= */
#kb{position:fixed;left:10px;z-index:60;font:11px/1.3 var(--mono);
bottom:calc(var(--barh) + 16px + env(safe-area-inset-bottom,0px))}
#kbdot{width:42px;height:42px;border-radius:999px;background:var(--accent);
border:2px solid var(--on-accent);color:var(--on-accent);
font:700 15px/1 var(--mono);cursor:pointer;
box-shadow:0 4px 16px rgba(0,0,0,.5);padding:0}
#kbpanel{display:none;background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:11px 12px 12px;box-shadow:0 8px 30px var(--shadow-sheet);
min-width:238px;max-width:calc(100vw - 24px)}
#kb.open #kbpanel{display:block}
#kb.open #kbdot{display:none}
.kbrow{margin-bottom:9px}
.kbrow:last-child{margin-bottom:0}
.kbl{display:block;color:var(--dim);font-size:8.5px;letter-spacing:1.1px;
text-transform:uppercase;margin-bottom:4px}
.kbseg{display:flex;gap:0;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.kbseg button{flex:1;min-width:0;background:transparent;border:0;
border-right:1px solid var(--line);color:var(--dim);font:10.5px/1 var(--mono);
padding:7px 2px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbseg button:last-child{border-right:0}
.kbseg button.on{background:var(--accent);color:var(--on-accent);font-weight:700}
#kbclose{position:absolute;top:6px;right:8px;background:none;border:0;color:var(--dim);
font:13px/1 var(--mono);cursor:pointer;padding:2px 4px}
#kbpanel{position:relative}
#kbnow{color:var(--dim);font-size:8.5px;margin-top:9px;letter-spacing:.4px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
"""

PANEL_HTML = """
<!-- ============ PROTOTYPE KNOBS — NOT PART OF THE BOARD ============ -->
<div id=kb>
  <button id=kbdot title="theme + type knobs">Aa</button>
  <div id=kbpanel>
    <button id=kbclose>×</button>
    <div class=kbrow><span class=kbl>theme</span>
      <div class=kbseg data-knob=theme>
        <button data-v=dark>dark</button><button data-v=light>light</button>
      </div></div>
    <div class=kbrow><span class=kbl>type scale</span>
      <div class=kbseg data-knob=fs>
        <button data-v=0.95>0.95</button><button data-v=1>1.0</button
        ><button data-v=1.1>1.1</button><button data-v=1.25>1.25</button
        ><button data-v=1.4>1.4</button>
      </div></div>
    <div class=kbrow><span class=kbl>prose face</span>
      <div class=kbseg data-knob=face>
        <button data-v=system>system</button><button data-v=avenir>Avenir</button
        ><button data-v=verdana>Verdana</button><button data-v=charter>Charter</button>
      </div></div>
    <div class=kbrow><span class=kbl>density</span>
      <div class=kbseg data-knob=lhx>
        <button data-v=0.88>compact</button><button data-v=1>normal</button
        ><button data-v=1.16>roomy</button>
      </div></div>
    <div id=kbnow></div>
  </div>
</div>
"""

# IT HAS TO BE AN EXTERNAL FILE. The server sends
#   Content-Security-Policy: default-src 'none'; script-src 'self'; ...
# and `script-src 'self'` drops inline <script> on the floor — silently, as far
# as the page is concerned: board.js runs (external, same-origin), the panel's
# markup parses and renders, and the handler simply never attaches, so the dot
# is there and tapping it does nothing. Note the inline <style> above is fine;
# the policy carries `style-src 'unsafe-inline'` and no such allowance for
# script. Served at /proto.js off the same whitelist as board.js.
PANEL_JS = """/* ============ PROTOTYPE KNOBS — NOT PART OF THE BOARD ============
   No persistence, deliberately: this panel exists for one sitting, to settle
   four values. Whatever wins gets written into board.html by hand. */
(function(){
  var FACES = {
    system:'-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif',
    avenir:'"Avenir Next","Avenir",system-ui,sans-serif',
    verdana:'Verdana,"DejaVu Sans",Geneva,sans-serif',
    charter:'Charter,"Iowan Old Style",Georgia,"Times New Roman",serif'
  };
  var state = {theme:'dark', fs:'1', face:'system', lhx:'1'};
  var kb = document.getElementById('kb');
  var root = document.documentElement;

  function apply(){
    root.setAttribute('data-theme', state.theme);
    root.style.setProperty('--fs', state.fs);
    root.style.setProperty('--lhx', state.lhx);
    root.style.setProperty('--face', FACES[state.face]);
    kb.querySelectorAll('.kbseg').forEach(function(seg){
      var k = seg.dataset.knob;
      seg.querySelectorAll('button').forEach(function(b){
        b.classList.toggle('on', b.dataset.v === state[k]);
      });
    });
    document.getElementById('kbnow').textContent =
      state.theme + ' \\u00b7 ' + state.fs + '\\u00d7 \\u00b7 ' + state.face +
      ' \\u00b7 lh ' + state.lhx;
  }

  kb.addEventListener('click', function(e){
    var b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'kbdot') { kb.classList.add('open'); return; }
    if (b.id === 'kbclose') { kb.classList.remove('open'); return; }
    var seg = b.closest('.kbseg');
    if (!seg) return;
    state[seg.dataset.knob] = b.dataset.v;
    apply();
  });

  apply();
})();
"""


def main() -> None:
    with open(SRC, encoding="utf-8") as fh:
        html = fh.read()

    # -- faces first: the px rewrite below runs over `font:` shorthands, and
    # -- doing families first keeps those shorthands shorter to match.
    for lit, tok in FACES:
        html = html.replace(lit, tok)

    # -- colour literals -> tokens. Skip the :root line itself; it is replaced
    # -- wholesale below, and rewriting a token's own definition to reference
    # -- itself would be a nice infinite loop.
    head, sep, tail = html.partition("--barh:52px;}")
    assert sep, "the :root block did not end where expected"
    for lit, tok in COLOURS:
        tail = tail.replace(lit, tok)

    # -- swap the token block, keeping board.html's own commentary (the
    # -- --col / --gut / --rail / --barh notes) intact.
    head = head.replace(
        ":root{--bg:#0c0e11;--panel:#14181d;--panel2:#0a0c0f;--fg:#dde3e9;--dim:#6b7681;\n"
        "--line:#232a31;--accent:#e6b450;--q:#7fd1c4;--p:#e6b450;--m:#8ab4f8;--w:#5b646d;--red:#e06c75;\n",
        TOKENS,
    )
    assert "--fg-max:#fff" in head, "the :root swap did not take"
    html = head + sep + tail

    # -- px -> rem for TYPE ONLY. Everything else stays px: --fs is a type
    # -- scale, not a zoom, and scaling borders and radii with it would just be
    # -- the browser's own pinch-zoom wearing a costume.
    def rem(m):
        return "%grem" % (float(m.group(1)) / 16)

    html = re.sub(r"font-size:([0-9.]+)px", lambda m: "font-size:" + rem(m), html)
    # the `font:` shorthand — the size is the one length before the slash
    html = re.sub(
        r"font:([^;}]*)",
        lambda m: "font:" + re.sub(r"([0-9.]+)px", rem, m.group(1)),
        html,
    )
    html = html.replace(
        "*{box-sizing:border-box}",
        "html{font-size:calc(16px * var(--fs))}\n*{box-sizing:border-box}",
    )

    # -- light palette, density, panel CSS: appended inside <style>, so they
    # -- win every cascade tie against the rules above.
    html = html.replace("\n</style>", "\n" + LIGHT + DENSITY + PANEL_CSS + "</style>")
    html = html.replace(
        '<script src="board.js"></script>',
        PANEL_HTML + '<script src="board.js"></script>\n<script src="proto.js"></script>')
    with open(os.path.join(ROOT, "web", "proto.js"), "w", encoding="utf-8") as fh:
        fh.write(PANEL_JS)
    html = html.replace("<title>claude board</title>",
                        "<title>claude board — proto</title>")

    with open(DST, "w", encoding="utf-8") as fh:
        fh.write(html)

    left = re.findall(r"#[0-9a-fA-F]{3,6}\b|rgba\([^)]*\)", html.split("--barh:52px;}")[1])
    print("wrote %s" % DST)
    print("colour literals left outside the token block: %d %s"
          % (len(left), sorted(set(left))))
    print("font-size px left: %d" % len(re.findall(r"font-size:[0-9.]+px", html)))


if __name__ == "__main__":
    main()
