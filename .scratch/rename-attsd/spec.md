# Rename claude-launcher to AttSD

The project is renamed **AttSD** — *Assistant to the Software Developer*. The
`tt` carries "to the". The expansion is printed at the top of the README; the
reference it plays on is never named in the repo.

## Decisions

**Name.** `AttSD` in prose, `attsd` in anything machine-readable (repo, socket,
launchd label, log, env prefix). Uppercasing the env var flattens the `tt` —
shell convention outranks the joke.

**Glossary.** `Launcher` is retired. Its work splits between **Board** (the page)
and **server**, written **AttSD server** wherever tmux's own server is also in
play. `CONTEXT.md` stays flat — it defines words, it does not tell jokes.

**Runtime identifiers.**

| now | after |
| --- | --- |
| `CLAUDE_LAUNCHER_TMUX_SOCKET` | `ATTSD_TMUX_SOCKET` |
| tmux socket `claude-launcher` | `attsd` |
| launchd label `com.saivenky.claude-launcher` | `com.saivenky.attsd` |
| `/tmp/claude-launcher.log` | `~/Library/Logs/attsd.log` |

The log moves off `/tmp` because a LaunchAgent's log should survive a reboot.

**History is rewritten, not preserved.** ADR prose is rewritten to say AttSD.
Fixtures are **re-captured**, never string-replaced: `.pane` and `.ansi` are
hard-wrapped terminal frames, and swapping a 15-character name for a 5-character
one yields wrap points no terminal could have produced. ADR 0027 records the
rename and the retirement of `Launcher` as one decision.

**Cutover cannot be run from inside the thing being renamed.** The agent session
driving this work is a pane on the socket that gets drained, in the directory
that gets renamed. So the work splits:

- *Reversible* — prose, ADR, runtime identifier strings, the live-Run manifest.
  Lands as commits.
- *Irreversible* — draining 8 live Runs, two directory renames, the launchd
  reload. One runbook, run by hand from a terminal outside tmux.

Seven Runs on the socket belong to other repos (`caddy` x2, `jot` x2, `tempo`
x2, `strength-log`). tmux cannot move a window between servers, so they are
drained too — hence the manifest, which is captured before anything dies and
drives the resume afterwards. This is safe only because Runs are disposable and
Sessions are durable (ADR 0002).

Renaming the repo directory also changes the slug Claude Code derives from the
cwd, so `~/.claude/projects/-Users-skandallu-projects-claude-launcher` and its
27 session files are renamed in the same step. Otherwise this repo's history
splits across two slug directories and `--resume` shows half of it.
