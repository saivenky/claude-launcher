# The read is a serif at a scale, and the phone picks the theme

The **Board**'s prose moves from the system UI sans to a **serif**, every type
size on the page moves from a hardcoded px to a `rem` behind one multiplier
(`--fs`, set to `1.25`), and the Board gains a **light** theme selected by
`prefers-color-scheme` and by nothing else — no toggle, no stored preference, no
JavaScript. This sits on top of
[ADR 0017](0017-the-scrollback-folds-into-exchanges.md), which made typography a
decision rather than a coat of paint, and supersedes only its choice of *face*.
Every colour literal in `board.html` becomes a token on the way, because that is
what a second theme requires.

## Context

The ask was two sentences: a light mode, and bigger, more legible type overall.
Neither is answerable from an armchair — "legible" is a property of a specific
face at a specific size on a specific phone in specific light, and no amount of
reasoning about it substitutes for looking. So the first move was not a design,
it was a **prototype**: `web/proto.html`, generated from `board.html`, serving
the real `board.js` against the real `/api/board`, with four knobs (theme, type
scale, prose face, density) and a loud dot to open them. Reachable over the
**Launcher transport** on the phone this tool exists for, against live **Runs**.

Building it forced the work that any themed page needs anyway. `board.html` had
a token block, but **34 colour literals leaked past it** — `#b9c4ce` inside
`.md`, `#4c5660` inside `.rl`, five `rgba(0,0,0,…)` scrims — and a literal in the
middle of a rule cannot be re-bound by a theme. It also had **17 distinct font
sizes** from 8.5px to 16px, half of them fractional, with no scale and no root
size, so "bigger" meant editing seventeen numbers and hoping.

## Considered options

**The face.** The prototype offered system-sans, Avenir, Verdana and a serif.
The serif won on sight. The follow-up wish was a *slab* serif, which was
rejected on a fact rather than a taste: **no mobile OS ships one**. A slab means
shipping a webfont — a binary asset, a route to serve it, and a flash of
fallback text on every load — into a UI that is deliberately two hot-served text
files with no build step (ADR 0005). The system serif gets most of the effect
for zero bytes.

**The stack.** Initially written Charter-first on the assumption of an iPhone.
The phone is **Android**, which ships none of Charter, Georgia, Palatino,
Baskerville, Hoefler Text or Times New Roman — its serif is Noto Serif. A
device-side probe was added to the prototype panel (width-comparison, not
`document.fonts.check`, which is unreliable for locally-installed families)
precisely because a stack falls through silently and a screenshot cannot tell
you which face you are looking at. Settled at `Charter, Georgia, "Noto Serif",
serif`: four deep, one per platform, no webfont.

**The scale.** 0.95 / 1.0 / 1.1 / 1.25 / 1.4 as a multiplier on a 16px root.
1.25 won. Implemented as `html{font-size:calc(16px * var(--fs))}` with every
size a rem, so the whole page's type is one number. It scales **type only** —
borders, radii, padding and the reading column stay px, because a scale that
moved those too is just the browser's zoom, which the reader already has.

**Density** was a knob (compact / normal / roomy) and `normal` won, so nothing
about line-height or vertical rhythm changed. It is recorded here because it was
genuinely offered and genuinely declined: legibility at these sizes is mostly
line-height, and it turned out the existing values were already right.

**The theme switch** was the one real argument. Three options: follow the
phone; a manual toggle; or both, with an override. The Board is **one screen**
with no settings surface, and its only drawer is **Intake** — the *create* side
of the Board (launch / resume / **recover** / **Task**). A theme control does not
belong there, and putting it there anyway would quietly redefine Intake as "the
settings sheet", spending a glossary term to save a swipe. `prefers-color-scheme`
costs no UI, no persisted state, no new term, and both themes stay two swipes
away in the phone's own quick settings — which is the switch you reach for in
sunlight regardless. It also costs no JavaScript, which matters more than it
looks: the server sends `script-src 'self'`, so the inline `<script>` that
normally stops a themed page flashing before its preference loads **is not
available here**. A media query resolves before first paint and cannot flash.

## Decision

- Prose is `--face`: `Charter, Georgia, "Noto Serif", serif`. The machine face
  (`--mono`) is unchanged, and so is ADR 0017's actual point — monospace still
  means *machine*, and the eye still tells a path from a sentence without
  reading either. A serif makes that split louder, not different: the UI sans
  was also the face of every button and label, so the read was wearing the
  chrome's clothes.
- `--fs: 1.25` over a 16px root; every font size on the page is a rem.
- Light is a `@media(prefers-color-scheme:light)` block that re-binds the
  tokens. Nothing else selects a theme.
- All 34 colour literals are tokens. The new names are the prose ramp
  (`--fg-max`, `--fg1..3`), the label gutter (`--lbl`, `--lbl-you`), the quiet
  hairlines (`--line2`, `--line3`), `--q-soft`, `--on-accent`, and the scrims.
- **The label gutter is `2.5rem`, not `40px`** — see below.

The light palette is not an inversion. The dark theme spends its contrast on a
few bright accents against near-black; inverted, that reads muddy. So the page
is a warm off-white rather than `#fff` (a full-screen white read is glare),
surfaces step *up* toward white, and every accent is darkened until it holds.
Amber is the one that could not survive unchanged — `#e6b450` on white is about
1.7:1 — so it becomes a dark amber and every dark-ink-on-amber fill inverts to
white-ink-on-dark-amber through `--on-accent`.

## Consequences

**The gutter had to become type-sized, and that is the finding worth keeping.**
At 1.25 the label `CLAUDE` wants 43.8px of a 40px track, and `.rl` never
declared `white-space:nowrap` — so it did not overflow, it **wrapped**, and the
**Record** stopped being three lines. That is the one thing a Record may not do:
the fixed shape is its whole value (ADR 0017), the reason it beat a prose gist,
and a label that wraps at one type size and not another destroys it silently. So
the two tracks of the gutter and the number column beside them are rem
(`2.5rem`, `0.85rem`) — sized by the type they hold, not by the layout around
them. They are 40px and 13px exactly at `--fs:1`, so this changed nothing until
the scale moved.

The general rule that falls out: **a px length that holds text is a bug once
there is a type scale.** These four were the only ones; a future one will not
announce itself either, and the way this was caught was measuring the rendered
page, not reading the stylesheet.

`CONTEXT.md`'s **Record** entry named the gutter "40px". It no longer does — the
glossary should not have been carrying a pixel value in the first place.

A reader who prefers a light Board but keeps the phone dark cannot have one. That
is the accepted cost of having no control, and it is reversible: the tokens are
already split, so a toggle is a `data-theme` attribute and a place to put it —
the second being the hard half, and the reason it was not done now.

Anything that hardcodes a colour or a px font size in `board.html` from here is a
regression, and it will be invisible until someone opens the other theme or moves
the scale.
