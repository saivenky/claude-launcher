# The Nickname is typed, and it wins

A **Session** carries an optional **Nickname** that you type. Wherever a **Run**
is named, the Nickname takes the slot a derived label would have taken —
`aiTitle` on the **Focus**, the snippet on a queue row, the **Ask** text on a
**Blocked** row — and the **Workspace** keeps its place beside it.

## Context

The **Workspace** is the first level of context and ADR 0023 spent an ADR making
sure it survives: it is the identity, it truncates last, it holds the header's
first row alone. That works until you run three Sessions in one repo, which is
the normal case here — a worktree per feature is one Workspace each, but three
`claude`s in `~/projects/claude-launcher` are three rows reading
`claude-launcher`, and the Workspace has no more to say.

The second level is not missing. It is derived, late, and in one place:

- The transcript carries `{"type":"ai-title","aiTitle":"…"}` — Claude Code's own
  generated summary. It is in 3527 of 4375 transcripts on disk (81%), stable
  once written, re-emitted nearly every turn. It does **not** appear at session
  start: first occurrence lands around turn 50 in the files sampled.
- `_ai_title` (`server.py:1652`) scans the whole transcript keeping the last
  match, and the payload attaches it to the **focus item only**
  (`server.py:3055`). Queue rows, Foreign rows and the **Ask** get nothing.
- Queue rows show `one` — `_last_msg`, a 160-char tail — which the
  `question`/`approval` lanes replace with the **Ask** text (`server.py:2921`).
- The **Recover** picker shows `_first_user_msg` (`server.py:798`), the opening
  prompt.

So four different derived strings, none of them chosen by you, and the only one
that reads like a name is on the one Run you are already looking at. The
question *which of these three is it* is asked on the queue, and the queue is
where nothing answers it.

There is also a per-Session store already: `.board-state.json` holds `priority`
and `snooze`, both keyed by `sessionId` (`server.py:1545`). And `sessionId` never
changes — resume is literally `cl --resume <sessionId>` (`server.py:193`), and
across 3568 transcripts there are zero files whose inner `sessionId` differs from
the filename and zero ids appearing in two files. **Transfer** (`server.py:1319`)
and **Recover** (`server.py:3540`) funnel into the same call. Anything keyed by
`sessionId` survives every lifecycle verb the Launcher has, for free.

## Decision

**A Nickname is typed by a human, optional, stored per `sessionId`, and it beats
every derived label wherever the two compete.**

Typed rather than derived, when `aiTitle` was sitting right there and free: a
derived label is a guess at what the Session is *about*, and the Board already
shows three such guesses. The entire value of a second level is that the word
means something to *you* — "the auth refactor", "the flaky test" — which is
exactly the thing a summariser cannot produce, and it is available the moment
you name it rather than fifty turns in.

Consequences, each following from the same rule (the typed name wins, the
Workspace stays):

- **On the wire it is `nickname`**, `None` when unset — never an empty string,
  so "no Nickname" has one representation and every render site is one
  truthiness check. It rides *beside* `one` rather than being substituted into
  it server-side, so the fallback lives in one place in `board.js`.
- **It replaces `one` on every row, including Blocked rows** where `one` is the
  Ask. This looks like a regression and is not: the lane badge already says
  `question` or `approval` (ADR 0023), which is the part of an Ask that changes
  what you do, and the first 160 characters of the rest are usually boilerplate.
  The full Ask survives on the Focus, which is where you answer it. An
  un-nicknamed Blocked row is unchanged.
- **`aiTitle` stays on the Focus.** The obvious move — extend the derived label
  to every row as the middle of a nickname → `aiTitle` → snippet chain — costs a
  full end-to-end transcript scan per Run per board poll, to show a label that
  arrives fifty turns late, on rows you have just been given a better way to
  name. The Focus's chain is nickname → `aiTitle` → snippet; a row's chain is
  nickname → snippet.
- **It lives on row one of the Focus header, not in `.about`.** ADR 0025's
  condensed header `display:none`s `.fabout` and keeps only `.fdir`
  (`board.html:200-203`). A Nickname in the `.about` band would vanish exactly
  when it is needed most — scrolled deep into a **Scrollback** with an **Ask**
  in front of you, which is the state ADR 0025 exists to protect. Both levels of
  *where am I* survive condense, at the condensed scale.
- **Both get a floor.** ADR 0023's finding was that `.fdir` was the only item on
  the row without `flex:0 0 auto`, so every shortfall was paid out of it in
  full, by construction. Giving the Nickname no floor reproduces that inversion
  one level down: at 390px the Nickname is what disappears, and the row silently
  reverts to looking like every other row in the repo with nothing saying so.
  The Nickname yields first, down to its floor; then the Workspace starts
  yielding.
- **`elideNickname` is a third rule**, beside `elideWorkspace` and `elidePath`.
  It is tail-biased — keep the head, drop the end — because a Nickname is a
  phrase you type discriminator-first, where a Workspace is repo-plus-slug and a
  path is a list of segments. One function branching on its input would be one
  function with three contracts.
- **Set it after the fact, never at intake.** At spawn you know the folder and a
  vague intention; the name typed then is the one most likely to be wrong and
  never corrected. Ambiguity is only felt once there are two of them.
- **Two entry points: the Focus header, and a long-press on any row.** The
  header is primary because it is where you are when you notice. The long-press
  exists because a **Foreign Run** never takes the Focus (`CONTEXT.md`), so a
  header-only affordance would leave the one Session you most want to label —
  someone else's terminal, no **Respond**, visible only so it can be
  **Transferred** — able to display a Nickname and never receive one. Putting
  the Nickname on the Session rather than the Run was meant to avoid depending
  on Launcher control; a header-only edit would have reintroduced that
  dependency at the last step. Press-and-hold is free: `board.js` has zero touch
  listeners, no `contextmenu` and no hold timers.
- **The edit is inline, in place.** The header is `position:sticky` at a fixed
  height and the Scrollback scrolls under it; an input that swaps into row one
  costs no layout, where a sheet overlays the thing you are reading in order to
  decide what to call it. Not a prefix on the reply box: ADR 0015 made the
  bottom edge the reply box, and a prefix that sometimes means *rename* and
  sometimes means *say this to Claude* puts a typo one Enter away from the model.
- **Empty submit clears it. 24 characters max.** A separate delete control is a
  second affordance for the null case of one you already have. The cap roughly
  matches what row one can render, so you cannot author a name whose
  distinguishing half is never shown — ADR 0023's failure, self-inflicted.
- **Not unique.** The Launcher cannot promise uniqueness across Sessions it does
  not control, and enforcing it means a modal at the moment you were reducing
  friction. Two identical Nicknames in one Workspace is visible on screen and
  costs one retype.
- **`/api/nickname` is ungated**, joining `priority` and `snooze` behind
  same-origin + JSON. ADR 0007's rule is narrow and explicit: the token guards
  **Respond** *because Respond can approve tool calls*. A Nickname is a
  board-organising act whose blast radius is a wrong word you can retype.
- **Pruned at load, when the transcript is gone.** `.board-state.json` has no GC
  today — `priority` entries outlive their transcripts forever — and a Nickname
  has no expiry to prune it the way a snooze has. A missing transcript is the
  one condition where deletion cannot lose anything, because a Session with no
  transcript cannot be resumed by anything. At load only: once per server start,
  a `stat` per entry, never in the path of a UI action.

## Alternatives considered

**Extend `aiTitle` to every row and skip typing entirely.** It already exists, it
is already parsed, and 81% of transcripts have one. Rejected on three counts, any
one of which would be enough: it is absent for the first ~50 turns, which is
precisely the window in which you spawn a second Session in a repo and lose track
of which is which; serving it board-wide means an end-to-end file scan per Run
per poll; and it names what a Session is *about*, which is a moving target — a
Session that started on auth and is now fixing a test rewrites its own label
under you. The Nickname is deliberately the one string on the Board that nothing
changes but you.

**Nickname the Run, not the Session.** Simpler — it could live in a tmux pane
option beside `@cl_run_id`, with no store at all and no pruning question. But the
Run is the thing that ends: close it and the name goes, resume it and you retype.
It also puts Nicknames out of reach of Foreign Runs entirely, and out of reach of
the **Recover** picker, which lists Sessions with no Run at all and is the single
surface where near-identical rows from one repo are hardest to tell apart.

**Set it at intake, beside the directory picker.** One entry point, no gesture,
no inline edit. Rejected: it asks for the name at the only moment you reliably do
not have it, and a wrong name typed at spawn is one nobody goes back to fix. It
also adds a second decision to an intake that currently asks one.

**Enforce uniqueness within a Workspace.** Tempting, because the whole point is
telling two Sessions in one repo apart, and two rows reading `auth` fails at
exactly that. Rejected on where the check would have to live: the Launcher would
be refusing a name on behalf of Sessions it may not control, at the moment of a
one-handed edit on a phone, with a modal. The failure is visible and self-
correcting; the guard is not.

**Show the Nickname *and* the snippet on a queue row.** Rejected: the snippet
exists to answer *which one is this*, badly. Showing both spends a phone row's
width saying one thing twice, and ADR 0023 already had to give `.qdir` a cap
because that row has no width to spare.
