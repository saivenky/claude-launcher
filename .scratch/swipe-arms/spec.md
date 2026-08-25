# The swipe arms before it commits

## The complaint

Two failures, one cause.

- **On a desktop, selecting text moves the Focus.** The Scrollback is prose you
  drag across to select. That drag is the same pointer stream the swipe reads
  (`web/board.js:2964-3000`), so a selection past 70px sideways lands you on a
  different Run.
- **On a phone, you cannot tell you are swiping until you let go.** The commit is
  a single arithmetic check at `pointerup`. Nothing on screen changes while the
  finger is down, so the gesture has no in-flight state to read and no way to
  abort — the first feedback (`flashEdge`, the toast) arrives after the Focus has
  already moved.

Both are the same design: the gesture is invisible until it has already happened.

## The shape

**The gesture is touch-only, it arms visibly, and it can be abandoned.**

1. **Touch pointers only.** `pointerdown` bails unless `pointerType === "touch"`.
   A mouse and a pen select text and never move the Focus. Desktop keeps the two
   routes it already has — a trackpad's `wheel` flick and ←/→ — so the design is
   still judgeable there, which is the reason the code gave for reading pointers
   in the first place. That reason is spent; the routes outlived it.
   Per-event, not per-device (`matchMedia("(pointer: coarse)")`): a touchscreen
   laptop must select with its mouse and swipe with a finger, and only the event
   knows which is on the glass.

2. **A live peek names where you are going.** A fixed pill below the header
   carries the destination Run's **Nickname** or **Workspace** and its state
   (**Blocked** / working / idle), sliding in from the edge you are moving
   toward, tracking horizontal travel.

3. **The read never moves.** The peek is a fixed overlay, like every cue at this
   edge. Dragging the Scrollback sideways would fight the vertical read and cost
   `touch-action` and pointer capture to arbitrate.

4. **Armed is a state, and it snaps.** Past the threshold the pill jumps to its
   resting position, flips to solid, and its label becomes `release → <name>`.
   Drag back under the threshold and it snaps out and disarms; release disarmed
   and nothing happened. The pill is a readout, not the thing under the finger,
   so the threshold reads better as a detent than as a colour change — and the
   motion is redundant with the colour, so it survives sunlight and colour
   blindness. It pairs with the haptic already available there.

5. **A refusal is visible from the first pixel.** With a reply half-typed
   (`replyEngaged`) the pill never arms: it says so and stays inert. A gesture
   that arms and then refuses at release is a lie.

6. **One landing readout.** `wheel` and ←/→ commit instantly and have no
   in-flight phase, so they flash the same pill on landing. `flashEdge`, `.edge`,
   `#edgel`/`#edger` go — they existed only because the drag had nothing else to
   say.

7. **The one-off hint stays.** `.swipehint` and `cl_swipe` teach that the gesture
   exists; the peek reports one in progress. Different jobs — the peek cannot
   teach a gesture you never start.

The thresholds do not move: `SWIPE_MIN` 70px, `SWIPE_BIAS` 1.8. They were
measured on a phone and are about not making the vertical read feel sticky.
Feedback fixes legibility, not the numbers.

## Verified on glass

`.scratch/prototypes/swipe-cue.html` (throwaway) put three cue shapes — an edge
strip, the peek pill, a filling bottom bar — against snap-vs-track arming on a
real phone. `peek / snap / workspace+state` won.

## Not in scope

No ADR. The claim being reversed ("POINTER events, never touch-only") lives in a
comment block in `web/board.js`, not in `docs/adr/` — no ADR has ever owned this
gesture. The correction goes where the claim is.
