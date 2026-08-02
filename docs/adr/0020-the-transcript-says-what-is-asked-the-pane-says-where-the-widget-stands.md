# The transcript says what is asked; the pane says where the widget stands

An **Ask**'s content — every question, every option label, every option
*description*, `multiSelect` — is read from the pending `AskUserQuestion`
`tool_use` in the transcript, which carries all of it. The **rendered pane** is
demoted from a content fallback to a *position sensor*: which **Ask** of the
**Ask Set** is current, where the cursor sits, which options are toggled. One
Ask is on screen at a time, and each is answered against a freshly read pane
rather than a scripted keystroke run. This narrows
[ADR 0009](0009-blocked-focus-is-read-from-the-rendered-pane.md) and supersedes
neither it nor ADR 0014.

## Context

The Board rendered a two-question ask on a live `strength-log` Run and got it
wrong in five separate ways at once. The payload, verbatim:

```
ask      "Ticket 3 (detector) ... Keep them split or merge into one tracer bullet?"
cursor   0
options  ["Keep split (Recommended)", "Merge into one",
          "Fold into ticket 1 (Recommended)", "Keep separate"]
pendingInput  "←  ☐ Granularity  ☐ Expand/contract  ✔ Submit  →\n\nTicket 3 ..."
```

The last two options answer a question the page never showed. Worse, they do not
answer it: the pane's rows are `1. Keep split / 2. Merge into one / 3. Type
something. / 4. Chat about this`, so stepping the cursor by the button's index
sends you into the widget's *affordances*. Measured off the captured fixture:

```
[Keep split (Recommended)]         -> 0 x down + enter -> 'Keep split'        ✓
[Merge into one]                   -> 1 x down + enter -> 'Merge into one'    ✓
[Fold into ticket 1 (Recommended)] -> 2 x down + enter -> 'Type something.'   ✗
[Keep separate]                    -> 3 x down + enter -> 'Chat about this'   ✗
```

Not a mislabel — a wrong answer, sent silently, to a question that was never on
screen.

Underneath sat the real fault, and it is a rendering change nobody noticed.
`_parse_selector` was written against the iTerm-era widget, which put the
highlighted option's description in a box-drawn **side panel on the same rows**
as the labels — which is precisely what `_BOX_RE` exists to strip. The current
tmux renderer puts each description on the lines **below** its label. So option
lines are no longer contiguous, and the parser's whole "last contiguous run of
option lines" premise is void. The captured frame yields eight groups of one:

```
['Range moves to the exercise, keyed by side']          <- assistant PROSE
['Drop the plan-item range and stop training-prep ...'] <- assistant PROSE
['Detect an unreachable range']                         <- assistant PROSE
['Propose and accept the retune on the exercise card']  <- assistant PROSE
['Keep split (Recommended)']
['Merge into one']
['Type something.']
['Chat about this']
  -> _parse_selector takes the LAST group (1 option) and bails at <2
```

Two independent defects there: descriptions break contiguity, and `_OPT_RE`
cannot tell a menu row from a numbered list Claude *wrote*. `_parse_selector`
therefore returns `{}`, and the `cursor: 0` in the payload is not a reading —
it is the default, which happens to be right often enough to have hidden this.

A census over every transcript in `~/.claude/projects` sized the problem:

- **425 asks. 99 carry more than one question** — `{1: 326, 2: 66, 3: 25, 4: 8}`.
  Multi-question is a quarter of all asks, not an edge case.
- **30 questions are `multiSelect`.**
- **Option descriptions: median 175 chars, p90 285, max 525.** All discarded.
  The label alone frequently cannot decide the question — the description is
  where the reasoning is.

And the severity was worse than the reported bug. `_is_question_widget` tests
for the string `"add notes"`, an affordance the current renderer does not paint.
Confirmed against both a single- and a multi-question capture: it returns
`False` for **every** `AskUserQuestion`. With the selector parse also failing,
both guards at the `pending` assignment miss, `_pane_input` scrapes the widget
body out from between its framing rules, and the phone paints
**⚠ unsent text already in this session's input box** over the question itself —
next to a "clear the box" button that would fire hundreds of `BSpace` into a
live selector. That is the exact false ⚠ the code's own comment calls
structurally impossible. It has been firing on all 425, not the 99.

Two further facts settled by capture rather than assumption:

- **A pending `AskUserQuestion` *is* on disk.** ADR 0009 states it "frequently is
  **not** on disk yet". The blocked Run's `tool_use` row landed at
  `05:53:22.744Z` and sat there pending indefinitely, with only trailer rows
  after it. Claude Code appends the assistant row as it streams, so the structure
  is available essentially when the widget paints. The race ADR 0009 designed
  around is far narrower than it assumed — which is what makes demoting the pane
  affordable.
- **A single-question widget has no tab strip.** It renders a bare `☐ <header>`
  line; the `←  ☐ A  ☐ B  ✔ Submit  →` strip is the multi-question case. So the
  anchor cannot be the strip. It is the **checkbox header** — which is already
  what `_pane_question` anchors on, and `_pane_question` is the one pane parser
  that still works on both renderers. The fix reuses an anchor the codebase has.

## Decision

**The transcript is authoritative for content; the pane is authoritative for
position.** Nothing the tool sent is re-derived from pixels, and nothing about
where the widget is standing is guessed from the tool.

**One Ask on screen at a time.** `/api/board` keeps `ask` singular. The Ask Set
is modelled server-side and never drawn whole; the phone shows the current Ask
with its `header`, its options, and each option's description.

**The anchor is the checkbox header line, not the tab strip and not
contiguity.** Option rows are scanned in visual order below it. This one change
fixes the selector parse, replaces the stale `"add notes"` signature, and stops
`_OPT_RE` matching prose, because prose above the header is now out of scope.
The structured labels from disk cross-check the scan by prefix: when the pane
and the transcript disagree, the phone falls back rather than sending a
keystroke it cannot justify.

**Options carry their descriptions, and therefore leave the sticky bar.** The
chip row rode `.respond` (`position:sticky;bottom:0`); four options × 175 chars
is ~320px of sticky bar on an 844px phone. Options move into the card beside the
Ask, so the thing you read and the thing you tap are one target. `clearFloor`'s
landing must now clear the ask *and* its options — board.js's "the options are
already up" assumption dies with this.

**Answers are polled, never scripted.** A tap sends only that Ask's keystrokes;
the widget advances itself and the next poll re-reads the pane. `multiSelect` is
the same rule at finer grain: one tap is one toggle (`step + space`), the pane
paints the toggle state, and a separate `done` sends `Enter`. Nothing compounds
— the worst case is one wrong answer, never a scripted run of them, and there is
no partial progress on disk to reconcile against if it did.

**Free text routes through the widget's own `Type something.` row, and falls
back to `Esc`.** The composer currently sends `send-keys -l <text>` then a
separate `Enter` into a selector. Measured on a live probe Run — an external
driver replicating `respond_run`'s two calls against a real widget — the frame
before the text and the frame after it are **byte-identical**: eleven characters
produced no filter, no echo, no error, no trace. The `Enter` then selected the
highlighted row, and the tool returned `Option ALPHA` — an answer nobody chose.
So a considered reply typed into a box whose placeholder reads `answer…` is
silently discarded and replaced by whatever sits under a cursor the phone is not
even reading correctly. This is the worst of the seven defects, because it is
triggered by doing exactly what the UI invites. Routing through `Type something.`
makes an answer land as an answer.
When that row cannot be found, the phone sends `Esc` — cancelling the Ask Set
and dropping to the real input box — and says so on the button, because
cancelling a question is not answering it.

The governing constraint, stated because it decides the ties above: **nearly
everything must be drivable away from the desk.** That is why the composer is
not simply disabled while a widget owns the screen, even though disabling it is
the safe answer.

## Considered options

- **Render the whole Ask Set as a form and replay one keystroke script.** Better
  on a phone — you see both calls before choosing. Rejected: it drives a widget
  whose intermediate state is never re-read, so a single dropped or extra
  keystroke silently shifts everything after it, and nothing on disk records
  partial progress to detect it.
- **Answer everything through `Type something.` and never select.** A wrong
  keystroke becomes structurally impossible and `multiSelect` stops being a
  distinct path. Rejected as the default: it converts a structured answer into
  prose the receiving Claude must re-interpret. Kept as the free-text path.
- **Read `capture-pane -e` and parse the ANSI highlight.** Authoritative for both
  cursor and current tab — the current tab is marked by attributes that `-p`
  drops. Rejected as disproportionate: every existing pane parser (`_OPT_RE`,
  `_BOX_RE`, `_pane_input`, `_RULE_CHARS`) would have to strip escapes, a rewrite
  of the pane layer to recover one field that text-matching the question body
  recovers for free. The capture is kept as a fixture in case this is revisited.

## Consequences

- **ADR 0009's hybrid survives; its emphasis inverts.** "Prefer structured data,
  fall back to the pane" still holds. What changes is that the pane is no longer
  a *content* fallback — it answers a question the transcript structurally
  cannot. ADR 0009's "frequently not on disk yet" premise is corrected above.
- **`_is_question_widget`'s signature was a version-pinned string** and nothing
  told us when it stopped matching. The checkbox anchor is structural rather than
  a string an affordance happens to use, but the class of failure is what the
  fixtures below exist to catch next time.
- **Fixtures become captures, not literals.** `tests/fixtures/ask_multi.*` and
  `ask_single.*` are verbatim `capture-pane` output plus the transcript tail. The
  inline `_ASK_PANE` in `test_server.py` is the iTerm renderer frozen in a Python
  literal — it passed throughout, which is precisely how the renderer change went
  unseen. A capture whose exact whitespace is the thing under test does not belong
  in a hand-edited literal.
- **`CONTEXT.md` gains **Ask Set** and rewrites **Ask** and **Blocked**.**
  "MultiAsk" is recorded as a flagged ambiguity: it names the rare case (326 of
  425 asks hold one question) and would force every call site to branch on
  cardinality, which is the branch the term exists to remove.
- **The `Type something.` route was driven blind and had to be measured too.**
  Two facts, both established by a live probe on 2.1.220 and neither guessable:
  the highlighted row **is** the text buffer — the literal text types straight
  into it and the trailing `Enter` submits it, returning
  `The user answered: "<question>"="<your words>"` — while an `Enter` sent first
  to "open" the row instead **rejects the tool use** (`The user doesn't want to
  proceed with this tool use … STOP what you are doing`) and drops to the
  ordinary input box, which is the `Esc` outcome by another name. And the
  stepping keys must be **one `send-keys` call each**: batched as
  `send-keys Down Down Enter` the two Downs were silently dropped and the Enter
  answered the highlighted row — the wrong-answer table, reproduced by the fix
  for it. Both are recorded in `respond_run`, and both are why this ADR's own
  "capture before you edit" rule now extends to the keystrokes, not only the
  parse.
- **The one-tap answer now costs a poll per step.** A four-question Set is four
  round trips, and a three-tick `multiSelect` is four more. Accepted deliberately:
  the alternative is a script that cannot be verified between steps.
- **The generalisation became its own decision.**
  [ADR 0021](0021-the-renderer-is-unversioned-so-every-parse-fails-loudly.md) —
  the renderer is unversioned, so every parse must fail loudly. Four of the
  defects above were version-pinned assumptions that degraded *silently into an
  action*, which is a rule about all pane parsing rather than about the Ask, and
  it binds parsers this ADR never touched.

## Escape hatch

If the poll-per-step latency becomes the complaint, batch *within one Ask*
(toggles then submit) before batching *across* Asks — the Set boundary is where
verification is cheapest to keep. If the checkbox anchor breaks on a future
renderer, read `capture-pane -e` and anchor on the highlight attribute; the ANSI
fixture is already captured for exactly that. Do not restore option labels
sourced from the pane as the primary content path — the transcript carries the
descriptions and the pane truncates at terminal width.
