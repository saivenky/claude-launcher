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


class HttpEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_launch = server.launch_iterm
        server.launch_iterm = lambda _workdir: None  # stub out osascript
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_iterm = cls._saved_launch

    def _request(self, method, path, *, data=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data, method=method, headers=headers or {},
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            finally:
                e.close()

    def test_cross_origin_post_blocked(self):
        status, body = self._request(
            "POST", "/launch",
            data=b"dir=",
            headers={"Origin": "http://evil.com", "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 403)
        self.assertIn("cross-origin", body)

    def test_same_origin_post_with_invalid_dir_returns_400(self):
        host = f"127.0.0.1:{self.port}"
        status, body = self._request(
            "POST", "/launch",
            data=b"dir=definitely-does-not-exist-xyz",
            headers={"Origin": f"http://{host}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 400)
        self.assertIn("subdir", body)

    def test_oversized_body_rejected(self):
        big = b"dir=" + b"A" * 5000
        status, _ = self._request("POST", "/launch", data=big,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 413)

    def test_wrong_content_type_rejected(self):
        status, _ = self._request("POST", "/launch", data=b'{"dir":"x"}',
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(status, 415)

    def test_post_no_origin_with_stubbed_launch_succeeds(self):
        old_default = server.DEFAULT_DIR
        server.DEFAULT_DIR = os.path.dirname(__file__)
        try:
            status, body = self._request(
                "POST", "/launch", data=b"",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(status, 200)
            self.assertIn("launched in", body)
        finally:
            server.DEFAULT_DIR = old_default


class RunParseTests(unittest.TestCase):
    def test_parse_iterm_panes(self):
        # four fields: id, tty, name, cl_task tag (blank when untagged)
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
                        '"bridgeSessionId":"session_abc"}')
            with open(os.path.join(base, "11402.json"), "w") as f:
                f.write('{"pid":11402,"cwd":"/x","status":"idle"}')  # no bridge
            with open(os.path.join(base, "bad.json"), "w") as f:
                f.write("not json")  # skipped
            meta = server._run_meta(base)
            self.assertEqual(meta[39909], {
                "cwd": "/Users/me/obsidian", "status": "waiting", "remote": True,
                "sessionId": "", "updatedAt": None,
            })
            self.assertEqual(meta[11402], {
                "cwd": "/x", "status": "idle", "remote": False,
                "sessionId": "", "updatedAt": None,
            })
            self.assertEqual(set(meta), {39909, 11402})
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_first_user_msg(self):
        base = os.path.join(os.path.dirname(__file__), "_txfix")
        sid = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        proj = os.path.join(base, "-Users-me-proj")
        os.makedirs(proj, exist_ok=True)
        try:
            lines = [
                '{"type":"user","message":{"content":"<system-reminder>skip me"}}',
                '{"type":"assistant","message":{"content":"not user"}}',
                '{"type":"user","message":{"content":[{"type":"tool_result","content":"skip"}]}}',
                '{"type":"user","message":{"content":[{"type":"text","text":"fix the failing test"}]}}',
            ]
            with open(os.path.join(proj, sid + ".jsonl"), "w") as f:
                f.write("\n".join(lines) + "\n")
            self.assertEqual(server._first_user_msg(sid, base), "fix the failing test")
            self.assertEqual(server._first_user_msg("bad-id", base), "")
            self.assertEqual(server._first_user_msg(sid, base + "_missing"), "")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_last_msg(self):
        base = os.path.join(os.path.dirname(__file__), "_lmfix")
        sid = "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"
        proj = os.path.join(base, "-Users-me-proj")
        os.makedirs(proj, exist_ok=True)
        try:
            lines = [
                '{"type":"user","message":{"content":[{"type":"text","text":"first ask"}]}}',
                '{"type":"assistant","message":{"content":[{"type":"text","text":"done, all green"}]}}',
                '{"type":"user","message":{"content":[{"type":"tool_result","content":"ignore me"}]}}',
            ]
            with open(os.path.join(proj, sid + ".jsonl"), "w") as f:
                f.write("\n".join(lines) + "\n")
            # last text message wins; trailing tool_result is skipped
            self.assertEqual(server._last_msg(sid, base), "done, all green")
            self.assertEqual(server._last_msg("bad-id", base), "")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_last_active(self):
        self.assertEqual(server._last_active(None), "")
        self.assertEqual(server._last_active((time.time() - 30) * 1000), "now")
        self.assertEqual(server._last_active((time.time() - 47 * 60) * 1000), "47m")
        self.assertEqual(server._last_active((time.time() - 3 * 3600) * 1000), "3h")
        self.assertEqual(server._last_active((time.time() - 4 * 86400) * 1000), "4d")

    def test_clean_title(self):
        self.assertEqual(
            server._clean_title("✳ Analyze daily scheduling feasibility (Python)"),
            "Analyze daily scheduling feasibility")
        self.assertEqual(
            server._clean_title("⠐ Improve /grill-me command functionality (caffeinate)"),
            "Improve /grill-me command functionality")
        self.assertEqual(server._clean_title("Plain title"), "Plain title")


class CloseRunTests(unittest.TestCase):
    def test_rejects_bad_id_format(self):
        self.assertFalse(server.close_run("../etc"))
        self.assertFalse(server.close_run("short"))

    def test_rejects_id_not_in_live_set(self):
        saved = server.list_runs
        server.list_runs = lambda: [{"id": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}]
        try:
            self.assertFalse(server.close_run("BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"))
        finally:
            server.list_runs = saved


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
        sid = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        cmd = server._resume_cmd("/w", sid)
        self.assertEqual(cmd, f"cd '/w' && cl --resume '{sid}' --remote-control")
        # id before --remote-control, else the flag's [name] arg eats it
        self.assertLess(cmd.index("--resume"), cmd.index("--remote-control"))

    def test_resume_cmd_remote_disabled_omits_flag(self):
        server.REMOTE = False
        self.assertEqual(server._resume_cmd("/w", "x"), "cd '/w' && cl --resume 'x'")


class RenderTasksTests(unittest.TestCase):
    def setUp(self):
        self._saved = server.TASKS

    def tearDown(self):
        server.TASKS = self._saved

    def test_no_tasks_renders_blank(self):
        server.TASKS = []
        self.assertEqual(server._render_tasks(), "")

    def test_tasks_render_buttons_and_text_box(self):
        server.TASKS = [
            {"id": "cap", "label": "cap", "workdir": "~", "command": "/c", "input": "text"},
            {"id": "s", "label": "s", "workdir": "~", "command": "/s", "input": "none"},
        ]
        out = server._render_tasks()
        self.assertIn('name="task" value="cap"', out)
        self.assertIn("or launch a dir", out)          # divider before generic form
        # only the text task gets a seed box; the 'none' task does not
        self.assertEqual(out.count('name="input"'), 1)


class NamedLaunchHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_launch = server.launch_iterm
        cls._saved_tasks = server.TASKS_BY_ID
        cls.calls = []
        server.launch_iterm = lambda *a, **k: cls.calls.append((a, k))
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

    def _post(self, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/launch", data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            finally:
                e.close()

    def test_unknown_task_rejected(self):
        status, body = self._post(b"task=nope")
        self.assertEqual(status, 400)
        self.assertIn("unknown task", body)

    def test_missing_workdir_rejected(self):
        status, body = self._post(b"task=gone")
        self.assertEqual(status, 400)
        self.assertIn("workdir does not exist", body)

    def test_named_launch_passes_command_and_task_id(self):
        status, body = self._post(b"task=sched")
        self.assertEqual(status, 200)
        self.assertIn("launched sched", body)
        args, kwargs = self.calls[-1]
        self.assertEqual(args[1], "/scheduling today")      # prompt
        self.assertEqual(kwargs["task_id"], "sched")        # tag stamped

    def test_text_input_seed_appended(self):
        status, _ = self._post(b"task=cap&input=buy%20milk")
        self.assertEqual(status, 200)
        args, _ = self.calls[-1]
        self.assertEqual(args[1], "/capture-task buy milk")

    def test_text_input_blank_uses_bare_command(self):
        status, _ = self._post(b"task=cap&input=")
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
        sid = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        self._write(sid, [
            '{"type":"mode","sessionId":"x"}',          # meta: no cwd
            '{"type":"bridge-session"}',                # meta: no cwd
            '{"type":"user","cwd":"/Users/me/obsidian","message":{"content":"hi"}}',
        ])
        self.assertEqual(server._session_cwd(sid, self.base), "/Users/me/obsidian")

    def test_falls_back_to_unmunged_dir_name_when_no_cwd(self):
        sid = "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"
        self._write(sid, ['{"type":"mode","sessionId":"x"}'])  # never carries a cwd
        # lossy un-munge of the -Users-me-proj project dir
        self.assertEqual(server._session_cwd(sid, self.base), "/Users/me/proj")

    def test_unknown_or_malformed_id_returns_blank(self):
        self.assertEqual(
            server._session_cwd("CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC", self.base), "")
        self.assertEqual(server._session_cwd("bad-id", self.base), "")


class LiveSessionIdsTests(unittest.TestCase):
    def test_collects_nonblank_session_ids(self):
        saved = server.list_runs
        server.list_runs = lambda: [
            {"sessionId": "s1"}, {"sessionId": ""}, {"sessionId": "s2"},
        ]
        try:
            self.assertEqual(server._live_session_ids(), {"s1", "s2"})
        finally:
            server.list_runs = saved


_GOOD = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"    # transcript + existing cwd
_LIVE = "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"    # currently-live Session
_GONE = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"    # transcript, but cwd deleted
_UNKNOWN = "DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD"  # no transcript


class ResumeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {
            "launch_iterm": server.launch_iterm,
            "transcript": server._transcript_path,
            "cwd": server._session_cwd,
            "live": server._live_session_ids,
        }
        cls.here = os.path.dirname(os.path.abspath(__file__))  # a dir that exists
        cls.calls = []
        server.launch_iterm = lambda *a, **k: cls.calls.append((a, k))
        server._transcript_path = lambda sid, *a, **k: (
            "/x/" + sid + ".jsonl" if sid in (_GOOD, _LIVE, _GONE) else "")
        server._session_cwd = lambda sid, *a, **k: (
            "/no/such/dir" if sid == _GONE else cls.here)
        server._live_session_ids = lambda: {_LIVE}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server.launch_iterm = cls._saved["launch_iterm"]
        server._transcript_path = cls._saved["transcript"]
        server._session_cwd = cls._saved["cwd"]
        server._live_session_ids = cls._saved["live"]

    def setUp(self):
        type(self).calls.clear()

    def _post(self, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/resume", data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            finally:
                e.close()

    def test_malformed_id_rejected(self):
        status, body = self._post(b"session_id=not-a-uuid")
        self.assertEqual(status, 400)
        self.assertIn("invalid session id", body)
        self.assertEqual(self.calls, [])

    def test_unknown_id_rejected(self):
        status, body = self._post(f"session_id={_UNKNOWN}".encode())
        self.assertEqual(status, 400)
        self.assertIn("no such session", body)
        self.assertEqual(self.calls, [])

    def test_live_id_rejected(self):
        status, body = self._post(f"session_id={_LIVE}".encode())
        self.assertEqual(status, 400)
        self.assertIn("already live", body)
        self.assertEqual(self.calls, [])

    def test_missing_dir_rejected(self):
        status, body = self._post(f"session_id={_GONE}".encode())
        self.assertEqual(status, 400)
        self.assertIn("dir is gone", body)
        self.assertEqual(self.calls, [])

    def test_valid_resume_spawns_session(self):
        status, body = self._post(f"session_id={_GOOD}".encode())
        self.assertEqual(status, 200)
        self.assertIn(f"resumed {_GOOD}", body)
        args, kwargs = self.calls[-1]
        self.assertEqual(args[0], self.here)          # workdir from transcript cwd
        self.assertEqual(kwargs["resume_id"], _GOOD)  # spawns cl --resume <id>

    def test_get_is_405(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/resume", method="GET")
        try:
            urllib.request.urlopen(req, timeout=2)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
        self.assertEqual(code, 405)  # POST-only, mirrors /launch

    def test_cross_origin_rejected(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/resume",
            data=f"session_id={_GOOD}".encode(), method="POST",
            headers={"Origin": "http://evil.com",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            e.close()
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
