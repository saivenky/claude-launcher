# 02 — README carries the AttSD identity

**What to build:** someone landing on the README meets AttSD, not
claude-launcher. The head is replaced with the agreed copy:

> # AttSD
> **Assistant to the Software Developer.** Not the assistant — *assistant to*.
> It doesn't write your code; it watches the ones that do, and taps you when
> they need a decision.
>
> Spawn, observe, and answer local Claude Code Runs from your phone.

No second hint beyond that copy, and no greppable giveaway anywhere in the repo — a literal reference in source is the
one thing that destroys the deniability the name is built on.

The rest of the README body speaks the vocabulary ticket 01 settled: no
`launcher`, and `AttSD server` where tmux's server is also in frame. Setup
instructions still describe the *current* runtime identifiers unless ticket 03
has already landed — do not invent names this ticket does not own.

**Blocked by:** 01 — the README must speak vocabulary that is already settled.

**Status:** landed — 1368a22

- [x] README head matches the agreed copy exactly
- [x] No occurrence of `launcher` (any case) remains in the README
- [x] No word naming the reference behind the name appears anywhere in the repo
- [x] Links to `CONTEXT.md` and ADRs still resolve
- [x] `python -m unittest discover -s tests` and `ruff check .` pass
