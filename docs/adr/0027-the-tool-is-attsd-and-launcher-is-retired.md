# The tool is AttSD, and `Launcher` is retired with it

The project is **AttSD** — `attsd` in anything machine-readable, `AttSD` in
prose. The glossary term `Launcher` is retired in the same act, because the
rename is what killed it: the word named the project *and* one of its parts, and
only one of those two jobs survives a rename. Its work splits between the
**Board** (the page you drive it from, already a term) and the **server** (the
process on the Mac), written **AttSD server** wherever tmux's own server is also
in play. Prose in earlier ADRs is rewritten to the new vocabulary rather than
preserved as written.

## Context

`claude-launcher` described the tool it was on day one and stopped describing it
somewhere around ADR 0007. Launching is now one of four shapes of **Intake**
(ADR 0008), and the tool's centre of gravity is triage: **Observe**, **Respond**,
**Recover**, **Transfer**, the **Focus** and its **Scrollback**. A name that
points at the smallest verb in the glossary is a name that argues with the
glossary.

`Launcher` had a second, sharper problem, and it is why the two decisions are
one. Its glossary entry read "the server that spawns, lists, and closes local
Claude Code **Runs**, and the page used to drive it" — one term for two things,
carrying an `_Avoid_: server` line to keep the process from being named
directly. That was defensible while the page was nameless. It stopped being
defensible at ADR 0008, when the page became the **Board** and got an entry of
its own; from then on `Launcher` meant the process in some sentences, the page in
others, and the project in the repo name. Renaming the project takes the third
job away and leaves a term doing two.

The `_Avoid_: server` line was itself the wrong call, made for a real reason.
The ambiguity it was dodging is tmux's server — genuine, but narrow, and it
appears in perhaps six passages across the whole corpus.

## Considered options

**Rename the project, keep `Launcher` as the term for the process.** The
smallest change. Rejected: it leaves the glossary's most-used term naming
something with a different name on its own repo, and leaves the process/page
conflation untouched. The conflation is the actual defect; the rename is only
what exposed it.

**Retire `Launcher` in favour of a new coined term.** Rejected: the two things it
named already have good names. The page is the **Board**. The process is a
server, which is what every sentence about it already wanted to say before the
`_Avoid_` line stopped it. A coinage here buys nothing but a fourth word for the
reader to learn.

**Keep `server` on the `_Avoid_` list and disambiguate with a coined word.**
Rejected on cost: the tmux-server collision is real in a handful of passages, and
spelling it out (**AttSD server** vs **tmux server**) resolves those passages
exactly where the confusion could occur, at no cost to the hundreds of sentences
where there is only one server in the room.

**Preserve the ADR record verbatim, and note the rename in this ADR alone.**
This is the honest alternative and it was rejected, not overlooked. Its case is
strong: an ADR is dated, and rewriting a 2025 document to use a 2026 name makes
it assert something that was not true when it was written. Against it: these ADRs
are read as the current explanation of why the system is shaped as it is, not as
an archive — CONTEXT.md and the ADRs are one corpus, cross-referenced in both
directions, and a corpus half in one vocabulary and half in the other makes every
reader ask which term is live before they can read the argument. Ten ADRs is a
tax charged on every future read. Git history preserves the original text
exactly, which is the archive the verbatim argument actually wants.

## Decision

**`AttSD` in prose, `attsd` in anything machine-readable** — repo, tmux socket,
launchd label, log file, env prefix. The expansion is printed once, at the top of
the README, and nowhere else; the reference it plays on is not named in this repo
at all. `CONTEXT.md` stays flat: a glossary defines words, it does not tell them.
Environment variables uppercase to `ATTSD_*` — shell convention outranks the
capitalisation of the name.

**`Launcher` is retired as a term.** Its entry is replaced by **Server**, and the
page half of its old definition is handed to the **Board**, which already held
it in practice.

**`server` is promoted, reversing its `_Avoid_` line.** Where tmux's own server
is also under discussion, both are written out — **AttSD server** and **tmux
server** — and plain "server" means the AttSD one everywhere else. This is
recorded as a resolved ambiguity in `CONTEXT.md` rather than silently dropped,
because the old guidance was explicit and a reader who remembers it deserves to
find out what changed.

**Earlier ADRs are rewritten in the new vocabulary.** Ten of them used
`Launcher`; all now read in the current terms.

## Consequences

- **Ten ADRs now use a name that did not exist on their date.** This is the price
  paid above, stated plainly here so that a reader who notices the anachronism —
  ADR 0001 arguing about Tailscale as "the AttSD transport" months before the
  name existed — finds the reason rather than a mystery. The trade was
  legibility of the corpus against fidelity of each document's date. If you need
  what a given ADR said on the day it was written, `git log --follow` on the file
  has it verbatim.
- **Filenames and ADR numbers are unchanged**, including
  `0001-tailscale-as-launcher-transport.md` and
  `0008-board-supersedes-the-inline-launcher-page.md`. Numbers are the permanent
  record and inbound links resolve to paths; only titles and body prose moved.
  So two files on disk still say `launcher` while nothing inside them does,
  which is the deliberate seam between the record and its text.
- **The historical page keeps a name.** ADR 0003's and 0005's "launcher page" is
  now the **launch page** — the inline `/` that ADR 0008 deleted. It needed a
  name that is not `Launcher` and not `Board`, since the whole point of 0008 is
  that they are different pages.
- **Runtime identifiers still say `claude-launcher`** — the tmux socket and
  session, `CLAUDE_LAUNCHER_*`, the launchd label — until the cutover lands.
  Prose and identifiers move in separate steps because one is reversible and the
  other drains every live **Run** on the socket, including Runs belonging to
  other repos.
- **The measured examples in ADR 0023 keep the old name.** `claude-launcher`
  arriving as `claude-lau…` at a 20px root is an observation about a
  fifteen-character string; substituting a five-character one would make the
  ADR's own evidence false. Where the old name is data rather than a reference to
  this project, it stays.

## Escape hatch

If the split turns out to be too fine in practice — if "the server" reads as
under-specified in passages this ADR did not anticipate — the fix is to spell
**AttSD server** in more places, not to reintroduce a single word for the process
and the page. That collision is what this ADR exists to end.
