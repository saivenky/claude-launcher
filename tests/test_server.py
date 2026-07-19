import json
import os
import shutil
import socket
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

_RUN = "11111111-1111-1111-1111-111111111111"      # a Run id (tmux window)
_GOOD = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"     # transcript + existing cwd
_LIVE = "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"     # Session with a live Run
_GONE = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"     # transcript, but cwd deleted
_UNKNOWN = "DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD"  # no transcript


class _HttpCase(unittest.TestCase):
    """Serves the real Handler on a loopback port and posts JSON to it."""

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _origin(self):
        return f"http://127.0.0.1:{self.port}"

    def _raw(self, method, path, *, data=None, headers=None):
        req = urllib.request.Request(
            self._url(path), data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
            finally:
                e.close()

    def _post(self, path, obj, *, origin=True, ctype="application/json"):
        headers = {"Content-Type": ctype}
        if origin:
            headers["Origin"] = self._origin()
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        status, text, _ = self._raw("POST", path, data=body, headers=headers)
        try:
            return status, json.loads(text)
        except ValueError:
            return status, {"message": text}


class EscaperTests(unittest.TestCase):
    def test_shell_quote_escapes_single_quote(self):
        self.assertEqual(server.shell_quote("a'b"), "'a'\\''b'")


class ResolveDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(os.path.join(os.path.dirname(__file__), "_tmpfix"))
        os.makedirs(os.path.join(self.tmp, "ok"), exist_ok=True)
        self._saved_root = server.PROJECTS_ROOT
        self._saved_default = server.DEFAULT_DIR
        server.PROJECTS_ROOT = self.tmp
        server.DEFAULT_DIR = self.tmp

    def tearDown(self):
        server.PROJECTS_ROOT = self._saved_root
        server.DEFAULT_DIR = self._saved_default
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_subdir_and_blank_default(self):
        self.assertEqual(server.resolve_dir("ok"), os.path.join(self.tmp, "ok"))
        self.assertEqual(server.resolve_dir(None), self.tmp)

    def test_outside_root_rejected(self):
        for bad in ("../etc", "/etc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server.resolve_dir(bad)

    def test_nonexistent_rejected(self):
        with self.assertRaises(ValueError):
            server.resolve_dir("does-not-exist-xyz")


class RecentDirsTests(unittest.TestCase):
    """The launch input's quick-pick list, derived from the cwd recorded in one
    (newest) transcript per ~/.claude/projects dir."""

    def setUp(self):
        self.tmp = os.path.realpath(os.path.join(os.path.dirname(__file__), "_dirfix"))
        self.root = os.path.join(self.tmp, "root")
        self.state = os.path.join(self.tmp, "state")
        for sub in ("alpha", "beta", "nested/deep"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        self._saved_root = server.PROJECTS_ROOT
        server.PROJECTS_ROOT = self.root

    def tearDown(self):
        server.PROJECTS_ROOT = self._saved_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _proj(self, slug, cwd, mtime):
        """A project dir holding one transcript whose first line records `cwd`,
        stamped with `mtime` so ordering is deterministic."""
        d = os.path.join(self.state, slug)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, slug + ".jsonl")
        with open(p, "w") as fh:
            fh.write(json.dumps({"type": "user", "cwd": cwd}) + "\n")
        os.utime(p, (mtime, mtime))
        os.utime(d, (mtime, mtime))

    def test_newest_first_filtered_and_deduped(self):
        self._proj("d-beta", os.path.join(self.root, "beta"), 2000)
        self._proj("d-nested", os.path.join(self.root, "nested/deep"), 1000)
        self._proj("d-alpha", os.path.join(self.root, "alpha"), 3000)
        self._proj("d-alpha2", os.path.join(self.root, "alpha"), 5000)  # dup cwd, newest
        self._proj("d-outside", "/etc", 4000)                           # outside root
        self._proj("d-gone", os.path.join(self.root, "ghost"), 4500)    # cwd missing
        os.makedirs(os.path.join(self.state, "d-empty"), exist_ok=True)  # no transcript

        dirs = server._recent_dirs(base=self.state)
        # alpha floats to top on its newest occurrence; dup collapses; /etc and
        # the deleted-project cwd are dropped; nested paths kept verbatim.
        self.assertEqual(dirs, ["alpha", "beta", "nested/deep"])

    def test_missing_state_dir_is_empty(self):
        self.assertEqual(server._recent_dirs(base=os.path.join(self.tmp, "nope")), [])

    def test_capped_at_max(self):
        for i in range(server._RECENT_DIRS_MAX + 5):
            sub = f"p{i:02d}"
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
            self._proj(f"d-{sub}", os.path.join(self.root, sub), 1000 + i)
        dirs = server._recent_dirs(base=self.state)
        self.assertEqual(len(dirs), server._RECENT_DIRS_MAX)


class RecentDirsApiTests(_HttpCase):
    """GET /api/dirs wraps the recent-dirs list as data (ADR 0008), served
    fresh so the quick-pick reflects work done since page load."""

    @classmethod
    def setUpClass(cls):
        cls._saved = server._recent_dirs
        server._recent_dirs = lambda *a, **k: ["alpha", "nested/deep"]
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server._recent_dirs = cls._saved

    def test_dirs_payload_shape(self):
        status, body, headers = self._raw("GET", "/api/dirs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"dirs": ["alpha", "nested/deep"]})
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class HttpEndpointTests(_HttpCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_launch = server.launch_run
        server.launch_run = lambda *a, **k: _RUN  # stub out tmux
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_run = cls._saved_launch

    def test_cross_origin_post_blocked(self):
        status, body = self._post("/api/launch", {"dir": ""}, origin=False)
        self.assertEqual(status, 403)  # no Origin header at all -> fail closed
        self.assertIn("origin missing", body["message"])

    def test_foreign_origin_post_blocked(self):
        status, _, _ = self._raw(
            "POST", "/api/launch", data=b"{}",
            headers={"Origin": "http://evil.com", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)

    def test_same_origin_post_with_invalid_dir_returns_400(self):
        status, body = self._post("/api/launch", {"dir": "definitely-does-not-exist-xyz"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("subdir", body["message"])

    def test_oversized_body_rejected(self):
        status, _ = self._post("/api/launch", json.dumps({"dir": "A" * 5000}).encode())
        self.assertEqual(status, 413)

    def test_form_encoded_body_rejected(self):
        # Form encoding would make this a CORS "simple request" (no preflight).
        # Requiring JSON is what makes cross-origin POST structurally impossible.
        status, body = self._post(
            "/api/launch", b"dir=x", ctype="application/x-www-form-urlencoded")
        self.assertEqual(status, 415)
        self.assertIn("application/json", body["message"])

    def test_malformed_json_rejected(self):
        status, body = self._post("/api/launch", b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("bad json", body["message"])

    def test_json_array_body_rejected(self):
        status, body = self._post("/api/launch", [1, 2])
        self.assertEqual(status, 400)
        self.assertIn("json object", body["message"])

    def test_launch_returns_run_id_for_optimistic_row(self):
        old_default = server.DEFAULT_DIR
        server.DEFAULT_DIR = os.path.dirname(__file__)
        try:
            status, body = self._post("/api/launch", {"dir": ""})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["runId"], _RUN)
            self.assertIn("launched in", body["message"])
        finally:
            server.DEFAULT_DIR = old_default

    def test_get_on_post_endpoint_is_405(self):
        for path in ("/api/launch", "/api/resume", "/api/close"):
            with self.subTest(path=path):
                status, _, headers = self._raw("GET", path)
                self.assertEqual(status, 405)
                self.assertEqual(headers.get("Allow"), "POST")

    def test_root_serves_the_board_under_csp(self):
        # The Board is the only page now (ADR 0008): `/` serves board.html.
        status, body, headers = self._raw("GET", "/")
        self.assertEqual(status, 200)
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp.split("style-src")[0])
        self.assertIn('<script src="board.js"></script>', body)
        self.assertIn("claude board", body)
        self.assertIn("<noscript>", body)

    def test_board_js_is_served_and_innerhtml_is_the_lone_exception(self):
        status, body, headers = self._raw("GET", "/board.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        # ADR 0006: exactly one innerHTML *assignment* (the server-escaped
        # context markdown); every other field stays textContent.
        self.assertIn("ctx.innerHTML = f.contextHtml", body)
        assigns = [ln for ln in body.splitlines()
                   if ".innerHTML" in ln and "=" in ln and not ln.strip().startswith("//")]
        self.assertEqual(len(assigns), 1)

    def test_retired_inline_page_routes_are_gone(self):
        # The inline launcher, its script, and the old /board alias are removed.
        for path in ("/app.js", "/board", "/api/runs"):
            with self.subTest(path=path):
                status, _, _ = self._raw("GET", path)
                self.assertEqual(status, 404)


class ListRunsTests(unittest.TestCase):
    """The tmux list-panes walk joined to `ps` and Claude Code's per-pid session
    files. Each pane carries its @cl_run_id (US-separated) as the Run id."""

    PANES = ("R1\x1f/dev/ttys001\x1fold work (claude)\x1f\x1f@1\n"
             "R2\x1f/dev/ttys002\x1flogin\x1f\x1f@2\n"
             "R3\x1f/dev/ttys003\x1fnot claude\x1f\x1f@3\n")
    PS = ("  100 ttys001 claude\n"
          "  200 ttys002 claude\n"
          "  300 ttys003 zsh\n")

    def setUp(self):
        self._saved = (server._list_panes_raw, server._ps_output, server._run_meta,
                       server._last_msg)
        server._list_panes_raw = lambda: self.PANES
        server._ps_output = lambda: self.PS
        server._last_msg = lambda sid, *a, **k: ""
        # pid 100 registered with Claude Code; pid 200 has not yet (the ~0.5s
        # window between `claude` reaching ps and writing sessions/<pid>.json)
        server._run_meta = lambda *a, **k: {
            100: {"cwd": "/x", "status": "idle", "remote": False,
                  "sessionId": _GOOD, "updatedAt": 1000},
        }

    def tearDown(self):
        (server._list_panes_raw, server._ps_output, server._run_meta,
         server._last_msg) = self._saved

    def test_pane_without_claude_is_not_a_run(self):
        self.assertNotIn("R3", [r["id"] for r in server.list_runs()])

    def test_row_carries_the_attach_command_for_its_window(self):
        # R1 is a registered Run on window @1 — its row hands the client the
        # ready-to-paste grouped-attach line (ADR 0011).
        sock = server.TMUX_SOCKET
        row = {r["id"]: r for r in server.list_runs()}["R1"]
        self.assertEqual(
            row["attach"],
            f"tmux -L {sock} new-session -t {sock} "
            f"\\; set destroy-unattached on \\; select-window -t @1")

    def test_pane_without_cl_run_id_is_invisible(self):
        # A pane the Launcher didn't create has a blank @cl_run_id (e.g. the
        # session's own initial window) and must never surface as a Run — even
        # if `claude` happens to be running on its tty.
        server._list_panes_raw = lambda: (
            "\x1f/dev/ttys001\x1fsome shell\x1f\x1f@9\n" + self.PANES)
        ids = [r["id"] for r in server.list_runs()]
        self.assertEqual(ids, ["R2", "R1"])   # the blank-id row dropped entirely

    def test_unregistered_run_is_marked_starting(self):
        rows = {r["id"]: r for r in server.list_runs()}
        self.assertTrue(rows["R2"]["starting"])
        self.assertFalse(rows["R1"]["starting"])

    def test_starting_run_sorts_first_despite_having_no_updated_at(self):
        # it would otherwise land at the bottom under `updatedAt or 0`, then
        # jump to the top half a second later when its metadata appears
        self.assertEqual([r["id"] for r in server.list_runs()], ["R2", "R1"])

    def test_active_runs_sort_newest_first(self):
        server._run_meta = lambda *a, **k: {
            100: {"cwd": "/x", "status": "idle", "remote": False,
                  "sessionId": _GOOD, "updatedAt": 1000},
            200: {"cwd": "/y", "status": "busy", "remote": True,
                  "sessionId": _LIVE, "updatedAt": 5000},
        }
        self.assertEqual([r["id"] for r in server.list_runs()], ["R2", "R1"])

    def test_dir_is_home_collapsed(self):
        home = os.path.expanduser("~")
        server._run_meta = lambda *a, **k: {
            100: {"cwd": home + "/projects/x", "status": "", "remote": False,
                  "sessionId": _GOOD, "updatedAt": 1},
        }
        rows = {r["id"]: r for r in server.list_runs()}
        self.assertEqual(rows["R1"]["dir"], "~/projects/x")


class TasksApiTests(_HttpCase):
    """Intake config reaches the static Board as data via GET /api/tasks
    (ADR 0008), not as server-rendered markup."""

    @classmethod
    def setUpClass(cls):
        cls._saved = (server.TASKS, server._tasks_mtime)
        server._tasks_mtime = lambda: server._TASKS_MTIME   # freeze the reload
        server.TASKS = [
            {"id": "cap", "label": "capture", "workdir": "~", "command": "/capture-task",
             "input": "text", "placeholder": "a thought"},
            {"id": "jot", "workdir": "~", "exec": ["/bin/sh"], "input": "textarea",
             "buttons": [{"id": "jot", "label": "jot"}, {"id": "jot-log", "label": "log"}]},
            {"id": "sched", "label": "schedule", "workdir": "~", "command": "/scheduling",
             "input": "none"},
        ]
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.TASKS, server._tasks_mtime = cls._saved

    def test_tasks_payload_shape(self):
        status, body, headers = self._raw("GET", "/api/tasks")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(headers["ETag"])
        self.assertIn("root", data)                          # compose-bar label
        groups = data["tasks"]
        self.assertEqual(groups[0]["input"], "text")
        self.assertEqual(groups[0]["placeholder"], "a thought")
        self.assertEqual([b["id"] for b in groups[0]["buttons"]], ["cap"])
        # a button group shares one seed box but lists both buttons
        self.assertEqual(groups[1]["input"], "textarea")
        self.assertEqual([b["label"] for b in groups[1]["buttons"]], ["jot", "log"])
        self.assertEqual(groups[2]["input"], "none")

    def test_a_markup_placeholder_is_carried_verbatim_as_data(self):
        # Escaping moved from the server to the client (textContent / the
        # .placeholder property), so the payload carries the raw string.
        saved = server.TASKS
        server.TASKS = [{"id": "x", "label": "x", "workdir": "~", "exec": ["/usr/bin/true"],
                         "input": "textarea", "placeholder": "<script>alert(1)</script>"}]
        try:
            _, body, _ = self._raw("GET", "/api/tasks")
            g = json.loads(body)["tasks"][0]
            self.assertEqual(g["placeholder"], "<script>alert(1)</script>")
        finally:
            server.TASKS = saved

    def test_if_none_match_returns_304_without_body(self):
        _, _, headers = self._raw("GET", "/api/tasks")
        etag = headers["ETag"]
        status, body, headers2 = self._raw(
            "GET", "/api/tasks", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, "")
        self.assertEqual(headers2["ETag"], etag)

    def test_stale_etag_returns_200(self):
        status, body, _ = self._raw(
            "GET", "/api/tasks", headers={"If-None-Match": '"deadbeefdeadbeef"'})
        self.assertEqual(status, 200)
        self.assertIn("tasks", json.loads(body))


# A real AskUserQuestion widget as iTerm renders it: numbered options in a left
# column, the highlighted option's description in a box-drawn side panel on the
# SAME rows, a checkbox header, and the notes affordance. The whole thing is
# framed by horizontal rules — which is exactly why the naive input-box reader
# used to mistake it for unsent text.
# Blank lines sit between the header, the question, and the options — the exact
# gaps that a naive upward walk from the first option trips over.
_ASK_FRAME = [
    "─" * 60,
    " ☐ New-work signal  ",
    "",
    "When new Blocked work arrives while you're holding a card, how much should it signal? ",
    "",
    "❯ 1. Passive count only           ┌" + "─" * 30 + "┐ ",
    "  2. Passive + subtle mark        │ Run Y blocks → summary '2 need you'        │ ",
    "  3. Gentle toast                 │   'up next' count +1. No move.               │ ",
    "                                  └" + "─" * 30 + "┘ ",
    "                                  Notes: press n to add notes ",
    "─" * 60,
    "Enter to select · ↑/↓ to navigate · n to add notes · Esc to cancel ",
]
_ASK_PANE = "\n".join(_ASK_FRAME)

# `contents of session` returns scrollback: a widget that re-rendered shows up
# twice. Only the last (live) frame counts — here the cursor has moved to opt 2.
_ASK_PANE_DUP = "\n".join(_ASK_FRAME + [
    "some intervening assistant output ",
    "─" * 60,
    " ☐ New-work signal  ",
    "",
    "When new Blocked work arrives while you're holding a card, how much should it signal? ",
    "",
    "  1. Passive count only           ",
    "❯ 2. Passive + subtle mark        ",
    "  3. Gentle toast                 ",
    "─" * 60,
    "Enter to select · ↑/↓ to navigate · n to add notes · Esc to cancel ",
])

# A permission prompt: a numbered menu with no side panel and no notes line.
_PERMISSION_PANE = "\n".join([
    "─" * 60,
    "Bash(rm -rf build) ",
    "❯ 1. Yes ",
    "  2. Yes, and don't ask again ",
    "  3. No, and tell Claude what to do differently ",
    "─" * 60,
])

# A plain input box with a half-typed reply between the rules.
_INPUT_PANE = "\n".join([
    "─" * 60,
    "❯ draft a reply but do not send ",
    "─" * 60,
    "\U0001f4c1 ~/projects/x ",
])


class PaneParsingTests(unittest.TestCase):
    """The rendered pane is parsed three ways — a numbered selector, the
    AskUserQuestion prompt, and a free-text input box — and they must not bleed
    into one another (the mangled-card bug, ADR 0009)."""

    def test_option_labels_drop_the_box_art_side_panel(self):
        sel = server._parse_selector(_ASK_PANE)
        self.assertEqual(sel["options"],
                         ["Passive count only", "Passive + subtle mark", "Gentle toast"])

    def test_cursor_tracks_the_highlight_glyph(self):
        self.assertEqual(server._parse_selector(_ASK_PANE)["cursor"], 0)

    def test_stale_scrollback_frame_is_ignored(self):
        # Only the live frame counts: 3 options, cursor on the moved highlight.
        sel = server._parse_selector(_ASK_PANE_DUP)
        self.assertEqual(sel["options"],
                         ["Passive count only", "Passive + subtle mark", "Gentle toast"])
        self.assertEqual(sel["cursor"], 1)

    def test_pane_question_survives_a_duplicated_frame(self):
        self.assertEqual(
            server._pane_question(_ASK_PANE_DUP),
            "When new Blocked work arrives while you're holding a card, how much should it signal?")

    def test_permission_menu_parses_cleanly(self):
        sel = server._parse_selector(_PERMISSION_PANE)
        self.assertEqual(sel["options"],
                         ["Yes", "Yes, and don't ask again", "No, and tell Claude what to do differently"])

    def test_widget_is_recognised_by_its_notes_affordance(self):
        self.assertTrue(server._is_question_widget(_ASK_PANE))
        self.assertFalse(server._is_question_widget(_PERMISSION_PANE))
        self.assertFalse(server._is_question_widget(_INPUT_PANE))

    def test_pane_question_reads_the_prompt_not_the_header(self):
        self.assertEqual(
            server._pane_question(_ASK_PANE),
            "When new Blocked work arrives while you're holding a card, how much should it signal?")

    def test_input_box_reads_a_real_draft(self):
        self.assertEqual(server._pane_input(_INPUT_PANE), "draft a reply but do not send")


class PaneContentsTests(unittest.TestCase):
    """_pane_contents resolves the Run UUID to its tmux pane and reads the
    visible frame with `capture-pane -p` (ADR 0010, slice 3). '' is the contract
    callers depend on for "couldn't read": a bad-shape id, or a UUID no live pane
    carries, never reaches capture-pane."""

    def setUp(self):
        self._saved_tmux = server._tmux
        self.calls = []
        self.resolvable = True

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                out = f"{_RUN}\x1f%5\n" if self.resolvable else ""
                return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
            if args[0] == "capture-pane":
                return types.SimpleNamespace(
                    returncode=0, stdout="rendered frame\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake

    def tearDown(self):
        server._tmux = self._saved_tmux

    def test_resolves_the_pane_and_returns_capture_pane_output(self):
        self.assertEqual(server._pane_contents(_RUN), "rendered frame\n")
        # the visible frame is read off the resolved pane (`-p`, no scrollback)
        self.assertIn(("capture-pane", "-p", "-t", "%5"), self.calls)

    def test_a_bogus_id_returns_empty_without_touching_tmux(self):
        for bad in ("../etc", "short"):
            self.assertEqual(server._pane_contents(bad), "")
        self.assertEqual(self.calls, [])

    def test_an_unresolvable_uuid_returns_empty_before_capture(self):
        self.resolvable = False   # valid UUID shape, but no pane carries it
        self.assertEqual(server._pane_contents(_LIVE), "")
        self.assertEqual([c for c in self.calls if c[0] == "capture-pane"], [])


class BlockedFocusTests(unittest.TestCase):
    """A pending AskUserQuestion never reaches the transcript, so its focus card
    is enriched entirely from the rendered pane. Regression guard for the card
    that showed 'approval', box-art options, an empty ask, and a false ⚠ pending
    (ADR 0009)."""

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "_transcript_path", "_pane_contents", "_ai_title",
                        "_tmux_server_down")}
        server._tmux_server_down = lambda: False         # don't shell out to real tmux
        server._transcript_path = lambda *a, **k: ""     # empty tail → nothing flushed
        server._ai_title = lambda sid: "fix the cards"
        server._pane_contents = lambda rid: _ASK_PANE
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "",
        }]

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)

    def test_unflushed_question_is_a_question_not_an_approval(self):
        self.assertEqual(server._board()["focus"]["lane"], "question")

    def test_options_are_clean_and_the_ask_is_present(self):
        focus = server._board()["focus"]
        self.assertEqual(focus["options"],
                         ["Passive count only", "Passive + subtle mark", "Gentle toast"])
        self.assertTrue(focus["ask"].startswith("When new Blocked work arrives"))

    def test_the_widget_is_not_reported_as_unsent_input(self):
        self.assertEqual(server._board()["focus"]["pendingInput"], "")


# The exact command from the live incident (session d4440820, obsidian Run
# blocked on a Bash permission prompt). Unlike an AskUserQuestion, a command
# approval's tool_use IS flushed to the transcript — the concrete blocker is on
# disk, the card just never reads it.
_APPROVAL_CMD = (
    'for f in Notes/2026-07-1[2-7]*.md; do echo "=== $f ==="; '
    'wc -w "$f" | awk \'{print $1" words"}\'; done'
)
_APPROVAL_ROWS = [
    {"type": "user", "message": {"content": [
        {"type": "text", "text": "look at my recent dated notes over the past 5 "
                                 "days. see if there's anything that looks like it "
                                 "should be consolidated."}]}},
    # last assistant turn is a bare Bash tool_use — no prose, so _full_context's
    # text/ask both come back empty. The tool_use has no matching tool_result, so
    # it is the pending (flushed) blocker.
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_bash1", "name": "Bash",
         "input": {"command": _APPROVAL_CMD,
                   "description": "Word counts for recent notes"}}]}},
]
# The Bash approval as the pane renders it — a numbered menu, no widget/side panel.
_BASH_APPROVAL_PANE = "\n".join([
    "─" * 60,
    " Bash command ",
    "   " + _APPROVAL_CMD,
    "   Word counts for recent notes ",
    " Do you want to proceed? ",
    "❯ 1. Yes ",
    "  2. No ",
    "─" * 60,
])


class ApprovalFocusTests(unittest.TestCase):
    """A command-approval (Bash/Edit/…) leaves a FLUSHED pending tool_use, but the
    focus card only pulls a prompt out of a pending tool_use when it is
    AskUserQuestion — so a Bash approval renders an empty ask, and the card never
    says WHAT is being approved (just Yes/No).

    Captured live: session d4440820, blocked on `_APPROVAL_CMD`, returned lane
    'approval' with ask='' and contextHtml=''. Pre-existing board gap, not the
    tmux swap (`_full_context` reads the transcript, not the pane).

    Ticket: .scratch/approval-card-detail/issues/01-approval-cards-show-command.md
    """

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "_tail_rows", "_transcript_path",
                        "_pane_contents", "_ai_title", "_tmux_server_down")}
        server._tmux_server_down = lambda: False
        server._transcript_path = lambda *a, **k: ""
        server._tail_rows = lambda sid: _APPROVAL_ROWS
        server._pane_contents = lambda rid: _BASH_APPROVAL_PANE
        server._ai_title = lambda sid: "Consolidate recent dated notes"
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "obsidian", "dir": "~/obsidian",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "",
        }]

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)

    def test_the_repro_really_is_an_approval(self):
        # Sanity (passes today): a pending non-AskUserQuestion tool_use → approval.
        self.assertEqual(server._board()["focus"]["lane"], "approval")

    def test_approval_card_shows_the_command_being_approved(self):
        # Was RED until ticket 01: today ask carries the flushed Bash command, so
        # the card says WHAT is being approved instead of a bare Yes/No.
        focus = server._board()["focus"]
        shown = f"{focus.get('ask', '')} {focus.get('contextHtml', '')}"
        self.assertIn("wc -w", shown,
                      "approval card must surface the command being approved")

    def test_bash_command_rides_the_plaintext_ask_field(self):
        # The command is untrusted transcript text; it goes in `ask` (textContent,
        # never innerHTML), so contextHtml carries no approval `input`.
        focus = server._board()["focus"]
        self.assertIn("wc -w", focus["ask"])
        self.assertNotIn("wc -w", focus["contextHtml"])
        self.assertEqual(focus["lane"], "approval")          # badge unchanged
        self.assertEqual(focus["options"], ["Yes", "No"])    # Yes/No preserved


# Approval tool_uses other than Bash. Each leaves a FLUSHED pending tool_use
# (ADR 0009); the card reads its `input`, not the pane. _lane_of + _full_context
# are exercised directly with a stubbed transcript tail.
class ApprovalDetailTests(unittest.TestCase):
    def setUp(self):
        self._saved = server._tail_rows

    def tearDown(self):
        server._tail_rows = self._saved

    def _rows_for(self, tool):
        return [
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "go ahead and make the change"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_x", "name": tool["name"],
                 "input": tool["input"]}]}},
        ]

    def _ask_for(self, tool):
        server._tail_rows = lambda sid: self._rows_for(tool)
        _, ask, _ = server._full_context(_GOOD)
        return ask

    def test_edit_shows_the_target_file_and_a_change_summary(self):
        ask = self._ask_for({"name": "Edit", "input": {
            "file_path": "/Users/x/obsidian/Notes/2026-07-17.md",
            "old_string": "todo: draft", "new_string": "done: drafted the note"}})
        self.assertIn("Notes/2026-07-17.md", ask)
        self.assertIn("done: drafted the note", ask)         # concise change summary

    def test_write_shows_the_target_file(self):
        ask = self._ask_for({"name": "Write", "input": {
            "file_path": "/Users/x/projects/app/config.py", "content": "PORT = 8080"}})
        self.assertIn("Write", ask)
        self.assertIn("projects/app/config.py", ask)

    def test_exitplanmode_shows_the_plan(self):
        ask = self._ask_for({"name": "ExitPlanMode", "input": {
            "plan": "## Plan\n1. Add the endpoint\n2. Wire the button"}})
        self.assertIn("Add the endpoint", ask)

    def test_a_long_command_is_clipped_with_an_ellipsis(self):
        long_cmd = "echo " + "x" * 2000
        ask = self._ask_for({"name": "Bash", "input": {"command": long_cmd}})
        self.assertLessEqual(len(ask), server._ASK_MAX + 1)  # +1 for the ellipsis
        self.assertTrue(ask.endswith("…"))
        self.assertNotIn("x" * 2000, ask)

    def test_context_prose_above_the_command_is_clipped(self):
        long_prose = "we were " + "y" * 3000
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": long_prose},
            {"type": "tool_use", "id": "toolu_x", "name": "Bash",
             "input": {"command": "ls"}}]}}]
        server._tail_rows = lambda sid: rows
        text, ask, _ = server._full_context(_GOOD)
        self.assertEqual(ask, "ls")
        self.assertLessEqual(len(text), server._CTX_MAX + 1)
        self.assertTrue(text.endswith("…"))


class BoardPayloadTests(unittest.TestCase):
    """The Board's item dict now carries `bridge` so the client can build the
    deep-link into the Claude app (ADR 0008)."""

    def setUp(self):
        self._saved = (server.cached_runs, server._tmux_server_down)
        server._tmux_server_down = lambda: False         # don't shell out to real tmux

    def tearDown(self):
        server.cached_runs, server._tmux_server_down = self._saved

    def test_items_carry_the_bridge_for_the_deep_link(self):
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
            "status": "busy", "bridge": "session_abc", "updatedAt": 5000, "snippet": "",
        }]
        board = server._board()
        self.assertIsNone(board["focus"])                    # a working Run is not a focus
        self.assertEqual(board["watching"][0]["bridge"], "session_abc")

    def test_no_wall_clock_time_on_the_wire(self):
        # The client formats ages from raw updatedAt, so an idle board yields a
        # stable ETag/304 — the same invariant the old /api/runs guarded.
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/p/x",
            "status": "busy", "bridge": "", "updatedAt": 5000, "snippet": "",
        }]
        _, etag_a = server._board_payload()
        real_time = time.time
        time.time = lambda: real_time() + 3600
        try:
            _, etag_b = server._board_payload()
        finally:
            time.time = real_time
        self.assertEqual(etag_a, etag_b)

    def test_server_down_is_surfaced_and_shifts_the_etag(self):
        # A dead tmux server is a distinct empty state, not an ordinary empty
        # board — the payload carries the signal and its ETag reflects the flip
        # (ADR 0010). The web client rendering it is a documented follow-up.
        server.cached_runs = lambda: []
        server._tmux_server_down = lambda: False
        self.assertFalse(server._board()["serverDown"])
        body_up, etag_up = server._board_payload()
        server._tmux_server_down = lambda: True
        self.assertTrue(server._board()["serverDown"])
        body_down, etag_down = server._board_payload()
        self.assertNotEqual(etag_up, etag_down)
        self.assertNotEqual(body_up, body_down)


class IdConfusionTests(_HttpCase):
    """A Run id and a Session id are both UUIDs. Neither endpoint may take the
    other's — that ambiguity is the whole reason they were renamed."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {"launch": server.launch_run, "list": server.list_runs,
                      "transcript": server._transcript_path,
                      "cwd": server._session_cwd}
        cls.calls = []
        server.launch_run = lambda *a, **k: (cls.calls.append((a, k)), _RUN)[1]
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _LIVE}]
        server._transcript_path = lambda sid, *a, **k: (
            "/x/" + sid + ".jsonl" if sid in (_GOOD, _LIVE) else "")
        server._session_cwd = lambda sid, *a, **k: os.path.dirname(__file__)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_run = cls._saved["launch"]
        server.list_runs = cls._saved["list"]
        server._transcript_path = cls._saved["transcript"]
        server._session_cwd = cls._saved["cwd"]

    def setUp(self):
        type(self).calls.clear()
        server.invalidate_runs()

    def test_close_rejects_a_session_id(self):
        # _LIVE is a live *Session* id, not the Run id — must not close anything
        status, body = self._post("/api/close", {"runId": _LIVE})
        self.assertEqual(status, 400)
        self.assertIn("could not close", body["message"])

    def test_close_ignores_a_sessionid_field(self):
        status, _ = self._post("/api/close", {"sessionId": _RUN})
        self.assertEqual(status, 400)  # wrong field name -> empty runId

    def test_resume_rejects_a_run_id(self):
        # _RUN is a Run id; it has no transcript, so it is not a Session
        status, body = self._post("/api/resume", {"sessionId": _RUN})
        self.assertEqual(status, 400)
        self.assertIn("no such session", body["message"])
        self.assertEqual(self.calls, [])

    def test_resume_refuses_a_session_that_is_already_running(self):
        status, body = self._post("/api/resume", {"sessionId": _LIVE})
        self.assertEqual(status, 400)
        self.assertIn("already live", body["message"])
        self.assertEqual(self.calls, [])

    def test_resume_of_idle_session_spawns_a_run(self):
        status, body = self._post("/api/resume", {"sessionId": _GOOD})
        self.assertEqual(status, 200)
        self.assertEqual(body["runId"], _RUN)
        self.assertIn(f"resumed {_GOOD}", body["message"])
        _, kwargs = self.calls[-1]
        self.assertEqual(kwargs["resume_id"], _GOOD)

    def test_resume_rejects_malformed_id(self):
        status, body = self._post("/api/resume", {"sessionId": "not-a-uuid"})
        self.assertEqual(status, 400)
        self.assertIn("invalid session id", body["message"])
        self.assertEqual(self.calls, [])


class RunParseTests(unittest.TestCase):
    def test_parse_tmux_panes(self):
        # five fields: @cl_run_id, pane_tty, pane_title, @cl_task (blank
        # untagged), window_id
        out = ("ID1\x1f/dev/ttys001\x1f✳ Fix bug (claude)\x1fcapture\x1f@4\n"
               "ID2\x1f/dev/ttys002\x1fDefault\x1f\x1f@5\n")
        self.assertEqual(
            server._parse_tmux_panes(out),
            [("ID1", "ttys001", "✳ Fix bug (claude)", "capture", "@4"),
             ("ID2", "ttys002", "Default", "", "@5")],
        )

    def test_parse_tmux_panes_skips_malformed(self):
        self.assertEqual(server._parse_tmux_panes("garbage-no-separators\n\n"), [])

    def test_parse_tmux_panes_drops_rows_without_a_run_id(self):
        # A pane not created by the Launcher renders @cl_run_id empty; it is not
        # a Run and must be dropped, keeping list_runs to Runs it owns.
        out = ("\x1f/dev/ttys001\x1fsome shell\x1f\x1f@1\n"
               "ID2\x1f/dev/ttys002\x1fclaude\x1fcap\x1f@2\n")
        self.assertEqual(
            server._parse_tmux_panes(out),
            [("ID2", "ttys002", "claude", "cap", "@2")],
        )

    def test_attach_cmd_targets_the_window_in_a_self_cleaning_grouped_session(self):
        sock = server.TMUX_SOCKET
        self.assertEqual(
            server._attach_cmd("@7"),
            f"tmux -L {sock} new-session -t {sock} "
            f"\\; set destroy-unattached on \\; select-window -t @7")

    def test_attach_cmd_is_empty_without_a_window(self):
        self.assertEqual(server._attach_cmd(""), "")

    def test_parse_claude_ttys_filters_to_claude(self):
        out = (
            "  32324 ttys001 claude\n"
            "  11402 ttys006 claude --resume abc\n"
            "  31125 ??      /usr/bin/python server.py\n"
            "  99999 ttys009 node something\n"
        )
        self.assertEqual(
            server._parse_claude_ttys(out),
            {"ttys001": 32324, "ttys006": 11402},
        )

    def test_run_meta(self):
        base = os.path.join(os.path.dirname(__file__), "_sessfix")
        os.makedirs(base, exist_ok=True)
        try:
            with open(os.path.join(base, "39909.json"), "w") as f:
                f.write('{"pid":39909,"cwd":"/Users/me/obsidian","status":"waiting",'
                        '"bridgeSessionId":"session_abc","updatedAt":123}')
            with open(os.path.join(base, "11402.json"), "w") as f:
                f.write('{"pid":11402,"cwd":"/x","status":"idle"}')  # no bridge
            with open(os.path.join(base, "77000.json"), "w") as f:
                # a malformed bridge id: still "remote", but not a usable deep link
                f.write('{"pid":77000,"cwd":"/z","bridgeSessionId":"../evil"}')
            with open(os.path.join(base, "bad.json"), "w") as f:
                f.write("not json")  # skipped
            meta = server._run_meta(base)
            self.assertEqual(meta[39909], {
                "cwd": "/Users/me/obsidian", "status": "waiting", "remote": True,
                "bridge": "session_abc", "sessionId": "", "updatedAt": 123,
            })
            self.assertEqual(meta[11402], {
                "cwd": "/x", "status": "idle", "remote": False,
                "bridge": "", "sessionId": "", "updatedAt": None,
            })
            self.assertEqual(meta[77000]["remote"], True)   # field present
            self.assertEqual(meta[77000]["bridge"], "")     # but not a valid deep-link id
            self.assertEqual(set(meta), {39909, 11402, 77000})
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_run_meta_drops_unknown_status(self):
        # status becomes a `st-<status>` CSS class on the client, so it is the
        # one field textContent cannot make safe. Anything unrecognized -> "".
        base = os.path.join(os.path.dirname(__file__), "_statusfix")
        os.makedirs(base, exist_ok=True)
        try:
            for pid, status in ((1, '"compacting"'), (2, '"busy"'), (3, 'null')):
                with open(os.path.join(base, f"{pid}.json"), "w") as f:
                    f.write(f'{{"pid":{pid},"status":{status}}}')
            meta = server._run_meta(base)
            self.assertEqual(meta[1]["status"], "")     # unknown -> dropped
            self.assertEqual(meta[2]["status"], "busy")  # known -> kept
            self.assertEqual(meta[3]["status"], "")     # non-string -> dropped
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_run_meta_drops_non_numeric_updated_at(self):
        base = os.path.join(os.path.dirname(__file__), "_updfix")
        os.makedirs(base, exist_ok=True)
        try:
            with open(os.path.join(base, "7.json"), "w") as f:
                f.write('{"pid":7,"updatedAt":"yesterday"}')
            self.assertIsNone(server._run_meta(base)[7]["updatedAt"])
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_first_user_msg(self):
        base = os.path.join(os.path.dirname(__file__), "_txfix")
        proj = os.path.join(base, "-Users-me-proj")
        os.makedirs(proj, exist_ok=True)
        try:
            lines = [
                '{"type":"user","message":{"content":"<system-reminder>skip me"}}',
                '{"type":"assistant","message":{"content":"not user"}}',
                '{"type":"user","message":{"content":[{"type":"tool_result","content":"skip"}]}}',
                '{"type":"user","message":{"content":[{"type":"text","text":"fix the failing test"}]}}',
            ]
            with open(os.path.join(proj, _GOOD + ".jsonl"), "w") as f:
                f.write("\n".join(lines) + "\n")
            self.assertEqual(server._first_user_msg(_GOOD, base), "fix the failing test")
            self.assertEqual(server._first_user_msg("bad-id", base), "")
            self.assertEqual(server._first_user_msg(_GOOD, base + "_missing"), "")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_last_msg(self):
        base = os.path.join(os.path.dirname(__file__), "_lmfix")
        proj = os.path.join(base, "-Users-me-proj")
        os.makedirs(proj, exist_ok=True)
        try:
            lines = [
                '{"type":"user","message":{"content":[{"type":"text","text":"first ask"}]}}',
                '{"type":"assistant","message":{"content":[{"type":"text","text":"done, all green"}]}}',
                '{"type":"user","message":{"content":[{"type":"tool_result","content":"ignore me"}]}}',
            ]
            with open(os.path.join(proj, _LIVE + ".jsonl"), "w") as f:
                f.write("\n".join(lines) + "\n")
            # last text message wins; trailing tool_result is skipped
            self.assertEqual(server._last_msg(_LIVE, base), "done, all green")
            self.assertEqual(server._last_msg("bad-id", base), "")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_clean_title(self):
        self.assertEqual(
            server._clean_title("✳ Analyze daily scheduling feasibility (Python)"),
            "Analyze daily scheduling feasibility")
        self.assertEqual(
            server._clean_title("⠐ Improve /grill-me command functionality (caffeinate)"),
            "Improve /grill-me command functionality")
        self.assertEqual(server._clean_title("Plain title"), "Plain title")

    def test_clean_title_treats_the_tmux_hostname_default_as_no_title(self):
        # tmux titles a fresh pane with the host's name until `claude` overrides
        # it; that default must read as no title so list_runs backstops with
        # _first_user_msg instead of showing the hostname (ADR 0010).
        for host in (socket.gethostname(), socket.gethostname().split(".", 1)[0]):
            self.assertEqual(server._clean_title(host), "")
        # A genuine claude title still survives the hostname check.
        self.assertEqual(server._clean_title("✳ Fix the login bug"), "Fix the login bug")


class CachedRunsTests(unittest.TestCase):
    def setUp(self):
        self._saved = server.list_runs
        self.hits = []
        server.list_runs = lambda: (self.hits.append(1), [{"id": _RUN, "sessionId": _GOOD}])[1]
        server.invalidate_runs()

    def tearDown(self):
        server.list_runs = self._saved
        server.invalidate_runs()

    def test_repeat_calls_collapse_into_one_applescript_walk(self):
        server.cached_runs()
        server.cached_runs()
        server.cached_runs()
        self.assertEqual(len(self.hits), 1)

    def test_invalidate_forces_a_fresh_walk(self):
        # a closed Run must vanish on the very next poll, not up to a TTL later
        server.cached_runs()
        server.invalidate_runs()
        server.cached_runs()
        self.assertEqual(len(self.hits), 2)


class CloseRunTests(unittest.TestCase):
    def test_rejects_bad_id_format(self):
        self.assertFalse(server.close_run("../etc"))
        self.assertFalse(server.close_run("short"))

    def test_rejects_id_not_in_live_set(self):
        saved = server.list_runs
        server.list_runs = lambda: [{"id": _RUN}]
        server.invalidate_runs()
        try:
            self.assertFalse(server.close_run(_LIVE))
        finally:
            server.list_runs = saved
            server.invalidate_runs()

    def test_resolves_the_uuid_to_its_window_and_kills_it(self):
        saved_list, saved_tmux = server.list_runs, server._tmux
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _GOOD}]
        server.invalidate_runs()
        self.calls = []

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                return types.SimpleNamespace(
                    returncode=0, stdout=f"{_RUN}\x1f%9\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake
        try:
            self.assertTrue(server.close_run(_RUN))
            # a pane target resolves to its window for kill-window
            self.assertIn(("kill-window", "-t", "%9"), self.calls)
        finally:
            server.list_runs, server._tmux = saved_list, saved_tmux
            server.invalidate_runs()

    def test_a_live_run_with_no_resolvable_pane_does_not_kill(self):
        # In the live set but the UUID no longer resolves to a pane (a stale
        # cache after the server restarted): close must not fire a kill-window.
        saved_list, saved_tmux = server.list_runs, server._tmux
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _GOOD}]
        server.invalidate_runs()
        self.calls = []

        def fake(*args, check=True):
            self.calls.append(args)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake
        try:
            self.assertFalse(server.close_run(_RUN))
            self.assertEqual([c for c in self.calls if c[0] == "kill-window"], [])
        finally:
            server.list_runs, server._tmux = saved_list, saved_tmux
            server.invalidate_runs()


class RespondRunTests(unittest.TestCase):
    """respond_run drives a live Run's pane over `tmux send-keys` (ADR 0010,
    slice 2). Text is typed literally (`-l`) then submitted by a SEPARATE Enter;
    selector keys map to fixed tmux key names sent bare; a stale/bogus id
    no-ops, exactly as close_run does."""

    def setUp(self):
        self._saved_list, self._saved_tmux = server.list_runs, server._tmux
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _GOOD}]
        server.invalidate_runs()
        self.calls = []
        self.resolvable = True

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                out = f"{_RUN}\x1f%7\n" if self.resolvable else ""
                return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake

    def tearDown(self):
        server.list_runs, server._tmux = self._saved_list, self._saved_tmux
        server.invalidate_runs()

    def _sends(self):
        return [c for c in self.calls if c and c[0] == "send-keys"]

    def test_text_is_typed_literally_then_submitted_by_a_separate_enter(self):
        self.assertTrue(server.respond_run(_RUN, "hello world"))
        sends = self._sends()
        typed = ("send-keys", "-t", "%7", "-l", "hello world")
        enter = ("send-keys", "-t", "%7", "Enter")
        self.assertIn(typed, sends)
        self.assertIn(enter, sends)
        # landmine 3: the literal text is typed BEFORE its distinct Enter submit
        self.assertLess(sends.index(typed), sends.index(enter))

    def test_the_enter_submit_carries_no_dash_l(self):
        server.respond_run(_RUN, "hi")
        enter = next(c for c in self._sends() if c[-1] == "Enter")
        self.assertNotIn("-l", enter)   # a bare key name, never literal text

    def test_each_key_maps_to_its_tmux_name_sent_bare(self):
        vocab = ["enter", "esc", "up", "down", "right", "left", "tab", "space"]
        self.assertTrue(server.respond_run(_RUN, "", vocab))
        sends = self._sends()
        for name in ("Enter", "Escape", "Up", "Down", "Right", "Left", "Tab", "Space"):
            self.assertIn(("send-keys", "-t", "%7", name), sends)
        # keys are bare names — never `-l` (that would type the literal word)
        self.assertTrue(all("-l" not in c for c in sends))

    def test_an_unknown_key_is_ignored_and_nothing_is_sent(self):
        self.assertFalse(server.respond_run(_RUN, "", ["boom"]))
        self.assertEqual(self._sends(), [])

    def test_a_bogus_id_no_ops_without_touching_tmux(self):
        for bad in ("../etc", "short"):
            self.assertFalse(server.respond_run(bad, "hi"))
        self.assertEqual(self.calls, [])

    def test_a_stale_id_not_in_the_live_set_no_ops(self):
        # _LIVE is a valid UUID but not the live Run id, so it never resolves.
        self.assertFalse(server.respond_run(_LIVE, "hi"))
        self.assertEqual(self.calls, [])

    def test_a_live_id_with_no_resolvable_pane_no_ops(self):
        self.resolvable = False   # in the live set, but list-panes has no match
        self.assertFalse(server.respond_run(_RUN, "hi"))
        self.assertEqual(self._sends(), [])


class ClearInputTests(unittest.TestCase):
    """clear_input empties a live Run's box with N `send-keys BSpace`
    backspaces (ADR 0010, slice 2). N is the real box length read from the pane
    (slice 3) plus a small over-count margin; an empty box still deletes only the
    safe margin, which a backspace at the input start no-ops away."""

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("list_runs", "_tmux", "_pane_contents", "_pane_input")}
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _GOOD}]
        server.invalidate_runs()
        self.calls = []
        self.resolvable = True

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                out = f"{_RUN}\x1f%3\n" if self.resolvable else ""
                return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)
        server.invalidate_runs()

    def test_an_empty_box_read_still_sends_the_margin_of_backspaces(self):
        server._pane_contents = lambda rid: ""   # nothing typed in the box
        self.assertTrue(server.clear_input(_RUN))
        send = next(c for c in self.calls if c[0] == "send-keys")
        self.assertEqual(send[:3], ("send-keys", "-t", "%3"))
        self.assertEqual(list(send[3:]), ["BSpace"] * 16)   # 0 content + 16 margin

    def test_backspace_count_is_the_box_length_plus_the_margin(self):
        server._pane_contents = lambda rid: "unused"
        server._pane_input = lambda text: "draft"   # 5 chars
        server.clear_input(_RUN)
        send = next(c for c in self.calls if c[0] == "send-keys")
        self.assertEqual(list(send[3:]), ["BSpace"] * (5 + 16))
        self.assertTrue(all(k == "BSpace" for k in send[3:]))

    def test_a_bogus_id_no_ops_without_touching_tmux(self):
        self.assertFalse(server.clear_input("short"))
        self.assertEqual(self.calls, [])

    def test_a_live_id_with_no_resolvable_pane_no_ops(self):
        self.resolvable = False
        server._pane_contents = lambda rid: ""
        self.assertFalse(server.clear_input(_RUN))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])


class LaunchCmdTests(unittest.TestCase):
    def setUp(self):
        self._remote, self._command = server.REMOTE, server.COMMAND
        server.REMOTE, server.COMMAND = True, "cl"

    def tearDown(self):
        server.REMOTE, server.COMMAND = self._remote, self._command

    def test_generic_launch_has_no_prompt(self):
        self.assertEqual(server._launch_cmd("/w"), "cd '/w' && cl --remote-control")

    def test_prompt_goes_before_remote_flag(self):
        cmd = server._launch_cmd("/w", "/capture-task seed")
        self.assertEqual(cmd, "cd '/w' && cl '/capture-task seed' --remote-control")
        # the prompt must precede --remote-control, never trail it (else the
        # flag's optional [name] arg swallows it)
        self.assertLess(cmd.index("/capture-task"), cmd.index("--remote-control"))

    def test_remote_disabled_omits_flag(self):
        server.REMOTE = False
        self.assertEqual(server._launch_cmd("/w", "/x"), "cd '/w' && cl '/x'")

    def test_resume_cmd_id_precedes_remote_flag(self):
        cmd = server._resume_cmd("/w", _GOOD)
        self.assertEqual(cmd, f"cd '/w' && cl --resume '{_GOOD}' --remote-control")
        # id before --remote-control, else the flag's [name] arg eats it
        self.assertLess(cmd.index("--resume"), cmd.index("--remote-control"))

    def test_resume_cmd_remote_disabled_omits_flag(self):
        server.REMOTE = False
        self.assertEqual(server._resume_cmd("/w", "x"), "cd '/w' && cl --resume 'x'")


class LaunchRunTests(unittest.TestCase):
    """launch_run mints the Run UUID itself, bootstraps the detached tmux
    server/session, opens a bare window, and *types* the launch line into it
    (never passes it as a new-window argument). Guards the three ADR 0010
    landmines: per-window width, bare window, separate Enter."""

    def setUp(self):
        self._saved = server._tmux
        self.calls = []
        self.session_exists = True   # has-session succeeds -> bootstrap no-ops

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "has-session":
                return types.SimpleNamespace(
                    returncode=0 if self.session_exists else 1, stdout="", stderr="")
            if args[0] == "new-window":
                return types.SimpleNamespace(returncode=0, stdout="%42\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake

    def tearDown(self):
        server._tmux = self._saved

    def _of(self, verb):
        return [c for c in self.calls if c and c[0] == verb]

    def test_launch_returns_a_generated_uuid(self):
        rid = server.launch_run("/w")
        self.assertTrue(server._UUID_RE.match(rid))

    def test_the_returned_uuid_is_the_one_stamped_on_the_pane(self):
        rid = server.launch_run("/w")
        self.assertIn(("set", "-p", "-t", "%42", "@cl_run_id", rid), self.calls)

    def test_window_is_bare_and_the_launch_line_is_typed_then_submitted(self):
        server.launch_run("/w")
        # landmine 2: the window is created bare — the launch line is never a
        # new-window argument (it would run via /bin/sh -c and `cl` not be found).
        self.assertEqual(
            self._of("new-window"),
            [("new-window", "-d", "-t", server.TMUX_SOCKET, "-P", "-F", "#{pane_id}")])
        sends = self._of("send-keys")
        typed = next(c for c in sends if "-l" in c)
        enter = next(c for c in sends if c[-1] == "Enter")
        self.assertEqual(typed[:4], ("send-keys", "-t", "%42", "-l"))
        self.assertTrue(typed[4].startswith("cd ") and " cl" in typed[4])  # the launch line
        # landmine 3: submit is a SEPARATE Enter keystroke, after the literal text.
        self.assertEqual(enter, ("send-keys", "-t", "%42", "Enter"))
        self.assertLess(self.calls.index(typed), self.calls.index(enter))

    def test_width_is_pinned_per_window_never_globally(self):
        # landmine 1: `window-size manual` is set per-window only. Setting it
        # globally crashes the tmux server on the next new-window (3.6a).
        server.launch_run("/w")
        self.assertIn(("set", "-w", "-t", "%42", "window-size", "manual"), self.calls)
        self.assertNotIn(("set", "-g", "window-size", "manual"), self.calls)

    def test_task_id_is_stamped_on_the_pane(self):
        server.launch_run("/w", "/capture", task_id="cap")
        self.assertIn(("set", "-p", "-t", "%42", "@cl_task", "cap"), self.calls)

    def test_no_task_id_leaves_the_pane_untagged(self):
        server.launch_run("/w")
        self.assertEqual([c for c in self.calls if "@cl_task" in c], [])

    def test_bootstrap_creates_the_session_when_absent(self):
        self.session_exists = False
        server.launch_run("/w")
        self.assertEqual(len(self._of("new-session")), 1)
        self.assertIn("-d", self._of("new-session")[0])
        # geometry: global `window-size latest` (never `manual`) + default-size.
        self.assertIn(("set", "-g", "window-size", "latest"), self.calls)
        self.assertIn(("set", "-g", "default-size", "120x40"), self.calls)

    def test_bootstrap_noops_when_the_session_already_exists(self):
        self.session_exists = True
        server.launch_run("/w")
        self.assertEqual(self._of("new-session"), [])


class TasksDataTests(unittest.TestCase):
    """_tasks_data serializes the buttons the old _render_tasks used to draw
    (ADR 0008): input kind, placeholder, and each button's id + label."""

    def setUp(self):
        self._saved = (server.TASKS, server._tasks_mtime)
        server._tasks_mtime = lambda: server._TASKS_MTIME   # freeze the reload

    def tearDown(self):
        server.TASKS, server._tasks_mtime = self._saved

    def test_no_tasks_is_empty(self):
        server.TASKS = []
        self.assertEqual(server._tasks_data(), [])

    def test_tasks_expose_input_kind_and_buttons(self):
        server.TASKS = [
            {"id": "cap", "label": "cap", "workdir": "~", "command": "/c", "input": "text"},
            {"id": "s", "label": "s", "workdir": "~", "command": "/s", "input": "none"},
        ]
        data = server._tasks_data()
        self.assertEqual(data[0]["input"], "text")
        self.assertEqual([b["id"] for b in data[0]["buttons"]], ["cap"])
        self.assertEqual(data[1]["input"], "none")

    def test_a_button_group_shares_one_seed_and_lists_both_buttons(self):
        server.TASKS = [{
            "id": "jot", "workdir": "~", "exec": ["/bin/sh"], "input": "textarea",
            "buttons": [{"id": "jot", "label": "jot"},
                        {"id": "jot-log", "label": "log", "args": ["--log"]}],
        }]
        g = server._tasks_data()[0]
        self.assertEqual(g["input"], "textarea")        # one shared seed box
        self.assertEqual([b["id"] for b in g["buttons"]], ["jot", "jot-log"])

    def test_placeholder_defaults_to_the_label(self):
        server.TASKS = [{"id": "cap", "label": "Capture", "workdir": "~",
                         "command": "/c", "input": "text"}]
        self.assertEqual(server._tasks_data()[0]["placeholder"], "Capture…")

    def test_unknown_input_kind_falls_back_to_none(self):
        server.TASKS = [{"id": "x", "label": "x", "workdir": "~",
                         "command": "/c", "input": "weird"}]
        self.assertEqual(server._tasks_data()[0]["input"], "none")


class GroupFlattenTests(unittest.TestCase):
    def test_a_group_flattens_each_button_to_its_own_dispatch(self):
        t = {"id": "jot", "workdir": "~", "exec": ["/bin/sh", "run.sh"],
             "log": "l.log", "input": "textarea",
             "buttons": [{"id": "jot", "label": "jot"},
                         {"id": "jot-log", "label": "log", "args": ["--log"]}]}
        by_id = {b["id"]: server._resolve(t, b) for b in server._buttons(t)}
        self.assertEqual(by_id["jot"]["exec"], ["/bin/sh", "run.sh"])
        self.assertEqual(by_id["jot-log"]["exec"], ["/bin/sh", "run.sh", "--log"])
        self.assertEqual(by_id["jot-log"]["workdir"], "~")   # shared field inherited
        self.assertNotIn("buttons", by_id["jot"])            # groups never reach the handler


class RefreshTasksTests(unittest.TestCase):
    """tasks.py edits go live without a launcher restart (mtime-gated reload)."""

    def setUp(self):
        self._saved = (server.TASKS, server.TASKS_BY_ID, server.TASK_LABELS,
                       server._TASKS_MTIME, server._load_tasks, server._tasks_mtime)

    def tearDown(self):
        (server.TASKS, server.TASKS_BY_ID, server.TASK_LABELS,
         server._TASKS_MTIME, server._load_tasks, server._tasks_mtime) = self._saved

    def test_an_unchanged_mtime_never_reloads(self):
        # The hot path a test relies on: a stable file is never re-read, so a
        # directly-injected TASKS_BY_ID survives a request.
        server._TASKS_MTIME = 100
        server._tasks_mtime = lambda: 100
        server._load_tasks = lambda: self.fail("must not reload an unchanged file")
        server.refresh_tasks()

    def test_a_changed_file_is_reloaded(self):
        server._TASKS_MTIME = 100
        server._tasks_mtime = lambda: 200
        server._load_tasks = lambda: (["t"], {"x": {"id": "x", "label": "x"}}, {"x": "x"})
        server.refresh_tasks()
        self.assertIn("x", server.TASKS_BY_ID)
        self.assertEqual(server._TASKS_MTIME, 200)

    def test_a_bad_edit_keeps_the_last_good_config(self):
        good = {"good": {"id": "good", "label": "good"}}
        server.TASKS_BY_ID = dict(good)
        server._TASKS_MTIME = 100
        server._tasks_mtime = lambda: 200

        def boom():
            raise ValueError("half-typed tasks.py")

        server._load_tasks = boom
        server.refresh_tasks()
        self.assertEqual(server.TASKS_BY_ID, good)          # previous buttons survive
        self.assertEqual(server._TASKS_MTIME, 200)          # advanced, so no retry-thrash


class NamedLaunchHttpTests(_HttpCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_launch = server.launch_run
        cls._saved_tasks = server.TASKS_BY_ID
        cls.calls = []
        server.launch_run = lambda *a, **k: (cls.calls.append((a, k)), _RUN)[1]
        here = os.path.dirname(os.path.abspath(__file__))  # a dir that exists
        server.TASKS_BY_ID = {
            "cap": {"id": "cap", "label": "cap", "workdir": here,
                    "command": "/capture-task", "input": "text"},
            "sched": {"id": "sched", "label": "sched", "workdir": here,
                      "command": "/scheduling today", "input": "none"},
            "gone": {"id": "gone", "label": "gone", "workdir": "/no/such/dir",
                     "command": "/x", "input": "none"},
        }
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_run = cls._saved_launch
        server.TASKS_BY_ID = cls._saved_tasks

    def setUp(self):
        type(self).calls.clear()

    def test_unknown_task_rejected(self):
        status, body = self._post("/api/launch", {"task": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("unknown task", body["message"])

    def test_missing_workdir_rejected(self):
        status, body = self._post("/api/launch", {"task": "gone"})
        self.assertEqual(status, 400)
        self.assertIn("workdir does not exist", body["message"])

    def test_named_launch_passes_command_and_task_id(self):
        status, body = self._post("/api/launch", {"task": "sched"})
        self.assertEqual(status, 200)
        self.assertEqual(body["runId"], _RUN)
        self.assertIn("launched sched", body["message"])
        args, kwargs = self.calls[-1]
        self.assertEqual(args[1], "/scheduling today")      # prompt
        self.assertEqual(kwargs["task_id"], "sched")        # tag stamped

    def test_text_input_seed_appended(self):
        status, _ = self._post("/api/launch", {"task": "cap", "input": "buy milk"})
        self.assertEqual(status, 200)
        args, _ = self.calls[-1]
        self.assertEqual(args[1], "/capture-task buy milk")

    def test_text_input_blank_uses_bare_command(self):
        status, _ = self._post("/api/launch", {"task": "cap", "input": ""})
        self.assertEqual(status, 200)
        args, _ = self.calls[-1]
        self.assertEqual(args[1], "/capture-task")

    def test_non_string_input_is_ignored(self):
        status, _ = self._post("/api/launch", {"task": "cap", "input": {"evil": 1}})
        self.assertEqual(status, 200)
        args, _ = self.calls[-1]
        self.assertEqual(args[1], "/capture-task")


class SessionCwdTests(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "_cwdfix")
        self.proj = os.path.join(self.base, "-Users-me-proj")
        os.makedirs(self.proj, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _write(self, sid, lines):
        with open(os.path.join(self.proj, sid + ".jsonl"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def test_reads_cwd_from_transcript_skipping_meta_lines(self):
        self._write(_GOOD, [
            '{"type":"mode","sessionId":"x"}',          # meta: no cwd
            '{"type":"bridge-session"}',                # meta: no cwd
            '{"type":"user","cwd":"/Users/me/obsidian","message":{"content":"hi"}}',
        ])
        self.assertEqual(server._session_cwd(_GOOD, self.base), "/Users/me/obsidian")

    def test_falls_back_to_unmunged_dir_name_when_no_cwd(self):
        self._write(_LIVE, ['{"type":"mode","sessionId":"x"}'])  # never carries a cwd
        # lossy un-munge of the -Users-me-proj project dir
        self.assertEqual(server._session_cwd(_LIVE, self.base), "/Users/me/proj")

    def test_unknown_or_malformed_id_returns_blank(self):
        self.assertEqual(server._session_cwd(_GONE, self.base), "")
        self.assertEqual(server._session_cwd("bad-id", self.base), "")


class LiveSessionIdsTests(unittest.TestCase):
    def test_collects_nonblank_session_ids(self):
        saved = server.list_runs
        server.list_runs = lambda: [
            {"sessionId": "s1"}, {"sessionId": ""}, {"sessionId": "s2"},
        ]
        server.invalidate_runs()
        try:
            self.assertEqual(server._live_session_ids(), {"s1", "s2"})
        finally:
            server.list_runs = saved
            server.invalidate_runs()


class DispatchTests(_HttpCase):
    """A Dispatch starts no Run: no claude, no Session, no pane (ADR 0004)."""

    @classmethod
    def setUpClass(cls):
        cls._saved_tasks = dict(server.TASKS_BY_ID)
        cls._saved_dispatch = server.dispatch
        cls._saved_launch = server.launch_run
        server.launch_run = lambda *a, **k: _RUN
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.TASKS_BY_ID = cls._saved_tasks
        server.dispatch = cls._saved_dispatch
        server.launch_run = cls._saved_launch

    def setUp(self):
        self.calls = []
        server.dispatch = lambda *a, **k: self.calls.append((a, k))
        here = os.path.dirname(__file__)
        server.TASKS_BY_ID = {
            "jot": {"id": "jot", "label": "jot", "workdir": here,
                    "exec": ["/bin/bash", "run.sh"], "log": "logs/jot.log",
                    "input": "textarea"},
            "bare": {"id": "bare", "label": "bare", "workdir": here,
                     "exec": ["/usr/bin/true"], "input": "none"},
            "sess": {"id": "sess", "label": "sess", "workdir": here,
                     "command": "/capture", "input": "text"},
        }

    def test_a_dispatch_returns_no_run_id(self):
        status, body = self._post("/api/launch", {"task": "jot", "input": "wash the dog bed"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertNotIn("runId", body)          # nothing to paint an optimistic row for
        self.assertIn("dispatched jot", body["message"])

    def test_the_seed_is_passed_as_argv_never_through_a_shell(self):
        self._post("/api/launch", {"task": "jot", "input": "rm -rf / ; echo $HOME"})
        (workdir, argv, seed, log), _ = self.calls[0]
        self.assertEqual(argv, ["/bin/bash", "run.sh"])
        self.assertEqual(seed, "rm -rf / ; echo $HOME")   # inert: one argv element
        self.assertTrue(log.endswith("logs/jot.log"))

    def test_a_multiline_seed_survives_intact(self):
        self._post("/api/launch", {"task": "jot", "input": "move the Kallax\nanchor it"})
        (_, _, seed, _), _ = self.calls[0]
        self.assertEqual(seed, "move the Kallax\nanchor it")

    def test_a_dispatch_needing_a_seed_refuses_an_empty_one(self):
        status, body = self._post("/api/launch", {"task": "jot", "input": "   "})
        self.assertEqual(status, 400)
        self.assertIn("needs a seed", body["message"])
        self.assertEqual(self.calls, [])

    def test_an_input_none_dispatch_runs_with_no_seed(self):
        status, _ = self._post("/api/launch", {"task": "bare", "input": "ignored"})
        self.assertEqual(status, 200)
        (_, _, seed, _), _ = self.calls[0]
        self.assertEqual(seed, "")

    def test_an_oversized_seed_is_refused_before_it_is_spawned(self):
        status, body = self._post("/api/launch", {"task": "jot", "input": "x" * 801})
        self.assertEqual(status, 400)
        self.assertIn("800 characters", body["message"])
        self.assertEqual(self.calls, [])

    def test_a_nul_in_the_seed_is_refused(self):
        status, _ = self._post("/api/launch", {"task": "jot", "input": "a\x00b"})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_the_seed_cap_fits_inside_the_body_cap(self):
        # Worst-case UTF-8 is 4 bytes/char; a seed that passes must never have
        # tripped "body too large" first.
        self.assertLessEqual(server.MAX_SEED_CHARS * 4, server.MAX_BODY_BYTES)

    def test_a_task_still_starts_a_run(self):
        status, body = self._post("/api/launch", {"task": "sess", "input": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(body["runId"], _RUN)     # a Task is still a Run
        self.assertEqual(self.calls, [])          # and never a Dispatch

    def test_a_missing_workdir_is_refused_for_a_dispatch(self):
        server.TASKS_BY_ID["jot"]["workdir"] = "/definitely/not/here"
        status, body = self._post("/api/launch", {"task": "jot", "input": "x"})
        self.assertEqual(status, 400)
        self.assertIn("workdir does not exist", body["message"])
        self.assertEqual(self.calls, [])


class TaskConfigTests(unittest.TestCase):
    def test_exec_must_be_a_list_of_strings(self):
        with self.assertRaises(ValueError):
            server._validate_tasks([{"id": "x", "exec": "run.sh"}])
        with self.assertRaises(ValueError):
            server._validate_tasks([{"id": "x", "exec": []}])

    def test_a_task_cannot_also_be_a_dispatch(self):
        with self.assertRaises(ValueError):
            server._validate_tasks([{"id": "x", "exec": ["/usr/bin/true"], "command": "/c"}])

    def test_a_textarea_task_exposes_a_textarea_seed_and_a_button(self):
        saved = (server.TASKS, server._tasks_mtime)
        server._tasks_mtime = lambda: server._TASKS_MTIME
        server.TASKS = [{"id": "jot", "label": "jot", "workdir": "~",
                         "exec": ["/usr/bin/true"], "input": "textarea",
                         "placeholder": "a thought"}]
        try:
            g = server._tasks_data()[0]
            self.assertEqual(g["input"], "textarea")
            self.assertEqual(g["placeholder"], "a thought")
            self.assertEqual([b["id"] for b in g["buttons"]], ["jot"])
        finally:
            server.TASKS, server._tasks_mtime = saved


class DispatchSpawnTests(unittest.TestCase):
    """The real `dispatch`, exercised end to end against /bin/sh."""

    def test_it_writes_the_seed_to_the_log_and_detaches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "logs", "out.log")
            server.dispatch(d, ["/bin/sh", "-c", 'printf "%s" "$0"'], "hello $(whoami)", log)
            for _ in range(40):                      # the child is detached; wait for it
                if os.path.exists(log) and os.path.getsize(log):
                    break
                time.sleep(0.05)
            with open(log) as fh:
                # `$(whoami)` arrived literally: argv, never a shell expansion.
                self.assertEqual(fh.read(), "hello $(whoami)")

    def test_a_missing_log_directory_is_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "deep", "nested", "out.log")
            server.dispatch(d, ["/usr/bin/true"], "", log)
            self.assertTrue(os.path.isdir(os.path.dirname(log)))


if __name__ == "__main__":
    unittest.main()
