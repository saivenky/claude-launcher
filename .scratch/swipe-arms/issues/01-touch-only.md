# 01 — the drag is touch-only, so a mouse can select text

**Status:** landed — 8639223

`pointerdown` arms the drag for every pointer type (`web/board.js:2964-2968`).
On a desktop that is text selection: drag across the Scrollback to select a
sentence, travel 70px sideways, and the Focus moves out from under you.

The drag becomes touch-only: bail unless `e.pointerType === "touch"`. Desktop
keeps `wheel` and ←/→, which are the same move and already wired.

Per-event, never `matchMedia("(pointer: coarse)")` — a touchscreen laptop must
select with its mouse and swipe with its finger, and only the event knows which.

The **hold** (ADR 0026) shares this pointer stream and is *not* touch-only: a
long press with a mouse is a deliberate act, not a side effect of reading. Only
the drag half of the listener is gated.

## Acceptance

- A `pointerdown` with `pointerType: "mouse"` or `"pen"` followed by a 180px
  horizontal drag leaves the Focus where it was.
- The same drag with `pointerType: "touch"` still moves it.
- A mouse press-and-hold on a row still opens the Nickname field.
- `wheel` and ←/→ are untouched.
- An event with no `pointerType` at all (the stub DOM's default, and any
  synthesised event) is treated as touch, so existing gesture tests still
  describe a phone.
