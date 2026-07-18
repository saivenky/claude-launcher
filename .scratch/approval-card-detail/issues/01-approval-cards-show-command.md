# 01 — Approval cards show what's being approved

**What to build:** When a **Blocked** **Run** is an **approval** — a permission prompt for a Bash/Edit/Write tool call, or a plan approval — the **Board**'s focus card shows *what* you're being asked to approve, instead of the current "(no explicit question — your move)" placeholder with a bare Yes/No.

Today the card fills its "ask"/context from the last assistant turn's prose and only pulls a prompt out of a pending tool_use when that tool is `AskUserQuestion`. A Bash/Edit approval leaves a flushed pending tool_use with the concrete blocker (the command, the file, the plan), but the card never reads it — so you approve blind. Per ADR 0009 an approval *always* leaves a flushed pending tool_use, so this is structured transcript data, not a **rendered pane** scrape.

Surface that pending tool_use on the approval card, above the Yes/No, alongside the recent conversation context. The **question** lane (AskUserQuestion) and the `approval` lane badge are unchanged.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**Repro:** [`../repro.md`](../repro.md) — the exact live example (pane text + the empty payload), and a failing test (`tests/test_server.py::ApprovalFocusTests`, marked `@unittest.expectedFailure`) that goes green when this lands.

- [ ] A Run blocked on a Bash permission prompt shows the command on the focus card, not the "no explicit question" placeholder
- [ ] An Edit/Write approval shows the target file, with a concise summary of the change when one is available
- [ ] A plan approval (ExitPlanMode) shows the plan, or its summary
- [ ] The approval card also shows the recent conversation context (what the Run was doing) above the command, when that context exists
- [ ] Long commands, plans, or context are truncated so the card stays readable on a phone
- [ ] AskUserQuestion cards are unchanged — still their own prompt + options
- [ ] The lane badge stays `approval`; only the missing detail is added
