# The Workspace owns the header's first row

The **Focus**'s header is two rows at every width and every lane: the
**Workspace** alone on the first, the lane badge, age, queue count and ＋ wrapped
beneath it. The Workspace is also stamped on the **Ask**, and it is a named field
on the wire (`workspace`) rather than a `title` that held four kinds of string.

## Context

`.fhead` is a flex row of five things. Four of them — `.fbadge`, `.fsid`,
`.fmeta`, `.zbtn`, `.iplus` — carry `flex:0 0 auto`. `.fdir` did not. So `.fdir`
was the only item that could shrink, and every shortfall in the strip was paid
out of it, in full, by construction. On a 390px phone at this page's type scale
(a 20px root, ADR 0018) `claude-launcher` arrived as `claude-lau…`, and on the
**Blocked** lanes — whose badge read `blocked · question`, the widest of the five
— less than that.

This had been hit before. The comment in `focusCard` records the last round: a
`LANE_NOUN` beside the age was deleted, worth 55-63px, on the finding that
`.fdir` was "down to two characters". That bought pixels without touching which
item spends them, so the inversion survived the fix and came back the moment a
Workspace got longer or a badge got wider.

What it costs is not cosmetic. Two worktrees of one repo differ *only* in the
slug (`claude-launcher-scrollback-fold` vs `claude-launcher-recover-filter`), so
a head-first truncation keeps the part shared by every row and drops the only
part that tells them apart. The header was rendering two different Runs
identically, in the one field you read to decide whether you are in the right
place before you answer.

The same inversion, differently dressed, elsewhere:

- `.qdir` on a queue row had `white-space:nowrap` and **no** ellipsis, so an
  overlong Workspace was not truncated but clipped — no marker, just a name that
  stopped.
- The `.ask` block carried no Workspace at all. `syncChrome` slides `.fhead` out
  with `.hid` while you read, which is exactly the state a long **Scrollback**
  leaves you in when an approval arrives. Approving a tool call is the one
  irreversible act on the **Board**, and it could be done with nothing on screen
  saying which project it lands in.
- The **Recover** picker's `.recovdir` head-truncated a *path*, so five rows read
  `~/projects/.workt…` — identical prefixes, every discriminator gone.
- The payload called all of this `title`, a field that held the cwd's basename,
  or the tmux pane title, or the Session's first user message, depending. A term
  that exists in `CONTEXT.md` and nowhere in the payload is a term the next
  reader will not trust.

## Decision

**The Workspace takes the header's first row, alone, and the chrome wraps under
it.** Fixed at two rows — not "two rows when the name is long" — because the
header is `position:sticky` and the **Scrollback** scrolls *under* it. A header
that changes height reflows the read, which is the one thing `board.html`'s own
chrome block says it must never do.

Consequences, each following from the same rule (identity truncates last):

- `.qdir` gets `flex:0 0 auto` and a 62% cap; `.qone`, the snippet, shrinks
  first. The snippet is a hint; the Workspace is where you would be going.
- The `.ask` block carries a lane-coloured Workspace stamp.
- The lane badge says `question` / `approval`, not `blocked · question`.
  "Blocked" is the *state*, which the card's coloured top border already carries;
  `question` and `approval` are the two *shapes* of an **Ask**, which a colour
  cannot say. The other three lanes are unchanged — they were never the problem.
- Truncation is measured and middle-out, in two functions named for what they
  take. `elideWorkspace` is repo-biased: the first ten characters, then the tail
  whole, because the tail is the discriminator. `elidePath` drops whole
  *segments* — `~/…/claude-launcher-recover-filter` — because a path is a list,
  and eliding mid-segment produces something that reads as a typo rather than as
  a path with something missing. One function branching on `/` would be one
  function with two contracts, and neither caller is ever ambiguous.
- `title` becomes `workspace` on the wire, `None` when there is no cwd. A Run
  without a directory has no Workspace, and the pane title that used to fall into
  that slot read as a project name without being one.

**And a working Run can be the Focus.** `order` is `blocked + recent`, so a Board
whose Runs were all **working** had no head, no `focus`, and therefore no
`.fhead` — hence no queue pill (it lives in the header) and no swipe target (it
requires a Focus). A phone with several live Runs and no route to any of them,
while the ≥900px rail listed them all quite happily. The fallback now chains
`order → working → dormant → snoozed`, leaving `order` itself untouched so
`upnext` and `counts.needYou` still mean *wants you*. `CONTEXT.md` already said a
working Focus was legitimate; this is the one place that did not honour it.

## Alternatives considered

**Keep one row; make the badge yield instead.** Hide `.fbadge` at phone width on
the `bq`/`bp` lanes, where the card's top border is already teal or amber, and
dim the age. Prototyped side by side against this at 390px with a live
character-count. It works — but only on the two Blocked lanes, which is to say
only where the border happens to carry the lane. `WORKING` and `YOUR MOVE` have
no colour tell, keep their badges, and get nothing; a long Workspace on an idle
Run truncates exactly as before. It buys pixels conditionally and leaves the
structural rule — the name is the only thing that shrinks — standing for the
third time. Rejected on the prototype, on the case that started this.

**Ask git for the repo root** instead of taking the cwd's basename, so a nested
worktree layout (`.worktrees/<repo>/<slug>`) would still name the repo. A
subprocess per Run per board poll, to serve a directory layout this workspace
does not use. The dependency is written into `_workspace` and into the glossary
entry instead: flatten-the-slug is what makes the basename right, and a reader
who changes the convention needs the line that says this breaks.

**Swap the Recover picker's title for the Workspace**, demoting the opening
prompt. Prototyped; rejected. That row is picked by *which conversation was
that*, and the prompt answers it better than a repo name does — three rows from
one repo are told apart by what you asked, not by where. The path keeps its
second line and gets segment-wise truncation, which is what was actually broken.

**A permanent second line is 34px of sticky chrome on every Run**, including the
ones whose name fits with room to spare, and ADR 0015 argued hard for less
chrome. Accepted deliberately: the alternative is a header whose height depends
on its content, which reflows the read mid-scroll. This ADR exists so that a
future reader who wants those 34px back finds the reason before the ruler.
