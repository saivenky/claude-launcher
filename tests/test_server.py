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


class SessionParseTests(unittest.TestCase):
    def test_parse_iterm_sessions(self):
        out = "ID1\x1f/dev/ttys001\x1f✳ Fix bug (claude)\nID2\x1f/dev/ttys002\x1fDefault\n"
        self.assertEqual(
            server._parse_iterm_sessions(out),
            [("ID1", "ttys001", "✳ Fix bug (claude)"), ("ID2", "ttys002", "Default")],
        )

    def test_parse_iterm_sessions_skips_malformed(self):
        self.assertEqual(server._parse_iterm_sessions("garbage-no-separators\n\n"), [])

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

    def test_session_meta(self):
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
            meta = server._session_meta(base)
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


class CloseSessionTests(unittest.TestCase):
    def test_rejects_bad_id_format(self):
        self.assertFalse(server.close_session("../etc"))
        self.assertFalse(server.close_session("short"))

    def test_rejects_id_not_in_live_set(self):
        saved = server.list_sessions
        server.list_sessions = lambda: [{"id": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}]
        try:
            self.assertFalse(server.close_session("BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"))
        finally:
            server.list_sessions = saved


if __name__ == "__main__":
    unittest.main()
