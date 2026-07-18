# tmux replaces iTerm as the Run substrate

A **Run** stops being an iTerm pane driven over AppleScript and becomes a
**tmux window** — one pane — inside a single detached session named
`claude-launcher` on a dedicated socket. Launch, list, close, **Observe**, and
**Respond** all move from `osascript` to the `tmux` CLI. The Launcher's model is
untouched: a Run is still the one thing it creates and destroys, still keyed by a
UUID, still correlated to its **Session** through `pane tty -> ps -> sessions/<pid>.json`.

## Context

The whole Run-discovery chain reads Claude Code's own state, not the terminal's:
a pane's tty leads through `ps` to `~/.claude/sessions/<pid>.json`. iTerm was
never load-bearing there — it only supplied a tty, a place to type, and a screen
to scrape. So `list_runs`, the transcript readers, the ETag/cache layer, and the
Board don't know what a terminal is, and none of them change here. The iTerm
coupling is six concrete spots: `launch_iterm`, `_LIST_SCRIPT`, `_CLOSE_SCRIPT`,
`_RESPOND_SCRIPT`, `_PANE_SCRIPT`, and the `user.cl_task` pane stamp.

Two things pushed the swap. Phone-launched Runs call `activate` and pile up as
visible iTerm tabs — a Run created from across the house yanks the Mac's
foreground and leaves a tab to close. And driving a GUI app over its AppleScript
dictionary is the fragile part of the design: it needs iTerm running and
focusable, it breaks under a screen lock, and the dictionary can shift under an
iTerm update. tmux is the idiomatic substrate for exactly this — detached
sessions with a stable, documented CLI — so building bespoke session management
on top of a terminal emulator reads as a workaround for not using the tool meant
for the job.

The focus theft alone is a one-line fix: deleting `activate` from the launch
script leaves the frontmost app untouched (verified — Finder stayed frontmost,
the tab was still created). That was considered and rejected as the whole answer:
it fixes focus but not the tab pile-up, and leaves every other Run verb puppeting
a GUI app. This ADR supersedes that path rather than shipping alongside it.

The port turned out to hinge on three tmux behaviours that are invisible until
you hit them, each of which looks like removable complexity to a later reader and
would be "simplified" straight back into a bug. They are recorded here for that
reason.

## Decision

**One detached server, one session, a window per Run.** A single `tmux -L
claude-launcher` server holds one session, `claude-launcher`; each Run is a
`new-window` in it with a single pane. Ensuring the server/session exists is the
direct analogue of today's `activate` + `if (count of windows) = 0 then create
window`. The pane is addressed for `send-keys` and `capture-pane`; its window
owns the size.

**Runs stay UUID-keyed; the UUID is a pane option, not tmux's id.** tmux pane
ids (`%12`) are *reused across a server restart*, so a phone holding a stale id
could pass the liveness guard and `send-keys` into an unrelated Session — the
worst failure this tool has. We keep generating our own UUID at launch and stamp
it as the pane option `@cl_run_id` (alongside `@cl_task`); `list-panes` resolves
UUID -> pane + tty in one call. `_UUID_RE`, the id contract, CONTEXT.md's "a Run
id is a UUID" story, and the tests keyed on it are all untouched.

**Width is pinned per-window, never globally.** ADR 0009's selector and
box-glyph parsers depend on a stable render width. tmux's default
(`window-size latest`) lets a narrow client that merely *attaches to look* reflow
a Run to its width and leave it there — silently corrupting the parse frame.
`window-size manual` fixes that, but setting it **globally** crashes the tmux
server on the next `new-window` (observed repeatedly on 3.6a). The working recipe
is `default-size 120x40` + global `window-size latest` (so `new-window` is safe)
+ per-window `set -w window-size manual` after each Run's window exists (so that
one Run's width is immune to any client). Verified end to end: a real 40-column
client attaching and detaching left the Run at 120x40.

**The launch line is typed, not passed as an argument.** `cl` is a zsh shell
function, not a binary. `tmux new-window 'cd X && cl'` runs the command through
`/bin/sh -c`, which has never sourced `.zshrc`, so `cl` is *not found* and the
Run dies in a pane nobody is watching. The window is created **bare** and the
launch line is delivered with `send-keys -l` into the live interactive shell —
a direct translation of iTerm's `write text`, which is why this preserves the
existing code shape.

## Considered options

- **iTerm minus `activate`.** The cheapest fix for the concrete half of the
  motivation, and free. Rejected as the *whole* answer: tabs still accumulate,
  and every Run verb keeps driving a GUI app over AppleScript. Kept as the
  fallback if the swap is deferred.
- **tmux hosted inside iTerm (`tmux -CC`).** Preserves the local glance by
  rendering tmux panes as native iTerm tabs. Rejected: it keeps *both*
  dependencies, is an iTerm-specific integration (so it buys almost none of the
  portability or stability), and reintroduces window-follows-client reflow — the
  exact parse hazard `window-size manual` exists to kill.
- **A Run = a tmux *session* (not a window).** Would make `tmux ls` mirror the
  Board and let `attach -t <run>` open exactly one Run. Rejected on language:
  tmux's own word for its container is "session," so this makes "session" mean
  both the durable thread *and* the Run — the precise four-way conflation
  CONTEXT.md exists to prevent. A window keeps "session" meaning only `sessionId`.
- **Widen `_UUID_RE` to accept `%N`.** Least code. Rejected: `%N` is unique only
  within one server lifetime, so a stale id can drive the wrong Run after a
  restart, and it churns CONTEXT.md and every UUID-keyed test.

## Consequences

- **ADR 0009's fragile pane parsing survives the swap unchanged.** Feeding a real
  detached-pane `capture-pane` of the trust prompt to the unmodified
  `_parse_selector` returned `{'options': ['Yes, I trust this folder', 'No,
  exit'], 'cursor': 0}`, with `_pane_input` correctly empty (no false "unsent
  text" warning). `capture-pane -p` returns only the visible frame, so 0009's
  "only the last contiguous run of option lines is live" scrollback guard becomes
  belt-and-suspenders rather than load-bearing — a strict improvement.
- **The macOS-only guard drops.** The Launcher no longer needs AppleScript or a
  GUI session, so it can run on any host with `tmux` + `claude`. This wasn't the
  motivation, but it comes for free.
- **A dead tmux server kills every Run silently.** iTerm quitting is at least
  *visible* — the app vanishes from the Dock. A detached tmux server dying takes
  all Runs with it and leaves nothing on screen. The Board should surface "no
  tmux server" as a distinct empty state rather than an ordinary empty list, so
  this failure is legible.
- **A pane created outside the Launcher has no `@cl_run_id`, so it is invisible
  to `list_runs`.** This is arguably correct — the Launcher only manages Runs it
  created — but it is a behaviour change from the iTerm walk, which saw every
  pane.
- **`respond_run`'s 0.15s bracketed-paste sleep is iTerm-specific and may be
  removable.** It exists because iTerm's `write text` wraps input in bracketed
  paste; `tmux send-keys -l` does not. The submit-as-separate-Enter split should
  stay, but the pause is a candidate to drop after testing.
- **`_clean_title` needs a second look.** tmux's `pane_title` defaults to the
  hostname rather than an iTerm-style titled tab; the `_first_user_msg` fallback
  already backstops an unusable title, but the strip logic assumes iTerm's
  glyph + `(profile)` shape.
