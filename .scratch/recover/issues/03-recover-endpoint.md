# 03 — POST /api/recover: sequential bulk resume

**What to build:** `POST /api/recover` taking `{"sessionIds": [...]}` — resume
each as a Managed Run and return a per-Session result. This is Recover's write
side; it is a fan-out over the same `launch_run(workdir, resume_id=…)` that
`_handle_resume` already calls.

- **Server-side loop, sequential** — one request, not N client calls. The
  server is threaded; N parallel resumes would race on tmux window creation.
  Sequential spawning also paces the `claude` startup spike.
- **Partial-failure tolerant.** Per member, re-run `_handle_resume`'s own guards
  fresh (valid UUID, transcript exists, `not in _live_session_ids()`, cwd is a
  dir) — a dir can vanish, or the Session go live, between the picker's GET and
  this POST. A failed member is **skipped, not fatal**; keep going. Return
  `[{sessionId, ok, runId | message}]` so the picker can toast *"4 resumed, 1
  skipped: dir gone."*
- **No Focus grab.** Unlike `_handle_resume` (whose client `watch()`es the new
  Run into Focus), Recover returns run ids but the client does **not** focus any
  of them — they join the queue (CONTEXT: Rotation; new work never steals the
  Focus). Server side this is just "don't do anything special"; the contract is
  the client's (slice 04).
- **Ungated**, consistent with launch / resume / close / transfer — Recover
  approves nothing (the token gate exists only for Respond, ADR 0007). Register
  in `_API_POSTS` and the `do_POST` dispatch; same-origin still applies.
- `invalidate_runs()` once after the loop, not per member.

**Blocked by:** 01

**Status:** ready-for-agent
