import json
import os
import shutil
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

_RUN = "11111111-1111-1111-1111-111111111111"      # a Run id (iTerm pane)
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

    def test_applescript_quote_escapes_backslash_and_quote(self):
        self.assertEqual(server.applescript_quote('a"b'), '"a\\"b"')
        self.assertEqual(server.applescript_quote("a\\b"), '"a\\\\b"')

    def test_applescript_quote_escapes_whitespace(self):
        self.assertEqual(server.applescript_quote("a\nb"), '"a\\nb"')
        self.assertEqual(server.applescript_quote("a\tb"), '"a\\tb"')
        self.assertEqual(server.applescript_quote("a\rb"), '"a\\rb"')

    def test_applescript_quote_strips_control_chars(self):
        self.assertEqual(server.applescript_quote("a\x07b\x1fc"), '"abc"')


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


class HttpEndpointTests(_HttpCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_launch = server.launch_iterm
        server.launch_iterm = lambda *a, **k: _RUN  # stub out osascript
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_iterm = cls._saved_launch

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

    def test_index_serves_csp_and_no_inline_script(self):
        status, body, headers = self._raw("GET", "/")
        self.assertEqual(status, 200)
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp.split("style-src")[0])
        self.assertIn('<script src="app.js"></script>', body)
        self.assertIn("<noscript>", body)

    def test_app_js_is_served_and_never_assigns_innerhtml(self):
        status, body, headers = self._raw("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        # the invariant that replaces per-field escaping
        self.assertNotIn("innerHTML", body)


class ListRunsTests(unittest.TestCase):
    """The pane walk joined to `ps` and Claude Code's per-pid session files."""

    PANES = ("R1\x1f/dev/ttys001\x1fold work (claude)\x1f\n"
             "R2\x1f/dev/ttys002\x1flogin\x1f\n"
             "R3\x1f/dev/ttys003\x1fnot claude\x1f\n")
    PS = ("  100 ttys001 claude\n"
          "  200 ttys002 claude\n"
          "  300 ttys003 zsh\n")

    def setUp(self):
        self._saved = (server._osascript, server._ps_output, server._run_meta,
                       server._last_msg)
        server._osascript = lambda script: self.PANES
        server._ps_output = lambda: self.PS
        server._last_msg = lambda sid, *a, **k: ""
        # pid 100 registered with Claude Code; pid 200 has not yet (the ~0.5s
        # window between `claude` reaching ps and writing sessions/<pid>.json)
        server._run_meta = lambda *a, **k: {
            100: {"cwd": "/x", "status": "idle", "remote": False,
                  "sessionId": _GOOD, "updatedAt": 1000},
        }

    def tearDown(self):
        (server._osascript, server._ps_output, server._run_meta,
         server._last_msg) = self._saved

    def test_pane_without_claude_is_not_a_run(self):
        self.assertNotIn("R3", [r["id"] for r in server.list_runs()])

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


class RunsApiTests(_HttpCase):
    ROWS = [{
        "id": _RUN, "sessionId": _GOOD, "title": "fix the bug",
        "dir": "~/projects/x", "status": "busy", "remote": True,
        "updatedAt": 1783610128878, "snippet": "done, all green",
        "starting": False,
    }]

    @classmethod
    def setUpClass(cls):
        cls._saved = server.list_runs
        server.list_runs = lambda: [dict(r) for r in cls.ROWS]
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.list_runs = cls._saved

    def setUp(self):
        server.invalidate_runs()

    def test_runs_payload_shape(self):
        status, body, headers = self._raw("GET", "/api/runs")
        self.assertEqual(status, 200)
        run = json.loads(body)["runs"][0]
        self.assertEqual(run["id"], _RUN)
        self.assertEqual(run["sessionId"], _GOOD)
        self.assertEqual(run["updatedAt"], 1783610128878)   # raw epoch ms
        self.assertTrue(headers["ETag"])

    def test_no_formatted_relative_time_on_the_wire(self):
        _, body, _ = self._raw("GET", "/api/runs")
        self.assertNotIn("active", body)
        self.assertNotIn("_updated", body)

    def test_etag_is_stable_as_wall_clock_advances(self):
        # The regression test for the trap: formatting updatedAt into "47m"
        # server-side made an idle Run's payload change every minute, which
        # silently defeats both the ETag and the client's skip-render check.
        _, etag_a = server._runs_payload()
        real_time = time.time
        time.time = lambda: real_time() + 3600
        try:
            server.invalidate_runs()
            _, etag_b = server._runs_payload()
        finally:
            time.time = real_time
        self.assertEqual(etag_a, etag_b)

    def test_if_none_match_returns_304_without_body(self):
        _, _, headers = self._raw("GET", "/api/runs")
        etag = headers["ETag"]
        status, body, headers2 = self._raw(
            "GET", "/api/runs", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, "")
        self.assertEqual(headers2["ETag"], etag)

    def test_stale_etag_returns_200(self):
        status, body, _ = self._raw(
            "GET", "/api/runs", headers={"If-None-Match": '"deadbeefdeadbeef"'})
        self.assertEqual(status, 200)
        self.assertIn("runs", json.loads(body))


class IdConfusionTests(_HttpCase):
    """A Run id and a Session id are both UUIDs. Neither endpoint may take the
    other's — that ambiguity is the whole reason they were renamed."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {"launch": server.launch_iterm, "list": server.list_runs,
                      "transcript": server._transcript_path,
                      "cwd": server._session_cwd}
        cls.calls = []
        server.launch_iterm = lambda *a, **k: (cls.calls.append((a, k)), _RUN)[1]
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
        server.launch_iterm = cls._saved["launch"]
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
    def test_parse_iterm_panes(self):
        # four fields: run id, tty, name, cl_task tag (blank when untagged)
        out = ("ID1\x1f/dev/ttys001\x1f✳ Fix bug (claude)\x1fcapture\n"
               "ID2\x1f/dev/ttys002\x1fDefault\x1f\n")
        self.assertEqual(
            server._parse_iterm_panes(out),
            [("ID1", "ttys001", "✳ Fix bug (claude)", "capture"),
             ("ID2", "ttys002", "Default", "")],
        )

    def test_parse_iterm_panes_skips_malformed(self):
        self.assertEqual(server._parse_iterm_panes("garbage-no-separators\n\n"), [])

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


class LaunchItermTests(unittest.TestCase):
    def setUp(self):
        self._saved = server._osascript

    def tearDown(self):
        server._osascript = self._saved

    def test_returns_run_id_from_applescript(self):
        server._osascript = lambda script: _RUN + "\n"
        self.assertEqual(server.launch_iterm("/w"), _RUN)

    def test_non_uuid_output_yields_no_run_id(self):
        # never hand the client a correlation key it cannot match against a row
        server._osascript = lambda script: "some iTerm chatter\n"
        self.assertEqual(server.launch_iterm("/w"), "")

    def test_task_id_is_stamped_on_the_pane(self):
        seen = []
        server._osascript = lambda script: (seen.append(script), _RUN)[1]
        server.launch_iterm("/w", "/capture", task_id="cap")
        self.assertIn('set variable named "user.cl_task" to "cap"', seen[0])
        self.assertIn("return id", seen[0])


class RenderTasksTests(unittest.TestCase):
    def setUp(self):
        self._saved = server.TASKS

    def tearDown(self):
        server.TASKS = self._saved

    def test_no_tasks_renders_blank(self):
        server.TASKS = []
        self.assertEqual(server._render_tasks(), "")

    def test_tasks_render_buttons_and_seed_box(self):
        server.TASKS = [
            {"id": "cap", "label": "cap", "workdir": "~", "command": "/c", "input": "text"},
            {"id": "s", "label": "s", "workdir": "~", "command": "/s", "input": "none"},
        ]
        out = server._render_tasks()
        self.assertIn('data-task="cap"', out)
        self.assertNotIn("<form", out)                  # forms are gone; JS posts JSON
        self.assertIn("or launch a dir", out)           # divider before generic launcher
        self.assertEqual(out.count("<input"), 1)        # only the text task gets a seed box

    def test_a_button_group_shares_one_seed_box(self):
        server.TASKS = [{
            "id": "jot", "workdir": "~", "exec": ["/bin/sh"], "input": "textarea",
            "buttons": [{"id": "jot", "label": "jot"},
                        {"id": "jot-log", "label": "log", "args": ["--log"]}],
        }]
        out = server._render_tasks()
        self.assertEqual(out.count("<textarea"), 1)     # one shared box, not two
        self.assertIn('data-task="jot"', out)
        self.assertIn('data-task="jot-log"', out)
        self.assertIn('class="btnrow"', out)            # buttons grouped in a row

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
        cls._saved_launch = server.launch_iterm
        cls._saved_tasks = server.TASKS_BY_ID
        cls.calls = []
        server.launch_iterm = lambda *a, **k: (cls.calls.append((a, k)), _RUN)[1]
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
        server.launch_iterm = cls._saved_launch
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
        cls._saved_launch = server.launch_iterm
        server.launch_iterm = lambda *a, **k: _RUN
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
        server.launch_iterm = cls._saved_launch

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

    def test_a_textarea_task_renders_a_textarea_and_a_button(self):
        saved = server.TASKS
        server.TASKS = [{"id": "jot", "label": "jot", "workdir": "~",
                         "exec": ["/usr/bin/true"], "input": "textarea",
                         "placeholder": "a thought"}]
        try:
            html_out = server._render_tasks()
            self.assertIn('<textarea class="input"', html_out)
            self.assertIn('placeholder="a thought"', html_out)
            self.assertIn('class="task multiline"', html_out)
            self.assertIn('data-task="jot"', html_out)
        finally:
            server.TASKS = saved

    def test_a_placeholder_cannot_inject_markup(self):
        saved = server.TASKS
        server.TASKS = [{"id": "x", "label": "x", "workdir": "~", "exec": ["/usr/bin/true"],
                         "input": "textarea", "placeholder": '"><script>alert(1)</script>'}]
        try:
            self.assertNotIn("<script>", server._render_tasks())
        finally:
            server.TASKS = saved


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
