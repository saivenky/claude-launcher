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

const SRC = fs.readFileSync(path.join(path.resolve(__dirname, ".."), "web/board.js"), "utf8");

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
  append(...ns) { for (const n of ns) if (n != null) this.children.push(n); }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t, ev) { (this.listeners[t] || []).forEach((fn) => fn(ev || {})); }
  setAttribute(k, v) { this[k] = v; }
  focus() { doc.activeElement = this; }
  blur() { if (doc.activeElement === this) doc.activeElement = null; this.dispatch("blur", {}); }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
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
}
const doc = {
  activeElement: null,
  hidden: true,   // stops schedule() firing background polls; the tests drive poll() by hand
  _byId: {},
  getElementById(id) { return doc._byId[id] || (doc._byId[id] = new El("div")); },
  createElement(t) { return new El(t); },
  createTextNode(t) { return {__text: String(t)}; },
  addEventListener() {},
};

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
const ctxOf = {};        // sessionId -> contextHtml, so a test can change one
let etagN = 0;
const fetched = [];      // every URL board.js asked for
const respondLog = [];

function fakeBoard(focusSid) {
  const rank = {question: 0, approval: 0, yourmove: 1};
  const order = world.filter((s) => s.lane in rank)
    .sort((a, b) => (rank[a.lane] - rank[b.lane]) || (a.pri - b.pri));
  let focus = focusSid ? world.find((s) => s.sessionId === focusSid) : null;
  const pinned = !!focus;
  if (!focus) focus = order[0] || null;
  const strip = (s) => ({runId: s.runId, sessionId: s.sessionId, title: s.title, dir: "/p/" + s.title,
                         status: "", bridge: "", updatedAt: s.updatedAt, lane: s.lane, pri: s.pri, one: s.one});
  return {
    focus: focus ? Object.assign(strip(focus), {
      aiTitle: "about " + focus.title, contextHtml: ctxOf[focus.sessionId] || "<p>ctx</p>",
      ask: "what now?", options: [], cursor: 0, pendingInput: "", pinned,
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
  return res(200, {ok: true});
}

const store = {cl_token: "secret"};   // pre-seeded: Respond is token-gated (ADR 0007)
const sandbox = {
  document: doc, console,
  window: {prompt: () => "secret", confirm: () => true},
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
const ctxEl = () => focusWrap().querySelector(".ctx");
const shownSid = () => {   // the card prints sessionId[:8] in its meta line
  const c = card(); if (!c) return null;
  const meta = c.querySelector(".fmeta");
  return meta ? meta.textContent.slice(0, 8) : null;
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
  ctxEl().scrollTop = 120;
  const node = card();
  world.push(S(W, "working", 1, "worker"));
  world[2].updatedAt = 9999;
  await poll();
  ok("churn: an unrelated Run's update leaves the card alone", card() === node);
  ok("churn: the half-typed reply survives", ti().value === "half-typed reply",
     "got " + JSON.stringify(ti().value));
  ok("churn: the context scroll survives", ctxEl().scrollTop === 120, "got " + ctxEl().scrollTop);

  // When the Focus's own data does move, it rebuilds — carrying state across.
  ctxOf[A] = "<p>new context arrived</p>";
  await poll();
  ok("own change: the card is rebuilt", card() !== node);
  ok("own change: the reply is carried over", ti().value === "half-typed reply",
     "got " + JSON.stringify(ti().value));
  ok("own change: the scroll is carried over", ctxEl().scrollTop === 120, "got " + ctxEl().scrollTop);
  ok("own change: the new context is shown", ctxEl().innerHTML === "<p>new context arrived</p>");

  // A different Session is a different reply — never inherit the last one's.
  sandbox.setPinned(B);
  await settle();
  ok("switch: tapping a row moves the Focus", shownSid() === B.slice(0, 8), "got " + shownSid());
  ok("switch: the new card gets a clean box", ti().value === "", "got " + JSON.stringify(ti().value));
  sandbox.setPinned(A); await settle();
  ti().value = ""; ctxOf[A] = "<p>ctx</p>";

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

  console.log("\n  " + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
