# claude-launcher

A tool to spawn, observe, and respond to local Claude Code sessions on a
Mac from a phone. It owns *lifecycle* (spawn / close / **resume**) and now
**observes** and **responds** to live Runs over its own transport; the
Claude app's **Remote Control bridge** stays the rich single-session
channel. This glossary fixes the language so the channels are never
conflated.

The word "session" is dangerously overloaded — Claude Code alone uses it
for four different things (see *Flagged ambiguities*). Here it means one
thing only: the durable thread that `sessionId` identifies, the same
thing the Claude app calls a session.

## Language

**Launcher**:
The server that spawns, lists, and closes local Claude Code **Runs**, and
the page used to drive it.
_Avoid_: server, app (overloaded), backend

**Session**:
The durable thread of work, persisted as
`~/.claude/projects/*/<sessionId>.jsonl` and identified by Claude Code's
own `sessionId`. This is what the Claude app shows you and what you paste
to **resume**. A Session outlives any one **Run**: close it, resume it
later, and it is the same Session. Never destroyed by the Launcher.
_Avoid_: conversation, transcript (the file on disk, not the living
thread), history, thread

**Run**:
One `claude` process executing a **Session**, whatever terminal holds it. A
Run embodies exactly one Session; ending a Run leaves its Session intact. At
most one live Run per Session. Two kinds, told apart by who started it: a
**Managed Run** — the only thing the Launcher creates and destroys —
concretely a tmux window (one pane) stamped `@cl_run_id` (see ADR 0010;
formerly an iTerm pane), and a **Foreign Run**.
_Avoid_: session (that's the durable thread here — and tmux's own container,
see *Flagged ambiguities*), window, tab, terminal, process, instance

**Workspace**:
Which project a **Run** is in, as one word — the name you switch *between*. The
basename of the Run's working directory, which is the repo for
`~/projects/<repo>` and the repo-plus-discriminator for a worktree at
`~/projects/.worktrees/<repo>-<slug>`, because that layout flattens the slug into
the directory name. *That flattening is load-bearing*: nest the worktrees instead
and the Workspace becomes the slug alone, with the repo gone. A **Session** in a
directory that no longer exists still has one; a Run with no working directory at
all has none, and the **Board** says so rather than putting something else in the
slot. Not a path — the path is a separate field, and a Workspace never contains a
`/`.
_Avoid_: project (the repo, not the checkout — two worktrees of one repo are two
Workspaces), repo (same), dir / cwd / path (the Workspace is derived *from* the
path and is not one), title (what the field was called while it held four
different kinds of string)

**Nickname**:
A short name *you* typed for a **Session**, so two Sessions in one **Workspace**
can be told apart. The Workspace is the first level of context — *where am I* —
and it stops answering the moment you run three Sessions in one repo; the
Nickname is the second — *which of these*. Optional, and its absence is a legal
state with a defined fallback, so a Session without one is not broken. Human-
authored by construction: derived labels already exist here — the `aiTitle` the
transcript carries, the opening prompt, the last message — and each is a guess
at what a Session is *about*, where a Nickname is a decision about what it is
*called* (ADR 0026). On the **Session**, not the **Run**: it survives close,
**Resume**, **Recover** and **Transfer**, all of which keep the `sessionId`, and
a **Foreign Run**'s Session has one like any other, because a Session does not
stop being yours because you started its Run in a terminal. Never unique — the
Launcher cannot promise it across Sessions it does not control, and two rows
reading the same word is a mistake you can see and retype. Wherever a Run is
named, the Nickname takes the slot the guess would have taken, and the
**Workspace** stays beside it: identity truncates last, and the Workspace is the
identity (ADR 0023).
_Avoid_: title (the word has meant the cwd basename, the pane title and the
first user message here, and is being retired), label (the triage vocabulary),
alias (implies you could **resume** by it — you cannot; a Session is addressed
by its `sessionId`), name (a Session's name is its `sessionId`), tag

**Foreign Run**:
A live **Run** the Launcher did not start — a `claude` someone ran by hand in
any other terminal, so it has no tmux window and no `@cl_run_id`. The Launcher
sees it (its **Session**, dir, status, and last message all come from Claude
Code's own state, never the terminal's) but cannot reach into it: no
**rendered pane**, so no **Respond**, no **Attach**, no close. Visible for
exactly two reasons — so it can be **transferred**, and so the one-live-Run-per-
Session guard is not blind to it.
_Avoid_: unmanaged (the Launcher does observe it), external, orphan, stray

**Resume**:
Start a new **Run** on an existing **Session** (via
`claude --resume <sessionId>`). Distinct from re-attaching to a *live*
Run, which is the **Remote Control bridge**'s job, not the Launcher's.
_Avoid_: reopen, restore, continue (overloaded by `claude --continue`)

**Resumable Session**:
A **Session** with a transcript on disk *and* a working directory that still
exists, but no live **Run** — one the Launcher can bring back. A Session whose
cwd is gone is not resumable (there is nowhere to `cd`), and a Session with a
live Run is not either (the one-live-Run-per-Session guard refuses it). Wider
than what the **Recover** picker lists: a **Headless Session** is Resumable and
is still never offered there, because being resumable is a fact about the
Session while being *offered* is a judgement about whether you wanted it back.
_Avoid_: closed session (a Run can be absent for reasons other than closing),
dead session (the Session is never dead — only its Run)

**Headless Session**:
A **Session** whose transcript records no interactive entrypoint — written by
the Claude Agent SDK or a `claude -p`, with no human at a terminal at any point.
A property of *origin*, fixed at birth and read from the transcript, never of
where it ran: a Session in the same directory that someone actually sat in front
of is not one. **Recover** never offers a Headless Session, because an
unattended agent can leave dozens an hour and nobody is waiting on any of them
(ADR 0022) — but it is still a **Resumable Session**, so **Resume** by
`sessionId` brings it back. The transcript-side twin of the tty test that keeps a
headless `claude -p` off the **Board**: the same call about origin, made on a
different substrate.
_Avoid_: SDK session (names one writer of several), unattended / background
session (each is true of the moment it ran, but the property is the origin, not
the moment), agent session (every **Run** is an agent), dispatch (a **Dispatch**
leaves no Session at all)

**Recover**:
Discover and **resume** — as Managed Runs, in bulk — the **Resumable
Sessions** that were live before a restart. The discovery-and-bulk sibling of
**Resume**: where Resume takes one `sessionId` you already know, Recover lists
Resumable Sessions newest-first and pre-selects the **recovery set**, so one
tap brings a whole batch back. It lists only the Sessions a human drove — a
**Headless Session** is filtered out before the recovery set is even computed,
so a burst of them cannot crowd the window or drag the pre-tick (ADR 0022).
It resumes each member exactly as Resume does —
a new **Run** per Session — and nothing of the old Run comes with it: any
in-flight turn from before the restart is already lost (as with **Transfer**).
Only the Sessions are reopened; no prior *state* is restored.
_Avoid_: restore / reopen (nothing of the old Run's state returns), bulk
resume (names the mechanism, not the restart intent), restart (the trigger,
not the act)

**Recovery set**:
The subset of **Resumable Sessions** the Launcher judges were live at the last
restart, pre-ticked in the **Recover** picker: a recency-cluster anchored on
the newest Resumable Session, chaining to older ones while each gap stays
within a tolerance, leashed to a total span. A *heuristic over transcript
mtimes, not a record* — the Launcher keeps no memory of what was live (ADR
0013) — so it is a best guess, freely editable before you resume.
_Avoid_: restart batch (informal only — the set is the named thing),
live-at-restart set (wordy)

**Transfer**:
End a **Foreign Run** and **Resume** its **Session** as a Managed Run — one
tap, one atomic operation. What moves is *custody of the Session*, not a
process: the original `claude` is killed and a new Run replaces it, so the
in-flight turn and any text typed-but-not-sent in the old terminal are lost
(accepted — see ADR 0012). The Session itself is untouched, as always. The
only Launcher action that destroys a Run it did not create, and the only
thing that can be done to a Foreign Run at all.
_Avoid_: adopt / take over (each reads as driving it *where it lives*, which
is the rejected design); migrate, move (no process moves — see *Flagged
ambiguities*); hand-off (that's **Attach** and the bridge)

**Observe**:
Read a live **Run**'s externally visible state — which **Session** it is
executing, its working directory, its status, its most recent message, and
its **rendered pane** (the on-screen TUI, where a **Blocked** Run's actual
prompt lives even when it never reaches the transcript) — without driving
it. Strictly one-way. Typing, answering, and approving are **Respond**'s
job (over the Launcher transport) or the **Remote Control bridge**'s (over
the cloud) — never Observe's.
_Avoid_: monitor, watch, view, read (each also suggests two-way)

**Respond**:
Inject input into a live **Run** over the **Launcher transport** — free
text, a selector choice (arrow / enter), or a permission approval — by
writing to the Run's pane (`tmux send-keys`; ADR 0010). The two-way sibling of **Observe** and the
Launcher's own driving channel, distinct from the **Remote Control bridge**
(Anthropic's cloud, one session, the Claude app). When the Run is busy,
Claude Code's native input queue absorbs the response until the next turn.
Free text is *routed*, never typed at whatever is on screen: while an **Ask
Set** owns the pane it goes through the widget's own `Type something` row, and
where that row cannot be used the only route left presses `Esc` first — which
cancels the **Ask** instead of answering it, so the phone says so and asks
before it happens (ADR 0020). A screen that could not be read is refused, not
typed at (ADR 0021).
Because it can *approve* tool calls, Respond removes the human-in-the-loop
backstop — so it, unlike the read-only verbs, is gated by a shared secret.
_Avoid_: drive (the channel-agnostic *capability*, not this one channel);
type / answer / approve (each names only one shape); remote control

**Attach**:
Open a *live* **Run**'s own tmux window in a local terminal to drive it by
hand — the copy-to-clipboard `tmux … new-session -t claude-launcher \;
select-window` line the **Board** hands you per Run (the `❯` local twin of the
`↗` **Remote Control bridge** handoff). A *handoff to a local terminal*, not a
new **Run**: it opens an ephemeral grouped-session *view* onto the Run's
existing window, so the Run keeps its one live process and its UUID, and the
view self-destroys the moment you detach. The connection is a **local** tmux
client on the Mac — the Board only *serves the command string* over the
**Launcher transport**; the attach itself traverses no transport. Distinct
from **Resume** (a *new* Run on a *dead* **Session** — refused while this Run
is live) and from **Respond** (input over the transport, no terminal).
Requires a live Run; a closed one has no window to attach.
_Avoid_: resume (a new Run, not this), open / reopen (overloaded), tmux
session (the view is a throwaway grouped session, never a **Run**)

**Board**:
The single screen — the Launcher's only page — that aggregates every live
**Run**, holds one at a time as the **Focus** while the rest queue by
**Priority**, and inside a level by urgency (**Blocked** first, with its
concrete blocker), lets you **Respond**
to the Focus inline, and carries the full **intake** and lifecycle
surface: launch, **resume**, **recover**, close, the one-tap **Task** / **Dispatch**
buttons, and the two per-Run handoffs — the `↗` deep-link to the **Remote
Control bridge** and the `❯` **Attach** line for a local terminal. It
supersedes the legacy inline launcher page (once served at `/` alongside
it): the Board *is* the launcher page now, grown from a run list into the
whole management surface. Its UI is served from disk and hot-reloads (ship
new HTML, no relaunch); the Launcher's `/api/*` capability surface behind
it — including `/api/tasks`, the task definitions the static page can no
longer inline server-side — is the stable contract.
_Avoid_: dashboard, list (it is more than a list now), inbox, feed

**Focus**:
The single **Run** the **Board** shows in full — its **scrollback**, its
**ask** when it has one, and the reply box — as opposed to the queued rows. At
most one. *You* choose it: a row tap, a swipe, or, when you hold none, the queue's
head if anything wants you and otherwise the newest live Run. The second half of
that fallback is not a nicety: a **Board** whose Runs are all merely **working**
has no urgent head, and a Focus of *none* takes the header, the queue's way in
and the swipe with it — live Runs and no route to any of them (ADR 0023). An
empty Board means nothing is live, never that nothing is urgent. Its header names
the **Workspace** first and alone, on a row of its own (ADR 0023), and that row is
what survives when the header yields to the read: the header never leaves, it
gets smaller, and everything in it but the name is what gives way (ADR 0025).
The swipe is a touch gesture, and it declares itself before it acts: while it is
in flight it names the Run it would hand you, and it moves the Focus only on a
release that means it. Where there is no touch the same move is a trackpad flick
or ←/→, so the move exists everywhere the **Board** does.
It stays yours until you move it or it resolves. **Priority** and
urgency order the queue, never the Focus — a newly-**Blocked** Run joins the
queue; it does not take the Focus from you.
_Avoid_: card (it is not drawn as one — a card marks one of many, and a Focus is
at most one, ADR 0024), selection, current, active, top

**Scrollback**:
The recent **turns** and **work** of the Focus's **Session**, newest last —
what the Focus shows in place of a single message. A *window on the
transcript*, not the transcript itself: it is a bounded tail, and a **Foreign
Run**'s Session has one just as a Managed Run's does, because it is read from
the transcript and not from a pane. Read as **exchanges** rather than as a flat
list: they are **folded** by distance from now, cut by a **seam**, and the page
lands on that seam so the newest prose is on screen before you move (ADR 0017).
That grouping is presentation only — the payload is still the bounded list of
entries ADR 0014 bounds. Distinct from the **rendered pane**, which is the live
TUI and the only place a permission prompt exists (ADR 0009).
_Avoid_: history, log, transcript (the file on disk — the scrollback is a
read of its tail), context (was the old single-message field; see ADR 0014)

**Turn**:
One thing that was *said* in a **Scrollback** — prose you sent, or one
assistant reply. Never tool calls: those are **Work**, the scrollback's other
kind of entry, and no row of the transcript carries both (ADR 0016). A slash
command you invoked is a Turn of yours; the skill body it injects is not,
because you did not send it.
_Avoid_: message (overloaded), exchange (an **Exchange** is a whole group of
entries — a Turn of yours is only what *opens* one), event; entry (a Turn is one
*kind* of scrollback entry, not the general word for one)

**Work**:
One entry of a **Scrollback** standing for a contiguous stretch of tool calls —
everything the **Run** did between two things that were said, as one thing,
each call naming what it was *doing* and not merely which tool it was. It costs
the scrollback exactly what a **Turn** costs, which is the whole point: charged
per call instead, tool names took 5–8 of the Focus's 14 entries and evicted the
prose the Scrollback exists to show (ADR 0016).
_Avoid_: tool run / run of calls (**Run** is one `claude` process here, and that
collision is not survivable), step, activity, action, tool use (the atom, not
the stretch)

**Exchange**:
One **turn** of yours plus everything the **Run** said and did in reply — the
unit the **Scrollback** is read in. Its boundary is not a judgement call: an
Exchange opens on something *you* did (a Turn or a slash command) and runs to the
next such thing, which is the same boundary ADR 0016 uses to break a `claude`
block. Derived in the client from the entries the payload already carries; it is
not a field and it costs `/api/board` nothing. A **Foreign Run**'s Session groups
into Exchanges exactly as a Managed one's does.
_Avoid_: chapter (the prototype's word, and it implies an author chose where the
break went), thread, block (that is ADR 0016's chained assistant run), pair (an
Exchange holds one prompt and any number of replies and **Works**), conversation

**Record**:
A past **Exchange** folded to a fixed three-line shape in one label gutter:
`you` (your prompt), `work` (what it touched — the **Works**' calls
run-length-encoded, with a `⚙n` count), `claude` (the reply's first sentence, or
that reply's closing question to you, in teal). The label says *who*, and the
same three words are used at every depth of the **Fold**. Fixed shape is the
whole value — the column can be skimmed without reading a value — which is why it
beat a variable-length prose gist (ADR 0017). Tap it and it opens to prose in
place, anchored, at 0px of drift.
_Avoid_: card (nothing singular on this page is drawn as one, ADR 0024), row (names the
shape, not the thing), summary / gist / headline (each was the rejected design —
a Record is three answers, not one sentence), entry (a **Turn** and a **Work**
are entries; a Record folds several of them)

**Fold**:
How much of an **Exchange** the **Scrollback** shows, graded by distance from
now: the live tail is full prose, the run-up inside the Exchange you are standing
in is one gutter row per thing that was said, and every older Exchange is one
**Record**. Presentation and nothing else — folded or unfolded, the payload is
the same. `read all` unfolds the lot and restores the linear read.
_Avoid_: collapse (names one direction of one row), truncation (nothing is
dropped — it is all one tap away), summary, digest

**Seam**:
The `NEWEST` rule that cuts the **Fold** from the live prose below it, and the
thing the page *lands on* — parked 250px down, not against the header, so the tail
of the run-up peeks above it. That peek is load-bearing: it is how a reader learns
there is a Fold and that it is skimmable. Below the seam the label gutter stops
and the read takes the full column.
_Avoid_: divider / rule (names the pixels, not the landing), fold (the Fold is
the folded region; the seam is its edge), anchor (that is the scroll mechanic
every unfold uses), marker

**Ask**:
The specific input a **Blocked** **Run** needs from you — the concrete
blocker, and exactly one decision. A property of being Blocked and of nothing
else: an **idle** Run has no Ask, and prose ending in a question mark is not
one, because it is already the last **turn** of the **scrollback**. What is
being asked comes from the transcript, which carries it in full; *where the
widget is standing* comes from the **rendered pane**, which is the only source
for it (ADR 0020, narrowing ADR 0009).
_Avoid_: question (only one of its two shapes — the other is an approval),
prompt (overloaded by Claude Code's own permission prompt)

**Ask Set**:
The **Asks** raised by one `AskUserQuestion`, answered in order and submitted
together. Cardinality-neutral: a Set of one is still a Set, which is what keeps
a single-question ask (326 of 425 on disk) and a four-question one the same code
path. Only ever *one* Ask of a Set is on screen — the **current Ask** — so the
Set is a thing the server models and the phone never draws whole. An approval is
always a Set of one; only a question can raise several.
_Avoid_: MultiAsk (names the rare case — most Sets hold one), batch, group,
tab strip (the widget's rendering of a Set, not the Set)

**Priority**:
The level *you* set on a **Session** — `high`, `normal` or `low` — saying how
much of your attention its work is owed, and the thing the queue's tiers are cut
along. Set from the **Board**, from any queued row and not only the **Focus**,
and persisted, so it outlives the **Run** exactly as the Session does and
survives a restart. It outranks urgency because it is the one thing here you
*typed*: whether a Run is **Blocked** is inferred from what it happens to be
doing this second, where a level is a standing judgement about the work — so a
`low` Blocked Run sits below a `normal` **idle** one, and "I know it is asking,
I do not care yet" stays sayable. A tier and never a tiebreak: the queue is
walked one level at a time, a level is exhausted only once every Run in it has
gone **working**, and only then does the queue descend to the next. A `high` Run
never dorms, however long it has sat. It does not reorder **snoozed** — a snooze
says *when* a Run comes back, and no level may quietly overrule that.
_Avoid_: urgency (that is the lane — **Blocked** before **idle** — which
Priority now outranks), importance (a claim about the work's worth; a level is a
claim about your attention today), rank (names a position in the queue, which is
what a level *produces*), weight (implies it is summed with something — it is
not; it is read first and alone)

**Rotation**:
How the **Focus** advances through the queue — consent-based. It moves only
when you act (tap a row, swipe, skip) or when the Focus you hold *resolves*:
goes **working** because you **Respond**, is closed, or stops being
**Blocked**. Nothing else moves it. Consent is why a swipe commits on a
deliberate release and not on any release: a swipe you let go of before it has
committed hands you nothing, and there is no such thing as a Focus you did not
mean to take. The flick and the arrow keys that stand in for the swipe without
touch are the same consent, given in one motion instead of two. The "curated
round-robin" names this queue's order, not a clock: the order is **Priority** first — every `high`, then
every `normal`, then every `low`, with **Blocked** before **idle** inside a
level — and you walk it at your pace. `skip →` walks it too: it hands the Focus
to the next Run after this one, not back to the queue's head, because under
tiers the head is often the Run you have just declined.
_Avoid_: round-robin (the queue's order, not the advance rule), auto-advance,
cycle, refresh

**Intake**:
Starting new work from the **Board** — a generic dir **launch**, a
**resume** by `sessionId`, a **Recover** of the Sessions live before a
restart, or a one-tap **Task** / **Dispatch**. The
*create* side of the Board, as opposed to the *triage* side (**Observe** /
**Respond**) that acts on work already running.
_Avoid_: launch (only one of intake's shapes), compose, new session, new run
(three of the four shapes produce a **Run**, so it distinguishes none of them —
and a **Dispatch** produces none at all)

**Blocked**:
A **Run** paused awaiting a *specific required input from you*: an
**approval** (a permission prompt or plan approval) or a **question**
(AskUserQuestion). Distinct from **idle** ("your move" — the turn ended but
nothing is required) and from Claude Code's `status: waiting` flag, which is
only a lossy proxy for it. What the **Board**'s queue leads with inside a level
of **Priority**, never across them. Read from the
transcript tail plus the **rendered pane**, never from the status flag
alone. A question may raise several **Asks** at once — an **Ask Set** — in
which case the Run stays Blocked until the last one is submitted, and each is
answered against a freshly read pane rather than a script (ADR 0020).
_Avoid_: waiting (Claude Code's status flag, not this), stuck, needs-input

**Task**:
A named, preset **Run** definition in `tasks.py` — fixed workdir plus an
initial `/slash-command`, surfaced as a one-tap button. A convenience over
the generic "type a dir" launch; it still starts an ordinary Run on a new
**Session** (tagged `user.cl_task` so the list can label it).
_Avoid_: job, action, command (overloaded); **Dispatch** (that starts no Run)

**Dispatch**:
A named, preset *command* in `tasks.py` (an `exec` argv), run **detached**:
no `claude`, no **Session**, no **Run**, no pane. It shares only the
one-tap button with a **Task**. The **Launcher** spawns it and forgets it —
there is nothing to **observe**, nothing to close, and no `sessionId` to
**resume**. Its own output is its trace, wherever it chooses to leave it.
Exists for fire-and-forget agents a phone should be able to trigger without
opening a session to babysit.
_Avoid_: task, run (a Dispatch is neither); job, background task (each
suggests something the Launcher tracks — it does not)

**Launcher transport**:
The path by which a phone reaches the **Launcher** endpoint (today: a
Tailscale-routed HTTP request to a LAN-bound port). This is the only
thing a Tailscale replacement would change.
_Avoid_: connection, network, tunnel (each names only one option)

**Remote Control bridge**:
Anthropic's cloud channel that carries a **Run**'s typing, approvals, and
output to the Claude app. Independent of the **Launcher transport** — it
does not flow over Tailscale. Its own `bridgeSessionId` names a *bridge
channel*, never a **Session**.
_Avoid_: remote control (lowercase reads as a generic capability)

**Reachability scope**:
Where a phone must be for the **Launcher transport** to work:
*same-LAN* (home Wi-Fi only) vs *anywhere* (cellular / foreign network).
_Avoid_: access, availability

## Relationships

- A **Launcher** spawns and closes many **Runs**. It never creates or
  destroys a **Session**
- A **Run** executes exactly one **Session**; a **Session** outlives its
  Run and can be **resumed** into a new one
- At most one live **Run** per **Session** — resume refuses a Session that
  already has one, so a transcript is never forked. The guard must count
  **Foreign Runs** too: a Session live in another terminal is just as forkable
  as one live in a tmux window
- **Recover** is a bulk **Resume** and nothing more: it creates only Managed
  Runs, never touches a Session's file, and obeys the same one-live-Run guard
  per member. A member whose resume fails — its cwd vanished, say — is skipped;
  the rest still come back
- A **Resumable Session** has no live Run; resuming it, by **Resume** or
  **Recover**, makes it live and drops it from the picker. A **Foreign Run**
  makes its Session *not* Resumable — the resume guard already counts it
  (ADR 0012)
- A **Headless Session** is **Resumable** but never **Recover**able: the picker
  offers a strict subset of the Resumable Sessions. That gap is the only one of
  its kind in this glossary, and it is deliberate — Recover guesses what you
  wanted (ADR 0013), so it is allowed to be wrong in the direction of offering
  less, where **Resume** obeys a `sessionId` you typed and is not
- After a machine restart no **Run** survives, so every Session that had one
  becomes **Resumable**. The **recovery set** is the Launcher's guess at which
  of those were *live at the restart* — a heuristic over transcript mtimes, not
  a record; the Launcher never persists the live set (ADR 0013)
- Every **Run** is either Managed or **Foreign**, and only the Launcher's own
  act of starting it decides which. A Managed Run never becomes Foreign; a
  Foreign Run becomes Managed only by being **transferred**, which replaces it
- A **Foreign Run** is never **Blocked**, never takes the **Focus**, and never
  enters **Rotation**. It has no **rendered pane** to read a blocker from, and
  no **Respond** to answer one with — a row you cannot answer would only make
  the queue lie. It sits outside the triage surface entirely
- **Transfer** needs a **Foreign Run**; **Attach** and **Respond** need a
  Managed one. No Run offers both, and the split is the same one throughout:
  the Launcher drives only what it started
- A **Run**'s lifecycle flows over the **Launcher transport**; its I/O
  flows over the **Remote Control bridge** — different channels
- A **Launcher** **observes** Runs; it never drives them. Observing rides
  the **Launcher transport**; driving rides the bridge
- Three ways lead onto a *live* **Run**: **Respond** (input over the
  **Launcher transport**), the **Remote Control bridge** (Anthropic's cloud →
  the Claude app), and **Attach** (a local terminal onto the Run's tmux
  window). Only the bridge leaves the Mac; **Attach** never touches the
  transport at all — the Board just serves its command string
- **Attach** needs a live **Run**; **Resume** needs a **Session** with none.
  The same Session cannot offer both at once — the live-Run guard that refuses
  Resume is exactly what makes Attach the only terminal route while a Run runs
- A **Launcher transport** choice is bounded by the required
  **Reachability scope**
- A **Dispatch** produces no **Run** and no **Session**, so none of the
  Launcher's other verbs apply to it: it cannot be **observed**, closed, or
  **resumed**. Spawning it is the whole interaction
- A **Task** and a **Dispatch** are told apart by one field — `command`
  starts a **Run**, `exec` starts a Dispatch. Never both
- Every **Run** has a **Workspace**, Managed or **Foreign** alike, derived by the
  same rule from the same field — so the one thing that says where you are is
  never computed two ways. It is the only identity a **Run** carries that you can
  read at a glance: a `sessionId` is for pasting, a **Session**'s summary says
  what the work is and not where it lives
- Wherever a **Run** is named — a queue row, the **Focus**'s header, the
  **Ask** — the **Workspace** is what names it, and it truncates last. It is
  answering a different question from everything beside it (*where am I*, not
  *what is happening*), so nothing else on the surface can stand in for it
- Beside the **Workspace**, and only ever beside it, sits the **Nickname** — the
  second level of context, answering *which of the several here*. It truncates
  before the Workspace and after everything else, and it takes the slot a
  derived label would have taken, never the Workspace's (ADR 0026)
- The **Focus**'s header gives the read everything it can spare and never the
  **Workspace** or the **Nickname**, so both levels of *where am I* are on
  screen at the moment you answer an **Ask** — deep in a **Scrollback**, where
  nothing else is (ADR 0025)
- A **Board** holds exactly one **Focus**; every other actionable **Run**
  queues behind it by **Priority**, and by urgency inside a level. A **Blocked**
  Run outranks its own level, not the levels above it and not the Focus
- A **Focus** always offers a reply box — **idle**, **Blocked** or **working**
  alike. Only an **Ask** is conditional, because only a **Blocked** Run has
  one. Responding to a working Run is not a special case: its input queues
  until the turn ends
- A **Scrollback** is made of **turns** and **work** and belongs to a
  **Session**, so it survives its **Run** exactly as the Session does —
  **resume** a Session and the scrollback is still there. It is a bounded tail,
  never the whole thread
- A **Work** is delimited by **turns**: it begins when something stops being
  said and ends when something is said again. So a Scrollback never holds two
  adjacent Works, and what a Work contains is never a matter of judgement
- An **Exchange** groups a **Scrollback**'s entries; it never changes them or how
  many there are. So the bound is still ADR 0014's — a fixed number of **turns**
  and **Works** — and an Exchange whose opening Turn has slid out of the window is
  a real Exchange with no prompt, labelled as such rather than hidden
- The **Fold** is graded by distance from now, so the **Exchange** you are
  standing in is never a **Record**: its run-up is rows and its tail is prose.
  Only Exchanges you have finished with fold that far
- A **Record** goes teal when its Exchange ended by putting a question to you.
  In chronological order your answer is the very next row *down* — which is the
  relation an inverted order destroys, and the reason the order is not negotiable
- The **Seam** is where the page lands, once per **Scrollback**, and only if you
  are still parked where the last landing left you. Scroll up into history and it
  leaves you there: an auto-scroll that yanks a reader is the same bug as landing
  at the oldest entry, facing the other way
- **Rotation** advances the **Focus** only on your action or when the Focus
  resolves. New work surfaces as a count on the queue — never by replacing
  what you are reading or typing in

## Example dialogue

> **A:** "If we drop Tailscale, do we lose the ability to drive a **Run**
> from the phone?"
> **B:** "No — driving a Run rides the **Remote Control bridge** through
> Anthropic's cloud. Tailscale only carries the **Launcher transport**.
> Replacing it only affects spawn / list / close."

> **A:** "I left a `claude` running in iTerm at home — can I answer it from
> my phone?"
> **B:** "Not directly; it's a **Foreign Run**, so there's no pane to
> **Respond** into. **Transfer** it — that kills it and **resumes** the same
> **Session** as a Managed Run you can drive. You'll lose whatever turn was
> in flight, and iTerm keeps a dead tab until you close it."

> **A:** "The Run's idle and it asked me something at the end — why is there no
> **ask** on the card?"
> **B:** "Because it isn't **Blocked**. An **Ask** is the blocker of a Blocked
> Run, and the Launcher reads it off the **rendered pane** when the transcript
> hasn't got it. Prose ending in a `?` is just the last **turn** — it's already
> on screen in the **scrollback**, so a strip would say it twice."

> **A:** "Tapping × killed my session."
> **B:** "It ended the **Run**. The **Session** is on disk; **resume** it
> and you get a new Run on the same Session."

## Flagged ambiguities

- "session" names five different things across Claude Code and its
  substrate — resolved in favour of the identifier a human actually handles:
  - `~/.claude/sessions/<pid>.json` — a live process. Here: a **Run**.
  - `sessionId` — the durable thread. Here: a **Session**. *This one wins
    the word*, because it is the only one the user ever sees or types.
  - `bridgeSessionId` — the **Remote Control bridge**'s channel. Here: a
    *bridge channel*, never a Session.
  - tmux's top-level container, a `tmux session` — the most dangerous
    claimant, because it is a word in the Launcher's own CLI. Resolved by
    topology: the Launcher runs *one* tmux session (`claude-launcher`) and a
    Run is a **window** in it, never a tmux session (ADR 0010). So "close the
    session" is never ambiguous — you close a **Run**, i.e. a tmux window.
  - a Run's tmux window/pane (formerly iTerm's `sessions of tabs`) — the
    terminal container. Here: a **Run**, an implementation detail.
- "close a session" conflated ending a process with destroying a thread —
  resolved: you close a **Run**. Nothing the Launcher does can destroy a
  **Session**.
- "session management" was used to mean lifecycle *and* possibly typing
  into a live Run — partially resolved: management today means lifecycle
  (spawn / close / **resume**) plus **Observe**. Driving a Run stays the
  **Remote Control bridge**'s and is deferred, not ruled out.
- "transfer a session from iTerm" read as *moving* something — flagged and
  kept anyway, with the reading pinned down. Nothing moves: a **Session**
  never moves (it is a file the Launcher never touches), and a `claude`
  process cannot be reparented off its tty. **Transfer** moves only *custody*,
  by destroying one **Run** and starting another on the same Session. If a
  future reader expects the process to survive, this is the line that says it
  does not.
- "a Run is a tmux window" was true between ADR 0010 and 0012 — resolved: a
  **Run** is one `claude` process; a tmux window is what a *Managed* Run is
  made of. The narrower definition made every `claude` outside the Launcher
  nameless, which is why the one-live-Run-per-Session guard silently stopped
  holding.
- "context" named the **Focus**'s reading surface while that surface was one
  message (the `contextHtml` field of ADR 0006) — retired in favour of
  **Scrollback** once it became many **turns** (ADR 0014). The word was always
  doing two jobs: this repo's *domain* context and the Focus's run-up. Only the
  first survives.
- "a **turn** carries prose *and* the tools it invoked" was written into the
  glossary and into `_scrollback` — and was never true. A census over 40
  transcripts found 657 tool-carrying assistant rows, every one holding exactly
  ONE `tool_use` and ZERO prose: Claude Code emits a row per call. The wrong
  model was not idle. It is why the scrollback rendered a bare chip per row and
  charged a slot for each, which is what ADR 0016 undoes.
- "run" would name a stretch of tool calls if left alone, and **Run** is the
  central term of this glossary — one `claude` process. Resolved before it could
  spread: the stretch is a **Work**, and "tool run" is on that entry's _Avoid_
  list. The tell that this was close: the payload field is `role: "work"` and
  the code still calls the loop variable a run in its own comments, where the
  surrounding text makes the sense unambiguous.
- "asked" was drafted as a **Record**'s label for your prompt, and rejected —
  twice, in two prototypes, which is why it is written down. **Ask** is a narrow
  thing here (a **Blocked** Run's concrete blocker) and `asked` is it in the past
  tense, so a label reading `ASKED` on every folded row would have made the
  glossary's tightest term the page's loosest word. It failed on pixels too
  (`REPLIED` will not fit the gutter at any type size the page uses; `ASKED` and
  `ASKS` are two letters apart). The labels say *who* — `you` / `work` / `claude` — and the
  verb question is closed (ADR 0017)
- "MultiAsk" was proposed for the container an `AskUserQuestion` raises, and
  rejected for cardinality: 326 of 425 asks on disk hold exactly one question,
  so the name argues with the common case and forces every call site to ask
  "is this a MultiAsk or an Ask" — the branch the term exists to remove. An
  **Ask Set** of one is still a Set. Every other term here is cardinality-neutral
  (a **Work** is a stretch of calls; a stretch of one is still a Work), and
  "Multi-" is the one prefix that cannot be.
- "chapter" named an **Exchange** through three rounds of prototyping — retired.
  It reads as something an author chose to break, where an Exchange's boundary is
  mechanical: it opens on something *you* did, the same cut ADR 0016 already makes
- "the ask" was used for any question a **Run** left hanging — resolved and
  narrowed to **Blocked** Runs only. The prose-`?` heuristic that fed it on an
  **idle** Run produced a second copy of the last **turn**, not new
  information, so an idle Run has no **Ask** at all.
- "intake agent" was used for an unattended agent in *another* repo whose
  headless Sessions were filling the **Recover** picker — flagged, and the word
  is not shared. **Intake** here is the **Board**'s create-side (launch,
  **resume**, **Recover**, **Task** / **Dispatch**) and nothing else. That
  agent is named by what it leaves behind on this side of the boundary — a
  **Headless Session** — because its own name is not ours to spend, and the
  filter would be wrong if it were about one agent rather than about origin.
- "depend on Tailscale" was used to mean the whole tool — resolved:
  Tailscale is only the **Launcher transport**; the **Remote Control
  bridge** is unaffected.
- "no install" was used to mean lighter overall — flagged: it constrains
  only the *phone* side; the Mac still runs **Launcher** code (and any
  transport's native bits).
- "recover" reads as *restore prior state* — flagged and narrowed: **Recover**
  reopens **Sessions**; it does not bring back a Run's in-flight turn, which is
  lost at the restart exactly as it is under **Transfer**. It is a
  discovery-and-bulk **Resume**, never a state-restoration. If a future reader
  expects the old turn to return, this is the line that says it does not.
