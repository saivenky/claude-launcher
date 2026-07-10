# claude-launcher

A tool to spawn and manage local Claude Code sessions on a Mac from a
phone. It owns *lifecycle* only; the running session's I/O is owned
elsewhere. This glossary fixes the language so the two are never
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
One `claude` process executing a **Session** — concretely, an iTerm pane
running `claude`. This is the only thing the Launcher creates and
destroys. A Run embodies exactly one Session; ending a Run leaves its
Session intact. At most one live Run per Session.
_Avoid_: session (that's the durable thread here), tab, terminal,
process, instance

**Resume**:
Start a new **Run** on an existing **Session** (via
`claude --resume <sessionId>`). Distinct from re-attaching to a *live*
Run, which is the **Remote Control bridge**'s job, not the Launcher's.
_Avoid_: reopen, restore, continue (overloaded by `claude --continue`)

**Observe**:
Read a live **Run**'s externally visible state — which **Session** it is
executing, its working directory, whether it is busy/waiting/idle, when it
was last active, its most recent message — without driving it. The
Launcher's only read capability. Observing is strictly one-way: it never
types, answers, or approves. Those belong to the **Remote Control
bridge**.
_Avoid_: monitor, watch, view, read (each also suggests two-way)

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
- A **Launcher transport** choice is bounded by the required
  **Reachability scope**
- A **Dispatch** produces no **Run** and no **Session**, so none of the
  Launcher's other verbs apply to it: it cannot be **observed**, closed, or
  **resumed**. Spawning it is the whole interaction
- A **Task** and a **Dispatch** are told apart by one field — `command`
  starts a **Run**, `exec` starts a Dispatch. Never both

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

- "session" names four different things in Claude Code itself — resolved
  in favour of the identifier a human actually handles:
  - `~/.claude/sessions/<pid>.json` — a live process. Here: a **Run**.
  - `sessionId` — the durable thread. Here: a **Session**. *This one wins
    the word*, because it is the only one the user ever sees or types.
  - `bridgeSessionId` — the **Remote Control bridge**'s channel. Here: a
    *bridge channel*, never a Session.
  - iTerm's `sessions of tabs` — a terminal pane. Here: a **Run**'s pane,
    an implementation detail.
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
