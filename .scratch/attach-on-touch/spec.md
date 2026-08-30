# Attach is not offered where there is no terminal

The Board hands every Managed Run two handoffs: `↗` to the **Remote Control
bridge** and `❯` to **Attach** — a `tmux … new-session` line to paste into a
local terminal (ADR 0011). On a phone the second one cannot be taken: there is
no tmux on the phone, so the button is a control for a thing that device cannot
do.

`❯` renders only where a local terminal could plausibly exist: a device whose
*primary* pointer is fine. Both surfaces go — the compact `❯` in the queue row's
glyph strip and the labelled `attach ❯` on the Focus card — because the reason
is device-level, not surface-level.

## Not in scope

- **The server.** `/api/board` keeps sending `attach` on every Managed row. A
  device sniff in the transport would make the payload device-dependent, which
  the ETag and the test suite both assume it is not.
- **`copyAttach` / `legacyCopy`.** The insecure-origin clipboard fallback looks
  like it existed only for the phone path being hidden here. It did not: the Mac
  reached as `http://mac-mini` **from the Mac** is an insecure origin with a fine
  pointer, and ADR 0011 calls that the common case. It stays.
- **`CONTEXT.md`.** **Attach**'s entry already says *local terminal*. Which
  devices are offered the button is a Board rendering rule, and the glossary is
  not a spec.
