# 05 — Rename the GitHub repo

**What to build:** `saivenky/claude-launcher` becomes `saivenky/attsd` on
GitHub, and the local clone's `origin` points at the new URL. GitHub redirects
the old URL, so existing clones keep working — the `set-url` is hygiene, not a
rescue.

This is outward-facing: it renames a public repository. It is yours to do, not
an agent's.

**Blocked by:** None — can start immediately.

**Status:** ready-for-human

- [ ] Repository renamed to `attsd` on GitHub
- [ ] `git remote -v` shows the new URL
- [ ] A `git fetch` succeeds against the renamed remote
