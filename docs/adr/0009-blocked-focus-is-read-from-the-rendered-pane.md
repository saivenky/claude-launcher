# A Blocked focus card is read hybrid — tool_use when flushed, the rendered pane otherwise

The focus card's **kind** (question vs approval), its **options**, its
**cursor**, and its **prompt** come from the AskUserQuestion tool_use when that
has flushed to the transcript, and from the *rendered pane* when it hasn't. In
the same spirit, `_lane_of` treats a `status:waiting` Run with **no pending
tool_use** as a **question**, not an **approval**.

## Context

A **Blocked** Run is either an **approval** (a permission prompt or plan
approval) or a **question** (AskUserQuestion) — see [CONTEXT.md](../../CONTEXT.md).
The two need different cards: an approval offers a fixed Yes / No menu, a
question offers the model's own labelled options.

The catch is *when the blocker reaches the transcript*. An approval always
leaves a **flushed pending tool_use** — the `Bash` / `Edit` / `ExitPlanMode`
the human is being asked to approve is already on disk. A pending
**AskUserQuestion** frequently is **not** on disk yet: like a permission
prompt, its widget is live on screen while its tool_use has not been written.
CONTEXT.md already names this — a Blocked Run's actual prompt "lives even when
it never reaches the transcript," in the rendered pane.

The old classifier read the transcript only: "pending AskUserQuestion tool_use →
question, else → approval." So an unflushed question fell through to
**approval**, and the focus enrichment then scraped the pane with a selector
regex that assumed a plain numbered menu. But the AskUserQuestion widget paints
the highlighted option's **description in a box-drawn side panel on the same
rows** as the labels. The result was a single card that lied four ways at once:
badge `approval`, option labels with box-art bleed (`Passive count only ┌───┐`),
an empty ask, and the whole widget shown as "⚠ unsent text already in the box"
because the input-box reader mistook the rule-framed widget for a typed draft.

## Decision

- **Classify by what a pending tool_use *would* be.** An approval always has one;
  a question often doesn't. So `waiting` + no pending tool_use → **question**.
  This costs zero extra pane reads (it is pure transcript logic) and fixes the
  off-focus badge too.
- **Prefer structured data, fall back to the pane.** When the AskUserQuestion
  tool_use has flushed, take options and the question from its `input`
  (clean, unambiguous). Otherwise parse them from the pane: cut each option
  label at the first box-drawing glyph, and read the prompt from the lines
  between the widget's checkbox header and its first option.
- **Only the last pane frame is live.** `contents of session` returns the
  scrollback, so a re-rendered widget appears several times; the parsers take
  the last contiguous option run and the last checkbox header.
- **A menu or widget owns the screen**, so there is no free-text input box —
  `pendingInput` is suppressed whenever a selector or the question widget is
  present, killing the false ⚠.

## Considered options

- **Wait for the tool_use to flush, transcript only.** Clean structured data,
  but the card stays blank or wrong for as long as the question is pending — and
  permission prompts *never* flush, so the pane is unavoidable for them anyway.
  Rejected: it can't cover the very cases that most need covering.
- **Pane only, always.** One code path, but it throws away clean structured
  labels when they exist and stays maximally exposed to TUI layout drift.
  Rejected in favour of the hybrid.
- **Read the pane for every Blocked Run.** Authoritative badges everywhere, but
  an AppleScript walk per Blocked Run every poll breaks the one-read-per-poll
  cost model. Rejected: off-focus rows keep the cheap coarse badge.

## Consequences

- Off-focus Blocked rows are labelled by the coarse classifier, which can only
  guess question-vs-approval; the focused card, read from the pane, is
  authoritative. A one-word badge on a queued row is a fair price.
- Pane parsing is coupled to Claude Code's TUI: the notes affordance
  (`add notes`) is the widget signature, the box-drawing range is the
  side-panel boundary, and the checkbox glyph anchors the prompt. A rendering
  change upstream can break these — mitigated by preferring the structured
  tool_use whenever it is present, so the fragile path is only the fallback.
- The "no pending tool_use → question" default will mislabel the rare *unflushed
  plan approval* as a question until its pane is read on focus. Acceptable, and
  called out here so it is not "fixed" back to `approval` without this context.
