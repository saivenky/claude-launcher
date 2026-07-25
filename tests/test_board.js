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
// of the read is below the fold (readingUp), and the intake dock's, to publish
// the height the composer stands on (syncDockHeight). The stub lays out those
// two and nothing else — enough to drive the scroll-chrome rule, nowhere near
// enough to pretend CSS ran.
const layout = {cardBottom: 0, dockHeight: 0};
const rect = (b) => ({top: 0, left: 0, right: 0, width: 0, bottom: b, height: b});

// --- stub DOM: only the surface board.js actually touches -------------------
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {}; this._cls = "";
    this.value = ""; this.scrollTop = 0; this.selectionStart = 0; this.selectionEnd = 0;
    this.hidden = false; this._html = "";
  }
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
    if (cls.includes(" dock ")) return rect(layout.dockHeight);
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
  // Only `--dockh` is ever written here (board.js::syncDockHeight); the tests
  // read it back to prove the dock's height is measured, not guessed.
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
// board.html ships these hidden and gives the dock a class; the stub creates
// bare, visible elements, so seed both or the client reads an intake that is
// permanently open and a dock with no box to measure.
["dockexp", "dirpop", "recoverbar", "recovpanel", "toast", "swipehint"]
  .forEach((id) => { doc.getElementById(id).hidden = true; });
// Classes, because a gesture is refused by where it started (board.js::inChrome
// reads `closest`), and tags, because the arrow keys stand down while an input
// has focus. board.html says both; the stub makes every by-id node a bare div.
["dock", "dirpop", "recovpanel", "rail", "swipehint"]
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
  if (url.startsWith("api/board")) {
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
  const hasCls = (n, c) => (" " + n.className + " ").includes(" " + c + " ");

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
  // Run is not a special case — its input queues until the turn ends.
  for (const lane of ["yourmove", "question", "working"]) {
    world[0].lane = lane;
    await poll();
    ok("composer: a " + lane + " Focus still offers the reply box", !!ti() &&
       !!focusWrap().querySelector(".send"));
  }
  ok("composer: a working Focus says what happens to what you type",
     card().textContent.includes("queues until this turn ends"), card().textContent);
  ok("composer: and nothing is disabled to say it", ti().disabled !== true);

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
  const dock = () => doc.getElementById("dock");
  const hid = (n) => !!n && (" " + (n.className || "") + " ").includes(" hid ");
  // The client hides the chrome on consecutive travel UP (back into history) and
  // hands it back on travel down — or outright, at the end of the read. A
  // **Scrollback** is oldest-first, so "not near the bottom" alone would hide it
  // for the whole of a first read; travel is what the client actually watches.
  const scrollTo = (y) => { win.scrollY = y; win.dispatch("scroll"); };
  const readUp = () => { scrollTo(1600); scrollTo(1200); };
  const readDown = () => { scrollTo(1200); scrollTo(1600); };

  ok("chrome: at the live end of the scrollback it is all up",
     !hid(fhead()) && !hid(respond()) && !hid(dock()));

  layout.cardBottom = 2400;    // the end of the read is far below an 800px fold
  readDown();
  ok("chrome: reading DOWN a long run-up keeps it up — that is the way to the answer",
     !hid(fhead()) && !hid(respond()) && !hid(dock()));

  readUp();
  ok("chrome: scrolling up into history slides the Focus's header away", hid(fhead()));
  ok("chrome: and the composer with it", hid(respond()));
  ok("chrome: the intake dock rides the same state — the bottom edge is one thing",
     hid(dock()));
  ok("chrome: hiding never unbuilds the composer — the same box is still there",
     !!ti() && ti() === respond().querySelector(".ti"));

  layout.cardBottom = 700;     // the end of the read is back on screen
  scrollTo(1190);              // a nudge further UP: the end of the read wins anyway
  ok("chrome: returning near the bottom brings all three back",
     !hid(fhead()) && !hid(respond()) && !hid(dock()));

  // The escape hatch, and its limit: a tap is a nudge, not a latch.
  layout.cardBottom = 2400;
  readUp();
  ok("escape hatch: hidden to begin with", hid(respond()));
  doc.dispatch("click");
  ok("escape hatch: interacting with the page restores the chrome without a scroll",
     !hid(fhead()) && !hid(respond()) && !hid(dock()));
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
     hid(fhead()) && hid(respond()) && hid(dock()));

  // A different Session is a different read: you have travelled nowhere in it
  // yet, so it opens with the chrome up however deep the last one was.
  world.push(S(B, "question", 1, "bravo2"));
  sandbox.setPinned(B);
  await settle();
  ok("switch: a new Session opens with the chrome up, wherever the page sits",
     !hid(fhead()) && !hid(respond()) && !hid(dock()), card() && card().className);
  sandbox.setPinned(T);
  await settle();

  // --- The intake dock still works, and is not occluded by the composer -----
  readUp();
  ok("intake: the dock clears the bottom edge with the rest while you read", hid(dock()));
  doc.getElementById("dplus").dispatch("click");
  ok("intake: ＋ still opens the expanded dock (resume + tasks, ADR 0008)",
     doc.getElementById("dockexp").hidden === false);
  ok("intake: an intake you have opened is never slid out from under you", !hid(dock()));
  ok("intake: while the Focus's own chrome still gets out of the read's way", hid(respond()));
  doc.getElementById("dplus").dispatch("click");
  ok("intake: closing it hands the dock back to the chrome state", hid(dock()));

  doc.dispatch("click");
  doc.getElementById("dir").value = "sandbox";
  doc.getElementById("launch").dispatch("click");
  await settle();
  ok("intake: and dir-launch still posts — the hot path ADR 0008 measured",
     fetched.includes("api/launch"), JSON.stringify(fetched.slice(-3)));

  layout.dockHeight = 96;   // e.g. the Recover pill is up: the dock is taller
  win.dispatch("resize");
  ok("bottom edge: the dock's height is measured and published, never guessed",
     doc.documentElement.style._p["--dockh"] === "96px",
     JSON.stringify(doc.documentElement.style._p));

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
  const hint = () => doc.getElementById("swipehint");
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
                              ["the intake dock", doc.getElementById("dock")],
                              ["the Recover sheet", doc.getElementById("recovpanel")],
                              ["the dir popup", doc.getElementById("dirpop")],
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
  const HIDS = [".fhead.hid", ".respond.hid", ".dock.hid"];

  ok("no layout reserved: the chrome is sticky, so the turns scroll UNDER it",
     rule(".fhead").includes("position:sticky") && rule(".respond").includes("position:sticky"),
     rule(".fhead") + " || " + rule(".respond"));
  ok("no layout reserved: hiding is a transform — never a display or height reflow",
     HIDS.every((s) => /transform:translateY/.test(rule(s))) &&
     !HIDS.some((s) => /display:none|height:0|max-height/.test(rule(s))),
     HIDS.map(rule).join(" || "));
  ok("bottom edge: the composer stacks ON the dock rather than across it",
     rule(".respond").includes("bottom:var(--dockh)") && rule(".dock").includes("bottom:0"),
     rule(".respond"));
  const rm = HTML.indexOf("@media(prefers-reduced-motion:reduce){");
  ok("chrome: the slide respects prefers-reduced-motion (the .spin precedent)",
     rm > 0 && HTML.slice(rm, rm + 220).includes(".fhead,.respond,.dock{transition:none}"),
     HTML.slice(rm, rm + 220));

  ok("column: one bounded reading column, wider than the old 640px phone width",
     /--col:740px/.test(HTML) && rule(".wrap").includes("var(--gut)"), rule(".wrap"));
  ok("column: which collapses to today's 14px gutters on a phone — nothing changes there",
     /--gut:max\(14px,calc\(50% - var\(--col\)\/2\)\)/.test(HTML));
  ok("column: and the fixed dock snaps to the SAME column, not the viewport edge",
     rule(".dock").includes("var(--gut)"), rule(".dock"));

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
     rule(".dock").includes("left:var(--rail)"),
     rule(".wrap") + " || " + rule(".dock"));
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
