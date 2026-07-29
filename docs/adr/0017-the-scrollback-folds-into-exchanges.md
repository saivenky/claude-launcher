# The Scrollback folds into exchanges and lands you at the newest, not the oldest

The **Scrollback** stops being a flat chronological list of entries and becomes a
list of **exchanges** — one **turn** of yours plus everything Claude said and did
in reply — folded by distance from now, with a `NEWEST` **seam** the page lands
on so the live prose is on screen before the reader moves. Chronological order is
kept: oldest at the top, newest at the bottom. This sits on top of
[ADR 0014](0014-the-focus-is-a-scrollback.md) and
[ADR 0016](0016-a-stretch-of-tool-calls-is-one-entry.md) and supersedes neither.
Grouping, folding and landing are **presentation**, computed in the client from
the entries `/api/board` already sends; nothing here changes what that payload
may cost.

## Context

ADR 0014 bought the Focus a run-up and ADR 0016 stopped tool calls from eating
it. Both were about *what is in the window*. Neither asked what it is like to
read 14 entries of it on a phone.

Measured on a 390×844 phone over live transcripts: the Focus's scrollback is
**5064px tall — six screens** — and the page lands at `scrollY 0`, which is the
**oldest** entry in it. So every visit to a Run begins with a scroll to the
bottom, past everything already read, to reach the one thing being answered. The
user's complaint, verbatim: "no landmarks / can't skim", "density &
typography", and "I constantly am scrolling down to get to the most recent before
scrolling back if needed for context."

Three things are wrong there and they are not the same thing. *Landing* is where
the page opens. *Skimming* is whether the history has landmarks. *Density* is
whether the read is a wall — the baseline sets the whole scrollback in 13px
monospace at 1.6, which is what makes it read like a log file.

## Considered options

Six variants, built against live Runs on branch `prototype/scrollback-reading`
(`proto-a.js` … `proto-f.js` behind `?variant=`, `prototype-serve.py` overlaying
the real Board) and screenshotted at 390×844 over four fixture Sessions. Heights
below are the same fixture the baseline measures 5064px on.

- **Ⓐ land at the end.** Everything but the newest exchange folds to one 26px
  row; the page scrolls itself to a `newest` seam under the header. **1471px.**
  Fixes the landing and the height, and found the two hazards every later variant
  inherits — `renderFocus` empties the card before rebuilding it, so `scrollY` at
  render time is a lie, and any unfold above the read must be anchored.
- **Ⓑ sticky landmark rail.** A to-scale spine in the left gutter, one tick per
  landmark, a viewport thumb, and a permanent `↓`. **4649px** — it deliberately
  keeps the whole linear read and only makes you un-lost inside it. Rejected: it
  answers "landmarks" and "density" and leaves the six screens standing.
- **Ⓒ chapters.** A turn of yours opens a chapter; the newest is open, the rest
  are two-line cards. **1322px**, **844px** collapsed — the whole session as a
  table of contents. Established the unit. Rejected as shipped shape: the fold is
  flat, so the exchange you are standing in is folded exactly like one from
  yesterday.
- **Ⓓ chapters graded by distance.** Ⓒ's unit with Ⓐ's landing, and one rule for
  both: *how folded a thing is, is how far it is from now*. Seam landed at
  **248px**. Rejected on the row, not the geometry: a 26px prose gist has to guess
  whether to show your prompt or the reply, so its shape changes line to line and
  the column stops being skimmable.
- **Ⓔ inverted document, newest first**, history below as an `ask` / `did` / `got`
  ledger. Needs no landing at all — `scrollY 0` *is* the newest prose — and
  unfolding history can never displace the read. **Rejected by the user**: "I like
  E but the order and wording is very confusing." It also collided with board.js's
  chrome, which reads scrolling up as going back into history, and its `ask` label
  collided with this app's **Ask**.
- **Ⓕ = Ⓓ's order with Ⓔ's presentation.** The user's own instruction after
  seeing Ⓔ. **Ⓕ won.**

## Decision

Ⓕ, and `proto-f.js` is its specification.

**The unit is the exchange, and the grain gets finer as it approaches now.** An
exchange opens on something *you* did — a **turn** or a slash command — which is
exactly the boundary ADR 0016 already uses to break a `claude` block, so the
grouping introduces no new judgement and no new payload field. Distance 2+ (every
older exchange) is one **record**. Distance 1 (the run-up inside the exchange you
are standing in) is one gutter row per thing Claude said. Distance 0 (the live
tail) is full prose at full width.

**A record is a fixed three-line shape in one 40px label gutter, and the gutter
says WHO.** `you` — your prompt. `work` — what the exchange touched, the calls of
its **Works** run-length-encoded to their one-word labels with a `⚙n` count.
`claude` — the reply's first sentence, or, when that reply closed by putting a
question to *you*, that question in teal. The fixed shape is the point: one
column runs the whole fold and the eye can travel it without reading a value.
This is what beat Ⓓ, and it costs ~52px per record against Ⓓ's 26px — half as
many fit in the peek, paid knowingly for a column Ⓓ could not give at all.

**The labels are `you` / `work` / `claude`.** Two of the three are already in the
app's mouth: `work` is CONTEXT.md's term for a stretch of tool calls, and
`you` / `claude` are the captions the baseline already wore. Rejected:

- `asked` / `ran` / `replied`. `REPLIED` does not fit 40px — it wrapped and added
  ~32px to every row that had a reply, which is every row. `ASKED` and `ASKS`
  differ by two letters at 8.5px uppercase, so the one distinction the gutter must
  carry is the one it renders least legibly. And `asked` is this app's **Ask** in
  the past tense, where an Ask is a specific thing (a **Blocked** Run's blocker) —
  Ⓔ's mistake with a different suffix.
- `❯` / `⚙` / `↳` / `?`. Genuinely cheaper — ~30px back to the value column — and
  rejected anyway, because the gutter's job is to say *who* and a glyph cannot.
  `↳` reads as "the next thing", not "Claude", so a row stops being a small
  dialogue and becomes three anonymous facts.

**The order stays chronological, and the teal question is why that is not
nostalgia.** A record whose reply ended in a question to you is answered by the
very next row *down*. Numbering ascends from the top, `earlier` names what is
above, `↓ newest` names the direction of now: everything on the page points one
way. Ⓔ's inversion made that relation unreadable and the user said so.

**A `NEWEST` seam separates the fold from the live prose, and the page lands with
the seam 250px down.** Not hard against the sticky header: the peek above the
seam is load-bearing, because it is how a reader learns there *is* a fold and
that it is skimmable. Measured, on all four fixture Sessions: seam at exactly
**250px**, **535–575px of newest prose** below it at the landing, and at
`scrollY 0` the whole session — 4 records, the current prompt, the seam and 286px
of live prose — **fits one screen**.

**The landing fires once per scrollback, not once per poll.** A new entry
re-lands you only if you were still parked where the last landing left you. If
you scrolled up into history with a record half-read, you keep your place — an
auto-scroll that yanks a reader is the baseline's bug wearing the other mask. A
Run you swiped to has no place to keep, so it lands unconditionally.

**Density is part of the decision, not a coat of paint.** Prose moves to the
system UI face; monospace survives exactly where it *means* machine — the label
gutter, tool names, paths, counts, code — so the eye tells a path from a sentence
without reading either. The height comes from the fold; the read is not squeezed.

## Consequences

- **ADR 0014's bound and its one-payload rule are untouched.**
  `_SCROLLBACK_TURNS` entries, each clipped, on the `scrollback` array of
  `/api/board`. The fold *rearranges* those entries in the client; it does not ask
  for more of them and it does not fetch anything. If a later reader wants the fold
  to reach further back, that is a change to what `/api/board` may cost and belongs
  in its own ADR — exactly as ADR 0016 said of counting chains.
- **ADR 0016 survives whole, and the fold reframes its caption.** A stretch of
  tool calls is still one **Work** entry with one slot; a Work in the run-up still
  uses the one-line collapsible and its own open state, so there is one mechanism
  and not a second. What changes is the *chained `claude` block*: 0016 chained
  assistant entries to stop repaying a 15px caption per entry (143 captions → 29).
  The label gutter now carries who, once per row, above the seam, and below the
  seam the seam itself does — so the caption is suppressed entirely and 0016's
  economy is honoured rather than undone. The chain remains the payload-side
  grouping rule; it is no longer the thing the reader sees.
- **A record's `work` field folds across the whole exchange, not per Work.**
  Across five stretches `git` would otherwise be named three times, and saying it
  three times is the noise the field exists to remove. So the `⚙n` count and the
  labels come from different levels: `n` is the true number of calls, the labels
  are the distinct artifacts, capped at five with a `+k`.
- **Opening a record costs 0px of anchor drift.** Measured. Every unfold is
  anchored on its own node's top, and `read all` — which restores the full linear
  read, 1362px → 4557px — is anchored on the strip the button lives in, not on the
  seam, which would fire the reader to the bottom of a page that just quadrupled.
- **Every derived string is text, and the html sink stays exactly one.** A
  record's fields are produced by rendering a turn into a *detached* prose node and
  reading `.textContent`, then re-emitted through `textContent`. Tool names, call
  details and slash commands are untrusted transcript text on the same footing.
  ADR 0006's argument is unchanged and the number of `innerHTML` sinks does not
  grow.
- **Open state is module state in the client, keyed by content.** Same reason and
  same idiom as ADR 0016's `openRuns`: the card is rebuilt every poll and the
  scrollback is a sliding window, so an index would be worthless four seconds
  later.
- **CONTEXT.md gains **Exchange**, **Record**, **Fold** and **Seam**, and rewrites
  **Turn** and **Scrollback**.** Turn's `_Avoid_` list said "exchange (that is a
  pair)"; an Exchange is now a named thing, so that line is wrong and is replaced.
  The `asked`/**Ask** collision is recorded there as a flagged ambiguity, because
  it is the second time a design has reached for that word.
- **A short prompt is quoted, not titled.** "yes", "do it", "a" are *answers*, not
  subjects; the `claude` field carries such a row, which is the whole reason a
  record has three fields and not a headline. Ⓓ needed a special case for this; Ⓕ
  does not.

## Escape hatch

If the fold hides too much, raise the peek or move the distance grading — make
distance 2 a run-up rather than a record — before unfolding by default; the fold
is what buys the landing. If the landing ever fights a reader, tighten the
"parked" test that gates it before removing it; landing at the oldest entry is
the bug this ADR exists to fix. If the history needs to reach further than the
window, that is paging on a different surface, which is still what ADR 0014 said,
and not a bigger `scrollback`.

Do not invert the order. It was built, shipped to the phone, and rejected in the
user's own words; the teal question and its answer on the next row down are only
legible this way round.
