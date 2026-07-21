# 03 — The Board lists Foreign Runs, outside the triage surface

**What to build:** Surface the **Foreign Runs** found in slice 02 on the **Board**, in their own quiet section — visible, never drivable.

Per ADR 0012 and CONTEXT.md's relationships:

- Never **Blocked**, never takes the **Focus**, never enters **Rotation**. A Foreign Run has no **rendered pane** to read a blocker from and no **Respond** to answer one with; a row you cannot answer would make the queue lie.
- No **Respond**, no **Attach** (`❯`), no close (`×`) — none of them have anything to act on.
- `↗` to the **Remote Control bridge** is fine if the Run is bridged: `bridgeSessionId` is terminal-independent.
- Shows what it can: title, dir, status, last message.

The consequence to keep legible: a Foreign Run sitting on a permission prompt is *silent* on the phone. That is intended — you started it by hand at the Mac.

**Blocked by:** 02

**Status:** resolved

## Comments

Shipped in `dbe559d`. Foreign Runs arrive on their own payload key rather than in `items`, which makes "never Blocked, never the Focus, never in Rotation" structural instead of a rule every lane has to remember — the Focus discipline reads focus/upnext/watching/snoozed/dormant, and a Foreign Run is in none of them. The row is a `div` rather than a `.qbody` button, so there is no tap target to pin one with.

The projection is a whitelist, not a copy: `runId`, `attach` and `pid` are dropped rather than blanked. `counts` stays Managed-only.
