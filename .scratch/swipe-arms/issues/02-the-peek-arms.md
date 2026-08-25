# 02 — the drag arms visibly, and can be abandoned

**Status:** landed — 3e71bfd
**Blocked by:** 01

Nothing on screen changes while the finger is down: the commit is one check at
`pointerup` (`web/board.js:2994-2999`). You cannot tell you are swiping until
you have swiped, and there is no way to back out.

Give the gesture an in-flight state and draw it.

**The peek.** A fixed pill under the header naming where you would land — the
destination's **Nickname** or **Workspace**, plus its state dot (**Blocked** /
working / idle). It slides in from the edge you are moving toward, its offset
and opacity tracking horizontal travel. Fixed overlay only: a cue may not move
the read (the rule `.edge` already obeyed).

**Armed snaps.** Past `SWIPE_MIN` with the `SWIPE_BIAS` ratio held, the pill
snaps to its resting position, goes solid, and reads `release → <name>`. Drag
back under the threshold and it snaps out and disarms. Release while disarmed
and nothing happened — the Focus only moves on an armed release. Fire the
existing haptic on the arming edge, once per arm.

**A refusal never arms.** With `replyEngaged()` true the pill appears saying the
reply is holding the Focus, and does not arm at any distance. The toast at
release stays for the paths that have no in-flight phase.

**One landing readout.** `wheel` and ←/→ commit instantly; flash the same pill
on landing. Delete `flashEdge`, `.edge`, `#edgel`, `#edger`, `edgeLEl`,
`edgeREl` and the CSS behind them — they existed only because the drag had
nothing else to say. The landing toast stays.

Thresholds unchanged: `SWIPE_MIN` 70, `SWIPE_BIAS` 1.8.
`.swipehint` and `cl_swipe` stay exactly as they are — teaching the gesture and
reporting one in progress are different jobs.

Shape confirmed on a phone: `.scratch/prototypes/swipe-cue.html`, preset
`peek / snap / workspace+state`.

## Acceptance

- A touch drag of 40px sideways draws the peek naming the next Run, unarmed;
  releasing there leaves the Focus alone.
- Past 70px the peek arms; releasing there moves the Focus.
- Armed, then dragged back to 30px, then released: the Focus does not move.
- A mostly-vertical drag draws no peek and moves nothing.
- With a reply half-typed the peek says so and never arms at any distance.
- `wheel` and ←/→ still move the Focus and flash the peek.
- No `.edge` rule, no `#edgel`/`#edger` node, and the suite no longer asserts on
  them.
- `.swipehint`, `cl_swipe` and `--barh` behave exactly as before.
