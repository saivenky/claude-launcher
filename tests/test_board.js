// Board client tests. Drives web/board.js for real — the whole file is eval'd
// in a vm against a stub DOM and a fake /api/board — and asserts the Focus
// discipline (CONTEXT.md: Focus, Rotation) holds.
//
// This exists because board.js is not decoration: it decides which Run you are
// looking at and when that changes, and the Python suite cannot see a line of
// it. Run by tests/test_board.py, so `python3 -m unittest discover -s tests`
// covers it too. No dependencies — plain node.
//
// Black-box on purpose: `pinned` is a lexical `let` and unreachable from here,
// so the tests assert on what the client actually *does* — the URLs it asks for
// and the DOM it leaves behind — not on its internals.
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "web/board.js"), "utf8");
// The stylesheet is read too, at the bottom. The stub DOM runs no CSS, so a DOM
// assertion can only prove the client toggled a class — never that hiding the
// chrome actually yielded the pixels. Only board.html can say that.
const HTML = fs.readFileSync(path.join(ROOT, "web/board.html"), "utf8");

// --- stub layout -----------------------------------------------------------
// board.js reads exactly two boxes: the Focus card's, to decide whether the end
// of the read is below the fold (readingUp), and the composer's, to publish the
// height the swipe hint and the toast stand on (syncBarHeight — there is no dock
// to measure since ADR 0015). The stub lays out those two and nothing else —
// enough to drive the scroll-chrome rule, nowhere near enough to pretend CSS ran.
const layout = {cardBottom: 0, barHeight: 0};
const rect = (b) => ({top: 0, left: 0, right: 0, width: 0, bottom: b, height: b});

// The composer's own metrics. The reply box is a textarea whose height is
// MEASURED rather than declared (board.js::growComposer — `field-sizing:content`
// is not in Safari yet), so the stub has to be able to answer getComputedStyle
// and scrollHeight for it. No CSS runs here, so these numbers are not a rendering:
// they are what the client is *told*, which is what makes its arithmetic — five
// rows of this line-height, plus this padding and this border — assertable at all.
// A test may move `lineHeight` to prove the cap follows the box and is not a
// constant.
const boxStyle = {lineHeight: "20px", paddingTop: "8px", paddingBottom: "8px",
                  borderTopWidth: "1px", borderBottomWidth: "1px"};
const BOX_PAD = 16, BOX_BORDER = 2;   // what those add up to, for the sums below

// --- stub DOM: only the surface board.js actually touches -------------------
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {}; this._cls = "";
    this.value = ""; this.scrollTop = 0; this.selectionStart = 0; this.selectionEnd = 0;
    this.hidden = false; this._html = "";
    // The composer writes its measured height here (board.js::growComposer), and
    // a test reads it back — the one inline style this client sets.
    this.style = {};
  }
  // A textarea's content height, the way a browser reports it: one line per
  // newline plus the box's own padding, never its border. That last part is the
  // gotcha growComposer exists to get right, so the stub has to reproduce it.
  // Assign to override — a test needs a wall of text without typing one.
  get scrollHeight() {
    if (this._sh !== undefined) return this._sh;
    return String(this.value || "").split("\n").length * parseFloat(boxStyle.lineHeight) + BOX_PAD;
  }
  set scrollHeight(v) { this._sh = v; }
  get className() { return this._cls; }
  set className(v) { this._cls = v || ""; }
  get textContent() {
    return this.children.map((c) => (c.__text !== undefined ? c.__text : c.textContent)).join("");
  }
  set textContent(v) { this.children = []; if (v != null && v !== "") this.children.push({__text: String(v)}); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get firstChild() { return this.children[0]; }
  // `tagName` because the arrow keys that walk the ring stand down while
  // anything is taking text, and that is what board.js reads to know.
  get tagName() { return (this.tag || "").toUpperCase(); }
  // `parent` exists for `closest()` below, which is how board.js decides a
  // gesture started somewhere the gesture does not live.
  append(...ns) {
    for (const n of ns) if (n != null) { if (n instanceof El) n.parent = this; this.children.push(n); }
  }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t, ev) { (this.listeners[t] || []).forEach((fn) => fn(ev || {})); }
  setAttribute(k, v) { this[k] = v; }
  focus() { doc.activeElement = this; }
  blur() { if (doc.activeElement === this) doc.activeElement = null; this.dispatch("blur", {}); }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  getBoundingClientRect() {
    const cls = " " + this._cls + " ";
    if (cls.includes(" focus ")) return rect(layout.cardBottom);
    if (cls.includes(" respond ")) return rect(layout.barHeight);
    return rect(0);
  }
  querySelector(sel) {   // class selectors only — all board.js asks for
    const cls = sel.slice(1);
    const walk = (n) => {
      for (const c of n.children) {
        if (!(c instanceof El)) continue;
        if ((" " + c._cls + " ").includes(" " + cls + " ")) return c;
        const r = walk(c); if (r) return r;
      }
      return null;
    };
    return walk(this);
  }
  // Self-or-ancestor, over a comma-separated list of class and tag selectors —
  // exactly the shape board.js hands it (SWIPE_BLOCK).
  closest(sel) {
    const parts = sel.split(",").map((s) => s.trim()).filter(Boolean);
    for (let n = this; n; n = n.parent) {
      for (const p of parts) {
        if (p[0] === "." ? (" " + n._cls + " ").includes(" " + p.slice(1) + " ") : n.tag === p) return n;
      }
    }
    return null;
  }
}
const doc = {
  activeElement: null,
  hidden: true,   // stops schedule() firing background polls; the tests drive poll() by hand
  _byId: {},
  // Only `--barh` is ever written here (board.js::syncBarHeight); the tests read
  // it back to prove the composer's height is measured, not guessed.
  documentElement: {style: {_p: {}, setProperty(k, v) { doc.documentElement.style._p[k] = v; }}},
  getElementById(id) { return doc._byId[id] || (doc._byId[id] = new El("div")); },
  createElement(t) { return new El(t); },
  createTextNode(t) { return {__text: String(t)}; },
  // Recorded rather than dropped: the escape hatch that brings hidden chrome
  // back is a document-level click, and a test has to be able to fire it.
  _lis: {},
  addEventListener(t, fn) { (doc._lis[t] = doc._lis[t] || []).push(fn); },
  dispatch(t, ev) { (doc._lis[t] || []).forEach((fn) => fn(ev || {})); },
};
// board.html ships these hidden and gives the sheets their classes; the stub
// creates bare, visible elements, so seed both or the client reads a scrim that is
// permanently up and a **Recover** row offering a set it has not fetched.
["dirpop", "recover", "recovpanel", "toast", "swipehint", "zscrim", "iscrim"]
  .forEach((id) => { doc.getElementById(id).hidden = true; });
// Classes, because a gesture is refused by where it started (board.js::inChrome
// reads `closest`), and tags, because the arrow keys stand down while an input
// has focus. board.html says both; the stub makes every by-id node a bare div.
// `isheet` also carries `open` / `inline` — the whole **Intake** state (ADR 0015)
// — so the base class has to be there for setCls to preserve.
["isheet", "dirpop", "recovpanel", "rail", "swipehint"]
  .forEach((id) => { doc.getElementById(id).className = id; });
["dir", "sid"].forEach((id) => { doc.getElementById(id).tag = "input"; });

// --- fake /api/board -------------------------------------------------------
// Mirrors server.py::_board's focus rule: honour ?focus= when that Session is
// still listed, else fall back to the head of blocked+recent. If that rule ever
// changes server-side, change it here too — the adopt tests are only meaningful
// against a server that can still steal the focus back.
let world = [];          // [{sessionId, runId, lane, title, updatedAt, pri, one}]
// Foreign Runs live in their own array for the same reason they live on their
// own payload key: they are not lanes, so nothing that orders `world` may ever
// see one (server.py::_foreign_items, ADR 0012).
let foreignWorld = [];   // [{sessionId, title, dir, status, bridge, updatedAt, one}]
// sessionId -> **Scrollback**: the recent **turns**, oldest first, exactly the
// shape server.py::_scrollback ships (ADR 0014). A test can swap one Session's.
const sbOf = {};
const SB = () => [{role: "user", html: "<p>which one?</p>", tools: []},
                  {role: "assistant", html: "<p>ctx</p>", tools: []}];
let etagN = 0;
const fetched = [];      // every URL board.js asked for
const respondLog = [];
const transferLog = [];  // every body posted to api/transfer
let transferReply = {status: 200, body: {ok: true, runId: "r-transferred"}};
// GET /api/recoverable: the **Resumable Sessions**, newest-first, with the
// **recovery set** flagged (ADR 0013). Steerable, because the **Recover** row's
// two states — `recover · N` and plain `recover` — are exactly the two shapes of
// this payload, and its absence is the third.
let recoverable = {sessions: [], preselectCount: 0};
let recovEtagN = 0;
// board.js polls once at load. That first poll is made to FAIL, once, so the boot
// case is covered: nothing rendered means no Focus card and therefore no ＋, and
// **Intake** has to fall back to the empty Board's inline layout rather than leave
// the page with no route to it at all (ADR 0015). Cleared on use.
let boardDown = true;

function fakeBoard(focusSid) {
  const rank = {question: 0, approval: 0, yourmove: 1};
  const order = world.filter((s) => s.lane in rank)
    .sort((a, b) => (rank[a.lane] - rank[b.lane]) || (a.pri - b.pri));
  let focus = focusSid ? world.find((s) => s.sessionId === focusSid) : null;
  const pinned = !!focus;
  if (!focus) focus = order[0] || null;
  const strip = (s) => ({runId: s.runId, sessionId: s.sessionId, title: s.title, dir: "/p/" + s.title,
                         status: "", bridge: "", updatedAt: s.updatedAt, lane: s.lane, pri: s.pri, one: s.one});
  // The **Ask** is a property of being **Blocked** and of nothing else: server.py
  // blanks it off the question/approval lanes (ADR 0014). Mirrored here, or the
  // "no ask on an idle Focus" test would only be testing the fake.
  const blocked = focus && (focus.lane === "question" || focus.lane === "approval");
  return {
    focus: focus ? Object.assign(strip(focus), {
      aiTitle: "about " + focus.title, scrollback: sbOf[focus.sessionId] || SB(),
      ask: blocked ? "what now?" : "", options: [], cursor: 0, pendingInput: "", pinned,
    }) : null,
    upnext: order.filter((s) => s !== focus).map(strip),
    watching: world.filter((s) => s.lane === "working" && s !== focus).map(strip),
    snoozed: [], dormant: [],
    foreign: foreignWorld.map((s) => Object.assign({}, s)),
    counts: {needYou: order.length, watching: 0, dormant: 0, snoozed: 0},
  };
}

function res(status, body, etag) {
  return Promise.resolve({
    status, ok: status >= 200 && status < 300,
    headers: {get: () => etag || null},
    json: () => Promise.resolve(body),
  });
}
function fakeFetch(url, opts) {
  fetched.push(url);
  if (url.startsWith("api/tasks")) return res(200, {root: "~/projects/", tasks: []}, "t1");
  if (url.startsWith("api/recoverable")) return res(200, recoverable, "rc" + (++recovEtagN));
  if (url.startsWith("api/board")) {
    if (boardDown) { boardDown = false; throw new Error("unreachable"); }
    const m = /[?&]focus=([^&]*)/.exec(url);
    // A fresh ETag every time: the point is that unrelated churn forces renders.
    return res(200, fakeBoard(m ? decodeURIComponent(m[1]) : ""), "e" + (++etagN));
  }
  if (url === "api/respond") { respondLog.push(JSON.parse(opts.body)); return res(200, {ok: true}); }
  if (url === "api/transfer") {
    transferLog.push(JSON.parse(opts.body));
    return res(transferReply.status, transferReply.body);
  }
  return res(200, {ok: true});
}

// window.confirm and window.alert are stubs a test can steer: Transfer confirms
// before it kills, and shouts through alert() when a kill is left with nothing
// resumed. Both default to "the user said yes and saw it".
const confirmLog = [];
const alertLog = [];
let confirmReply = true;

const store = {cl_token: "secret"};   // pre-seeded: Respond is token-gated (ADR 0007)
// The **Scrollback** has no scroll box of its own any more (ADR 0014 killed the
// 46vh one), so the reading position the client carries across a rebuild is the
// PAGE scroll. The stub window keeps it addressable from a test.
// innerHeight + the recorded scroll/resize listeners are what let a test drive
// the scroll-driven chrome: the client hides it when the end of the read sits
// below this fold (board.js::readingUp).
const win = {prompt: () => "secret",
             confirm: (m) => { confirmLog.push(m); return confirmReply; },
             alert: (m) => { alertLog.push(m); },
             scrollY: 0, scrollTo: (x, y) => { win.scrollY = y; },
             innerHeight: 800,
             // The composer measures itself off this (board.js::growComposer). The
             // stub answers with the metrics `.ti` sets in board.html, since a
             // textarea's height is the one thing on this page the client has to
             // compute instead of declare.
             getComputedStyle: () => boxStyle,
             _lis: {},
             addEventListener(t, fn) { (win._lis[t] = win._lis[t] || []).push(fn); },
             dispatch(t, ev) { (win._lis[t] || []).forEach((fn) => fn(ev || {})); }};
const sandbox = {
  document: doc, console,
  window: win,
  localStorage: {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; },
                 removeItem: (k) => { delete store[k]; }},
  fetch: fakeFetch, setTimeout, clearTimeout, Date, JSON, Object, Array, Math, String, encodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox);

// --- helpers ---------------------------------------------------------------
const app = doc.getElementById("app");
const focusWrap = () => app.children[0];
const card = () => focusWrap().querySelector(".focus");
const ti = () => focusWrap().querySelector(".ti");
const sbEl = () => focusWrap().querySelector(".sb");
const shownSid = () => {   // the card prints sessionId[:8] in its own .fsid span
  const c = card(); if (!c) return null;
  const sid = c.querySelector(".fsid");
  return sid ? sid.textContent.slice(0, 8) : null;
};
const lastBoardUrl = () => [...fetched].reverse().find((u) => u.startsWith("api/board"));
const zones = () => app.children[1];   // the queue half of #app; the Focus is children[0]
const findAll = (root, cls) => {       // every descendant carrying this class
  const out = [];
  const walk = (n) => {
    for (const c of (n.children || [])) {
      if (!(c instanceof El)) continue;
      if ((" " + c.className + " ").includes(" " + cls + " ")) out.push(c);
      walk(c);
    }
  };
  walk(root);
  return out;
};
const ghost = (txt) => findAll(focusWrap(), "ghost").find((b) => b.textContent === txt);
// **Intake** (ADR 0015): the sheet, its scrim, the ＋ that opens it from inside the
// Focus's card header, and the **Recover** row. The ＋ is built by board.js, so it
// is findable; the sheet's own contents are authored in board.html, which the stub
// does not parse — what is in the sheet and in what order is asserted against the
// stylesheet at the bottom of this file instead.
const isheet = () => doc.getElementById("isheet");
const iscrim = () => doc.getElementById("iscrim");
const iplus = () => findAll(focusWrap(), "iplus")[0];
const recovRow = () => doc.getElementById("recover");
// One class predicate for the whole file — sheets carry `open` / `inline`, chrome
// carries `hid`, a turn carries its role, and they are all the same question.
const hasCls = (n, c) => !!n && (" " + (n.className || "") + " ").includes(" " + c + " ");
const tick = (ms) => new Promise((r) => setTimeout(r, ms));
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await tick(5); };
const poll = async () => { await sandbox.poll(); await settle(); };

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "\n         " + extra : "")); }
}

// `pri` must not go through `|| 1` — 0 IS a priority (high), and coercing it to
// normal quietly defuses the no-cut-in test: nothing outranks the held Focus, so
// it passes without the client doing anything.
const S = (sid, lane, pri, title) => ({sessionId: sid, runId: "r-" + sid, lane,
                                       pri: pri === undefined ? 1 : pri,
                                       title, updatedAt: 1000, one: "one-" + title});
const A = "aaaaaaaa-1111-1111-1111-111111111111";
const B = "bbbbbbbb-2222-2222-2222-222222222222";
const W = "wwwwwwww-3333-3333-3333-333333333333";

(async () => {
  // The boot case, before anything is asked of the client: its load-time poll was
  // made to fail (boardDown), so nothing has rendered — no card, so no ＋. Intake
  // must still be reachable, which means the inline layout (ADR 0015).
  ok("unreachable: a Board that never loaded still offers Intake, inline",
     hasCls(isheet(), "inline") && !iplus(), isheet().className);

  // Adopt: the server picks a head once; from then on the client owns the Focus.
  world = [S(A, "question", 1, "alpha")];
  await poll();
  ok("adopt: the first Focus handed over is shown", shownSid() === A.slice(0, 8), "got " + shownSid());
  await poll();
  ok("adopt: every later poll carries ?focus=", lastBoardUrl() === "api/board?focus=" + A,
     "got " + lastBoardUrl());

  // The whole point: a higher-priority Blocked Run must not take the card.
  const before = card();
  world.push(S(B, "approval", 0, "bravo"));   // pri 0 — would be order[0] server-side
  await poll();
  ok("no cut-in: a new high-priority Blocked Run does not take the Focus",
     shownSid() === A.slice(0, 8), "got " + shownSid());
  ok("no cut-in: it queues in up-next instead", app.children[1].textContent.includes("bravo"));
  ok("no cut-in: and the held card is not even rebuilt", card() === before);

  // One ETag covers the whole board, so another Run's churn redraws this page.
  // That redraw must not reach the Focus card.
  ti().value = "half-typed reply";
  win.scrollY = 120;   // you had read some way down the scrollback
  const node = card();
  world.push(S(W, "working", 1, "worker"));
  world[2].updatedAt = 9999;
  await poll();
  ok("churn: an unrelated Run's update leaves the card alone", card() === node);
  ok("churn: the half-typed reply survives", ti().value === "half-typed reply",
     "got " + JSON.stringify(ti().value));
  ok("churn: the reading position survives", win.scrollY === 120, "got " + win.scrollY);

  // When the Focus's own data does move, it rebuilds — carrying state across.
  sbOf[A] = SB().concat([{role: "assistant", html: "<p>a new turn arrived</p>", tools: []}]);
  await poll();
  ok("own change: the card is rebuilt", card() !== node);
  ok("own change: the reply is carried over", ti().value === "half-typed reply",
     "got " + JSON.stringify(ti().value));
  // The scrollback lost its own scroller with ADR 0014, so the reading position
  // to carry is the page's. A poll must never throw you back up a long run-up.
  ok("own change: the reading position is carried over", win.scrollY === 120, "got " + win.scrollY);
  ok("own change: the new turn is shown",
     findAll(sbEl(), "md").slice(-1)[0].innerHTML === "<p>a new turn arrived</p>",
     JSON.stringify(findAll(sbEl(), "md").map((m) => m.innerHTML)));

  // A different Session is a different reply — never inherit the last one's.
  sandbox.setPinned(B);
  await settle();
  ok("switch: tapping a row moves the Focus", shownSid() === B.slice(0, 8), "got " + shownSid());
  ok("switch: the new card gets a clean box", ti().value === "", "got " + JSON.stringify(ti().value));
  sandbox.setPinned(A); await settle();
  ti().value = ""; delete sbOf[A];

  // Advance-on-resolve: the one automatic move.
  world.find((s) => s.sessionId === A).lane = "working";
  await poll();
  ok("resolve: the card is held while it flips to working", shownSid() === A.slice(0, 8),
     "got " + shownSid());
  await tick(1500);   // the 1.2s hand-off
  await settle();
  ok("resolve: then the Focus is handed to the next head", shownSid() === B.slice(0, 8),
     "got " + shownSid());
  await poll();
  ok("resolve: and the new Focus is adopted in turn", lastBoardUrl() === "api/board?focus=" + B,
     "got " + lastBoardUrl());

  // Tapping an already-working row is a choice, not a resolve — don't bounce.
  sandbox.setPinned(W);
  await settle();
  ok("tap-working: a working row can be chosen", shownSid() === W.slice(0, 8), "got " + shownSid());
  await tick(1500);
  await settle();
  ok("tap-working: choosing it does not trip the advance", shownSid() === W.slice(0, 8),
     "got " + shownSid());

  // A busy Run still takes input (Claude Code queues it), so advancing off a
  // card you are typing into would eat the very text this all exists to protect.
  world.find((s) => s.sessionId === A).lane = "question";
  sandbox.setPinned(A); await settle();
  ti().value = "a reply I am still writing";
  ti().focus();
  world.find((s) => s.sessionId === A).lane = "working";   // resolves under you
  await poll();
  await tick(1500);
  await settle();
  ok("mid-reply: the advance is deferred while you type", shownSid() === A.slice(0, 8),
     "got " + shownSid());
  ok("mid-reply: the draft is intact", ti().value === "a reply I am still writing",
     "got " + JSON.stringify(ti().value));
  // The other half of replyEngaged: text in the box counts even once the caret is
  // gone, because you may have tapped away to read the run-up before sending.
  ti().blur();
  await settle(); await tick(1500); await settle();
  ok("mid-reply: the text alone goes on gating it, with the caret gone",
     shownSid() === A.slice(0, 8), "got " + shownSid());
  const box = ti();
  box.value = ""; box.blur();
  await settle(); await tick(1500); await settle();
  ok("mid-reply: letting go releases the deferred advance", shownSid() !== A.slice(0, 8),
     "still on " + shownSid());

  // The box survives rebuilds now, so a sent reply must be cleared explicitly —
  // otherwise the carry-over resurrects it and a sent reply looks unsent.
  world.find((s) => s.sessionId === B).lane = "question";
  sandbox.setPinned(B); await settle();
  ti().value = "ship it";
  focusWrap().querySelector(".send").dispatch("click");
  await settle();
  ok("respond: the text reaches the pane", respondLog.length === 1 && respondLog[0].text === "ship it",
     JSON.stringify(respondLog));
  ok("respond: a sent reply clears the box", ti().value === "", "got " + JSON.stringify(ti().value));

  // --- Foreign Runs: visible, never drivable (ADR 0012) ---------------------
  // A `claude` started by hand at the Mac. It arrives on its own payload key, so
  // the assertions that matter are the negative ones: it is not in the queue, it
  // cannot be answered, and none of the Focus discipline above moves because of it.
  const F = "ffffffff-4444-4444-4444-444444444444";
  foreignWorld = [{sessionId: F, title: "mine", dir: "~/projects/mine", status: "waiting",
                   bridge: "session_abc", updatedAt: 1000, one: "the last thing it said"}];
  const held = shownSid();   // B, from the respond test above
  await poll();
  const fr = findAll(zones(), "frow");
  ok("foreign: it gets a row of its own", fr.length === 1, "rows: " + fr.length);
  ok("foreign: showing title, dir, status and last message",
     fr.length === 1 && ["mine", "~/projects/mine", "waiting", "the last thing it said"]
       .every((s) => fr[0].textContent.includes(s)), fr.length && fr[0].textContent);
  const acts = fr.length ? findAll(fr[0], "iconbtn") : [];
  ok("foreign: ↗ to the bridge is offered — the one route onto it that is not a terminal",
     acts.length === 1 && acts[0].tag === "a" && acts[0].href === "https://claude.ai/code/session_abc",
     JSON.stringify(acts.map((a) => a.tag + ":" + a.textContent)));
  ok("foreign: no ❯ attach and no × close — neither has anything to act on",
     !acts.some((a) => a.tag === "button"));
  ok("foreign: and no tap target to hand it the Focus with",
     fr.length === 1 && !fr[0].listeners.click && findAll(fr[0], "qbody").length === 0);

  // Nothing about the Focus discipline may move because a Foreign Run appeared.
  ok("foreign: its arrival does not disturb the held Focus", shownSid() === held,
     "was " + held + ", now " + shownSid());
  ok("foreign: it is not in the queue either",
     findAll(zones(), "qrow").every((r) => !r.textContent.includes("~/projects/mine")));

  // Rotation walks data.upnext, which a Foreign Run is never in: with nothing
  // else actionable, `skip →` must refuse rather than land on it.
  ghost("skip →").dispatch("click");
  await settle();
  ok("foreign: it never enters Rotation — skip stays on the held card",
     shownSid() === held, "got " + shownSid());

  // And it can never be the Focus, not even as the only Run the Board can see.
  world = [];
  await poll();
  ok("foreign: it never takes the Focus — an all-foreign board is still 'all clear'",
     card() === null && focusWrap().textContent.includes("All clear"), focusWrap().textContent);
  ok("foreign: while staying listed and visible", findAll(zones(), "frow").length === 1);

  // --- Transfer: the one thing a Foreign Run can be told to do (ADR 0012) ----
  // Everything above says what these rows cannot do. This is the exception, and
  // it has to be reachable without turning the section into a second queue.
  const xfer = () => findAll(zones(), "fgxfer")[0];
  ok("transfer: the row offers it", !!xfer() && xfer().tag === "button" &&
     xfer().textContent === "transfer", xfer() && xfer().textContent);
  ok("transfer: not as a fourth glyph in the queue's icon row",
     !(" " + xfer().className + " ").includes(" iconbtn "), xfer().className);

  // The status is the price of the tap: a busy Run is mid-turn and that turn dies
  // with the process. Never a refusal — you tapped from somewhere else, and a
  // refusal would only strand you — so it is named instead (ADR 0012).
  foreignWorld[0].status = "busy";
  await poll();
  ok("transfer: a mid-turn run says so on the button",
     xfer().textContent === "transfer · mid-turn", xfer().textContent);

  confirmReply = false;
  xfer().dispatch("click");
  await settle();
  ok("transfer: it confirms first — a mis-tap here ends a Run",
     confirmLog.length === 1 && confirmLog[0].startsWith("Transfer this run?"),
     JSON.stringify(confirmLog));
  ok("transfer: the confirm names the in-flight turn and the unsent text",
     confirmLog[0].includes("mid-turn") && confirmLog[0].includes("not sent"), confirmLog[0]);
  ok("transfer: declining posts nothing at all", transferLog.length === 0,
     JSON.stringify(transferLog));

  confirmReply = true;
  xfer().dispatch("click");
  await settle();
  ok("transfer: accepting posts the Session", transferLog.length === 1 &&
     transferLog[0].sessionId === F, JSON.stringify(transferLog));
  ok("transfer: and never a pid — the server re-derives that from its own walk",
     transferLog.length === 1 && Object.keys(transferLog[0]).join() === "sessionId",
     JSON.stringify(transferLog[0]));
  // It is a Managed Run now, and invisible until `claude` reaches `ps` — the same
  // gap a launch leaves, so it earns the same optimistic card and burst-poll.
  ok("transfer: the new Run gets the optimistic card a launch would get",
     findAll(doc.getElementById("pending"), "startcard").length === 1);

  // The loud path: the kill landed, the resume did not, and this Session now has
  // nothing running while you are away from the Mac. A toast fades in 2.6s.
  transferReply = {status: 500, body: {ok: false, orphaned: true,
    message: "ended the Foreign Run but the resume failed — NOTHING IS RUNNING"}};
  xfer().dispatch("click");
  await settle();
  ok("transfer: a resume that failed after the kill blocks until it is read",
     alertLog.length === 1 && alertLog[0].includes("NOTHING IS RUNNING"),
     JSON.stringify(alertLog));

  transferReply = {status: 400, body: {ok: false, orphaned: false,
    message: "no live Foreign Run on that Session"}};
  xfer().dispatch("click");
  await settle();
  ok("transfer: an ordinary refusal stays a toast", alertLog.length === 1,
     JSON.stringify(alertLog));

  // --- The Focus is a Scrollback (ADR 0014) ---------------------------------
  // The card renders the Session's recent **turns** — what you said, what it did
  // and what it then said — in place of the single last assistant message.
  const T = "77777777-5555-5555-5555-555555555555";
  foreignWorld = [];
  world = [S(T, "yourmove", 1, "scroll")];
  sbOf[T] = [
    {role: "user", html: "<p>consolidate the notes</p>", tools: []},
    // The tool-only turn, and an injection attempt riding a field that is NOT
    // the innerHTML'd one.
    {role: "assistant", html: "", tools: ["Bash", "<img src=x onerror=alert(1)>"]},
    {role: "assistant", html: "<p>done — <strong>3 files</strong></p>", tools: ["Read"]},
  ];
  sandbox.setPinned(T);
  await settle();
  const turns = () => findAll(sbEl(), "turn");

  ok("scrollback: one element per turn, oldest first", turns().length === 3 &&
     hasCls(turns()[0], "you") && hasCls(turns()[2], "ai"),
     turns().map((t) => t.className).join(" | "));

  // The ADR 0006 / ADR 0003 split, in one pair of assertions: the ONE field the
  // server rendered escape-first becomes markup; every other field of the same
  // turn is untrusted text and cannot become an element.
  ok("scrollback: a turn's html reaches the DOM as markup (ADR 0006)",
     findAll(sbEl(), "md")[0].innerHTML === "<p>consolidate the notes</p>",
     JSON.stringify(findAll(sbEl(), "md").map((m) => m.innerHTML)));
  const chips = findAll(turns()[1], "tool");
  ok("scrollback: a turn's other fields cannot inject — textContent, never innerHTML (ADR 0003)",
     chips.length === 2 && chips[1].textContent === "<img src=x onerror=alert(1)>" &&
     chips[1].innerHTML === "", JSON.stringify(chips.map((c) => [c.textContent, c.innerHTML])));

  // A working Run emits long stretches of tool calls with no prose. Drawn as
  // blanks the whole scrollback looks broken.
  ok("scrollback: a tool-only turn draws its chips and is not blank",
     turns()[1].textContent.includes("Bash") && hasCls(turns()[1], "toolsonly"),
     JSON.stringify(turns()[1].textContent));
  ok("scrollback: the newest assistant turn reads as the live one",
     hasCls(turns()[2], "live") && !hasCls(turns()[1], "live"));

  // An **Ask** is the blocker of a **Blocked** Run and of nothing else. On an
  // idle Focus the closing question is already the last turn above.
  ok("ask: an idle Focus draws no ask block at all", findAll(focusWrap(), "ask").length === 0,
     JSON.stringify(findAll(focusWrap(), "ask").map((a) => a.textContent)));
  ok("ask: and no placeholder stands in for it",
     !card().textContent.includes("no explicit question"), card().textContent);

  world[0].lane = "question";
  await poll();
  const askBox = () => findAll(focusWrap(), "ask");
  ok("ask: a Blocked Focus draws one, carrying the blocker",
     askBox().length === 1 && askBox()[0].textContent.includes("what now?"),
     JSON.stringify(askBox().map((a) => a.textContent)));

  // The composer is unconditional (CONTEXT.md: Focus). Responding to a working
  // Run is not a special case — its input queues until the turn ends. Each lane
  // says where the text goes in the box's own placeholder, and nowhere else: a
  // **Blocked** Focus is answered, a working one is queued behind the turn.
  const PLACEHOLDER = {yourmove: "type your reply…", question: "answer…",
                       working: "queue a note for the next turn…"};
  for (const lane of ["yourmove", "question", "working"]) {
    world[0].lane = lane;
    await poll();
    ok("composer: a " + lane + " Focus still offers the reply box", !!ti() &&
       !!focusWrap().querySelector(".send"));
    ok("composer: and its box says where a " + lane + " Focus's text goes",
       ti().placeholder === PLACEHOLDER[lane], ti().placeholder);
  }
  ok("composer: a working Focus says what happens to what you type",
     card().textContent.includes("queues until this turn ends"), card().textContent);
  ok("composer: and nothing is disabled to say it", ti().disabled !== true);

  // --- The composer is a growing textarea (ADR 0015) ------------------------
  // **Respond** carries prose — a paragraph, a path, a pasted error — and the
  // `<input>` this replaces showed the last few words of it through a keyhole. So
  // it is a textarea that grows per keystroke and caps at five rows. What the stub
  // can prove is the arithmetic and the wiring; the pixels are board.html's, and
  // the stylesheet assertions at the bottom of this file cover what they can.
  const boxH = () => ti().style.height;
  const ROW = parseFloat(boxStyle.lineHeight);
  ok("composer: the reply box is a textarea, not an input", ti().tag === "textarea", ti().tag);
  ok("composer: one row at rest — this box's line-height, padding and border, the box the input had",
     ti().rows === 1 && boxH() === (ROW + BOX_PAD + BOX_BORDER) + "px",
     "rows=" + ti().rows + " h=" + boxH());
  // The overlay this slice came from (prototype/bottom-edge-intake) kept the real
  // input alive and mirrored the textarea into it, because it could not reach the
  // send closure from outside the page. This one is native, so there is no second
  // copy of your reply anywhere.
  ok("composer: and it is the only box in the row — no hidden input left mirroring it",
     findAll(focusWrap(), "ti").length === 1 && !findAll(focusWrap(), "ti")
       .some((b) => b.tag === "input"),
     JSON.stringify(findAll(focusWrap(), "ti").map((b) => b.tag)));

  ti().value = "one\ntwo\nthree";
  ti().dispatch("input");
  ok("composer: it grows per keystroke, inline — `field-sizing:content` is not in Safari yet",
     boxH() === (3 * ROW + BOX_PAD + BOX_BORDER) + "px", boxH());
  ti().scrollHeight = 400;   // a wall of text, without typing one
  ti().dispatch("input");
  ok("composer: and stops at five rows, from where it scrolls inside itself",
     boxH() === (5 * ROW + BOX_PAD + BOX_BORDER) + "px", boxH());
  boxStyle.lineHeight = "30px";
  ti().dispatch("input");
  ok("composer: the cap is five rows of THIS box's line-height, never a pixel constant",
     boxH() === (5 * 30 + BOX_PAD + BOX_BORDER) + "px", boxH());
  boxStyle.lineHeight = ROW + "px";
  // `--barh` is the composer's measured height and the swipe hint stands on it
  // (ADR 0015), so a bar that moves per keystroke has to be republished per
  // keystroke or the hint ends up underneath the box.
  layout.barHeight = 74;
  ti().dispatch("input");
  ok("composer: every growth republishes --barh, which the swipe hint stands on",
     doc.documentElement.style._p["--barh"] === "74px",
     JSON.stringify(doc.documentElement.style._p));
  layout.barHeight = 0;
  ti().scrollHeight = undefined;   // back to counting the lines in the box

  // ENTER INSERTS A NEWLINE; ⌘/Ctrl+Enter SENDS (ADR 0015). A soft keyboard has no
  // Shift+Enter, so the Slack idiom would make the second line of a five-row box
  // untypeable on the phone this whole tool exists for.
  let swallowed = false;
  const enter = (mods) => Object.assign({key: "Enter",
                                         preventDefault: () => { swallowed = true; }}, mods || {});
  const sentN = respondLog.length;
  ti().value = "line one";
  ti().dispatch("keydown", enter());
  await settle();
  ok("composer: Enter does not send — the keystroke is left to insert a newline",
     respondLog.length === sentN && !swallowed && ti().value === "line one",
     respondLog.length - sentN + " sent / " + JSON.stringify(ti().value));
  ti().dispatch("keydown", enter({metaKey: true}));
  await settle();
  ok("composer: ⌘Enter is what sends",
     respondLog.length === sentN + 1 && respondLog[sentN].text === "line one",
     JSON.stringify(respondLog.slice(sentN)));
  ok("composer: swallowing the keystroke, so a send never also leaves a newline behind",
     swallowed);
  ok("composer: a sent reply clears the box, and the box shrinks back with the text",
     ti().value === "" && boxH() === (ROW + BOX_PAD + BOX_BORDER) + "px",
     JSON.stringify(ti().value) + " / " + boxH());
  ti().value = "line two";
  ti().dispatch("keydown", enter({ctrlKey: true}));
  await settle();
  ok("composer: and Ctrl+Enter is the same shortcut on a keyboard without a ⌘",
     respondLog.length === sentN + 2 && respondLog[sentN + 1].text === "line two",
     JSON.stringify(respondLog.slice(sentN)));

  // What a rebuild costs you, with a textarea in the bar: the text, the caret
  // inside it, and — past the cap — the box's own scroll, which an `<input>` never
  // had. A poll may no more throw you to the top of your own draft than to the top
  // of the read.
  ti().value = "first\nsecond\nthird";
  ti().dispatch("input");
  ti().focus();
  ti().setSelectionRange(6, 12);
  ti().scrollTop = 18;
  const oldBox = ti();
  world[0].updatedAt = 4141;   // move the Focus's own data, or nothing is rebuilt
  await poll();
  ok("rebuild: the box is a new node, and still a textarea",
     ti() !== oldBox && ti().tag === "textarea", ti().tag);
  ok("rebuild: carrying a multi-line reply over intact",
     ti().value === "first\nsecond\nthird", JSON.stringify(ti().value));
  ok("rebuild: with the caret where you left it, not dumped at the end",
     ti().selectionStart === 6 && ti().selectionEnd === 12,
     ti().selectionStart + "-" + ti().selectionEnd);
  ok("rebuild: and the box's own scroll, which only a capped textarea has",
     ti().scrollTop === 18, "got " + ti().scrollTop);
  ok("rebuild: re-measured, so the bar is not left one row under the text it holds",
     boxH() === (3 * ROW + BOX_PAD + BOX_BORDER) + "px", boxH());
  ti().value = "";
  ti().dispatch("input");
  ti().blur();
  doc.activeElement = null;

  // --- The chrome is a scroll position, not a mode (ADR 0014's Context) -----
  // The Focus's header and its composer are worth pixels only at the live end of
  // the **Scrollback**. Scroll up into history and they get out of the read's
  // way; come back near the bottom and they return.
  world[0].lane = "yourmove";
  sandbox.setPinned(T);        // also cancels the advance the composer loop armed
  await settle();
  doc.activeElement = null;
  layout.cardBottom = 0;
  win.dispatch("scroll");

  const fhead = () => card() && card().querySelector(".fhead");
  const respond = () => card() && card().querySelector(".respond");
  // The third thing at this edge. It used to be the intake dock; ADR 0015 deleted
  // that, so what is left riding the chrome state with the card's own two bars is
  // the swipe hint, which stands on the composer's measured height.
  const hint = () => doc.getElementById("swipehint");
  const hid = (n) => !!n && (" " + (n.className || "") + " ").includes(" hid ");
  // The client hides the chrome on consecutive travel UP (back into history) and
  // hands it back on travel down — or outright, at the end of the read. A
  // **Scrollback** is oldest-first, so "not near the bottom" alone would hide it
  // for the whole of a first read; travel is what the client actually watches.
  const scrollTo = (y) => { win.scrollY = y; win.dispatch("scroll"); };
  const readUp = () => { scrollTo(1600); scrollTo(1200); };
  const readDown = () => { scrollTo(1200); scrollTo(1600); };

  ok("chrome: at the live end of the scrollback it is all up",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  layout.cardBottom = 2400;    // the end of the read is far below an 800px fold
  readDown();
  ok("chrome: reading DOWN a long run-up keeps it up — that is the way to the answer",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  readUp();
  ok("chrome: scrolling up into history slides the Focus's header away", hid(fhead()));
  ok("chrome: and the composer with it", hid(respond()));
  ok("chrome: the intake dock rides the same state — the bottom edge is one thing",
     hid(hint()));
  ok("chrome: hiding never unbuilds the composer — the same box is still there",
     !!ti() && ti() === respond().querySelector(".ti"));

  layout.cardBottom = 700;     // the end of the read is back on screen
  scrollTo(1190);              // a nudge further UP: the end of the read wins anyway
  ok("chrome: returning near the bottom brings all three back",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  // The escape hatch, and its limit: a tap is a nudge, not a latch.
  layout.cardBottom = 2400;
  readUp();
  ok("escape hatch: hidden to begin with", hid(respond()));
  doc.dispatch("click");
  ok("escape hatch: interacting with the page restores the chrome without a scroll",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));
  scrollTo(win.scrollY - 200);
  ok("escape hatch: and another step back into history takes it away again",
     hid(respond()));

  // A scroll may no more snatch the keyboard away than a poll may.
  doc.dispatch("click");
  ti().focus();
  readUp();
  ok("chrome: an active reply keeps its box, however far up the read you are",
     !hid(respond()));
  ti().blur();
  readUp();
  ok("chrome: letting go of the box hands the pixels back to the read", hid(respond()));

  // The state that must survive a poll, now with a third thing in it: a
  // half-typed reply, a reading position, AND a chrome state that still agrees
  // with where the reader is.
  ti().value = "still writing this";
  win.scrollY = 640;
  const heldCard = card();
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and another turn</p>", tools: []}]);
  await poll();
  ok("rebuild: the card is rebuilt when its own data moves", card() !== heldCard);
  ok("rebuild: the half-typed reply still survives", ti().value === "still writing this",
     "got " + JSON.stringify(ti().value));
  ok("rebuild: the reading position still survives", win.scrollY === 640, "got " + win.scrollY);
  ok("rebuild: and the chrome is left where the reader left it, on the new nodes",
     hid(fhead()) && hid(respond()) && hid(hint()));

  // A different Session is a different read: you have travelled nowhere in it
  // yet, so it opens with the chrome up however deep the last one was.
  world.push(S(B, "question", 1, "bravo2"));
  sandbox.setPinned(B);
  await settle();
  ok("switch: a new Session opens with the chrome up, wherever the page sits",
     !hid(fhead()) && !hid(respond()) && !hid(hint()), card() && card().className);
  sandbox.setPinned(T);
  await settle();

  // --- Intake is a sheet behind a ＋ in the card header (ADR 0015) -----------
  // The docked launch bar and the **Recover** pill above it are deleted: at the
  // live end of the read the bottom edge is the composer and nothing else. Every
  // shape of Intake is one sheet now, and the one thing that opens it is the ＋ in
  // the **Focus**'s sticky header — a Board-level verb borrowing the only strip
  // that is always on screen while you read.
  ok("intake: the ＋ lives in the Focus's sticky header, where the read cannot lose it",
     !!iplus() && iplus().tag === "button" && iplus().textContent === "＋" &&
     fhead().querySelector(".iplus") === iplus(), iplus() && iplus().className);
  ok("intake: it is the header's last thing, so it falls in behind the queue count",
     fhead().children[fhead().children.length - 1] === iplus() &&
     findAll(fhead(), "zbtn").length === 1,
     fhead().children.map((c) => c.className).join(" | "));
  ok("intake: and the sheet is shut until you ask for it",
     !hasCls(isheet(), "open") && iscrim().hidden === true, isheet().className);

  readUp();
  ok("intake: the ＋'s own strip clears the read while nothing is open", hid(fhead()));
  doc.dispatch("click");   // the escape hatch: chrome back, no scroll
  iplus().dispatch("click");
  ok("intake: tapping it brings the sheet up over the read, with a scrim behind it",
     hasCls(isheet(), "open") && iscrim().hidden === false, isheet().className);
  ok("intake: and marks the ＋ while it is open",
     hasCls(iplus(), "hot") && iplus()["aria-expanded"] === "true", iplus().className);
  readUp();
  ok("intake: an Intake you have opened is never slid out from under you — the ＋ is in there",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));
  iscrim().dispatch("click");
  ok("intake: the scrim dismisses it, exactly as it does the queue's sheet",
     !hasCls(isheet(), "open") && iscrim().hidden === true, isheet().className);
  ok("intake: and the ＋ stops being marked", !hasCls(iplus(), "hot"), iplus().className);
  ok("intake: which hands the chrome back to the scroll position it was at", hid(respond()));

  doc.dispatch("click");
  iplus().dispatch("click");
  doc.dispatch("keydown", {key: "Escape"});
  ok("intake: Escape shuts it too — one idiom with the queue sheet",
     !hasCls(isheet(), "open"), isheet().className);

  // Every shape of **Intake** is in there, and acting is a dismissal — the sheet
  // was only ever open in order to reach one of these.
  iplus().dispatch("click");
  doc.getElementById("dir").value = "sandbox";
  doc.getElementById("launch").dispatch("click");
  await settle();
  ok("intake: dir-launch still posts — the hot path ADR 0008 measured",
     fetched.includes("api/launch"), JSON.stringify(fetched.slice(-3)));
  ok("intake: and firing an action puts the sheet away", !hasCls(isheet(), "open"),
     isheet().className);
  iplus().dispatch("click");
  doc.getElementById("sid").value = "some-session-id";
  doc.getElementById("resume").dispatch("click");
  await settle();
  ok("intake: resume-by-sessionId is in the sheet too, and dismisses it as well",
     fetched.includes("api/resume") && !hasCls(isheet(), "open"), isheet().className);

  // The **Recover** row: hidden iff nothing is resumable, and it never badges the
  // ＋ (ADR 0015 — a non-empty recovery set outside a restart is the mtime
  // heuristic finding a cluster, and a badge would put that on the one strip you
  // cannot scroll away from).
  ok("recover: nothing resumable, so the row is not there at all",
     recovRow().hidden === true);
  recoverable = {sessions: [{sessionId: "r1", title: "one", dir: "/p/one", mtime: 1, preselect: true},
                            {sessionId: "r2", title: "two", dir: "/p/two", mtime: 1}],
                 preselectCount: 1};
  await sandbox.loadRecoverable();
  await settle();
  ok("recover: something is resumable, so the row appears",
     recovRow().hidden === false, "hidden=" + recovRow().hidden);
  ok("recover: it carries the recovery-set count, and the total beside it",
     recovRow().textContent.includes("recover · 1") &&
     recovRow().textContent.includes("2 resumable"), recovRow().textContent);
  ok("recover: and the ＋ carries no badge of it — that is the decision, not an omission",
     iplus().textContent === "＋", iplus().textContent);
  recoverable = {sessions: recoverable.sessions, preselectCount: 0};
  await sandbox.loadRecoverable();
  await settle();
  ok("recover: an empty recovery set reads plain `recover` and still offers the picker",
     recovRow().textContent.includes("recover") &&
     !recovRow().textContent.includes("·") &&
     recovRow().textContent.includes("2 resumable"), recovRow().textContent);
  iplus().dispatch("click");
  recovRow().dispatch("click");
  await settle();
  ok("recover: tapping it opens the picker and closes the sheet — never two sheets",
     doc.getElementById("recovpanel").hidden === false && !hasCls(isheet(), "open"),
     isheet().className);
  doc.getElementById("recovclose").dispatch("click");
  recoverable = {sessions: [], preselectCount: 0};
  await sandbox.loadRecoverable();
  await settle();
  ok("recover: nothing resumable again, so the row goes", recovRow().hidden === true);

  layout.barHeight = 96;   // e.g. a Blocked Focus: the composer grew a row of options
  win.dispatch("resize");
  ok("bottom edge: the composer's height is measured and published, never guessed",
     doc.documentElement.style._p["--barh"] === "96px",
     JSON.stringify(doc.documentElement.style._p));
  layout.barHeight = 0;

  // With no queue there is no count for the ＋ to follow, and it still has to be
  // there — it is the only thing on the page that opens Intake.
  world = [world[0]];
  world[0].updatedAt = 4242;   // move the Focus's own data, or the card is not rebuilt
  await poll();
  ok("intake: the ＋ is there with no queue count beside it at all",
     !!iplus() && findAll(fhead(), "zbtn").length === 0,
     fhead().children.map((c) => c.className).join(" | "));

  // --- The empty Board: Intake is the only thing you can do (ADR 0015) -------
  // After a machine restart no **Run** survives, so there is no **Focus**, no
  // `.fhead` and therefore no ＋. Intake stops being a sheet and renders inline in
  // the page flow instead — open, unscrimmed, undismissable. This is the screen the
  // Recover pill used to be on the bottom edge for.
  recoverable = {sessions: [{sessionId: "r1", title: "one", dir: "/p/one", mtime: 1, preselect: true}],
                 preselectCount: 1};
  world = [];
  await poll();
  ok("empty board: no Focus, so no card and no ＋ to open a sheet with",
     card() === null && !iplus(), focusWrap().textContent);
  ok("empty board: so Intake renders inline, in the page flow, instead",
     hasCls(isheet(), "inline") && !hasCls(isheet(), "open"), isheet().className);
  ok("empty board: with no scrim, because there is nothing behind it to dismiss to",
     iscrim().hidden === true);
  ok("empty board: and Recover is loud right there — the pill's whole job",
     recovRow().hidden === false && recovRow().textContent.includes("recover · 1"),
     recovRow().textContent);
  doc.dispatch("keydown", {key: "Escape"});
  iscrim().dispatch("click");
  ok("empty board: nothing dismisses it — Intake is the only thing you can do here",
     hasCls(isheet(), "inline"), isheet().className);
  doc.getElementById("dir").value = "fresh";
  doc.getElementById("launch").dispatch("click");
  await settle();
  ok("empty board: and launching from it does not put it away either",
     hasCls(isheet(), "inline"), isheet().className);

  world = [S(T, "yourmove", 1, "scroll")];
  await poll();
  ok("empty board: a Focus arriving hands Intake back to the sheet and the ＋",
     !hasCls(isheet(), "inline") && !!iplus(), isheet().className);
  recoverable = {sessions: [], preselectCount: 0};
  await sandbox.loadRecoverable();

  // --- The return path: a ring you can walk (this slice) --------------------
  // Answering a Run used to drop it three headings down under a label promising
  // it would resurface "when it needs you". It does not, and the follow-up
  // thought arrives minutes later — too late for any "keep this card" button,
  // because you did not have the thought yet. So the Board owes a way BACK:
  // one ring, walked by a gesture on a phone and by a rail on a monitor, and
  // setPinned underneath both — never a second mechanism.
  doc.dispatch("click");        // chrome back up; nothing below is mid-read
  doc.activeElement = null;
  layout.cardBottom = 0;
  win.dispatch("scroll");
  const G = "99999999-6666-6666-6666-666666666666";
  world = [S(A, "question", 1, "alpha"), S(B, "yourmove", 1, "bravo"), S(W, "working", 1, "worker")];
  // A **Foreign Run** on the board throughout: every assertion below is also an
  // assertion that it is nowhere in the ring (ADR 0012).
  foreignWorld = [{sessionId: G, title: "byhand", dir: "~/projects/byhand", status: "waiting",
                   bridge: "", updatedAt: 1000, one: "started by hand at the Mac"}];
  sandbox.setPinned(A);
  await settle();

  const rail = () => doc.getElementById("rail");
  const railRows = () => findAll(rail(), "qrow");
  const isNow = (r) => (" " + r.className + " ").includes(" now ");
  const nowRow = () => railRows().find(isNow);
  const fdir = () => (card() ? card().querySelector(".fdir").textContent : "");
  // Pointer, not touch: the prototype's touch-only first cut fired nothing under
  // a mouse, so the gesture did not exist on a desktop at all.
  const swipe = (target, dx, dy) => {
    win.dispatch("pointerdown", {target, clientX: 200, clientY: 400});
    win.dispatch("pointerup", {target, clientX: 200 + dx, clientY: 400 + dy});
  };

  ok("hint: one line says the gesture is there — nothing else on the page does",
     hint().hidden === false, "hidden=" + hint().hidden);

  // The ring is the Board's display order with the Focus spliced into its own
  // zone: [worker (answered), alpha (the Focus), bravo].
  swipe(sbEl(), -130, 12);
  await settle();
  ok("swipe: a horizontal drag on the Scrollback moves the Focus",
     shownSid() === B.slice(0, 8), "got " + shownSid());
  ok("swipe: and moves it the one way anything moves it — setPinned, so ?focus= follows",
     lastBoardUrl() === "api/board?focus=" + B, "got " + lastBoardUrl());
  ok("swipe: the edge you moved toward flashes — a gesture leaves no other mark",
     (" " + doc.getElementById("edger").className + " ").includes(" on "),
     doc.getElementById("edger").className);
  ok("swipe: and the toast names where it put you",
     doc.getElementById("toast").textContent === "→ bravo",
     JSON.stringify(doc.getElementById("toast").textContent));
  ok("hint: which retires it for good, on this device",
     hint().hidden === true && store.cl_swipe === "used", JSON.stringify(store.cl_swipe));

  // The whole point of the slice. `answered · still running` LEADS the ring, so
  // the Run you replied to sits one step off the head of the queue — back past
  // alpha and you are on it, with no tap and nothing to have thought of earlier.
  swipe(sbEl(), 140, -10);
  await settle();
  ok("swipe: back the way you came", shownSid() === A.slice(0, 8), "got " + shownSid());
  swipe(sbEl(), 140, -10);
  await settle();
  ok("swipe: and once more is the Run you already answered — the return path",
     shownSid() === W.slice(0, 8), "got " + shownSid());

  const seen = [];
  for (let i = 0; i < 5; i++) { swipe(sbEl(), -150, 0); await settle(); seen.push(shownSid()); }
  ok("ring: it walks every Managed Run and wraps", new Set(seen).size === 3, JSON.stringify(seen));
  ok("ring: and never lands on a Foreign Run — it is not in the ring at all",
     !seen.includes(G.slice(0, 8)), JSON.stringify(seen));
  ok("ring: while that Foreign Run is still listed and transferable",
     findAll(zones(), "frow").length === 1);

  // A trackpad's two-finger flick is the same gesture on a laptop.
  let held2 = shownSid();
  win.dispatch("wheel", {deltaX: 9, deltaY: 240, target: sbEl()});
  await settle();
  ok("trackpad: an ordinary vertical scroll is never a move", shownSid() === held2,
     "got " + shownSid());
  win.dispatch("wheel", {deltaX: 95, deltaY: 6, target: sbEl()});
  await settle();
  ok("trackpad: a horizontal flick moves it", shownSid() !== held2, "still " + shownSid());
  held2 = shownSid();
  win.dispatch("wheel", {deltaX: 95, deltaY: 6, target: sbEl()});
  await settle();
  ok("trackpad: one flick is one move — the burst of events behind it is ignored",
     shownSid() === held2, "got " + shownSid());

  // The keyboard does on a desktop what the swipe does on glass.
  await tick(750);   // the wheel lock is a flick's, not a key's
  doc.activeElement = null;
  held2 = shownSid();
  doc.dispatch("keydown", {key: "ArrowRight"});
  await settle();
  ok("keyboard: → walks the ring", shownSid() !== held2, "still " + shownSid());
  doc.dispatch("keydown", {key: "ArrowLeft"});
  await settle();
  ok("keyboard: ← walks it back", shownSid() === held2, "got " + shownSid());
  doc.getElementById("dir").focus();
  doc.dispatch("keydown", {key: "ArrowRight"});
  await settle();
  ok("keyboard: but the arrows belong to the caret while an input has focus",
     shownSid() === held2, "got " + shownSid());
  doc.getElementById("dir").blur();
  doc.activeElement = null;

  // Where the gesture does not live. Each of these is its own surface with its
  // own targets, and one of them is the reply box this whole file protects.
  const guarded = shownSid();
  const noSwipe = (name, target) => {
    swipe(target, -170, 0);
    return name;
  };
  for (const [name, node] of [["the composer", card().querySelector(".respond")],
                              ["the Intake sheet", isheet()],
                              ["the Recover sheet", doc.getElementById("recovpanel")],
                              ["the dir dropup", doc.getElementById("dirpop")],
                              ["the rail", rail()]]) {
    noSwipe(name, node);
    await settle();
    ok("no swipe from " + name, shownSid() === guarded, "got " + shownSid());
  }

  ti().value = "half a thought";
  swipe(sbEl(), -180, 0);
  await settle();
  ok("swipe: a half-typed reply is never swiped out from under you",
     shownSid() === guarded && ti().value === "half a thought",
     shownSid() + " / " + JSON.stringify(ti().value));
  ti().value = "";

  // Vertical scrolling has to stay effortless: a **Scrollback** is read by
  // dragging it up and down, and a horizontal reading that fires too easily
  // makes that read feel sticky.
  swipe(sbEl(), 40, 300);
  await settle();
  ok("scrolling: a mostly-vertical drag is a read, not a swipe", shownSid() === guarded,
     "got " + shownSid());
  swipe(sbEl(), 100, 90);
  await settle();
  ok("scrolling: horizontal must out-run vertical outright before it counts",
     shownSid() === guarded, "got " + shownSid());
  swipe(sbEl(), 40, 4);
  await settle();
  ok("scrolling: and a short slide is slop, not a gesture", shownSid() === guarded,
     "got " + shownSid());

  // --- The sheet: the queue's way in at a width with no room for a rail -----
  // The count lives in the Focus's sticky header because that is the one strip
  // still on screen once the read has scrolled past everything else.
  const zbtn = () => findAll(doc.getElementById("app"), "zbtn")[0];
  ok("sheet: the count rides the Focus's sticky header, where the read can't lose it",
     !!zbtn() && /queued/.test(zbtn().textContent), zbtn() && zbtn().textContent);
  ok("sheet: and it is shut until you ask for it", !zones().className.includes("open"),
     zones().className);
  zbtn().dispatch("click");
  await settle();
  ok("sheet: tapping the count brings the queue up over the read",
     zones().className.includes("open") && doc.getElementById("zscrim").hidden === false,
     zones().className);
  // Two sheets, one read: opening either has to shut the other, because two
  // stacked sheets over the **Scrollback** is not a state (ADR 0015).
  iplus().dispatch("click");
  ok("sheets: opening Intake shuts the queue — two stacked sheets is not a state",
     hasCls(isheet(), "open") && !zones().className.includes("open") &&
     doc.getElementById("zscrim").hidden === true, zones().className);
  zbtn().dispatch("click");
  ok("sheets: and opening the queue shuts Intake, the same way round",
     zones().className.includes("open") && !hasCls(isheet(), "open") &&
     iscrim().hidden === true, isheet().className);
  doc.activeElement = null;   // opening Intake focuses the dir field; drop it again
  const sheetRow = findAll(zones(), "qrow").find((r) => !isNow(r));
  sheetRow.querySelector(".qbody").dispatch("click");
  await settle();
  ok("sheet: landing a Focus is its whole purpose, so that puts it away again",
     !zones().className.includes("open") && doc.getElementById("zscrim").hidden === true,
     zones().className);

  // --- The rail: the same ring, spent as width a monitor never misses -------
  ok("rail: it draws the whole ring, the Focus included", railRows().length === 3,
     railRows().map((r) => r.textContent).join(" | "));
  ok("rail: with the current-Run marker on the Run the card is showing",
     !!nowRow() && nowRow().textContent.includes(fdir()),
     nowRow() && nowRow().textContent);
  const wasNow = nowRow().textContent;
  swipe(sbEl(), -150, 0);
  await settle();
  ok("rail: a swipe moves that marker — on a monitor the gesture has a readout",
     !!nowRow() && nowRow().textContent !== wasNow && nowRow().textContent.includes(fdir()),
     nowRow() && nowRow().textContent);
  ok("rail: a Foreign Run is not in it either", !rail().textContent.includes("byhand"),
     rail().textContent);
  ok("rail: its rows reach a Run rather than act on one — no ↗ ❯ × strip at 290px",
     findAll(rail(), "rowact").length === 0);
  const otherRow = railRows().find((r) => !isNow(r));
  const target = otherRow.querySelector(".qdir").textContent;
  otherRow.querySelector(".qbody").dispatch("click");
  await settle();
  ok("rail: clicking one moves the Focus, exactly as a row tap does",
     fdir() === target && !!nowRow() && nowRow().querySelector(".qdir").textContent === target,
     fdir() + " / " + target);

  // --- The zones below the card: unchanged on a phone, and honest now -------
  sandbox.setPinned(A);
  await settle();
  const heads = () => findAll(zones(), "qhead").map((h) => h.textContent);
  ok("zones: the phone still gets them under the card — the rail is not its answer",
     findAll(zones(), "qrow").length >= 2 && heads().length >= 2, JSON.stringify(heads()));
  ok("relabel: `answered · still running` leads them",
     heads()[0].startsWith("answered · still running"), JSON.stringify(heads()));
  ok("relabel: nothing still promises to resurface on its own",
     heads().every((h) => !h.includes("resurfaces")), JSON.stringify(heads()));
  const answered = findAll(zones(), "qrow").find((r) => r.textContent.includes("worker"));
  ok("relabel: and it is not dimmed — you are reading it because it did NOT resurface",
     !!answered && answered.closest(".dim") === null, answered && answered.className);
  ok("zones: the Focus is spliced into the RAIL only; these stay the payload's own",
     findAll(zones(), "qrow").every((r) => !isNow(r)),
     findAll(zones(), "qrow").map((r) => r.className).join(" | "));

  // --- What only the stylesheet can answer ----------------------------------
  // The stub runs no CSS, so everything above proves the client toggles a class.
  // Whether that class actually yields the pixels — and whether the column is
  // bounded — lives in board.html, so these read it.
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = (sel) => {
    const m = new RegExp("^" + esc(sel) + "\\{([^}]*)\\}", "m").exec(HTML);
    return m ? m[1] : "(no rule for " + sel + ")";
  };
  const HIDS = [".fhead.hid", ".respond.hid", ".swipehint.hid"];

  ok("no layout reserved: the chrome is sticky, so the turns scroll UNDER it",
     rule(".fhead").includes("position:sticky") && rule(".respond").includes("position:sticky"),
     rule(".fhead") + " || " + rule(".respond"));
  ok("no layout reserved: hiding is a transform — never a display or height reflow",
     HIDS.every((s) => /transform:translateY/.test(rule(s))) &&
     !HIDS.some((s) => /display:none|height:0|max-height/.test(rule(s))),
     HIDS.map(rule).join(" || "));
  // ADR 0015's whole claim, in the one place that can prove it: the composer
  // stands on the viewport, not on a dock, and `--dockh` is not a term anywhere —
  // every offset that had it lost it rather than gained a zero.
  ok("bottom edge: the composer IS the bottom edge — nothing is docked under it",
     rule(".respond").includes("bottom:0") && rule(".dock") === "(no rule for .dock)" &&
     rule(".dockbar") === "(no rule for .dockbar)", rule(".respond"));
  // Comments in this stylesheet talk about --dockh at length — it is the term ADR
  // 0015 removed, and saying so is the point — so strip them: this asserts about
  // declarations, not prose.
  const DECLS = HTML.replace(/\/\*[\s\S]*?\*\//g, "");
  ok("bottom edge: and no offset stands on a --dockh that no longer exists",
     !/var\(--dockh\)/.test(DECLS) && !/--dockh:/.test(DECLS),
     (/[^;{]*var\(--dockh\)[^;]*/.exec(DECLS) || [""])[0]);
  ok("bottom edge: so the composer owns the safe-area inset the dock absorbed for it",
     rule(".respond").includes("env(safe-area-inset-bottom"), rule(".respond"));
  ok("bottom edge: and the Recover pill that sat on it is gone outright",
     !/\.recoverbtn/.test(HTML) && !/\.recoverbar/.test(HTML));
  // The composer's shape, in the one place that can state it. "One row at rest,
  // pixel-identical to the input" IS this rule declaring nothing of its own: the
  // box, the font and the padding are still `.ti`, shared with the input it
  // replaced, and `rows=1` says the height. No cap is written here either — the cap
  // is five of this box's own rows (board.js::growComposer), so it follows the font
  // instead of dating with it.
  ok("composer: the textarea shares the input's own box, so at rest it IS that box",
     rule("textarea.ti") !== "(no rule for textarea.ti)" &&
     !/font|padding|line-height|height/.test(rule("textarea.ti")), rule("textarea.ti"));
  ok("composer: no resize handle — a drag handle is desktop-only furniture — and it scrolls at the cap",
     /resize:none/.test(rule("textarea.ti")) && /overflow-y:auto/.test(rule("textarea.ti")),
     rule("textarea.ti"));
  ok("composer: the row is bottom-aligned, so `respond →` stays put as the box grows",
     /align-items:flex-end/.test(rule(".replyrow")), rule(".replyrow"));
  // A growing box at this edge could have broken exactly two things, and both are
  // declarations: the slide-away is a percentage of the bar's OWN height, and the
  // bar is stuck to the bottom, so it grows up into the read rather than off-screen.
  ok("composer: a box grown to five rows still slides fully off the bottom edge",
     /transform:translateY\(100%\)/.test(rule(".respond.hid")) &&
     rule(".respond").includes("bottom:0"), rule(".respond.hid"));
  const rm = HTML.indexOf("@media(prefers-reduced-motion:reduce){");
  ok("chrome: the slide respects prefers-reduced-motion (the .spin precedent)",
     rm > 0 && HTML.slice(rm, rm + 220).includes(".fhead,.respond,.isheet{transition:none}"),
     HTML.slice(rm, rm + 220));

  ok("column: one bounded reading column, wider than the old 640px phone width",
     /--col:740px/.test(HTML) && rule(".wrap").includes("var(--gut)"), rule(".wrap"));
  ok("column: which collapses to today's 14px gutters on a phone — nothing changes there",
     /--gut:max\(14px,calc\(50% - var\(--col\)\/2\)\)/.test(HTML));
  ok("column: and the fixed Intake sheet snaps to the SAME column, not the viewport edge",
     rule(".isheet").includes("var(--gut)"), rule(".isheet"));

  // --- Intake, as only the markup and the stylesheet can say it (ADR 0015) ---
  // The sheet's contents are authored, not built, so the stub DOM cannot see them:
  // what is in the sheet, in what order, and where the sheet sits in the document
  // are all read straight out of board.html here.
  const sheetHtml = HTML.slice(HTML.indexOf("<div class=isheet"), HTML.indexOf("<div id=app>"));
  const INTAKE_ORDER = ["class=dirpop", "class=ilaunch", "id=launch", "class=irecov",
                        "id=sid", "id=tasks"];
  let seq = -1, inOrder = true;
  for (const s of INTAKE_ORDER) { const i = sheetHtml.indexOf(s); if (i < seq) inOrder = false; seq = i; }
  ok("intake: the sheet holds every shape of it — launch (with its dropup), Recover, resume, tasks",
     !!sheetHtml && inOrder && /class=dlbl/.test(sheetHtml),
     JSON.stringify(INTAKE_ORDER.map((s) => [s, sheetHtml.indexOf(s)])));
  ok("intake: authored ABOVE the card slot, which is what makes inline mode need no node moved",
     HTML.indexOf("<div class=isheet") < HTML.indexOf("<div id=app>") &&
     HTML.indexOf("<div id=pending>") < HTML.indexOf("<div class=isheet"));
  ok("intake: and it is a sheet the same way the queue's is — over the read, with a scrim",
     /position:fixed/.test(rule(".isheet")) && /transform:translateY/.test(rule(".isheet")) &&
     /\.isheet\.open\{transform:none/.test(HTML) && /position:fixed/.test(rule(".iscrim")),
     rule(".isheet"));
  ok("intake: the empty Board drops the fixed positioning and nothing else moves",
     /position:static/.test(rule(".isheet.inline")) &&
     /transform:none/.test(rule(".isheet.inline")), rule(".isheet.inline"));

  // The rail's gate is a media query, so only the sheet can say where it is.
  const mq = HTML.indexOf("@media(min-width:900px){");
  const mqBlock = mq > 0 ? HTML.slice(mq, HTML.indexOf("\n}", mq)) : "";
  ok("rail: it does not exist below 900px — the phone gets the sheet and the swipe",
     rule(".rail") === "display:none" && /\.rail\{display:block/.test(mqBlock), rule(".rail"));
  // The narrow half of the same answer. Stacking the queue under an unbounded
  // Scrollback put it at the end of the read — the further you read the further
  // away the rest of the Board got, which is the original complaint again.
  ok("sheet: below 900px the queue is a sheet over the read, not a stack under it",
     /position:fixed/.test(rule(".zones")) && /transform:translateY/.test(rule(".zones")),
     rule(".zones"));
  ok("sheet: which the >=900px rail sends back to ordinary flow, drawn once",
     /\.zones\{position:static/.test(mqBlock) && /\.zgrab,\.zscrim,\.zbtn\{display:none\}/.test(mqBlock),
     mqBlock.slice(0, 160));
  ok("rail: where it does exist the queue steps aside, so the list is never drawn twice",
     /\.queues\{display:none\}/.test(mqBlock), mqBlock.slice(-120));
  ok("rail: and the reading column is OFFSET by it, never overlapped",
     /--rail:290px/.test(mqBlock) &&
     /--gut:max\(14px,calc\(50% - var\(--rail\)\/2 - var\(--col\)\/2\)\)/.test(mqBlock) &&
     rule(".wrap").includes("margin-left:var(--rail)") &&
     rule(".isheet").includes("left:var(--rail)"),
     rule(".wrap") + " || " + rule(".isheet"));
  // Intake has no wide half. The queue steps aside above 900px because the rail
  // draws it there; nothing draws Intake in the rail, so the sheet and its ＋ stay
  // exactly as they are and the breakpoint never mentions either (ADR 0015).
  ok("intake: it is a sheet at EVERY width — no rail draws it, so nothing steps aside",
     !/\.isheet\{/.test(mqBlock) && !/\.iplus/.test(mqBlock) && !/\.iscrim/.test(mqBlock),
     mqBlock.slice(0, 200));
  ok("swipe: the Scrollback keeps the vertical axis and claims only the horizontal",
     rule(".sb").includes("touch-action:pan-y"), rule(".sb"));
  ok("swipe: the landing cue is a fixed overlay — a cue may not move the read",
     rule(".edge").includes("position:fixed") && /\.edge\.on\{opacity/.test(HTML),
     rule(".edge"));
  ok("hint: it names the gesture, and clears the read like the rest of this edge",
     HTML.includes("drag sideways to move between Runs") &&
     /transform:translateY/.test(rule(".swipehint.hid")), rule(".swipehint.hid"));

  console.log("\n  " + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
