# Nickname your Sessions

## The problem

The **Workspace** is the only human-readable identity a **Run** carries on the
**Board**, and it is the basename of the working directory. Three `claude`s in
`~/projects/claude-launcher` are three rows reading `claude-launcher`. The
Workspace answers *where am I*; nothing answers *which of these three*.

The Board is not silent — it is showing four different derived strings, none
chosen by you:

| Surface | Field | Source |
| --- | --- | --- |
| Focus header `.fabout` | `aiTitle` | `_ai_title`, `server.py:1652` — Focus only |
| Queue row `.qone` | `one` | `_last_msg` tail, 160 chars |
| Blocked queue row `.qone` | `one` | replaced by the **Ask** text, `server.py:2921` |
| Recover picker | `title` | `_first_user_msg`, `server.py:798` |

`aiTitle` is the only one that reads like a name, and it is on the one Run you
are already looking at. It also arrives around turn 50 — never at the moment you
spawn the second Session in a repo, which is the moment the confusion starts.

## The shape

A **Nickname**: a short name you type, on the **Session**, optional. See
`CONTEXT.md` (*Nickname*) and ADR 0026 for the decisions and their reasons. The
load-bearing ones:

- Stored per `sessionId` in `.board-state.json` beside `priority` and `snooze`.
  `sessionId` never changes across close / **Resume** / **Recover** /
  **Transfer**, so the Nickname survives all four for free.
- On the wire as `nickname`, `None` when unset — never `""`.
- It **replaces `one`** on every row, including **Blocked** rows where `one` is
  the Ask. The lane badge already carries `question`/`approval`; the full Ask
  survives on the Focus.
- `aiTitle` **stays Focus-only**. Focus chain: nickname → `aiTitle` → snippet.
  Row chain: nickname → snippet.
- On **row one** of the Focus header beside the Workspace, never in `.about` —
  the condensed header `display:none`s `.fabout` (`board.html:200-203`) and the
  Nickname must survive condense.
- **Both floored.** ADR 0023's bug was an item with no floor paying every
  shortfall. Nickname yields first, down to its floor, then the Workspace.
- `elideNickname` is **tail-biased** (keep the head), a third rule beside
  `elideWorkspace` (repo-biased, middle-out) and `elidePath` (segment-wise).
- Two entry points: **tap the Focus header** (primary), **long-press any row**
  (so Foreign Sessions, which never take the Focus, are reachable).
- **Inline edit in place.** No sheet, no reply-box prefix.
- Empty submit clears. **24 character cap.** Not unique.
- `/api/nickname` is **ungated** — same-origin + JSON, like `priority` and
  `snooze`. ADR 0007's token guards Respond only.
- **Pruned at load** when the transcript is gone.

## Surfaces to change

- Focus header row one — `board.js:1712-1770`, `.fdir` at `:1737`, `.fabout` at
  `:1717`
- Queue rows — `.qone` at `board.js:1967`
- Foreign rows — `.fgone` at `board.js:2014`
- Ask block — `.askws` at `board.js:1597`
- Recover picker — `.recovtitle2` at `board.js:723`
- Reply toast — `board.js:328` reads `f.title`, which is not on the payload, so
  it has been rendering the literal `"✓ sent — session is now working"` since
  ADR 0023 renamed the field

## Constraints from existing gestures

- Row tap is `setPinned` (`board.js:1968`); a long-press must not also pin.
- Swipe needs `|dx| > 70` (`board.js:2511`), so a stationary hold is safe.
- `input` is already in `SWIPE_BLOCK` (`board.js:2521`), so an inline field
  will not fight the Focus ring.
- No touch listeners, no `contextmenu`, no hold timers exist today — press-and-
  hold is a free affordance.

## Origin

`/grill-with-docs`, this session. Every decision above was put to the user and
confirmed, except the three noted in the run log (condensed-header behaviour,
the toast fix, and prune timing), which were taken on the tech lead's
recommendation under `/ship`.
