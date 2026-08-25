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
// `seamTop` is the third: the landing measures the **Seam**'s viewport-relative
// top and parks it 250px down (board.js::landOn). No CSS runs here, so this is
// not where the seam IS — it is what the client is told it is, which is what
// makes the landing's arithmetic assertable. Where the seam actually lands on a
// 390×844 phone is a browser measurement and belongs in one.
// `askTop`/`askBottom`, `barTop` and `hintTop` are the fourth: a **Blocked**
// Focus's **Ask** renders BELOW the Scrollback, so the landing takes a FLOOR —
// park the seam, then scroll on until the Ask clears the bottom edge
// (board.js::clearFloor). That edge is the composer, or the swipe hint standing
// on it while that is still up, so the stub can place all three. The defaults put
// the bar at the bottom of the viewport, the hint far below everything and the
// Ask at the very top: the "nothing to clear" case every other test wants.
// `optTop`/`optBottom` are the fifth, and they are new with ADR 0020: the
// options are IN the Ask block now, each carrying its description, so the block
// runs hundreds of px past its own question. What the landing must clear is
// therefore the FIRST option, not the block's bottom — this is what lets a test
// tell those two numbers apart.
// `fheadHeight` is the sixth, and new with the header-condense port
// (board.js::headPad): the landing no longer copies the sticky header's
// height by hand, it measures `.fhead` at scroll time. Defaulting this to 0
// is the honest stub answer — no CSS runs here, so there is no real box to
// report — and it is also the case every OTHER landing test below wants: a
// pad of just the hair, so a test can move this one number to prove the pad
// tracks it rather than a literal.
const layout = {cardBottom: 0, barTop: 800, barHeight: 0, seamTop: 0,
                askTop: 0, askBottom: 0, optTop: 0, optBottom: 0,
                pendBottom: 0, hintTop: 1e6, fheadHeight: 0};
const rect = (b, t) => ({top: t || 0, left: 0, right: 0, width: 0, bottom: b, height: b});
// Mirrors board.js::HEAD_HAIR — the small margin `headPad` adds on top of
// whatever it measures `.fhead` at. The stub's `.fhead` has no real box (no
// CSS runs here), so `layout.fheadHeight` starts at 0 and this is the whole
// of what a landing "under the header" parks against by default; a test that
// moves `fheadHeight` proves the pad follows the measurement.
const HEAD_HAIR = 8;
const headPadStub = () => layout.fheadHeight + HEAD_HAIR;

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
  // No parser runs here, so `innerHTML` is kept verbatim for the ADR 0006
  // assertions AND flattened to its text, because the **Fold** derives every
  // line of a **Record** by rendering a turn into a DETACHED prose node and
  // reading `.textContent` (board.js::foldText). A stub that dropped the text
  // would make every Record blank and prove nothing about the fields.
  set innerHTML(v) {
    this._html = String(v);
    const txt = this._html.replace(/<[^>]*>/g, "");
    this.children = txt ? [{__text: txt}] : [];
  }
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
    // The bar has a TOP as well as a height now: syncBarHeight reads the height,
    // the landing's floor reads the top, and they are not the same number.
    if (cls.includes(" respond ")) {
      return {top: layout.barTop, left: 0, right: 0, width: 0,
              bottom: layout.barTop + layout.barHeight, height: layout.barHeight};
    }
    if (cls.includes(" swipehint ")) return rect(layout.hintTop, layout.hintTop);
    // board.js::headPad reads this at scroll time — full header or condensed,
    // whichever `.fhead` reports right now. `fheadHeight` defaults to 0, the
    // honest answer for a stub that runs no CSS.
    if (cls.includes(" fhead ")) return rect(layout.fheadHeight, 0);
    if (cls.includes(" ask ")) {
      return {top: layout.askTop, left: 0, right: 0, width: 0,
              bottom: layout.askBottom, height: layout.askBottom - layout.askTop};
    }
    // Every option answers with the same box; the landing only ever measures the
    // first one, which is the one it guarantees is on screen.
    if (cls.includes(" opt ")) {
      return {top: layout.optTop, left: 0, right: 0, width: 0,
              bottom: layout.optBottom, height: layout.optBottom - layout.optTop};
    }
    // The pending-input warning sits UNDER the Ask, and when there is one it is
    // what the floor has to clear: you must see that there is unsent text before
    // you type over it.
    if (cls.includes(" pending ")) return rect(layout.pendBottom, layout.askBottom);
    if (cls.includes(" seam ")) return rect(0, layout.seamTop);
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
["dirpop", "recover", "recovpanel", "toast", "swipehint", "peek", "zscrim", "iscrim"]
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
// Unsent text already in the Run's own input box. Steerable because the warning
// it draws is the other thing the landing's floor has to clear.
let pendingText = "";
// The **Ask Set** the server models and the phone draws one Ask of (ADR 0020) —
// `{}` for an approval, an idle Run and a permission menu, which is why it is
// steerable alongside the legacy `options`/`cursor` triple rather than instead
// of it. Those two still serve the permission menu, and `cursor: null` is a real
// value there: nobody read where the menu is standing.
let askSet = {};
let legacyOpts = [];
let legacyCursor = 0;
// Where the reply box's text would actually land (server.py::_text_route, ADR
// 0020). Steerable, because one of its values — `esc` — means sending prose
// CANCELS the ask rather than answering it, and the whole point is that the
// phone says so before the tap instead of after it. The server ships the NAME
// of the route and why; the keystroke count stays server-side.
let textRoute = {route: "plain", reason: ""};
const SB = () =>[{role: "user", html: "<p>which one?</p>"},
                  {role: "assistant", html: "<p>ctx</p>"}];
let etagN = 0;
const fetched = [];      // every URL board.js asked for
const respondLog = [];
const respondReplies = [];   // [status, body] per POST, in order; empty = plain 200
const transferLog = [];  // every body posted to api/transfer
const nickLog = [];      // every body posted to api/nickname — token-free by design
const priLog = [];       // every body posted to api/priority — the row's control and the Focus's share it
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
  // zone -> priority -> lane -> recency, the same nesting server.py::_board
  // sorts by: priority is a tier, Blocked precedes idle inside a tier, and
  // recency flips direction by lane (Blocked oldest-first, idle freshest-first).
  const order = world.filter((s) => s.lane in rank)
    .sort((a, b) => (a.pri - b.pri) || (rank[a.lane] - rank[b.lane])
                 || (rank[a.lane] === 0 ? (a.updatedAt || 0) - (b.updatedAt || 0)
                                        : (b.updatedAt || 0) - (a.updatedAt || 0)));
  let focus = focusSid ? world.find((s) => s.sessionId === focusSid) : null;
  const pinned = !!focus;
  if (!focus) focus = order[0] || null;
  // `nickname` rides BESIDE `one`, never substituted into it, and its absence is
  // `null` and never `""` — one representation for "no Nickname", so the client
  // has one truthiness check per surface (server.py::_nickname, ADR 0026).
  const strip = (s) => ({runId: s.runId, sessionId: s.sessionId, workspace: s.workspace,
                         dir: "/p/" + s.workspace,
                         status: "", bridge: "", updatedAt: s.updatedAt, lane: s.lane, pri: s.pri,
                         one: s.one, nickname: s.nickname || null});
  // The **Ask** is a property of being **Blocked** and of nothing else: server.py
  // blanks it off the question/approval lanes (ADR 0014). Mirrored here, or the
  // "no ask on an idle Focus" test would only be testing the fake.
  const blocked = focus && (focus.lane === "question" || focus.lane === "approval");
  return {
    focus: focus ? Object.assign(strip(focus), {
      aiTitle: "about " + focus.workspace, scrollback: sbOf[focus.sessionId] || SB(),
      ask: blocked ? "what now?" : "",
      options: blocked ? legacyOpts : [],
      cursor: blocked ? legacyCursor : null,
      askSet: blocked ? askSet : {},
      pendingInput: pendingText, pinned, textRoute,
    }) : null,
    upnext: order.filter((s) => s !== focus).map(strip),
    watching: world.filter((s) => s.lane === "working" && s !== focus).map(strip),
    // The two dim zones. They were hard-coded empty for as long as nothing on the
    // client could reach into them wrongly; `skip →` is the first verb that
    // could, so they have to be able to hold a row before the test that says it
    // does not. `rank` names neither lane, so neither ever enters `order` — which
    // is the server's own shape: `snoozed` and `dormant` are lifted out before
    // the triage list is built at all (server.py::_board).
    snoozed: world.filter((s) => s.lane === "snoozed" && s !== focus).map(strip),
    dormant: world.filter((s) => s.lane === "dormant" && s !== focus).map(strip),
    // A Foreign Run carries a Nickname too, and its absence is `null` here for
    // the same reason it is null on a queue row: one representation of "no
    // Nickname" (server.py::_foreign_items, ADR 0026).
    foreign: foreignWorld.map((s) => Object.assign({}, s, {nickname: s.nickname || null})),
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
  if (url === "api/respond") {
    respondLog.push(JSON.parse(opts.body));
    // Queued replies, consumed one per POST: the server refuses a send it would
    // have to route through `Esc` and hands back a 409 naming the route, so a
    // test has to be able to make the FIRST send fail and the retry succeed.
    return respondReplies.length ? res(...respondReplies.shift()) : res(200, {ok: true});
  }
  if (url === "api/transfer") {
    transferLog.push(JSON.parse(opts.body));
    return res(transferReply.status, transferReply.body);
  }
  // The store the real server keeps, in one line: keyed by sessionId, empty
  // clears, and the next poll shows it. Applied to `world` rather than merely
  // recorded, so the tests can assert the round trip and not just the request.
  // Same shape as the nickname store below, and applied to `world` for the same
  // reason: the row's priority control is only interesting if the level it sets
  // comes BACK on the next poll and redraws the glyph. Recording the request
  // alone would prove the tap fired and nothing about what the row then said.
  if (url === "api/priority") {
    const b = JSON.parse(opts.body);
    priLog.push(b);
    const s = world.find((w) => w.sessionId === b.sessionId);
    if (s) s.pri = {high: 0, normal: 1, low: 2}[b.level];
    return res(200, {ok: true});
  }
  if (url === "api/nickname") {
    const b = JSON.parse(opts.body);
    nickLog.push(b);
    const s = world.find((w) => w.sessionId === b.sessionId);
    if (s) s.nickname = b.nickname || null;
    return res(200, {ok: true});
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
             // Every unfold above the read is anchored on its own node's top
             // (board.js::keepAnchored). With no CSS the stub's boxes never move,
             // so this can only ever be a no-op here — the 0px of drift ADR 0017
             // measures is a browser number, not one this file can produce.
             scrollBy: (x, y) => { win.scrollY = Math.max(0, win.scrollY + y); },
             innerHeight: 800,
             // The composer measures itself off this (board.js::growComposer). The
             // stub answers with the metrics `.ti` sets in board.html, since a
             // textarea's height is the one thing on this page the client has to
             // compute instead of declare.
             getComputedStyle: () => boxStyle,
             _lis: {},
             addEventListener(t, fn) { (win._lis[t] = win._lis[t] || []).push(fn); },
             dispatch(t, ev) { (win._lis[t] || []).forEach((fn) => fn(ev || {})); }};
// A phone has a haptic and this file has to be able to hear it: arming fires it
// once per arm (board.js::haptic), which is a claim about a state machine and not
// about hardware. Nothing else on the page vibrates.
const vibrateLog = [];
const sandbox = {
  document: doc, console,
  window: win,
  navigator: {vibrate: (ms) => { vibrateLog.push(ms); return true; }},
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
const S = (sid, lane, pri, workspace) => ({sessionId: sid, runId: "r-" + sid, lane,
                                           pri: pri === undefined ? 1 : pri,
                                           workspace, updatedAt: 1000, one: "one-" + workspace});
const A = "aaaaaaaa-1111-1111-1111-111111111111";
const B = "bbbbbbbb-2222-2222-2222-222222222222";
const W = "wwwwwwww-3333-3333-3333-333333333333";
const C = "cccccccc-4444-4444-4444-444444444444";

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

  // THE TWO COPIES OF THE TRIAGE SORT HAVE TO AGREE. The zone arrives already
  // sorted by the server and is never re-sorted here; only the **Focus** is
  // spliced back in, at the index `sortsBefore.upnext` computes. So the ring is
  // the one place the client's copy can be caught disagreeing — and `C` is the
  // case that catches it: a `high` **idle** Run must sit above a `normal`
  // **Blocked** one, which is false under the old zone -> lane -> priority
  // nesting and true under zone -> priority -> lane -> recency. The Focus (`A`,
  // normal and Blocked) is the row it has to be spliced past.
  world.push(S(C, "yourmove", 0, "charlie"));
  await poll();
  ok("triage order: priority outranks lane, and the Focus splices in by the same rule",
     sandbox.ringOrder().join(",") === [B, C, A].join(","),
     "got " + sandbox.ringOrder().join(","));
  world.pop();
  await poll();

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
  sbOf[A] = SB().concat([{role: "assistant", html: "<p>a new turn arrived</p>"}]);
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
  // The toast used to read `f.title`, a field ADR 0023 removed from the payload —
  // every successful reply toasted the literal string "session is now working"
  // rather than naming anything. Workspace when there is no Nickname yet.
  ok("respond: the toast names the Run by Workspace, not the literal 'session'",
     doc.getElementById("toast").textContent === "✓ sent — bravo is now working",
     JSON.stringify(doc.getElementById("toast").textContent));

  // A Nickname, when there is one, wins over the Workspace here too — the same
  // chain as every other surface that names a Run (ADR 0026).
  world.find((s) => s.sessionId === B).nickname = "the release notes";
  await poll();
  ti().value = "ship it again";
  focusWrap().querySelector(".send").dispatch("click");
  await settle();
  ok("respond: the toast names the Run by Nickname when there is one",
     doc.getElementById("toast").textContent === "✓ sent — the release notes is now working",
     JSON.stringify(doc.getElementById("toast").textContent));

  // --- Foreign Runs: visible, never drivable (ADR 0012) ---------------------
  // A `claude` started by hand at the Mac. It arrives on its own payload key, so
  // the assertions that matter are the negative ones: it is not in the queue, it
  // cannot be answered, and none of the Focus discipline above moves because of it.
  const F = "ffffffff-4444-4444-4444-444444444444";
  foreignWorld = [{sessionId: F, workspace: "mine", dir: "~/projects/mine", status: "waiting",
                   bridge: "session_abc", updatedAt: 1000, one: "the last thing it said"}];
  const held = shownSid();   // B, from the respond test above
  await poll();
  const fr = findAll(zones(), "frow");
  ok("foreign: it gets a row of its own", fr.length === 1, "rows: " + fr.length);
  ok("foreign: showing workspace, dir, status and last message",
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

  // --- The Focus is a Scrollback (ADR 0014), grouped by speaker (ADR 0016) ---
  // The card renders the Session's recent entries — what you said, what it did
  // and what it then said. A contiguous stretch of assistant prose and **work
  // runs** is ONE claude block; only something you did breaks it.
  const T = "77777777-5555-5555-5555-555555555555";
  foreignWorld = [];
  world = [S(T, "yourmove", 1, "scroll")];
  // Three **Exchanges** (ADR 0017), so the two older ones fold to **Records** and
  // the newest is the read. The tool calls sit in the FIRST one, where they are
  // what the Record's `work` line is made of, and again in the last, where they
  // are the ADR 0016 collapsible.
  const WCALLS = [{name: "Bash", detail: "git status --short"},
                  {name: "Bash", detail: "git commit -m wip"},
                  // An injection attempt riding `detail` — the field that is NOT
                  // the innerHTML'd one, in both renderings of it.
                  {name: "Read", detail: "<img src=x onerror=alert(1)>"}];
  sbOf[T] = [
    {role: "user", html: "<p>consolidate the notes</p>"},
    {role: "work", n: 9, calls: WCALLS},
    {role: "assistant", html: "<p>Read both notes. Which of the two titles do you want on it?</p>"},
    {role: "command", cmd: "/ship"},
    {role: "assistant", html: "<p>Shipped. Nothing else was touched.</p>"},
    {role: "user", html: "<p>now the tests</p>"},
    {role: "assistant", html: "<p>on it</p>"},
    // A **work run**: nine calls, of which the last three kept their detail.
    {role: "work", n: 9, calls: WCALLS},
    {role: "assistant", html: "<p>done — <strong>3 files</strong></p>"},
  ];
  sandbox.setPinned(T);
  await settle();
  // The Exchange you are standing in: the run-up rows, the **Seam**, and the live
  // prose under it. Scoped to that, not to the whole Scrollback — an opened
  // **Record** renders its own Exchange's work runs, and ADR 0016 keys open state
  // by a run's FIRST CALL, so two runs that started the same way open together.
  const nowEx = () => findAll(sbEl(), "now")[0];
  const runs = () => findAll(nowEx(), "work");
  const lines = () => findAll(nowEx(), "wline");
  const seam = () => findAll(sbEl(), "seam")[0];
  // A **Record** and its three lines. `recs()[0]` is the oldest Exchange, which
  // is the order everything on this page counts in.
  const recs = () => findAll(sbEl(), "rec");
  const labels = (r) => findAll(r, "rl").map((s) => s.textContent);
  const values = (r) => findAll(r, "rv").map((s) => s.textContent);

  // ADR 0016's `claude` caption is SUPPRESSED, not undone (ADR 0017): above the
  // **Seam** the 40px label gutter says who, once per row, and below it the seam
  // itself does — so a contiguous assistant stretch still repays no caption.
  ok("scrollback: no caption repeats over the read — the gutter and the seam say who",
     findAll(sbEl(), "who").length === 0 && findAll(sbEl(), "chain").length === 0 &&
     findAll(sbEl(), "live").length === 1,
     findAll(sbEl(), "who").length + " captions");
  // Your prompt rides the same gutter as the Records above rather than the
  // `.turn you` bubble, so the column runs unbroken from the top of the fold to
  // the seam and a one-word prompt costs one line.
  ok("scrollback: the current prompt is a gutter row, not a bubble",
     findAll(sbEl(), "turn").length === 0 && findAll(sbEl(), "nask").length === 1 &&
     findAll(findAll(sbEl(), "nhead")[0], "rl")[0].textContent === "you",
     findAll(sbEl(), "nask").length + " prompts");

  // The ADR 0006 / ADR 0003 split, in one pair of assertions: the ONE field the
  // server rendered escape-first becomes markup; every other field of the same
  // entry is untrusted text and cannot become an element.
  ok("scrollback: an entry's html reaches the DOM as markup (ADR 0006)",
     findAll(sbEl(), "md")[0].innerHTML === "<p>now the tests</p>",
     JSON.stringify(findAll(sbEl(), "md").map((m) => m.innerHTML)));

  // A working Run emits long stretches of tool calls with no prose. One line for
  // the whole run, naming how many steps and roughly what of — never one slot
  // per call, which is what was evicting the prose.
  ok("scrollback: a work run is one collapsed line, not one row per call",
     runs().length === 1 && lines().length === 0 &&
     runs()[0].textContent.includes("9 steps"),
     JSON.stringify(runs().map((r) => r.textContent)));
  ok("scrollback: the collapsed summary skims the run and dedupes it",
     runs()[0].textContent.includes("git ×2"), JSON.stringify(runs()[0].textContent));

  findAll(sbEl(), "roll")[0].onclick();
  ok("scrollback: tapping a work run opens it, one line per kept call",
     lines().length === 3 && findAll(sbEl(), "wname")[0].textContent === "Bash" &&
     findAll(sbEl(), "wdetail")[0].textContent === "git status --short",
     JSON.stringify(lines().map((l) => l.textContent)));
  ok("scrollback: an opened run says how many calls it did not keep",
     findAll(sbEl(), "wmore")[0].textContent.includes("6 earlier"),
     JSON.stringify(findAll(sbEl(), "wmore").map((m) => m.textContent)));
  const detail = findAll(sbEl(), "wdetail")[2];
  ok("scrollback: a call's detail cannot inject — textContent, never innerHTML (ADR 0003)",
     detail.textContent === "<img src=x onerror=alert(1)>" && detail.innerHTML === "",
     JSON.stringify([detail.textContent, detail.innerHTML]));

  // The rail marks the reply you are answering. Below the seam that is all there
  // is, so the rail moved onto the live tail rather than onto a block.
  ok("scrollback: the prose below the seam wears the live rail",
     findAll(sbEl(), "live").length === 1 &&
     findAll(sbEl(), "live")[0].textContent.includes("done — 3 files"),
     findAll(sbEl(), "live").map((l) => l.textContent).join(" | "));

  // An opened run must survive the card being rebuilt, because on a working Run
  // that happens every poll — a run that re-collapsed under you every four
  // seconds would be unusable. Which is why the open set is module state keyed
  // by the run's own first call, not something the card carries.
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and then this</p>"}]);
  await poll();
  ok("scrollback: an opened run stays open when the Focus's data moves",
     lines().length === 3, JSON.stringify(lines().map((l) => l.textContent)));

  // ...but it belongs to the Session it was opened in.
  const T2 = "77777777-6666-6666-6666-666666666666";
  world = [S(T, "yourmove", 1, "scroll"), S(T2, "yourmove", 1, "other")];
  sbOf[T2] = [{role: "assistant", html: "<p>elsewhere</p>"}];
  sandbox.setPinned(T2);
  await poll();
  sandbox.setPinned(T);
  await poll();
  ok("scrollback: and it does not follow you to another Session",
     lines().length === 0, JSON.stringify(lines().map((l) => l.textContent)));
  world = [S(T, "yourmove", 1, "scroll")];
  await poll();

  // --- The Scrollback folds into Exchanges (ADR 0017) -----------------------
  // An **Exchange** opens on something YOU did and runs to the next such thing —
  // the same boundary ADR 0016 uses to break a claude block, so the grouping is
  // mechanical and costs the payload nothing. The newest is the read; every
  // older one is a **Record**: three lines in one 40px label gutter.
  ok("fold: everything older than the exchange you are in is one record each",
     recs().length === 2 && findAll(sbEl(), "now").length === 1,
     recs().length + " records");
  // Graded by distance from now: the run-up inside the Exchange you are standing
  // in is one gutter row per thing Claude said, and only the live tail is prose.
  ok("fold: and the newest exchange is rows above the seam, prose below it",
     findAll(sbEl(), "inrow").length === 2 &&
     findAll(findAll(sbEl(), "inrow")[0], "rv")[0].textContent === "on it" &&
     findAll(sbEl(), "live")[0].textContent.includes("and then this"),
     findAll(sbEl(), "inrow").length + " run-up rows / " +
     findAll(sbEl(), "live")[0].textContent);

  // The fixed shape is the point: the same three words in the same column on
  // every Record, so the eye can run down it without reading a value.
  ok("fold: a record is three lines — you, work, claude",
     labels(recs()[0]).join("/") === "you/work/claude", labels(recs()[0]).join("/"));
  ok("fold: `you` is your prompt",
     values(recs()[0])[0] === "consolidate the notes", JSON.stringify(values(recs()[0])));
  // Run-length-encoded across the WHOLE Exchange, not inside one run: across five
  // runs `git` would otherwise be named three times. The count and the labels
  // come from different levels — `n` is the true number of calls.
  ok("fold: `work` is the exchange's calls, deduped, with the true count",
     values(recs()[0])[1] === "git ×2, <img" &&
     findAll(recs()[0], "rgear")[0].textContent === "⚙9",
     JSON.stringify(values(recs()[0])) + " " +
     JSON.stringify(findAll(recs()[0], "rgear").map((g) => g.textContent)));

  // A line with nothing to say is omitted, never blank — three empty labels
  // would make the gutter noise instead of a landmark.
  ok("fold: a line whose content is absent is omitted, not left blank",
     labels(recs()[1]).join("/") === "you/claude" &&
     findAll(recs()[1], "rgear").length === 0, labels(recs()[1]).join("/"));
  ok("fold: a slash command opens an exchange too, and titles its record",
     values(recs()[1])[0] === "/ship", JSON.stringify(values(recs()[1])));

  // Teal when that reply closed by putting a question to YOU. In chronological
  // order the answer to it is the very next row DOWN — which is the relation an
  // inverted order destroys, and why the order is not negotiable.
  ok("fold: a record goes teal when that reply put a question to you",
     hasCls(recs()[0], "recq") &&
     values(recs()[0])[2] === "Which of the two titles do you want on it?",
     recs()[0].className + " " + JSON.stringify(values(recs()[0])));
  ok("fold: and a reply that asked nothing is its first sentence, not teal",
     !hasCls(recs()[1], "recq") && values(recs()[1])[1] === "Shipped.",
     recs()[1].className + " " + JSON.stringify(values(recs()[1])));

  // Every line of every Record is DERIVED from a turn's html by rendering it
  // into a detached prose node and reading .textContent (board.js::foldText), so
  // the one innerHTML sink is still proseEl's. Proved by walking the whole
  // Scrollback: nothing but a `.md` may carry markup.
  const sinks = () => {
    const out = [];
    const walk = (n) => {
      for (const c of (n.children || [])) {
        if (!(c instanceof El)) continue;
        if (c.innerHTML) out.push(c.className + " := " + c.innerHTML);
        walk(c);
      }
    };
    walk(sbEl());
    return out;
  };
  ok("fold: no transcript text reaches an innerHTML sink except through proseEl",
     sinks().every((s) => s.startsWith("md ")) && sinks().length > 0,
     JSON.stringify(sinks()));
  ok("fold: so a record's own lines are text, and injection-shaped text stays text",
     findAll(recs()[0], "rv").every((s) => s.innerHTML === "") &&
     values(recs()[0])[1].includes("<img"),
     JSON.stringify(findAll(recs()[0], "rv").map((s) => s.innerHTML)));

  // Tap it and it opens in place — to PROSE, not to a second set of rows: you
  // already said which Exchange you wanted by tapping it.
  findAll(recs()[1], "rhd")[0].onclick();
  ok("fold: tapping a record opens it in place, to the whole exchange as prose",
     hasCls(recs()[1], "recopen") && findAll(recs()[1], "rbody").length === 1 &&
     findAll(recs()[1], "md")[0].innerHTML === "<p>Shipped. Nothing else was touched.</p>",
     recs()[1].className);
  ok("fold: an opened record still names the slash command it opened on (ADR 0016)",
     findAll(recs()[1], "cmd").length === 1 &&
     findAll(recs()[1], "cmd")[0].textContent.includes("/ship"),
     JSON.stringify(findAll(recs()[1], "cmd").map((c) => c.textContent)));
  ok("fold: and the folded lines it replaces are gone while it is open",
     labels(recs()[1]).join("/") === "you", labels(recs()[1]).join("/"));

  // The Focus card is rebuilt on every poll of a working Run, so which Records
  // are open is module state keyed by CONTENT — the same reason and the same
  // idiom as ADR 0016's `openRuns`. An index would name a different Exchange
  // four seconds later: the Scrollback is a sliding window.
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and one more</p>"}]);
  await poll();
  ok("fold: an opened record stays open when the Focus's data moves",
     recs().length === 2 && hasCls(recs()[1], "recopen"),
     recs().map((r) => r.className).join(" | "));
  world = [S(T, "yourmove", 1, "scroll"), S(T2, "yourmove", 1, "other")];
  sandbox.setPinned(T2); await poll();
  sandbox.setPinned(T); await poll();
  ok("fold: but it does not follow you to another Session",
     recs().every((r) => !hasCls(r, "recopen")),
     recs().map((r) => r.className).join(" | "));

  findAll(recs()[1], "rhd")[0].onclick();
  findAll(recs()[1], "rhd")[0].onclick();
  ok("fold: and tapping it again folds it back to the same three lines",
     !hasCls(recs()[1], "recopen") && labels(recs()[1]).join("/") === "you/claude",
     recs()[1].className + " " + labels(recs()[1]).join("/"));

  // An Exchange whose opening turn has slid out of the window is a real
  // Exchange with no prompt — labelled as such rather than hidden, because the
  // Scrollback is a bounded tail and this is what its top edge looks like.
  const T3 = "77777777-7777-7777-7777-777777777777";
  world = [S(T3, "yourmove", 1, "lead")];
  sbOf[T3] = [
    {role: "assistant", html: "<p>Picked it back up. All four suites are green.</p>"},
    {role: "user", html: "<p>carry on</p>"},
    {role: "assistant", html: "<p>Carrying on.</p>"},
  ];
  sandbox.setPinned(T3);
  await poll();
  ok("fold: a leading assistant entry is an exchange whose prompt slid out",
     recs().length === 1 && labels(recs()[0]).join("/") === "you/claude" &&
     values(recs()[0])[0] === "(prompt is off the top of the window)",
     JSON.stringify(values(recs()[0])));

  // A prompt you sent a second ago with nothing back yet is not an Exchange to
  // fold the read behind: folding the reply you are still reading the moment you
  // answer it would be the landing bug wearing the other mask.
  sbOf[T3] = sbOf[T3].concat([{role: "user", html: "<p>and the docs</p>"}]);
  await poll();
  // It sits BELOW the read, where you left it, and takes the NEXT number — the
  // count never runs backwards.
  ok("fold: a prompt with nothing back yet does not fold the read behind it",
     recs().length === 1 && findAll(sbEl(), "pend").length === 1 &&
     findAll(findAll(sbEl(), "pend")[0], "rn")[0].textContent === "3",
     recs().length + " records, " + findAll(sbEl(), "pend").length + " pending");

  // --- The **Seam**, and the landing (ADR 0017) ------------------------------
  // The `NEWEST` rule cuts the **Fold** from the live prose, and the page lands
  // on it — parked 250px down, so the tail of the run-up peeks above and the
  // reader learns there IS a fold. The stub runs no CSS, so where the seam
  // actually sits on a 390×844 phone is a browser measurement; what IS provable
  // here is the arithmetic and, far more importantly, WHEN the landing fires.
  layout.seamTop = 900;
  world = [S(T, "yourmove", 1, "scroll"), S(T2, "yourmove", 1, "other")];
  sandbox.setPinned(T2); await poll();
  win.scrollY = 0;
  sandbox.setPinned(T); await poll();

  const strip = () => findAll(sbEl(), "ftop")[0];
  const fbtns = () => findAll(strip(), "fbtn");
  const nums = () => findAll(sbEl(), "rn").map((n) => n.textContent);

  ok("seam: a NEWEST rule cuts the fold from the live read",
     !!seam() && findAll(seam(), "seaml")[0].textContent === "newest" &&
     findAll(sbEl(), "live").length === 1, seam() && seam().textContent);
  // 4 things Claude said on the way here, and 9 calls under them — the tally is
  // what is folded ABOVE the seam, which is the peek the landing buys.
  ok("seam: and it carries the tally of what is folded above it",
     findAll(seam(), "seamn")[0].textContent === "4 above · ⚙9",
     findAll(seam(), "seamn").map((n) => n.textContent).join("|"));

  ok("landing: the page parks the seam 250px down, not against the header",
     win.scrollY === layout.seamTop - 250, String(win.scrollY));

  // Once per **Scrollback**, not once per poll. The Focus card is rebuilt every
  // time its payload moves — on a working Run that is every four seconds — and a
  // page that re-scrolled itself each time would be unusable.
  world[0].updatedAt = 1001;
  win.scrollY = 300;
  await poll();
  ok("landing: a rebuild whose scrollback did not move does not re-land you",
     win.scrollY === 300, String(win.scrollY));
  ok("landing: and the reading position survives that rebuild",
     recs().length === 2 && !!seam(), String(recs().length));

  // A new entry DOES re-land you — but only because you were still parked where
  // the last landing left you, i.e. still reading the end.
  win.scrollY = layout.seamTop - 250;
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and a newer one</p>"}]);
  await poll();
  ok("landing: a new entry re-lands you while you are parked at the end",
     win.scrollY === (layout.seamTop - 250) + layout.seamTop - 250, String(win.scrollY));

  // ...and does not, once you have scrolled up into history with a Record
  // half-read. An auto-scroll that yanks a reader is the baseline's bug wearing
  // the other mask (ADR 0017).
  win.scrollY = 200;
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and a newer one still</p>"}]);
  await poll();
  ok("landing: but a reader who scrolled away is never yanked back",
     win.scrollY === 200, String(win.scrollY));

  // A Run you swiped to is a read you have travelled nowhere in, so it has no
  // place to keep and lands unconditionally.
  sandbox.setPinned(T2); await poll();
  ok("landing: switching Focus lands the new Session whatever the old one left",
     win.scrollY === 200 + layout.seamTop - headPadStub(), String(win.scrollY));
  // Nothing above the seam at all — one Exchange, still being answered — means
  // there is no peek to buy, so it parks under the header instead.
  ok("landing: with nothing folded above it the seam goes under the header",
     findAll(sbEl(), "ftop").length === 0 && findAll(sbEl(), "inrow").length === 0);

  win.scrollY = 0;
  sandbox.setPinned(T); await poll();

  // The strip: how much is up there, and the two blunt moves. Everything on it
  // points ONE way, because the order is chronological and down is later.
  ok("strip: it names what is earlier, and how much of it",
     findAll(strip(), "ftopl")[0].textContent === "earlier" &&
     findAll(strip(), "ftopn")[0].textContent === "2 exchanges",
     strip().textContent);
  ok("strip: the number column ascends, and the exchange you are in takes the next",
     nums().join("/") === "1/2/3", nums().join("/"));

  // `read all` restores the linear read; pressing it again folds it back. It is
  // anchored on the strip it lives in — anchoring the seam would fire the reader
  // to the bottom of a page that just quadrupled — which no CSS-less DOM can
  // prove, so what is asserted here is the toggle.
  ok("read all: the strip offers it, and the jump back to the seam",
     fbtns().map((b) => b.textContent).join("/") === "read all/↓ newest",
     fbtns().map((b) => b.textContent).join("/"));
  fbtns()[0].onclick();
  ok("read all: it unfolds every record at once and offers the way back",
     recs().every((r) => hasCls(r, "recopen")) && fbtns()[0].textContent === "collapse all",
     recs().map((r) => r.className).join(" | "));
  fbtns()[0].onclick();
  ok("read all: and collapse all folds the lot back to three lines each",
     recs().every((r) => !hasCls(r, "recopen")) && fbtns()[0].textContent === "read all",
     recs().map((r) => r.className).join(" | "));

  win.scrollY = 0;
  fbtns()[1].onclick();
  ok("read all: `↓ newest` goes exactly where the landing put you",
     win.scrollY === layout.seamTop - 250, String(win.scrollY));

  // A run-up row — distance 1 of the **Fold**: one gutter row per thing Claude
  // said on the way here, opening in place to that entry's prose.
  const inrows = () => findAll(sbEl(), "inrow");
  ok("run-up: each thing Claude said on the way here is one gutter row",
     inrows().length >= 1 &&
     findAll(inrows()[0], "rl")[0].textContent === "claude" &&
     findAll(inrows()[0], "rv")[0].textContent === "on it",
     inrows().map((r) => r.textContent).join(" | "));
  findAll(inrows()[0], "rhd")[0].onclick();
  ok("run-up: and tapping one opens it to that entry's prose, in place",
     hasCls(inrows()[0], "inopen") &&
     findAll(inrows()[0], "md")[0].innerHTML === "<p>on it</p>",
     inrows()[0].className);
  // Same reason and same idiom as openRuns and openRecords: the card is rebuilt
  // every poll, so the open set is module state keyed by content.
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>later still</p>"}]);
  await poll();
  ok("run-up: an opened row stays open when the Focus's data moves",
     hasCls(inrows()[0], "inopen"), inrows()[0].className);
  sandbox.setPinned(T2); await poll();
  sandbox.setPinned(T); await poll();
  ok("run-up: but it does not follow you to another Session",
     inrows().every((r) => !hasCls(r, "inopen")),
     inrows().map((r) => r.className).join(" | "));

  // --- The edges the Fold meets off the happy path (slice 04) ---------------
  //
  // OPENING A RECORD IS GOING INTO HISTORY, AND THE ANCHOR HID THAT. Every
  // unfold is anchored on its own node's top, which is the ADR's 0px of drift —
  // and that success is exactly what defeated the "are you still parked where
  // the landing left you" test, because a reader two screens up in a Record they
  // just opened has moved the page by 0px. Measured in a browser on a growing
  // Run: open a Record, wait one 4s poll, and the page jumped 984px back down
  // onto the seam, with the Record still open behind it (ADR 0017: "scroll up
  // into history with a Record half-read and you keep your place").
  win.scrollY = layout.seamTop - 250;   // parked exactly where the landing left you
  findAll(recs()[0], "rhd")[0].onclick();
  const heldAt = win.scrollY;
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>arriving mid-read</p>"}]);
  await poll();
  ok("landing: opening a Record holds it — an anchored unfold is still travel",
     win.scrollY === heldAt && hasCls(recs()[0], "recopen"),
     win.scrollY + " / " + recs()[0].className);
  // `↓ newest` is the reader saying they are done in history, so it is also the
  // one move that gives the landing back.
  fbtns()[1].onclick();
  const rearmed = win.scrollY;   // where that button put the page
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and one after that</p>"}]);
  await poll();
  ok("landing: and `↓ newest` re-arms it, so the next entry follows you down again",
     win.scrollY === rearmed + layout.seamTop - 250, String(win.scrollY));

  // A **Blocked** Focus. `focus.ask` renders BELOW the Scrollback, so a landing
  // that parks the seam 250px down and stops leaves the one thing you came to
  // answer under the composer: measured at 390×844, seam at 250, 535px of live
  // prose, the Ask's top at 778 behind a composer whose top was 744. The option
  // buttons were the only part that was fine — they ride the sticky `.respond` —
  // so the page offered three answers to a question it was not showing.
  //
  // The stub runs no CSS and its boxes do not move when the page scrolls, so
  // what is provable here is the ARITHMETIC the client applies; where the Ask
  // actually sits on a phone is a browser measurement, as the seam's is.
  const parked = () => layout.seamTop - 250;
  layout.barTop = 700; layout.barHeight = 100;   // the composer's top edge
  layout.askTop = 820; layout.askBottom = 900;   // ...and the Ask, wholly under it
  world = [S(T, "question", 1, "scroll"), S(T2, "yourmove", 1, "other")];
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  ok("landing: a Blocked Focus scrolls on until its Ask clears the composer",
     win.scrollY === parked() + (layout.askBottom - (layout.barTop - 10)),
     String(win.scrollY));
  // What gives way is the PEEK and only the peek. The extra is capped at the
  // seam's own distance from the sticky header, so the newest prose — the reply
  // the Ask is asking about — is never scrolled off the top to make room.
  layout.askBottom = 9000;   // an Ask far taller than the peek could ever buy
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  ok("landing: and the peek is all it may spend — the newest prose is never lost",
     win.scrollY === parked() + (layout.seamTop - headPadStub()), String(win.scrollY));
  layout.askBottom = 900;

  // The swipe hint is a FIXED, opaque strip standing on the composer until the
  // first swipe, so while it is up it is the real bottom edge — and it was
  // covering the Ask's second line.
  layout.hintTop = 600;
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  ok("landing: the swipe hint is part of that edge while it is still up",
     win.scrollY === parked() + (layout.askBottom - (layout.hintTop - 10)),
     String(win.scrollY));
  layout.hintTop = 1e6;

  // Unsent text already in the Run's own box is the other thing you must see
  // before you type over it, and it sits under the Ask — so it is what the
  // floor clears when there is one.
  pendingText = "half a sentence I never sent";
  layout.pendBottom = 1000;
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  ok("landing: and the pending-input warning under it is what the floor clears",
     findAll(focusWrap(), "pending").length === 1 &&
     win.scrollY === parked() + (layout.pendBottom - (layout.barTop - 10)),
     String(win.scrollY));
  pendingText = "";
  layout.pendBottom = 0;

  // Nothing on an unblocked Focus renders below the read, so there is no floor
  // there and the seam's 250px stands exactly as ADR 0017 measured it.
  world[0].lane = "yourmove";
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  ok("landing: an unblocked Focus has nothing below the read, so 250px stands",
     win.scrollY === parked(), String(win.scrollY));

  // `↓ newest` claims to go exactly where the landing put you, so on a Blocked
  // Focus it has to replay the floor too, not just the pad.
  world[0].lane = "question";
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(T); await poll();
  win.scrollY = 0;
  fbtns()[1].onclick();
  ok("landing: `↓ newest` replays that floor, not just the seam's 250px",
     win.scrollY === layout.seamTop - 250 + (layout.askBottom - (layout.barTop - 10)),
     String(win.scrollY));
  layout.barTop = 800; layout.askTop = 0; layout.askBottom = 0;
  world[0].lane = "yourmove";

  // A TINY Session — one Exchange, two entries, nothing folded above at all.
  // The strip, the Records and the tally must all be absent rather than empty,
  // and the seam parks under the header because there is no peek to buy.
  const tiny = "99999999-9999-9999-9999-999999999999";
  world = [S(tiny, "yourmove", 1, "tiny")];
  sbOf[tiny] = [{role: "user", html: "<p>is this the whole session?</p>"},
                {role: "assistant", html: "<p>yes — two entries and nothing else.</p>"}];
  win.scrollY = 0; sandbox.setPinned(tiny); await poll();
  ok("tiny: with one Exchange there is no strip, no record and no tally",
     findAll(sbEl(), "ftop").length === 0 && recs().length === 0 &&
     findAll(seam(), "seamn").length === 0 && findAll(sbEl(), "inrow").length === 0,
     sbEl().textContent);
  ok("tiny: but the seam and the read below it are still there, and honest",
     findAll(seam(), "seaml")[0].textContent === "newest" &&
     findAll(sbEl(), "live")[0].textContent.includes("two entries"),
     findAll(sbEl(), "live").map((l) => l.textContent).join("|"));
  ok("tiny: and with no peek to buy it parks under the header, not 250px down",
     win.scrollY === layout.seamTop - headPadStub(), String(win.scrollY));

  // The pad follows the header's live height rather than a literal: give the
  // stub's `.fhead` a real box — as it would be at a condensed height, say —
  // and the exact same "no peek" landing must park against IT, not against
  // the hair alone. This is board.js::headPad's whole reason to exist.
  //
  // A landing fires once per scrollback, so a fresh, unconditional landing —
  // the kind a Focus you have just swiped to gets — is what proves the point
  // without also proving something about the "still parked" arithmetic
  // (which the cumulative-landing test above already owns).
  layout.fheadHeight = 31;   // roughly the condensed header's own height
  world = [S(tiny, "yourmove", 1, "tiny"), S(T2, "yourmove", 1, "other")];
  win.scrollY = 0; sandbox.setPinned(T2); await poll();
  win.scrollY = 0; sandbox.setPinned(tiny); await poll();
  ok("tiny: the pad tracks the measured header, not a hand-copied number",
     win.scrollY === layout.seamTop - headPadStub(), String(win.scrollY));
  layout.fheadHeight = 0;
  world = [S(tiny, "yourmove", 1, "tiny")];

  // One entry, nothing back yet: an Exchange with a prompt and no reply. Still
  // the read, never a Record — folding the thing you just sent behind a summary
  // would be the baseline's bug wearing the other mask.
  sbOf[tiny] = [{role: "user", html: "<p>just sent this</p>"}];
  world[0].updatedAt = 4242;
  await poll();
  ok("tiny: a lone prompt with nothing back is the read, and says so",
     recs().length === 0 && findAll(sbEl(), "now").length === 1 &&
     findAll(sbEl(), "live")[0].textContent.includes("nothing back yet"),
     sbEl().textContent);

  // A WORK RUN CROSSING THE SEAM. The Fold grades by distance from now, so an
  // entry moves: a **work run** that was prose-side of the seam becomes a
  // `.wkrow` in the run-up the moment a newer reply arrives. It must not
  // re-collapse on the way — ADR 0017 promises one mechanism and not a second,
  // and it is the same `openRuns` on both sides of the seam.
  const X = "88888888-8888-8888-8888-888888888888";
  world = [S(X, "yourmove", 1, "cross"), S(T2, "yourmove", 1, "other")];
  sbOf[X] = [{role: "user", html: "<p>run the suite</p>"},
             {role: "assistant", html: "<p>on it</p>"},
             {role: "work", n: 2, calls: [{name: "Bash", detail: "pytest -q"},
                                          {name: "Read", detail: "board.js"}]},
             {role: "assistant", html: "<p>green</p>"}];
  sandbox.setPinned(X); await poll();
  const wlines = () => findAll(sbEl(), "wline");
  ok("cross: a work run whose reply is the newest sits BELOW the seam, as prose",
     findAll(sbEl(), "wkrow").length === 0 &&
     findAll(findAll(sbEl(), "live")[0], "work").length === 1,
     sbEl().textContent);
  findAll(sbEl(), "roll")[0].onclick();
  sbOf[X] = sbOf[X].concat([{role: "assistant", html: "<p>and pushed</p>"}]);
  await poll();
  ok("cross: a newer reply moves it above the seam and it stays open on the way",
     findAll(sbEl(), "wkrow").length === 1 && wlines().length === 2,
     findAll(sbEl(), "wkrow").length + " rows / " + wlines().length + " call lines");
  // ADR 0016's own accepted flaw, INHERITED rather than added to: the open set is
  // keyed by the stretch's first call, so a stretch still growing past
  // `_RUN_CALLS` shifts that key and collapses once. The Fold changed nothing
  // here — a Record keys on content for the same reason, and a past Exchange is
  // by definition not growing, so no Record can hit it.
  sbOf[X] = sbOf[X].slice();
  sbOf[X][2] = {role: "work", n: 3,
                calls: [{name: "Bash", detail: "pytest -q -x"},
                        {name: "Bash", detail: "pytest -q"},
                        {name: "Read", detail: "board.js"}]};
  await poll();
  ok("cross: a stretch that outgrows its own first call collapses once (ADR 0016)",
     wlines().length === 0 && findAll(sbEl(), "wkrow").length === 1,
     wlines().length + " call lines");

  // A VERY LONG SINGLE TURN, and a very long prompt. The client clips a
  // **Record**'s lines and nothing else: below the seam the read is whatever the
  // server sent, because ADR 0014's clip is the server's and doing it twice would
  // hide text with no way to reach it.
  const Y = "aaaaaaaa-8888-8888-8888-888888888888";
  const HUGE = "A prompt pasted in whole. ".repeat(96);          // ~2400 chars
  const WALL = "<p>" + "One clipped turn. ".repeat(222) + "</p>";  // ~4000 chars
  world = [S(Y, "yourmove", 1, "long"), S(T2, "yourmove", 1, "other")];
  sbOf[Y] = [{role: "user", html: "<p>" + HUGE + "</p>"},
             {role: "assistant", html: "<p>Done. Anything else?</p>"},
             {role: "user", html: "<p>" + HUGE + "</p>"},
             {role: "assistant", html: WALL}];
  sandbox.setPinned(Y); await poll();
  ok("long: a record's `you` line is one clipped line, however long the prompt",
     values(recs()[0])[0].length <= 130 && values(recs()[0])[0].endsWith("…"),
     values(recs()[0])[0].length + ": " + values(recs()[0])[0].slice(-24));
  ok("long: the turn below the seam is whatever the server sent, clipped once",
     findAll(findAll(sbEl(), "live")[0], "md")[0].innerHTML === WALL,
     String(findAll(findAll(sbEl(), "live")[0], "md")[0].innerHTML.length));
  // The one prompt on the page that has to be read in full: the Exchange you are
  // standing in is never folded, so its prompt is not clipped either.
  ok("long: and the prompt you are standing in is not clipped at all",
     findAll(findAll(sbEl(), "nask")[0], "md")[0].innerHTML === "<p>" + HUGE + "</p>",
     String(findAll(findAll(sbEl(), "nask")[0], "md")[0].innerHTML.length));

  // THE LANE IS NAMED ONCE. `.fmeta` used to lead the age with a second word for
  // the lane `.fbadge` says two inches to its left, and on the two **Blocked**
  // lanes — whose badge is the widest of the five — that duplicate pushed the
  // 390px header to 426px, which `width=device-width` answers by shrinking the
  // WHOLE read to 91%. Measured in a browser at 390×844: 426px on the question
  // and approval lanes, 390px on the other three, and `.fdir` — the project
  // name — squeezed to zero.
  world = [S(Y, "question", 1, "long")];
  await poll();
  const fmeta = () => findAll(focusWrap(), "fmeta")[0].textContent;
  ok("header: the badge names the lane, and the meta is the age and nothing else",
     findAll(focusWrap(), "fbadge")[0].textContent === "question" &&
     /^[0-9]+[smhd]$/.test(fmeta()), fmeta());
  ok("header: no lane word survives beside it to say the same thing twice",
     !/waiting|idle|working/.test(fmeta()) && !SRC.includes("LANE_NOUN["), fmeta());

  layout.seamTop = 0;
  world = [S(T, "yourmove", 1, "scroll")];
  sandbox.setPinned(T);
  await poll();

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
  // The Workspace stamp, and NOT as a duplicate of the header's: `.fhead` is
  // slid out by syncChrome while you read, which is the state an approval lands
  // in at the bottom of a long scrollback. Approving is the one irreversible act
  // on the Board and it must never be done with nothing on screen naming the
  // project (ADR 0023).
  ok("ask: it stamps the Workspace, so an approval is never answered blind",
     findAll(askBox()[0], "askws").length === 1 &&
     findAll(askBox()[0], "askws")[0].textContent === world[0].workspace,
     JSON.stringify(findAll(askBox()[0], "askws").map((a) => a.textContent)));
  // No Nickname yet — nothing extra is drawn beside the Workspace stamp.
  ok("ask: no Nickname, no second stamp",
     findAll(askBox()[0], "asknick").length === 0,
     JSON.stringify(findAll(askBox()[0], "asknick").map((a) => a.textContent)));

  // The Nickname joins the Workspace, same lane-coloured treatment — the harder
  // version of "where am I" (ADR 0026): the Workspace says which project, the
  // Nickname says which of the several Runs in it, and an approval is the one
  // irreversible tap this block exists to protect.
  world[0].nickname = "the migration";
  await poll();
  ok("ask: a Nickname stamps beside the Workspace, same treatment",
     findAll(askBox()[0], "askws").length === 1 &&
     findAll(askBox()[0], "asknick").length === 1 &&
     findAll(askBox()[0], "asknick")[0].textContent === "the migration",
     JSON.stringify(findAll(askBox()[0], "asknick").map((a) => a.textContent)));
  world[0].nickname = undefined;
  await poll();

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
  // EVERY SCROLL STEP SETTLES BEFORE THE NEXT ONE, and that is not test hygiene
  // — it is the behaviour. The header CONDENSES rather than leaving (board.html:
  // `.fhead.hid`), so a toggle changes layout, and board.js::syncChrome answers
  // the very next scroll event by re-baselining instead of reading travel from
  // it: that event may be the browser correcting scrollY for the collapse rather
  // than a finger. A real drag delivers ~60 events a second, so it loses one
  // frame; a test that fires two events in the same tick loses the second
  // outright unless it lets the post-layout frame run.
  const scrollTo = async (y) => { win.scrollY = y; win.dispatch("scroll"); await settle(); };
  const readUp = async () => { await scrollTo(1600); await scrollTo(1200); };
  const readDown = async () => { await scrollTo(1200); await scrollTo(1600); };

  ok("chrome: at the live end of the scrollback it is all up",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  layout.cardBottom = 2400;    // the end of the read is far below an 800px fold
  await readDown();
  ok("chrome: reading DOWN a long run-up keeps it up — that is the way to the answer",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  await readUp();
  ok("chrome: scrolling up into history slides the Focus's header away", hid(fhead()));
  ok("chrome: and the composer with it", hid(respond()));
  ok("chrome: the intake dock rides the same state — the bottom edge is one thing",
     hid(hint()));
  ok("chrome: hiding never unbuilds the composer — the same box is still there",
     !!ti() && ti() === respond().querySelector(".ti"));

  layout.cardBottom = 700;     // the end of the read is back on screen
  await scrollTo(1190);              // a nudge further UP: the end of the read wins anyway
  ok("chrome: returning near the bottom brings all three back",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));

  // The escape hatch, and its limit: a tap is a nudge, not a latch.
  layout.cardBottom = 2400;
  await readUp();
  ok("escape hatch: hidden to begin with", hid(respond()));
  doc.dispatch("click");
  ok("escape hatch: interacting with the page restores the chrome without a scroll",
     !hid(fhead()) && !hid(respond()) && !hid(hint()));
  // The tap expanded the header, so it settles like any other toggle before the
  // next scroll is allowed to mean anything — see scrollTo above.
  await settle();
  await scrollTo(win.scrollY - 200);
  ok("escape hatch: and another step back into history takes it away again",
     hid(respond()));

  // A scroll may no more snatch the keyboard away than a poll may.
  doc.dispatch("click");
  ti().focus();
  await readUp();
  ok("chrome: an active reply keeps its box, however far up the read you are",
     !hid(respond()));
  ti().blur();
  await readUp();
  ok("chrome: letting go of the box hands the pixels back to the read", hid(respond()));

  // --- the header condenses, and what that costs to get right ---------------
  // Hidden is not gone. `.fhead` is `position:sticky` and sticky is in flow, so
  // the transform that used to hide it stranded an ~81px blank band; it
  // collapses to the **Workspace** alone instead (board.html: `.fhead.hid`).
  // Nothing is unbuilt to do it — the CSS drops the rest — and that is what lets
  // the escape hatch hand the whole strip back in one class change.
  ok("condense: the hidden header still says which Run you are in",
     hid(fhead()) && fhead().querySelector(".fdir").textContent === "scroll",
     fhead().className + " || " + (fhead().querySelector(".fdir") || {}).textContent);
  ok("condense: and it still HOLDS everything it stops drawing, so nothing is rebuilt to show it",
     [".fbadge", ".fmeta", ".fabout", ".iplus"].every((c) => !!fhead().querySelector(c)),
     fhead().children.map((c) => c.className).join(" | "));
  // The session title is chrome on row one now, not a band under the header —
  // so what condense drops is a genuine second row.
  ok("condense: the session title rides the Workspace's own row, and no band is left",
     fhead().querySelector(".fabout").textContent === "about scroll" &&
     fhead().children[0] === fhead().querySelector(".frow1") &&
     fhead().querySelector(".frow1").children.map((c) => c.className).join(" ") === "fdir fabout" &&
     !card().querySelector(".about"),
     fhead().querySelector(".frow1").children.map((c) => c.className).join(" | "));

  // HYSTERESIS, NOT A BIGGER DEADZONE (board.js: CHROME_HIDE_STEP 28 /
  // CHROME_SHOW_STEP 64). One symmetric threshold means a jitter just over it
  // round-trips the bars forever — free while the hidden header still reserved
  // its box, a visible judder now that it collapses. Showing has to out-travel
  // hiding, so no wobble can cross both lines.
  await scrollTo(2000);
  ok("condense: travelling back down the read brings the full header back", !hid(fhead()));
  await scrollTo(1960);
  ok("condense: 40px up is past the hide step, so it condenses", hid(fhead()));
  await scrollTo(2000);
  ok("condense: the same 40px back down is NOT enough to expand it again", hid(fhead()));
  await scrollTo(2030);
  ok("condense: 70px is, so the way back is deliberate rather than a wobble", !hid(fhead()));

  // --- naming the Focus (ADR 0026) ------------------------------------------
  // The **Workspace** answers *where am I* and stops answering the moment you run
  // three Sessions in one repo. A **Nickname** is the second level — *which of
  // these* — typed by you, on the Session, and it takes the slot the derived
  // label would have taken.
  const frow1 = () => fhead() && fhead().querySelector(".frow1");
  const nickIn = () => fhead() && fhead().querySelector(".fnickin");
  const nickSpan = () => fhead() && fhead().querySelector(".fnick");
  const kids = (n) => n.children.map((c) => c.className).join(" ");

  ok("nickname: an unnamed Session's header is exactly as it was — aiTitle keeps the slot",
     !nickSpan() && !!fhead().querySelector(".fabout"), kids(frow1()));

  frow1().dispatch("click");
  await settle();
  ok("nickname: tapping the Workspace row turns it into a field, in place",
     !!nickIn() && nickIn().tag === "input", kids(frow1()));
  ok("nickname: empty when there is no name yet, and capped where the server caps it",
     nickIn().value === "" && nickIn().maxLength === 24,
     JSON.stringify(nickIn().value) + " / " + nickIn().maxLength);

  // The card is rebuilt on every poll of its own data, and this client polls
  // every few seconds — a rebuild mid-word would take the keyboard away.
  const field = nickIn();
  sbOf[T] = SB().concat([{role: "assistant", html: "<p>a turn arrived while you typed</p>"}]);
  await poll();
  ok("nickname: a poll may not take the field away mid-word", nickIn() === field);

  nickIn().value = "the auth refactor";
  nickIn().dispatch("keydown", {key: "Escape"});
  await settle();
  ok("nickname: Escape closes it and writes nothing",
     !nickIn() && !nickLog.length && !nickSpan(), JSON.stringify(nickLog));

  frow1().dispatch("click");
  await settle();
  nickIn().value = "the auth refactor";
  nickIn().dispatch("keydown", {key: "Enter"});
  await settle();
  await poll();
  ok("nickname: Enter posts it to /api/nickname, keyed by Session",
     nickLog.length === 1 && nickLog[0].sessionId === T &&
     nickLog[0].nickname === "the auth refactor", JSON.stringify(nickLog));
  // Ungated, and this is the assertable half of that: ADR 0007's token guards
  // Respond because Respond can approve a tool call. Naming a row cannot.
  ok("nickname: with no token — it rides the same helper priority and snooze do",
     !("token" in nickLog[0]), JSON.stringify(nickLog[0]));
  ok("nickname: and from then on that is what the header calls the Session",
     !!nickSpan() && nickSpan().textContent === "the auth refactor" &&
     kids(frow1()) === "fdir fnick", kids(frow1()));
  ok("nickname: beside the Workspace, which never yields its place",
     frow1().querySelector(".fdir").textContent === "scroll");
  ok("nickname: it supersedes the derived label — aiTitle no longer has a slot",
     !fhead().querySelector(".fabout"), kids(frow1()));

  // The state ADR 0025 exists to protect: scrolled deep into a Scrollback with an
  // Ask in front of you. A Nickname in the `.about` band would vanish exactly
  // there, which is why it is on row one.
  await scrollTo(1990);
  ok("nickname: it survives the condense — both levels of where-am-I stay on screen",
     hid(fhead()) && !!nickSpan() && nickSpan().textContent === "the auth refactor" &&
     fhead().querySelector(".fdir").textContent === "scroll", kids(frow1()));

  await scrollTo(2100);
  frow1().dispatch("click");
  await settle();
  ok("nickname: re-opening pre-fills with the name you already have — renaming is the common case",
     nickIn().value === "the auth refactor", JSON.stringify(nickIn().value));
  nickIn().value = "";
  nickIn().dispatch("keydown", {key: "Enter"});
  await settle();
  await poll();
  ok("nickname: an empty submit IS the delete — no second control for the null case",
     nickLog[nickLog.length - 1].nickname === "" && !nickSpan() &&
     !!fhead().querySelector(".fabout"), kids(frow1()));

  // The elide rules, driven straight. The stub runs no CSS, so a test has to BE
  // the thing that says "this is clipped" — which is exactly the contract these
  // functions have with the browser: they never estimate, they ask.
  const measured = (max) => {
    const n = new El("span");
    n.clientWidth = 100;
    Object.defineProperty(n, "scrollWidth",
                          {get: () => (n.textContent.length <= max ? 100 : 200)});
    return n;
  };
  const nk = measured(9);
  sandbox.elideNickname(nk, "auth refactor pass two");
  ok("elide: a Nickname loses its TAIL — you type the discriminator first",
     nk.textContent === "auth ref…", nk.textContent);
  const ws = measured(15);
  sandbox.elideWorkspace(ws, "claude-launcher-session-nickname");
  ok("elide: and the Workspace's own rule is untouched — repo-biased, the slug kept",
     ws.textContent === "claude-lau…name", ws.textContent);

  // THE SETTLE WINDOW. A toggle changes layout, and the scroll event that
  // arrives next may be the browser compensating for it rather than a finger —
  // indistinguishable, and the reason the prototype flickered. Both events here
  // land in one tick, with no frame between them for layout to settle in, and
  // the second is a 200px move: without the guard it would expand the header it
  // just condensed.
  win.scrollY = 1900; win.dispatch("scroll");
  const condensedFirst = hid(fhead());
  win.scrollY = 2100; win.dispatch("scroll");
  ok("condense: a scroll in the same frame as a toggle is re-baselined, never read as travel",
     condensedFirst && hid(fhead()), condensedFirst + " -> " + fhead().className);
  await settle();
  // And it is a window, not a latch: once layout has settled the next real
  // travel decides as usual, from the post-collapse position.
  await scrollTo(2300);
  ok("condense: once layout has settled the next drag counts again", !hid(fhead()));
  await readUp();

  // The state that must survive a poll, now with a third thing in it: a
  // half-typed reply, a reading position, AND a chrome state that still agrees
  // with where the reader is.
  ti().value = "still writing this";
  win.scrollY = 640;
  const heldCard = card();
  sbOf[T] = sbOf[T].concat([{role: "assistant", html: "<p>and another turn</p>"}]);
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

  await readUp();
  ok("intake: the ＋'s own strip clears the read while nothing is open", hid(fhead()));
  doc.dispatch("click");   // the escape hatch: chrome back, no scroll
  iplus().dispatch("click");
  ok("intake: tapping it brings the sheet up over the read, with a scrim behind it",
     hasCls(isheet(), "open") && iscrim().hidden === false, isheet().className);
  ok("intake: and marks the ＋ while it is open",
     hasCls(iplus(), "hot") && iplus()["aria-expanded"] === "true", iplus().className);
  await readUp();
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

  // This is what nicknaming is FOR — a list of near-identical rows from one
  // repo, which the opening prompt used to be the only way to tell apart
  // (ADR 0023 tried swapping the Workspace in for it and rejected that; the
  // prompt answers "which conversation was that" better than a repo name
  // does). A row with a Nickname leads with it and keeps the prompt, demoted
  // rather than deleted, on a second line; a row without one is unchanged.
  recoverable = {sessions: [
    {sessionId: "r1", title: "fix the flaky test", dir: "/p/one", mtime: 1, nickname: "the flaky test"},
    {sessionId: "r2", title: "add auth middleware", dir: "/p/two", mtime: 1, nickname: null},
  ], preselectCount: 0};
  await sandbox.loadRecoverable();
  await settle();
  iplus().dispatch("click");
  recovRow().dispatch("click");
  await settle();
  const recovList = () => doc.getElementById("recovlist");
  const recovTitles = () => findAll(recovList(), "recovtitle2");
  const recovPrompts = () => findAll(recovList(), "recovprompt");
  ok("recover: a row with a Nickname leads with it",
     recovTitles()[0].textContent === "the flaky test",
     JSON.stringify(recovTitles().map((t) => t.textContent)));
  ok("recover: and the opening prompt is demoted to a second line, not dropped",
     recovPrompts().length === 1 && recovPrompts()[0].textContent === "fix the flaky test",
     JSON.stringify(recovPrompts().map((p) => p.textContent)));
  ok("recover: a row with no Nickname is unchanged — the prompt still leads, no second line",
     recovTitles()[1].textContent === "add auth middleware",
     JSON.stringify(recovTitles().map((t) => t.textContent)));
  doc.getElementById("recovclose").dispatch("click");
  recoverable = {sessions: [], preselectCount: 0};
  await sandbox.loadRecoverable();
  await settle();

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
  // A drag is a STREAM, and the move in the middle of it is load-bearing: the
  // gesture arms while the finger is down and release only spends what it armed,
  // so a down/up pair with nothing between them is not a drag any browser makes
  // and no longer moves anything here either.
  const dragTo = (target, dx, dy, pointerType) =>
    win.dispatch("pointermove", {target, clientX: 200 + dx, clientY: 400 + dy, pointerType});
  const swipe = (target, dx, dy) => {
    win.dispatch("pointerdown", {target, clientX: 200, clientY: 400});
    dragTo(target, dx, dy);
    win.dispatch("pointerup", {target, clientX: 200 + dx, clientY: 400 + dy});
  };
  const peek = () => doc.getElementById("peek");
  const peekPill = () => (peek().hidden ? null : peek().children[0]);
  const peekPart = (cls) => {
    const p = peekPill();
    const n = p && p.querySelector(cls);
    return n ? n.textContent : "";
  };
  const peekSays = () => peekPart(".peektxt");
  const peekState = () => peekPart(".peekstate");
  const peekArmed = () => hasCls(peekPill(), "armed");
  // How far the pill still has to travel before it is home: board.js writes the
  // slide inline (paintPeek), so this is the one number a test can read back.
  const peekSlide = () => parseFloat(String(peekPill().style.transform).replace(/[^-\d.]/g, ""));

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
  ok("swipe: the peek flashes on the Run it landed on — a gesture leaves no other mark",
     peekSays() === "\u2192 bravo", JSON.stringify(peekSays()) + " hidden=" + peek().hidden);
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

  // --- Naming a row, by press and hold (ADR 0026) ---------------------------
  // The header names the Run you are already looking at. The question *which of
  // these three* is asked on the QUEUE, which is where the second level has to
  // land — and on the **Foreign** rows above all, because a Foreign Run never
  // takes the Focus and so the header can never reach one.
  const rowFor = (ws) => findAll(zones(), "qrow").find((r) => r.textContent.includes(ws));
  const oneOf = (row) => findAll(row, "qone")[0];
  const fgRow = () => findAll(zones(), "frow")[0];
  const rowField = () => zones().querySelector(".fnickin");

  world.find((s) => s.sessionId === B).nickname = "the flaky test";
  await poll();
  ok("row: a named Session's row reads Workspace + Nickname, in the snippet's slot",
     oneOf(rowFor("bravo")).textContent === "the flaky test" &&
     rowFor("bravo").textContent.includes("bravo"), rowFor("bravo").textContent);
  ok("row: and not both — this row has no width to spend saying one thing twice",
     !rowFor("bravo").textContent.includes("one-bravo"), rowFor("bravo").textContent);
  ok("row: a typed name does not wear the voice of the derived one it displaced",
     hasCls(oneOf(rowFor("bravo")), "nick") && !hasCls(oneOf(rowFor("worker")), "nick"),
     oneOf(rowFor("bravo")).className + " / " + oneOf(rowFor("worker")).className);
  ok("row: and an unnamed row is exactly as it was",
     oneOf(rowFor("worker")).textContent === "one-worker", oneOf(rowFor("worker")).textContent);

  // The **Blocked** case, which looks like a regression and is not. server.py
  // already swaps `one` for the **Ask** text on the question/approval lanes, and
  // goes on doing it — the Nickname rides BESIDE `one`, so choosing between the
  // two is the client's, once, in one place.
  const K = "55555555-7777-7777-7777-777777777777";
  world.push(Object.assign(S(K, "question", 1, "kilo"),
                           {one: "May I run `rm -rf build`?"}));
  await poll();
  ok("blocked row: unnamed, it shows the Ask exactly as before",
     oneOf(rowFor("kilo")).textContent === "May I run `rm -rf build`?",
     oneOf(rowFor("kilo")).textContent);
  world.find((s) => s.sessionId === K).nickname = "the release branch";
  await poll();
  ok("blocked row: named, the Nickname displaces the Ask text too",
     oneOf(rowFor("kilo")).textContent === "the release branch" &&
     !rowFor("kilo").textContent.includes("rm -rf"), rowFor("kilo").textContent);
  ok("blocked row: and the lane badge is untouched — that is the part of an Ask that changes what you do",
     findAll(rowFor("kilo"), "qbadge")[0].textContent === "question",
     findAll(rowFor("kilo"), "qbadge")[0].textContent);
  sandbox.setPinned(K);
  await settle();
  ok("blocked row: the full Ask survives on the Focus, which is where you answer it",
     !!focusWrap().querySelector(".ask") &&
     focusWrap().querySelector(".ask").textContent.includes("what now?"),
     focusWrap().querySelector(".ask") && focusWrap().querySelector(".ask").textContent);
  sandbox.setPinned(A);
  await settle();

  // THE GESTURE. A hold shares one pointer stream with the swipe and one target
  // with the tap-to-focus, so most of what follows is about what it must NOT do.
  const HOLD_MS = 500;   // board.js::HOLD_MS
  const press = async (target, ms) => {
    win.dispatch("pointerdown", {target, clientX: 200, clientY: 400});
    await tick(ms);
    win.dispatch("pointerup", {target, clientX: 200, clientY: 400});
    await settle();
  };

  const shortBody = rowFor("worker").querySelector(".qbody");
  await press(shortBody, 40);
  shortBody.dispatch("click");   // the click a real tap leaves behind
  await settle();
  ok("hold: a short tap still pins the row — the gesture it shares is untouched",
     shownSid() === W.slice(0, 8) && !rowField(), shownSid());
  sandbox.setPinned(A);
  await settle();

  const heldBody = rowFor("worker").querySelector(".qbody");
  await press(heldBody, HOLD_MS + 60);
  ok("hold: pressing and holding a queue row opens the same inline field, on that row",
     !!rowField() && rowField().tag === "input", zones().textContent);
  ok("hold: on the row you held and on no other — one field, one Session",
     findAll(zones(), "fnickin").length === 1 &&
     rowFor("worker").querySelector(".fnickin") === rowField());
  ok("hold: and holding does not pin — the Focus is where you left it",
     shownSid() === A.slice(0, 8), shownSid());
  heldBody.dispatch("click");   // ...and the click the browser sends after the hold
  await settle();
  ok("hold: nor does the click the hold leaves behind, which is not a tap either",
     shownSid() === A.slice(0, 8) && !!rowField(), shownSid());

  // The queue is redrawn wholesale on every poll, and this client polls every
  // few seconds — the same rule the Focus card's field lives by.
  const rf = rowField();
  world.find((s) => s.sessionId === W).one = "a turn arrived while you typed";
  await poll();
  ok("hold: a poll may not take the row's field away mid-word", rowField() === rf);

  let nk0 = nickLog.length;
  rowField().value = "the long one";
  rowField().dispatch("keydown", {key: "Enter"});
  await settle();
  ok("hold: Enter names that Session from the queue, without ever making it the Focus",
     nickLog.length === nk0 + 1 && nickLog[nk0].sessionId === W &&
     nickLog[nk0].nickname === "the long one" && shownSid() === A.slice(0, 8),
     JSON.stringify(nickLog[nk0]) + " / " + shownSid());
  world.find((s) => s.sessionId === W).nickname = "the long one";
  await poll();
  ok("hold: and from then on that is what the row calls it",
     oneOf(rowFor("worker")).textContent === "the long one",
     oneOf(rowFor("worker")).textContent);

  // A hold is not a swipe. They read the same pointerdown/pointerup pair, so the
  // drag is spent the moment the hold fires — otherwise letting go anywhere but
  // where you started would rotate the Focus out from under the field.
  const swiped = shownSid();
  const dragBody = rowFor("bravo").querySelector(".qbody");
  win.dispatch("pointerdown", {target: dragBody, clientX: 200, clientY: 400});
  await tick(HOLD_MS + 60);
  win.dispatch("pointerup", {target: dragBody, clientX: 0, clientY: 400});
  await settle();
  ok("hold: a hold released 200px away still does not rotate the Focus",
     shownSid() === swiped && !!rowField(), shownSid());
  rowField().dispatch("keydown", {key: "Escape"});
  await settle();
  ok("hold: Escape closes the row's field and writes nothing",
     !rowField() && nickLog.length === nk0 + 1, JSON.stringify(nickLog.slice(nk0)));

  // ...and a swipe is not a hold: past the slop the finger is scrolling the read
  // or walking the ring, and either way it is no longer resting on a row.
  const moveBody = rowFor("bravo").querySelector(".qbody");
  win.dispatch("pointerdown", {target: moveBody, clientX: 200, clientY: 400});
  win.dispatch("pointermove", {target: moveBody, clientX: 200, clientY: 470});
  await tick(HOLD_MS + 60);
  win.dispatch("pointerup", {target: moveBody, clientX: 200, clientY: 470});
  await settle();
  ok("hold: a finger that travelled disarms it — that drag belongs to the page",
     !rowField(), zones().textContent);

  // The flag a hold leaves behind belongs to the ROW, not to the page. Opening
  // the field changes that row's height while the finger is still down, so the
  // click on release can land on a row that slid into its place — and that row's
  // tap has nothing to do with the hold.
  const heldRow = rowFor("worker").querySelector(".qbody");
  const otherRowBody = rowFor("kilo").querySelector(".qbody");
  await press(heldRow, HOLD_MS + 60);
  otherRowBody.dispatch("click");
  await settle();
  ok("hold: the click it suppresses is its own row's — another row's tap still pins",
     shownSid() === K.slice(0, 8), shownSid());
  rowField().dispatch("keydown", {key: "Escape"});
  await settle();
  sandbox.setPinned(A);
  await settle();

  // ...and an edit whose row is adopted as the Focus has lost the row it lived
  // on: the page's zones draw every Managed Run EXCEPT the Focus. Holding the
  // surface open there would keep a row the payload no longer has, in the queue,
  // beside a card now showing the same Run.
  await press(rowFor("bravo").querySelector(".qbody"), HOLD_MS + 60);
  ok("hold: (given) the field is up on bravo's row", !!rowField());
  sandbox.setPinned(B);
  await settle();
  await poll();
  ok("hold: a row taken for the Focus takes its edit with it, leaving no stale row behind",
     !rowField() && !rowFor("bravo"), zones().textContent);
  sandbox.setPinned(A);
  await settle();

  // THE POINT OF THE WHOLE GESTURE. A Foreign Run never takes the Focus, so
  // without this the one Session you most want to tell apart could display a
  // Nickname and never receive one — and naming it must not cost a Transfer.
  const xferBtn = findAll(fgRow(), "fgxfer")[0];
  await press(xferBtn, HOLD_MS + 60);
  ok("hold: a hold that landed on `transfer` names nothing — that button is a target of its own",
     !fgRow().querySelector(".fnickin") && transferLog.length === 3,
     JSON.stringify(transferLog.length));

  nk0 = nickLog.length;
  await press(fgRow(), HOLD_MS + 60);
  ok("foreign: holding a Foreign row opens the field there too",
     !!fgRow().querySelector(".fnickin"));
  fgRow().querySelector(".fnickin").value = "mid-migration";
  fgRow().querySelector(".fnickin").dispatch("keydown", {key: "Enter"});
  await settle();
  ok("foreign: a Foreign Session can be named without being Transferred first",
     nickLog.length === nk0 + 1 && nickLog[nk0].sessionId === G &&
     nickLog[nk0].nickname === "mid-migration" && transferLog.length === 3,
     JSON.stringify(nickLog[nk0]));
  foreignWorld[0].nickname = "mid-migration";
  await poll();
  ok("foreign: and the row reads it in the snippet's slot, by the one rule every row uses",
     findAll(fgRow(), "fgone")[0].textContent === "mid-migration" &&
     !fgRow().textContent.includes("started by hand"), fgRow().textContent);

  // The rail draws rows too — including the Focus's own (`.now`), which is the
  // one Session reachable from both surfaces at once. Holding it must not raise
  // a second field in the header for the same name.
  const railBody = railRows().find((r) => !isNow(r)).querySelector(".qbody");
  await press(railBody, HOLD_MS + 60);
  ok("rail: the same hold works there — the gesture is the row's, not the page's",
     !!rail().querySelector(".fnickin"), rail().textContent);
  rail().querySelector(".fnickin").dispatch("keydown", {key: "Escape"});
  await settle();
  const nowBody = nowRow().querySelector(".qbody");
  await press(nowBody, HOLD_MS + 60);
  ok("rail: holding the Focus's own row names it THERE — never two fields for one name",
     !!rail().querySelector(".fnickin") && !fhead().querySelector(".fnickin"),
     rail().textContent);
  rail().querySelector(".fnickin").dispatch("keydown", {key: "Escape"});
  await settle();
  ok("rail: and closing it hands the surface back to the poll", !rail().querySelector(".fnickin"));

  // --- Touch only: a mouse selects text, it does not swipe (issue 01) -------
  // The Scrollback is prose you drag across to select on a desktop. Gating the
  // drag half of this listener to touch means that selection never moves the
  // Focus — the hold below shares the same pointerdown and stays open to
  // every pointer type, because a long press with a mouse is deliberate.
  const swipeAs = (target, dx, dy, pointerType) => {
    win.dispatch("pointerdown", {target, clientX: 200, clientY: 400, pointerType});
    dragTo(target, dx, dy, pointerType);
    win.dispatch("pointerup", {target, clientX: 200 + dx, clientY: 400 + dy, pointerType});
  };

  sandbox.setPinned(A);
  await settle();
  const beforeMouse = shownSid();
  swipeAs(sbEl(), -180, 0, "mouse");
  await settle();
  ok("touch-only: a mouse drag across the Scrollback leaves the Focus where it was",
     shownSid() === beforeMouse, "got " + shownSid());
  swipeAs(sbEl(), -180, 0, "pen");
  await settle();
  ok("touch-only: a pen drag is the same selection, not a swipe",
     shownSid() === beforeMouse, "got " + shownSid());
  swipeAs(sbEl(), -180, 0, "touch");
  await settle();
  ok("touch-only: the same drag on glass still moves the Focus",
     shownSid() !== beforeMouse, "got " + shownSid());

  sandbox.setPinned(A);
  await settle();
  swipeAs(sbEl(), -180, 0, undefined);
  await settle();
  ok("touch-only: and an event with no pointerType at all is treated as touch too",
     shownSid() !== beforeMouse, "got " + shownSid());

  const heldMouse = rowFor("bravo").querySelector(".qbody");
  win.dispatch("pointerdown", {target: heldMouse, clientX: 200, clientY: 400, pointerType: "mouse"});
  await tick(HOLD_MS + 60);
  win.dispatch("pointerup", {target: heldMouse, clientX: 200, clientY: 400, pointerType: "mouse"});
  await settle();
  ok("touch-only: a mouse press-and-hold on a row still opens the Nickname field",
     !!rowField() && rowField().tag === "input", zones().textContent);
  rowField().dispatch("keydown", {key: "Escape"});
  heldMouse.dispatch("click");   // the click the browser leaves after the hold, consumed
  await settle();
  sandbox.setPinned(A);
  await settle();

  // --- The drag arms visibly, and can be abandoned (issue 02) ---------------
  // Nothing on screen used to change while the finger was down: the commit was
  // one arithmetic check at release, so you could not tell you were swiping
  // until you had swiped, and there was no way to back out. The decision is
  // drawn now — a pill under the header naming where you would land — and that
  // pill's own armed state is what release spends. There is no second
  // measurement behind it that could disagree with what you were looking at.
  world = [S(A, "question", 1, "alpha"), S(B, "yourmove", 1, "bravo"), S(W, "working", 1, "worker")];
  foreignWorld = [];
  sandbox.setPinned(A);
  await settle();

  // The detent is felt as well as seen, and it fires on the EDGE: once when the
  // drag crosses the threshold, not once per pixel past it, and not again on the
  // way back — the finger already knows it came back.
  const v0 = vibrateLog.length;
  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -40, 0, "touch");
  await settle();
  ok("haptic: nothing yet at 40px — under the threshold there is nothing to feel",
     vibrateLog.length === v0, JSON.stringify(vibrateLog.slice(v0)));
  dragTo(sbEl(), -90, 0, "touch");
  dragTo(sbEl(), -160, 0, "touch");
  await settle();
  ok("haptic: one buzz on the arming edge, and dragging on past it stays silent",
     vibrateLog.length === v0 + 1, JSON.stringify(vibrateLog.slice(v0)));
  dragTo(sbEl(), -30, 0, "touch");
  dragTo(sbEl(), -160, 0, "touch");
  await settle();
  ok("haptic: crossing back and out again is a second arm, and buzzes again",
     vibrateLog.length === v0 + 2, JSON.stringify(vibrateLog.slice(v0)));
  win.dispatch("pointerup", {target: sbEl(), clientX: 40, clientY: 400, pointerType: "touch"});
  await settle();
  sandbox.setPinned(A);
  await settle();

  // Nowhere to go is a refusal like any other, and the pill has to say it while
  // the finger is still down. A gesture that armed here would be an invisible
  // commit — the very thing this slice deletes — so it never arms.
  world = [S(A, "question", 1, "alpha")];
  await poll();
  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -120, 0, "touch");
  await settle();
  ok("peek: on a Board with nothing else on it the pill says so, and never arms",
     peekSays() === "nothing else on the Board to move to" && !peekArmed(),
     JSON.stringify(peekSays()) + " " + (peekPill() && peekPill().className));
  win.dispatch("pointerup", {target: sbEl(), clientX: 80, clientY: 400, pointerType: "touch"});
  await settle();
  ok("peek: and releasing there moves nothing, because there was nothing to move to",
     shownSid() === A.slice(0, 8), "got " + shownSid());
  world = [S(A, "question", 1, "alpha"), S(B, "yourmove", 1, "bravo"), S(W, "working", 1, "worker")];
  sandbox.setPinned(A);
  await settle();

  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -40, 0, "touch");
  await settle();
  const firstPill = peekPill();
  ok("peek: 40px sideways already names the Run this drag would land on",
     peekSays() === "\u2192 bravo", JSON.stringify(peekSays()) + " hidden=" + peek().hidden);
  ok("peek: and says what is waiting for you over there, in three words not five",
     peekState() === "idle", JSON.stringify(peekState()));
  ok("peek: unarmed at 40px — under the threshold this is a readout, not a promise",
     !peekArmed(), peekPill().className);
  const slid40 = peekSlide();
  dragTo(sbEl(), -60, 0, "touch");
  await settle();
  ok("peek: it slides in from the side the destination is on, tracking the travel",
     slid40 > peekSlide() && peekSlide() > 0, slid40 + " then " + peekSlide());
  // One node, mutated. A pill rebuilt per frame would be a new element every
  // time, and a new element has nothing to transition FROM — the snap that makes
  // arming a detent could not animate at all (board.html: .peekpill).
  ok("peek: and it is ONE node the whole way, so the snap has something to ease from",
     peekPill() === firstPill && peek().children.length === 1, peek().children.length);
  win.dispatch("pointerup", {target: sbEl(), clientX: 140, clientY: 400, pointerType: "touch"});
  await settle();
  ok("peek: so releasing short of the threshold leaves the Focus exactly where it was",
     shownSid() === A.slice(0, 8), "got " + shownSid());
  ok("peek: and the pill leaves with the finger", peek().hidden === true, peek().textContent);

  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -40, 0, "touch");
  dragTo(sbEl(), -90, 0, "touch");
  await settle();
  ok("peek: past 70px it arms, and says what letting go now would do",
     peekArmed() && peekSays() === "release \u2192 bravo",
     peekPill().className + " " + JSON.stringify(peekSays()));
  ok("peek: armed is a RESTING position — it snaps home rather than tracking on",
     peekSlide() === 0 && peekPill().style.opacity === "1",
     peekPill().style.transform + " / " + peekPill().style.opacity);
  win.dispatch("pointerup", {target: sbEl(), clientX: 110, clientY: 400, pointerType: "touch"});
  await settle();
  ok("peek: and releasing armed is the one thing that moves the Focus",
     shownSid() === B.slice(0, 8), "got " + shownSid());

  // The whole point of drawing the gesture: a threshold you can cross back over.
  sandbox.setPinned(A);
  await settle();
  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -90, 0, "touch");
  await settle();
  const wasArmed = peekArmed();
  dragTo(sbEl(), -30, 0, "touch");
  await settle();
  ok("peek: dragged back under the threshold it snaps out and disarms again",
     wasArmed && !peekArmed() && peekSays() === "\u2192 bravo",
     wasArmed + " / " + peekPill().className);
  win.dispatch("pointerup", {target: sbEl(), clientX: 170, clientY: 400, pointerType: "touch"});
  await settle();
  ok("peek: releasing there does nothing at all — an armed gesture can be abandoned",
     shownSid() === A.slice(0, 8), "got " + shownSid());

  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), 10, 90, "touch");
  await settle();
  ok("peek: a mostly-vertical drag draws no peek — that is the read being scrolled",
     peek().hidden === true, peek().textContent);
  win.dispatch("pointerup", {target: sbEl(), clientX: 210, clientY: 490, pointerType: "touch"});
  await settle();
  ok("peek: and it moves nothing, however far it travelled",
     shownSid() === A.slice(0, 8), "got " + shownSid());

  // A **Nickname** names a Run wherever there is one, on this surface like every
  // other (ADR 0026) — the peek is where you decide whether to go, so it has to
  // call the destination what the rest of the Board calls it.
  world.find((s) => s.sessionId === B).nickname = "the swipe cue";
  await poll();
  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -50, 0, "touch");
  await settle();
  ok("peek: a Nickname is what it calls the destination, and the Workspace answers otherwise",
     peekSays() === "\u2192 the swipe cue", JSON.stringify(peekSays()));
  win.dispatch("pointerup", {target: sbEl(), clientX: 150, clientY: 400, pointerType: "touch"});
  await settle();
  world.find((s) => s.sessionId === B).nickname = null;
  await poll();

  // A refusal is visible from the first pixel. A gesture that armed and then
  // refused at release would be a lie told in the one place there is room to
  // tell the truth — and the reply is exactly what the Focus must not be taken
  // away from (replyEngaged).
  ti().value = "half a thought";
  win.dispatch("pointerdown", {target: sbEl(), clientX: 200, clientY: 400, pointerType: "touch"});
  dragTo(sbEl(), -120, 0, "touch");
  await settle();
  ok("peek: with a reply half-typed the pill says the reply is holding the Focus",
     peekSays() === "the reply is holding the Focus", JSON.stringify(peekSays()));
  ok("peek: and it does not arm past the threshold", !peekArmed(), peekPill().className);
  dragTo(sbEl(), -400, 0, "touch");
  await settle();
  ok("peek: nor at any distance at all — a refusal never becomes a promise",
     !peekArmed() && peekSays() === "the reply is holding the Focus", peekPill().className);
  win.dispatch("pointerup", {target: sbEl(), clientX: -200, clientY: 400, pointerType: "touch"});
  await settle();
  ok("peek: so the Focus stays with the reply you are writing",
     shownSid() === A.slice(0, 8), "got " + shownSid());
  ti().value = "";
  await settle();

  // The two routes with no in-flight phase at all. They commit on the event that
  // arrives, so the pill they never got to arm flashes on the Run they landed on
  // — at rest and unarmed, because by then the release has already happened.
  const beforeWheel = shownSid();
  win.dispatch("wheel", {deltaX: 95, deltaY: 6, target: sbEl()});
  await settle();
  ok("landing: a trackpad flick still moves the Focus, and flashes the same pill",
     shownSid() !== beforeWheel && peekSays().startsWith("\u2192 ") && !peekArmed(),
     shownSid() + " " + JSON.stringify(peekSays()));
  ok("landing: the toast that named where it put you is still there beside it",
     doc.getElementById("toast").textContent.startsWith("\u2192 "),
     JSON.stringify(doc.getElementById("toast").textContent));
  const beforeKey = shownSid();
  doc.activeElement = null;   // the arrows stand down while anything is taking text
  doc.dispatch("keydown", {key: "ArrowRight"});
  await settle();
  ok("landing: and \u2190/\u2192 do the same — one readout for all three routes",
     shownSid() !== beforeKey && peekSays().startsWith("\u2192 ") && !peekArmed(),
     shownSid() + " " + JSON.stringify(peekSays()));
  ok("landing: which clears itself, so a cue never sits over the read it named",
     await (async () => { await tick(360); return peek().hidden === true; })(),
     peek().textContent);

  sandbox.setPinned(A);
  await settle();

  // --- Priority, set from a queue row (slice 03) ----------------------------
  // Priority is the OUTER key of the triage order now (5555960), so triage
  // means walking the queue marking things. Every mark used to cost a
  // focus-change, because `.prisel` lives on the **Focus** and nowhere else —
  // and a focus-change is precisely what **Rotation** is built to protect. The
  // row's own control is the fix, so the two things that have to be proved
  // about it are the two halves of that sentence: the tap moves the LEVEL, and
  // it does not move the FOCUS.
  world = [S(A, "question", 1, "alpha"), S(B, "yourmove", 1, "bravo")];
  foreignWorld = [];
  sandbox.setPinned(A);
  await poll();
  // The event a real row hands its control. `stopPropagation` is not decoration
  // here — it is the mechanism, so the test counts it rather than swallowing it.
  let stopped = 0;
  const tap = (node) => node.dispatch("click", {stopPropagation: () => { stopped++; }});
  const priOf = (row) => findAll(row, "qpri")[0];
  ok("row priority: the control is on the row at `normal` too — dim, not absent",
     !!priOf(rowFor("bravo")) && hasCls(priOf(rowFor("bravo")), "no"),
     priOf(rowFor("bravo")) && priOf(rowFor("bravo")).className);
  ok("row priority: and it is a real button, so a keyboard and the hold gesture both know what it is",
     priOf(rowFor("bravo")).tag === "button");

  const p0 = priLog.length, focusBefore = shownSid();
  tap(priOf(rowFor("bravo")));
  await settle();
  ok("row priority: tapping it marks THAT Run up a level",
     priLog.length === p0 + 1 && priLog[p0].sessionId === B && priLog[p0].level === "high",
     JSON.stringify(priLog.slice(p0)));
  ok("row priority: and the Focus does not move — the mark costs no Rotation",
     shownSid() === focusBefore && focusBefore === A.slice(0, 8), "got " + shownSid());
  ok("row priority: which is the stopPropagation, not a coincidence", stopped === 1);
  await poll();
  ok("row priority: the level comes back on the poll and the row wears the flag",
     hasCls(priOf(rowFor("bravo")), "hi") && priOf(rowFor("bravo")).textContent === "\u2691",
     priOf(rowFor("bravo")).className + " " + priOf(rowFor("bravo")).textContent);

  // The same cycle the Focus's `.prisel` runs, because it IS the same function
  // (board.js::cyclePriority). high → low → normal, and `low` is legible on a
  // row now rather than looking exactly like `normal`.
  tap(priOf(rowFor("bravo")));
  await poll();
  ok("row priority: the cycle continues to `low`, which has a glyph of its own",
     priLog.slice(-1)[0].level === "low" && hasCls(priOf(rowFor("bravo")), "lo") &&
     priOf(rowFor("bravo")).textContent === "\u2690",
     priOf(rowFor("bravo")).className + " " + priOf(rowFor("bravo")).textContent);
  tap(priOf(rowFor("bravo")));
  await poll();
  ok("row priority: and back to `normal` — one cycle, shared with the Focus's own control",
     priLog.slice(-1)[0].level === "normal" && hasCls(priOf(rowFor("bravo")), "no"),
     priOf(rowFor("bravo")).className);
  ok("row priority: the Focus never moved across the whole walk",
     shownSid() === A.slice(0, 8), "got " + shownSid());

  // The other half of the affordance: the row is still a row.
  const p1 = priLog.length;
  rowFor("bravo").querySelector(".qbody").dispatch("click");
  await settle();
  ok("row priority: tapping anywhere ELSE on the row still makes it the Focus",
     shownSid() === B.slice(0, 8) && priLog.length === p1, "got " + shownSid());
  sandbox.setPinned(A);
  await poll();

  // The rail draws the same rows and gets the same glyph — it is the monitor
  // surface, and the tiers are why the queue is in the order it is in. What it
  // does NOT get is the tap, for the same reason it gets no ↗ ❯ × strip.
  const railPri = railRows().map(priOf);
  ok("rail: every row there shows its level too — the tiers are readable at a distance",
     railPri.length === railRows().length && railPri.every(Boolean),
     railPri.map((n) => n && n.className).join(" | "));
  ok("rail: but the slot is a readout there, never a control",
     railPri.every((n) => n.tag === "span" && !n.listeners.click && hasCls(n, "ro")),
     railPri.map((n) => n.tag + ":" + n.className).join(" | "));

  // --- `skip →` walks Up Next, and only Up Next (slice 04) ------------------
  // The button handed the card to `upnext[0]` — the queue HEAD. That was very
  // nearly right while priority was a tiebreak, because the **Focus** was usually
  // the head itself and skipping it left the runner-up standing at the front.
  // Priority as a tier (5555960) ends that for good: a level holds the head
  // against everything below it, so `skip` from the head landed back on the Run
  // you had just declined, or re-landed on it on the next poll. It now means the
  // row AFTER this one, wrapping inside `upnext`.
  //
  // The wrap is why `snoozed` and `dormant` carry rows here at last. Read across
  // the swipe's ring instead of inside the zone, "next after me" would walk a
  // triaged Focus out of **Rotation**'s queue and into two zones you deferred on
  // purpose — so the point of the block below is as much what skip REFUSES as
  // where it goes. The ring's own order and wrap are a separate verb and are
  // asserted, untouched, further up this file.
  const SNZ = "55555555-7777-7777-7777-777777777777";
  const DRM = "66666666-8888-8888-8888-888888888888";
  // Distinct `updatedAt`, because idle Runs sort freshest-first: alpha, bravo,
  // charlie is then the queue's order and not an accident of insertion order.
  world = [S(A, "yourmove", 1, "alpha"), S(B, "yourmove", 1, "bravo"),
           S(C, "yourmove", 1, "charlie"), S(SNZ, "snoozed", 1, "snoozy"),
           S(DRM, "dormant", 1, "dozy")];
  world[0].updatedAt = 3000; world[1].updatedAt = 2000; world[2].updatedAt = 1000;
  world[4].updatedAt = 500;   // dozy sorts behind the whole queue — see the wrap below
  // A **Foreign Run** on the board for the length of the walk, so every step is
  // also an assertion that skip cannot reach one (ADR 0012).
  foreignWorld = [{sessionId: G, title: "byhand", dir: "~/projects/byhand", status: "waiting",
                   bridge: "", updatedAt: 1000, one: "started by hand at the Mac"}];
  // THE FOCUS STARTS IN THE MIDDLE OF THE QUEUE, and that is the whole design of
  // this fixture. Hold the head and "the head" and "the row behind me" are the
  // same Session, and the old `upnext[0]` passes every assertion below by
  // accident. Held on `bravo`, the head is `alpha` — a row already BEHIND you —
  // and the two readings finally name different Runs.
  sandbox.setPinned(B);
  await poll();
  const rowText = () => findAll(zones(), "qrow").map((r) => r.textContent).join(" | ");
  ok("skip: the zones it must never reach are on the board to be reached",
     rowText().includes("snoozy") && rowText().includes("dozy"), rowText());
  ok("skip: and the queue head is a row the Focus is already past",
     findAll(zones(), "qrow")[0].textContent.includes("alpha"),
     findAll(zones(), "qrow")[0].textContent);

  const walked = [];
  for (let i = 0; i < 4; i++) {
    ghost("skip →").dispatch("click");
    await settle();
    walked.push(shownSid());
  }
  ok("skip: it lands on the row BEHIND the Focus, not back on the queue head",
     walked[0] === C.slice(0, 8), "got " + walked[0]);
  ok("skip: from the last row of Up Next it wraps to the first",
     walked[1] === A.slice(0, 8), "got " + walked[1]);
  ok("skip: past the wrap the walk simply resumes — the queue is a ring of its own",
     walked[2] === B.slice(0, 8) && walked[3] === C.slice(0, 8),
     JSON.stringify(walked));
  ok("skip: and it never leaves Up Next — no snoozed, no dormant, no Foreign Run",
     !walked.some((s) => [SNZ, DRM, G].map((x) => x.slice(0, 8)).includes(s)),
     JSON.stringify(walked));

  // A Focus that is in no queue at all: the server hands the Focus over on its
  // own key, and this one is `dormant` — pinned by a row tap, a member of
  // nothing. `skip` still has to mean something, and what it means is the row
  // after where `sortsBefore.upnext` WOULD splice it. `dozy` is the stalest Run
  // on the board, so it would splice in last, and "after last" is the head.
  sandbox.setPinned(DRM);
  await poll();
  ghost("skip →").dispatch("click");
  await settle();
  ok("skip: from a Focus outside Up Next it steps off where the Focus WOULD sit",
     shownSid() === A.slice(0, 8), "got " + shownSid());

  // And an empty queue is a refusal. There are two dim rows sitting right there
  // and neither is an answer.
  world = [S(A, "yourmove", 1, "alpha"), S(SNZ, "snoozed", 1, "snoozy"),
           S(DRM, "dormant", 1, "dozy")];
  sandbox.setPinned(A);
  await poll();
  ghost("skip →").dispatch("click");
  await settle();
  ok("skip: an empty Up Next is a refusal, never a raid on the zones beside it",
     shownSid() === A.slice(0, 8) &&
     doc.getElementById("toast").textContent === "nothing up next",
     shownSid() + " / " + JSON.stringify(doc.getElementById("toast").textContent));

  world = [S(A, "question", 1, "alpha"), S(B, "yourmove", 1, "bravo")];
  foreignWorld = [];
  sandbox.setPinned(A);
  await poll();

  // --- The Ask Set: what is asked, and what a tap is worth (ADR 0020) -------
  //
  // The transcript says WHAT is asked; the pane says WHERE the widget stands.
  // The client's job is to draw the first and to send the second WITHOUT
  // recomputing it — the code these replace did `i - (f.cursor || 0)`, counting
  // an index into the wrong list from a cursor that may never have been read.
  // `|| 0` is what made that silent, so the cases below are mostly about the
  // client refusing rather than the client answering.
  const Q = "44444444-4444-4444-4444-444444444444";
  const askOf = () => focusWrap().querySelector(".ask");
  const optsOf = () => findAll(askOf(), "opt");
  const whyText = () => {
    const w = askOf().querySelector(".askwhy");
    return w ? w.textContent : "";
  };
  // A question longer than the 200 chars `focus.ask` is clipped to, because the
  // tail of a question is usually where the actual choice is.
  const LONGQ = "Ticket 3 splits the detector from the retune, and the two share " +
    "the range walk, the fixture and most of the review surface — so the only " +
    "real difference is how much lands in one commit. Fold it into ticket 1, or " +
    "keep the two separate?";
  const SET = () => ({
    index: 1, count: 2, question: LONGQ, header: "Granularity",
    multiSelect: false, tappable: true, fallback: "",
    options: [
      // Signed, and measured against the widget's ROWS: the cursor is on the
      // second option's row, so the first is one Up away and the second is zero.
      {label: "Fold into ticket 1", description: "one tracer bullet, one review",
       row: 2, steps: -1, checked: null},
      {label: "Keep separate", description: "two tickets, two reviews, twice the run-up",
       row: 3, steps: 0, checked: null},
    ],
  });
  world = [S(Q, "question", 1, "asker"), S(A, "yourmove", 1, "alpha")];
  // Three exchanges, so there IS something above the seam and the landing parks
  // it 250px down rather than under the header — the peek is what the floor
  // spends, so a Session with no peek would prove nothing about spending it.
  sbOf[Q] = SB().concat(SB()).concat(SB());
  askSet = SET();
  win.scrollY = 0;
  sandbox.setPinned(Q);
  await poll();

  ok("ask set: the block says which Ask of the Set it is, and the Ask's header",
     /ask 2 of 2/.test(askOf().textContent) && /Granularity/.test(askOf().textContent),
     askOf().textContent.slice(0, 80));
  ok("ask set: the FULL question, not the 200-char clip the queue's one-liner takes",
     askOf().querySelector(".qtext").textContent === LONGQ && LONGQ.length > 200,
     String(askOf().querySelector(".qtext").textContent.length));
  ok("ask set: and every option carries the description that decides it",
     optsOf().length === 2 &&
     optsOf().map((o) => o.querySelector(".odesc").textContent).join("|") ===
       "one tracer bullet, one review|two tickets, two reviews, twice the run-up",
     optsOf().map((o) => o.textContent).join(" | "));
  // The whole point of the slice: the options are a thing you READ, in the card,
  // and the sticky bar is the reply box and nothing else.
  ok("ask set: the options are in the card — the sticky bar keeps only the reply box",
     findAll(focusWrap().querySelector(".respond"), "opt").length === 0 &&
     optsOf().length === 2 && !!focusWrap().querySelector(".ti"),
     focusWrap().querySelector(".respond").textContent);

  let n0 = respondLog.length;
  optsOf()[0].dispatch("click");
  await settle();
  ok("ask set: a tap sends the SERVER's signed steps — an Up, because the cursor is past it",
     respondLog.length === n0 + 1 &&
     JSON.stringify(respondLog[n0].keys) === JSON.stringify(["up", "enter"]),
     JSON.stringify(respondLog.slice(n0)));
  n0 = respondLog.length;
  optsOf()[1].dispatch("click");
  await settle();
  ok("ask set: and zero steps is a bare enter — the index into the options is never used",
     respondLog.length === n0 + 1 &&
     JSON.stringify(respondLog[n0].keys) === JSON.stringify(["enter"]),
     JSON.stringify(respondLog.slice(n0)));

  // 326 of 425 Asks on disk are a Set of one, so a permanent "ask 1 of 1" is
  // noise on three quarters of every ask this Board will ever draw.
  askSet = Object.assign(SET(), {index: 0, count: 1});
  await poll();
  ok("ask set: a Set of one draws no position line — 'ask 1 of 1' is noise",
     !/ask 1 of/.test(askOf().textContent) && optsOf().length === 2,
     askOf().textContent.slice(0, 60));

  // multiSelect: one tap is ONE TOGGLE, and the ticks are the pane's, never
  // this card's — it is rebuilt every poll and the pane is the truth.
  const MULTI = (a, b) => Object.assign(SET(), {
    index: 0, count: 1, multiSelect: true, header: "Pick any",
    options: [
      {label: "the detector", description: "walks the range", row: 0, steps: 0, checked: a},
      {label: "the retune", description: "accepts the proposal", row: 1, steps: 1, checked: b},
    ],
  });
  askSet = MULTI(true, false);
  await poll();
  ok("ask set: a multiSelect draws its ticks from the payload, and marks them twice over",
     optsOf()[0].textContent.startsWith("☑") && hasCls(optsOf()[0], "on") &&
     optsOf()[1].textContent.startsWith("☐") && !hasCls(optsOf()[1], "on"),
     optsOf().map((o) => o.className + ":" + o.textContent.slice(0, 3)).join(" | "));
  n0 = respondLog.length;
  optsOf()[1].dispatch("click");
  await settle();
  ok("ask set: one tap on a multiSelect is ONE TOGGLE — space, never enter",
     respondLog.length === n0 + 1 &&
     JSON.stringify(respondLog[n0].keys) === JSON.stringify(["down", "space"]),
     JSON.stringify(respondLog.slice(n0)));
  // The pane is the truth: the tap above must not have left a tick behind in the
  // client. Force a REBUILD with the same Ask Set — a card that remembered the
  // tap would now disagree with the screen it is supposed to be reporting, and
  // the card is rebuilt every time anything about this Run moves.
  world[0].updatedAt = 2000;
  await poll();
  ok("ask set: the tick is not remembered locally — the next poll re-reads the pane",
     optsOf()[1].textContent.startsWith("☐") && !hasCls(optsOf()[1], "on"),
     optsOf().map((o) => o.textContent.slice(0, 3)).join(" | "));
  askSet = MULTI(true, true);
  await poll();
  ok("ask set: and when the pane says both are ticked, both are ticked",
     optsOf().every((o) => hasCls(o, "on") || hasCls(o, "done")),
     optsOf().map((o) => o.className).join(" | "));
  const doneBtn = optsOf().find((o) => hasCls(o, "done"));
  n0 = respondLog.length;
  doneBtn.dispatch("click");
  await settle();
  ok("ask set: submitting the ticks is a separate control, and the only enter on it",
     !!doneBtn && respondLog.length === n0 + 1 &&
     JSON.stringify(respondLog[n0].keys) === JSON.stringify(["enter"]),
     JSON.stringify(respondLog.slice(n0)));

  // A refusal, and every shape of it. The read survives; the tap does not.
  askSet = Object.assign(SET(), {
    tappable: false, fallback: "no-cursor",
    options: SET().options.map((o) => Object.assign({}, o, {row: null, steps: null})),
  });
  await poll();
  n0 = respondLog.length;
  optsOf().forEach((o) => o.dispatch("click"));
  await settle();
  ok("ask set: an untappable Ask still READS — options and descriptions, in full",
     optsOf().length === 2 && optsOf().every((o) => hasCls(o, "ro")) &&
     /two tickets, two reviews/.test(askOf().textContent),
     optsOf().map((o) => o.className).join(" | "));
  ok("ask set: and nothing on it can send a keystroke — not one control, not one tap",
     respondLog.length === n0 && optsOf().every((o) => o.tag !== "button") &&
     findAll(askOf(), "done").length === 0,
     JSON.stringify(respondLog.slice(n0)));
  ok("ask set: it says WHY, in plain language, and where to answer instead",
     /read-only/.test(whyText()) && /no cursor/.test(whyText()) &&
     /terminal/.test(whyText()) && /box below/.test(whyText()), whyText());

  // A REFUSED multiSelect has no toggle state either — `checked` is null on
  // every option of every fallback. Drawing that as ☐ would be this slice's own
  // bug in miniature: an unread value rendered as a confident one, which is
  // `cursor || 0` wearing a checkbox.
  askSet = Object.assign(MULTI(null, null), {tappable: false, fallback: "pane-mismatch"});
  await poll();
  ok("ask set: an unread toggle is a third state — never drawn as 'not ticked'",
     optsOf().every((o) => o.textContent.startsWith("?") && !hasCls(o, "on")) &&
     findAll(askOf(), "unread").length === 2,
     optsOf().map((o) => o.textContent.slice(0, 2)).join(" | "));

  // `unmatched` is the original bug's own case: we do not know WHICH Ask is on
  // screen, so its answers are withheld rather than drawn under the wrong
  // question — and the block still has to make sense with none.
  askSet = {index: -1, count: 2, question: "", header: "", multiSelect: false,
            options: [], tappable: false, fallback: "unmatched"};
  await poll();
  ok("ask set: `unmatched` carries no options at all — never q1's answers under q2",
     optsOf().length === 0 && !!askOf(), askOf() && askOf().textContent);
  ok("ask set: and with no options the block still says what it is looking at, and why not",
     askOf().querySelector(".qtext").textContent === "what now?" &&
     !/ask 0 of|ask -1/.test(askOf().textContent) && /not the one/.test(whyText()),
     askOf().textContent.slice(0, 120));

  // Five refusals, five different causes — a refusal you cannot tell apart from
  // a different refusal is half a silent failure.
  const whys = [];
  for (const fb of ["no-pane", "no-widget", "unmatched", "pane-mismatch", "no-cursor"]) {
    askSet = Object.assign(SET(), {tappable: false, fallback: fb});
    await poll();
    whys.push(whyText());
  }
  ok("ask set: each way of refusing says a different thing, because each has a different cause",
     new Set(whys).size === 5 && whys.every((w) => w.length > 40), JSON.stringify(whys));

  // The permission menu has no Ask Set — the legacy triple still serves it, and
  // the cursor rule is the same rule: unread means untappable. This is the exact
  // line `|| 0` used to paper over.
  askSet = {};
  legacyOpts = ["Yes", "Yes, and don't ask again", "No"];
  legacyCursor = null;
  await poll();
  n0 = respondLog.length;
  optsOf().forEach((o) => o.dispatch("click"));
  await settle();
  ok("menu: an unread cursor is not row 0 — the permission menu refuses too",
     optsOf().length === 3 && optsOf().every((o) => o.tag !== "button") &&
     respondLog.length === n0 && /no cursor/.test(whyText()),
     whyText() + " || " + JSON.stringify(respondLog.slice(n0)));
  legacyCursor = 1;
  await poll();
  n0 = respondLog.length;
  optsOf()[2].dispatch("click");
  await settle();
  ok("menu: and a cursor that WAS read steps from where it actually sits",
     respondLog.length === n0 + 1 &&
     JSON.stringify(respondLog[n0].keys) === JSON.stringify(["down", "enter"]),
     JSON.stringify(respondLog.slice(n0)));
  legacyOpts = []; legacyCursor = 0;

  // --- FREE TEXT: where it lands, and when landing it cancels the ask -------
  // The seventh and worst of ADR 0020's defects, measured on a live probe: text
  // typed straight at a question widget left the frame byte-identical, and the
  // Enter after it answered with whatever row the cursor sat on. The server now
  // routes it and says on the wire which route this is; everything below is the
  // phone's half — the routing itself is not this file's to assert.
  askSet = SET();
  textRoute = {route: "affordance", reason: ""};
  sandbox.setPinned(Q); await poll();
  const sendBtn = () => focusWrap().querySelector(".send");
  let n1 = respondLog.length, c1 = confirmLog.length;
  ti().value = "the considered answer";
  sendBtn().dispatch("click");
  await settle();
  ok("free text: the ordinary route is an ordinary send — no warning, no confirm",
     sendBtn().textContent === "respond →" && !hasCls(sendBtn(), "danger") &&
     confirmLog.length === c1 && respondLog.length === n1 + 1 &&
     respondLog[n1].text === "the considered answer" &&
     respondLog[n1].cancelAsk === undefined,
     sendBtn().textContent + " || " + JSON.stringify(respondLog.slice(n1)));

  textRoute = {route: "esc", reason: "no-row"};
  await poll();
  const warnOf = () => findAll(focusWrap(), "warn")[0];
  ok("free text: when the only route is Esc, the button stops saying 'respond'",
     sendBtn().textContent === "cancel ask & send →" && hasCls(sendBtn(), "danger"),
     sendBtn().textContent + " / " + sendBtn().className);
  ok("free text: and the composer says what Esc does, in words, before you type",
     !!warnOf() && /CANCELS the ask/.test(warnOf().textContent),
     warnOf() ? warnOf().textContent : "(no warning)");

  n1 = respondLog.length; c1 = confirmLog.length;
  confirmReply = false;
  ti().value = "a considered answer";
  sendBtn().dispatch("click");
  await settle();
  ok("free text: cancelling the ask is confirmed first, and 'no' sends nothing",
     confirmLog.length === c1 + 1 && /CANCELS/.test(confirmLog[c1]) &&
     respondLog.length === n1 && ti().value === "a considered answer",
     JSON.stringify(confirmLog.slice(c1)) + " || " + JSON.stringify(respondLog.slice(n1)));

  confirmReply = true;
  n1 = respondLog.length;
  sendBtn().dispatch("click");
  await settle();
  ok("free text: and 'yes' sends consent as its own field — never `force`",
     respondLog.length === n1 + 1 && respondLog[n1].cancelAsk === true &&
     respondLog[n1].force === undefined && respondLog[n1].text === "a considered answer",
     JSON.stringify(respondLog.slice(n1)));

  // The label came from a poll, and a poll is seconds old. The server re-reads
  // the pane at send time and can refuse what the label said was fine — which is
  // what stops a stale label cancelling a question silently.
  textRoute = {route: "affordance", reason: ""};
  await poll();
  respondReplies.push([409, {ok: false, route: "esc", reason: "no-row",
                             message: "free text can only reach it by pressing Esc first — " +
                                      "which CANCELS the question instead of answering it."}]);
  n1 = respondLog.length; c1 = confirmLog.length;
  ti().value = "typed while the widget moved";
  sendBtn().dispatch("click");
  await settle();
  ok("free text: a route that changed under the label is confirmed, then retried with consent",
     confirmLog.length === c1 + 1 && /CANCELS the question/.test(confirmLog[c1]) &&
     respondLog.length === n1 + 2 && respondLog[n1].cancelAsk === undefined &&
     respondLog[n1 + 1].cancelAsk === true && ti().value === "",
     JSON.stringify(respondLog.slice(n1)));

  respondReplies.push([409, {ok: false, route: "esc", message: "cancels the question"}]);
  confirmReply = false;
  n1 = respondLog.length;
  ti().value = "not this time";
  sendBtn().dispatch("click");
  await settle();
  ok("free text: declining that one leaves the ask alone and keeps the text",
     respondLog.length === n1 + 1 && ti().value === "not this time",
     JSON.stringify(respondLog.slice(n1)));
  confirmReply = true;

  // NOBODY READ THE SCREEN. There is no "send anyway" here on purpose: the
  // anyway IS the bug — a blind send-keys at a frame nobody looked at (ADR 0021).
  respondReplies.push([409, {ok: false, route: "refuse",
                             message: "could not read this Run's screen"}]);
  n1 = respondLog.length; c1 = confirmLog.length;
  ti().value = "into the dark";
  sendBtn().dispatch("click");
  await settle();
  ok("free text: an unreadable screen is refused outright — not confirmed, not retried",
     respondLog.length === n1 + 1 && confirmLog.length === c1 &&
     ti().value === "into the dark" && /could not read/.test(doc.getElementById("toast").textContent),
     doc.getElementById("toast").textContent + " || " + JSON.stringify(respondLog.slice(n1)));

  textRoute = {route: "plain", reason: ""};
  askSet = {};
  ti().value = "";

  // THE LANDING, RE-DERIVED. The Ask block now runs hundreds of px past its own
  // question, so "clear the whole block" would spend the entire peek on every
  // Blocked landing and still come up short. The floor clears the question plus
  // the FIRST option — the blocker and at least one answer, with its reasoning.
  askSet = SET();
  layout.seamTop = 400;
  layout.barTop = 700; layout.barHeight = 100;
  layout.askTop = 820; layout.askBottom = 2400;   // question + four descriptions
  layout.optTop = 900; layout.optBottom = 960;    // the first option inside it
  win.scrollY = 0; sandbox.setPinned(A); await poll();
  win.scrollY = 0; sandbox.setPinned(Q); await poll();
  ok("landing: a Blocked Focus now clears its blocker AND its first option",
     win.scrollY === parked() + (layout.optBottom - (layout.barTop - 10)),
     String(win.scrollY));
  // Demanding the whole block would have hit the cap — every pixel of the peek
  // spent, and the last three options still under the bar. A worse trade than a
  // flick, and it costs you the newest prose the Ask is asking about.
  ok("landing: not the whole option list — that would spend the entire peek and still fall short",
     layout.askBottom - (layout.barTop - 10) > layout.seamTop - headPadStub() &&
     win.scrollY !== parked() + (layout.seamTop - headPadStub()),
     String(win.scrollY) + " vs capped " + (parked() + layout.seamTop - headPadStub()));
  layout.barTop = 800; layout.barHeight = 0;
  layout.askTop = 0; layout.askBottom = 0; layout.optTop = 0; layout.optBottom = 0;
  askSet = {};

  // --- What only the stylesheet can answer ----------------------------------
  // The stub runs no CSS, so everything above proves the client toggles a class.
  // Whether that class actually yields the pixels — and whether the column is
  // bounded — lives in board.html, so these read it.
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = (sel) => {
    const m = new RegExp("^" + esc(sel) + "\\{([^}]*)\\}", "m").exec(HTML);
    return m ? m[1] : "(no rule for " + sel + ")";
  };
  // `.hid` is one class and TWO rules, because the two bars sit at different
  // edges. At the bottom edge hidden chrome must reserve nothing, and a
  // transform delivers that. At the top it cannot: `.fhead` is `position:sticky`
  // and sticky is IN FLOW, so the transform this list used to include moved the
  // paint and kept every pixel of the box — a blank band where the header had
  // been. The header condenses instead.
  const DOCKED = [".respond.hid", ".swipehint.hid"];

  ok("no layout reserved: the chrome is sticky, so the turns scroll UNDER it",
     rule(".fhead").includes("position:sticky") && rule(".respond").includes("position:sticky"),
     rule(".fhead") + " || " + rule(".respond"));
  ok("no layout reserved: at the BOTTOM edge hiding is a transform, so the box goes with the paint",
     DOCKED.every((s) => /transform:translateY/.test(rule(s))) &&
     !DOCKED.some((s) => /display:none|height:0|max-height/.test(rule(s))),
     DOCKED.map(rule).join(" || "));
  // The header's rule, and the bug it replaces: a sticky element cannot be
  // hidden by moving it, so this one is never hidden at all.
  ok("condense: the sticky header never transforms away — that stranded its box",
     !/transform/.test(rule(".fhead.hid")) && !/opacity/.test(rule(".fhead.hid")) &&
     !/display:none/.test(rule(".fhead.hid")), rule(".fhead.hid"));
  ok("condense: it gets smaller instead — less padding, and the Workspace at reading-dim",
     /padding:5px 0/.test(rule(".fhead.hid")) && /color:var\(--fg3\)/.test(rule(".fhead.hid")) &&
     /font-size/.test(rule(".fhead.hid .fdir")),
     rule(".fhead.hid") + " || " + rule(".fhead.hid .fdir"));
  // What condense KEEPS is the whole decision (ADR 0023): wherever a Run is
  // named, the Workspace names it. Everything else on the strip is droppable and
  // this is the list — badge, age, sessionId, title, queue pill, ＋ and the
  // spacer that would otherwise hold row two open.
  const DROPPED = [".fbadge", ".grow", ".fmeta", ".fsid", ".fabout", ".zbtn", ".iplus"];
  const condensedOut = (/^([^{}]*\.fhead\.hid[^{}]*)\{display:none\}/m.exec(HTML) || ["", ""])[1];
  ok("condense: everything but the Workspace goes, and the Workspace never does",
     DROPPED.every((c) => condensedOut.includes(".fhead.hid " + c)) &&
     !condensedOut.includes(".fdir"), condensedOut);
  // …and the **Nickname** goes with the Workspace, not with the list above. It
  // is the second level of *where am I* (ADR 0026), and the condensed header is
  // the one place both levels have to survive.
  ok("condense: the Nickname condenses beside the Workspace and is never dropped",
     !condensedOut.includes(".fnick") && /font-size/.test(rule(".fhead.hid .fnick")),
     condensedOut + " || " + rule(".fhead.hid .fnick"));
  // ADR 0023's bug was one item on row one with no floor, paying every shortfall
  // in full. Two items now, and giving the new one no floor would reproduce that
  // inversion one level down — at 390px the Nickname would be what vanishes.
  ok("row one: BOTH items are floored, so neither can render as zero characters",
     /min-width:\d+ch/.test(rule(".fdir")) && /min-width:\d+ch/.test(rule(".fnick")) &&
     !/min-width:0/.test(rule(".fdir")), rule(".fdir") + " || " + rule(".fnick"));
  ok("row one: and the Nickname is the one that yields first, the Workspace last",
     /flex:0 4 auto/.test(rule(".fnick")) && /flex:0 1 auto/.test(rule(".fdir")),
     rule(".fnick"));
  // The flicker, in the two places the stylesheet can speak to it. Animating a
  // property that changes height reflows on every frame of the collapse, and
  // scroll anchoring hands that reflow back to the handler that caused it as
  // travel. See the spec: not bisected, so all three ship together.
  ok("condense: nothing that changes height is animated — the box snaps, only the colour eases",
     /transition:color/.test(rule(".fhead")) &&
     !/transition:[^;]*(padding|height|font-size|transform)/.test(rule(".fhead")),
     rule(".fhead"));
  ok("condense: and scroll anchoring is off, so the collapse cannot be read back as a drag",
     /overflow-anchor:none/.test(rule("html")) && /overflow-anchor:none/.test(rule("body")),
     rule("html") + " || " + rule("body"));
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

  // ADR 0020's layout claim, in the one place that can prove it: the options
  // LEFT the sticky bar. The DOM tests above prove the client builds them into
  // the card; only the sheet can say the bar no longer dresses them, and that
  // nothing wrapping-chip-shaped is left behind on it.
  ok("ask set: the options are authored with the Ask, above the sticky bar's own rules",
     HTML.indexOf(".opts{") < HTML.indexOf(".respond{") &&
     HTML.indexOf(".opts{") > HTML.indexOf(".ask{") &&
     rule(".respond").includes("position:sticky"), rule(".opts"));
  ok("ask set: one option per row, full width — a chip row cannot carry a description",
     /flex-direction:column/.test(rule(".opts")) && !/flex-wrap/.test(rule(".opts")) &&
     /width:100%/.test(rule(".opt")), rule(".opts") + " || " + rule(".opt"));
  // The read is prose and wears the reading face (ADR 0018); the counter and the
  // widget's tab label are machine text and keep the machine one. Nothing here
  // names a colour — a hardcoded literal cannot be swapped by the light theme.
  // Everything you READ in this block — the question, the labels, the
  // descriptions and the sentence explaining a refusal — is prose. The counter
  // and the widget's tab label are not, and keep the machine face.
  ok("ask set: everything you read in the block is prose, so it wears the reading face",
     [".ask .qtext", ".olbl", ".odesc", ".askwhy"].every((s) => /var\(--face\)/.test(rule(s))) &&
     ![".askn", ".askhdr"].some((s) => /var\(--face\)/.test(rule(s))),
     [".askwhy", ".askn"].map(rule).join(" || "));
  ok("ask set: and every colour on the block is a token, so both themes get it",
     [".opt", ".opt.on", ".opt.ro", ".askwhy", ".wlbl", ".askn", ".askhdr", ".obox",
      ".obox.unread", ".opt.done"].every((s) => !/#[0-9a-f]{3}|rgb/i.test(rule(s))),
     [".opt", ".askwhy", ".askn"].map(rule).join(" || "));

  // The destructive send is a different control, and the stub runs no CSS — only
  // the sheet can say it is dressed as one, in tokens both themes define.
  ok("free text: the cancelling send is dressed as its own control, in tokens",
     /var\(--red\)/.test(rule(".send.danger")) &&
     !/#[0-9a-f]{3}|rgb/i.test(rule(".send.danger")) &&
     !/#[0-9a-f]{3}|rgb/i.test(rule(".queued.warn")) &&
     (HTML.match(/--red:/g) || []).length === 2,
     rule(".send.danger") + " || " + rule(".queued.warn"));

  ok("column: one bounded reading column, wider than the old 640px phone width",
     /--col:740px/.test(HTML) && rule(".wrap").includes("var(--gut)"), rule(".wrap"));
  ok("column: which collapses to a flat 6px gutter on a phone (ADR 0024)",
     /--gut:max\(6px,calc\(50% - var\(--col\)\/2\)\)/.test(HTML));
  ok("column: and the fixed Intake sheet snaps to the SAME column, not the viewport edge",
     rule(".isheet").includes("var(--gut)"), rule(".isheet"));
  // --- the Focus is the page, not a card on it (ADR 0024) -------------------
  // The stub runs no CSS, so the stylesheet is the only place this decision can
  // be asserted — and it is the kind that gets "fixed" back by a reader who sees
  // a borderless reading surface next to a list of bordered rows and assumes the
  // inconsistency is a bug. It is the rule: a card marks one of MANY, and a
  // **Focus** is at most one.
  ok("bleed: the Focus has no box — no panel fill, no border, no corners",
     !/background:/.test(rule(".focus")) && !/border-radius/.test(rule(".focus")) &&
     !/border:1px/.test(rule(".focus")), rule(".focus"));
  ok("bleed: but it keeps the status rule, which is where --lane is set",
     /border-top:2px solid var\(--m\)/.test(rule(".focus")) &&
     /--lane:var\(--m\)/.test(rule(".focus")) &&
     rule(".live").includes("var(--lane)"), rule(".focus"));
  ok("bleed: the Scrollback pays no inner gutter — the read runs to --gut and stops",
     /padding:7px 0 /.test(rule(".sb")), rule(".sb"));
  ok("bleed: and ONE hairline is left inside the read, above the composer",
     !/border-bottom/.test(rule(".sb")) &&
     rule(".respond").includes("border-top:1px solid var(--line)"),
     rule(".sb") + " || " + rule(".respond"));
  // Sticky chrome must stay opaque — the turns scroll under it — so the risk of
  // dropping the card was chrome that kept --panel and became a floating bar.
  ok("bleed: sticky chrome wears the PAGE's background, not the vanished panel's",
     rule(".fhead").includes("background:var(--bg)") &&
     rule(".respond").includes("background:var(--bg)") &&
     !/border-radius/.test(rule(".fhead")) && !/border-radius/.test(rule(".respond")),
     rule(".fhead") + " || " + rule(".respond"));
  ok("bleed: the launching placeholder wears what the Focus wears, so nothing flickers",
     !/background:/.test(rule(".startcard")) && !/border-radius/.test(rule(".startcard")) &&
     /border-top:2px/.test(rule(".startcard")), rule(".startcard"));
  // The **Ask** is still a band to the edge: it is the one block that has to
  // separate itself from prose. The session title is NOT one any more — it moved
  // onto the header's first row beside the Workspace, so the resting header is
  // one band rather than two and condense drops a real row instead of a stripe
  // that was doing separator duty. Nothing named `.about` is left.
  ok("bleed: the Ask is a band to the edge, text still on the column",
     rule(".ask").includes("margin:0 calc(-1 * var(--gut))") &&
     rule(".ask").includes("background:var(--panel2)"), rule(".ask"));
  ok("bleed: and the session title is inline chrome now, not a second stripe under it",
     rule(".about") === "(no rule for .about)" && rule(".albl") === "(no rule for .albl)" &&
     rule(".fabout").includes("var(--face)") && !/margin|background/.test(rule(".fabout")),
     rule(".fabout"));
  // The gutter is ONE token or it is not a gutter. `.zones` spelled 14px in its
  // own hand and agreed with --gut only by luck; ADR 0024 moved the number and
  // that duplicate would have drifted silently.
  // Scoped to the sheet, not the sheet-wide grep: `#toast` has a `padding:8px
  // 14px` of its own and it is a pill's internal padding, not a page gutter.
  // Every surface that spans the WIDTH is what has to read the token.
  ok("gutter: every full-width surface reads the token — none spells it out",
     [".wrap", ".isheet", ".zones", ".swipehint"].every(
       (s) => /padding:[^;]*\bvar\(--gut\)/.test(rule(s))) &&
     !/padding:8px 14px/.test(rule(".zones")),
     [".wrap", ".isheet", ".zones", ".swipehint"].map((s) => s + " => " + rule(s)).join(" || "));

  // The row's priority slot. The stub runs no CSS, so it can prove the client
  // toggled `hi` / `no` / `lo` and never that the three come out LOOKING
  // different — and on a 13px glyph that is the whole design. Only the
  // stylesheet can say it: no pair of levels may differ by opacity alone, or
  // one of them is just another one seen through fog.
  ok("row priority: the three levels differ in colour, not only in opacity",
     rule(".qpri").includes("color:var(--accent)") &&
     rule(".qpri.lo").includes("color:var(--dim)") &&
     rule(".qpri.no").includes("color:var(--dim)") && /opacity/.test(rule(".qpri.no")) &&
     !/opacity/.test(rule(".qpri.lo")),
     rule(".qpri") + " || " + rule(".qpri.lo") + " || " + rule(".qpri.no"));
  // `flex:0 0 auto` on the slot is the same statement `.qdir` makes: the snippet
  // shrinks first. It is what "spend the pixels out of `.qone`" actually means,
  // and it is not assertable anywhere but here.
  ok("row priority: the slot holds its width, so the snippet is what gives way",
     /flex:0 0 auto/.test(rule(".qpri")) && /flex:0 0 auto/.test(rule(".qdir")) &&
     /flex:1 1 0/.test(rule(".qone")), rule(".qpri"));
  // The hit box has to be thumb-sized and the row must not grow to hold it —
  // padding out, margin back, and the negative half is the part that is easy to
  // drop and impossible to notice until the queue is one row taller.
  ok("row priority: the tap target grows, the row does not",
     /padding:7px 3px/.test(rule(".qpri")) && /margin:-7px 0 -7px -3px/.test(rule(".qpri")),
     rule(".qpri"));
  // The `⚑` no longer has a rule of its own: it was a bare colour on a span
  // inside `.qdir`, and both the span and the width it was spending are gone.
  ok("row priority: and the old bare flag rule went with the span that wore it",
     rule(".flag") === "(no rule for .flag)", rule(".flag"));

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
     /--gut:max\(6px,calc\(50% - var\(--rail\)\/2 - var\(--col\)\/2\)\)/.test(mqBlock) &&
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
  // The stub runs no CSS, so only the stylesheet can say that the gutter is ONE
  // column — which is the whole value of the Record, and the thing a variable
  // prose gist could not give (ADR 0017).
  // 2.5rem rather than 40px since ADR 0018 made the read scalable: a gutter
  // pinned in px stops lining up with the type at any other root size. Still
  // ONE column, which is the thing under test.
  ok("fold: the label gutter is one column, the same on every record",
     /grid-template-columns:2\.5rem minmax\(0,1fr\) auto/.test(rule(".rf")), rule(".rf"));
  ok("fold: and every folded line is ONE line — a record's height is fixed",
     rule(".rv").includes("white-space:nowrap") && rule(".rv").includes("text-overflow:ellipsis"),
     rule(".rv"));
  ok("fold: the detached node foldText reads through is never drawn",
     rule(".foldvoid") === "display:none", rule(".foldvoid"));
  // Below the seam the gutter stops and the read takes the full column — the
  // whole design in one line, and only the stylesheet can say it. The rail moved
  // here from ADR 0016's chained block: below the seam the tail IS the reply you
  // are answering.
  ok("seam: below it the read takes the full column and wears the live rail",
     rule(".live").includes("border-left:2px solid var(--lane)") &&
     rule(".chain") === "(no rule for .chain)", rule(".live"));
  ok("seam: and the seam itself is a rule with the lane's own accent on it",
     /text-transform:uppercase/.test(rule(".seaml")) &&
     rule(".seaml").includes("var(--lane)") && /linear-gradient/.test(rule(".seamrule")),
     rule(".seaml"));
  // Density is part of ADR 0017's decision, not a coat of paint: the baseline set
  // this whole read in 13px monospace at 1.6, which is what made it read like a
  // log file. Monospace survives exactly where it MEANS machine.
  //
  // The two faces are named ONCE as variables (ADR 0018 set the read in a serif
  // and let the phone pick the theme), so the assertion is that prose wears the
  // reading face and never the machine one — asserting a font stack here would
  // pin the design to whichever family it happened to name.
  ok("density: prose wears the reading face, not the log-file monospace",
     /var\(--face\)/.test(rule(".md")) && /var\(--face\)/.test(rule(".sb")) &&
     !/var\(--mono\)/.test(rule(".sb")), rule(".md"));
  ok("density: and the machine face survives where it means machine",
     /var\(--mono\)/.test(rule(".md code,.md pre")) && /var\(--mono\)/.test(rule(".rl")) &&
     /var\(--mono\)/.test(rule(".rv.rw")), rule(".md code,.md pre"));
  // …and the variables resolve to real stacks, so the indirection above cannot
  // pass against a face that was never defined.
  ok("density: the two faces are defined, and they are a serif and a monospace",
     /--face:[^;]*serif/.test(HTML) && /--mono:ui-monospace/.test(HTML),
     (HTML.match(/--face:[^;]*/) || [""])[0]);
  // The invariant the edge strip obeyed, restated about the thing that replaced
  // it: the peek is drawn OVER the read and never in it. A cue that pushed the
  // **Scrollback** sideways would be fighting the vertical read for the same
  // pixels, which is the whole reason this is an overlay and not a transform on
  // the page.
  ok("swipe: the peek is a fixed overlay — a cue may not move the read",
     rule("#peek").includes("position:fixed") && rule("#peek").includes("pointer-events:none") &&
     rule(".edge") === "(no rule for .edge)" && !HTML.includes("id=edgel") &&
     !HTML.includes("id=edger"), rule("#peek"));
  // Arming is a DETENT, and the snap is what makes it one: past the threshold
  // board.js writes the resting geometry and this transition lands it. Motion
  // redundant with colour survives sunlight and a colour-blind reader, so the
  // stylesheet has to carry both and neither alone.
  ok("swipe: and arming snaps — the threshold is felt as a detent, not only seen",
     /transition:transform \.12s/.test(rule(".peekpill")) &&
     rule(".peekpill.armed").includes("var(--accent)"), rule(".peekpill"));
  ok("hint: it names the gesture, and clears the read like the rest of this edge",
     HTML.includes("drag sideways to move between Runs") &&
     /transform:translateY/.test(rule(".swipehint.hid")), rule(".swipehint.hid"));

  console.log("\n  " + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
