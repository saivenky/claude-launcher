# The launcher page runs JavaScript

The page becomes a small JS client: it `fetch`es a JSON **Run** list from
`GET /api/runs`, renders rows in the DOM, and posts lifecycle actions
without navigating. This reverses a deliberate zero-script posture
(`default-src 'none'`, every button a plain `<form method="post">`) on a
tool with no authentication that spawns shells.

## Context

Every action used to navigate. `POST /launch` returned `text/plain`
("launched in …"), stranding the phone on a bare text page; `POST /close`
was better only by accident (a `303` back to `/`). Errors were worse: a
`400` left you on a plain-text page you had to *back* out of. The goal is a
page you can live on — tap, see a toast, watch the Run list update in
place — which is a browser-side capability and therefore needs scripts.

Two facts constrain how far this can go:

- **`server.py` has zero third-party dependencies and no build step.** That
  is what lets it be a single file behind a launchd plist. A page that
  manages seven rows does not need a bundler.
- **The launcher is a shell-spawner with no app-auth** (see
  [ADR 0001](0001-tailscale-as-launcher-transport.md)). It leans entirely on
  a network boundary. Anything that widens the browser-side attack surface
  must pay for itself.

A **Run** is also eventually consistent: `launch_iterm` returns as soon as
AppleScript has typed the command into a new iTerm pane, but the pane is
filtered out of `list_sessions()` until `claude` appears in `ps` (1–3s).
Removing the navigation exposed this — you would tap **launch**, get a
toast, and watch an unchanged list.

## Considered options

- **Stay zero-JS; `303` every POST back to `/?msg=…`** and render the toast
  server-side. No CSP change, ~15 lines. Rejected: a full reload on every
  tap, and it forecloses **Observe**-style polling entirely.
- **Return HTML fragments; swap `innerHTML`.** Reuses `_render_sessions()`
  and its `html.escape` calls. Rejected: it forces `innerHTML` on a payload
  containing `snippet` — arbitrary text from a transcript whose schema we do
  not own — so XSS would depend forever on having escaped every field.
- **A separate BFF tier.** Rejected: `server.py` *is* the backend-for-
  frontend. A BFF on the Mac is `server.py` with more steps; a BFF anywhere
  else needs public ingress, which ADR 0001 rejected. Only aggregating
  multiple Macs would justify a tier, and there is one Mac.
- **Hash-pinned inline `<script>`** (`script-src 'sha256-…'`). Marginally
  stricter than `'self'`. Rejected: `_render_index()` string-replaces the
  template, so a `{tasks}` sequence or a `</script>` inside a JS literal
  silently breaks the hash. The browser then blocks the script and logs to
  a console unreachable from a phone. A mechanism that fails silently and
  remotely is not worth the marginal strictness.

## Decision

The page executes JavaScript, served from `GET /app.js` under
`script-src 'self'`. Lifecycle moves under one namespace — `GET /api/runs`,
`POST /api/launch`, `POST /api/resume`, `POST /api/close` — so the
content-type and `Origin` rules are one prefix check rather than a tuple
repeated per route. The root serves exactly `/` and `/app.js`. `runId` names
a **Run**; `sessionId` names a **Session**; the two UUIDs that both used to
arrive as `session_id` are now distinguishable on the wire.

Four invariants make this pay for itself:

**1. No time-varying presentation crosses the wire.** `/api/runs` returns
`updatedAt` as epoch ms, not `"47m"`. Relative time is formatted
client-side. This is not cosmetic: `_last_active()` made an idle Run's
payload mutate every minute, defeating both `ETag`/`304` and any "skip the
re-render if nothing changed" check. Raw timestamps also give a ticking
clock with no request at all. Time-*invariant* formatting stays on the
server — `dir` is still `~`-collapsed there, because the client does not
know `$HOME` and a home directory does not change between polls.

**2. The client never parses HTML.** Rows are built with `createElement` +
`textContent`. `innerHTML` is banned for data, and that ban is greppable.
This is a *stronger* guarantee than the `html.escape` it replaces: escaping
was correct but manual, per-field, forever; `textContent` has no HTML
parsing step, so no input can escape it. The one value that becomes
structure rather than text — `status`, which lands in a `st-{status}` class
and originates in Claude Code's `sessions/<pid>.json` — is whitelisted
server-side to `busy` / `waiting` / `idle` / `""`.

**3. CSRF gets stronger, not weaker.** With no `<form>`s left, POST bodies
are required to be `application/json`. That is not a CORS "simple request",
so a cross-origin `fetch` must preflight, and the preflight fails because no
CORS headers are sent. The `Origin` check also stops failing open on a
missing header. Structural, not header-checked. `form-action` leaves the
CSP; there are no forms.

**4. Spawn is eventually consistent; close is immediate.** The AppleScript
returns `id of current session`, so `POST /api/launch` and `POST /api/resume`
hand back a `runId`. The client paints an optimistic `starting…` row keyed by
it and burst-polls (~400ms) until a real row with that `id` arrives, giving
up after 10s with a `run failed to start` toast. `/api/close` needs none of
this. Polling is a chained `setTimeout` (never `setInterval`, which stacks
overlapping requests behind a slow cellular RTT), 4s while visible, paused
on `visibilitychange`, and `list_runs()` is memoized ~750ms so a burst poll,
a periodic poll, and a second tab collapse into one AppleScript walk.
Mutations invalidate that memo: a closed Run must vanish on the next poll,
not up to a TTL later.

Measurement showed a *second* consistency window nobody predicted. `claude`
reaches `ps` ~0.5s after launch, but writes `~/.claude/sessions/<pid>.json`
~0.5s after *that*. In between, a Run has no Session, no cwd, no `updatedAt`
— and its pane is still titled by the shell, so it rendered as a row called
`login` that sorted to the *bottom* of the list (no `updatedAt`), then jumped
to the top once its metadata landed. So a Run with no `sessionId` is reported
as `starting: true` and sorted first. The client draws it exactly like the
optimistic row, because they are the same fact — a Run that has not finished
starting — observed from two different distances.

The final CSP:

```
default-src 'none'; script-src 'self'; connect-src 'self';
style-src 'unsafe-inline'; base-uri 'none'
```

## Consequences

**Without JavaScript the page is inert.** There is no degraded mode,
because the degraded mode *was* the `<form>`s, and removing them is what
bought invariant 3. A `<noscript>` says so rather than leaving a dead page
unexplained. The residual risk is environmental — Lockdown Mode, a content
blocker — not a code defect, and recovery is `ssh` or the Mac itself. This
was accepted deliberately over progressive enhancement, which would have
kept the forms and the weaker `Origin`-only CSRF check.

Polling costs ~140ms of subprocess per request (84ms AppleScript walk over
every iTerm pane, 53ms `ps`), which is why the visibility pause and the
memo are load-bearing rather than polish: a tab left open would otherwise
fire ~900 Apple Events an hour into iTerm for nobody.

## Escape hatch

If per-row liveness (a spinner on the `busy` Run) makes whole-list
re-rendering feel blunt, diff rows by `id` — the payload is already keyed
for it. If polling ever becomes the bottleneck, `server.py` is a
`ThreadingHTTPServer`; an SSE endpoint holding a thread per client is a
small, additive change. Neither needs a new tier, a build step, or a
dependency.
