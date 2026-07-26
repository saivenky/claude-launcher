# The bottom edge is the reply box; Intake is a sheet behind a ＋ in the Focus's header

The docked **Intake** bar — the generic-dir launch row, and the **Recover** pill
above it — is deleted. Every shape of Intake (launch, **resume**, **Recover**,
**Task** / **Dispatch**) moves into one bottom sheet, opened by a ＋ in the
**Focus**'s sticky header at every width. `--dockh` goes to `0`, so the composer
*is* the bottom edge — and it becomes a one-row textarea that grows to five.

## Context

[ADR 0014](0014-the-focus-is-a-scrollback.md) measured the chrome around the read
and took the pixels back for the **Scrollback**. This is the same complaint one
layer down. At the live end of the read the bottom edge was three stacked bars:
the composer standing at `bottom:var(--dockh)`, the launch row under it, and —
whenever *any* **Session** was resumable — the Recover pill between them, plus
the safe-area inset the dock absorbed on its behalf.

Two of those three are **Intake**, the *create* half of the **Board**. The half
you are actually using is the other one: **Observe** and **Respond**, on work
already running. A create surface held the one strip that cannot be scrolled
away from, permanently, for a verb used a handful of times a day.

The Recover pill was the sharper case. `renderRecoverBadge` hid it on exactly one
condition — `recoverSessions.length === 0` — so it was on screen whenever
anything at all was resumable, which is nearly always. **Recover** answers a
restart, which is not.

## Considered options

Three variants, prototyped against live Runs on branch
`prototype/bottom-edge-intake` (`prototype-serve.py` overlays the real Board and
proxies `/api/*` to the running Launcher, POSTs stubbed). All three share the
sheet, the textarea, and the eviction of the pill; they disagree only about where
the one affordance that opens Intake lives.

- **A — ＋ in the composer row**: `[＋] [textarea] [respond →]`. One row at the
  bottom edge, but a shared one — a Board-level verb sitting inside the Focus's
  own reply bar, next to the button that answers a **Run**.
- **B — a floating ＋ above the composer**. Costs no layout at all; occludes the
  read instead. Invents a second entry idiom for a surface whose peer — the queue
  sheet — is entered from the card header.
- **C — ＋ in the Focus's sticky header**, beside the queue count. The bottom edge
  is the composer and nothing else.

## Decision

**C**, plus the composer changes the eviction makes room for:

- The textarea is **one row at rest** — pixel-identical to the old `<input>` —
  grows per keystroke, and caps at five rows before scrolling internally. A
  `rows=3` box would hand back most of what killing the dock just recovered, on
  every Run, including the ones you never type into.
- **Enter inserts a newline; ⌘/Ctrl+Enter sends.** A soft keyboard has no
  Shift+Enter, so the Slack idiom would make a second line untypeable on the
  phone this whole tool exists for. The send button is already in the row.
- **No Recover badge on the ＋.** After a machine restart no Run survives, so the
  Board *is* empty, Intake renders inline where the card would be, and the count
  is loud in exactly the case the pill was built for. Any other time a recovery
  set is non-empty it is the mtime heuristic ([ADR 0013](0013-recover-guesses-the-live-set.md))
  finding a cluster that was not a restart — a badge there would put a false
  positive back on the one strip you cannot scroll away from.

## Consequences

- **A Board-level verb now lives in a Run-level strip.** This is deliberate, and
  it is the one thing a future reader is most likely to try to "fix". The queue
  count set the precedent — that strip is already where the Board's *other*
  surface is entered — and the alternative was a second permanent layer of
  chrome. Intake is not a property of the Focus; it just borrows the only bar
  that is always on screen while you read.
- **The ＋ rides the chrome state, because `.fhead` does.** Scroll up into history
  and there is no way to open Intake without first tapping to bring the chrome
  back. Accepted: reading history is not when you launch, and the tap-anywhere
  escape hatch already exists.
- **The ＋ stays in `.fhead` at every width, unlike the queue count**, which is
  `display:none` above 900px because the rail draws the queue there. Nothing
  draws Intake in the rail, so there is no duplicate to avoid and no breakpoint
  fork to maintain.
- **`--barh` now moves per keystroke.** It was measured rather than guessed
  already (a **Blocked** Focus grows the bar by a row of options); the textarea
  makes that measurement continuous, and the swipe hint that stands on it follows.
- **The empty Board grew a second layout.** With no Focus there is no `.fhead`,
  so Intake renders inline and open above the card slot rather than as a sheet.
  This is the post-reboot screen, where Intake is the only thing you can do.

## Escape hatch

If Intake proves too buried, badge the ＋ before re-docking a bar — the count was
the pill's only information, and the bar was the whole cost.
