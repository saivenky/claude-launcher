# The theme is selectable, and the page header holds the switch

The **Board** gains a three-state theme control — `auto` / `light` / `dark` — in
the **page header**, remembered per device. `auto` follows the phone. This
supersedes [ADR 0018](0018-the-read-is-a-serif-at-a-scale-and-the-phone-picks-the-theme.md)
on theme *selection* only; its face, its type scale and its palettes are
untouched.

## Context

ADR 0018 decided the phone picks the theme and nothing else does, and gave three
reasons: no UI to place, no state to keep, no term to spend. The reasons were
sound and the conclusion was still wrong, for the one thing they did not weigh —
that the reader wants to choose. "There needs to be a toggle so I can manually
switch" closes that; the cost is what it always was, and it now has to be paid
rather than argued away.

Two things had to change to pay it.

**A media query cannot be overridden by an attribute.** ADR 0018's light block
was `@media(prefers-color-scheme:light)`. With three states, `auto` means "ask
the phone" while `light` and `dark` must beat the phone — and expressing that in
CSS alone needs the palette in two places (`:root:not([data-theme])` inside the
query, `:root[data-theme=light]` outside it). Two copies of eleven token lines
that must never drift is a worse bug than the one it avoids. So `theme.js`
resolves `auto` against `matchMedia` itself and always writes a concrete answer
to `data-theme`; the palette is stated once. This makes JavaScript load-bearing
for the theme, which is free — the Board is rendered entirely by `board.js` and
does not exist without it.

**The no-flash guarantee had to be re-earned.** ADR 0018 got it for nothing: a
media query resolves before first paint. A script does not, unless it runs in
the `<head>`, and the two obvious homes both fail — `board.js` loads at the end
of the body, and an inline `<script>` is dropped by the server's
`script-src 'self'`. Hence `web/theme.js`: its own file, in the head,
synchronous. Measured at 117ms against `domInteractive` 127ms, so the attribute
is set before the body is parsed.

## Considered options

Placement was the whole argument, because the Board is one screen with no
settings surface.

- **The Intake sheet.** Reachable from anywhere — the ＋ sits in the Focus's
  *sticky* header — but **Intake** is a glossary term meaning *starting new
  work*, and a preference living there turns it into "the settings sheet" by
  accretion. Would have needed `CONTEXT.md` to separate the sheet-as-container
  from Intake-as-concept.
- **The Focus's sticky header, as one cycling button.** Always visible, zero
  taps. Rejected on two counts: the header already carries six things at 390px
  (the `sessionId` is hidden below 560px to fit), and one glyph standing for
  three states cannot be read at a glance — you would tap to find out where you
  are. It is also *per-Run* chrome holding a *global* preference, and it does
  not exist on an empty Board.
- **The page header.** Chosen. It is the only strip that belongs to the Board
  rather than to a **Run**, which is exactly what a global preference is, and it
  costs the glossary nothing.

## Decision

The control is three buttons in `<header>`, beside `◆ claude board`. State lives
in `localStorage` under `cl_theme`: absent means auto, `light` and `dark` pin.
Every storage access is wrapped, following the `cl_swipe` precedent — reaching
`localStorage` at all throws where cookies are blocked, and this runs at load.

While in `auto` the control listens for `prefers-color-scheme` changing and
repaints; while pinned it ignores it, because the phone's switch is then not
ours to obey. Both mobile OSes flip that on a schedule, so it does fire.

## Consequences

**The switch is out of reach from where you actually stand.** The page header
scrolls away, and ADR 0017 lands the page on the **seam** — measured at 5064px,
six screens, into a real **Scrollback**. So changing the theme means scrolling to
the top of the document, and Android has no tap-the-status-bar shortcut for it.
This is known and accepted: the alternative was spending either a glossary term
or the sticky header's last free slot on a control touched when the light
changes and not otherwise. If it becomes a real irritation the fix is not to move
it into the read — it is to give the Board a settings surface, which it does not
have and does not yet need.

`auto` is the default and stays the default until touched, so nothing changes for
a reader who never finds the control.

The theme no longer degrades without JavaScript. Nothing on this page did
anyway.
