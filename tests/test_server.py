import os
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class EscaperTests(unittest.TestCase):
    def test_shell_quote_wraps_in_single_quotes(self):
        self.assertEqual(server.shell_quote("foo"), "'foo'")

    def test_shell_quote_escapes_single_quote(self):
        self.assertEqual(server.shell_quote("a'b"), "'a'\\''b'")

    def test_shell_quote_preserves_shell_metas(self):
        for s in ["$(x)", "`x`", ";rm", "&", "|", "\n", "\\"]:
            with self.subTest(s=s):
                self.assertTrue(server.shell_quote(s).startswith("'"))
                self.assertTrue(server.shell_quote(s).endswith("'"))

    def test_applescript_quote_escapes_backslash_and_quote(self):
        self.assertEqual(server.applescript_quote('a"b'), '"a\\"b"')
        self.assertEqual(server.applescript_quote("a\\b"), '"a\\\\b"')

    def test_applescript_quote_escapes_whitespace(self):
        self.assertEqual(server.applescript_quote("a\nb"), '"a\\nb"')
        self.assertEqual(server.applescript_quote("a\tb"), '"a\\tb"')
        self.assertEqual(server.applescript_quote("a\rb"), '"a\\rb"')

    def test_applescript_quote_strips_control_chars(self):
        self.assertEqual(server.applescript_quote("a\x07b\x1fc"), '"abc"')

    def test_sanitize_log_strips_crlf(self):
        self.assertEqual(server.sanitize_log("a\rb\nc"), "a?b?c")


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

    def test_blank_returns_default(self):
        self.assertEqual(server.resolve_dir(None), self.tmp)
        self.assertEqual(server.resolve_dir(""), self.tmp)

    def test_valid_subdir(self):
        self.assertEqual(server.resolve_dir("ok"), os.path.join(self.tmp, "ok"))

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            server.resolve_dir("../etc")

    def test_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            server.resolve_dir("/etc")

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

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _request(self, method, path, *, data=None, headers=None):
        req = urllib.request.Request(
            self._url(path), data=data, method=method, headers=headers or {}
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            finally:
                e.close()

    def test_get_index_returns_html(self):
        status, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("claude-launcher", body)

    def test_get_launch_returns_405(self):
        status, _ = self._request("GET", "/launch")
        self.assertEqual(status, 405)

    def test_get_unknown_returns_404(self):
        status, _ = self._request("GET", "/nope")
        self.assertEqual(status, 404)

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
        server.DEFAULT_DIR = os.path.dirname(__file__)  # any existing dir
        try:
            status, body = self._request(
                "POST", "/launch", data=b"",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(status, 200)
            self.assertIn("launched in", body)
        finally:
            server.DEFAULT_DIR = old_default


if __name__ == "__main__":
    unittest.main()
