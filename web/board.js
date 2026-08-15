"use strict";
// Board client. Fetches GET /api/board and renders the curated round-robin.
// Everything is built with createElement + textContent (never innerHTML for
// data) — the ONE exception is a **Turn**'s `html`, which the server renders as
// escape-first markdown (see ADR 0006, widened from one field to N by ADR 0014:
// same function, same guarantee). That single sink is greppable below as
// `.innerHTML =`, in turnEl().
//
// Since ADR 0008 the Board is the Launcher's only page, so it also carries
// **Intake** (dir-launch, resume, Recover, task/dispatch buttons — a sheet behind
// the ＋ in the Focus's header since ADR 0015, a docked bottom bar before that),
// the per-run close (×) and deep-link (↗), and the optimistic launch card.
// Below all of it, outside the triage surface, sit the Foreign Runs — seen,
// never driven, and transferable in one tap (foreignZone, ADR 0012).
//
// The whole page is one bounded reading column (board.html: --col / --gut), and
// the chrome around the Focus — its header and its composer, which now owns the
// bottom edge outright — follows the scroll rather than a mode. See the chrome
// section below (syncChrome).
//
// Rotation is consent-based (CONTEXT.md: Focus, Rotation). Two rules carry it,
// and between them nothing the Board does can cost you a half-typed reply or
// your place in the context:
//   1. You hold the Focus. It is adopted the first time the server hands one
//      over and pinned from then on, so urgency orders the *queue* and never
//      the card in front of you. It moves when you move it — or when it
//      resolves out from under you (advance-on-resolve, in render).
//   2. The Focus card is never rebuilt unless its own data moved, and a rebuild
//      carries the reply box and your reading position across (renderFocus). One
//      ETag covers the whole board, so any other Run's churn redraws this page;
//      that redraw must not reach the card.
//   3. There is a way BACK. Answering a Run sends it three headings down the
//      page, and the thought you want to add arrives minutes later — after any
//      "keep this card" affordance could have helped, because you did not have
//      the thought yet. So the Focus walks a ring: a swipe on a phone, a
//      persistent rail on a monitor (the return path, below). Both end at
//      setPinned, both are yours to trigger, and neither ever moves the Focus
//      on its own.

const app = document.getElementById("app");
const summary = document.getElementById("summary");
const toastEl = document.getElementById("toast");
const pendingEl = document.getElementById("pending");
const dirEl = document.getElementById("dir");
const dirpopEl = document.getElementById("dirpop");
const sidEl = document.getElementById("sid");
const dirRootEl = document.getElementById("dirroot");
const launchEl = document.getElementById("launch");
const resumeEl = document.getElementById("resume");
const isheetEl = document.getElementById("isheet");       // the **Intake** sheet
const iscrimEl = document.getElementById("iscrim");       // and its dismissal
const iheadEl = document.getElementById("ihead");
const tasksEl = document.getElementById("tasks");
const taskslblEl = document.getElementById("taskslbl");
const recoverBtnEl = document.getElementById("recover");  // the Recover ROW, in
                                                          // the sheet (ADR 0015)
const recovPanelEl = document.getElementById("recovpanel");
const recovTitleEl = document.getElementById("recovtitle");
const recovSubEl = document.getElementById("recovsub");
const recovListEl = document.getElementById("recovlist");
const recovGoEl = document.getElementById("recovgo");
const recovCloseEl = document.getElementById("recovclose");
const railEl = document.getElementById("rail");           // the >=900px queue
const edgeLEl = document.getElementById("edgel");         // the swipe's landing
const edgeREl = document.getElementById("edger");         // cue, one per side
const hintEl = document.getElementById("swipehint");

// #app is split in two, once, up front: the Focus card gets its own container so
// redrawing the queue below can never touch it. That redraw is what used to eat
// a half-typed reply — see renderFocus().
const focusWrap = el("div");
const zonesWrap = el("div", "zones");
app.append(focusWrap, zonesWrap);

// --- the two sheets: the queue, and Intake ----------------------------------
// THE QUEUE SHEET (narrow only; the rail is the wide half). A phone has no 290px
// to spend on a permanent rail, and stacking the queue under the **Scrollback**
// puts it at the end of an unbounded read — the further you read, the further
// away the rest of the Board gets. So at this width the same list is a sheet over
// the read, opened from the count in the Focus's sticky header. At >=900px the CSS
// returns .zones to ordinary flow and none of this state is reachable, because
// the button that sets it is hidden.
//
// THE INTAKE SHEET is its peer and deliberately the same object (ADR 0015): every
// shape of **Intake** over the read, a scrim behind it, opened from the ＋ in the
// same sticky header, dismissed by the scrim or by acting. It exists at EVERY
// width — nothing draws Intake in the rail, so there is no wide half for it to
// step aside for.
//
// AND THEY ARE MUTUALLY EXCLUSIVE. Two stacked sheets over one read is not a
// state: each setter closes the other, guarded by `if (open)` so opening one
// cannot bounce back and close itself.
let queueOpen = false;
let intakeSheetOpen = false;
// The empty-Board layout (ADR 0015): no **Focus** means no `.fhead`, so no ＋ and
// no sheet — Intake renders inline in the page flow instead, always open, with
// nothing that dismisses it. Held here because every dismissal below has to
// refuse while it is true.
let intakeInline = false;
const scrimEl = document.getElementById("zscrim");

function queueCount() {
  if (!boardData) return 0;
  return ringGroups(false).reduce((n, g) => n + g.items.length, 0);
}

// The one class-toggle vocabulary this file uses — setHid is the same call — so
// there is nothing here a reader has to learn twice.
function setCls(node, cls, on) {
  if (!node) return;
  const base = (node.className || "").split(" ").filter((c) => c && c !== cls).join(" ");
  node.className = on ? base + " " + cls : base;
}

function setQueueOpen(open) {
  queueOpen = !!open;
  setCls(zonesWrap, "open", queueOpen);
  if (scrimEl) scrimEl.hidden = !queueOpen;
  if (queueOpen) setIntake(false);
}

// Open or shut the Intake sheet. Inline Intake is not a sheet and has no closed
// state, so it refuses outright rather than quietly desyncing the ＋ from it.
function setIntake(open) {
  if (intakeInline) return;
  intakeSheetOpen = !!open;
  setCls(isheetEl, "open", intakeSheetOpen);
  if (iscrimEl) iscrimEl.hidden = !intakeSheetOpen;
  markIntakePlus();
  if (intakeSheetOpen) {
    setQueueOpen(false);
    // The dir field is the hot path (ADR 0008), and focusing it also paints the
    // recent-dirs dropup — so the sheet opens on the thing you most likely came
    // for rather than on a row you then have to tap.
    if (dirEl && dirEl.focus) dirEl.focus();
  }
  applyChrome();   // an Intake that just closed may slide away with the rest
}

// The ＋ lives in the Focus's card header, so it is rebuilt whenever the card is;
// focusCard() seeds it from this state and this re-marks the live one.
function markIntakePlus() {
  const b = focusWrap.querySelector(".iplus");
  if (!b) return;
  setCls(b, "hot", intakeSheetOpen);
  b.setAttribute("aria-expanded", intakeSheetOpen ? "true" : "false");
}

// Sheet ⇄ inline. Driven by renderFocus, because the presence of a **Focus** is
// the whole condition: the ＋ rides `.fhead`, and there is no `.fhead` without a
// card to put it in.
function setIntakeInline(on) {
  intakeInline = !!on;
  if (intakeInline) {
    intakeSheetOpen = false;
    if (iscrimEl) iscrimEl.hidden = true;
    setCls(isheetEl, "open", false);
  }
  setCls(isheetEl, "inline", intakeInline);
  // The heading carries the difference, because the surface itself does not: on
  // an empty Board this is not "also available", it is the only thing there is.
  iheadEl.textContent = intakeInline
    ? "nothing running · start something" : "intake · start something new";
}

if (scrimEl) scrimEl.addEventListener("click", () => setQueueOpen(false));
if (iscrimEl) iscrimEl.addEventListener("click", () => setIntake(false));
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  setQueueOpen(false);
  setIntake(false);
});

// THE SHAPE, NOT THE STATE. These read `blocked · question` / `blocked · approval`
// once, and at 18 characters the badge was the widest thing in the header's second
// row — competing with the **Workspace** for a phone's pixels to say a word the
// card's own coloured top border (`.bq` / `.bp`) already says. "Blocked" is the
// state; `question` and `approval` are the two shapes of an **Ask**, which is the
// part you cannot get from a colour. The other three lanes were never the problem.
const LANE_LABEL = {question: "question", approval: "approval",
                    yourmove: "your move", working: "working", snoozed: "snoozed"};
// THERE IS NO LANE_NOUN. It captioned the age in the Focus's header with a
// second word for the lane `.fbadge` beside it already names, and that duplicate
// was 55-63px the 390px header did not have — see focusCard.
const ROW_CLS = {question: "lane-q", approval: "lane-p", yourmove: "lane-m",
                 working: "lane-w", snoozed: "lane-w"};
const ROW_BADGE = {question: "question", approval: "approval", yourmove: "your move",
                   working: "working", snoozed: "snoozed"};

// **Blocked**: paused awaiting a specific required input from you (CONTEXT.md).
// The two lanes that have an **Ask**, and the only two that draw one.
const isBlocked = (f) => f.lane === "question" || f.lane === "approval";

function el(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
}

// --- eliding: two functions, because a name and a path are not the same thing --
// One function branching on "/" would be one function with two contracts, and
// neither caller is ever ambiguous about which it holds: `.fdir` is handed a
// **Workspace** (already a basename, server-side) and `.recovdir` is handed a
// path. So they are named for what they take.
//
// Both measure, they do not estimate: `scrollWidth > clientWidth` is the browser
// telling you it clipped, at the real font, after layout. Call them from a
// `nextFrame` — before layout every width is 0 and every string "fits". That is
// also why they no-op cleanly under the stub DOM the tests run: it has no
// layout, so nothing is ever measured as clipped and the text stands as given.

// The **Workspace**, repo-biased. Two worktrees of one repo differ ONLY in the
// slug (`claude-launcher-scrollback-fold` vs `claude-launcher-recover-filter`),
// so a head-first truncation — CSS's ellipsis, i.e. what this replaces — keeps
// the part you already knew and drops the only part that tells them apart. That
// is the context switch failing, which is the whole reason this exists. Keep
// enough of the repo to recognise it, then the tail, whole.
// TRUE means "this node is overflowing and there is a real measurement saying
// so". `clientWidth` of 0 is not a narrow box, it is a box nobody laid out — an
// unattached node, a hidden parent, or the stub DOM the tests drive — and
// treating it as narrow elides every string on the page down to a stump. So the
// unmeasured case leaves the text exactly as given.
function clipped(node) {
  return node.clientWidth > 0 && node.scrollWidth > node.clientWidth;
}

const WS_PREFIX = 10;
function elideWorkspace(node, name) {
  node.textContent = name || "—";
  if (!name || !clipped(node)) return;
  for (let tail = name.length - WS_PREFIX; tail > 0; tail--) {
    node.textContent = name.slice(0, WS_PREFIX) + "…" + name.slice(name.length - tail);
    if (!clipped(node)) return;
  }
  // Narrower than `head…t`: nothing sensible is left, so stop rather than emit
  // a string that is more ellipsis than name.
}

// A path, by whole segments. A path is a LIST, not a string: eliding mid-segment
// gives `~/project…er-scrollback-fold`, which reads as a typo rather than as a
// path with something missing. Dropping `.worktrees` entire is honest and costs
// nothing anyone reads. First segment and last segment always survive — the last
// especially, since it is the Workspace by another name.
function elidePath(node, path) {
  node.textContent = path || "";
  if (!path || !clipped(node)) return;
  const parts = path.replace(/\/+$/, "").split("/");
  // `drop` segments after the first go; the first and everything past them stay.
  // Fewer than three segments has no middle to drop — CSS clips it, as before.
  for (let drop = 1; parts.length - drop >= 2; drop++) {
    node.textContent = parts[0] + "/…/" + parts.slice(drop + 1).join("/");
    if (!clipped(node)) return;
  }
}

function age(ms) {
  if (!ms) return "";
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return Math.floor(s) + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.hidden = true; }, 2600);
}

// --- Respond: token held client-side (never served), sent per request ------
function getToken() {
  let t = localStorage.getItem("cl_token");
  if (!t) {
    t = (window.prompt("Enter launcher token (CLAUDE_LAUNCHER_TOKEN):") || "").trim();
    if (t) localStorage.setItem("cl_token", t);
  }
  return t;
}

// Resolves true only when the text actually reached the pane — the caller clears
// the box on that, and only that.
async function sendRespond(f, payload, force) {
  const token = getToken();
  if (!token) { toast("no token — respond cancelled"); return false; }
  const body = Object.assign({runId: f.runId, token}, payload);
  if (force) body.force = true;
  let r;
  try {
    r = await fetch("api/respond", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
  } catch (e) { toast("respond unreachable"); return false; }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { localStorage.removeItem("cl_token"); toast("token rejected — re-enter"); return false; }
  // The screen could not be read, so nothing was typed at it (ADR 0021). There
  // is no "send anyway" here on purpose: the anyway IS the bug — a blind
  // send-keys at a frame nobody read.
  if (r.status === 409 && data.route === "refuse") {
    toast(data.message || "could not read the screen — nothing sent");
    return false;
  }
  // The destructive route, raised from the pane as it is NOW rather than as the
  // last poll saw it. The button below is already labelled for this when the
  // poll agreed; this is the same question asked against a fresher read, and it
  // is what makes a stale label unable to cancel a question silently.
  if (r.status === 409 && data.route === "esc") {
    if (window.confirm((data.message || "sending this cancels the question") +
        "\n\nCancel the question and send your text to the input box anyway?")) {
      return sendRespond(f, Object.assign({}, payload, {cancelAsk: true}), force);
    }
    toast("cancelled — the ask is untouched");
    return false;
  }
  if (r.status === 409) {   // the box already has unsent text — never blind-append
    if (window.confirm("This session already has unsent text:\n\n" + (data.existing || "") +
        "\n\nSend your reply anyway? It will be added below the above.")) {
      return sendRespond(f, payload, true);
    }
    toast("cancelled — clear the box on the Mac first");
    return false;
  }
  if (!r.ok) { toast(data.message || ("respond failed (" + r.status + ")")); return false; }
  toast("✓ sent — " + (f.title || "session") + " is now working");
  // You already hold this Focus — there is nothing to pin. render()'s
  // advance-on-resolve hands it on once it actually flips to working.
  etag = null;
  poll();
  setTimeout(() => { etag = null; poll(); }, 1500);   // catch the busy flip promptly
  return true;
}

// Priority + snooze reorder a view (no Run is driven), so they are not
// token-gated — just the same-origin + JSON POST every mutation uses.
async function postState(path, body, note) {
  try {
    const r = await fetch(path, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    if (!r.ok) { toast("failed (" + r.status + ")"); return; }
  } catch (e) { toast("unreachable"); return; }
  if (note) toast(note);
  etag = null;
  poll();
}

async function sendClear(f) {
  const token = getToken();
  if (!token) { toast("no token — clear cancelled"); return; }
  let r;
  try {
    r = await fetch("api/clear", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({runId: f.runId, token}),
    });
  } catch (e) { toast("clear unreachable"); return; }
  if (r.status === 401) { localStorage.removeItem("cl_token"); toast("token rejected — re-enter"); return; }
  if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.message || "clear failed"); return; }
  toast("box cleared");
  pinned = f.sessionId; etag = null; poll();
  setTimeout(() => { etag = null; poll(); }, 1200);
}

// --- Intake mutations: launch / resume / close ride the same same-origin +
// JSON POST as the rest, but are NOT token-gated (they keep close/launch/resume's
// network-trust-only posture — see ADR 0007). Returns the parsed body with an
// `ok` reflecting the server's own field (present on success and failure).
async function postJSON(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    return Object.assign({ok: r.ok, status: r.status}, data);
  } catch (e) { return {ok: false, message: "unreachable"}; }
}

// The deep-link into the Claude app. The bridge id is server-whitelisted to
// session_<alnum>; re-check here before it ever becomes an href, exactly as the
// old inline page did.
function deepLink(bridge) {
  return (bridge && /^session_[A-Za-z0-9]+$/.test(bridge))
    ? "https://claude.ai/code/" + bridge : "";
}

// Copy the server-built `tmux … attach` line so you can drive a live Run by hand
// in a local terminal — the ❯ local twin of ↗ (ADR 0011). The Clipboard API only
// exists in a secure context: fine at localhost, absent when the Board is reached
// over plain HTTP by hostname (e.g. http://mac-mini) or the Tailscale phone path.
// So fall back to a synchronous execCommand('copy') via an off-screen textarea,
// which still lands the string on a real clipboard on an insecure origin — one
// tap, no prompt() dialog to hand-copy from.
// Pasting the line drops you into a full-screen TUI with no visible way out —
// the one place tmux surfaces to someone who may not know it. The toast is the
// only moment we hold their attention, so it carries the exit key and the
// reassurance that detaching does not end the Run (destroy-unattached kills the
// throwaway view, never the window — ADR 0011).
const ATTACH_HINT = "copied — paste in a terminal; Ctrl-b d to leave, run keeps going";

async function copyAttach(cmd) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(cmd);
      toast(ATTACH_HINT);
      return;
    }
  } catch (_) { /* fall through to the execCommand path */ }
  if (legacyCopy(cmd)) toast(ATTACH_HINT);
  else toast("copy failed");
}

// Insecure-origin clipboard write: select a hidden textarea and execCommand copy.
// Deprecated but works where navigator.clipboard doesn't (non-secure contexts).
function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove();
  return ok;
}

async function closeRun(item) {
  if (!item.runId) { toast("nothing to close"); return; }
  // A mis-tap on a dense list would end a Run; confirm first. Closing ends the
  // Run, never the Session — resume it later — so this is a guard, not a wall.
  if (!window.confirm("Close this run?\n\nThe session stays on disk — resume it later.")) return;
  const res = await postJSON("api/close", {runId: item.runId});
  toast(res.ok ? "closed" : (res.message || "close failed"));
  etag = null; poll();
}

// The ↗ (deep-link) and × (close) that sit on every row and the focus card.
function rowActions(item) {
  const wrap = el("div", "rowact");
  const link = deepLink(item.bridge);
  if (link) {
    const a = el("a", "iconbtn", "↗");
    a.href = link; a.target = "_blank"; a.rel = "noopener";
    a.title = "open in the Claude app";
    a.addEventListener("click", (e) => e.stopPropagation());
    wrap.append(a);
  }
  // ❯ — the local twin of ↗: copy the tmux attach line for a hands-on terminal.
  // Shown wherever the server serves an attach string; copyAttach degrades to a
  // legacy clipboard write on an insecure origin (see copyAttach).
  if (item.attach) {
    const t = el("button", "iconbtn", "❯");
    t.title = "copy tmux attach command";
    t.setAttribute("aria-label", "copy tmux attach command");
    t.addEventListener("click", (e) => { e.stopPropagation(); copyAttach(item.attach); });
    wrap.append(t);
  }
  const x = el("button", "iconbtn x", "×");
  x.title = "close run";
  x.setAttribute("aria-label", "close run");
  x.addEventListener("click", (e) => { e.stopPropagation(); closeRun(item); });
  wrap.append(x);
  return wrap;
}

// --- Intake: the sheet's actions + the optimistic launch card ---------------
// A launched Run is invisible until `claude` reaches `ps` (1-3s). The launch
// hands back a runId; key an optimistic "starting…" card by it, burst-poll, and
// reconcile it away when the real Run surfaces (ADR 0003 invariant 4, adapted
// from the old flat list to the Board's lanes). A Dispatch returns no runId, so
// it paints no card — the toast is the whole feedback (ADR 0004).
const pendingRuns = new Map();   // runId -> {label}
const START_DEADLINE = 10000;

function watch(runId, label) {
  if (!runId) return;
  pendingRuns.set(runId, {label});
  renderPending();
  setTimeout(() => {
    if (pendingRuns.delete(runId)) { renderPending(); toast("run failed to start"); }
  }, START_DEADLINE);
  etag = null; schedule();   // burst until it materialises
}

function renderPending() {
  pendingEl.textContent = "";
  for (const {label} of pendingRuns.values()) {
    const c = el("div", "startcard");
    c.append(el("span", "spin"));
    c.append(el("span", null, "starting… " + (label || "")));
    pendingEl.append(c);
  }
}

function reconcile(data) {
  if (!pendingRuns.size) return;
  const live = new Set();
  for (const it of [data.focus].concat(data.upnext || [], data.watching || [],
                    data.snoozed || [], data.dormant || [])) {
    if (it && it.runId) live.add(it.runId);
  }
  let changed = false;
  for (const id of [...pendingRuns.keys()]) {
    if (live.has(id)) { pendingRuns.delete(id); changed = true; }
  }
  if (changed) renderPending();
}

// The launch input's quick-pick: the folders you've recently run Claude in
// (server-derived; ADR 0006). A native <datalist> is unreliable here — it
// won't open when its options arrive after focus (our fetch is async) and iOS
// Safari barely renders it — so we drive our own dropup popover with the same
// combobox semantics: free-text stays, we just show suggestions and fill on
// tap. Recent dirs change as you work, so we refetch on each focus.
// It is a DROPUP, and it still is inside the sheet: board.html puts it above the
// launch row in document order, which is where the list has always opened.
let dirCache = [];

function hidePop() {
  dirpopEl.hidden = true;
  dirEl.setAttribute("aria-expanded", "false");
}

function renderPop(filter) {
  const f = (filter || "").trim().toLowerCase();
  const items = dirCache.filter((d) => d.toLowerCase().includes(f));
  dirpopEl.textContent = "";
  if (!items.length) { hidePop(); return; }
  for (const d of items) {
    const row = el("button", "diritem", d);
    row.type = "button";
    row.setAttribute("role", "option");
    // pointerdown fires before the input's blur; preventDefault keeps the input
    // focused so the tap fills the value instead of racing the blur-hide.
    row.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      dirEl.value = d;
      hidePop();
    });
    dirpopEl.append(row);
  }
  dirpopEl.hidden = false;
  dirEl.setAttribute("aria-expanded", "true");
}

async function loadDirs() {
  renderPop(dirEl.value);              // paint the cache at once if we have one
  let data;
  try { data = await (await fetch("api/dirs")).json(); }
  catch (e) { return; }
  dirCache = data.dirs || [];
  if (document.activeElement === dirEl) renderPop(dirEl.value);
}

async function launchDir() {
  const dir = dirEl.value.trim();
  const res = await postJSON("api/launch", {dir});
  toast(res.message || (res.ok ? "launched" : "launch failed"));
  // Acting is a dismissal — the sheet was only ever open to reach this (ADR 0015).
  if (res.ok) { dirEl.value = ""; setIntake(false); watch(res.runId, dir || "default"); }
  hidePop();
}

async function resumeSession() {
  const sessionId = sidEl.value.trim();
  const res = await postJSON("api/resume", {sessionId});
  toast(res.message || (res.ok ? "resumed" : "resume failed"));
  if (res.ok) { sidEl.value = ""; setIntake(false); watch(res.runId, "resume"); }
}

async function launchTask(btn) {
  const t = btn._task;
  const body = {task: t.id};
  if (t.seedEl) body.input = t.seedEl.value.trim();
  const res = await postJSON("api/launch", body);
  toast(res.message || (res.ok ? "launched" : "launch failed"));
  if (res.ok) { if (t.seedEl) t.seedEl.value = ""; setIntake(false); watch(res.runId, t.label); }
}

// Task defs are data now (ADR 0008): fetch and build the buttons client-side,
// textContent only. Re-fetched on load and on visibility regain to catch a
// tasks.py edit; ETag-revalidated so an unchanged file is a 304.
let tasksEtag = null;
async function loadTasks() {
  let data;
  try {
    const r = await fetch("api/tasks", tasksEtag ? {headers: {"If-None-Match": tasksEtag}} : {});
    if (r.status === 304) return;
    tasksEtag = r.headers.get("ETag");
    data = await r.json();
  } catch (e) { return; }
  if (data.root) dirRootEl.textContent = data.root.replace(/\/?$/, "/");
  const groups = data.tasks || [];
  tasksEl.textContent = "";
  taskslblEl.hidden = groups.length === 0;
  for (const g of groups) {
    const box = el("div", "taskgroup");
    let seedEl = null;
    if (g.input === "textarea") {
      seedEl = el("textarea", "tseed"); seedEl.rows = 2; seedEl.placeholder = g.placeholder;
      box.append(seedEl);
    } else if (g.input === "text") {
      seedEl = el("input", "tseed"); seedEl.placeholder = g.placeholder;
      box.append(seedEl);
    }
    const btnrow = el("div", "tbtns");
    for (const b of g.buttons) {
      const btn = el("button", "dgo", b.label);
      btn._task = {id: b.id, label: b.label, seedEl};
      btn.addEventListener("click", () => launchTask(btn));
      btnrow.append(btn);
    }
    box.append(btnrow);
    tasksEl.append(box);
  }
}

// --- Recover: a row in the Intake sheet + the picker (slice 04, ADR 0015) ----
// A discovery-and-bulk Resume (CONTEXT.md: Recover). GET /api/recoverable lists
// the Resumable Sessions newest-first and flags the recovery set (`preselect`);
// its `preselectCount` rides the Recover ROW as a nudge — and on the empty
// post-reboot Board that row is inline in the page flow, which is the case the old
// bottom-edge pill existed for. No auto-open, no auto-resume (ADR 0013, consent
// ethos): the count is a nudge, not an action. You tap → the picker opens
// pre-ticked → you confirm.
//
// THE ONE BEHAVIOURAL CONTRAST WITH resumeSession(): a recovered Run is NEVER
// watch()'d into Focus. Recover fans out a batch; they join the queue as a count
// and you pick what to open (Rotation — new work never steals the Focus). Only
// single paste-resume, which names one Session deliberately, focuses its Run.
let recoverEtag = null;
let recoverSessions = [];   // last-fetched Resumable-Session rows (newest-first)
let recoverPreselect = 0;   // size of the recovery set — the badge count
let recoverOpen = false;
let pickerRows = [];        // [{session, cb}] — live, to read the ticked set

async function loadRecoverable() {
  let data;
  try {
    const r = await fetch("api/recoverable", recoverEtag ? {headers: {"If-None-Match": recoverEtag}} : {});
    if (r.status === 304) return;   // unchanged since last look — badge stands
    recoverEtag = r.headers.get("ETag");
    data = await r.json();
  } catch (e) { return; }
  recoverSessions = data.sessions || [];
  recoverPreselect = data.preselectCount || 0;
  renderRecoverRow();
  if (recoverOpen) renderRecoverList();   // keep an open picker current
}

// The row is present whenever anything is resumable; it carries the recovery-set
// count when that set is non-empty (`recover · N`), else reads plain `recover` and
// opens the full list (spec: uncounted-but-present). The TOTAL resumable count
// rides the other end of the row, because the two numbers answer different
// questions: N is the Launcher's guess at a restart, the total is everything the
// picker will list.
//
// THE ＋ CARRIES NO BADGE, and that is the decision, not an omission (ADR 0015).
// After a machine restart no Run survives, so the Board *is* empty, Intake is
// inline where the card would be, and this count is already loud in exactly the
// case the old bottom-edge pill was built for. Any OTHER time the recovery set is
// non-empty it is the mtime heuristic (ADR 0013) finding a cluster that was not a
// restart — and a badge there would put a false positive back on the one strip you
// cannot scroll away from, which is the whole thing this slice deleted.
//
// It no longer measures anything either: the pill used to grow the dock the
// composer stood on, so appearing or going meant re-publishing `--dockh`. There is
// no dock, so this row costs the bottom edge nothing.
function renderRecoverRow() {
  const n = recoverSessions.length;
  recoverBtnEl.hidden = n === 0;
  if (n === 0) { if (recoverOpen) closeRecover(); return; }
  recoverBtnEl.textContent = "";
  recoverBtnEl.append(el("span", null,
    recoverPreselect > 0 ? ("recover · " + recoverPreselect) : "recover"));
  recoverBtnEl.append(el("span", "n", n + " resumable"));
}

function openRecover() {
  // Recover is reached from inside the Intake sheet, and the picker is a modal of
  // its own — so the sheet goes, here rather than on the row's click, because
  // this is the one door into the picker (ADR 0015: acting dismisses the sheet).
  setIntake(false);
  recoverOpen = true;
  recovPanelEl.hidden = false;
  renderRecoverList();
  loadRecoverable();   // the badge's snapshot may be stale — refresh on open
}

function closeRecover() {
  recoverOpen = false;
  recovPanelEl.hidden = true;
}

// Row: dir · title · relative-last-active (spec) — title on top, `dir · age`
// below, so the mtime that explains the pre-tick is always visible. `preselect`
// rows come pre-ticked; the rest tickable. All fields land as textContent.
function renderRecoverList() {
  recovTitleEl.textContent = recoverPreselect > 0
    ? ("recover · " + recoverPreselect + " live at the restart") : "recover";
  recovSubEl.textContent = recoverPreselect > 0
    ? "ticked are the Launcher's guess at what was live — a heuristic, edit freely."
    : "pick the sessions to bring back.";
  recovListEl.textContent = "";
  pickerRows = [];
  if (!recoverSessions.length) {
    recovListEl.append(el("div", "recovempty", "nothing resumable right now."));
    updateRecoverGo();
    return;
  }
  for (const s of recoverSessions) {
    const row = el("label", "recovrow");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = s.preselect === true;
    cb.addEventListener("change", updateRecoverGo);
    const mid = el("div", "recovmid");
    mid.append(el("div", "recovtitle2", s.title || "claude"));
    // The prompt stays the title and the path stays beneath it — this row is
    // picked by "which conversation was that", and the opening prompt answers it
    // better than a repo name does. What changed is the truncation: head-first
    // ellipsis on `~/projects/.worktrees/claude-launcher-scrollback-fold` kept
    // `~/projects/.workt…` — every character of it shared with every other row —
    // and dropped the only part that told them apart. By segment instead.
    const rd = el("div", "recovdir");
    mid.append(rd);
    nextFrame(() => elidePath(rd, s.dir || ""));
    const when = s.mtime ? age(s.mtime * 1000) + " ago" : "";   // mtime is epoch SECONDS
    row.append(cb, mid, el("div", "recovtime", when));
    recovListEl.append(row);
    pickerRows.push({session: s, cb});
  }
  updateRecoverGo();
}

// The Resume action's N tracks the ticked count live.
function updateRecoverGo() {
  const n = pickerRows.filter((r) => r.cb.checked).length;
  recovGoEl.textContent = "resume " + n;
  recovGoEl.disabled = n === 0;
}

// POST the ticked sessionIds; summarise the per-member result array; close;
// refresh. /api/recover returns a TOP-LEVEL ARRAY (not the {ok,…} object
// postJSON wraps), so it gets its own fetch. No watch()/Focus of any returned
// runId — see the note atop this section.
async function doRecover() {
  const ids = pickerRows.filter((r) => r.cb.checked).map((r) => r.session.sessionId);
  if (!ids.length) { toast("nothing ticked"); return; }
  recovGoEl.disabled = true;
  recovGoEl.textContent = "resuming…";
  let results;
  try {
    const r = await fetch("api/recover", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({sessionIds: ids}),
    });
    results = await r.json().catch(() => null);
    if (!r.ok || !Array.isArray(results)) {
      toast("recover failed (" + r.status + ")");
      updateRecoverGo();
      return;
    }
  } catch (e) {
    toast("recover unreachable");
    updateRecoverGo();
    return;
  }
  const okd = results.filter((x) => x && x.ok);
  const bad = results.filter((x) => !x || !x.ok);
  let msg = okd.length + " resumed";
  if (bad.length) msg += ", " + bad.length + " skipped: " + ((bad[0] && bad[0].message) || "");
  toast(msg);
  closeRecover();
  // Recovered Runs surface on the next poll and JOIN THE QUEUE — no watch(), so
  // no optimistic card and, deliberately, no Focus grab (Rotation). Burst a few
  // polls to catch them reaching `ps` (1-3s), same gap a launch leaves.
  loadRecoverable();   // the picker's list just shrank
  etag = null; poll();
  setTimeout(() => { etag = null; poll(); }, 1500);
  setTimeout(() => { etag = null; poll(); }, 3500);
}

// --- The **Scrollback**'s three entries (ADR 0014, reshaped by ADR 0016) ------
//
// ADR 0006's innerHTML exception lives in this section and nowhere else in this
// file. Only an entry's `html` is ever assigned: it is markdown the server
// already rendered escape-first (`_md_to_html`), so a `<script>` in a transcript
// arrives as text, never as an element. ADR 0014 widened that exception from one
// field to N. Everything else here — a tool's name, its detail, a slash command
// — is untrusted transcript text and goes through el() → textContent, per
// ADR 0003. A **work run**'s `calls` are NOT html and must never become any.

// Which **work runs** the reader has opened, by the identity of the run's first
// call. Module state rather than card state on purpose: the Focus card is
// rebuilt whenever its payload moves — which on a working Run is every poll —
// and a run that re-collapsed under you every four seconds would be unusable.
// (A run past _RUN_CALLS that is STILL growing does shift its own first call and
// so does collapse once. Runs that long are rare enough to accept it.)
let openRuns = new Set();

const runKey = (w) => (w.calls[0] ? w.calls[0].name + " " + w.calls[0].detail : "");

// A call's one-word label for the collapsed summary: the basename if the first
// token is a path, else the token itself — `git`, `npm`, `server.py`. The whole
// detail is what EXPANDING is for; this is the skim.
function callLabel(c) {
  const head = (c.detail || "").split(/\s+/)[0];
  if (!head) return c.name;
  return head.includes("/") ? (head.split("/").filter(Boolean).pop() || head) : head;
}

// `git ×3, npm, server.py` — run-length encoded, because a stretch of work is
// usually the same tool over and over and "git, git, git" says it three times.
function runSummary(w) {
  const out = [];
  w.calls.forEach((c) => {
    const l = callLabel(c);
    const last = out[out.length - 1];
    if (last && last.l === l) last.n++;
    else out.push({l: l, n: 1});
  });
  return out.map((x) => (x.n > 1 ? x.l + " ×" + x.n : x.l)).join(", ");
}

function callLine(c) {
  const row = el("div", "wline");
  row.append(el("span", "wname", c.name));
  row.append(el("span", "wdetail", c.detail || "—"));
  return row;
}

// A **work run**: one contiguous stretch of tool calls, on ONE line until you
// open it. Collapsed it says how many steps and roughly what of; expanded it is
// one line per call, each ellipsised at the column edge. Toggling rewrites this
// box in place — never a re-render, which would take the half-typed reply and
// the scroll position with it.
function fillWork(box, w, key) {
  const open = openRuns.has(key);
  box.textContent = "";
  const line = el("div", "roll");
  line.append(el("span", "rollct", w.n + (w.n === 1 ? " step" : " steps")));
  line.append(el("span", "rollsum", open ? "" : runSummary(w)));
  line.append(el("span", "rollcar", open ? "▴" : "▾"));
  line.onclick = () => {
    if (open) openRuns.delete(key); else openRuns.add(key);
    fillWork(box, w, key);
  };
  box.append(line);
  if (!open) return;
  if (w.n > w.calls.length) {
    box.append(el("div", "wmore", (w.n - w.calls.length) + " earlier steps not kept"));
  }
  w.calls.forEach((c) => box.append(callLine(c)));
}

function workEl(w) {
  const box = el("div", "work");
  fillWork(box, w, runKey(w));
  return box;
}

function proseEl(t, cls) {
  const wrap = el("div", cls);
  const md = el("div", "md");
  md.innerHTML = t.html;   // server-escaped markdown — see ADR 0006 / 0014
  wrap.append(md);
  return wrap;
}

// The slash command you invoked. It stands in for the skill body the server now
// drops (ADR 0016) — without it a bare `/ship` leaves no trace at all and the
// reply below it reads as unmotivated.
function commandEl(t) {
  const d = el("div", "cmd");
  d.append(document.createTextNode("you invoked "));
  d.append(el("b", null, t.cmd));
  return d;
}

// --- The **Fold** (ADR 0017) -------------------------------------------------
//
// The Scrollback is read in **Exchanges** — one turn of yours plus everything
// the Run said and did in reply — folded by distance from now. The Exchange you
// are standing in is the read, full prose, exactly as ADR 0014/0016 render it.
// Every OLDER Exchange is one **Record**: a fixed three-line shape in a 40px
// label gutter, `you` / `work` / `claude`, opening in place to the whole thing.
//
// Grouping and folding are PRESENTATION. The payload is still ADR 0014's bounded
// list of entries; nothing here asks the server for more of them, and nothing
// here fetches. The 5064px this replaces was six screens of a 390×844 phone, and
// the page opened at the top of it — the oldest thing in the window.
//
// The security rule of the section above holds here without an exception: a
// Record's lines are DERIVED from a turn's `html` by rendering it into a
// DETACHED prose node and reading `.textContent`, then re-emitted through el().
// So the one innerHTML sink is still proseEl's and there is still only one.

// Which **Records** the reader has opened, keyed by content for the same reason
// and by the same idiom as `openRuns`: the Focus card is rebuilt whenever its
// payload moves, and the Scrollback is a sliding window, so an index would name
// a different Exchange four seconds later.
let openRecords = new Set();

// And which run-up rows — the things Claude said on the way to now, inside the
// Exchange you are standing in. A third set rather than a third meaning for one:
// a Record is an Exchange and a run-up row is one entry inside one, so they can
// never collide and neither can hide the other's state.
let openInner = new Set();

// The only place a turn's html is touched outside of rendering it for real. The
// node is built detached, read as text, and dropped; `foldVoid` never reaches a
// document, but it is a class so a stray one is visible rather than mystifying.
function foldText(t) {
  if (!t || !t.html) return "";
  return (proseEl(t, "foldvoid").textContent || "").replace(/\s+/g, " ").trim();
}

function clipText(s, max) {
  if (!s) return "";
  return s.length > max ? s.slice(0, max - 1).replace(/\s+\S*$/, "") + "…" : s;
}

// Split on a terminator FOLLOWED BY SPACE, never on any `.`: a path is full of
// full stops and none of them ends a sentence. A colon is not a terminator
// either — "Confirmed from the repo:" is a lead-in and the half worth reading is
// the half after it.
const sentencesOf = (s) => (s || "").split(/(?<=[.!?…])\s+/).filter((x) => x.trim());

function firstSentence(s, max) {
  const ss = sentencesOf(s);
  return clipText(ss.length ? ss[0] : s, max);
}

// The reply's LAST sentence, but only when it put a question to YOU — that is
// what is still open, and in chronological order the next row down is the answer
// to it. Short trailing fragments ("ok?") are not the question the record means.
function trailingQuestion(s) {
  const ss = sentencesOf(s);
  const last = ss.length ? ss[ss.length - 1].trim() : "";
  if (!/\?["'’)\]]?$/.test(last) || last.length < 8) return "";
  return clipText(last, 130);
}

// A Record's `work` line: `callLabel`'s one-word labels folded across the WHOLE
// Exchange rather than inside one **work run**, because across five runs `git`
// would otherwise be named three times and saying it three times is the noise
// this line exists to remove. So the count and the labels come from different
// levels: `n` is the true number of calls, the labels are the distinct artifacts.
function artifactsOf(body) {
  const seen = new Map();
  const order = [];
  body.forEach((t) => {
    if (t.role !== "work") return;
    (t.calls || []).forEach((c) => {
      const l = callLabel(c);
      if (seen.has(l)) { seen.set(l, seen.get(l) + 1); return; }
      seen.set(l, 1);
      order.push(l);
    });
  });
  const shown = order.slice(0, 5).map((l) => (seen.get(l) > 1 ? l + " ×" + seen.get(l) : l));
  if (order.length > 5) shown.push("+" + (order.length - 5));
  return shown.join(", ");
}

const stepsOf = (body) => body.reduce(
  (n, t) => n + (t.role === "work" ? (t.n || (t.calls || []).length) : 0), 0);

// An **Exchange** opens on something YOU did — a turn or a slash command — which
// is exactly the boundary ADR 0016 already uses to break a `claude` block, so
// the grouping introduces no new judgement and no new payload field. Entries
// before the first of those are the tail of an Exchange whose prompt has slid
// out of the window: a real Exchange with no prompt, labelled as such rather
// than hidden.
function exchangesOf(entries) {
  const out = [];
  let cur = null;
  entries.forEach((t) => {
    if (t.role === "user" || t.role === "command") {
      cur = {head: t, body: []};
      out.push(cur);
      return;
    }
    if (!cur) { cur = {head: null, body: []}; out.push(cur); }
    cur.body.push(t);
  });
  return out;
}

// Identity across polls: content only, so a rebuild — or a window that has slid
// by an entry — finds the same key and leaves the Record open.
function recordKey(ex) {
  const h = ex.head
    ? (ex.head.role === "command" ? (ex.head.cmd || "") : foldText(ex.head).slice(0, 90))
    : "«earlier»";
  const b = ex.body[0];
  const tail = !b ? ""
    : b.role === "work" ? runKey(b) : foldText(b).slice(0, 50);
  return h + " ⋮ " + tail;
}

// The same identity, one level down: a single entry inside the Exchange you are
// standing in. Content only, for the same reason recordKey is.
function entryKey(t) {
  return t.role === "work" ? "w:" + runKey(t) : "a:" + foldText(t).slice(0, 70);
}

// The three lines. None of them is a summary of the Exchange — each answers a
// different question the reader actually came back with, which is what a
// one-sentence gist could not do.
function recordFields(ex) {
  const cmd = !!ex.head && ex.head.role === "command";
  // `cmd` already carries its slash (server.py::_CMD_RE) and is untrusted
  // transcript text, so it goes to the line as-is and through textContent.
  const ask = ex.head ? (cmd ? (ex.head.cmd || "") : foldText(ex.head)) : "";
  const replies = ex.body.filter((t) => t.role === "assistant");
  const said = replies.length ? foldText(replies[replies.length - 1]) : "";
  const q = trailingQuestion(said);
  // A short prompt of PROSE — "yes", "do it", "a" — is an ANSWER and not a
  // subject, so it is quoted rather than set as a title, and an opened Record
  // does not then repeat it in a bubble 40px lower. A slash command is short for
  // a different reason: `/ship` NAMES the thing you asked for, so it is a title
  // however few characters it is, and its own row still has to stand in for the
  // skill body the server drops (ADR 0016).
  const grunt = !cmd && ask.length > 0 && ask.length < 16;
  return {
    you: !ask ? "" : cmd ? ask : grunt ? "“" + ask + "”" : clipText(ask, 130),
    grunt: grunt,
    noAsk: !ex.head,
    work: artifactsOf(ex.body),
    steps: stepsOf(ex.body),
    said: q || firstSentence(said, 130),
    openQ: !!q,
    waiting: !ex.body.length,
  };
}

// One helper builds every labelled line, so `you`, `work` and `claude` are the
// same 40px column to the pixel. That column IS the landmark: the eye runs down
// it without reading a value. A line with nothing to say is omitted, never blank
// — three empty labels would make the column noise instead of a landmark.
function fieldLine(grid, label, value, cls) {
  if (!value) return;
  grid.append(el("span", "rl " + cls, label));
  grid.append(el("span", "rv " + cls, value));
}

// An opened Record is the Exchange as prose — you already said which one you
// wanted by tapping it, so it unfolds to the read and not to a second set of
// rows. `skipHead` is for a grunt: an open Record still carries "yes" whole on
// its own `you` line, and repeating it in a bubble 40px lower is the only place
// this layout would ever say the same thing twice.
function recordBody(ex, skipHead) {
  const box = el("div", "rbody");
  if (ex.head && !skipHead) {
    box.append(ex.head.role === "command" ? commandEl(ex.head) : proseEl(ex.head, "turn you"));
  }
  ex.body.forEach((t) => box.append(t.role === "work" ? workEl(t) : proseEl(t, "cm")));
  if (!ex.body.length) box.append(el("div", "rwait", "…nothing back yet"));
  return box;
}

// Toggling rewrites this box in place, never the card — a re-render would take
// the half-typed reply and the reading position with it (fillWork makes the same
// call for the same reason).
function fillRecord(box, ex, key, no) {
  const open = openRecords.has(key);
  const f = recordFields(ex);
  box.textContent = "";
  // Teal when that reply closed by putting a question to YOU, and only while
  // folded: opened, the question is on screen in full a line below.
  box.className = "rec" + (open ? " recopen" : (f.openQ ? " recq" : ""));

  const hd = el("button", "rhd");
  hd.setAttribute("aria-expanded", open ? "true" : "false");
  // The number ascends from the top, and the Exchange you are standing in carries
  // the next one, so the count never runs backwards. Everything on this page
  // points one way: `earlier` is above, `↓ newest` is below, and a Record whose
  // reply put a question to you is answered by the very next row DOWN.
  hd.append(el("span", "rn", String(no)));
  const grid = el("div", "rf");
  fieldLine(grid, "you",
    f.you || (f.noAsk ? "(prompt is off the top of the window)" : ""),
    "ru" + (f.noAsk || f.grunt ? " rdim" : ""));
  if (!open) {
    if (f.work) {
      fieldLine(grid, "work", f.work, "rw");
      if (f.steps) grid.append(el("span", "rgear", "⚙" + f.steps));
    }
    fieldLine(grid, "claude", f.waiting ? "…nothing back yet" : f.said,
      f.openQ ? "rq" : "rs");
  }
  hd.append(grid);
  hd.append(el("span", "rcar", open ? "▴" : "▾"));
  // ANCHORED: opening a Record above the read would otherwise slide the read down
  // by exactly what the Record gained. Measured at 0px of drift (ADR 0017).
  hd.onclick = () => keepAnchored(box, () => {
    if (open) openRecords.delete(key); else openRecords.add(key);
    fillRecord(box, ex, key, no);
  });
  box.append(hd);
  if (open) box.append(recordBody(ex, f.grunt));
}

function recordEl(ex, no) {
  const box = el("div", "rec");
  fillRecord(box, ex, recordKey(ex), no);
  return box;
}

// A run-up row: ONE thing Claude said on the way to now, inside the Exchange you
// are standing in — distance 1 of the **Fold**. Same 40px gutter and the same
// word as a Record above, but one field only: at this distance "what did it
// touch" is already answered by the work rows sitting beside it in full.
function fillInner(box, t, key) {
  const open = openInner.has(key);
  box.textContent = "";
  box.className = "inrow" + (open ? " inopen" : "");
  const hd = el("button", "rhd rhdin");
  hd.setAttribute("aria-expanded", open ? "true" : "false");
  const grid = el("div", "rf");
  fieldLine(grid, "claude",
    open ? "▾" : (firstSentence(foldText(t), 200) || "(no text)"), "rs");
  hd.append(grid);
  hd.append(el("span", "rcar", open ? "▴" : "▾"));
  hd.onclick = () => keepAnchored(box, () => {
    if (open) openInner.delete(key); else openInner.add(key);
    fillInner(box, t, key);
  });
  box.append(hd);
  if (open) box.append(proseEl(t, "cm inbody"));
}

function innerEl(t) {
  const box = el("div", "inrow");
  fillInner(box, t, entryKey(t));
  return box;
}

// A **work run** in the run-up keeps ADR 0016's own one-line collapsible AND its
// own open state (openRuns) and simply moves in under the same gutter — so there
// is one mechanism and not a second, and `work` means here exactly what it means
// on a Record above.
function workRow(t) {
  const box = el("div", "wkrow");
  box.append(el("span", "rl rw", "work"));
  box.append(workEl(t));
  return box;
}

// --- The **Seam**, and the landing (ADR 0017) --------------------------------
//
// The `NEWEST` rule cuts the **Fold** from the live prose, and the page lands on
// it: parked 250px down, not hard against the sticky header, so the tail of the
// run-up peeks above. That peek is load-bearing — it is how a reader learns there
// IS a Fold and that it is skimmable. Measured at exactly 250px on all four
// fixture Sessions, with 535-575px of newest prose below it.
const SEAM_PEEK = 250;   // px down the viewport the seam parks at
const HEAD_PAD = 52;     // the sticky .fhead plus a hair — where a scroll parks
                         // a node when there is no fold to peek at
const PARKED = 40;       // px of drift still counted as "where the landing left
                         // you", i.e. still reading the end
const FLOOR_GAP = 10;    // px of air between the **Ask** and the composer's top

let liveSeam = null;     // the seam of the card now on screen. A frame scheduled
                         // by a card a poll has since replaced must not scroll to
                         // a node that is no longer in the document.
let landedSig = null;    // the scrollback we have already landed on
let landedY = 0;         // and where that landing left the page
// The reader has opened something in the **Fold**, so the landing stops chasing
// the live end. This is not the same test as the drift below, and it has to be
// its own flag BECAUSE the unfold is anchored: keepAnchored's whole job is to
// move the page 0px, so a reader who deliberately opened a Record two screens up
// is still, by `landedY`, exactly where the landing left them — and the next
// poll's entry would fire them straight back down onto the seam. The anchor's
// success was defeating the parked test. Measured: open a Record, wait one 4s
// poll, and the page jumped 984px (ADR 0017: "scroll up into history with a
// Record half-read and you keep your place").
let landHeld = false;

// A Focus you have just swiped to is a read you have travelled nowhere in, so it
// lands unconditionally. Called from renderFocus beside the open sets, which is
// the one place that knows the Session changed.
function resetLanding() { landedSig = null; landedY = 0; landHeld = false; }

const entriesSig = (entries) => entries.map(
  (t) => t.role + (t.html ? t.html.length : "") + (t.n || "") + (t.cmd || "")).join("|");

// Unfolding anything above the read slides the read down by exactly what it
// gained. Anchor on the node's own top: for a row you can see that is a no-op —
// all the growth is below it — and for one that has drifted off the top edge it
// holds the page still. Measured at 0px of drift (ADR 0017).
function keepAnchored(node, mutate) {
  // Every route into the Fold comes through here — a Record, a run-up row,
  // `read all` — and every one of them is the reader leaving the live end. So
  // this is where the landing is told to stop chasing it; `↓ newest` is the one
  // move that gives it back (goNewest).
  landHeld = true;
  const box = node.getBoundingClientRect ? node : null;
  const before = box ? box.getBoundingClientRect().top : 0;
  mutate();
  const after = box ? box.getBoundingClientRect().top : 0;
  if (after !== before && window.scrollBy) window.scrollBy(0, after - before);
}

function scrollToNode(node, pad) {
  if (!node.getBoundingClientRect) return;
  const y = (window.scrollY || 0) + node.getBoundingClientRect().top - pad;
  window.scrollTo(0, Math.max(0, y));
}

// The landing that is actually on screen, so `↓ newest` can replay it rather
// than re-derive it — "exactly where you landed" has to include the pad the
// landing chose and the floor it cleared, or on a **Blocked** Focus the button
// would put the reader somewhere the landing never did.
let liveLanding = null;

// `↓ newest`: back to exactly where the landing put you, and the one thing that
// re-arms it. Asking for the live end is saying you are done in history, so the
// next entry may follow you down again.
function goNewest(seam) {
  const l = (liveLanding && liveLanding.seam === seam) ? liveLanding : null;
  scrollToNode(seam, l ? l.pad : SEAM_PEEK);
  if (l) clearFloor(seam, l.top, l.bot);
  landHeld = false;
  landedY = Math.max(0, window.scrollY || 0);
}

// A **Blocked** Focus's **Ask** — and the pending-input warning under it — render
// BELOW the **Scrollback** (focusCard), so a landing that parks the seam 250px
// down and stops leaves the blocker off screen: measured at 390×844, the seam at
// 250 and 535px of live prose put the Ask's top at 778 behind a composer whose
// top was 744. A Blocked Run is the case the whole Board exists for, so the
// landing takes a FLOOR: park the seam, then scroll on until the Ask clears the
// composer.
//
// THE OLD REASONING FOR WHAT THE FLOOR CLEARS IS DEAD (ADR 0020). It said an
// over-tall Ask could run under the bar because "the options are already up" —
// they rode the sticky `.respond`, so only the question was ever at risk. The
// options are now IN the Ask block, each with its description, so an Ask that
// runs under the bar takes every answer with it: the page would be showing a
// question and no way to answer it, which is the same failure the floor exists
// to prevent, one element further down.
//
// So the floor's bottom is the FIRST option, not the block's own bottom. Four
// options at ADR 0020's median 175 chars is ~320px of options under the
// question, more than the peek can ever buy — demanding all of them would spend
// the entire peek on every Blocked landing and STILL come up short, which is a
// worse trade than a flick. Through the first option guarantees the header, the
// full question and at least one answer with the reasoning that decides it; the
// rest is one flick, and it is now beneath something you can already read
// rather than behind a bar. (An unsent-text warning still outranks it — you must
// see that before you type over it — and it sits below the options anyway.)
//
// What gives way is the PEEK, and only the peek: the extra is capped at
// `seam − HEAD_PAD`, so the seam never rises past the header and the newest prose
// — the reply the Ask is asking about — is never scrolled off the top to make
// room for it.
function clearFloor(seam, top, bottom) {
  if (!top || !top.getBoundingClientRect) return;
  const bar = focusWrap.querySelector(".respond");
  const vh = window.innerHeight || 0;
  // The composer is not the only thing at this edge: the swipe hint is a fixed,
  // OPAQUE strip standing on `--barh` until the first swipe, so until then it is
  // the real bottom of the read — and it was covering the Ask's second line.
  let floor = bar ? bar.getBoundingClientRect().top : vh;
  if (hintEl && !hintEl.hidden && hintEl.getBoundingClientRect) {
    floor = Math.min(floor, hintEl.getBoundingClientRect().top);
  }
  const limit = floor - FLOOR_GAP;
  const over = (bottom || top).getBoundingClientRect().bottom - limit;
  if (over <= 0) return;
  const room = seam.getBoundingClientRect().top - HEAD_PAD;
  const extra = Math.min(over, room);
  if (extra > 0 && window.scrollBy) window.scrollBy(0, extra);
}

// The landing needs layout to measure against, and it must have the LAST word:
// renderFocus is still building the card when this is called, and restore() puts
// the reader's own scroll back straight after. So it goes in a frame of its own.
function nextFrame(fn) {
  if (window.requestAnimationFrame) window.requestAnimationFrame(fn);
  else setTimeout(fn, 0);
}

// It fires ONCE PER SCROLLBACK, not once per poll. A new entry re-lands you only
// if you were still parked where the last landing left you; scroll up into
// history with a Record half-read and you keep your place, because an auto-scroll
// that yanks a reader is the baseline's bug wearing the other mask.
//
// `window.scrollY` inside the frame is honest, and that is the one thing neither
// prototype could lean on: they read it mid-rebuild, when the card had been
// emptied and the browser had clamped the page to ~0, and both had to keep a
// deafened copy of it instead. renderFocus grabs the reader's position BEFORE it
// empties anything (grab) and restores it, so by this frame the page is where the
// reader actually left it.
function landOn(seam, sig, pad, floorTop, floorBot) {
  liveLanding = {seam: seam, pad: pad, top: floorTop, bot: floorBot};
  if (sig === landedSig) return;   // this scrollback has had its landing
  const first = landedSig === null;
  landedSig = sig;
  nextFrame(() => {
    if (seam !== liveSeam) return;   // a poll rebuilt the card under this frame
    if (!first && (landHeld || Math.abs((window.scrollY || 0) - landedY) > PARKED)) return;
    scrollToNode(seam, pad);
    clearFloor(seam, floorTop, floorBot);
    landedY = Math.max(0, window.scrollY || 0);
    // A scroll THIS FILE performed is not the reader travelling, so the
    // scroll-driven chrome must not read it as one: showChrome re-anchors it at
    // the live end, which is exactly where the landing just put you. It costs
    // nothing when the chrome is already up.
    showChrome();
  });
}

// The strip above the **Fold**: how much is up there, and the two blunt moves.
// Everything on it points ONE way — `earlier` names what is below it and above
// the seam, `↓ newest` names the direction of now — because the order is
// chronological and down is later (ADR 0017; do not invert it).
function foldStrip(past, metas, rows, seam) {
  const top = el("div", "ftop");
  top.append(el("span", "ftopl", "earlier"));
  top.append(el("span", "ftopn",
    past.length + (past.length === 1 ? " exchange" : " exchanges")));
  top.append(el("span", "grow"));
  const allOpen = () => metas.every((m) => openRecords.has(m.key));
  const bulk = el("button", "fbtn", allOpen() ? "collapse all" : "read all");
  bulk.onclick = () => {
    const shut = allOpen();
    // Anchored on the STRIP the button lives in — the one node the reader is
    // certainly looking at when they press it. History unfolds downward from it
    // and `↓ newest` an inch away is the way back. Anchoring the seam instead
    // would fire them to the bottom of a page that just quadrupled (ADR 0017:
    // 1362px → 4557px).
    keepAnchored(top, () => {
      metas.forEach((m, i) => {
        if (shut) openRecords.delete(m.key); else openRecords.add(m.key);
        fillRecord(rows[i], past[i], m.key, m.no);
      });
    });
    bulk.textContent = shut ? "read all" : "collapse all";
  };
  top.append(bulk);
  const down = el("button", "fbtn fbtngo", "↓ newest");
  down.onclick = () => goNewest(seam);   // exactly where you landed, and re-armed
  top.append(down);
  return top;
}

// The landing is ARMED by scrollbackEl and FIRED by focusCard, once the whole
// card exists. It has to be that way round: on a **Blocked** Focus the landing's
// floor is the **Ask**, and the Ask is a sibling BELOW the Scrollback that has
// not been built yet when the seam is.
let armedLanding = null;

// The Scrollback itself, graded by distance from now (ADR 0017): the strip and
// the **Records** above, the Exchange you are standing in as gutter rows, the
// **Seam**, and below it the live tail as full prose at full width.
//
// ADR 0016's chained `claude` caption is SUPPRESSED here rather than undone: the
// label gutter carries who, once per row, above the seam, and below the seam the
// seam itself does. The chain remains the payload-side grouping rule — the server
// still charges every entry in one against _SCROLLBACK_TURNS — it is just no
// longer the thing the reader sees.
function scrollbackEl(entries) {
  const sb = el("div", "sb");
  armedLanding = null;
  if (!entries.length) {
    liveSeam = null;   // no seam on this card, so a frame a previous one queued
                       // must find nothing to land on rather than a stale node
    sb.append(el("div", "rwait", "(nothing in the transcript tail yet)"));
    return sb;
  }
  const exchanges = exchangesOf(entries);
  // The Exchange you are STANDING IN is the newest one the Run has actually
  // answered in. A prompt you sent a second ago with nothing back yet is not an
  // Exchange to fold the read behind — it is a line under the read, and folding
  // the reply you are still reading the moment you answer it would be the
  // baseline's bug wearing the other mask.
  let ci = exchanges.length - 1;
  while (ci > 0 && !exchanges[ci].body.some((t) => t.role === "assistant")) ci--;
  const past = exchanges.slice(0, ci);
  const cur = exchanges[ci];
  const pending = exchanges.slice(ci + 1);

  // The split INSIDE that Exchange. Below the seam goes the last thing Claude
  // said, plus the **work run** that produced it — already one line, and dropping
  // it would break the causality that last paragraph is reporting — plus anything
  // after. Everything earlier in the Exchange is a run-up row.
  let cut = -1;
  for (let i = cur.body.length - 1; i >= 0; i--) {
    if (cur.body[i].role === "assistant") { cut = i; break; }
  }
  if (cut < 0) cut = cur.body.length;
  while (cut > 0 && cur.body[cut - 1].role === "work") cut--;
  const runup = cur.body.slice(0, cut);
  const tail = cur.body.slice(cut);

  const seam = el("div", "seam");
  liveSeam = seam;

  if (past.length) {
    const metas = past.map((ex, i) => ({key: recordKey(ex), no: i + 1}));
    const rows = past.map((ex, i) => recordEl(ex, i + 1));
    sb.append(foldStrip(past, metas, rows, seam));
    rows.forEach((r) => sb.append(r));
  }

  // Your prompt is never folded: "what did I ask" is the frame for everything
  // under it. It rides the same gutter as the Records above rather than the
  // `.turn you` bubble, so a one-word prompt costs one line and needs no special
  // case — and the column runs unbroken from the top of the fold to the seam.
  const now = el("div", "now");
  const head = el("div", "nhead");
  head.append(el("span", "rn rnow", String(past.length + 1)));
  const hg = el("div", "rf");
  hg.append(el("span", "rl ru", "you"));
  if (cur.head && cur.head.role === "command") hg.append(commandEl(cur.head));
  else if (cur.head) hg.append(proseEl(cur.head, "nask"));
  else hg.append(el("span", "rv ru rdim", "(prompt is off the top of the window)"));
  head.append(hg);
  now.append(head);

  runup.forEach((t) => now.append(t.role === "work" ? workRow(t) : innerEl(t)));

  seam.append(el("span", "seaml", "newest"));
  seam.append(el("span", "seamrule"));
  if (runup.length) {
    const st = stepsOf(runup);
    seam.append(el("span", "seamn",
      runup.length + " above" + (st ? " · ⚙" + st : "")));
  }
  now.append(seam);

  // Below the seam the gutter stops and the read takes the full column. That
  // boundary is the whole design in one line: labelled rows above, prose below.
  const live = el("div", "live");
  tail.forEach((t) => live.append(t.role === "work" ? workEl(t) : proseEl(t, "cm")));
  if (!tail.length) live.append(el("div", "rwait", "…nothing back yet"));
  now.append(live);
  sb.append(now);

  // A prompt with nothing back yet: below the read, where you left it, and it
  // takes the NEXT number — the count never runs backwards.
  pending.forEach((ex, i) => {
    const p = el("div", "pend");
    const g = el("div", "rf rfpend");
    g.append(el("span", "rn rnow", String(past.length + 2 + i)));
    g.append(el("span", "rl ru", "you"));
    g.append(ex.head.role === "command" ? commandEl(ex.head) : proseEl(ex.head, "nask"));
    p.append(g);
    p.append(el("div", "rwait", "…nothing back yet"));
    sb.append(p);
  });

  // Nothing above the seam at all — a first exchange, still being answered —
  // means there is no peek to buy, so the seam goes under the header instead of
  // 250px down and the whole read is on screen. (And no peek means no room for
  // the Ask's floor to spend either; clearFloor caps itself on exactly that.)
  armedLanding = {seam: seam, sig: entriesSig(entries),
                  pad: (past.length || runup.length) ? SEAM_PEEK : HEAD_PAD};
  return sb;
}

// --- The **Ask** block (ADR 0020) ------------------------------------------
// The transcript says WHAT is asked; the pane says WHERE the widget is standing.
// Everything drawn below comes off `focus.askSet`, and every keystroke sent
// comes off the server's `steps` — a SIGNED count already measured against the
// widget's real rows. The client does not recompute it and must not: the
// predecessor of this code did `i - (f.cursor || 0)`, which counted an index
// into the wrong list from a cursor that may never have been read, and `|| 0`
// turned "nobody painted a cursor" into "the cursor is on row 0". That default
// was right often enough to hide four separate defects for months.
//
// `askSet` is `{}` for an approval, an idle Run and a permission menu — none of
// them has question structure to model. Those keep the legacy `ask` / `options`
// / `cursor` triple, and the cursor rule above holds there too: `null` means
// unread, which means untappable.

// How far the widget is from where a tap wants it, as keystrokes. `steps` is
// signed and already measured; all this does is spell it.
//   single-select: step, then Enter — one tap is the answer.
//   multiSelect:   step, then Space — one tap is ONE TOGGLE. Submitting is a
//                  separate control, because the widget submits the whole
//                  question at once and a tick is not an answer.
function stepKeys(steps, multi) {
  const n = Math.abs(steps || 0);
  return Array(n).fill(steps >= 0 ? "down" : "up").concat(multi ? "space" : "enter");
}

// Every way the phone can refuse to tap, in the words of what it means to the
// person holding it. The server names five and they do NOT mean the same thing:
// two of them are the ordinary, benign race (the Ask was answered at the desk
// between the transcript read and the pane capture) and read as "look again";
// three of them are the transcript and the screen actually disagreeing, and read
// as "do not trust this until you have looked at the terminal". A refusal you
// cannot tell apart from a different refusal is half a silent failure.
const ASK_WHY = {
  "no-pane": "nothing read this Run's screen this time round, so where the " +
    "widget is standing is unknown. The next poll usually settles it.",
  "no-widget": "the screen shows no question at all, while the transcript still " +
    "has one pending — most likely it was just answered at the terminal.",
  "unmatched": "the question on screen is not the one the transcript sent, so " +
    "which Ask you are looking at is unknown. Its answers are withheld rather " +
    "than drawn under the wrong question.",
  "pane-mismatch": "the rows on screen do not account for these options — the " +
    "widget may have been re-rendered. A keystroke count here would be a guess.",
  "no-cursor": "the screen paints no cursor, so there is nothing to count " +
    "keystrokes from.",
};
// WHAT THE BOX BELOW ACTUALLY DOES, which is not what this line used to claim.
// Three of the five refusals above happen WHILE the widget is still on screen,
// and "send prose in the box below" was then an invitation to the destructive
// path: with no cursor to step from there is nothing to count keystrokes from,
// so free text can only reach that Run by pressing Esc first — which cancels the
// question. The server decides which route is available and the composer is
// labelled for it (`textRoute`), so this points at that label rather than
// promising an outcome it does not control.
const ASK_WHY_TAIL = " Answer at the terminal, or use the box below — it says " +
  "there whether prose can answer this question or would cancel it.";

// The Ask block: which Ask of the Set, its header, the full question, and the
// options with the descriptions that decide them.
// Returns `{box, floor}` — `floor` is the FIRST option, which is what the
// landing must clear (see clearFloor).
function askEl(f) {
  const set = (f.askSet && f.askSet.count) ? f.askSet : null;
  const box = el("div", "ask");
  const strip = el("div", "askhd");
  strip.append(el("span", "lbl", "the ask"));
  // THE WORKSPACE, HERE TOO, AND IT IS NOT A DUPLICATE OF THE HEADER'S (ADR
  // 0023). `.fhead` is sticky, so it is on screen whenever the card is — and
  // syncChrome condenses it to the Workspace alone while you read, which is
  // exactly the state you are in when an approval lands at the bottom of a long
  // **Scrollback**. That the header keeps the name is why the two stamps agree
  // rather than one covering for the other; this one is the lane-coloured one,
  // in the block you are answering. Approving a tool call is the one
  // irreversible thing on the Board; it must not be possible to do it with
  // nothing on screen saying which project it lands in.
  if (f.workspace) strip.append(el("span", "askws", f.workspace));
  // "ask 1 of 2" ONLY when there is a 2. 326 of 425 Asks on disk are a Set of
  // one (ADR 0020's census), so a permanent "ask 1 of 1" is a line of noise on
  // three quarters of them — and `index` is -1 when nothing matched, which is
  // not a position and must not be printed as one.
  if (set && set.count > 1 && set.index >= 0) {
    strip.append(el("span", "askn", "ask " + (set.index + 1) + " of " + set.count));
  }
  if (set && set.header) strip.append(el("span", "askhdr", set.header));
  box.append(strip);
  // The FULL question. `f.ask` is clipped to 200 chars for the queue's
  // one-liner; `askSet.question` is not, and the tail of a question is where the
  // actual choice usually is. Falls back to `f.ask` for an approval, and for an
  // `unmatched` Set — where the server has already put the pane's own rendered
  // question there, since that is the one thing certainly on screen.
  box.append(el("div", "qtext", (set && set.question) || f.ask || ""));

  const multi = !!(set && set.multiSelect);
  let opts, tappable, why;
  if (set) {
    opts = set.options || [];
    tappable = !!set.tappable;
    why = set.fallback || "";
  } else {
    // A permission menu: no Ask Set, a flat list, and a cursor that indexes the
    // options directly because the menu's rows ARE its options. Unread cursor,
    // no tap — the same rule, not a special case.
    const cur = typeof f.cursor === "number" ? f.cursor : null;
    opts = (f.options || []).map((l, i) => ({
      label: l, description: "", checked: null,
      steps: cur === null ? null : i - cur}));
    tappable = cur !== null && opts.length > 0;
    why = tappable || !opts.length ? "" : "no-cursor";
  }

  if (!tappable && why) {
    const w = el("div", "askwhy");
    w.append(el("span", "wlbl", "read-only — "));
    w.append(document.createTextNode(ASK_WHY[why] + ASK_WHY_TAIL));
    box.append(w);
  }

  let floor = null;
  if (opts.length) {
    // ONE tap target per option, carrying the label AND the description. The
    // description is *why* the option is there — median 175 chars, p90 285 — and
    // splitting the read from the tap was explicitly rejected: you would be
    // reading one thing and pressing another.
    const list = el("div", "opts");
    opts.forEach((o) => {
      // A div, not a disabled button, when there is nothing to send: a
      // read-only Ask must still READ, and a greyed-out button says "wait" when
      // the honest word is "not from here".
      const b = el(tappable ? "button" : "div",
                   "opt" + (tappable ? "" : " ro") + (o.checked ? " on" : ""));
      const row = el("div", "orow");
      if (multi) {
        // Rendered from the PAYLOAD's `checked`, never from local memory: this
        // card is rebuilt every poll and the pane is the truth. A remembered
        // tick would survive a toggle made at the terminal and lie about it.
        //
        // THREE STATES, not two. `null` is the pane failing to read the box —
        // on every fallback it is what every option carries — and drawing that
        // as ☐ would be this whole slice's bug in miniature: an unread value
        // rendered as a confident one, which is `cursor || 0` again.
        const unread = o.checked === null || o.checked === undefined;
        row.append(el("span", "obox" + (unread ? " unread" : ""),
                      unread ? "?" : o.checked ? "☑" : "☐"));
      }
      row.append(el("span", "olbl", o.label));
      b.append(row);
      if (o.description) b.append(el("div", "odesc", o.description));
      if (tappable) {
        // Only ever a real reading here: `tappable` means the pane read the box.
        if (multi) b.setAttribute("aria-pressed", o.checked ? "true" : "false");
        b.addEventListener("click", () => sendRespond(f, {keys: stepKeys(o.steps, multi)}));
      }
      list.append(b);
      if (!floor) floor = b;
    });
    if (tappable && multi) {
      // The submit, and the only Enter on a multiSelect Ask. Ticking is not
      // answering: the widget holds every toggle until this.
      const done = el("button", "opt done", "submit these ticks →");
      done.addEventListener("click", () => sendRespond(f, {keys: ["enter"]}));
      list.append(done);
    }
    box.append(list);
  }
  return {box: box, floor: floor};
}

function focusCard(f) {
  const cls = f.lane === "question" ? "focus bq" : f.lane === "approval" ? "focus bp" : "focus";
  const card = el("div", cls);

  // ROW ONE IS THE **WORKSPACE**, ALONE (ADR 0023). Everything else in this
  // header wears `flex:0 0 auto`; `.fdir` was the only item that could shrink,
  // so it absorbed every shortfall and `claude-launcher` arrived on a phone as
  // `claude-lau…`. The previous fix deleted a different item's word and left
  // that inversion standing, which is why it came back. The name now takes the
  // whole first row and the chrome wraps beneath it — two rows at EVERY width
  // and lane, never a height that depends on how long the name is, because a
  // header that grows with its content reflows the sticky strip the
  // **Scrollback** scrolls under. It does have a second height, deliberately:
  // condensed, it is row one alone (board.html: `.fhead.hid`). That one is
  // driven by the scroll rather than by the data, it is the same for every Run,
  // and it is what the flicker guards in syncChrome pay for.
  //
  // Row one is a node of its own because it holds TWO things now: the session
  // title used to be a tinted band under the header (`.about`) and trails the
  // Workspace here instead, so the resting header is one band rather than two
  // and the condensed state drops a real row. `.fdir` keeps its own contract —
  // it is handed the Workspace and nothing else, which is what lets
  // elideWorkspace measure it — and `.fabout` is the item that gives width back
  // first (board.html: .frow1).
  const head = el("div", "fhead");
  const row1 = el("div", "frow1");
  const dir = el("span", "fdir");
  row1.append(dir);
  if (f.aiTitle) row1.append(el("span", "fabout", f.aiTitle));
  head.append(row1);
  head.append(el("span", "fbadge", LANE_LABEL[f.lane] || f.lane));
  head.append(el("span", "grow"));
  // Split, because this strip is now four things wide on a 390px phone and the
  // sessionId is the one nobody reads there — CSS drops it under 560px rather
  // than let the title and the badge wrap to two lines each.
  head.append(el("span", "fsid", (f.sessionId || "").slice(0, 8)));
  // THE AGE ONLY. `LANE_NOUN` used to lead it, and on every lane it said in one
  // word what `.fbadge` two inches left had already said in two — `waiting`
  // beside BLOCKED · QUESTION, `working` beside WORKING. Measured at 390×844:
  // that noun cost 55-63px in a strip that had none, and on the two **Blocked**
  // lanes — whose badge is the widest of the five — the row overflowed to 426px.
  // A page wider than the phone is not a wrap: `width=device-width` shrink-to-fits
  // the WHOLE read to 91%, which is every measurement in ADR 0017 quietly wrong,
  // and it does it on exactly the Runs the Board exists for.
  head.append(el("span", "fmeta", age(f.updatedAt)));
  // Measured, not guessed — and after layout, so `.fdir` has a real width to be
  // compared against. A Run with no cwd has no Workspace and gets `—`: the pane
  // title that used to fall in here read as a repo name without being one.
  nextFrame(() => elideWorkspace(dir, f.workspace));
  // The queue's way in on a phone. It lives HERE, in the one strip that stays on
  // screen while you read, because the queue is a sheet at this width rather
  // than a stack under an unbounded **Scrollback** (board.html: .zones). CSS
  // hides it at >=900px, where the rail is already showing the same list.
  const qn = queueCount();
  if (qn) {
    // Accented only when something in there actually needs you — the count is
    // the Board's whole triage signal while the queue itself is out of sight.
    const urgent = ((boardData || {}).counts || {}).needYou > 1;
    const qb = el("button", "zbtn" + (urgent ? " hot" : ""), qn + " queued ▾");
    qb.setAttribute("aria-haspopup", "dialog");
    qb.addEventListener("click", () => setQueueOpen(true));
    head.append(qb);
  }
  // **Intake**'s only way in (ADR 0015). A Board-level verb in a Run-level strip,
  // deliberately: this is the one bar that is always on screen while you read, the
  // queue count above already set that precedent, and the alternative was the
  // permanent bottom bar this slice deleted. Intake is not a property of the
  // Focus — it just borrows the strip.
  // LAST in the header, and at EVERY width: unlike `.zbtn`, which CSS drops at
  // >=900px because the rail draws the queue there, nothing draws Intake in the
  // rail, so there is no duplicate to avoid. It carries no Recover count — see
  // renderRecoverRow for why that is a decision.
  const plus = el("button", "iplus" + (intakeSheetOpen ? " hot" : ""), "＋");
  plus.setAttribute("aria-label", "intake — start something new");
  plus.setAttribute("aria-haspopup", "dialog");
  plus.setAttribute("aria-expanded", intakeSheetOpen ? "true" : "false");
  plus.addEventListener("click", () => setIntake(!intakeSheetOpen));
  head.append(plus);
  card.append(head);

  // The **Scrollback**: the Session's recent entries, oldest first, in place of
  // the single last assistant message (ADR 0014). What you said, what it did and
  // what it then said — the run-up you need in order to answer. It has NO scroll
  // box of its own; it flows into the page scroll (see `.sb` in board.html).
  card.append(scrollbackEl(f.scrollback || []));
  const land = armedLanding;   // captured before the Ask below can re-enter here
  // `floorBot` is the deepest thing the landing must put on screen. Since the
  // options moved into the card (ADR 0020) that is no longer the Ask box's own
  // bottom — see clearFloor for why it is the FIRST option and not the last.
  let askBox = null, warnBox = null, floorBot = null;

  // An **Ask** is the blocker of a **Blocked** Run and of nothing else
  // (CONTEXT.md). An idle Run's closing question is now visibly the last turn
  // above, so the old "(no explicit question — your move)" placeholder was ~62px
  // of chrome saying nothing; the server sends `ask: ""` off the blocked lanes.
  // Never draw an empty box.
  if (isBlocked(f) && (f.ask || (f.askSet && f.askSet.count))) {
    const a = askEl(f);
    card.append(a.box);
    askBox = a.box;
    floorBot = a.floor;
  }

  if (f.pendingInput) {   // there's already unsent text in this session's box
    const warn = el("div", "pending");
    warn.append(el("div", "plbl", "⚠ unsent text already in this session's input box — your reply would go below it"));
    warn.append(el("div", "ptext", f.pendingInput));
    const clr = el("button", "ghost", "clear the box");
    clr.addEventListener("click", () => sendClear(f));
    warn.append(clr);
    card.append(warn);
    warnBox = warn;
  }

  // THE OPTIONS ARE NOT HERE ANY MORE (ADR 0020) — they are in the Ask block
  // above, each one carrying the description that decides it. `.respond` is the
  // reply box and nothing else.
  const respond = el("div", "respond");
  // The reply box is unconditional — idle, **Blocked** or working alike
  // (CONTEXT.md: Focus). Responding to a working Run is not a special case, so
  // nothing here is disabled; it just says where the text goes, once, next to
  // the box: Claude Code's native input queue absorbs it until the next turn
  // (CONTEXT.md: Respond).
  if (f.lane === "working") {
    respond.append(el("div", "queued",
      "⏳ busy — what you send queues until this turn ends"));
  }
  // WHERE THIS TEXT WOULD ACTUALLY GO (ADR 0020). A question widget is not an
  // input box: the server routes prose through the widget's own `Type something`
  // row, and when that row cannot be used the only route left is `Esc`, which
  // CANCELS the ask. That is not an ordinary send and it does not get the
  // ordinary button — it is said here, before you type, and again in a confirm
  // before it happens. The label is computed server-side (`textRoute`); this
  // client never counts a keystroke of its own.
  const cancels = ((f.textRoute || {}).route || "") === "esc";
  if (cancels) {
    respond.append(el("div", "queued warn",
      "⚠ this question has no usable “Type something” row — sending prose here " +
      "presses Esc first, which CANCELS the ask rather than answering it. Your " +
      "text then goes to the ordinary input box."));
  }
  const row = el("div", "replyrow");
  // A TEXTAREA, one row at rest and pixel-identical to the `<input>` it replaces
  // (ADR 0015). **Respond** carries prose — a paragraph, a path, a pasted error —
  // and a single-line box showed the last few words of it through a keyhole. It
  // grows per keystroke and caps at five rows; growComposer owns that arithmetic
  // and the `--barh` the swipe hint stands on. `rows=1` is what makes the box the
  // old input's height before a single measurement happens.
  const ti = el("textarea", "ti");
  ti.rows = 1;
  ti.placeholder = isBlocked(f) ? "answer…"
    : f.lane === "working" ? "queue a note for the next turn…" : "type your reply…";
  // A DIFFERENT CONTROL FOR A DIFFERENT ACT. Not a disabled send (free text must
  // always have a route away from the desk) and not the same `respond →` in a
  // warning colour: the verb itself changes, because what the tap does changes.
  const send = el("button", cancels ? "send danger" : "send",
                  cancels ? "cancel ask & send →" : "respond →");
  // Clear only once the text is actually sent. The box now survives rebuilds,
  // so clearing optimistically (or not at all) would carry a stale value back
  // in and make a sent reply look unsent. It has to shrink back with the text —
  // an emptied five-row box would leave the bar standing at five rows.
  const fire = async () => {
    const v = ti.value.trim();
    if (!v) return;
    // The label came from a poll and a poll is up to four seconds old, so the
    // send carries consent explicitly and the server re-reads the pane and
    // decides again. Both halves are needed: this confirm is what stops a
    // destructive act being one tap, and the server's own check is what stops a
    // stale label authorising one.
    if (cancels && !window.confirm(
        "Esc CANCELS this question — it does not answer it. The Run drops to " +
        "its ordinary input box and your text goes there, with the question " +
        "unanswered.\n\nCancel the ask and send?")) {
      toast("cancelled — the ask is untouched");
      return;
    }
    const payload = cancels ? {text: v, cancelAsk: true} : {text: v};
    if (await sendRespond(f, payload)) { ti.value = ""; growComposer(ti); }
  };
  send.addEventListener("click", fire);
  ti.addEventListener("input", () => growComposer(ti));
  // ENTER INSERTS A NEWLINE. ⌘/Ctrl+Enter SENDS (ADR 0015). Not the Slack idiom,
  // deliberately: a soft keyboard has no Shift+Enter, so Enter-to-send would make
  // a second line untypeable on the phone this whole tool exists for — and the
  // multi-line box above would then be a box you could only ever put one line in.
  // Nothing is lost by moving send off Enter, because `respond →` is already in
  // this row and is the only way in on glass anyway; the modifier is purely the
  // hardware-keyboard shortcut. preventDefault so the send does not also leave a
  // stray newline behind in the box it is about to clear.
  ti.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
    if (e.preventDefault) e.preventDefault();
    fire();
  });
  // Letting go of the box is the moment a deferred advance becomes safe; the
  // re-poll re-runs render()'s check.
  ti.addEventListener("blur", () => { if (advanceWhenFree) { etag = null; poll(); } });
  row.append(ti, send);
  respond.append(row);

  // The per-run action strip. It is built here with the composer but appended to
  // the CARD, not to `.respond`: the composer IS the bottom edge now
  // (see the chrome section below) and a full strip of buttons riding that edge
  // would spend the pixels this whole layout exists to recover. These are the
  // rare, deliberate actions — they sit in the flow at the end of the read,
  // where you arrive just before the box.
  const actions = el("div", "actions");
  const pri = el("span", "prisel");
  pri.append(document.createTextNode("⚑ priority "));
  pri.append(el("b", null, f.pri === 0 ? "high" : f.pri === 2 ? "low" : "normal"));
  pri.append(document.createTextNode(" ▾"));
  const nextLevel = {1: "high", 0: "low", 2: "normal"};   // cycle normal→high→low→normal
  pri.addEventListener("click", () => {
    const lvl = nextLevel[f.pri === 0 || f.pri === 2 ? f.pri : 1];
    postState("api/priority", {sessionId: f.sessionId, level: lvl}, "priority: " + lvl);
  });
  actions.append(pri, el("span", "grow"));
  // No "↩ rotation" button any more: you always hold the Focus, so there is no
  // rotation to hand back to. `skip →` already goes to the most urgent card
  // that isn't this one, which is what that button was reached for.
  const snooze = el("button", "ghost", "snooze ▾");
  snooze.addEventListener("click", () => {
    const h = parseFloat(window.prompt("Snooze how many hours? (0 to un-snooze)", "1"));
    if (!isNaN(h)) postState("api/snooze", {sessionId: f.sessionId, minutes: Math.round(h * 60)},
      h > 0 ? "snoozed " + h + "h" : "un-snoozed");
  });
  const skip = el("button", "ghost", "skip →");
  // Reads the queue head at click time, not at build time — baking it in would
  // make every change of head rebuild this card (and snatch back the keyboard)
  // for a button you may never press.
  skip.addEventListener("click", () => nextUp ? setPinned(nextUp) : toast("nothing up next"));
  actions.append(snooze, skip);
  // Per-run deep-link + attach + close, mirroring the queued rows.
  const link = deepLink(f.bridge);
  if (link) {
    const open = el("a", "ghost", "open ↗");
    open.href = link; open.target = "_blank"; open.rel = "noopener";
    actions.append(open);
  }
  if (f.attach) {
    const term = el("button", "ghost", "attach ❯");
    term.title = "copy tmux attach command";
    term.addEventListener("click", () => copyAttach(f.attach));
    actions.append(term);
  }
  const close = el("button", "ghost", "× close");
  close.addEventListener("click", () => closeRun(f));
  actions.append(close);

  card.append(actions);
  card.append(respond);   // last child — the sticky composer (board.html: .respond)
  // LAST, and only now: the landing measures against a card that is complete, and
  // its floor is the **Ask** (or, when there is unsent text you must see before
  // you type over it, that warning under it). Nothing on an unblocked Focus is
  // below the read, so there is no floor there and the seam's 250px stands.
  if (land) landOn(land.seam, land.sig, land.pad, askBox, warnBox || floorBot || askBox);
  return card;
}

// `compact` is the rail's row: 290px has no room for the ↗ ❯ × strip, and the
// rail is for *getting to* a Run, not for acting on one from a distance — the
// card and the page's own zones still carry every action.
// `.now` is the Focus, which the rail (and only the rail) draws as a row: it is
// what makes the swipe legible on a monitor.
function qrow(item, opts) {
  // The row was one big <button>; it is now a div with a tap-to-focus body plus
  // separate action buttons (a button can't nest the × / ↗ buttons — ADR 0008).
  const o = opts || {};
  const row = el("div", "qrow " + (ROW_CLS[item.lane] || "lane-w") +
                        (pinned && item.sessionId === pinned ? " now" : ""));
  const body = el("button", "qbody");
  body.append(el("span", "qbadge", ROW_BADGE[item.lane] || ""));
  const dir = el("span", "qdir");
  if (item.pri === 0) { dir.append(el("span", "flag", "⚑ ")); }
  const ws = el("span", "qws");
  dir.append(ws);
  body.append(dir);
  nextFrame(() => elideWorkspace(ws, item.workspace));
  body.append(el("span", "qone", item.one || ""));
  body.addEventListener("click", () => setPinned(item.sessionId));
  row.append(body);
  if (!o.compact) row.append(rowActions(item));
  return row;
}

// --- Foreign Runs: visible, never drivable (ADR 0012) -----------------------
// A `claude` started by hand at the Mac. It has no pane of ours, so there is
// nothing to Respond into, no window to Attach to and none of ours to close —
// and it is never Blocked, never the Focus, never in Rotation (CONTEXT.md).
// That is structural here, not a rule this code remembers: these rows arrive on
// their own payload key, so every path the Focus discipline reads
// (data.focus / upnext / watching / snoozed / dormant) is Managed-only and a
// Foreign Run cannot reach it. Nothing below pins, and the row is a div rather
// than a .qbody button so there is no tap target to pin *with*.
//
// Two actions, and no more. ↗ — the Remote Control bridge is Anthropic's cloud,
// not a terminal, so it reaches a Run the Launcher's own transport cannot; a row
// without a bridge simply does not get it. And `transfer`, the one thing the
// Launcher itself can do to a Foreign Run (transferRun, below).
// Every field lands as textContent; only `bridge` becomes structure, and
// deepLink re-checks it exactly as it does for a Managed row.
function frow(item) {
  const row = el("div", "frow");
  const head = el("div", "fghead");
  head.append(el("span", "fgbadge", item.status || "running"));
  const fgws = el("span", "fgdir");
  head.append(fgws);
  head.append(el("span", "fgage", age(item.updatedAt)));
  nextFrame(() => elideWorkspace(fgws, item.workspace));
  const link = deepLink(item.bridge);
  if (link) {
    const a = el("a", "iconbtn", "↗");
    a.href = link; a.target = "_blank"; a.rel = "noopener";
    a.title = "open in the Claude app";
    head.append(a);
  }
  row.append(head);
  if (item.dir) {
    // A path, so it elides by whole segments — `~/…/claude-launcher-recover-filter`
    // rather than a stump. The last segment is the Workspace above by another
    // name, and it is the one that must survive.
    const p = el("div", "fgpath");
    row.append(p);
    nextFrame(() => elidePath(p, item.dir));
  }
  row.append(el("div", "fgone", item.one || ""));
  // Deliberately not a third .iconbtn beside ↗: that glyph row is the queue's,
  // and copying it here would make these read as rows demanding attention. A
  // labelled button in the section's own weight is discoverable — it is the only
  // thing on the row that does anything — without shouting.
  //
  // The status rides on the label, not only in the badge above, because it is the
  // price of the tap: a busy Run is mid-turn and that turn dies with the process.
  // Refusing the tap was considered and rejected — you tapped from somewhere
  // else, and a refusal only strands you (ADR 0012).
  const x = el("button", "fgxfer", item.status === "busy" ? "transfer · mid-turn" : "transfer");
  x.title = "end this run at the Mac and resume its session here";
  x.addEventListener("click", () => transferRun(item, x));
  row.append(x);
  return row;
}

// **Transfer**: one tap that ends the Foreign Run and resumes its Session as a
// Managed Run. One server call, never two — a tap that failed between a kill and
// a resume would leave the Session with nothing running, and you are not at the
// Mac to notice (ADR 0012). We send the sessionId; the pid is the server's to
// find, and is not on this row at all.
async function transferRun(item, btn) {
  // Mirrors the close confirm — a mis-tap here also ends a Run — but names a
  // larger cost, because this Run is not ours: nothing on its screen can be read
  // back first, so the loss is stated rather than enumerated.
  const midturn = item.status === "busy" ? "It is mid-turn — that turn is lost.\n\n" : "";
  if (!window.confirm("Transfer this run?\n\n" + midturn +
      "The run at the Mac is ended and its session resumed here as one you can " +
      "drive. Anything typed there and not sent goes with it, unseen.")) return;
  // The server kills, waits for the exit, then resumes — seconds, not
  // milliseconds. Say so on the button: an unmarked wait reads as a dead tap and
  // gets tapped again. (The server serialises Transfers so a second tap cannot
  // fork the Session, but a second confirm dialog is still noise.)
  btn.disabled = true;
  btn.textContent = "transferring…";
  const res = await postJSON("api/transfer", {sessionId: item.sessionId});
  if (res.orphaned) {
    // The kill landed and the resume did not: this Session now has nothing
    // running and you are away from the Mac. A toast fades after 2.6s, which is
    // exactly how you would miss it, so this one blocks until it is read.
    window.alert(res.message);
  }
  toast(res.message || (res.ok ? "transferred" : "transfer failed"));
  // It is a Managed Run now, invisible until `claude` reaches `ps` — the same
  // gap a launch or a resume leaves, so reuse the same optimistic card and
  // burst-poll. The poll also clears the Foreign row it replaced.
  if (res.ok) watch(res.runId, item.workspace || "transfer");
  etag = null; poll();
}

// Last on the page and visibly not a queue: no lane colour, no count in the
// summary line, nothing to answer. The note says why in the reading that matters
// on a phone — this exists, you cannot answer it from here, and there is one way
// to change that.
function foreignZone(parent, items) {
  if (!items || !items.length) return;
  const h = el("div", "qhead");
  h.append(document.createTextNode("elsewhere · started by hand at the Mac"));
  h.append(el("span", "ct", String(items.length)));
  parent.append(h);
  parent.append(el("div", "fgnote",
    "seen, not driven — no reply box, no attach, no close. transfer one to end it and pick the session up here, or answer it at the Mac (↗ where the Claude app is bridged)."));
  const box = el("div", "fgbox");
  items.forEach((it) => box.append(frow(it)));
  parent.append(box);
}

// One builder, two surfaces: the page's zones and the rail's. `opts` carries
// {dim, mine, sub, compact} — see ringGroups, which is where the labels live.
function zone(parent, label, items, count, opts) {
  if (!items || !items.length) return;
  const o = opts || {};
  const h = el("div", "qhead" + (o.mine ? " mine" : ""));
  h.append(document.createTextNode(label));
  h.append(el("span", "ct", String(count != null ? count : items.length)));
  parent.append(h);
  if (o.sub) parent.append(el("div", "qsub", o.sub));
  const box = el("div", o.dim ? "dim" : null);
  items.forEach((it) => box.append(qrow(it, o)));
  parent.append(box);
}

// --- The return path: one ring, two ways to walk it -------------------------
// The ring is the Board's own display order, and the **answered · still
// running** zone leads it — that is where the Run you just replied to went, and
// coming back to it is the whole point. The **Focus** is spliced into the zone
// its lane belongs to, because the server hands it over on its own key and it
// is therefore in none of them; without that the rail would show every Run
// except the one you are looking at, and the ring would have a hole where you
// are standing.
//
// A **Foreign Run** IS NEVER IN IT, and not by a filter: the ring reads
// `focus` / `watching` / `upnext` / `snoozed` / `dormant`, and a Foreign Run
// arrives on `foreign`, which nothing here touches. It has no **rendered pane**
// to read a blocker from and no **Respond** to answer with, so a Focus on one
// would be a card you cannot use (ADR 0012, CONTEXT.md).
let boardData = null;   // the last payload rendered — what the ring is read from

function ringGroups(withFocus) {
  const d = boardData || {};
  const groups = [
    {key: "watching", label: "answered · still running", items: (d.watching || []).slice(),
     mine: true,
     sub: "you replied and they are still working — nothing resurfaces on its own, so this is the way back to one"},
    {key: "upnext", label: "up next · curated round-robin", items: (d.upnext || []).slice()},
    {key: "snoozed", label: "snoozed", items: (d.snoozed || []).slice(), dim: true},
    {key: "dormant", label: "dormant · parked, still resumable", items: (d.dormant || []).slice(),
     dim: true},
  ];
  const f = withFocus ? d.focus : null;
  if (f) {
    // By lane, which is all the payload says. `yourmove` covers both the
    // rotation head and a dormant Run you pinned; the two are told apart
    // server-side by age alone and the difference is not worth a field.
    const home = f.lane === "working" ? "watching" : f.lane === "snoozed" ? "snoozed" : "upnext";
    const g = groups.find((x) => x.key === home) || groups[1];
    spliceFocus(g, f);
  }
  return groups;
}

// THE RING MUST KEEP ONE SHAPE AS THE FOCUS MOVES THROUGH IT. The Focus arrives
// on its own payload key, so every zone is missing it and it has to be put
// back — and putting it back at the *head* of its zone, the obvious thing, is
// what makes a swipe oscillate: the order depends on which Run is focused, so
// two Runs hand each other back and forth and a third is never reached.
//
// So it goes in at the index the Board's own sort would have given it
// (server.py::_board), which does not know what the Focus is. `sortsBefore`
// only has to agree with the order the zone already arrived in — the other
// rows are never re-sorted, so their order stays exactly the server's.
const sortsBefore = {
  // blocked before idle, then priority, then age — oldest-first while blocked
  // (you have waited longest), newest-first otherwise. The server's `order` key.
  upnext: (f, it) => {
    const fb = isBlocked(f) ? 0 : 1, ib = isBlocked(it) ? 0 : 1;
    if (fb !== ib) return fb < ib;
    const fp = f.pri === undefined ? 1 : f.pri, ip = it.pri === undefined ? 1 : it.pri;
    if (fp !== ip) return fp < ip;
    return fb === 0 ? (f.updatedAt || 0) <= (it.updatedAt || 0)
                    : (f.updatedAt || 0) >= (it.updatedAt || 0);
  },
  // Newest activity first — the server's key for `watching` and `dormant`
  // alike. `snoozed` is the one it cannot reproduce (the server orders that by
  // snooze expiry, which the payload does not carry); recency stands in, and
  // the cost is the Focus sitting a row from where it belongs in a zone you
  // parked on purpose.
  rest: (f, it) => (f.updatedAt || 0) >= (it.updatedAt || 0),
};

function spliceFocus(group, f) {
  const before = group.key === "upnext" ? sortsBefore.upnext : sortsBefore.rest;
  const items = group.items;
  let i = 0;
  while (i < items.length && !before(f, items[i])) i++;
  group.items = items.slice(0, i).concat([f], items.slice(i));
}

// The ring as sessionIds, in the order the rail draws them.
function ringOrder() {
  const out = [];
  for (const g of ringGroups(true)) {
    for (const it of g.items) {
      if (it.sessionId && out.indexOf(it.sessionId) < 0) out.push(it.sessionId);
    }
  }
  return out;
}

function ringItem(sid) {
  for (const g of ringGroups(true)) {
    const hit = g.items.find((it) => it.sessionId === sid);
    if (hit) return hit;
  }
  return null;
}

// The wide half of the return path: 290px of width a monitor never misses,
// carrying the whole ring, always. No tap to open it and no gesture to know
// about — on a big screen the Run you want is simply on screen. Below 900px it
// is `display:none` and the phone keeps its zones and the swipe (board.html).
// `.qrow.now` marks the Focus, so the gesture has a readout here: swipe, and
// the accent moves.
function renderRail() {
  if (!railEl) return;
  railEl.textContent = "";
  const n = ringOrder().length;
  const head = el("div", "railhead");
  head.append(el("b", null, "◆ board"));
  head.append(el("span", null, n + (n === 1 ? " run" : " runs")));
  railEl.append(head);
  for (const g of ringGroups(true)) {
    // No sub-line and no action strip at 290px — the rail is for reaching a
    // Run, and the page's own zones still say everything else.
    zone(railEl, g.label, g.items, g.items.length,
         {dim: g.dim, mine: g.mine, compact: true});
  }
}

// --- The Focus card: the one piece of DOM holding state you would miss -------
// Everything else on the page is derived from the payload and cheap to redraw.
// The Focus card is not: it holds a half-typed reply and a scroll position in
// the context. So it is never rebuilt on spec — only when its own data actually
// moved, and even then the two stateful bits are carried across.
let focusSig = null;   // signature of the payload the live card was built from
let focusSid = null;   // its Session — a different one earns a clean card
let nextUp = null;     // the queue head, for `skip →`. Kept out of the card.

function sigOf(f) {
  if (!f) return "";
  // `pinned` is true forever once adopted and drives no UI; leaving it in the
  // signature would rebuild the card for nothing on the very first adopt.
  const {pinned: _p, ...rest} = f;
  return JSON.stringify(rest);
}

function renderFocus(f) {
  const sig = sigOf(f);
  if (sig === focusSig) return;   // nothing about the Focus moved — hands off it
  const old = focusWrap.querySelector(".focus");
  const keep = (old && f && focusSid === f.sessionId) ? grab(old) : null;
  // Opened **work runs**, **Records** and run-up rows belong to the Session you
  // opened them in. A new Focus starts folded, and no set grows across Sessions —
  // and it has no reading position to keep either, so it re-lands on its seam
  // unconditionally (ADR 0017).
  if (!f || focusSid !== f.sessionId) {
    openRuns = new Set(); openRecords = new Set(); openInner = new Set();
    resetLanding();
  }
  focusSig = sig;
  focusSid = f ? f.sessionId : null;
  focusWrap.textContent = "";
  if (!f) {
    // No **Focus** means no `.fhead`, so there is no ＋ and nothing to open a
    // sheet with — so **Intake** stops being one and renders inline, open, above
    // this slot (ADR 0015). This is the post-reboot screen: Intake is the only
    // thing you can do here, and there must never be a Board with no route to it.
    setIntakeInline(true);
    focusWrap.append(el("div", "empty", "All clear — nothing needs you right now."));
    chromeHid = false;   // no read to get out of the way of
    applyChrome();
    return;
  }
  setIntakeInline(false);
  const card = focusCard(f);
  focusWrap.append(card);
  // The composer's height is measured, never declared (growComposer), and it is
  // measured HERE — after the card is in the document, or the box has no layout to
  // report. restore() does it for a carried-over reply; a clean box still gets the
  // measurement, so the at-rest row is the same computed height in both cases and
  // never `rows=1` in one and a measurement in the other.
  if (keep) restore(card, keep);
  else growComposer(card.querySelector(".ti"));
  // Chrome across a rebuild. A rebuild of the SAME Focus re-APPLIES the state it
  // already had rather than re-deriving it: restore() carried the reading
  // position over unchanged, so the state that matched it still matches — and a
  // turn arriving while you sit reading must not yank the composer away from
  // someone who has not moved. A *different* Session is a different read and
  // starts with the chrome up, wherever the page happens to sit: you have
  // travelled nowhere in it yet, so there is no history to be up in.
  if (!keep) { chromeHid = false; chromeAnchor = window.scrollY || 0; }
  applyChrome();
}

// What a rebuild would otherwise cost you: the reply text, the caret, whether
// the keyboard is up, and where you had read up to.
//
// That reading position used to be `.ctx`'s scrollTop, because the run-up sat in
// a 46vh scroller of its own. ADR 0014 killed that box, so the **Scrollback**
// flows into the page and the page scroll IS the reading position — same intent,
// one level up. A poll must not throw you back to the top of a long run-up.
// `boxScroll` is the composer's OWN scroll, which only exists because the box is
// a textarea capped at five rows (ADR 0015): past the cap it scrolls internally,
// so a long reply has a reading position of its own inside the bar, and a rebuild
// that dropped it would jump you to the top of your own draft.
function grab(card) {
  const ti = card.querySelector(".ti");
  return {text: ti ? ti.value : "", start: ti ? ti.selectionStart : 0,
          end: ti ? ti.selectionEnd : 0, active: !!ti && ti === document.activeElement,
          boxScroll: ti ? ti.scrollTop : 0,
          scroll: window.scrollY || 0};
}

function restore(card, k) {
  window.scrollTo(0, k.scroll);
  const ti = card.querySelector(".ti");
  if (!ti) return;
  ti.value = k.text;
  growComposer(ti);   // a carried-over reply is however many rows it was
  if (k.active) {     // it had the keyboard up — give it straight back
    ti.focus();
    try { ti.setSelectionRange(k.start, k.end); } catch (e) {}
  }
  // LAST, because both of the two calls above move it: the height clamps it, and
  // setSelectionRange scrolls the caret into view. This is the position the reader
  // actually left, so it gets the final say.
  ti.scrollTop = k.boxScroll;
}

// Are you mid-reply on the Focus? Text in the box counts even without the caret:
// you may have tapped away to read the context before sending.
function replyEngaged() {
  const ti = focusWrap.querySelector(".ti");
  return !!ti && (ti === document.activeElement || ti.value.trim() !== "");
}

// --- Scroll-driven chrome: reading and answering are scroll positions --------
// ADR 0014 measured the problem: on a 390×844 phone the chrome around the read
// took half the viewport. Slice 02 gave the **Scrollback** the page scroll; this
// is the other half — the chrome that is worth pixels only at the live end of it
// stops being worth them the moment you scroll up into history.
//
// No mode and no toggle: the scroll position IS the mode. Scroll up and the
// Focus's header and its composer slide away; travel back down, or reach the end
// of the read, and they return. They slide with a `transform` on
// `position:sticky` elements (board.html), so hidden chrome reserves NOTHING —
// the **turns** scroll under it, and hiding hands those pixels straight to the
// read. A max-height hide would reflow the whole scrollback and give the reader
// nothing until it landed.
//
// WHY TRAVEL AND NOT JUST "AM I NEAR THE BOTTOM". The prototype could ask only
// that, because it auto-scrolled a fresh Focus to its newest **turn**. This
// Board does not: a **Scrollback** is oldest-first and you land at the top of it
// and read down, so "not near the bottom" would mean the chrome was hidden the
// entire way through a first read, and on every page load. Travel says the thing
// that is actually true — moving up is going back into history, moving down is
// going toward the answer — and the end of the read still wins outright.
//
// THE BOTTOM EDGE IS THE COMPOSER, AND NOTHING ELSE (ADR 0015). It used to be
// shared and resolved by stacking: the intake dock at `bottom:0` (ADR 0008 had
// measured dir-launch as the hot path) with the composer standing on it at
// `bottom:var(--dockh)`, plus the **Recover** pill between them whenever anything
// at all was resumable. Two of those three are **Intake** — the *create* half of
// the **Board** — holding the one strip that cannot be scrolled away from, for a
// verb used a handful of times a day. They are one sheet behind a ＋ now, so there
// is one bar at this edge and one chrome state covering it.
const CHROME_SLACK = 140;   // px the end of the read must sit below the fold
                            // before there is any history to be up in
// ASYMMETRIC, AND THAT IS THE THIRD FLICKER GUARD. `CHROME_STEP = 24` in both
// directions was a deadzone: a jitter of 25px round-tripped the bars forever,
// which cost nothing while the hidden header still reserved its box and costs a
// visible judder now that it collapses (board.html: `.fhead.hid`). Showing must
// out-travel hiding, so the pair is hysteresis rather than a bigger deadzone —
// no oscillation can walk back and forth across one line. Off the prototype:
// 28px of travel up into history hides, 64px back down shows.
const CHROME_HIDE_STEP = 28;
const CHROME_SHOW_STEP = 64;
// The ceiling on the settle window below, and a fuse rather than a timer: the
// window normally closes on the post-layout frame, and this is only what closes
// it if the frames stop coming (a backgrounded tab), because a settle window
// that never closes is a chrome that never moves again.
const CHROME_SETTLE_MS = 220;

let chromeHid = false;
let chromeAnchor = 0;       // the scroll position the current run of travel began
                            // at, so the step is consecutive travel, not drift
let chromeSettling = false; // a toggle is in flight; the scroll we are seeing may
let chromeSettleUntil = 0;  // be our own layout change rather than a finger
let chromeSettleSeq = 0;    // ...and which toggle it belongs to
// The state actually ON the nodes, so the settle window opens for a TOGGLE and
// never for a redraw. applyChrome's callers all re-apply unconditionally — a
// rebuild re-paints the state it already had, an open Intake overrides it — and
// re-baselining the travel anchor on each of those would quietly eat a slow drag
// that spans a poll.
let chromeApplied = null;

// Is the end of the read below the fold? The card's own chrome is sticky and so
// contributes nothing here: this measures the turns, the **Ask** and the pending
// warning — everything you actually scroll through.
function readingUp() {
  const card = focusWrap.querySelector(".focus");
  if (!card || !card.getBoundingClientRect) return false;
  // Never slide the box out from under an active reply. Same instinct as
  // advanceWhenFree: the keyboard is up and the caret is in it, so a scroll must
  // no more snatch it away than a poll may.
  const ti = card.querySelector(".ti");
  if (ti && ti === document.activeElement) return false;
  return card.getBoundingClientRect().bottom > (window.innerHeight || 0) + CHROME_SLACK;
}

function setHid(node, hid) {
  setCls(node, "hid", hid);
}

// Is **Intake** on screen? The sheet, the inline empty-Board layout, and the
// **Recover** picker the sheet leads to. Not `dirpop`: the dropup only exists
// inside the sheet, so it cannot be open without the first term already being
// true.
function intakeOpen() {
  return intakeSheetOpen || intakeInline || recoverOpen;
}

// An Intake you have opened holds the whole chrome up — the ＋ that opened it
// lives in `.fhead` (ADR 0015), so sliding that strip away underneath an open
// sheet would leave the surface no marked way in and no way back to the read it
// covers. One rule for the header, the composer and the hint alike, exactly as
// when the dock rode this state with them.
function applyChrome() {
  const card = focusWrap.querySelector(".focus");
  const hid = chromeHid && !intakeOpen();
  setHid(card && card.querySelector(".fhead"), hid);
  setHid(card && card.querySelector(".respond"), hid);
  setHid(hintEl, hid);   // the swipe hint shares this edge; so does it
  // The header CONDENSES rather than leaving (board.html: `.fhead.hid`), so this
  // line just changed the page's layout — whoever asked for it, a tap on the ＋
  // as much as a scroll.
  if (hid !== chromeApplied) { chromeApplied = hid; settleChrome(); }
}

function setChrome(hid, y) {
  chromeAnchor = y;
  if (hid === chromeHid) return;
  chromeHid = hid;
  applyChrome();
}

// THE SETTLE WINDOW — the second of the three guards the collapse needs, and the
// only one that is code. A toggle now resizes the header, the document gets
// shorter or longer above the fold, and the scroll events that arrive next may
// be the browser's own compensation rather than a finger. Taking travel from
// those is how a header drives the handler that collapsed it. So: hold the line
// until the collapse has finished moving things, then take the POST-layout
// position as the new reference — never the pre-layout one.
//
// The frame is the signal and the clock is only the fuse. Two frames is honest
// here precisely because the condense animates nothing that costs layout
// (board.html: `.fhead`'s transition is colour and nothing else), so layout has
// genuinely stopped by then; the prototype needed a 220ms clock because it was
// still easing a property that reflowed. `nextFrame` falls back to a timeout
// where there is no rAF, which is also what makes this drivable in a test.
function settleChrome() {
  const seq = ++chromeSettleSeq;
  chromeSettling = true;
  chromeSettleUntil = Date.now() + CHROME_SETTLE_MS;
  nextFrame(() => nextFrame(() => {
    // Stale if the fuse blew first, or if another toggle has opened a window of
    // its own since. Either way this frame is not the one that says where the
    // reader is, and re-baselining from it would eat real travel.
    if (seq !== chromeSettleSeq || !chromeSettling) return;
    chromeAnchor = Math.max(0, window.scrollY || 0);
    chromeSettling = false;
  }));
}

// Not a pure question: an expired window is a blown fuse, and blowing it is what
// stops a chrome that never toggles again when the frames stop coming.
function chromeSettled() {
  if (chromeSettling && Date.now() < chromeSettleUntil) return false;
  chromeSettling = false;
  return true;
}

function syncChrome() {
  // Clamped: iOS rubber-band drives scrollY negative past the top and beyond the
  // maximum at the bottom, and the spring back reads here as real travel — enough
  // of it to trip CHROME_STEP and flap the bars while the finger is still.
  const y = Math.max(0, window.scrollY || 0);
  // The end of the read is on screen, or there is no read: you are at the answer
  // point and the chrome is up, whatever direction you arrived from. This is the
  // "returns near the bottom" half, and it covers a Focus short enough never to
  // scroll at all.
  if (!readingUp()) { setChrome(false, y); return; }
  // AFTER the rule above, not before it, and the ordering is load-bearing. The
  // settle window rejects a *travel* reading, and "the end of the read is on
  // screen" is a position: it is idempotent, it can only ever show the chrome,
  // so it cannot oscillate against itself. It also cannot be tripped BY the
  // collapse — condensing frees ~50px and CHROME_SLACK is 140, so no toggle can
  // move the card's bottom across that line on its own.
  if (!chromeSettled()) { chromeAnchor = y; return; }
  const d = y - chromeAnchor;
  const step = chromeHid ? CHROME_SHOW_STEP : CHROME_HIDE_STEP;
  if (chromeHid ? d > step : d < -step) setChrome(!chromeHid, y);
  else if (chromeHid ? d < 0 : d > 0) chromeAnchor = y;   // still going the way
                                                          // this state expects
}

// The escape hatch: interacting with the page brings the chrome back without
// making you scroll for it. A nudge, not a latch — re-anchored here, so another
// step back up into history takes it away again.
function showChrome() {
  setChrome(false, window.scrollY || 0);
}

// --- The swipe: the narrow half of the return path --------------------------
// POINTER events, never touch-only. The prototype's first cut listened on
// `touchstart`/`touchend`, which fires nothing under a mouse: on a desktop the
// gesture did not exist at all, and the design could not be judged there. One
// pointer listener covers finger, mouse and pen. A trackpad's two-finger flick
// arrives as `wheel` instead and ←/→ is the same move on a keyboard, so all
// three are wired — they are one gesture, not three features.
//
// THE THRESHOLDS ARE ABOUT VERTICAL SCROLLING. A **Scrollback** is read by
// dragging it up and down, and a horizontal reading that fires too easily makes
// that read feel sticky — which costs far more than a missed swipe. So a drag
// must travel >70px sideways AND out-run its own vertical travel by 1.8x before
// it counts. Both numbers came off a real phone (prototype/focus-layout).
const SWIPE_MIN = 70;      // px of horizontal travel before a drag is a gesture
const SWIPE_BIAS = 1.8;    // ...and how far it must out-run the vertical
const WHEEL_MIN = 42;      // one trackpad flick's deltaX
const WHEEL_BIAS = 1.6;
const WHEEL_LOCK = 700;    // a flick is many wheel events — take the first only
const EDGE_FLASH = 320;

// Where the gesture is NOT: the composer (you are typing into it), the **Intake**
// sheet, the Recover sheet, the dir dropup — each its own surface with its own
// targets — the rail (a list you scroll), and any input or markdown table that
// scrolls on an axis of its own.
const SWIPE_BLOCK = ".respond,.isheet,.recovpanel,.dirpop,.rail,input,textarea,table";
function inChrome(node) {
  return !!(node && node.closest && node.closest(SWIPE_BLOCK));
}

// A gesture leaves no mark, so say where it landed: the edge you moved toward
// flashes, and the toast names the Run. On a phone that is the only feedback
// there is; on a monitor the rail's `.now` says it too.
function flashEdge(dir) {
  const n = dir > 0 ? edgeREl : edgeLEl;
  if (!n) return;
  const base = "edge " + (dir > 0 ? "r" : "l");
  n.className = base + " on";
  clearTimeout(n._flash);
  n._flash = setTimeout(() => { n.className = base; }, EDGE_FLASH);
}

// Nothing on the page says the gesture exists, so one dim line does — until the
// first time it is used, remembered per device. It rides the chrome state like
// everything else at this edge (applyChrome), so a read is never nagged.
// Both localStorage calls are wrapped: reaching it AT ALL throws where cookies
// are blocked, and this one runs at load — an unhandled throw here would take
// the whole Board down for the sake of a hint.
let swipeUsed = false;
try { swipeUsed = localStorage.getItem("cl_swipe") === "used"; } catch (e) { /* no store */ }
function markSwipeUsed() {
  if (swipeUsed) return;
  swipeUsed = true;
  try { localStorage.setItem("cl_swipe", "used"); } catch (e) { /* hint returns next load */ }
  syncHint();
}
function syncHint() {
  if (!hintEl) return;
  hintEl.hidden = swipeUsed || !boardData || !boardData.focus || ringOrder().length < 2;
}

// Move the Focus one step around the ring. This is **Rotation** by your consent
// and nothing else (CONTEXT.md): a gesture you made, exactly like a row tap.
// Nothing here is automatic, and it does not touch advanceWhenFree.
function swipeFocus(dir) {
  const ring = ringOrder();
  if (ring.length < 2) { toast("nothing else on the Board to move to"); return; }
  // A gesture is easy to make by accident; a half-typed reply is not cheap to
  // lose. So the Focus does not move while there is one — the same rule the
  // deferred advance obeys, for the same reason (replyEngaged).
  if (replyEngaged()) { toast("finish or clear the reply first — the Focus stays put"); return; }
  const i = ring.indexOf(pinned);
  const next = ring[((i < 0 ? 0 : i) + dir + ring.length) % ring.length];
  if (!next || next === pinned) return;
  markSwipeUsed();
  flashEdge(dir);
  const it = ringItem(next);
  toast("→ " + ((it && (it.workspace || it.dir)) || "run"));
  setPinned(next);   // the ONE mechanism that moves the Focus. Never a second.
}

let dragX = 0, dragY = 0, dragging = false;
window.addEventListener("pointerdown", (e) => {
  dragging = !inChrome(e.target);
  dragX = e.clientX || 0;
  dragY = e.clientY || 0;
});
window.addEventListener("pointerup", (e) => {
  if (!dragging) return;
  dragging = false;
  const dx = (e.clientX || 0) - dragX, dy = (e.clientY || 0) - dragY;
  if (Math.abs(dx) > SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * SWIPE_BIAS) {
    swipeFocus(dx < 0 ? 1 : -1);
  }
});
window.addEventListener("pointercancel", () => { dragging = false; });

// The trackpad. Passive: this never preventDefaults, it only reads a flick that
// the page has no horizontal axis to spend anyway.
let wheelLock = 0;
window.addEventListener("wheel", (e) => {
  if (inChrome(e.target)) return;
  const dx = e.deltaX || 0, dy = e.deltaY || 0;
  if (Math.abs(dx) < WHEEL_MIN || Math.abs(dx) < Math.abs(dy) * WHEEL_BIAS) return;
  if (Date.now() < wheelLock) return;
  wheelLock = Date.now() + WHEEL_LOCK;
  swipeFocus(dx > 0 ? 1 : -1);
}, {passive: true});

// The keyboard. Ignored the moment anything is taking text — the reply box, the
// dir field, the resume field, a task's seed box — where an arrow key means
// "move the caret" and must go on meaning it.
document.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const t = document.activeElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (recoverOpen) return;   // the picker owns the screen while it is open
  swipeFocus(e.key === "ArrowRight" ? 1 : -1);
});

// Publish the composer's real height. This was syncDockHeight, and it published
// `--dockh` too — the dock's measured box, which the composer stood on and the
// **Recover** pill grew. ADR 0015 deleted the dock, so `--dockh` is gone with it
// and only the composer is left to measure.
//
// It still has to be measured rather than assumed: two things stand on `--barh` —
// the swipe hint (board.html: .swipehint) and the toast — and a **Blocked** Focus
// grows this bar by a row of options, so a constant would bury them underneath it
// precisely when the options appear. Re-measured on load, on resize, on every
// render (the card that carries the bar is rebuilt there) and now on every
// keystroke, because the reply box is a textarea that grows — see growComposer,
// which is the only caller that fires while your thumb is on the glass.
function syncBarHeight() {
  if (!document.documentElement) return;
  const bar = focusWrap && focusWrap.querySelector && focusWrap.querySelector(".respond");
  const bh = bar && bar.getBoundingClientRect ? Math.round(bar.getBoundingClientRect().height) : 0;
  if (bh > 0) document.documentElement.style.setProperty("--barh", bh + "px");
}

// The reply box's height, per keystroke: one row at rest, five at most, then it
// scrolls inside itself (ADR 0015). `field-sizing:content` is this in one CSS
// declaration and is not in Safari yet — which is the phone the whole tool exists
// for — so the height is measured here and written inline.
//
// THE CAP IS COMPUTED FROM THE BOX, NOT WRITTEN DOWN AS PIXELS. Five of *this*
// box's computed line-height, plus its own vertical padding and border, so it
// follows the font, the padding and the reader's text-size preference instead of
// dating the moment any of them move. The border is in that sum because `*` sets
// `box-sizing:border-box`, so an inline `height` has to cover it — and because
// `scrollHeight` is content + padding and never border, which is exactly the 2px
// that would otherwise leave the box a hair shorter than the `<input>` it replaces
// and scrolling by that much at rest.
//
// It ends in syncBarHeight, and that is the point of routing every growth through
// here: `--barh` is the composer's measured height and the swipe hint stands on
// it, so the bar moving per keystroke means the hint has to move with it.
const CAP_ROWS = 5;

function growComposer(ta) {
  if (!ta || !ta.style) return;
  const cs = (window.getComputedStyle && window.getComputedStyle(ta)) || {};
  const px = (v) => parseFloat(v) || 0;
  const lh = px(cs.lineHeight) || 19;   // a `normal` line-height parses to NaN
  const pad = px(cs.paddingTop) + px(cs.paddingBottom);
  const bord = px(cs.borderTopWidth) + px(cs.borderBottomWidth);
  ta.style.height = "auto";   // let scrollHeight report the TEXT, not the height
                              // this function wrote the last time it ran
  const sh = ta.scrollHeight || 0;
  // A box nothing has laid out yet measures 0. Hand the height back to `rows=1`
  // rather than collapse the bar to nothing.
  ta.style.height = sh > 0
    ? Math.min(sh + bord, Math.round(lh * CAP_ROWS + pad + bord)) + "px" : "";
  syncBarHeight();
}

function render(data) {
  boardData = data;  // the ring reads this — set before anything draws from it
  reconcile(data);   // clear any optimistic card whose real Run has surfaced
  const c = data.counts || {};
  summary.textContent = "";
  const b = (n) => { const s = el("b", null, String(n)); return s; };
  summary.append(b(c.needYou || 0), document.createTextNode(" need you · "),
    b(c.watching || 0), document.createTextNode(" watching · "),
    b(c.dormant || 0), document.createTextNode(" dormant"));

  const f = data.focus;
  nextUp = ((data.upnext || [])[0] || {}).sessionId || null;

  // --- Focus discipline: rotation is consent-based ---------------------------
  // Adopt. The server only picks a head while we hold nothing; the moment it
  // hands us one we make it ours, and every poll from here carries ?focus=. So
  // the head can never re-pick under you: a Run that blocks now joins the queue
  // instead of taking the card out from under what you are reading or typing.
  // A Focus that vanished is the same path — the server fell back to the head,
  // we adopt that, no special case.
  if (!f) { pinned = null; heldLane = null; }
  else if (pinned !== f.sessionId) { pinned = f.sessionId; heldLane = null; }

  // Advance-on-resolve: the one automatic move. The Focus you were holding went
  // working — you responded, or it was answered on the Mac — so it no longer
  // needs you; hand it on. Only a *transition* counts: tapping an already-working
  // row out of `watching` is a choice, not a resolve, so a fresh Focus records
  // its lane as a baseline (heldLane = null above) and never trips this.
  if (f && heldLane && heldLane !== "working" && f.lane === "working") advanceWhenFree = f.sessionId;
  if (f) heldLane = f.lane;
  // Deferred while you are mid-reply: a busy Run still takes input (Claude Code
  // queues it until the next turn), so advancing would eat the very text this
  // whole discipline exists to protect. The box's blur re-polls and lands here.
  if (advanceWhenFree && !replyEngaged()) {
    const sid = advanceWhenFree;
    advanceWhenFree = null;
    clearTimeout(advanceT);
    advanceT = setTimeout(() => { if (pinned === sid) setPinned(null); }, 1200);
  }

  renderFocus(f);
  syncBarHeight();   // the card just changed: --barh follows the composer's height
  zonesWrap.textContent = "";
  // Two boxes, because they answer to different rules at a wide width: the
  // queue steps aside for the rail (board.html) while the Foreign section —
  // which is not a queue, is not in the ring, and is not the rail's business —
  // stays in the flow at every width (ADR 0012).
  const queues = el("div", "queues");
  const foreigns = el("div", "foreigns");
  zonesWrap.append(queues, foreigns);
  // The zones, in the ring's order. `answered · still running` LEADS and is not
  // dimmed: it used to read "watching · resurfaces when it needs you", three
  // headings down and greyed, which was backwards — if you are reading this
  // list the Run did not resurface, and you came looking for it.
  for (const g of ringGroups(false)) zone(queues, g.label, g.items, g.items.length, g);
  foreignZone(foreigns, data.foreign);
  renderRail();
  syncHint();
}

// --- polling: chained setTimeout, ETag revalidate, paused when hidden -------
let etag = null;
let timer = null;
let pinned = null;   // the Focus you hold. Adopted on first sight and sent as
                     // ?focus= on every poll after — the server picks a head
                     // only while this is null (see render).
let heldLane = null;        // its lane as of last render — to spot the resolve
let advanceWhenFree = null; // a resolved Focus, waiting on you to stop typing
let advanceT = null;

function boardUrl() {
  return "api/board" + (pinned ? "?focus=" + encodeURIComponent(pinned) : "");
}

// Choosing a card (a row tap, `skip →`, or the advance handing one on).
function setPinned(sid) {
  pinned = sid;
  // Every route to a new Focus lands here — a row in the sheet, a row in the
  // rail, a swipe, `skip →` — so the sheet is put away here rather than on the
  // tap. Landing a Focus is the only reason it was open.
  setQueueOpen(false);
  heldLane = null;          // whatever lane it is in is a baseline, not a resolve
  advanceWhenFree = null;   // choosing cancels a pending advance
  clearTimeout(advanceT);
  etag = null;   // the focus param changes the payload — force a fresh fetch
  poll();
}

async function poll() {
  try {
    const headers = etag ? {"If-None-Match": etag} : {};
    const r = await fetch(boardUrl(), {headers});
    if (r.status === 304) { schedule(); return; }
    etag = r.headers.get("ETag");
    render(await r.json());
    // The live set just moved (a Run started or ended), so what is Resumable
    // moved too — refresh the recover count. ETag-cheap: a 304 when nothing
    // changed. Only on a real board change, never on the 304 above.
    loadRecoverable();
  } catch (e) {
    toast("board unreachable");
    // Nothing has ever rendered, so there is no Focus card and therefore no ＋ —
    // and the page would offer no route to **Intake** at all. Fall back to the
    // empty Board's inline layout, which is also the honest reading: we cannot see
    // a **Run**, so starting one is the only thing here (ADR 0015).
    if (!boardData) setIntakeInline(true);
  }
  schedule();
}

function schedule() {
  clearTimeout(timer);
  // Burst while a just-launched Run has not surfaced yet; steady otherwise.
  if (!document.hidden) timer = setTimeout(poll, pendingRuns.size ? 500 : 4000);
}

// The ＋ that opens the sheet is not wired here: it lives in the Focus's card
// header, so focusCard() builds it and binds it on every rebuild (ADR 0015).
launchEl.addEventListener("click", launchDir);
resumeEl.addEventListener("click", resumeSession);
dirEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") launchDir();
  else if (e.key === "Escape") hidePop();
});
dirEl.addEventListener("focus", loadDirs);
dirEl.addEventListener("input", () => renderPop(dirEl.value));
// A tap on a suggestion keeps focus (pointerdown preventDefault), so this blur
// only fires when you leave the field; the delay lets a pending tap land first.
dirEl.addEventListener("blur", () => setTimeout(hidePop, 120));
sidEl.addEventListener("keydown", (e) => { if (e.key === "Enter") resumeSession(); });
recoverBtnEl.addEventListener("click", openRecover);
recovCloseEl.addEventListener("click", closeRecover);
recovGoEl.addEventListener("click", doRecover);
// Tap the dimmed backdrop (never the sheet) to dismiss, and Escape to match.
recovPanelEl.addEventListener("click", (e) => { if (e.target === recovPanelEl) closeRecover(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && recoverOpen) closeRecover(); });

// The chrome follows the scroll; a tap anywhere is the escape hatch that brings
// it back without one. `click`, not `pointerdown`: a pointerdown is also how a
// scroll gesture STARTS, so the hatch would flash the chrome in and the scroll
// would take it straight back out. No exclusion list is needed either — hidden
// chrome is off-screen and cannot be the thing you tapped.
window.addEventListener("scroll", syncChrome, {passive: true});
window.addEventListener("resize", () => { syncBarHeight(); syncChrome(); });
document.addEventListener("click", showChrome);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else { poll(); loadTasks(); loadRecoverable(); }
});

syncBarHeight();
loadTasks();
loadRecoverable();
poll();
