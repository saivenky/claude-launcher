# The Board supersedes the inline launcher page

The **Board** (`web/board.html`, `web/board.js`, hot-served from disk per
[ADR 0005](0005-ui-hot-served-from-disk.md)) becomes the Launcher's *only*
page, served at `/`. The legacy inline launcher — `INDEX_HTML`, `APP_JS`,
`/app.js`, `/api/runs` — is deleted, and the Board absorbs everything it did:
the generic dir **launch**, **resume**, the one-tap **Task** / **Dispatch**
buttons (**intake**), close, and the deep-link handoff to the **Remote
Control bridge**. This is the "promote it to `/` and retire the inline page"
step [ADR 0005](0005-ui-hot-served-from-disk.md) named and deferred.

## Context

Two generations of the same page shipped side by side: the server-embedded
inline launcher at `/` (read via `/api/runs`, tasks string-replaced into the
HTML by `_render_index`) and the disk-served Board at `/board` (read via
`/api/board`). The Board is the one the owner actually lives on — it surfaces
**Blocked** Runs first, renders run-up context, and **Responds** inline — but
it could not *start* work or *close* a Run, so the old page could not be
retired. The daily hot path (measured by use) is dir-launch, closing Runs,
and deep-link handoffs — none of which the Board had.

Retiring `/` means every capability migrates or dies; none is being dropped.
Most of the migration is free under [ADR 0005](0005-ui-hot-served-from-disk.md)'s
thesis — a feature that only composes existing `/api/*` calls is UI-only:

- **close** — board items already carry `runId`; the client just POSTs `/api/close`.
- **launch / resume** — `/api/launch`, `/api/resume` already exist and return a
  `runId` for the optimistic row ([ADR 0003](0003-launcher-page-runs-javascript.md)).
- **deep-link** — a client-built `claude.ai/code/<bridge>` link.

Two capabilities are *not* free, and they are the substance of this decision.

**Tasks can no longer be inlined.** The inline page rendered the `tasks.py`
buttons server-side, string-replacing `{tasks}` into its HTML. A disk-served
static `board.html` has no such template step (that is the whole point of
[ADR 0005](0005-ui-hot-served-from-disk.md)) — so the task definitions must
reach the client as *data*, not baked markup.

**Deep-link needs one more field.** `/api/board` items dropped `bridge`, which
`cached_runs()` already computes; the client needs it to build the handoff URL.

## Considered options

- **Keep both pages.** The status quo. Rejected: it is exactly what this
  removes — two artifacts, two read-paths, the daily actions split across a
  page the owner does not live on.
- **Fold the task list into `/api/board`.** No new route; tasks ride the 4s
  poll. Rejected: it couples launch-intake *config* into the round-robin
  *read model*, and re-ships static task defs on every tick. The two have
  different lifecycles (tasks change only when `tasks.py` is edited) and
  belong on different clocks.
- **A new `GET /api/tasks` endpoint.** One route, fetched once on load (and on
  `visibilitychange`, to catch a `tasks.py` edit). Chosen. Costs one launcher
  restart to add — the restart [ADR 0005](0005-ui-hot-served-from-disk.md)
  explicitly budgets for a genuinely new capability, after which the UI
  iterates freely on top.

## Decision

- **`/` serves `board.html`.** `/board`, `/app.js`, and `/api/runs` are
  removed outright (no redirect — the bookmark set is one person's). The
  reachable GET surface is `/`, `/board.js`, `/api/board`, `/api/tasks`.
- **`_render_tasks` (HTML) becomes `_tasks_payload` (JSON).** It emits, per
  task, the input kind (`none` / `text` / `textarea`), placeholder, and its
  buttons (`id`, `label`) — the same data `_render_tasks` walked, now
  serialized instead of rendered. The client builds the buttons with
  `createElement` + `textContent`, so the placeholder-injection guard the HTML
  path needed is now structural, not an `html.escape` call.
- **`_board` items regain `bridge`.** Whitelisted server-side to
  `session_<alnum>` (already, in `_run_meta`); the client re-checks before it
  ever becomes an `href`, exactly as the inline page did.
- **Intake is a bottom compose bar; close and deep-link sit on every row.**
  Dir-launch stays visible (the hot path); resume and the task buttons fold
  behind a `＋`. Close is confirmed (`window.confirm`) because a mis-tap on a
  dense list ends a Run — recoverable by **resume**, but a confirm is cheap
  insurance on the most-frequent destructive action. `close` / `launch` /
  `resume` keep their network-trust-only posture
  ([ADR 0007](0007-respond-requires-auth.md)); only Respond / Clear are
  token-gated.

## Consequences

- **One page, one read-path.** `/api/board` + `/api/tasks` is the whole GET
  surface behind a single hot-served UI. The `/api/runs` machinery and its
  tests go with the inline page.
- **`board.js` grows the launcher's client logic** — the optimistic
  `starting…` card keyed by the returned `runId`, and the burst-poll that
  reconciles it ([ADR 0003](0003-launcher-page-runs-javascript.md) invariant
  4), now adapted from a flat list to the Board's lanes. A **Dispatch**
  returns no `runId`, so it paints no card — a toast is all the feedback there
  is (ADR 0004), and `watch()` already no-ops on a missing id.
- **The `innerHTML` ban still holds** ([ADR 0003](0003-launcher-page-runs-javascript.md),
  [ADR 0006](0006-board-context-rendered-server-side.md)): task labels,
  placeholders, and every new row enter the DOM as `textContent`. The sole
  `innerHTML` remains the server-escaped `contextHtml`.

## Supersedes

- [ADR 0003](0003-launcher-page-runs-javascript.md): "The root serves exactly
  `/` and `/app.js`" — root now serves `board.html`; `/app.js` is gone. The
  JSON-API + `Origin` + `textContent` invariants it established are unchanged
  and still carry the merged page.
- [ADR 0005](0005-ui-hot-served-from-disk.md): "The legacy launcher page
  (`/`, `APP_JS`) is untouched … promoting it to `/` and retiring the inline
  page is a later, separate step." This is that step.

## Escape hatch

If the task list ever needs to react to live state (a button that only appears
when some Run is blocked), fold it into `/api/board` then — the coupling this
rejects becomes the point, and the client already polls that payload. Until
then, intake config stays on its own endpoint and its own clock.
