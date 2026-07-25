# Recover: bring back the Sessions that were live before a restart

A machine restart kills every **Run** — the tmux server and every `claude`
process — while every **Session** survives as a `.jsonl` on disk. The Board
comes back empty (it lists only *live* Runs), and the only way back into a
Session today is to paste its `sessionId` into the resume box, which you don't
have. **Recover** closes that gap: a picker that lists the **Resumable
Sessions**, pre-ticks the ones judged live at the restart (the **recovery
set**), and resumes the batch with one tap.

This is the "recent-Sessions quick-pick on the resume box" the Transfer spec
deferred as *"worth building; not this."* Now it's this.

See ADR 0013 for the discovery-over-fidelity decision, ADR 0002 for why resume
spans every Session (not just `PROJECTS_ROOT`), and CONTEXT.md for **Resumable
Session** / **Recover** / **recovery set**.

## Why

- **A dead end after a reboot.** Resume needs a `sessionId` you must already
  know. After a restart you don't — so the Sessions you were mid-thought on are
  reachable only by hand-scanning `~/.claude/projects/*/*.jsonl` by mtime. (We
  did exactly that by hand once; this is that, as a feature.)
- **Resume isn't discoverable.** The capability exists; the affordance to
  *browse* what you can resume does not.

## Shape

- **Recover is a bulk Resume and nothing more.** It creates only Managed Runs,
  never touches a Session's file, obeys the one-live-Run guard per member, and
  loses the same in-flight turn Resume/Transfer already lose. No new lifecycle
  power — a discovery + fan-out layer over `launch_run(resume_id=…)`.
- **Discovery, not fidelity.** The Launcher persists no snapshot of what was
  live. The recovery set is a heuristic recomputed each open (ADR 0013).
- **Candidates = Resumable Sessions**: transcript on disk, cwd still exists, no
  live Run. Session-granularity (not dir — one restart batch held three
  strength-log Sessions). Spans every dir, `~/obsidian` included (ADR 0002).
- **Dead-cwd Sessions are hidden**, not greyed — the machine is littered with
  ephemeral `…/T/tmp…-vault/` dirs that can never be resumed.
- **The recovery set** (pre-ticked): a recency-cluster anchored on the newest
  candidate, chaining older while each gap ≤ **G** and total span ≤ **S**,
  capped at a phone-sized **N** — `G=15min`, `S=90min`, `cap=12`, plain tunable
  constants. Anchoring on the newest *candidate* (not session) self-heals: a
  resumed member goes live and drops out, sliding the anchor to the next.
- **Consent-based surfacing.** An intake button carries the set's count
  (`resume ⁵`) — the nudge that catches your eye on the empty post-reboot
  Board. No auto-open, no auto-resume. You tap → picker opens pre-ticked → you
  confirm.
- **No Focus grab.** Recovered Runs join the queue as a count; you pick what to
  open (Rotation: new work never steals the Focus). Unlike single paste-resume,
  which still focuses the one Run you deliberately named.
- **Row:** `dir · first-real-user-message · relative-last-active` — the
  last field is the mtime the cluster is built from, so you can see *why* a row
  is or isn't pre-ticked.

## Out of scope

- **An all-history browser.** The list is bounded (newest ~30). Older Sessions
  stay reachable only via the existing paste-`sessionId` box.
- **Persisting the live set** for exact restore. Rejected — ADR 0013.
- **An LLM-generated title per row.** Too costly for a list; the first real
  user message is the title.
- **Auto-recover on boot.** Against the consent ethos; the count badge is the
  whole nudge.

## Slices — all shipped

1. `01-resumable-session-enumeration` — candidate list + `GET /api/recoverable`. `c4e6750`
2. `02-recovery-set-heuristic` — recency-cluster pre-tick, G=15/S=90/cap 12. `b003441`
3. `03-recover-endpoint` — `POST /api/recover`, sequential, partial-failure tolerant. `6a16055`
4. `04-picker-and-count-badge` — bottom-sheet picker + count badge, no Focus grab. `811f71a`

Green at ship: 214 Python tests, 45 JS board tests, ruff clean, `node --check`
clean. **Not yet exercised end-to-end against a live launcher** — the running
process must restart to load the new `server.py` (`web/` is already hot-served,
so the UI is live but its endpoints 404 until then). Slice 04's phone layout is
pending a human taste pass.
