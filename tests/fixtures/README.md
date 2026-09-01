# Fixtures

Verbatim captures, not authored test data — edit them and they stop being
evidence. The inline literals in `test_server.py` (`_ASK_PANE`, `_PERMISSION_PANE`)
stay where they are; a capture this size, whose exact whitespace is the thing
under test, does not belong in a Python literal.

## Taking one

```
tools/capture-widget.py <name> --pane %59      # or --run <uuid> / --session <uuid>
tools/capture-widget.py --list                 # what is live, with its version
```

Writes `<name>.pane` (`capture-pane -p`, exactly what `_pane_contents` sees),
`<name>.ansi` (`-e`, the attributes `-p` drops) and `<name>.jsonl` (the last few
conversational transcript rows, ending on the pending `tool_use`), then appends
a stanza here with two TODOs to fill in. **Capture before you edit the renderer
vocabulary in `server.py`** — every one of ADR 0020's four version-pinned bugs
survived because the renderer was reasoned about instead of read.

Adding a capture extends the test matrix by existing:
`test_server.py::PaneFixtureMatrixTests` globs this directory and runs every
`.pane` through every pane parser. The name is load-bearing — `ask_*` MUST parse
as the AskUserQuestion widget, anything else must NOT.

## Version stamps

The Claude Code version is the tmux window name, so it is recorded with each
capture below. It is what makes "which renderer is this?" a line to read rather
than an excavation — the TUI is unversioned from our side and changes with no
changelog, which is the whole premise of ADR 0020.

## `ask_multi.*` — a two-question AskUserQuestion, tmux renderer

**Claude Code 2.1.220.** Captured 2026-08-02 from a live **Blocked** Run in
`~/projects/strength-log` (session `c6b5e741…`, pane `%59`), blocked on an
`AskUserQuestion` carrying two
questions (`Granularity`, `Expand/contract`). 99 of 425 asks across
`~/.claude/projects` carry more than one question, so this is the ordinary case,
not an exotic one.

- `ask_multi.pane` — `tmux capture-pane -p`, exactly what `_pane_contents` sees.
- `ask_multi.ansi` — the same frame with `-e`, kept because the current tab is
  marked by ANSI attributes that `-p` drops.
- `ask_multi.jsonl` — the last five conversational rows of the transcript,
  ending on the pending `tool_use`. Note it *is* on disk while pending, which
  ADR 0009 assumed was usually not the case.

What it reproduces: the pane's option lines are **not contiguous** in this
renderer — each option's description sits on the lines below its label, where
the iTerm renderer `_parse_selector` was written against put it in a side panel
on the *same* rows. So the "last contiguous run" premise yields eight one-element
groups, `_parse_selector` bails, and every option-driven path downstream falls
back to a cursor of 0 that nobody read.

Paired with its transcript it is also the **Ask Set** case: the payload must
carry q1's two options and *only* those. Concatenated, q2's two land under q1's
question and step the cursor into the widget's affordances (ADR 0020's
wrong-answer table).

## `ask_single.*` — a one-question AskUserQuestion, bare checkbox header

**Claude Code 2.1.252.** Re-captured 2026-09-01 from a probe Run in
`~/projects/attsd` (`8b210576…`, pane `%4`), replacing the 2.1.220 original
taken from the session that wrote ADR 0020. 326 of the 425 asks on disk hold
one question, so this is the
**common** shape, not a degenerate one: no tab strip, a bare ` ☐ multiSelect`
header, three options.

**What the re-capture found.** 2.1.252 draws the question inside a left gutter —
every wrapped line of it starts `│ `, where 2.1.220 drew it bare. The
transcript's structured question has never carried that glyph, so the rendered
prompt no longer matched the `tool_use` that raised it: every Ask read
`unmatched`, lost its cursor and went untappable. Live on the Board, and
invisible for as long as the fixtures stayed pinned to 2.1.220. `_GUTTER_RE` in
`server.py` strips it; this frame is the regression test.

- `ask_single.pane` / `.ansi` — the frame, `-p` and `-e`.
- `ask_single.jsonl` — the last five conversational rows, ending on the pending
  `tool_use`. Extracted verbatim from that session's transcript, not authored: a
  hand-written tool_use would agree with a hand-written pane by construction,
  and agreement between the two is the thing under test.

## `ask_toggled.*` — a multiSelect mid-answer, two rows ticked

**Claude Code 2.1.220.** Captured 2026-08-02 by driving a probe
`AskUserQuestion` (`Space`, `Down`, `Space`) in the same session
(`ac51fb45…`, pane `%64`) and grabbing the frame. It is the only capture that
shows a *ticked* row, and it corrects two things the earlier pair could not:

- The toggle box renders **inside the label** — `1. [✔] Row one`, `3. [ ] Row
  three`. Left on the label it fails the Ask Set's prefix cross-check against the
  structured `Row one`, so all 30 multiSelect asks on disk would go untappable.
- An **answered** question tab renders `☒`, which is the strip's state from Ask 2
  of a Set onward. Missing from the checkbox vocabulary the whole widget goes
  undetected and the false ⚠ unsent-text warning returns.

It also carries a cursor that is *not* on row 1 (it sits on `Row two`), so the
keystroke counts derived from it are signed — which no other capture exercises.
Its free-text row renders `Type something` with no full stop.

## `idle_box.*` — an ordinary idle frame, no widget, empty input box

**Claude Code 2.1.252.** Re-captured 2026-09-01 from a probe Run in
`~/projects/attsd` (pane `%4`), idle between turns.

- `idle_box.pane` / `.ansi` — the frame, `-p` and `-e`.
- No `.jsonl`: nothing about this frame depends on the transcript, and a
  transcript tail is verbatim conversation — not published without a reason.

The only NEGATIVE capture, and the matrix's negative branch runs on it alone: it
must NOT parse as a widget. Every widget assertion in this suite says "we can
still read the thing"; nothing said "we do not read it where it is not there",
and a `_HEADER_RE` that drifted loose — a tick of prose, a `✓` in a status line
— would suppress the real input box and the unsent-text read with it. It also
carries the two shapes those parsers must survive on an ordinary screen: a
status line full of glyphs, and an empty `❯` prompt between two rules.
