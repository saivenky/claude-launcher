# Terminal handoff attaches to the live Run, it does not resume

The **Board** grows a per-Run `❯` that copies a `tmux … new-session -t
claude-launcher \; select-window` line, opening the *live* Run's own tmux
window in a local terminal — the local-terminal twin of the `↗` handoff to the
**Remote Control bridge**. It is an **Attach**, not a **Resume**: it drives the
existing Run by hand, it never starts a new one.

## Context

`↗` already hands a live Run to the Claude app over the bridge. There was no
local-terminal equivalent — to get a Run onto the Mac's own keyboard you retyped
the working dir and pasted the sessionId, or scrolled `tmux ls` by hand. `❯`
fills that gap with one copy-to-clipboard command.

The obvious reading of "open this session from a terminal" is `cl --resume
<sessionId>` — and `_resume_cmd` already builds exactly that line. It is the
wrong verb here. Everything the Board lists is a *live* Run, and the live-Run
guard refuses Resume on a Session that already has one (at most one live Run per
Session; a transcript is never forked). So a Resume line would fail on every
Board row. While a Run is live, the only terminal route onto it is tmux attach
onto its existing window.

## Decision

**`❯` attaches; it does not resume.** The per-Run copy string opens the live
window in a terminal. Resume stays what it is — an **Intake** verb for a *dead*
Session, structurally refused while the Run is live.

**A grouped, self-cleaning view, not a plain attach.** The string is
`tmux -L claude-launcher new-session -t claude-launcher \; set
destroy-unattached on \; select-window -t @N`.

- A plain `attach -t claude-launcher` joins the one shared session, and tmux
  clients on a shared session share the *active window*. Attach a second
  terminal to a second Run and both terminals yank to it — the "why did my other
  window jump" surprise.
- `new-session -t` opens a grouped session: same windows, but its own active
  window and size, so each terminal sits on its own Run independently.
- `set destroy-unattached on` makes that throwaway session evaporate the moment
  you detach or close the terminal, so `tmux ls` never accumulates junk.
- `select-window` is mandatory — a bare attach lands on whatever window is
  *active*, which is not your Run.

**The server bakes the window id.** The client holds the Run UUID but not its
tmux window, so the server resolves it: `#{window_id}` is added to the existing
`list-panes` walk (`_PANE_FMT`) — one call, no new endpoint — and each row is
handed a ready-to-paste `attach` string, exactly as `bridge` rides each row
today. No user input is interpolated into the string, so there is no injection
surface and no token gate. The string is *served* over the **AttSD
transport**; the attach connection itself is a local tmux client that never
touches the transport.

## Considered options

- **`cl --resume <sessionId>` (the resume line).** The literal reading of the
  request, and the code already exists (`_resume_cmd`). Rejected: the Board only
  shows *live* Runs, and the live-Run guard refuses Resume on a running Session,
  so this would fail on every row. Resume is the verb for a *closed* Session —
  which the Board does not list.
- **Plain `attach -t claude-launcher \; select-window`.** Shortest line, no
  throwaway session. Rejected: shared-session clients share the active window, so
  a second attach yanks the first terminal to a different Run. Grouped +
  `destroy-unattached` buys independence for one extra clause and self-cleans.
- **A durable, self-re-resolving string** keyed on `@cl_run_id` at paste time
  (immune to the id-reuse hazard below). Rejected for now: it is a two-command
  shell line no human wants to read off a clipboard, and the hazard's real-world
  window here is seconds. It is the fix to reach for if attaches start landing on
  the wrong Run.
- **A Run = a tmux *session*** (so `attach -t <run>` targets it directly, no
  grouped view). Already rejected by ADR 0010 on language grounds — it makes
  "session" mean both the durable thread and the Run. The grouped view is an
  *ephemeral* session, not a Run container, so it does not reopen that
  conflation.

## Consequences

- **Window ids are reused across a tmux server restart** — the same `%N`/`@N`
  hazard ADR 0010's UUID stamp exists to dodge. A string copied *before* a
  restart attaches to the wrong Run *after* one. Accepted because this is
  copy-and-paste-*now* on the same Mac (staleness ≈ seconds), not a phone holding
  an id for minutes; the re-resolving variant above is the fix if it bites.
- **Two axes decide whether `❯` is drawn, and they are decided opposite ways.**
  *Origin — the copy degrades, not the button.* `navigator.clipboard.writeText`
  needs a secure context — fine at localhost, absent when the Board is reached
  over plain HTTP by hostname (e.g. `http://mac-mini`) or the Tailscale phone
  path (ADR 0001). An earlier revision *hid* the button off a secure context, but
  that also hid it on the Mac whenever it was opened by hostname rather than
  `localhost` — the common case. So on this axis the button always shows, and
  `copyAttach` falls back to a synchronous `execCommand('copy')` off an off-screen
  textarea, which still lands the string on a real clipboard on an insecure
  origin; a secure context (e.g. Tailscale Serve over HTTPS) restores the native
  one-tap `navigator.clipboard` write.
  *Device — the button itself goes.* Where the **primary** pointer is coarse, `❯`
  is not offered at all: both surfaces (the queue row's compact glyph and the
  Focus card's `attach ❯`) carry an `attach` class that `board.html` hides under
  `@media(pointer:coarse)`. The string is a `tmux` line and a phone runs no tmux,
  so there is nothing on that device for it to land in — the Universal Clipboard
  hop to the Mac's terminal, which this bullet once kept as the phone's fallback,
  is a hand-off between two machines rather than anything the Board can offer.
  `pointer`, never `any-pointer`: the question is the *primary* pointer, so a
  laptop with both a trackpad and a touchscreen reads `fine` and keeps its `❯` —
  that machine has a terminal, and the button is for it. The rule lives entirely
  in the stylesheet: `/api/board` goes on serving `attach` on every Managed row,
  so the payload and its ETag stay device-blind, and `display:none` takes the
  button out of the tab order rather than leaving it for a screen reader on a
  device that could never spend it.
- **Attach and Respond drive the same pane without conflict.** Respond's
  `send-keys` and Observe's `capture-pane` target the *pane* directly, never as
  attaching tmux clients, so a human attached-and-typing and the phone responding
  coexist on one pane — which is the whole point of Attach. ADR 0009's pane parse
  is likewise unaffected: `capture-pane -t <pane>` does not care which window a
  client made active.
- **A closed Run has no `❯`.** No live window, no `attach` field, no button — the
  same shape as ADR 0010's "a pane with no `@cl_run_id` is invisible to
  `list_runs`." To pick a *dead* Session back up from a terminal you **Resume**
  it, not Attach.
