# The renderer is unversioned, so every parse must fail loudly

The Claude Code TUI is an **unversioned external dependency** read by scraping a
**rendered pane**. It changes without notice and without a changelog, so the
design target is not "parse it correctly" — it is *fail loudly, and be cheap to
re-fit*. Three rules follow, and they bind every pane parser, not only the ones
[ADR 0020](0020-the-transcript-says-what-is-asked-the-pane-says-where-the-widget-stands.md)
touched.

## Context

ADR 0020 fixed seven defects in how an **Ask** reaches the phone. Fixing them is
not the lesson. The lesson is how they survived: **four version-pinned
assumptions, every one silent, every one in code whose own comments asserted it
was stable.**

- `"add notes"` — `_is_question_widget`'s signature, described in its docstring
  as "the stable signature". The renderer stopped painting it. The function
  returned `False` for *every* `AskUserQuestion` and nothing complained.
- **Descriptions in a box-drawn side panel** beside the labels — the renderer
  moved them *below* each label. `_parse_selector`'s contiguity premise returned
  `{}`, and `cursor` fell back to a hardcoded `0` that then drove keystrokes.
- **`☒` for an answered tab** — absent from `_CHECKBOX`, so an **Ask Set**
  mid-answer was invisible: the state the pane is in immediately after answering
  Ask 1 of every Set.
- **`[✔] ` / `[ ] ` toggle markers inside the label** — `_pane_widget`'s
  docstring asserted toggle state was unreadable. One capture disproved it.

Three of those four were found by *looking at a capture*, not by a failing test.
The suite was green throughout, because the only widget fixture in it was the
iTerm-era renderer frozen in a Python literal — it still passes, and it describes
a screen that no longer exists.

The failure mode is identical in all four: **a parse failure degraded silently
into an action.** Nothing raised, nothing logged, nothing was greyed out. A
defaulted `cursor` of `0` is the archetype — indistinguishable from a genuine
reading of row 0, and it is what turned "we could not read the screen" into
"send two Downs and Enter".

## Decision

**A parse failure may never produce an action.** No parser returns a
usable-looking default. A cursor that was not painted is `None`, never `0`,
because `0` is a legitimate reading and a defaulted `0` is a claim about the
screen that nobody made. Each way of refusing carries its own name — `no-pane`,
`no-widget`, `unmatched`, `pane-mismatch`, `no-cursor` — because a refusal you
cannot tell apart from another refusal is half a silent failure. Where two
sources *contradict* each other (the transcript says an `AskUserQuestion` is
pending; the captured pane shows no widget) that is written to stderr as well as
carried in the payload, deduped and bounded, worded so the reader can tell a race
from a re-fit: one line and quiet after is someone answering at the desk
mid-poll; every Run, or one Run forever, means the renderer has moved.

The rule shapes **APIs**, not just return values: an argument that can be got
wrong silently is the same defect as a defaulted cursor. The pane therefore
crosses into the Ask layer as one `_PaneRead` value and anything else raises —
prompted by a real mis-call during review that passed a raw pane where the
extracted question text was wanted and got back a plausible `unmatched` instead
of an error.

**One renderer vocabulary, in one place.** Every literal the TUI paints —
cursor glyphs, checkbox and tab glyphs, toggle markers, affordance labels, rule
characters, and the regexes derived from them — lives in a single marked
`RENDERER VOCABULARY` block with the re-fit procedure written beside it. What is
*structural* stays as logic and is deliberately not consolidated: the descending
numbering run that separates a menu row from a numbered line of prose, and the
checkbox-header anchor. A renderer that broke those needs new logic, not a new
literal, and blurring the two would hide which is which.

**Evidence before edits.** `tools/capture-widget.py` writes both frames (`-p`
and `-e`) and the matching transcript tail into `tests/fixtures/` in one command,
stamped with the Claude Code version read off the tmux window name — free at
capture time, and never a per-poll subprocess (ADR 0014's one-read-per-poll
discipline holds). Every capture then runs through every parser in a globbed
matrix, so **adding a capture extends coverage by existing**. That matrix, not a
person, is what catches the next `☒`.

## Consequences

- **The Board loses taps it used to offer wrongly.** An Ask Set whose cursor was
  not painted is readable and untappable, and one whose position the pane never
  named reports no position at all rather than "Ask 1 of 3"; `clear_input`
  refuses while a widget owns the screen, and on any frame that paints no input
  box, rather than firing its over-count margin of `BSpace` into a live
  selector; `/api/respond` stops asking whether to force a reply past a
  question it had mistaken for a draft. Fewer taps is the *point* — each one
  removed was a wrong answer waiting for a renderer change.
- **Fixtures are captures, and captures are cheap.** The iTerm-era `_ASK_PANE`
  literal stays as a regression fixture — it is the renderer that changed under
  us — but new evidence is never hand-typed. `idle_box` is the first *negative*
  capture: the branch that asserts we do not see a widget where there is none.
- **A re-fit is now a procedure rather than an investigation.** Capture, edit the
  constants, run the matrix. If that is not enough, the change was structural,
  which the vocabulary/logic split is designed to make obvious.
- **Version stamps make "which renderer is this?" answerable.** Every fixture
  records the version it was taken under; all of ADR 0020's were `2.1.220`.

## Escape hatch

If the stderr contradictions become noise, tighten the *wording* and the dedupe
before removing them — silence is the condition all four bugs needed. If a future
renderer defeats the checkbox anchor or the numbering run, that is the structural
case: read `capture-pane -e` and anchor on the highlight attribute, for which the
`.ansi` twin of every fixture is already on disk. Do not reintroduce a default
for an unread cursor; that single line is what ADR 0020's wrong answers were made
of.
