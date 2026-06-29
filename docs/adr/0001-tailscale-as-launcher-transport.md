# Tailscale as the launcher transport

We keep Tailscale as the **Launcher transport** (how a phone reaches the
spawn/list/close endpoint) and reject moving to iroh or a public tunnel.
The launcher has zero app-auth by design — it leans entirely on a network
boundary — and it spawns shell sessions on the Mac, so the threat model
is unforgiving.

## Context

Reaching the Mac from cellular ("anywhere" reachability) behind NAT
forces exactly one of two mutually exclusive shapes:

- **Overlay** — a client app on the phone (Tailscale, WireGuard,
  ZeroTier, iroh-native). Boundary stays "my devices"; Mac is never
  publicly exposed.
- **Public ingress** — a tunnel (Cloudflare, ngrok, iroh-WASM). No phone
  app, but the port becomes internet-reachable, so real auth is now
  mandatory.

There is no "no phone app + no public exposure + works on cellular"
option; it cannot exist behind NAT. The stated goal was "lighter, no
daemon/app install."

## Considered options

- **iroh (native phone app)** — gives iroh's real win (per-device NodeId
  allowlist, no public HTTP exposure), but still an install, and requires
  rewriting the stdlib server as an iroh node. Heavier, not lighter.
- **iroh (WASM browser page)** — no phone install, but you host a WASM
  client publicly, it runs relay-only through n0's servers speaking a
  custom protocol, the page must carry the credential (weak auth), and
  client+server are rewritten. Heaviest *and* weakest for this tool.
- **WireGuard / ZeroTier / Netbird** — still need the phone app; Tailscale
  is the managed-WireGuard version of these. No win.
- **Cloudflare Tunnel + Cloudflare Access** — the one defensible
  no-Tailscale path: phone opens a normal URL, zero install, works
  anywhere; `cloudflared` on the Mac, identity gate via Access (not a
  hand-rolled token). Cost: Cloudflare account, a domain, public ingress,
  trusting an IdP instead of a flat network.

## Decision

Stay on Tailscale. Its only cost is a free, quiet, one-time phone app;
every alternative either keeps that app (no win) or removes it by adding
public exposure + mandatory auth + a Mac daemon (worse posture for a
shell-spawner). iroh is specifically the worst fit here — heavier *and*
weaker under these constraints.

## Escape hatch

If the priority ever flips to "kill the phone app, accept public
exposure," use **Cloudflare Tunnel + Cloudflare Access** — not iroh.
