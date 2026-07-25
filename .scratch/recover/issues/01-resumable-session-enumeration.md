# 01 — Enumerate Resumable Sessions

**What to build:** A server-side list of **Resumable Sessions** and a `GET
/api/recoverable` that serves it — the read side Recover's picker renders.

A **Resumable Session** = a transcript on disk **+** a cwd that still exists **+**
no live Run. Concretely, per candidate: read the authoritative cwd from the
transcript (`_cwd_from_transcript`, already exists — the first line carrying a
`cwd`), drop it if `not os.path.isdir(cwd)`, drop it if its `sessionId` is in
`_live_session_ids()`.

- **Session-granularity, not dir.** This is *not* `_recent_dirs`, which dedups
  to one row per dir. Walk `*.jsonl` files, one row per Session. A single dir
  can yield several rows.
- **Spans every dir** — no `PROJECTS_ROOT` confinement (ADR 0002). `~/obsidian`
  must appear. This is the other place `_recent_dirs`' `prefix` filter must
  *not* be copied.
- **Hide dead-cwd Sessions** entirely — the `…/T/tmp…-vault/` graveyard. Not
  greyed, not counted.
- **Bounded, newest-first.** Serve the newest ~**30** by transcript mtime.
  Bound the *scan* too (mirror `_RECENT_DIRS_SCAN`): sort project dirs by their
  own mtime first, open only enough to fill the list — never walk a huge
  history. Cost is one cwd read per candidate; keep it to the head of the file.
- **Row payload:** `sessionId`, `dir` (display-tilde'd), `title`, `mtime`
  (epoch, for the client's relative render). `title` = the **first real user
  message** — skip skill-injected preambles (`Base directory for this skill…`)
  and bare `/slash-command` lines, same spirit as the `_first_user_msg`
  backstop in `list_runs`. Recovery-set flags come in slice 02.

`GET /api/recoverable` follows `/api/board` / `/api/tasks`: JSON, ETag if easy.
No token gate (read-only, like the board).

**Blocked by:** —

**Status:** ready-for-agent
