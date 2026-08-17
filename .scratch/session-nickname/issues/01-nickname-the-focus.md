# 01 — Nickname the Focus

**Status:** landed — 9401dc0

Spec: `.scratch/session-nickname/spec.md` · ADR 0026 · `CONTEXT.md` (*Nickname*)

**Blocked by:** None — can start immediately.

## What to build

You can give the **Focus** a **Nickname** by tapping its header, and from then on
that is what the Board calls that **Session** — through close, **Resume**,
**Recover** and **Transfer**, and through a server restart.

Tap the Workspace row in the Focus header. It becomes a text field in place,
pre-filled with the current Nickname if there is one. Type, press Enter: the
field becomes the Nickname, sitting beside the Workspace on the same row.
Submit it empty and the Nickname is gone and the header reads as it did before.

The Nickname supersedes `aiTitle` on the Focus — when one is set, `.fabout`
shows nothing; when it is not, `.fabout` is unchanged. Scroll down until the
header condenses and both the Workspace and the Nickname are still legible.

## Notes

- The store is `.board-state.json`, a third dict beside `priority` and `snooze`,
  keyed by `sessionId`. Prune entries whose transcript is gone — at load only,
  never on save.
- `nickname` on the wire, `None` when unset, never `""`. Send it beside `one`;
  the client picks.
- `/api/nickname` is ungated: same-origin + JSON, no token. Client uses
  `postState`, the token-free helper `priority` and `snooze` already use.
- Row one is two floored items. The Nickname yields first, down to its floor,
  then the Workspace yields — see ADR 0026 for why neither may reach zero.
- `elideNickname` is tail-biased: keep the head, drop the end. A third function
  beside `elideWorkspace` and `elidePath`, not a branch inside either.
- The condensed header currently `display:none`s `.fabout` and keeps only
  `.fdir`. The Nickname must be on `.frow1`, not in the `.about` band.
- 24 character cap, enforced server-side as well as in the field.

## Acceptance criteria

- [ ] Tapping the Focus header's Workspace row opens an inline field; Enter
      commits, Escape cancels, empty commits as a clear
- [ ] The Nickname renders beside the Workspace on row one and survives the
      header condensing on scroll
- [ ] `.fabout` shows `aiTitle` only when no Nickname is set
- [ ] A Nickname set, then the Run closed and the Session resumed, is still
      there — same for a server restart
- [ ] A Nickname whose Session's transcript no longer exists is dropped at load;
      one whose cwd is merely gone is kept
- [ ] `nickname` is `None` and not `""` for a Session with no Nickname
- [ ] Names longer than 24 characters are rejected server-side
- [ ] At 390px with a long Workspace and a long Nickname, neither renders as
      zero characters
- [ ] Python and board tests pass: `python3 -m unittest discover -s tests`
