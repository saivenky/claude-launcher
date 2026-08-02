# Fixtures

Verbatim captures, not authored test data — edit them and they stop being
evidence. The inline literals in `test_server.py` (`_ASK_PANE`, `_PERMISSION_PANE`)
stay where they are; a capture this size, whose exact whitespace is the thing
under test, does not belong in a Python literal.

## `ask_multi.*` — a two-question AskUserQuestion, tmux renderer

Captured 2026-08-02 from a live **Blocked** Run in `~/projects/strength-log`
(session `c6b5e741…`, pane `%59`), blocked on an `AskUserQuestion` carrying two
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

Captured 2026-08-02 from the `claude-launcher` session that wrote ADR 0020
(`ac51fb45…`). 326 of the 425 asks on disk hold one question, so this is the
**common** shape, not a degenerate one: no tab strip, a bare ` ☐ multiSelect`
header, three options.

- `ask_single.pane` / `.ansi` — the frame, `-p` and `-e`.
- `ask_single.jsonl` — the last five conversational rows, ending on the pending
  `tool_use`. Extracted verbatim from that session's transcript, not authored: a
  hand-written tool_use would agree with a hand-written pane by construction,
  and agreement between the two is the thing under test.

## `ask_toggled.*` — a multiSelect mid-answer, two rows ticked

Captured 2026-08-02 by driving a probe `AskUserQuestion` (`Space`, `Down`,
`Space`) in the same session and grabbing the frame. It is the only capture that
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
