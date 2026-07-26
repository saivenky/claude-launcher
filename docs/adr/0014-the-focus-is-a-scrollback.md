# The Focus shows a scrollback of turns, not the last assistant message

The **Focus** renders the last N **turns** of its **Session** — each one's prose
plus the names of the tools it invoked — instead of the single `contextHtml`
field [ADR 0006](0006-board-context-rendered-server-side.md) introduced. Every
turn is still rendered to escaped HTML server-side by `_md_to_html` and still
`innerHTML`'d, so ADR 0006's safety argument carries over unchanged; what
changes is how many fields it applies to, and what `/api/board` costs.

## Context

The Board was losing to the **Remote Control bridge** for reading. Measured on a
390×844 phone, the old focus card gave the message ~388px of a 844px viewport —
a `46vh` cap creating a *nested* scroll inside the page scroll — while the
chrome around it (header, session line, ask box, reply row, action row, dock
reserve) took the other half. Long run-ups were read in a letterbox.

Layout fixes recovered the pixels, and a prototype (branch
`prototype/focus-layout`) shipped four of them to a phone. But flipping between
them surfaced the deeper miss: a Run's last assistant message is not the thing
you need in order to answer it. You need what you said, what it did, and what it
then said — the same thing the Claude app shows. No amount of layout fixes one
message.

Two ways to get turns onto the page:

- **Client fetches a second endpoint** per Focus (what the prototype did, as
  `/api/proto/thread`). Keeps `/api/board` untouched and its ETag exactly as
  stable as it is today. Costs a second round trip on every Focus change, a
  second cache to invalidate, and a reading surface that arrives after the card
  it belongs to — visibly, on a phone.
- **`/api/board` carries the turns.** One payload, one ETag, the reading surface
  arrives with the Focus. Costs a bigger body and a weaker ETag: the payload now
  moves whenever *any* recent turn does, not only when the last one does.

## Decision

`/api/board` carries the turns, as a `scrollback` array on the focus object.

The ETag objection is smaller than it looks. The Focus's `updatedAt` already
moves on every new turn, so the payload was never stable across a working Run —
the 304s that matter are the ones on an *idle* Focus, and those still hold,
because an idle Session's tail does not change. What actually grows is body
size, and that is bounded on purpose: a fixed turn count, each turn clipped,
read from the same `_TAIL_WINDOW` tail `_ask_of` already reads. No new
file I/O — the tail is parsed once and yields both the scrollback and the
**Ask**.

The **Ask** stops being a property of the payload and becomes a property of
being **Blocked**. On an idle Run the old `ask` was the last `?`-terminated
sentence regexed out of the prose, which is now visibly the last turn on screen;
rendering it again was chrome that cost ~62px to repeat what the user had just
read. It is emitted only for the `question` and `approval` lanes, where it comes
off the **rendered pane** and is genuinely absent from the transcript
(ADR 0009).

## Consequences

- **ADR 0006's exception widens from one field to N, and its argument is the
  reason that is safe.** The escape-first render was never per-field — it is one
  function, `_md_to_html`, applied to untrusted text. Pointing it at fifteen
  strings instead of one adds no new sink. The `innerHTML` assignments in the
  client stay greppable and stay commented back to ADR 0006.
- **A Foreign Run could have a scrollback.** It is read from the transcript, not
  from a pane, so nothing about it requires a Managed Run. That is not a licence
  to give one a **Focus** — it still has no **Respond** and no **Ask**, and
  CONTEXT.md keeps it out of the triage surface. Noted so the next reader does
  not mistake the absence for a technical limit.
- **Tool-only turns are the common case and must render as something.** A
  working Run emits long stretches of `tool_use` with no prose; shown as blanks
  the scrollback looks broken. They render as dimmed tool-name chips.
  *(Superseded by [ADR 0016](0016-a-stretch-of-tool-calls-is-one-entry.md): the
  chips said nothing and, at one slot per call, took 5–8 of the 14 entries this
  ADR bounds — evicting the very prose it exists to show. A contiguous stretch
  is one **Work** entry now.)*
- **`/api/proto/thread` is prototype-only and does not ship.** It exists on the
  prototype branch to answer this question and dies with it; the real path is
  the `scrollback` field.

## Escape hatch

If body size becomes the problem, cut the turn count or the per-turn clip before
splitting the endpoint back out — a second round trip buys less than it costs,
and the split is what this ADR rejected. If the Board ever needs the *whole*
transcript rather than a tail, that is a different surface with different
paging, not a bigger `scrollback`.
