# The UI is hot-served from disk, decoupled from the server process

The **Board**'s UI (`web/board.html`, `web/board.js`) lives in files on disk
and is served *fresh on every request*. Editing a file and refreshing the
browser ships a new UI with no server restart. `server.py` becomes the
stable capability layer — the `/api/*` surface — behind a UI that changes
constantly.

## Context

Everything the page needs used to live inside `server.py` as string
constants: `INDEX_HTML`, `APP_JS`. That is what let the whole tool be one file
behind a launchd plist, and [ADR 0003](0003-launcher-page-runs-javascript.md)
leaned on it. But it couples the UI to the process: any pixel change is a
Python edit, which means a `launchctl unload/load` to take effect.

The declared goal for the expanded scope is a *rich UI whose features the
owner iterates on constantly* — "ship new HTML without relaunches." Session
*launching* stays declarative config (`tasks.py`, already hot-reloaded); the
new *session-management* surface (the Board) is built-to-spec and will change
far more often than the capability layer under it. Baking that surface into
the server would make every iteration a restart of a shell-spawning daemon.

The insight that makes the split safe: **the stable contract is the API, not
the UI.** A Board feature that only composes existing `/api/*` calls needs no
server change at all — so hot-serving its files is the entire cost of shipping
it. A feature that needs a *new capability* (a new endpoint) still costs one
restart to add the route; then the UI iterates freely on top again.

## Considered options

- **Keep the UI in `server.py` strings.** One file, no new surface — but every
  UI edit is a daemon restart. Rejected: it directly negates the stated goal.
- **A build step / bundler emitting the UI.** Real components, but a toolchain
  and a build artifact bolted onto a no-auth shell-spawner, and *still* a
  rebuild per change. Rejected for now; revisit only when a feature genuinely
  cannot be expressed in hand-written files (its own ADR).
- **Hot-serve static files from disk.** The files *are* the artifact; no build,
  no restart. Chosen.

## Decision

`server.py` serves a fixed whitelist of files (`web/board.html`,
`web/board.js`) read from disk on each `GET`, under the existing CSP. The name
set is a hardcoded map, so there is no user-supplied path to traverse. No
caching header games: the file is re-read every request, so a save is live on
the next refresh.

The Board's dynamic data rides `GET /api/board`, which the client polls (the
`/api/runs` machinery: chained `setTimeout`, ETag revalidation, paused while
hidden). The API is the contract; the files are the product.

## Consequences

- **Shipping a UI-only feature is now editing a file.** No restart, matching
  the goal. The launchd daemon restarts only when the *API* grows.
- **Two artifacts to keep coherent.** The client and `/api/board` share an
  implicit schema; a field rename touches both. Acceptable — it is the same
  discipline `/api/runs` and `app.js` already share.
- **Trust boundary unchanged.** The files sit on the user's Mac, same trust as
  `tasks.py`. Anyone who could rewrite `web/board.js` could already rewrite
  `server.py`; hot-serving adds no new writer.
- **The legacy launch page (`/`, `APP_JS`) is untouched.** The Board is
  additive at `/board`; promoting it to `/` and retiring the inline page is a
  later, separate step.

## Escape hatch

If the file set ever needs to grow open-ended (many partials, assets), replace
the whitelist with a single sandboxed static root (realpath + prefix check,
the pattern `resolve_dir` already uses). If a feature finally forces a build
step, that is a new ADR that supersedes the "no build" stance here — not a
quiet drift into a bundler.
