# 01 — _md_to_html missing fenced code block case

Status: ready-for-agent

**Jotted:** 2026-08-01 16:57
**Promoted out of the inbox:** 2026-08-01, unchanged — the grounding below was
re-verified against `server.py` and still holds.

> Claude Launcher appears to have a bug where the code fenced blocks don't render properly on my screen and they seem to just be one contiguous paragraph which makes no sense.

## Grounding

The work lands in `server.py`, function `_md_to_html` (lines 1531–1569). This is the repo's bespoke "escape-first markdown" renderer (referenced as ADR 0006). It walks lines and dispatches on headings, tables, and lists. Any line that doesn't match those patterns falls into the paragraph collector at lines 1563–1568, which gathers consecutive non-blank lines and emits them as `<p>{' '.join(buf)}</p>` — joining with a single space.

There is no branch for fenced code blocks (``` or ~~~). When a fenced block appears in transcript prose, the opening fence, all code lines, and the closing fence are all swept into that paragraph collector and joined into one space-separated string inside a `<p>`. That produces exactly the "one contiguous paragraph" Sai sees.

`board.html` does have `.md pre` and `.md code` CSS (lines 286–287), but `_md_to_html` never emits a `<pre>` element, so that styling is never reached. The inline helper `_md_inline` (lines 1524–1528) handles only `**bold**` and `` `inline code` ``; it has no fence awareness either.

## Read

*Recorded by the harness — what this run actually opened.*

- web/board.html
- server.py

## Comments

**2026-08-01** — Confirmed still open, and confirmed *visually*. While shipping
ADR 0018/0019 the Board was screenshotted at 390x844 against a live **Run** whose
reply contained a fenced table; it rendered as one unbroken run of inline-code
spans wrapping across nine lines, exactly as this ticket predicts. So the bug is
not subtle in practice — it is the most conspicuous thing on the screen whenever
a Run answers with code, which is often.

Re-grounded against current `server.py`: `grep` for a fence, `<pre>` or any
backtick-run branch returns **nothing**, and the paragraph collector is still
there (now ~`:1565`), still ending `out.append(f"<p>{_md_inline(' '.join(buf))}</p>")`.
Nothing about this changed under ADR 0018/0019 — those moved only `board.html`
and added `web/theme.js`; `_md_to_html` was not touched.

One thing the fix now inherits: `board.html`'s `.md pre` / `.md code` rules are
tokenised and theme-aware as of ADR 0018, and `.md code` already carries
`--panel2` / `--q`. So whatever `<pre>` this eventually emits will theme itself
correctly and needs no new colour — but it **must not** hardcode one, per ADR
0018's closing note.
