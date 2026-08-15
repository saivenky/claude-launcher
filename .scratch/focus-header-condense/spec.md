# The Focus header condenses; it never leaves

## The bug

`web/board.html:140`

```css
.fhead.hid{transform:translateY(-110%);opacity:0}
```

`.fhead` is `position:sticky`, which is **in flow**. The transform moves where it
paints and `opacity:0` stops it painting; neither takes its ~81–88px box out of
the layout. So when the chrome hides you lose the **Workspace** and the status
row and keep their space: a blank band between the page header and the first
line of the **Scrollback**.

The sheet promises the opposite two lines above, at `board.html:117`:

> `.hid` slides each one out with a transform. That pairing is the whole point:
> hidden chrome must reserve NO layout

That promise holds for `.respond` (`board.html:496`), which is bottom-docked, and
fails for `.fhead`, because sticky does not remove an element from flow the way
the comment assumes.

Why it reads as intermittent: `.hid` is driven by `board.js::syncChrome`
(`board.js:2340`) on scroll. A **Focus** whose read is too short to scroll never
hides its chrome and never shows the band. Scroll down far enough on a long one,
come back to the top, and it is there.

## The verdict

Judged on a phone against `.scratch/prototypes/focus-header.html`, four
structural values over one axis:

**`head=condense`, `density=airy`, `about=inline`.**

The hidden state is not an absent header. It is a single row carrying the
**Workspace** and nothing else — no badge, no age, no queue pill, no ＋, no
`.about` band — at `--fg3` and `0.75rem`, ~31px against the full header's ~81px.

### Why condense

The glossary already settled this and the prototype only confirmed it: wherever a
**Run** is named, the **Workspace** is what names it, and it truncates last
(`CONTEXT.md`, *Workspace*; ADR 0023). A header that hides completely is the one
treatment that breaks that rule on the surface where it matters most — the read
you are scrolled into, with several Runs live and one queue tap away. Condensing
spends ~31px to keep the answer to *where am I* on screen at all times, and
recovers the other ~50px, which is what the hide was for.

`about=inline` follows from the same cut: with the session title trailing the
Workspace on row one, the full header is one band rather than two, so what
condense drops is a genuine second row rather than a stripe that was already
doing separator duty.

### What the rejected values got wrong

- **`pin`** — never strands a gap, because nothing ever hides. Pays ~81px of a
  844px phone permanently for chrome you are not reading. The safest answer and
  the most expensive one.
- **`hide-collapse`** — the minimal repair: one rule, box collapses, gap gone.
  Rejected because it makes the fixed version of today's mistake permanent —
  scrolled into a read, nothing on screen says which **Run** you are answering.
- **`overlay`** — header out of flow entirely (`position:fixed` + translucent
  backdrop), so it can never reserve anything. Two costs: it needs a spacer that
  must collapse in step or it reproduces the bug in a new place, and when it does
  collapse the read slides up under the finger. Floating chrome over prose also
  puts a blur behind serif text for the whole scroll.

## The flicker, and what it means for the port

Collapsing the header changes layout, and a header that collapses on scroll can
drive the handler that collapsed it. The prototype flickered between condensed
and full on a slow drag until three changes went in together:

1. `overflow-anchor:none` on `html`/`body`. Scroll anchoring silently corrects
   `scrollY` when layout above the viewport resizes, and that correction is
   indistinguishable from a drag to the handler that caused it. Believed to be
   the actual cure.
2. Height removed from the header's `transition`. Animating a property that
   changes height reflows every frame, so a collapse fed the handler ~200ms of
   size changes instead of one.
3. A settle window after each toggle that re-baselines the reference scroll
   position once layout has stopped moving, plus asymmetric thresholds (28px of
   travel to hide, 64px to show).

**Not bisected.** All three shipped at once and the symptom went away. A headless
harness could not reproduce the flicker at all — original logic, fixed logic, and
a control with the settle guard disabled all scored one clean flip over a 360px
simulated drag — because programmatic scrolling produces no momentum, no
rubber-band, and no anchoring correction. So the three are carried into the port
together, and (1) is the one to keep if any is dropped.

Today's Board cannot flicker, and the reason is worth writing down: its hidden
header keeps its box, so nothing above the viewport ever resizes. **The bug is
what damps the oscillation.** Any fix that collapses layout — condense, collapse
or overlay alike — brings the flicker with it. Fixing `.fhead.hid` without also
handling scroll anchoring trades a visible gap for a visible judder.

## Also found

`board.js:1165` — `HEAD_PAD = 52` is a hand-maintained duplicate of the sticky
header's height, which measures ~81px at `--fs:1` and ~88px at the default. The
seam parking is already off by ~30px, and condense gives the header *two* heights
rather than one, so the constant cannot survive this change as a constant. See
issue `02`.
