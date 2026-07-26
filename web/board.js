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

const LANE_LABEL = {question: "blocked · question", approval: "blocked · approval",
                    yourmove: "your move", working: "working", snoozed: "snoozed"};
const LANE_NOUN = {question: "waiting", approval: "waiting", yourmove: "idle",
                   working: "working", snoozed: "snoozed"};
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
    mid.append(el("div", "recovdir", s.dir || ""));
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

// One **Turn** of the **Scrollback**: who spoke, its prose, and the names of the
// tools it invoked. `live` marks the newest assistant turn — the one you are
// actually answering — so it reads as the head of the run-up, not just the last
// paragraph of it.
//
// ADR 0006's innerHTML exception lives HERE and nowhere else in this file. Only
// `t.html` is assigned: it is markdown the server already rendered escape-first
// (`_md_to_html`), so a `<script>` in a transcript arrives as text, never as an
// element. ADR 0014 widened that exception from one field to N turns — same
// function, same sink. Every OTHER field of a turn (the role, each tool name) is
// untrusted transcript text and goes through el() → textContent, per ADR 0003.
function turnEl(t, live) {
  const tools = t.tools || [];
  const toolsOnly = !t.html && tools.length > 0;
  const wrap = el("div", "turn " + (t.role === "user" ? "you" : "ai") +
                         (live ? " live" : "") + (toolsOnly ? " toolsonly" : ""));
  wrap.append(el("div", "who", t.role === "user" ? "you" : "claude"));
  if (t.html) {
    const md = el("div", "md");
    md.innerHTML = t.html;   // server-escaped markdown — see ADR 0006 / 0014
    wrap.append(md);
  }
  // A turn carrying only tools is the COMMON case on a working Run. Rendered as
  // a blank the whole scrollback looks broken, so it draws dimmed chips instead
  // (ADR 0014).
  if (tools.length) {
    const chips = el("div", "tools");
    tools.forEach((name) => chips.append(el("span", "tool", name)));
    wrap.append(chips);
  }
  return wrap;
}

function focusCard(f) {
  const cls = f.lane === "question" ? "focus bq" : f.lane === "approval" ? "focus bp" : "focus";
  const card = el("div", cls);

  const head = el("div", "fhead");
  head.append(el("span", "fdir", f.title || f.dir || "claude"));
  head.append(el("span", "fbadge", LANE_LABEL[f.lane] || f.lane));
  head.append(el("span", "grow"));
  // Split, because this strip is now four things wide on a 390px phone and the
  // sessionId is the one nobody reads there — CSS drops it under 560px rather
  // than let the title and the badge wrap to two lines each.
  head.append(el("span", "fsid", (f.sessionId || "").slice(0, 8)));
  head.append(el("span", "fmeta", (LANE_NOUN[f.lane] || "") + " " + age(f.updatedAt)));
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

  if (f.aiTitle) {
    const about = el("div", "about");
    about.append(el("span", "albl", "session"));
    about.append(document.createTextNode(f.aiTitle));
    card.append(about);
  }

  // The **Scrollback**: the Session's recent **turns**, oldest first, in place of
  // the single last assistant message (ADR 0014). What you said, what it did and
  // what it then said — the run-up you need in order to answer. It has NO scroll
  // box of its own; it flows into the page scroll (see `.sb` in board.html).
  const sb = el("div", "sb");
  const turns = f.scrollback || [];
  if (!turns.length) sb.append(el("div", "who", "(nothing in the transcript tail yet)"));
  turns.forEach((t, i) => sb.append(
    turnEl(t, t.role === "assistant" && i === turns.length - 1)));
  card.append(sb);

  // An **Ask** is the blocker of a **Blocked** Run and of nothing else
  // (CONTEXT.md). An idle Run's closing question is now visibly the last turn
  // above, so the old "(no explicit question — your move)" placeholder was ~62px
  // of chrome saying nothing; the server sends `ask: ""` off the blocked lanes.
  // Never draw an empty box.
  if (isBlocked(f) && f.ask) {
    const ask = el("div", "ask");
    ask.append(el("div", "lbl", "the ask"));
    ask.append(el("div", "qtext", f.ask));
    card.append(ask);
  }

  if (f.pendingInput) {   // there's already unsent text in this session's box
    const warn = el("div", "pending");
    warn.append(el("div", "plbl", "⚠ unsent text already in this session's input box — your reply would go below it"));
    warn.append(el("div", "ptext", f.pendingInput));
    const clr = el("button", "ghost", "clear the box");
    clr.addEventListener("click", () => sendClear(f));
    warn.append(clr);
    card.append(warn);
  }

  const respond = el("div", "respond");
  if (f.options && f.options.length) {
    const opts = el("div", "opts");
    // Selecting option i: step the selector cursor from where it actually sits
    // (f.cursor, read off the rendered menu) to i, then enter.
    const cur = f.cursor || 0;
    f.options.forEach((o, i) => {
      const b = el("button", "opt", o);
      const d = i - cur;
      const keys = Array(Math.abs(d)).fill(d >= 0 ? "down" : "up").concat("enter");
      b.addEventListener("click", () => sendRespond(f, {keys}));
      opts.append(b);
    });
    respond.append(opts);
  }
  // The reply box is unconditional — idle, **Blocked** or working alike
  // (CONTEXT.md: Focus). Responding to a working Run is not a special case, so
  // nothing here is disabled; it just says where the text goes, once, next to
  // the box: Claude Code's native input queue absorbs it until the next turn
  // (CONTEXT.md: Respond).
  if (f.lane === "working") {
    respond.append(el("div", "queued",
      "⏳ busy — what you send queues until this turn ends"));
  }
  const row = el("div", "replyrow");
  const ti = el("input", "ti");
  ti.placeholder = isBlocked(f) ? "answer…"
    : f.lane === "working" ? "queue a note for the next turn…" : "type your reply…";
  const send = el("button", "send", "respond →");
  // Clear only once the text is actually sent. The box now survives rebuilds,
  // so clearing optimistically (or not at all) would carry a stale value back
  // in and make a sent reply look unsent.
  const fire = async () => {
    const v = ti.value.trim();
    if (v && await sendRespond(f, {text: v})) ti.value = "";
  };
  send.addEventListener("click", fire);
  ti.addEventListener("keydown", (e) => { if (e.key === "Enter") fire(); });
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
  dir.append(document.createTextNode(item.title || item.dir || "claude"));
  body.append(dir);
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
  head.append(el("span", "fgdir", item.title || item.dir || "claude"));
  head.append(el("span", "fgage", age(item.updatedAt)));
  const link = deepLink(item.bridge);
  if (link) {
    const a = el("a", "iconbtn", "↗");
    a.href = link; a.target = "_blank"; a.rel = "noopener";
    a.title = "open in the Claude app";
    head.append(a);
  }
  row.append(head);
  if (item.dir) row.append(el("div", "fgpath", item.dir));
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
  if (res.ok) watch(res.runId, item.title || "transfer");
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
  if (keep) restore(card, keep);
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
function grab(card) {
  const ti = card.querySelector(".ti");
  return {text: ti ? ti.value : "", start: ti ? ti.selectionStart : 0,
          end: ti ? ti.selectionEnd : 0, active: !!ti && ti === document.activeElement,
          scroll: window.scrollY || 0};
}

function restore(card, k) {
  window.scrollTo(0, k.scroll);
  const ti = card.querySelector(".ti");
  if (!ti) return;
  ti.value = k.text;
  if (k.active) {   // it had the keyboard up — give it straight back
    ti.focus();
    try { ti.setSelectionRange(k.start, k.end); } catch (e) {}
  }
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
const CHROME_STEP = 24;     // px of travel one way before it counts as a move

let chromeHid = false;
let chromeAnchor = 0;       // the scroll position the current run of travel began
                            // at, so the step is consecutive travel, not drift

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
}

function setChrome(hid, y) {
  chromeAnchor = y;
  if (hid === chromeHid) return;
  chromeHid = hid;
  applyChrome();
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
  const d = y - chromeAnchor;
  if (chromeHid ? d > CHROME_STEP : d < -CHROME_STEP) setChrome(!chromeHid, y);
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
  toast("→ " + ((it && (it.title || it.dir)) || "run"));
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
// precisely when the options appear. Re-measured on load, on resize, and on every
// render, because the card that carries the bar is rebuilt there.
function syncBarHeight() {
  if (!document.documentElement) return;
  const bar = focusWrap && focusWrap.querySelector && focusWrap.querySelector(".respond");
  const bh = bar && bar.getBoundingClientRect ? Math.round(bar.getBoundingClientRect().height) : 0;
  if (bh > 0) document.documentElement.style.setProperty("--barh", bh + "px");
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
