# A stretch of tool calls is one entry, and one claude block holds the stretch

A contiguous run of tool calls becomes a single **Work** entry in the
**Scrollback**, carrying what each call was *doing* rather than only which tool
it was, collapsed to one line until tapped. A contiguous stretch of assistant
**turns** and Works renders as one `claude` block. `isMeta` rows — a skill's
injected body — are dropped, and the `<command-name>` row that was being
filtered is kept in their place.

## Context

The **Focus** showed a tool call as a dimmed chip bearing the tool's name:
`Bash`, `Read`. Two complaints, and measurement inverted which one mattered.

A census over 40 transcripts:

- **657 tool-carrying assistant rows. Every one holds exactly ONE `tool_use`,
  and ZERO of them also carry prose.** Claude Code emits one assistant row per
  call. So `tools` was always a one-element list, the `flex-wrap` chip row never
  wrapped, and there was nothing to cap or expand — a whole branch of the design
  closed on one query. It also meant CONTEXT.md's **Turn** ("one assistant
  reply, with the names of any tools that reply invoked") described something
  that does not occur.
- **5–8 of the last 14 entries of a live Session are tool-only.** One session's
  window read `user | ToolSearch | WebSearch | WebSearch | ToolSearch | WebFetch
  | WebFetch | write_note | note_action | assistant` — eight of ten slots spent
  on bare tool names.

That second number is the real finding, and it is not about pixels. ADR 0014
bought the Focus "what you said, what it did, and what it then said" and bounded
the cost at `_SCROLLBACK_TURNS`. Charging a slot per call meant *what it did*
was evicting the other two out of the window that ADR 0014's bound defines. The
chips were both uninformative and expensive, and fixing only the first —
attaching a detail to every chip — would have made the second strictly worse.

A prototype (branch `prototype/scrollback-tools`) put five renderings on a phone
behind `?variant=`, with ⓪ rendering `main`'s rules exactly so there was a
baseline to lose to, and a live counter reading out slots spent and prose
surviving. Ⓐ log (a line per call), Ⓑ roll-up (the run on one line), Ⓒ narration
(tool work as a sentence in the assistant's voice), Ⓓ weighted (a line for
writes, a count for reads). Ⓑ won; a sixth variant then chained the blocks and
won over Ⓑ alone.

## Decision

**A contiguous stretch of tool calls is one entry.** `_scrollback` returns
`{"role": "work", "calls": [{name, detail}, …], "n": 7}` beside its prose
entries. The boundaries are not a judgement call: since prose and calls never
share a row, a stretch is exactly the work between two things that were said.

**A call says what it was doing.** `_call_of` generalises `_approval_detail` —
which already answered this question for the six approvable tools, for the
**Ask** — to every tool a turn can invoke. `detail` is plain text set with
textContent; unlike a turn's prose it never goes through `_md_to_html`, so it
must never reach an HTML sink. That asymmetry is why it is a separate field
rather than more `html`.

**A stretch of assistant entries is one `claude` block.** Only a **turn** of
yours breaks it. Every assistant entry used to repay the caption and its 15px,
so "prose, work, prose, work, prose" spent five captions to say one thing:
Claude is still going. Across thirteen live Sessions the last 14 entries held
143 captions between them; chained, 29.

**Grouping is presentation; slots are the payload bound.** A chain costs
whatever its contents cost. Charging a chain one slot would empty ADR 0014's
bound of meaning — one chain can hold ten 4000-char turns — and that bound is
the condition a bigger `/api/board` body was accepted under.

**An `isMeta` row is dropped; the `<command-name>` row is kept.** A skill's
injected body is 2–7KB of instructions nobody typed, rendered until now as prose
you appear to have sent. `isMeta` is a structured flag, not a string match, and
the census found it carries nothing else. Dropping it alone would leave a bare
`/ship` with no trace at all, so the row that was being filtered as plumbing is
promoted to stand in its place — it is the one line that is true.

## Consequences

- **The bound is now four numbers, not two.** `_SCROLLBACK_TURNS` entries, each
  either `_TURN_MAX` of prose or `_RUN_CALLS` calls of `_CALL_MAX`. A Work's
  worst case (~19KB) sits under a prose turn's, which is what makes charging
  them the same slot honest. A stretch longer than `_RUN_CALLS` keeps its true
  `n` and drops the details of its oldest calls, so the count never lies about
  what happened.
- **The same 14 slots now reach further back.** Measured across the same
  sessions: `11 calls = 11 slots, 3 prose` becomes `26 calls in 7 slots, 7
  prose`. More tool work is visible *and* more prose is, because the tool work
  stopped paying by the call.
- **A Blocked Run's pending call appears in its Work as well as its Ask.** It is
  in the transcript, so it is the last call of the last stretch. ADR 0014 deleted
  an ask that repeated the last turn, so the difference is worth stating: a Work
  is collapsed until tapped, so on screen the command appears exactly once, in
  the Ask. Expanded, the Work is where it sits in the sequence.
- **Which Works are open is module state in the client, keyed by the stretch's
  first call.** The Focus card is rebuilt whenever its payload moves — every
  poll on a working Run — and a stretch that re-collapsed under the reader every
  four seconds would be unusable. A stretch past `_RUN_CALLS` that is *still
  growing* does shift its own first call and so collapses once; stretches that
  long are rare enough to accept it.
- **CONTEXT.md gains **Work** and rewrites **Turn**.** A Turn is now only
  something that was *said*. The naming near-miss is recorded there too: the
  obvious name for a stretch of calls is a "tool run", and **Run** is this
  glossary's central term.
- **ADR 0014's `tools` array is gone from the payload.** It was always length 1.
  Nothing outside `board.js` read it.

## Escape hatch

If the body becomes the problem, cut the four numbers — that is what they are
for, and ADR 0014's instruction not to split the scrollback onto a second
endpoint still stands. If a stretch needs to be readable without tapping, widen
the collapsed summary before giving calls their own slots back; the slot is the
expensive thing, not the line. If chains ever need to be the unit that is
*counted*, that is a change to what `/api/board` may cost and belongs in its own
ADR, not in this one's margin.
