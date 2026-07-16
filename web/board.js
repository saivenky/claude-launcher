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

const app = document.getElementById("app");
const summary = document.getElementById("summary");
const toastEl = document.getElementById("toast");
const pendingEl = document.getElementById("pending");
const dirEl = document.getElementById("dir");
const sidEl = document.getElementById("sid");
const dirRootEl = document.getElementById("dirroot");
const launchEl = document.getElementById("launch");
const resumeEl = document.getElementById("resume");
const dplusEl = document.getElementById("dplus");
const dockexpEl = document.getElementById("dockexp");
const tasksEl = document.getElementById("tasks");
const taskslblEl = document.getElementById("taskslbl");

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

async function sendRespond(f, payload, force) {
  const token = getToken();
  if (!token) { toast("no token — respond cancelled"); return; }
  const body = Object.assign({runId: f.runId, token}, payload);
  if (force) body.force = true;
  let r;
  try {
    r = await fetch("api/respond", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
  } catch (e) { toast("respond unreachable"); return; }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { localStorage.removeItem("cl_token"); toast("token rejected — re-enter"); return; }
  if (r.status === 409) {   // the box already has unsent text — never blind-append
    if (window.confirm("This session already has unsent text:\n\n" + (data.existing || "") +
        "\n\nSend your reply anyway? It will be added below the above.")) {
      return sendRespond(f, payload, true);
    }
    toast("cancelled — clear the box on the Mac first");
    return;
  }
  if (!r.ok) { toast(data.message || ("respond failed (" + r.status + ")")); return; }
  toast("✓ sent — " + (f.title || "session") + " is now working");
  // Pin this session so rotation can't swap it away before you see your reply
  // land — but only until it flips to working. render() watches for that flip
  // and hands the focus to the next card automatically (no rotate click).
  pinned = f.sessionId;
  rotateWhenBusy = f.sessionId;
  etag = null;
  poll();
  setTimeout(() => { etag = null; poll(); }, 1500);   // catch the busy flip promptly
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

async function launchDir() {
  const dir = dirEl.value.trim();
  const res = await postJSON("api/launch", {dir});
  toast(res.message || (res.ok ? "launched" : "launch failed"));
  if (res.ok) { dirEl.value = ""; setDock(false); watch(res.runId, dir || "default"); }
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

function focusCard(f, nextSid) {
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
  const fire = () => { const v = ti.value.trim(); if (v) sendRespond(f, {text: v}); };
  send.addEventListener("click", fire);
  ti.addEventListener("keydown", (e) => { if (e.key === "Enter") fire(); });
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
  if (f.pinned) {
    const back = el("button", "ghost", "↩ rotation");
    back.addEventListener("click", () => setPinned(null));
    actions.append(back);
  }
  const snooze = el("button", "ghost", "snooze ▾");
  snooze.addEventListener("click", () => {
    const h = parseFloat(window.prompt("Snooze how many hours? (0 to un-snooze)", "1"));
    if (!isNaN(h)) postState("api/snooze", {sessionId: f.sessionId, minutes: Math.round(h * 60)},
      h > 0 ? "snoozed " + h + "h" : "un-snoozed");
  });
  const skip = el("button", "ghost", "skip →");
  skip.addEventListener("click", () => nextSid ? setPinned(nextSid) : toast("nothing up next"));
  actions.append(snooze, skip);
  // Per-run deep-link + close, mirroring the queued rows.
  const link = deepLink(f.bridge);
  if (link) {
    const open = el("a", "ghost", "open ↗");
    open.href = link; open.target = "_blank"; open.rel = "noopener";
    actions.append(open);
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

function zone(label, items, count, dimmed) {
  if (!items || !items.length) return;
  const h = el("div", "qhead");
  h.append(document.createTextNode(label));
  h.append(el("span", "ct", String(count != null ? count : items.length)));
  app.append(h);
  const box = el("div", dimmed ? "dim" : null);
  items.forEach((it) => box.append(qrow(it)));
  app.append(box);
}

function render(data) {
  reconcile(data);   // clear any optimistic card whose real Run has surfaced
  app.textContent = "";
  const c = data.counts || {};
  summary.textContent = "";
  const b = (n) => { const s = el("b", null, String(n)); return s; };
  summary.append(b(c.needYou || 0), document.createTextNode(" need you · "),
    b(c.watching || 0), document.createTextNode(" watching · "),
    b(c.dormant || 0), document.createTextNode(" dormant"));

  // Auto-rotate: after a respond the session stays pinned just long enough to
  // see it go busy, then we release the pin so the next card takes focus.
  if (rotateWhenBusy && data.focus &&
      data.focus.sessionId === rotateWhenBusy && data.focus.lane === "working") {
    const sid = rotateWhenBusy;
    rotateWhenBusy = null;
    setTimeout(() => { if (pinned === sid) setPinned(null); }, 1200);
  }

  if (data.focus) {
    app.append(focusCard(data.focus, (data.upnext[0] || {}).sessionId));
  } else {
    const e = el("div", "empty", "All clear — nothing needs you right now.");
    app.append(e);
  }
  zone("up next · curated round-robin", data.upnext, data.upnext.length);
  zone("snoozed", data.snoozed, data.snoozed.length, true);
  zone("watching · resurfaces when it needs you", data.watching, data.watching.length, true);
  zone("dormant · parked, still resumable", data.dormant, data.dormant.length, true);
}

// --- polling: chained setTimeout, ETag revalidate, paused when hidden -------
let etag = null;
let timer = null;
let pinned = null;   // a tapped row becomes the sticky focus until cleared
let rotateWhenBusy = null;   // a just-responded session: release the pin once it goes busy

function boardUrl() {
  return "api/board" + (pinned ? "?focus=" + encodeURIComponent(pinned) : "");
}

function setPinned(sid) {
  pinned = sid;
  rotateWhenBusy = null;   // any manual pin/unpin cancels a pending auto-rotate
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
dirEl.addEventListener("keydown", (e) => { if (e.key === "Enter") launchDir(); });
sidEl.addEventListener("keydown", (e) => { if (e.key === "Enter") resumeSession(); });

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else { poll(); loadTasks(); }
});

loadTasks();
poll();
