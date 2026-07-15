"use strict";
// Board client. Fetches GET /api/board and renders the curated round-robin.
// Everything is built with createElement + textContent (never innerHTML for
// data) — the ONE exception is the focus context, which the server renders as
// escape-first markdown HTML (see ADR 0006). That single field is greppable
// below as `.innerHTML =`.

const app = document.getElementById("app");
const summary = document.getElementById("summary");
const toastEl = document.getElementById("toast");

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
  // Stay on this session (don't let rotation swap it away) so you watch it go
  // busy and see the reply — otherwise a successful respond just looks like the
  // card jumped to someone else.
  pinned = f.sessionId;
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
  respond.append(actions);

  card.append(respond);
  return card;
}

function qrow(item) {
  const btn = el("button", "qrow " + (ROW_CLS[item.lane] || "lane-w"));
  btn.append(el("span", "qbadge", ROW_BADGE[item.lane] || ""));
  const dir = el("span", "qdir");
  if (item.pri === 0) { dir.append(el("span", "flag", "⚑ ")); }
  dir.append(document.createTextNode(item.title || item.dir || "claude"));
  btn.append(dir);
  btn.append(el("span", "qone", item.one || ""));
  btn.addEventListener("click", () => setPinned(item.sessionId));
  return btn;
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
  app.textContent = "";
  const c = data.counts || {};
  summary.textContent = "";
  const b = (n) => { const s = el("b", null, String(n)); return s; };
  summary.append(b(c.needYou || 0), document.createTextNode(" need you · "),
    b(c.watching || 0), document.createTextNode(" watching · "),
    b(c.dormant || 0), document.createTextNode(" dormant"));

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

function boardUrl() {
  return "api/board" + (pinned ? "?focus=" + encodeURIComponent(pinned) : "");
}

function setPinned(sid) {
  pinned = sid;
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
  if (!document.hidden) timer = setTimeout(poll, 4000);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else poll();
});

poll();
