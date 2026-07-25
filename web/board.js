"use strict";
// Board client. Fetches GET /api/board and renders the curated round-robin.
// Everything is built with createElement + textContent (never innerHTML for
// data) — the ONE exception is the focus context, which the server renders as
// escape-first markdown HTML (see ADR 0006). That single field is greppable
// below as `.innerHTML =`.
//
// Since ADR 0008 the Board is the Launcher's only page, so it also carries
// intake (dir-launch, resume, task/dispatch buttons — the bottom compose dock),
// the per-run close (×) and deep-link (↗), and the optimistic launch card.
// Below all of it, outside the triage surface, sit the Foreign Runs — seen,
// never driven, and transferable in one tap (foreignZone, ADR 0012).
//
// Rotation is consent-based (CONTEXT.md: Focus, Rotation). Two rules carry it,
// and between them nothing the Board does can cost you a half-typed reply or
// your place in the context:
//   1. You hold the Focus. It is adopted the first time the server hands one
//      over and pinned from then on, so urgency orders the *queue* and never
//      the card in front of you. It moves when you move it — or when it
//      resolves out from under you (advance-on-resolve, in render).
//   2. The Focus card is never rebuilt unless its own data moved, and a rebuild
//      carries the reply box and the context scroll across (renderFocus). One
//      ETag covers the whole board, so any other Run's churn redraws this page;
//      that redraw must not reach the card.

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
const dplusEl = document.getElementById("dplus");
const dockexpEl = document.getElementById("dockexp");
const tasksEl = document.getElementById("tasks");
const taskslblEl = document.getElementById("taskslbl");
const recoverbarEl = document.getElementById("recoverbar");
const recoverBtnEl = document.getElementById("recover");
const recovPanelEl = document.getElementById("recovpanel");
const recovTitleEl = document.getElementById("recovtitle");
const recovSubEl = document.getElementById("recovsub");
const recovListEl = document.getElementById("recovlist");
const recovGoEl = document.getElementById("recovgo");
const recovCloseEl = document.getElementById("recovclose");

// #app is split in two, once, up front: the Focus card gets its own container so
// redrawing the queue below can never touch it. That redraw is what used to eat
// a half-typed reply — see renderFocus().
const focusWrap = el("div");
const zonesWrap = el("div");
app.append(focusWrap, zonesWrap);

const LANE_LABEL = {question: "blocked · question", approval: "blocked · approval",
                    yourmove: "your move", working: "working", snoozed: "snoozed"};
const LANE_NOUN = {question: "waiting", approval: "waiting", yourmove: "idle",
                   working: "working", snoozed: "snoozed"};
const ROW_CLS = {question: "lane-q", approval: "lane-p", yourmove: "lane-m",
                 working: "lane-w", snoozed: "lane-w"};
const ROW_BADGE = {question: "question", approval: "approval", yourmove: "your move",
                   working: "working", snoozed: "snoozed"};

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

// --- Intake: compose dock + optimistic launch card -------------------------
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

function setDock(open) {
  dockexpEl.hidden = !open;
  dplusEl.setAttribute("aria-expanded", open ? "true" : "false");
}

// The launch input's quick-pick: the folders you've recently run Claude in
// (server-derived; ADR 0006). A native <datalist> is unreliable here — it
// won't open when its options arrive after focus (our fetch is async) and iOS
// Safari barely renders it — so we drive our own dropup popover with the same
// combobox semantics: free-text stays, we just show suggestions and fill on
// tap. Recent dirs change as you work, so we refetch on each focus.
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
  if (res.ok) { dirEl.value = ""; setDock(false); watch(res.runId, dir || "default"); }
  hidePop();
}

async function resumeSession() {
  const sessionId = sidEl.value.trim();
  const res = await postJSON("api/resume", {sessionId});
  toast(res.message || (res.ok ? "resumed" : "resume failed"));
  if (res.ok) { sidEl.value = ""; setDock(false); watch(res.runId, "resume"); }
}

async function launchTask(btn) {
  const t = btn._task;
  const body = {task: t.id};
  if (t.seedEl) body.input = t.seedEl.value.trim();
  const res = await postJSON("api/launch", body);
  toast(res.message || (res.ok ? "launched" : "launch failed"));
  if (res.ok) { if (t.seedEl) t.seedEl.value = ""; setDock(false); watch(res.runId, t.label); }
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

// --- Recover: the intake picker + empty-board count badge (slice 04) --------
// A discovery-and-bulk Resume (CONTEXT.md: Recover). GET /api/recoverable lists
// the Resumable Sessions newest-first and flags the recovery set (`preselect`);
// its `preselectCount` rides the intake pill as a nudge — the eye-catcher on the
// empty post-reboot Board. No auto-open, no auto-resume (ADR 0013, consent
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
  renderRecoverBadge();
  if (recoverOpen) renderRecoverList();   // keep an open picker current
}

// The pill is present whenever anything is resumable; it carries the count only
// when the recovery set is non-empty (`recover · N`), else reads plain `recover`
// and opens the full list (spec: uncounted-but-present).
function renderRecoverBadge() {
  const n = recoverSessions.length;
  recoverbarEl.hidden = n === 0;
  if (n === 0) { if (recoverOpen) closeRecover(); return; }
  recoverBtnEl.textContent = recoverPreselect > 0 ? ("recover · " + recoverPreselect) : "recover";
}

function openRecover() {
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

function focusCard(f) {
  const cls = f.lane === "question" ? "focus bq" : f.lane === "approval" ? "focus bp" : "focus";
  const card = el("div", cls);

  const head = el("div", "fhead");
  head.append(el("span", "fdir", f.title || f.dir || "claude"));
  head.append(el("span", "fbadge", LANE_LABEL[f.lane] || f.lane));
  head.append(el("span", "fmeta", (f.sessionId || "").slice(0, 8) + " · " +
    (LANE_NOUN[f.lane] || "") + " " + age(f.updatedAt)));
  card.append(head);

  if (f.aiTitle) {
    const about = el("div", "about");
    about.append(el("span", "albl", "session"));
    about.append(document.createTextNode(f.aiTitle));
    card.append(about);
  }

  const ctx = el("div", "ctx");
  ctx.innerHTML = f.contextHtml || "";   // server-escaped markdown — see ADR 0006
  card.append(ctx);

  const ask = el("div", "ask");
  ask.append(el("div", "lbl", "the ask"));
  ask.append(el("div", "qtext", f.ask || "(no explicit question — your move)"));
  card.append(ask);

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
  const row = el("div", "replyrow");
  const ti = el("input", "ti");
  ti.placeholder = "type your reply…";
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
  respond.append(actions);

  card.append(respond);
  return card;
}

function qrow(item) {
  // The row was one big <button>; it is now a div with a tap-to-focus body plus
  // separate action buttons (a button can't nest the × / ↗ buttons — ADR 0008).
  const row = el("div", "qrow " + (ROW_CLS[item.lane] || "lane-w"));
  const body = el("button", "qbody");
  body.append(el("span", "qbadge", ROW_BADGE[item.lane] || ""));
  const dir = el("span", "qdir");
  if (item.pri === 0) { dir.append(el("span", "flag", "⚑ ")); }
  dir.append(document.createTextNode(item.title || item.dir || "claude"));
  body.append(dir);
  body.append(el("span", "qone", item.one || ""));
  body.addEventListener("click", () => setPinned(item.sessionId));
  row.append(body);
  row.append(rowActions(item));
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
function foreignZone(items) {
  if (!items || !items.length) return;
  const h = el("div", "qhead");
  h.append(document.createTextNode("elsewhere · started by hand at the Mac"));
  h.append(el("span", "ct", String(items.length)));
  zonesWrap.append(h);
  zonesWrap.append(el("div", "fgnote",
    "seen, not driven — no reply box, no attach, no close. transfer one to end it and pick the session up here, or answer it at the Mac (↗ where the Claude app is bridged)."));
  const box = el("div", "fgbox");
  items.forEach((it) => box.append(frow(it)));
  zonesWrap.append(box);
}

function zone(label, items, count, dimmed) {
  if (!items || !items.length) return;
  const h = el("div", "qhead");
  h.append(document.createTextNode(label));
  h.append(el("span", "ct", String(count != null ? count : items.length)));
  zonesWrap.append(h);
  const box = el("div", dimmed ? "dim" : null);
  items.forEach((it) => box.append(qrow(it)));
  zonesWrap.append(box);
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
    focusWrap.append(el("div", "empty", "All clear — nothing needs you right now."));
    return;
  }
  const card = focusCard(f);
  focusWrap.append(card);
  if (keep) restore(card, keep);
}

// What a rebuild would otherwise cost you: the reply text, the caret, whether
// the keyboard is up, and where you had scrolled the context.
function grab(card) {
  const ti = card.querySelector(".ti");
  const ctx = card.querySelector(".ctx");
  return {text: ti ? ti.value : "", start: ti ? ti.selectionStart : 0,
          end: ti ? ti.selectionEnd : 0, active: !!ti && ti === document.activeElement,
          scroll: ctx ? ctx.scrollTop : 0};
}

function restore(card, k) {
  const ctx = card.querySelector(".ctx");
  if (ctx) ctx.scrollTop = k.scroll;
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

function render(data) {
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
  zonesWrap.textContent = "";
  zone("up next · curated round-robin", data.upnext, data.upnext.length);
  zone("snoozed", data.snoozed, data.snoozed.length, true);
  zone("watching · resurfaces when it needs you", data.watching, data.watching.length, true);
  zone("dormant · parked, still resumable", data.dormant, data.dormant.length, true);
  foreignZone(data.foreign);
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
  }
  schedule();
}

function schedule() {
  clearTimeout(timer);
  // Burst while a just-launched Run has not surfaced yet; steady otherwise.
  if (!document.hidden) timer = setTimeout(poll, pendingRuns.size ? 500 : 4000);
}

dplusEl.addEventListener("click", () => setDock(dockexpEl.hidden));
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

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else { poll(); loadTasks(); loadRecoverable(); }
});

loadTasks();
loadRecoverable();
poll();
