#!/usr/bin/env python3
"""Capture one rendered pane — and the transcript that explains it — as a fixture.

    tools/capture-widget.py <name> --pane %59
    tools/capture-widget.py <name> --run  <run-uuid>
    tools/capture-widget.py <name> --session <session-uuid>
    tools/capture-widget.py --list

Writes three files into tests/fixtures/ and one stanza into its README:

    <name>.pane   `tmux capture-pane -p`  — EXACTLY what `_pane_contents` sees
    <name>.ansi   `tmux capture-pane -p -e` — the same frame with its attributes,
                  kept because the current AskUserQuestion tab is marked by ANSI
                  that `-p` drops (ADR 0020's escape hatch depends on having it)
    <name>.jsonl  the last N conversational rows of the matching transcript,
                  which is where the pending tool_use — the WHAT of the Ask —
                  lives (ADR 0020)

The two frames are two `capture-pane` calls, so they can disagree if the pane
repaints between them — a spinner tick is enough. They are therefore CHECKED
against each other (escapes stripped, trailing space ignored) and re-taken, and
a pane that will not hold still is refused rather than written: a pair that does
not describe one frame is not evidence, and `.ansi` exists precisely to be
trusted when `.pane` has stopped parsing.

PRIVACY: `.jsonl` is a verbatim transcript tail — whatever you and Claude last
said, plus absolute paths. Read it before committing; pick a Session you are
happy to publish, or trim the rows by hand (and say so in the README stanza).

WHY THIS EXISTS. The Claude Code TUI is an unversioned external dependency with
no changelog, and the Board reads it by scraping a rendered pane. ADR 0020 found
four version-pinned assumptions in a single sitting, every one silent, and the
expensive part was never the fix — it was reconstructing what the renderer had
actually been painting. This makes that step one command, so a re-fit starts
from evidence instead of archaeology, and so the frame under test is a verbatim
capture rather than a literal somebody retyped (a retyped fixture agrees with a
hand-written parser by construction, which is how the iTerm-era `_ASK_PANE` went
on passing for months after the real renderer had moved).

The Claude Code VERSION is captured with it. tmux names the window after the
running version (`2.1.220`), so it costs nothing at capture time — and it turns
"which renderer is this fixture?" from archaeology into a line in the README.
Deliberately NOT read per poll: ADR 0014's one-read-per-poll discipline holds,
and this script is the place that pays for it.

Every capture added here automatically extends the test matrix — `tests/
test_server.py::PaneFixtureMatrixTests` globs this directory and runs every
`.pane` through every pane parser. That is the point: the `☒` answered-tab bug
was invisible until a human stumbled on it, and a matrix over captures is what
finds the next one.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import server  # noqa: E402

_FIXTURES = os.path.join(_ROOT, "tests", "fixtures")
# The pane fields this script joins on: id, the Launcher's Run stamp, the tty
# (which is how a pane is matched back to a `claude` process, and thence to a
# Session), and the window name — which IS the Claude Code version.
_FMT = "#{pane_id}\x1f#{@cl_run_id}\x1f#{pane_tty}\x1f#{window_name}"


def _panes() -> list[dict]:
    """Every pane on the Launcher's tmux server, joined to its Session.

    The join reuses `server`'s own helpers rather than reimplementing them: the
    fixture must be what the SERVER sees, so the reader is the server's."""
    out = server._tmux("list-panes", "-a", "-F", _FMT).stdout
    ttys = server._parse_claude_ttys(server._ps_output())
    meta = server._run_meta()
    rows = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        pane, run_id, tty, window = (line.split("\x1f") + ["", "", "", ""])[:4]
        pid = ttys.get(os.path.basename(tty.strip()), 0)
        m = meta.get(pid, {})
        rows.append({"pane": pane.strip(), "runId": run_id.strip(),
                     "tty": tty.strip(), "version": window.strip(),
                     "sessionId": m.get("sessionId", ""), "cwd": m.get("cwd", ""),
                     "status": m.get("status", "")})
    return rows


def _pick(rows: list[dict], args) -> dict:
    """The one pane the arguments name, or exit.

    Ambiguity is refused rather than resolved by taking the first: capturing the
    wrong pane is exactly the evidence failure this script exists to prevent, and
    a Session CAN have two panes (an Attach opens a grouped session)."""
    if args.pane:
        hit = [r for r in rows if r["pane"] == args.pane]
    elif args.run:
        hit = [r for r in rows if r["runId"] == args.run]
    else:
        hit = [r for r in rows if r["sessionId"] == args.session]
    if not hit:
        sys.exit("no pane matched — run with --list to see what is live")
    if len(hit) > 1:
        sys.exit("ambiguous: matched panes " + ", ".join(r["pane"] for r in hit) +
                 " — name one with --pane")
    return hit[0]


# Everything tmux paints with `-e` and drops with `-p`: SGR attributes, and the
# OSC 8 hyperlinks Claude Code's status line carries.
_ESC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;:?]*[ -/]*[@-~]")


def _same_frame(plain: str, ansi: str) -> bool:
    """Do the `-p` and `-e` captures describe ONE frame?

    Compared with the escapes stripped and each line right-trimmed, because that
    is the whole of the difference between the two encodings of a still pane —
    anything left over means the pane repainted between the two calls."""
    def flat(s: str) -> str:
        return "\n".join(ln.rstrip() for ln in _ESC_RE.sub("", s).split("\n"))
    return flat(plain) == flat(ansi)


def _capture_pair(pane: str, tries: int = 3) -> tuple[str, str]:
    """(`-p` frame, `-e` frame) of the same paint, or exit.

    Two calls cannot be atomic, so the pair is verified instead of assumed. A
    pane that keeps moving — a Run that is working rather than blocked — is
    refused: half the point of `.ansi` is to be the authority when `.pane` stops
    parsing, and a twin from a different moment cannot play that part."""
    for _ in range(tries):
        plain = server._tmux("capture-pane", "-p", "-t", pane).stdout
        ansi = server._tmux("capture-pane", "-p", "-e", "-t", pane).stdout
        if _same_frame(plain, ansi):
            return plain, ansi
    sys.exit(f"pane {pane} repainted between captures {tries}x — it is not "
             "holding still (is the Run working rather than blocked?). Nothing "
             "written: a -p and a -e from different frames are not a pair.")


def _tail(session_id: str, n: int) -> list[str]:
    """The last `n` conversational rows of a Session's transcript, verbatim.

    Conversational only (`user`/`assistant`), because the trailing rows of a
    blocked Session are summaries and meta that push the pending `tool_use` out
    of a small window. The lines are copied byte for byte — never re-serialised
    — so the fixture stays evidence."""
    path = server._transcript_path(session_id)
    if not path:
        return []
    keep = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") in ("user", "assistant"):
                keep.append(line.rstrip("\n"))
    return keep[-n:]


def _stanza(name: str, row: dict, when: str, tail: int) -> str:
    return "\n".join([
        f"## `{name}.*` — TODO: one line on what this capture reproduces",
        "",
        f"- **Claude Code {row['version'] or 'unknown'}** (the tmux window name at capture).",
        f"- Captured {when} from pane `{row['pane']}`, Session "
        f"`{(row['sessionId'] or '')[:8]}…`, in `{row['cwd'] or '?'}`.",
        f"- `{name}.pane` / `.ansi` — the frame, `-p` and `-e`.",
        f"- `{name}.jsonl` — the last {tail} conversational rows, ending on the "
        "pending `tool_use`.",
        "",
        "TODO: say what this frame shows that the others do not — the reason it "
        "is worth keeping.",
        "",
    ])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("name", nargs="?", help="fixture base name, e.g. ask_multi")
    ap.add_argument("--pane", help="tmux pane id, e.g. %%59")
    ap.add_argument("--run", help="Run id (the @cl_run_id UUID)")
    ap.add_argument("--session", help="Session id (the transcript UUID)")
    ap.add_argument("--rows", type=int, default=5,
                    help="conversational transcript rows to keep (default 5)")
    ap.add_argument("--list", action="store_true", help="show the live panes and exit")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing fixture of this name")
    args = ap.parse_args()

    try:
        rows = _panes()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(f"tmux unavailable on socket {server.TMUX_SOCKET}: {exc}")

    if args.list:
        for r in rows:
            print(f"{r['pane']:>5}  cc {r['version'] or '-':<8} {r['status'] or '-':<8} "
                  f"{(r['sessionId'] or '-')[:8]}  {r['cwd'] or ''}")
        return
    if not args.name or not (args.pane or args.run or args.session):
        ap.error("give a name and one of --pane / --run / --session (or --list)")

    row = _pick(rows, args)
    base = os.path.join(_FIXTURES, args.name)
    # A fixture is evidence, and evidence is not silently replaced. Overwriting
    # would also leave the old README stanza in place with the old version stamp
    # on it, so the record would be wrong in the one field it exists to carry.
    if not args.force and any(os.path.exists(base + ext)
                              for ext in (".pane", ".ansi", ".jsonl")):
        sys.exit(f"{args.name}.* already exists — pick another name, or --force "
                 "and update its README stanza (the version stamp changes too)")
    # The tmux window name IS the running Claude Code version — but only while
    # Claude Code is what renamed it. A stale or renamed window would stamp a
    # version this frame was not taken under, which is worse than no stamp: it is
    # a wrong answer to the one question the stamp exists to answer.
    if not re.fullmatch(r"\d+\.\d+\.\d+", row["version"]):
        sys.exit(f"pane {row['pane']} window name is {row['version']!r}, not a "
                 "Claude Code version — refusing to stamp a capture with it "
                 "(is `allow-rename` off, or is this not a Claude Code pane?)")

    plain, ansi = _capture_pair(row["pane"])
    tail = _tail(row["sessionId"], args.rows)

    os.makedirs(_FIXTURES, exist_ok=True)
    with open(base + ".pane", "w", encoding="utf-8") as fh:
        fh.write(plain)
    with open(base + ".ansi", "w", encoding="utf-8") as fh:
        fh.write(ansi)
    with open(base + ".jsonl", "w", encoding="utf-8") as fh:
        fh.write("\n".join(tail) + ("\n" if tail else ""))

    when = datetime.date.today().isoformat()
    stanza = _stanza(args.name, row, when, len(tail))
    readme = os.path.join(_FIXTURES, "README.md")
    # Appended, not merely printed: the version stamp is only useful if it is
    # beside the fixture when someone reads the directory a year later, and a
    # stamp that depends on remembering to paste it is the stamp we did not have.
    # Never twice for one name — the matrix reads the FIRST stanza it finds, so a
    # duplicate would hide the new stamp behind the stale one.
    with open(readme, encoding="utf-8") as fh:
        already = f"## `{args.name}.*`" in fh.read()
    if already:
        print(f"NOTE: README already has a `{args.name}.*` stanza — update its "
              f"version to {row['version']} by hand.", file=sys.stderr)
    else:
        with open(readme, "a", encoding="utf-8") as fh:
            fh.write("\n" + stanza)
    print(stanza)
    # What the parsers made of it, printed at capture time. A capture that does
    # not parse is the whole reason you are here — but a capture that parses
    # WRONG (a widget where you expected none, no cursor, no rows) is worth
    # seeing now rather than in a test failure ten minutes later.
    pr = server._read_pane(plain)
    w = pr.widget
    print(f"parsed: widget={'yes' if w else 'no'} rows={len(w.get('rows') or [])} "
          f"cursor={w.get('cursor')} tabs={len(w.get('tabs') or [])} "
          f"unsent={pr.unsent[:40]!r}", file=sys.stderr)
    if not tail:
        print(f"WARNING: no transcript rows for session {row['sessionId'] or '?'} — "
              "the .jsonl is empty and the fixture matrix will hold it to the "
              "pane invariants only.", file=sys.stderr)
    print(f"wrote {args.name}.pane/.ansi/.jsonl and appended a README stanza — "
          "fill in its two TODOs.", file=sys.stderr)


if __name__ == "__main__":
    main()
