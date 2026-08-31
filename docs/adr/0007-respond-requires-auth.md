# The server responds to Runs, and therefore requires a shared secret

The AttSD server gains **Respond**: `POST /api/respond` injects input into a live
**Run**'s iTerm pane over the **AttSD transport** — a typed reply
(submitted), or a whitelisted key sequence that drives a selector (a permission
menu, an AskUserQuestion). This is the server's own driving channel,
independent of the **Remote Control bridge**. Because it can *approve* a tool
call, it is gated by a shared secret; the read-only Board is not.

## Context

Until now the server owned lifecycle (spawn / close / **resume**) plus
read-only **Observe**. [ADR 0001](0001-tailscale-as-launcher-transport.md)
leaned entirely on a network boundary with no app-auth, reasoning about a
"shell-spawner." Respond changes the server's *nature*: it can now type into,
and approve permission prompts in, any live Session. That removes the
human-in-the-loop that currently backstops a destructive tool call — a caller
who reaches the port could spawn a Run and approve its own `rm -rf`.

That is a categorical escalation over spawn/close, and the mechanism was proven
before committing: AppleScript `write text` into the pane drives everything —
free text submits, arrow/enter drive a selector, and Claude Code's own input
queue absorbs a reply sent to a busy Run. So feasibility was never the
question; blast radius was.

The chosen point (see the design conversation): **ship the full capability, and
make it the trigger to finally require real auth** — "approve any permission
with no auth" is indefensible even on a trusted network. The rejected
alternatives were to keep no-auth and pretend nothing changed, or to neuter the
feature by forbidding permission approvals (which is most of the point of
"unblock from one screen").

## Decision

- **Respond is off unless `CLAUDE_LAUNCHER_TOKEN` is set.** Unset → 403; the
  read-only Board is unaffected. Enabling the dangerous verb is an explicit,
  opt-in act, not a default.
- **The token is a shared secret**, compared with `hmac.compare_digest` (constant
  time), sent by the client in the JSON body. It is **never served by the
  server** — the browser holds it in `localStorage` and the user enters it once,
  so merely reaching `GET /` does not yield it. That is what makes it real
  defense-in-depth over the network boundary rather than theatre.
- **It inherits the existing structural defenses.** `/api/respond` is a POST
  under the same same-origin + `application/json` rule as every other mutation
  (ADR 0003), so CSRF stays blocked structurally, before the token is even read.
- **It acts only on a currently-live `claude` Run**, exactly as `close` does — a
  stale or bogus `runId` no-ops. Reply text is `applescript_quote`-escaped and
  length-capped; keys come from a fixed server-side map, so a client drives a
  selector by name (`down`, `enter`) and can never supply a raw escape sequence.

## Consequences

- **The server is now a driver, not only an observer.** `CONTEXT.md` grows the
  verb **Respond** as the two-way sibling of **Observe**, and the old invariant
  "it never types, answers, or approves — those belong to the bridge" is amended:
  those are now Respond's job (AttSD transport) *or* the bridge's (cloud).
- **The bridge is no longer the only way to drive a Run** — but it stays the
  rich, single-session channel for deep work; Respond is the multiplexed,
  triage-speed one. They coexist by design.
- **Auth covers Respond only, for now.** Launch / resume / close keep their
  prior network-trust-only posture. That is a deliberate minimum: Respond is the
  new escalation, so it is what the secret guards first.
- **Exposure is still opt-in twice over.** The token gates the verb; binding
  beyond `127.0.0.1` is a separate, explicit choice. Neither happens by default.

## Escape hatch

If the threat model tightens (the port faces a less trusted network), extend the
same `hmac` check from `/api/respond` to *all* mutations — the check has one
home, and the client already knows how to attach the token. If per-session
authorization is ever wanted (approve for project X, not Y), the token becomes a
capability lookup rather than a single global compare; the call site does not
move.
