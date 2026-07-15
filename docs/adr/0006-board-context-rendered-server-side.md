# The Board renders context markdown server-side, and the client innerHTMLs it

The focus card's run-up **context** is the Session's last assistant message,
which is markdown (headers, tables, bold, code). `server.py` renders it to a
fixed, escaped HTML subset and ships it as one field, `contextHtml`; the client
assigns that field with `innerHTML`. This is a bounded, deliberate exception to
[ADR 0003](0003-launcher-page-runs-javascript.md)'s rule that the client never
parses HTML for data.

## Context

ADR 0003 bans `innerHTML` for data and builds every row with `createElement` +
`textContent`, because the payload carries `snippet` — arbitrary text from a
transcript whose schema we do not own — and `textContent` has no HTML-parsing
step, so no input can escape it. That guarantee is load-bearing and stays for
every *other* field.

The Board breaks the premise for one field only. "Answer with enough context"
requires *showing* the run-up, and that run-up is real markdown — a 2,000-char
message with a table and three headers is common (measured on live sessions).
Rendered as flat `textContent` it is an unreadable wall; the whole point of the
focus card is that it reads cleanly. Structure has to survive, so *something*
must turn markdown into DOM.

Two places could do it:

- **Client-side markdown → DOM**, built node-by-node (no `innerHTML`), keeping
  ADR 0003 intact. Costs a real markdown parser in `board.js` — the exact kind
  of hand-rolled complexity the no-build posture makes expensive to maintain,
  and it re-derives on every poll.
- **Server-side markdown → escaped HTML string**, `innerHTML`'d once. Reuses the
  Python renderer already written, keeps `board.js` tiny — but reintroduces
  `innerHTML` for a field sourced from an untrusted transcript.

## Decision

Render server-side, escape-first. `_md_to_html` `html.escape`s every text run
*before* emitting any markup, and emits only a fixed tag set (`h3`–`h6`, `p`,
`strong`, `code`, `ul`/`li`, `table`/`tr`/`th`/`td`). A `<script>` in a
transcript therefore arrives as the text `&lt;script&gt;`, never as an element —
so the `innerHTML` sink cannot execute it. The exception is confined to exactly
one assignment, greppable in `board.js` as the sole `.innerHTML =`; every other
field stays `textContent`.

The safety argument inverts ADR 0003's: there, escaping was rejected because it
was manual and per-field *forever*. Here it is one function over one field, and
it is escape-*first* (the default is escaped; markup is the whitelisted
exception), not escape-the-dangerous-bits (default raw). A new block type is
inert until explicitly rendered, so the failure mode is "shows as text," not
"executes."

## Consequences

- **`board.js` stays small.** No markdown parser on the client; the one heavy
  transform lives in Python where the transcript is already being read.
- **The `innerHTML` ban gains one audited exception, not a loophole.** It is a
  single line with a comment pointing here; a reviewer greps `innerHTML` and
  finds exactly one hit to reason about.
- **Renderer coverage is the new risk surface, not injection.** A markdown
  construct we don't handle degrades to a paragraph — ugly, never unsafe. The
  thing to test is escaping (a transcript containing `<`, `` ` ``, `|`), and
  that test is small and total.
- **Context ships pre-rendered in `/api/board`.** It is part of the ETag'd
  payload, so an unchanged focus Session still yields a stable ETag/304 — the
  render is not recomputed on the client and does not defeat caching.

## Escape hatch

If the Board ever needs richer markdown (nested lists, blockquotes, fenced
code with languages), grow `_md_to_html`'s whitelist — never switch to a
general HTML passthrough. The moment a real markdown library is warranted,
vendor one that emits a sanitized subset server-side; do not move rendering to
the client to get it.
