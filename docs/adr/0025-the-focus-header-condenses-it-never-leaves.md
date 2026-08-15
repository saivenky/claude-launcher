# The Focus header condenses; it never leaves

The **Focus**'s header no longer hides while you read. It condenses to a single
row carrying the **Workspace** alone — roughly 35px against the resting header's
~88px — and everything else in it yields. This narrows
[ADR 0023](0023-the-workspace-owns-the-headers-first-row.md): 0023 said the
Workspace owns the header's first row; this says that row is the one thing that
never leaves.

## The bug is the forcing function

`.fhead` is `position:sticky`, and **sticky is in flow**. The hidden state was

```css
.fhead.hid{transform:translateY(-110%);opacity:0}
```

which moves where the header *paints* and stops it painting, and takes not one
pixel of its ~88px box out of the layout. So hiding the chrome cost you the
Workspace and the status row and gave the read nothing back: a blank band
between the page header and the first line of the **Scrollback**. Intermittent
only in the sense that a Focus too short to scroll never hides at all, so the
band appears the first time you come back up a long one.

The sheet promised the opposite two lines above the rule:

> `.hid` slides each one out with a transform. That pairing is the whole point:
> hidden chrome must reserve NO layout

That promise is true of `.respond` and false of `.fhead`. `.respond` is
bottom-docked and genuinely out of the way when it translates; the header is
sticky and is not. **A rule that was right for one edge was applied to the
other**, and the class name being the same on both is what made it look like one
rule. So `.hid` now means two things deliberately, and the sheet says so: at the
bottom edge, reserve nothing; at the top edge, get smaller.

## The decision, and how it narrows ADR 0023

The header's rule is not *it reserves nothing*. It is that **it never leaves —
it gets smaller.** Condensed, `.fhead` is `.frow1` and nothing else: the lane
badge, the age, the sessionId, the queue pill, the ＋ and the session title all
`display:none`, the padding halves, `.fdir` drops to `0.75rem` and the strip
fades to `--fg3`.

Why the Workspace is the survivor is already in the glossary and the prototype
only confirmed it: wherever a **Run** is named the Workspace names it, and it
truncates last (`CONTEXT.md`, *Workspace*; ADR 0023). A header that vanishes is
the single treatment that breaks that rule on the surface where breaking it is
worst — a reader scrolled deep into a long **Scrollback**, several Runs live,
an approval one tap away, and nothing on screen saying which Run they are
answering. Approving a tool call is the one irreversible act on the **Board**.
Condensing spends ~35px to keep the answer to *where am I* on screen at all
times and still hands back the ~50px the hide was for.

`display:none` on the six dropped items is cheap for exactly the reason the old
rule got wrong: the header keeps its box either way, so nothing here is trying
to remove an element from a flow it never left.

**The session title moved out of its band and onto row one.** It was `.about`, a
full-bleed tinted stripe under the header treated as `.ask` is (ADR 0024) — and
it was quietly doing a second job. With the header two rows and the title a
third, "condense" would have dropped a stripe that was already reading as the
separator between the chrome and the read. Trailing the Workspace on row one
instead makes the resting header **one band**, so what condense drops is a
genuine second row.

Row one is now a node, `.frow1`, because it holds two things; `1 1 100%` moves
onto it, which is the job `.fdir` did alone under 0023. The flex inside it is
0023's rule in two declarations: `.fdir` is `0 1 auto` — sized by its content,
shrinkable only as a last resort — and `.fabout` is `1 1 0`, a zero basis, so
the title takes what is *left* and is the only item with anything to give back.
A long title ellipsises to nothing before the Workspace loses a character.

This recovered 42px above the Scrollback **even with the chrome up**. Measured
live on the phone: the Scrollback used to start at 216px; it now starts at 174px
full and 121px condensed.

## Alternatives considered

Four structural values were built into `.scratch/prototypes/focus-header.html`
and judged on a phone against a real Focus, not on a ruler.

**`pin` — never hide anything.** Cannot strand a gap, because nothing ever
hides. Pays ~88px of an 844px phone permanently for chrome you are not reading.
The safest answer and the most expensive one.

**`hide-collapse` — the minimal repair.** One rule: let the box collapse when it
hides and the gap is gone. Rejected because it makes the *fixed* version of
today's mistake permanent — scrolled into a read, nothing on screen says which
Run you are answering. It repairs the symptom and keeps the design error.

**`overlay` — take the header out of flow entirely.** `position:fixed` and a
translucent backdrop, so it can never reserve anything by construction. Two
costs: it needs a spacer that must collapse in step, which reproduces this bug
in a new place, and when the spacer does collapse the read slides up under the
finger. Floating chrome over prose also puts a blur behind serif text for the
entire scroll (ADR 0018 bought that serif deliberately).

## The consequence that matters most: the bug was damping a flicker

**Today's bug is the only reason today's Board does not judder.** A header that
collapses on scroll changes layout above the viewport; the browser's scroll
anchoring silently corrects `scrollY` to keep the visible content still; and
that correction is indistinguishable, to a scroll handler, from a finger moving
the same distance. So the handler that collapsed the header is handed its own
collapse back as travel, reverses itself, and the restore reads as travel the
other way. A slow drag parks you in the band where the strip flaps. Nothing
above the viewport ever resized while the hidden header kept its box — so the
bug hid a second bug, and any fix that collapses layout brings it out. Condense,
`hide-collapse` and `overlay` alike.

Three guards ship together:

1. **`overflow-anchor:none` on `html` and `body`** — both, because either can be
   the scrolling box depending on the engine. Believed to be the actual cure,
   and the one to keep if any is ever dropped. Nothing else on the page relies
   on anchoring: the **Fold** does its own anchor arithmetic precisely because
   it never trusted the browser's.
2. **No height-changing property in the header's transition.** The condense
   changes padding and a font size, and easing either reflows the page on every
   frame — a .2s collapse would feed the scroll handler ~200ms of size changes
   instead of one. The box snaps; only the fade to `--fg3` is animated.
3. **A settle window plus asymmetric thresholds in `syncChrome`.** After a
   toggle, the travel anchor is re-baselined on the *post*-layout frame rather
   than the pre-layout one, so the collapse's own scroll events are never read
   as travel (`CHROME_SETTLE_MS` is a fuse for a backgrounded tab, not the
   signal). And `CHROME_STEP = 24` in both directions was a deadzone a 25px
   jitter could round-trip forever; it becomes hysteresis — **28px of travel to
   hide, 64px to show** — so no wobble can cross both lines.

**They were not bisected, and that is unusual enough to say plainly.** A
headless harness could not reproduce the flicker at all: original logic, fixed
logic, and a control with the settle window disabled all scored one clean flip
over a simulated 360px drag, because programmatic scrolling produces no
momentum, no rubber-band and no anchoring correction. There was no rig that
could tell the three apart, so the symptom was confirmed gone by finger on a
phone and all three are carried. Accepted knowingly: the alternative was
shipping a guess about which one mattered, on the strength of a harness that
had already been shown blind to the phenomenon. A future reader who wants to
drop one needs a device, not a test run.

The ordering inside `syncChrome` is load-bearing for the same reason. The settle
window gates the *travel* reading only; `readingUp()` — is the end of the read
below the fold — is a position, is idempotent, can only ever show the chrome,
and so cannot oscillate against itself. It also cannot be tripped by the
collapse: condensing frees ~50px and `CHROME_SLACK` is 140.

## Knock-on: a header with two heights cannot be a literal

`HEAD_PAD = 52` was a hand copy of `.fhead`'s height, and it was already wrong —
the real header measures ~81px at `--fs:1` and ~88px at the default, so every
landing that used it parked ~30px short of the **Seam**. Condense made the
literal untenable rather than merely wrong: the header now has **two** heights,
and which one applies depends on the chrome state at the moment of the scroll,
not at the moment the card was built.

`headPad()` replaces it, reading `.fhead`'s live `getBoundingClientRect()`
height when a scroll actually fires. Verified live: 96px with the chrome up,
43px condensed, against the old literal 52. The *shape* of the landing pad —
`SEAM_PEEK`'s 250px design constant or the measured header — is still chosen at
build time and carried as a boolean; only its resolution to a pixel value is
deferred. `SEAM_PEEK` stays a constant, because it is a decision (ADR 0017) and
not a measurement.
