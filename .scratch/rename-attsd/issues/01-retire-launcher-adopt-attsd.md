# 01 — Retire `Launcher`, adopt AttSD in the glossary and ADR record

**What to build:** the repo's domain vocabulary stops saying `Launcher` and
starts saying AttSD. `CONTEXT.md` is titled `# AttSD` and opens with a plain
functional sentence — no expansion, no joke; a glossary defines words, it does
not tell them. The term `Launcher` is removed from the glossary, its work split
between **Board** (the page you drive it from) and **server** — written
**AttSD server** in any passage where tmux's own server is also under
discussion, so the two are never confused. Every ADR that used `Launcher`
speaks the new vocabulary. A new ADR 0027 records the rename and the term's
retirement as a single decision: they are the same decision, because the rename
is why `Launcher` died.

ADR 0027 should state the trade-off honestly — that earlier ADRs were rewritten
to use a name that did not exist on their date, chosen over preserving the
record, so a reader who notices the anachronism finds the reason.

Prose only. Nothing executes differently after this ticket.

**Blocked by:** None — can start immediately.

**Status:** landed — 1bfeeff

- [x] `CONTEXT.md` titled `# AttSD`, opening paragraph flat and free of the expansion
- [x] `Launcher` no longer appears as a defined term; **Board** and **server** cover its meaning
- [x] Passages discussing both servers disambiguate as **AttSD server** vs **tmux server**
- [x] All ADRs previously using `Launcher` read correctly in the new vocabulary
- [x] ADR 0027 exists, recording rename + retirement together, and names the rewritten-history trade-off
- [x] `python -m unittest discover -s tests` and `ruff check .` pass
