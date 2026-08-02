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
