# claude-launcher

A tool to spawn and manage local Claude Code sessions on a Mac from a
phone. It owns session *lifecycle* only; the running session's I/O is
owned elsewhere. This glossary fixes the language so the two are never
conflated.

## Language

**Launcher**:
The server that spawns, lists, and closes local Claude Code sessions,
and the page used to drive it.
_Avoid_: server, app (overloaded), backend

**Session**:
One running `claude` process on the Mac. Its lifecycle is owned by the
**Launcher**; its inner I/O is owned by the **Remote Control bridge**.
_Avoid_: tab, terminal, process

**Task**:
A named, preset **Session** launch defined in `tasks.py` — fixed workdir
plus an initial `/slash-command`, surfaced as a one-tap button. A
convenience over the generic "type a dir" launch; it still spawns an
ordinary **Session** (tagged `user.cl_task` so the list can label it).
_Avoid_: job, action, command (overloaded)

**Launcher transport**:
The path by which a phone reaches the **Launcher** endpoint (today: a
Tailscale-routed HTTP request to a LAN-bound port). This is the only
thing a Tailscale replacement would change.
_Avoid_: connection, network, tunnel (each names only one option)

**Remote Control bridge**:
Anthropic's cloud channel that carries a **Session**'s typing,
approvals, and output to the Claude app. Independent of the **Launcher
transport** — it does not flow over Tailscale.
_Avoid_: remote control (lowercase reads as a generic capability)

**Reachability scope**:
Where a phone must be for the **Launcher transport** to work:
*same-LAN* (home Wi-Fi only) vs *anywhere* (cellular / foreign network).
_Avoid_: access, availability

## Relationships

- A **Launcher** spawns and closes many **Sessions**
- A **Session**'s lifecycle flows over the **Launcher transport**; its
  I/O flows over the **Remote Control bridge** — different channels
- A **Launcher transport** choice is bounded by the required
  **Reachability scope**

## Example dialogue

> **A:** "If we drop Tailscale, do we lose the ability to drive a
> **Session** from the phone?"
> **B:** "No — driving a **Session** rides the **Remote Control
> bridge** through Anthropic's cloud. Tailscale only carries the
> **Launcher transport**. Replacing it only affects spawn/list/close."

## Flagged ambiguities

- "depend on Tailscale" was used to mean the whole tool — resolved:
  Tailscale is only the **Launcher transport**; the **Remote Control
  bridge** is unaffected.
- "no install" was used to mean lighter overall — flagged: it constrains
  only the *phone* side; the Mac still runs **Launcher** code (and any
  transport's native bits).
