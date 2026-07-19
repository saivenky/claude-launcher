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
One `claude` process executing a **Session** — concretely, a tmux window
(one pane) running `claude` (see ADR 0010; formerly an iTerm pane). This is
the only thing the Launcher creates and destroys. A Run embodies exactly one
Session; ending a Run leaves its Session intact. At most one live Run per
Session.
_Avoid_: session (that's the durable thread here — and tmux's own container,
see *Flagged ambiguities*), window, tab, terminal, process, instance

**Resume**:
Start a new **Run** on an existing **Session** (via
`claude --resume <sessionId>`). Distinct from re-attaching to a *live*
Run, which is the **Remote Control bridge**'s job, not the Launcher's.
_Avoid_: reopen, restore, continue (overloaded by `claude --continue`)

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
urgency (**Blocked** first, with its concrete blocker), lets you **Respond**
to the Focus inline, and carries the full **intake** and lifecycle
surface: launch, **resume**, close, the one-tap **Task** / **Dispatch**
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
The single **Run** the **Board** shows in full — its run-up context, the
ask, the reply box — as opposed to the queued rows. At most one. *You* choose
it: a row tap, or the first actionable Run when you hold none. It stays yours
until you move it or it resolves. Urgency orders the queue, never the Focus —
a newly-**Blocked** Run joins the queue; it does not take the Focus from you.
_Avoid_: card (the Focus's rendering, not the concept), selection, current,
active, top

**Rotation**:
How the **Focus** advances through the queue — consent-based. It moves only
when you act (tap a row, skip) or when the Focus you hold *resolves*: goes
**working** because you **Respond**, is closed, or stops being **Blocked**.
Nothing else moves it. The "curated round-robin" names this queue's order,
not a clock — you walk it at your pace.
_Avoid_: round-robin (the queue's order, not the advance rule), auto-advance,
cycle, refresh

**Intake**:
Starting new work from the **Board** — a generic dir **launch**, a
**resume** by `sessionId`, or a one-tap **Task** / **Dispatch**. The
*create* side of the Board, as opposed to the *triage* side (**Observe** /
**Respond**) that acts on work already running.
_Avoid_: launch (only one of intake's shapes), compose, new session

**Blocked**:
A **Run** paused awaiting a *specific required input from you*: an
**approval** (a permission prompt or plan approval) or a **question**
(AskUserQuestion). Distinct from **idle** ("your move" — the turn ended but
nothing is required) and from Claude Code's `status: waiting` flag, which is
only a lossy proxy for it. The **Board**'s top priority. Read from the
transcript tail plus the **rendered pane**, never from the status flag
alone.
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
- At most one live **Run** per **Session** — resume refuses a Session
  that is already running, so a transcript is never forked
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
- A **Board** holds exactly one **Focus**; every other actionable **Run**
  queues behind it by urgency. A **Blocked** Run outranks the queue, not the
  Focus
- **Rotation** advances the **Focus** only on your action or when the Focus
  resolves. New work surfaces as a count on the queue — never by replacing
  what you are reading or typing in

## Example dialogue

> **A:** "If we drop Tailscale, do we lose the ability to drive a **Run**
> from the phone?"
> **B:** "No — driving a Run rides the **Remote Control bridge** through
> Anthropic's cloud. Tailscale only carries the **Launcher transport**.
> Replacing it only affects spawn / list / close."

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
- "depend on Tailscale" was used to mean the whole tool — resolved:
  Tailscale is only the **Launcher transport**; the **Remote Control
  bridge** is unaffected.
- "no install" was used to mean lighter overall — flagged: it constrains
  only the *phone* side; the Mac still runs **Launcher** code (and any
  transport's native bits).
