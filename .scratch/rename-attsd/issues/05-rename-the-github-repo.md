# 05 — Rename the GitHub repo

**What to build:** `saivenky/claude-launcher` becomes `saivenky/attsd` on
GitHub, and the local clone's `origin` points at the new URL. GitHub redirects
the old URL, so existing clones keep working — the `set-url` is hygiene, not a
rescue.

This is outward-facing: it renames a public repository. It is yours to do, not
an agent's.

**Blocked by:** None — can start immediately.

**Status:** landed

- [x] Repository renamed to `attsd` on GitHub
- [x] `git remote -v` shows the new URL
- [x] A `git fetch` succeeds against the renamed remote

## Comments

Done during the cutover. An empty `attsd` repo had been created first, so the script deleted it and then renamed `claude-launcher`, keeping the history, the issues and the redirect from the old URL. Visibility stayed public.
