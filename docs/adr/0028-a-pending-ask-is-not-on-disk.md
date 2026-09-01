# A pending Ask is not on disk, so the pane is the only witness

On Claude Code 2.1.252 an `AskUserQuestion` that is *waiting for an answer* is
not in the Session's transcript. The `tool_use` row appears only once the Ask has
been answered, backdated to the moment it was raised. The Board therefore cannot
assume the pairing ADR 0020 is built on — a live widget and the `tool_use` that
raised it — is available at the moment it matters.

## Context

ADR 0020 reads an Ask from two sources and refuses to act when they disagree: the
transcript says *what* was asked (the structured questions and options), the pane
says *where the widget stands* (which Ask, which row the cursor is on). That
pairing is the whole design. `_ask_set` takes a pending `tool_use` and a
`_PaneRead` and reports `unmatched` rather than guessing when they cannot be
reconciled.

`ask_multi`'s fixture stanza recorded, in August, that the pending `tool_use`
*is* on disk — noting this contradicted ADR 0009, which had assumed it usually
was not. That was true of 2.1.220. It is not true of 2.1.252.

Measured on 2026-09-01 while re-capturing the fixtures. A probe Run raised an
Ask; the widget painted at 16:35:17 and stayed up. The transcript held no
`tool_use` for it — through two separate 90-second waits, with the file unchanged
at 50 rows. On answering at 16:37:59 the row appeared, carrying the timestamp
`16:35:17`. Read after the fact, the file gives no hint that it was ever absent:
the evidence of the gap is destroyed by the thing that closes it.

This is not a variant of ADR 0020's four bugs. Those were assumptions about how
the TUI *renders*, and a re-capture exposes them. This is an assumption about
when a file is *written*, and the file is self-healing — which is why it survived
a fixture suite built specifically to catch version-pinned assumptions.

## Considered options

**Treat the missing `tool_use` as a contradiction.** It is what `_ask_set`
already does — `unmatched`, no cursor, untappable. Rejected as the *intended*
behaviour: it makes every Ask untappable for as long as it is pending, which is
precisely the window the Board exists to serve. Correct as a refusal, useless as
a design.

**Read the questions off the pane alone when the transcript is silent.** The pane
carries the question text, the option labels and the cursor. Rejected for now: it
gives up the cross-check that ADR 0020 was written to add, and the failure it
guards against — a stale `tool_use` from the Ask you already answered sitting
under the widget for the one Claude raised next — is not hypothetical. Reaching
for it needs its own decision, with the discipline that replaces the pairing.

**Record the finding and keep the fixtures honest.** Chosen. The behaviour is an
external dependency's, unversioned and undocumented, and it may change back. What
must not happen is a fixture that quietly implies the pairing is observable when
it is not.

## Decision

- **The transcript is not a reliable witness to a *pending* Ask.** Any code or
  ADR that assumes it is must say which Claude Code version it was true of.
  ADR 0009's original assumption holds again; `ask_multi`'s stanza no longer does.
- **`ask_multi` and `ask_toggled` pair a live frame with a trimmed tail.** Frames
  captured with the widget up; the tail taken after answering and cut immediately
  after the `tool_use`, dropping the answer that arrived later. Every retained row
  is verbatim. The stanzas say so, because a reader who believes those tails were
  observed live would draw exactly the wrong conclusion from them.
- **`ask_single` is the one genuinely-live pairing** and is marked as such. It
  was captured in a window where the row happened to be on disk, which is
  evidence the behaviour is a race rather than a flat rule.

## Consequences

The fixture suite keeps testing reconciliation, and keeps doing it against real
frames, but two of its three transcript tails are now reconstructions of a state
that existed rather than recordings of it. That is a real weakening and it is
written down rather than smoothed over.

The Board's behaviour is unchanged by this ADR. On a pending Ask with no
transcript row the queue's one-liner reads what it can and `_ask_set` refuses to
emit keystrokes — a refusal, not a wrong answer. Whether that refusal is
acceptable in practice is the open question this ADR deliberately does not close.

## Escape hatch

If a later Claude Code writes the pending `tool_use` again, re-capture
`ask_multi` and `ask_toggled` live, delete the trim note from both stanzas, and
mark this ADR superseded with the version that fixed it. The check is one command:
raise an Ask, leave it up, and look for its `tool_use` in the Session's transcript
before answering.
