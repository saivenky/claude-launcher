# AttSD

The AttSD server spawns, observes, and answers local Claude Code Runs; the
Board is the page you drive it from. See `CONTEXT.md` for the domain glossary
and `docs/adr/` for the decisions behind the design.

## Agent skills

### Issue tracker

Issues and specs live as markdown under `.scratch/<feature>/` in this repo.
See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, default names, recorded as a `Status:` line in
each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
