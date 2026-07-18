# Repro — approval card shows no command

Captured live on 2026-07-17: session `d4440820` (obsidian Run) blocked on a Bash
permission prompt. The board classified it `approval` but the focus card showed
`ask=""` and an empty context band — you approve blind.

## What the rendered pane actually showed

```
 Bash command
   for f in Notes/2026-07-1[2-7]*.md; do echo "=== $f ==="; wc -w "$f" | awk '{print $1" words"}'; done
   Word counts for recent notes
 Contains simple_expansion
 Do you want to proceed?
 ❯ 1. Yes
   2. No
 Esc to cancel · Tab to amend · ctrl+e to explain
```

## What `/api/board` returned for that focus (the bug)

```json
{
  "lane": "approval",
  "options": ["Yes", "No"],
  "cursor": 0,
  "ask": "",              // <- should describe the Bash command
  "contextHtml": "",      // <- should carry the recent conversation context
  "pendingInput": ""
}
```

## Root cause

`_full_context` builds `ask`/context from the last assistant turn's prose and
only reads options out of a pending tool_use when it is `AskUserQuestion`. Here
the last assistant turn is a bare `Bash` tool_use (no text), so all three come
back empty. The Bash tool_use *is* flushed to the transcript (ADR 0009 — an
approval always leaves a flushed pending tool_use), so the fix reads structured
data, not the pane.

## Failing test

`tests/test_server.py::ApprovalFocusTests::test_approval_card_shows_the_command_being_approved`
— marked `@unittest.expectedFailure`, using this exact command. It flips to an
unexpected success (i.e. the suite goes red) once the card surfaces the command;
remove the decorator then. `test_the_repro_really_is_an_approval` guards that the
fixture is genuinely an approval.
