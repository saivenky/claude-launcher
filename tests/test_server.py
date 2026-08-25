import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _capture(name: str) -> str:
    """A verbatim `capture-pane` fixture, read from disk.

    Never retyped into a literal: the exact whitespace IS the thing under test,
    and ADR 0020 traces the whole renderer change going unnoticed to the inline
    `_ASK_PANE` below — an iTerm frame frozen in Python that kept passing while
    the real renderer moved out from under it. See tests/fixtures/README.md.
    """
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()

_RUN = "11111111-1111-1111-1111-111111111111"      # a Run id (tmux window)
_RUN2 = "22222222-2222-2222-2222-222222222222"     # the Run a Transfer resumes into
_GOOD = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"     # transcript + existing cwd
_LIVE = "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"     # Session with a live Run
_GONE = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"     # transcript, but cwd deleted
_UNKNOWN = "DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD"  # no transcript
_GOOD2 = "EEEEEEEE-EEEE-EEEE-EEEE-EEEEEEEEEEEE"    # a second resumable Session


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


_S1 = "a1a1a1a1-1111-1111-1111-111111111111"
_S2 = "a2a2a2a2-2222-2222-2222-222222222222"
_SHOME = "b0b0b0b0-0000-0000-0000-000000000000"
_SLIVE = "cccccccc-3333-3333-3333-333333333333"
_SDEAD = "dddddddd-4444-4444-4444-444444444444"
_SSDK = "eeeeeeee-5555-5555-5555-555555555555"


class RecoverableSessionsTests(unittest.TestCase):
    """The Recover picker's candidate list (slice 01): Resumable Sessions —
    transcript on disk + cwd still exists + no live Run — Session-granularity,
    newest-first, spanning every dir (no PROJECTS_ROOT confinement, ADR 0002)."""

    def setUp(self):
        self.tmp = os.path.realpath(os.path.join(os.path.dirname(__file__), "_recfix"))
        self.root = os.path.join(self.tmp, "root")     # a stand-in PROJECTS_ROOT
        self.state = os.path.join(self.tmp, "state")    # stand-in ~/.claude/projects
        for sub in ("alpha", "beta"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        self._saved_root = server.PROJECTS_ROOT
        server.PROJECTS_ROOT = self.root                # must NOT confine the list

    def tearDown(self):
        server.PROJECTS_ROOT = self._saved_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _txn(self, slug, sid, cwd, mtime, texts=("hello",), entrypoint=None):
        """A transcript for `sid` in project dir `slug`, first line carrying
        `cwd` (and `entrypoint`, when given, as Claude Code records it on that
        same row), `texts` written as successive user messages, stamped `mtime`."""
        d = os.path.join(self.state, slug)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sid + ".jsonl")
        lines = []
        for i, t in enumerate(texts):
            o = {"type": "user", "message": {"content": [{"type": "text", "text": t}]}}
            if i == 0:
                o["cwd"] = cwd
                if entrypoint is not None:
                    o["entrypoint"] = entrypoint
            lines.append(json.dumps(o))
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.utime(p, (mtime, mtime))
        os.utime(d, (mtime, mtime))
        return p

    def test_session_granularity_dead_cwd_live_and_span(self):
        home = os.path.expanduser("~")
        alpha = os.path.join(self.root, "alpha")
        # Two Sessions in ONE project dir -> two rows (not dir-deduped).
        self._txn("proj-a", _S1, alpha, 3000)
        self._txn("proj-a", _S2, alpha, 1000)
        # A cwd OUTSIDE PROJECTS_ROOT (home) is kept -> spans every dir.
        self._txn("proj-home", _SHOME, home, 5000)
        # A Session with a live Run is excluded; a dead-cwd Session is hidden.
        self._txn("proj-live", _SLIVE, os.path.join(self.root, "beta"), 4500)
        self._txn("proj-dead", _SDEAD, os.path.join(self.tmp, "ghost"), 4000)

        rows = server._recoverable_sessions(base=self.state, live={_SLIVE})
        ids = [r["sessionId"] for r in rows]
        # newest-first by transcript mtime; live + dead-cwd absent entirely.
        self.assertEqual(ids, [_SHOME, _S1, _S2])
        self.assertNotIn(_SLIVE, ids)
        self.assertNotIn(_SDEAD, ids)
        # both alpha Sessions survive as distinct rows (Session-granularity).
        self.assertEqual([r["dir"] for r in rows if r["sessionId"] in (_S1, _S2)],
                         [server._display_path(alpha)] * 2)
        # home cwd renders tilde-collapsed, as the board shows dirs.
        self.assertEqual(rows[0]["dir"], "~")
        self.assertEqual([r["mtime"] for r in rows], [5000, 3000, 1000])

    def test_title_skips_skill_preamble_and_bare_slash(self):
        alpha = os.path.join(self.root, "alpha")
        self._txn("proj-t", _S1, alpha, 3000, texts=(
            "Base directory for this skill: /x/y\n\n# Do Stuff",   # skill preamble
            "/scheduling",                                          # bare slash-command
            "actually build the thing",                             # the real ask
        ))
        rows = server._recoverable_sessions(base=self.state, live=set())
        self.assertEqual(rows[0]["title"], "actually build the thing")

    def test_nickname_rides_along_and_is_none_when_unset(self):
        """The Recover picker's rows are a different payload from the board's —
        a Session with no live Run at all — so it needs its own Nickname rather
        than inheriting the board's copy. Keyed the same way (sessionId), since
        that is the one id every lifecycle verb leaves untouched (ADR 0026)."""
        alpha = os.path.join(self.root, "alpha")
        self._txn("proj-a", _S1, alpha, 3000)
        self._txn("proj-b", _S2, alpha, 2000)
        saved = dict(server._NICKNAME)
        try:
            server._NICKNAME.clear()
            server._NICKNAME[_S1] = "the flaky test"
            rows = {r["sessionId"]: r for r in server._recoverable_sessions(
                base=self.state, live=set())}
            self.assertEqual(rows[_S1]["nickname"], "the flaky test")
            self.assertIsNone(rows[_S2]["nickname"])   # never "" — one representation
        finally:
            server._NICKNAME.clear()
            server._NICKNAME.update(saved)

    def test_missing_state_dir_is_empty(self):
        self.assertEqual(
            server._recoverable_sessions(base=os.path.join(self.tmp, "nope"), live=set()), [])

    def test_capped_at_max(self):
        alpha = os.path.join(self.root, "alpha")
        for i in range(server._RECOVERABLE_MAX + 5):
            sid = f"{i:08d}-5555-5555-5555-555555555555"
            self._txn(f"proj-{i:02d}", sid, alpha, 1000 + i)
        rows = server._recoverable_sessions(base=self.state, live=set())
        self.assertEqual(len(rows), server._RECOVERABLE_MAX)

    def test_headless_origin_excluded_absent_or_cli_entrypoint_kept(self):
        """A **Headless Session** — one whose transcript records no interactive
        entrypoint — is never offered. Absent field == interactive: fail toward
        showing, because old and third-party transcripts do not carry it."""
        alpha = os.path.join(self.root, "alpha")
        self._txn("proj-sdk", _SSDK, alpha, 5000, entrypoint="sdk-py")   # headless
        self._txn("proj-cli", _S1, alpha, 4000, entrypoint="cli")        # a terminal
        self._txn("proj-old", _S2, alpha, 3000)                          # no field

        ids = [r["sessionId"] for r in server._recoverable_sessions(
            base=self.state, live=set())]
        self.assertEqual(ids, [_S1, _S2])
        self.assertNotIn(_SSDK, ids)

    def test_headless_cluster_does_not_inflate_the_recovery_set(self):
        """The exclusion is upstream of the recency cluster: a burst of headless
        transcripts newer than real work must neither pre-tick nor chain the set
        through to older Sessions it would otherwise not reach."""
        alpha = os.path.join(self.root, "alpha")
        # 6 SDK transcripts a minute apart, newest on top …
        for i in range(6):
            sid = f"{i:08d}-9999-9999-9999-999999999999"
            self._txn(f"proj-sdk-{i}", sid, alpha, 100_000 - 60 * i, entrypoint="sdk-py")
        # … then real work, far enough below the burst that chaining through it
        # would be the only way the older Session could join the set.
        self._txn("proj-a", _S1, alpha, 100_000 - 60 * 5 - 10 * 60)
        self._txn("proj-b", _S2, alpha, 100_000 - 60 * 5 - 40 * 60)

        rows = server._recoverable_sessions(base=self.state, live=set())
        self.assertEqual([r["sessionId"] for r in rows], [_S1, _S2])
        # the surviving pair is 30min apart -> a gap over G, so only the anchor
        # pre-ticks. With the headless burst counted it would have been both.
        count = server._recovery_set_size([r["mtime"] for r in rows])
        self.assertEqual(count, 1)

    def test_resume_guard_still_accepts_a_headless_session(self):
        """Only what Recover *offers* narrows. A Headless Session is still a
        Resumable Session: paste its sessionId and Resume must still work — the
        escape hatch for the day you do want to reopen one."""
        alpha = os.path.join(self.root, "alpha")
        self._txn("proj-sdk", _SSDK, alpha, 5000, entrypoint="sdk-py")
        saved = (server._transcript_path, server._session_cwd, server._live_session_ids)
        find, cwd_of = saved[0], saved[1]        # point both at the fixture state
        server._transcript_path = lambda sid, *a, **k: find(sid, self.state)
        server._session_cwd = lambda sid, *a, **k: cwd_of(sid, self.state)
        server._live_session_ids = lambda: set()
        try:
            workdir, message = server._resume_guard(_SSDK)
        finally:
            (server._transcript_path, server._session_cwd,
             server._live_session_ids) = saved
        self.assertEqual(message, "")
        self.assertEqual(os.path.realpath(workdir), alpha)


class RecoverySetTests(unittest.TestCase):
    """The recovery-set recency cluster (slice 02, ADR 0013): a pure function
    over candidate mtimes (newest-first) returning how many of the top rows
    pre-tick. Constants: G = gap, S = span leash, N = cap."""

    def test_empty_list_selects_none(self):
        self.assertEqual(server._recovery_set_size([]), 0)

    def test_single_candidate_is_its_own_anchor(self):
        # a lone candidate is the anchor -> a cluster of one, always pre-ticked
        self.assertEqual(server._recovery_set_size([1_000]), 1)

    def test_anchor_on_newest_and_tight_chain(self):
        # gaps well within G, span within S -> the whole prefix pre-ticks
        self.assertEqual(server._recovery_set_size([10_000, 9_500, 9_000, 8_600]), 4)

    def test_gap_at_G_included_over_G_excluded(self):
        G = server._RECOVERY_GAP
        self.assertEqual(server._recovery_set_size([2_000, 2_000 - G]), 2)       # == G kept
        self.assertEqual(server._recovery_set_size([2_000, 2_000 - G - 1]), 1)   # > G stops
        # a gap over G mid-chain halts the chain right there (anchor + 1)
        self.assertEqual(
            server._recovery_set_size([3_000, 3_000 - G, 3_000 - G - (G + 1)]), 2)

    def test_gap_is_measured_to_previous_member_not_anchor(self):
        G = server._RECOVERY_GAP
        # 2nd gap is 800s (< G) though the 3rd row sits G+800 (> G) from the
        # anchor -> kept, proving rule 2 chains off the last member, not anchor.
        self.assertEqual(
            server._recovery_set_size([3_000, 3_000 - G, 3_000 - G - 800]), 3)

    def test_span_leash_cuts_an_otherwise_chained_tail(self):
        # every adjacent gap is a comfortable 800s (< G) so the CHAIN never
        # breaks; only the span leash (S) stops it, mid-run.
        mtimes = [100_000 - 800 * i for i in range(12)]
        # span first exceeds S=5400 at the 8th row (800*7=5600); the 7 rows up
        # to 800*6=4800 stay.
        self.assertEqual(server._recovery_set_size(mtimes), 7)

    def test_span_at_S_included_over_S_excluded(self):
        S, G = server._RECOVERY_SPAN, server._RECOVERY_GAP
        self.assertEqual(S, 6 * G)                          # reach exactly S in 6 hops
        at_S = [10_000 - G * i for i in range(7)]           # spans 0..S, last == S
        self.assertEqual(server._recovery_set_size(at_S), 7)    # row at span S kept
        over_S = at_S + [at_S[-1] - G]                      # next row: span S+G
        self.assertEqual(server._recovery_set_size(over_S), 7)  # > S dropped

    def test_cap_at_N(self):
        # a long tight chain (60s gaps, span stays under S for all rows) is
        # capped at N regardless of how many candidates still qualify.
        mtimes = [100_000 - 60 * i for i in range(server._RECOVERY_MAX + 8)]
        self.assertEqual(server._recovery_set_size(mtimes), server._RECOVERY_MAX)


class TitleNoiseTests(unittest.TestCase):
    def test_is_title_noise(self):
        self.assertTrue(server._is_title_noise("Base directory for this skill: /a/b"))
        self.assertTrue(server._is_title_noise("/scheduling"))
        self.assertTrue(server._is_title_noise("/clear"))
        # a real prompt that merely contains a slash command is kept
        self.assertFalse(server._is_title_noise("Let's /ship the release"))
        self.assertFalse(server._is_title_noise("fix the failing test"))


class RecoverableApiTests(_HttpCase):
    """GET /api/recoverable serves the Resumable-Session list as JSON with an
    ETag, mirroring /api/board and /api/tasks — read-only, no token gate."""

    @classmethod
    def setUpClass(cls):
        cls._saved = server._recoverable_sessions
        server._recoverable_sessions = lambda *a, **k: [
            {"sessionId": _S1, "dir": "~/obsidian", "title": "write the note", "mtime": 42}]
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        server._recoverable_sessions = cls._saved

    def test_payload_shape_and_etag(self):
        status, body, headers = self._raw("GET", "/api/recoverable")
        self.assertEqual(status, 200)
        self.assertTrue(headers["ETag"])
        # slice 02: the lone candidate is its own anchor -> pre-ticked, count 1.
        self.assertEqual(json.loads(body), {"sessions": [
            {"sessionId": _S1, "dir": "~/obsidian", "title": "write the note",
             "mtime": 42, "preselect": True}], "preselectCount": 1})

    def test_if_none_match_returns_304_without_body(self):
        _, _, headers = self._raw("GET", "/api/recoverable")
        etag = headers["ETag"]
        status, body, headers2 = self._raw(
            "GET", "/api/recoverable", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, "")
        self.assertEqual(headers2["ETag"], etag)


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
        for path in ("/api/launch", "/api/resume", "/api/recover", "/api/close", "/api/transfer"):
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
        # ADR 0006: exactly one innerHTML *assignment* (a **Turn**'s
        # server-escaped markdown); every other field stays textContent. ADR 0014
        # widened the exception from one field to N turns — through the same
        # function, so it is still ONE sink a reviewer has to reason about.
        self.assertIn("md.innerHTML = t.html", body)
        assigns = [ln for ln in body.splitlines()
                   if ".innerHTML" in ln and "=" in ln and not ln.strip().startswith("//")]
        self.assertEqual(len(assigns), 1)

    def test_the_scrollback_has_no_scroll_box_of_its_own(self):
        # ADR 0014: `.ctx{max-height:46vh;overflow-y:auto}` letterboxed a long
        # run-up into ~46% of a phone viewport, inside a second scrollbar. The
        # **Scrollback** flows into ordinary page scroll instead — no cap, no
        # nested scroller, and the retired class gone with it.
        _, body, _ = self._raw("GET", "/")
        rules = [ln for ln in body.splitlines() if ln.startswith(".sb{")]
        self.assertEqual(len(rules), 1, "expected one .sb rule")
        self.assertNotIn("max-height", rules[0])
        self.assertNotIn("overflow", rules[0])
        self.assertNotIn(".ctx", body)

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


class ForeignRunTests(unittest.TestCase):
    """A **Foreign Run** is a live `claude` the Launcher did not start: a `ttys*`
    in `ps` that is none of our panes (ADR 0012). It falls out of the same walk
    the Managed Runs do — `ps` plus sessions/<pid>.json, both already in hand —
    so detecting one costs no extra subprocess call."""

    PANES = "R1\x1f/dev/ttys001\x1fold work (claude)\x1f\x1f@1\n"
    PS = ("  100 ttys001 claude\n"                   # ours — the pane above
          "  400 ttys004 claude\n"                   # foreign — a claude in iTerm
          "  500 ??      claude -p summarize\n"      # headless — a Dispatch / CI
          "  600 ttys006 zsh\n")                     # not a claude at all
    META = {
        100: {"cwd": "/x", "status": "idle", "remote": False,
              "sessionId": _GOOD, "updatedAt": 1000},
        400: {"cwd": os.path.expanduser("~/projects/mine"), "status": "waiting",
              "remote": False, "bridge": "", "sessionId": _LIVE, "updatedAt": 5000},
    }

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("_list_panes_raw", "_ps_output", "_run_meta", "_last_msg",
                        "_first_user_msg")}
        server._list_panes_raw = lambda: self.PANES
        server._ps_output = lambda: self.PS
        server._run_meta = lambda *a, **k: self.META
        server._last_msg = lambda sid, *a, **k: "tail of " + sid
        server._first_user_msg = lambda sid, *a, **k: "opening ask of " + sid

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(server, name, fn)

    def _by_session(self):
        return {r["sessionId"]: r for r in server.list_runs()}

    def test_a_claude_in_another_terminal_is_a_foreign_run(self):
        row = self._by_session()[_LIVE]
        self.assertTrue(row["foreign"])
        self.assertEqual(row["pid"], 400)   # the only handle Transfer will have

    def test_a_headless_claude_p_is_invisible(self):
        # `claude -p` (a Dispatch, a script, CI) has no tty, so _parse_claude_ttys
        # never sees it. It is nobody's Run: there is nothing to observe and
        # nothing to transfer, and a widened filter would put CI jobs on the Board.
        self.assertEqual({r.get("pid") for r in server.list_runs() if r.get("foreign")},
                         {400})

    def test_a_managed_run_is_not_misclassified_as_foreign(self):
        row = self._by_session()[_GOOD]
        self.assertFalse(row.get("foreign"))
        self.assertEqual(row["id"], "R1")
        self.assertIn("select-window -t @1", row["attach"])

    def test_a_starting_managed_run_is_not_briefly_foreign(self):
        # Our pane's `claude` reaches `ps` ~0.5s before it writes
        # sessions/<pid>.json. It must stay Managed-and-starting through that
        # window, not flip kind under the Board for half a second.
        server._list_panes_raw = lambda: self.PANES + "R2\x1f/dev/ttys002\x1flogin\x1f\x1f@2\n"
        server._ps_output = lambda: self.PS + "  200 ttys002 claude\n"
        rows = [r for r in server.list_runs() if r.get("id") == "R2"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["starting"])
        self.assertFalse(rows[0].get("foreign"))

    def test_a_claude_in_a_pane_we_did_not_stamp_is_foreign(self):
        # Managed vs Foreign is decided by who started the Run, never by which
        # terminal holds it — a hand-run `claude` inside our own tmux server has
        # no @cl_run_id and is just as unreachable.
        server._list_panes_raw = lambda: self.PANES + "\x1f/dev/ttys009\x1fshell\x1f\x1f@9\n"
        server._ps_output = lambda: self.PS + "  900 ttys009 claude\n"
        server._run_meta = lambda *a, **k: {
            **self.META,
            900: {"cwd": "/z", "status": "idle", "remote": False,
                  "sessionId": _GONE, "updatedAt": 9000},
        }
        self.assertTrue(self._by_session()[_GONE]["foreign"])

    def test_a_foreign_claude_with_no_session_yet_is_skipped(self):
        # No sessions/<pid>.json means no Session: nothing to resume (so nothing
        # to transfer) and no sessionId to guard with. A contentless row would
        # only be noise.
        server._run_meta = lambda *a, **k: {100: self.META[100]}
        self.assertEqual([r for r in server.list_runs() if r.get("foreign")], [])

    def test_a_foreign_row_carries_session_state_but_no_pane_handles(self):
        # Everything on it comes from Claude Code's own state, never the
        # terminal's; the two pane-shaped fields stay empty because there is no
        # pane behind it.
        row = self._by_session()[_LIVE]
        self.assertEqual(row["id"], "")
        self.assertEqual(row["attach"], "")
        self.assertEqual(row["dir"], "~/projects/mine")
        self.assertEqual(row["status"], "waiting")
        self.assertEqual(row["snippet"], "tail of " + _LIVE)
        self.assertEqual(row["title"], "opening ask of " + _LIVE)
        self.assertFalse(row["starting"])

    def test_a_dead_tmux_server_hides_our_runs_but_not_the_foreign_one(self):
        # A dead tmux server takes every Managed Run with it (ADR 0010) — but the
        # claude left running in iTerm is untouched, and still forks its
        # transcript if the resume guard cannot see it. Our own Runs degrade to
        # Foreign rather than vanishing, which is honest: with no tmux to resolve
        # them through, unreachable is exactly what they now are.
        def gone():
            raise FileNotFoundError("tmux")

        server._list_panes_raw = gone
        rows = server.list_runs()
        self.assertIn(_LIVE, [r["sessionId"] for r in rows])
        self.assertEqual([r for r in rows if not r.get("foreign")], [])


class ForeignRunGuardTests(unittest.TestCase):
    """Nothing that reaches for a pane may act on a **Foreign Run** — it has no
    pane, so close / Respond / clear would either fail or, worse, land on some
    other Run. The refusal is enforced here, not trusted to the client: the
    fixture's foreign row deliberately carries a well-formed Run id."""

    def setUp(self):
        self._saved = (server.list_runs, server._tmux)
        self.calls = []
        server.list_runs = lambda: [
            {"id": _RUN, "sessionId": _GOOD},
            # a hostile shape: a Foreign Run that claims a real-looking Run id
            {"id": _LIVE, "sessionId": _LIVE, "foreign": True, "pid": 400, "attach": ""},
        ]

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                return types.SimpleNamespace(
                    returncode=0, stdout=f"{_RUN}\x1f%4\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        server._tmux = fake
        server.invalidate_runs()

    def tearDown(self):
        server.list_runs, server._tmux = self._saved
        server.invalidate_runs()

    def test_cached_runs_hides_it_from_everything_that_drives_a_run(self):
        self.assertEqual([r["id"] for r in server.cached_runs()], [_RUN])

    def test_cached_all_runs_still_sees_it(self):
        self.assertEqual([r["id"] for r in server.cached_all_runs()], [_RUN, _LIVE])

    def test_cached_foreign_runs_sees_only_it(self):
        # The Board's quiet section reads through here, so it can never pick up a
        # Managed Run and render it without its Respond / Attach / close.
        self.assertEqual([r["id"] for r in server.cached_foreign_runs()], [_LIVE])

    def test_close_refuses_it(self):
        self.assertFalse(server.close_run(_LIVE))
        self.assertEqual([c for c in self.calls if c[0] == "kill-window"], [])

    def test_respond_refuses_it(self):
        self.assertFalse(server.respond_run(_LIVE, "hello"))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

    def test_clear_refuses_it(self):
        self.assertFalse(server.clear_input(_LIVE))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

    def test_the_managed_run_beside_it_still_closes(self):
        self.assertTrue(server.close_run(_RUN))
        self.assertIn(("kill-window", "-t", "%4"), self.calls)


class TransferTests(unittest.TestCase):
    """**Transfer**: end the live **Foreign Run** on a **Session** and **resume**
    that Session as a **Managed Run** — one atomic operation (ADR 0012). Custody
    moves, no process does.

    The world here is derived from `self.alive` rather than fixed, because the
    order is the whole contract: the resume guard counts Foreign Runs, so a
    Transfer that resumed before the old pid left `ps` would be refused by its own
    guard — and only a walk that actually reflects the kill can catch that.

    `os.kill` is patched on the `os` module itself. There is no seam to stub
    instead, and inventing one would put a test-only indirection in front of the
    single irreversible line in the file.

    The graces are shrunk to milliseconds; the real ones are seconds, and the
    SIGKILL-escalation test has to sit through the SIGTERM one.
    """

    GRACES = {"_TRANSFER_TERM_GRACE": 0.05, "_TRANSFER_KILL_GRACE": 0.05,
              "_TRANSFER_POLL": 0.005}

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("list_runs", "launch_run", "_session_cwd", *self.GRACES)}
        self._kill = os.kill
        for name, val in self.GRACES.items():
            setattr(server, name, val)
        self.log = []          # every side effect, in order — the sequence IS the test
        self.alive = {400}     # pids `ps` and signal 0 can still see
        self.deaf = set()      # pids that ignore SIGTERM, to force the escalation
        self.immortal = set()  # pids that ignore everything, to exhaust both graces
        self.launch_fails = None
        self.alive_at_launch = None

        def fake_kill(pid, sig):
            self.log.append(("kill", pid, sig))
            if pid not in self.alive:
                raise ProcessLookupError(pid)
            if sig == 0 or pid in self.immortal:
                return
            if sig == signal.SIGTERM and pid in self.deaf:
                return
            self.alive.discard(pid)

        def fake_launch(workdir, prompt=None, task_id=None, resume_id=None):
            self.log.append(("launch", resume_id))
            self.alive_at_launch = set(self.alive)
            if self.launch_fails:
                raise self.launch_fails
            return _RUN2

        os.kill = fake_kill
        server.launch_run = fake_launch
        server.list_runs = self._world
        server._session_cwd = lambda sid, *a, **k: os.path.dirname(__file__)
        server.invalidate_runs()

    def tearDown(self):
        os.kill = self._kill
        for name, fn in self._saved.items():
            setattr(server, name, fn)
        server.invalidate_runs()

    def _world(self):
        """The live Runs, derived from `self.alive` so a walk actually reflects
        the kill. A Managed Run of ours sits beside the Foreign one throughout —
        Transfer must never touch it."""
        self.log.append(("walk",))
        rows = [{"id": _RUN, "sessionId": _GOOD, "attach": "tmux …"}]
        if 400 in self.alive:
            rows.append({"id": "", "attach": "", "foreign": True, "pid": 400,
                         "sessionId": _LIVE, "status": "busy"})
        return rows

    def _kinds(self):
        return [e[0] for e in self.log]

    def _acts(self):
        """The log stripped of the walks and the signal-0 liveness probes: just
        the two things that cannot be undone, in the order they happened."""
        return [e for e in self.log if e[0] == "launch" or (e[0] == "kill" and e[2])]

    def test_it_kills_the_run_then_resumes_the_session(self):
        self.assertEqual(server.transfer_session(_LIVE), _RUN2)
        self.assertEqual(self._acts(),
                         [("kill", 400, signal.SIGTERM), ("launch", _LIVE)])

    def test_the_resume_waits_for_the_exit(self):
        # Not politeness: `_live_session_ids` counts Foreign Runs, so resuming
        # before the old pid leaves `ps` would be refused by our own fork guard.
        self.deaf = {400}     # survives the SIGTERM, so the wait has to do work
        server.transfer_session(_LIVE)
        self.assertNotIn(400, self.alive_at_launch)

    def test_the_walk_is_refreshed_around_the_kill(self):
        # A memoized walk is up to _RUNS_TTL old: read before the kill it names a
        # pid that may already have exited (and been reused), read after it still
        # reports the Run we just ended and the resume never happens. So there
        # must be a fresh walk on both sides of the signal.
        server.transfer_session(_LIVE)
        kinds = self._kinds()
        self.assertEqual(kinds[0], "walk")
        self.assertIn("walk", kinds[kinds.index("kill"):kinds.index("launch")])

    def test_sigkill_escalates_after_the_sigterm_grace(self):
        self.deaf = {400}
        self.assertEqual(server.transfer_session(_LIVE), _RUN2)
        signals = [e[2] for e in self.log if e[0] == "kill" and e[2]]
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])

    def test_a_run_that_survives_sigkill_is_refused_with_nothing_resumed(self):
        self.immortal = {400}
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_LIVE)
        self.assertEqual(cm.exception.status, 500)
        # Not orphaned: the Run is still running, so the Session is not stranded.
        self.assertFalse(cm.exception.orphaned)
        self.assertIn("still has a live Run", str(cm.exception))
        self.assertNotIn("launch", self._kinds())

    def test_a_managed_runs_session_is_refused(self):
        # Closing one of ours is /api/close's job. Transfer must never become a
        # second way to kill a Run we own — nor to kill it *and* fork it.
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_GOOD)
        self.assertIn("already a Managed Run", str(cm.exception))
        self.assertEqual([e for e in self.log if e[0] != "walk"], [])

    def test_a_session_with_no_live_run_is_refused(self):
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_UNKNOWN)
        self.assertIn("no live Foreign Run", str(cm.exception))
        self.assertEqual([e for e in self.log if e[0] != "walk"], [])

    def test_a_bogus_session_id_is_refused_before_any_walk(self):
        for bad in ("", "not-a-uuid", "400", _LIVE + "x"):
            with self.subTest(bad=bad), self.assertRaises(server.TransferFailed) as cm:
                server.transfer_session(bad)
            self.assertIn("invalid session id", str(cm.exception))
        self.assertEqual(self.log, [])

    def test_a_dir_that_is_gone_is_refused_before_the_kill(self):
        # The dir check has to happen on this side of the irreversible step —
        # discovering it afterwards would strand the Session for a reason that
        # was visible all along.
        server._session_cwd = lambda sid, *a, **k: "/no/such/dir"
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_LIVE)
        self.assertIn("dir is gone", str(cm.exception))
        self.assertEqual([e for e in self.log if e[0] != "walk"], [])
        self.assertIn(400, self.alive)

    def test_a_resume_that_fails_after_the_kill_is_reported_as_orphaned(self):
        # The loud one. The Run is dead and nothing replaced it, and whoever
        # tapped is away from the Mac — so this can never read as a generic error.
        self.launch_fails = subprocess.CalledProcessError(1, "tmux")
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_LIVE)
        self.assertTrue(cm.exception.orphaned)
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("NOTHING IS RUNNING", str(cm.exception))
        self.assertIn(_LIVE, str(cm.exception))   # the Session, so it can be resumed by hand
        self.assertNotIn(400, self.alive)         # and it really is dead

    def test_a_second_transfer_of_the_same_session_finds_nothing_to_take(self):
        # What the _transfer_lock buys: a double-tap runs the second attempt
        # against the world the first one left, so it refuses instead of racing to
        # a second Managed Run on one transcript.
        server.transfer_session(_LIVE)
        with self.assertRaises(server.TransferFailed) as cm:
            server.transfer_session(_LIVE)
        self.assertIn("no live Foreign Run", str(cm.exception))
        self.assertEqual([e for e in self.log if e[0] == "launch"], [("launch", _LIVE)])

    def test_it_resumes_in_the_sessions_own_dir(self):
        seen = []
        server.launch_run = lambda workdir, *a, **k: (seen.append((workdir, k)), _RUN2)[1]
        server.transfer_session(_LIVE)
        self.assertEqual(seen, [(os.path.dirname(__file__), {"resume_id": _LIVE})])


class TransferApiTests(_HttpCase):
    """POST /api/transfer over the real Handler, end to end.

    The client sends a **Session** and never a pid: the pid is re-derived from
    the Launcher's own walk, which is the only thing standing between this and a
    kill-anything endpoint. Ungated, like launch / resume / close — the shared
    secret exists because **Respond** can approve tool calls (ADR 0007), and
    Transfer approves nothing.
    """

    @classmethod
    def setUpClass(cls):
        cls._saved = {n: getattr(server, n) for n in
                      ("list_runs", "launch_run", "_session_cwd", "TOKEN",
                       *TransferTests.GRACES)}
        cls._kill = os.kill
        for name, val in TransferTests.GRACES.items():
            setattr(server, name, val)
        # A token IS configured here, so "no token in the body" proves the
        # endpoint is ungated rather than that gating was never reachable.
        server.TOKEN = "a-shared-secret"
        cls.alive = {400}
        cls.launched = []
        cls.launch_fails = None

        def fake_kill(pid, sig):
            if pid not in cls.alive:
                raise ProcessLookupError(pid)
            if sig:
                cls.alive.discard(pid)

        def fake_launch(workdir, prompt=None, task_id=None, resume_id=None):
            cls.launched.append(resume_id)
            if cls.launch_fails:
                raise cls.launch_fails
            return _RUN2

        os.kill = fake_kill
        server.launch_run = fake_launch
        server.list_runs = lambda: (
            [{"id": _RUN, "sessionId": _GOOD, "attach": "tmux …"}] +
            [{"id": "", "attach": "", "foreign": True, "pid": pid,
              "sessionId": _LIVE if pid == 400 else _GONE, "status": "busy"}
             for pid in sorted(cls.alive)])
        server._session_cwd = lambda sid, *a, **k: os.path.dirname(__file__)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        os.kill = cls._kill
        for name, val in cls._saved.items():
            setattr(server, name, val)
        server.invalidate_runs()

    def setUp(self):
        type(self).alive = {400}
        type(self).launched = []
        type(self).launch_fails = None
        server.invalidate_runs()

    def test_it_transfers_and_hands_back_the_new_run_id(self):
        status, body = self._post("/api/transfer", {"sessionId": _LIVE})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["runId"], _RUN2)      # the client's optimistic row
        self.assertIn("transferred", body["message"])
        self.assertEqual(self.launched, [_LIVE])    # same Session, new Run
        self.assertNotIn(400, self.alive)

    def test_it_is_ungated_even_with_a_token_configured(self):
        self.assertTrue(server.TOKEN)
        status, _ = self._post("/api/transfer", {"sessionId": _LIVE})
        self.assertEqual(status, 200)

    def test_a_pid_in_the_body_is_ignored(self):
        # The server re-derives the pid from the walk. A pid in the body must be
        # inert — otherwise this endpoint kills whatever it is handed.
        type(self).alive = {400, 999}
        status, _ = self._post("/api/transfer", {"sessionId": _LIVE, "pid": 999})
        self.assertEqual(status, 200)
        self.assertEqual(self.alive, {999})   # 400 died, the named pid did not

    def test_a_body_carrying_only_a_pid_is_refused(self):
        status, body = self._post("/api/transfer", {"pid": 400})
        self.assertEqual(status, 400)
        self.assertIn("invalid session id", body["message"])
        self.assertIn(400, self.alive)
        self.assertEqual(self.launched, [])

    def test_a_managed_runs_session_is_refused(self):
        status, body = self._post("/api/transfer", {"sessionId": _GOOD})
        self.assertEqual(status, 400)
        self.assertIn("already a Managed Run", body["message"])
        self.assertEqual(self.launched, [])
        self.assertIn(400, self.alive)   # and the Foreign Run beside it is untouched

    def test_a_session_with_no_live_run_is_refused(self):
        status, body = self._post("/api/transfer", {"sessionId": _UNKNOWN})
        self.assertEqual(status, 400)
        self.assertIn("no live Foreign Run", body["message"])
        self.assertIn(400, self.alive)

    def test_a_resume_that_fails_after_the_kill_reports_it_distinctly(self):
        type(self).launch_fails = subprocess.CalledProcessError(1, "tmux")
        status, body = self._post("/api/transfer", {"sessionId": _LIVE})
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        # `orphaned` is the field the client keys its loud path on — a Session
        # with nothing running, and nobody at the Mac to see it.
        self.assertTrue(body["orphaned"])
        self.assertIn("NOTHING IS RUNNING", body["message"])
        self.assertIn(_LIVE, body["message"])

    def test_an_ordinary_refusal_is_not_flagged_as_orphaned(self):
        _, body = self._post("/api/transfer", {"sessionId": _UNKNOWN})
        self.assertFalse(body["orphaned"])

    def test_get_is_405_like_every_other_post_endpoint(self):
        status, _, headers = self._raw("GET", "/api/transfer")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")

    def test_cross_origin_is_blocked(self):
        status, _ = self._post("/api/transfer", {"sessionId": _LIVE}, origin=False)
        self.assertEqual(status, 403)
        self.assertIn(400, self.alive)


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


# REGRESSION fixture, kept deliberately. This is the iTerm-era renderer; the
# tmux captures in tests/fixtures/ are the current one. It is the renderer that
# changed under us with no test noticing, so both shapes stay covered.
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

    def test_widget_is_recognised_by_its_checkbox_header(self):
        self.assertTrue(server._is_question_widget(_ASK_PANE))
        self.assertFalse(server._is_question_widget(_PERMISSION_PANE))
        self.assertFalse(server._is_question_widget(_INPUT_PANE))

    def test_pane_question_reads_the_prompt_not_the_header(self):
        self.assertEqual(
            server._pane_question(_ASK_PANE),
            "When new Blocked work arrives while you're holding a card, how much should it signal?")

    def test_input_box_reads_a_real_draft(self):
        self.assertEqual(server._pane_input(_INPUT_PANE), "draft a reply but do not send")


class AskWidgetPaneTests(unittest.TestCase):
    """`_pane_widget` read against the two verbatim tmux captures — the current
    renderer, where each option's description sits on the lines BELOW its label
    rather than in a side panel beside it (ADR 0020).

    Both shapes are here on purpose: `ask_multi` carries a tab strip, `ask_single`
    a bare checkbox header, and 326 of the 425 asks on disk are single-question,
    so the bare header is the common case and not a fallback.
    """

    def setUp(self):
        self.multi = _capture("ask_multi.pane")
        self.single = _capture("ask_single.pane")
        self.toggled = _capture("ask_toggled.pane")

    def test_both_captures_are_recognised_as_the_widget(self):
        # The old `"add notes"` signature returned False for BOTH — i.e. for
        # every AskUserQuestion this renderer paints.
        self.assertTrue(server._is_question_widget(self.multi))
        self.assertTrue(server._is_question_widget(self.single))
        self.assertFalse(server._is_question_widget(_PERMISSION_PANE))
        self.assertFalse(server._is_question_widget(_INPUT_PANE))

    def test_the_tools_options_exclude_the_widgets_own_rows(self):
        self.assertEqual(server._pane_widget(self.multi)["options"],
                         ["Keep split (Recommended)", "Merge into one"])

    def test_the_affordance_rows_are_kept_and_marked_as_such(self):
        # Not dropped: free text routes through `Type something.`, so a later
        # slice has to be able to find which row it is.
        rows = server._pane_widget(self.multi)["rows"]
        self.assertEqual([r["label"] for r in rows],
                         ["Keep split (Recommended)", "Merge into one",
                          "Type something.", "Chat about this"])
        self.assertEqual([r["affordance"] for r in rows],
                         [False, False, True, True])

    def test_assistant_prose_above_the_header_is_out_of_scope(self):
        # The captured frame holds a four-item numbered ticket list Claude WROTE,
        # indistinguishable from menu rows by shape. Anchoring on the checkbox
        # header is what excludes it.
        labels = [r["label"] for r in server._pane_widget(self.multi)["rows"]]
        for prose in ("Range moves to the exercise, keyed by side",
                      "Drop the plan-item range and stop training-prep sending it",
                      "Detect an unreachable range",
                      "Propose and accept the retune on the exercise card"):
            self.assertNotIn(prose, labels)

    def test_a_single_question_widget_has_no_tab_strip(self):
        w = server._pane_widget(self.single)
        self.assertEqual(w["tabs"], [])
        self.assertEqual(w["header"], "multiSelect")
        self.assertEqual(w["options"], [
            "One tap = one toggle, then a separate done (Recommended)",
            "Collect locally, send one sequence",
            "Send multiSelect to free text"])

    def test_the_tab_strip_yields_the_questions_but_not_submit(self):
        # `✔ Submit` shares the strip with the question tabs; counted as a tab it
        # would make a two-Ask Set look like three.
        self.assertEqual(server._pane_widget(self.multi)["tabs"],
                         [{"label": "Granularity", "checked": False},
                          {"label": "Expand/contract", "checked": False}])

    def test_the_cursor_is_read_and_not_defaulted(self):
        # It sits on the first option in both captures, which a hardcoded 0 would
        # also produce — so move it, and check the reading follows.
        self.assertEqual(server._pane_widget(self.multi)["cursor"], 0)
        moved = (self.multi.replace("❯ 1. Keep split", "  1. Keep split")
                           .replace("  2. Merge into one", "❯ 2. Merge into one"))
        self.assertEqual(server._pane_widget(moved)["cursor"], 1)
        onto_affordance = (self.single.replace("❯ 1. One tap", "  1. One tap")
                                      .replace("  4. Type something.",
                                               "❯ 4. Type something."))
        self.assertEqual(server._pane_widget(onto_affordance)["cursor"], 3)

    def test_the_cursor_indexes_rows_so_it_counts_keystrokes(self):
        # Cursor and rows share one index space: stepping is over every row the
        # widget paints, affordances included. Indexing `options` instead is the
        # arithmetic that answered 'Chat about this' to a question nobody asked.
        w = server._pane_widget(self.multi)
        self.assertEqual(len(w["rows"]), 4)
        self.assertLess(w["cursor"], len(w["rows"]))

    def test_a_numbered_line_in_the_question_body_does_not_shift_the_rows(self):
        # The anchor puts prose ABOVE the header out of scope; the question body
        # sits BELOW it and is assistant-authored too. Read top-down, a stray
        # `1.` there claims row 1 and every real row shifts by one — ADR 0020's
        # wrong-answer table, one line lower down the frame.
        salted = self.multi.replace("into one tracer bullet?",
                                    "into one tracer bullet?\n1. maybe this")
        w = server._pane_widget(salted)
        self.assertEqual(w["options"], ["Keep split (Recommended)", "Merge into one"])
        self.assertEqual(len(w["rows"]), 4)

    def test_a_tick_of_prose_below_the_widget_does_not_hide_it(self):
        # `✓ done` is a checkbox header by shape. Anchoring on the last header
        # outright hands it the anchor, the widget goes unseen, and the false ⚠
        # this slice closes comes straight back.
        trailing = self.multi + "\n✓ done\n"
        self.assertTrue(server._is_question_widget(trailing))
        self.assertEqual(server._pane_widget(trailing)["options"],
                         ["Keep split (Recommended)", "Merge into one"])

    def test_a_tab_strip_without_its_left_arrow_is_still_a_tab_strip(self):
        # The `←` is absent on the first tab and in a narrow pane. Discriminating
        # on it alone reads the strip as a bare header: two Asks become none.
        narrow = self.multi.replace("←  ☐ Granularity", "☐ Granularity")
        w = server._pane_widget(narrow)
        self.assertEqual([t["label"] for t in w["tabs"]],
                         ["Granularity", "Expand/contract"])
        self.assertEqual(w["header"], "")

    def test_a_permission_menu_is_not_a_widget_and_still_parses(self):
        # No checkbox header at all, so the widget reader declines it; the
        # contiguity-based `_parse_selector` keeps serving that lane.
        self.assertEqual(server._pane_widget(_PERMISSION_PANE), {})
        self.assertEqual(server._parse_selector(_PERMISSION_PANE)["options"],
                         ["Yes", "Yes, and don't ask again",
                          "No, and tell Claude what to do differently"])

    def test_a_checked_off_line_of_prose_is_not_a_widget(self):
        # Assistant output ticking things off is a checkbox header by shape. With
        # no numbered rows under it there is nothing to drive, and saying yes
        # would suppress the unsent-text read on a Run that is not Blocked.
        self.assertEqual(server._pane_widget(
            "✓ ran the tests\n✓ committed\n\nAnything else?\n"), {})

    def test_an_answered_tab_is_still_a_widget(self):
        # `☒` is how the strip renders a question you have ALREADY answered, so it
        # is the state of every frame from Ask 2 of a Set onward. Absent from the
        # glyph vocabulary, `_HEADER_RE` misses, the widget goes undetected, and
        # the false ⚠ unsent-text warning is painted over the second Ask of every
        # Set — the exact bug the checkbox anchor was introduced to kill.
        self.assertTrue(server._is_question_widget(self.toggled))
        self.assertEqual(server._pane_widget(self.toggled)["tabs"],
                         [{"label": "Fixture", "checked": True}])

    def test_a_multiselect_rows_toggle_box_is_state_not_label(self):
        # The renderer paints the box INSIDE the label — `1. [✔] Row one`. Left
        # there it is not merely cosmetic: the structured label is `Row one`, so
        # the Ask Set's prefix cross-check fails and all 30 multiSelect asks on
        # disk go untappable.
        rows = server._pane_widget(self.toggled)["rows"]
        self.assertEqual([r["label"] for r in rows][:3],
                         ["Row one", "Row two", "Row three"])
        self.assertEqual([r["checked"] for r in rows][:3], [True, True, False])

    def test_a_single_select_row_has_no_toggle_state_rather_than_a_false_one(self):
        # None, not False. "Unticked" is a claim about a box the renderer never
        # painted, and a client that renders it draws checkboxes on a radio group.
        self.assertEqual([r["checked"] for r in server._pane_widget(self.multi)["rows"]],
                         [None, None, None, None])

    def test_the_free_text_row_is_an_affordance_without_its_full_stop(self):
        # A multiSelect frame renders it `4. [ ] Type something` — no period.
        # Read as an option it takes a seat, and every row below it answers the
        # question one line off.
        rows = server._pane_widget(self.toggled)["rows"]
        self.assertEqual([r["affordance"] for r in rows], [False, False, False, True, True])
        self.assertEqual(server._pane_widget(self.toggled)["options"],
                         ["Row one", "Row two", "Row three"])

    def test_an_unread_cursor_is_none_and_never_a_defaulted_zero(self):
        # THE archetype this slice generalises. A defaulted 0 is
        # indistinguishable from "the cursor is genuinely on row 0", so a parse
        # failure looked exactly like a reading and drove keystrokes for months
        # (ADR 0020). None forces every caller to decide, and `_ask_set` refuses.
        unmarked = self.multi.replace("❯ 1. Keep split", "  1. Keep split")
        self.assertIsNone(server._pane_widget(unmarked)["cursor"])
        self.assertEqual(server._pane_widget(unmarked)["options"],
                         ["Keep split (Recommended)", "Merge into one"])

    def test_a_row_whose_label_reads_as_empty_voids_the_whole_widget(self):
        # `startswith("")` is True for every option, so ONE blank label makes the
        # Ask Set's cross-check vacuous and hands back a tappable Ask whose rows
        # were never matched — a parse failure producing an action, which is the
        # rule this slice enforces. Refuse the widget instead.
        blanked = self.multi.replace("  2. Merge into one", "  2. ─────")
        self.assertEqual(server._pane_widget(blanked), {})

    def test_a_selector_with_no_highlight_reports_no_cursor(self):
        # Same rule on the permission lane, which `_parse_selector` still serves.
        unmarked = _PERMISSION_PANE.replace("❯ 1. Yes", "  1. Yes")
        sel = server._parse_selector(unmarked)
        self.assertEqual(len(sel["options"]), 3)
        self.assertIsNone(sel["cursor"])

    def test_the_prompt_is_read_off_a_tab_strip_frame(self):
        # `_pane_question` shares the anchor, so the multi shape reaches it too.
        self.assertTrue(server._pane_question(self.multi).startswith(
            "Ticket 3 (detector) is verifiable"))
        self.assertTrue(server._pane_question(self.single).startswith(
            "30 of the 425 asks on disk"))


def _fixture_rows(name: str) -> list:
    """The transcript tail of a fixture, parsed — the pair of its `.pane`.

    Verbatim rows off a real Session, like the captures beside them: the whole
    point of ADR 0020 is that the transcript and the pane are read TOGETHER, and
    a hand-written tool_use would agree with a hand-written pane by construction.
    """
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _questions(rows: list) -> list:
    return server._pending_tool_use(rows)["input"]["questions"]


def _capture_tool():
    """`tools/capture-widget.py` as a module — imported, not reimplemented.

    The matrix checks a property the script is responsible for (that a fixture's
    `-p` and `-e` frames are the same paint), and a second copy of "the same
    frame" would drift from the one that writes the files. Hyphenated filename,
    hence the loader."""
    import importlib.util
    path = os.path.join(os.path.dirname(_FIXTURES), "..", "tools", "capture-widget.py")
    spec = importlib.util.spec_from_file_location("capture_widget", os.path.normpath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CaptureWriteTests(unittest.TestCase):
    """What `tools/capture-widget.py` puts on disk. ADR 0021 makes a capture the
    unit of evidence and the fixture matrix the thing that reads it, so the two
    have to agree about what "this capture has no transcript tail" looks like."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.base = os.path.join(self.dir, "probe")

    def test_a_capture_with_no_transcript_tail_writes_no_jsonl_at_all(self):
        # An EMPTY `.jsonl` is worse than a missing one and the tool said so
        # wrongly: it warned that "the fixture matrix will hold it to the pane
        # invariants only", but the matrix skips on the file's EXISTENCE, and an
        # empty file exists. The capture went into the reconcile test instead and
        # died there on a `TypeError` inside someone else's assertion. Loud is
        # right; wrong-and-confusing is not.
        _capture_tool()._write_capture(self.base, "frame\n", "\x1b[1mframe\n", [])
        self.assertTrue(os.path.exists(self.base + ".pane"))
        self.assertTrue(os.path.exists(self.base + ".ansi"))
        self.assertFalse(os.path.exists(self.base + ".jsonl"))

    def test_a_capture_with_a_tail_writes_it_verbatim(self):
        rows = ['{"type":"user"}', '{"type":"assistant"}']
        _capture_tool()._write_capture(self.base, "frame\n", "\x1b[1mframe\n", rows)
        with open(self.base + ".jsonl", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "\n".join(rows) + "\n")

    def test_no_fixture_on_disk_carries_an_empty_transcript_tail(self):
        # The other half of the same agreement, held against the real directory:
        # a `.jsonl` that exists but is empty is exactly the state the matrix
        # cannot tell from a real tail.
        for name in os.listdir(_FIXTURES):
            if name.endswith(".jsonl"):
                self.assertTrue(os.path.getsize(os.path.join(_FIXTURES, name)),
                                f"{name} is empty — it should not have been written")


class RendererVocabularyTests(unittest.TestCase):
    """ADR 0021's central promise: when the TUI moves, the RENDERER VOCABULARY
    block is the ONE place to edit. That promise is only real if every literal
    the renderer paints is read from it at call time — a hardcoded copy in a
    branch means the documented re-fit procedure ("capture, edit the constants,
    run the matrix") silently does not work, which is the failure class the
    block exists to end.

    Driven by re-fitting the constants and re-reading the SAME capture, because
    that is the procedure itself. A frame renamed only in the fixture would test
    nothing about where the literal lives.
    """

    def setUp(self):
        self.pane = _capture("ask_multi.pane")
        self.strip = next(ln for ln in self.pane.split("\n") if "Submit" in ln)
        self.assertIn("←", self.strip)   # the shape these constants describe

    def _refit(self, pane, **names):
        for name, new in names.items():
            self.addCleanup(setattr, server, name, getattr(server, name))
            setattr(server, name, new)
        return server._pane_widget(pane)

    def test_the_submit_control_is_told_apart_by_a_named_constant(self):
        # `Submit` is the same species as `"add notes"` — an affordance LABEL the
        # renderer paints, which ADR 0020 caught version-pinned in a branch. It
        # sits in the tab strip beside the question tabs and is not a question:
        # unnamed, a renamed control becomes a third Ask in a two-Ask Set.
        refitted = self.pane.replace("✔ Submit", "✔ Send")
        w = self._refit(refitted, _SUBMIT_LABEL="Send")
        self.assertEqual([t["label"] for t in w["tabs"]],
                         ["Granularity", "Expand/contract"])

    def test_the_pending_tab_glyph_is_read_from_the_vocabulary(self):
        # `☐` was the SOLE definition of `checked` for a TAB, hardcoded twice in
        # this branch while `_CHECKBOX` sat in the block a screen away. A
        # renderer that swapped the empty box would report every pending
        # question as already answered — and `☒` (ADR 0020's third bug) is the
        # proof this glyph set moves.
        refitted = (self.pane.replace("☐ Granularity", "✓ Granularity")
                             .replace("☐ Expand/contract", "✓ Expand/contract"))
        w = self._refit(refitted, _TAB_PENDING="✓")
        self.assertEqual([t["checked"] for t in w["tabs"]], [False, False])

    def test_the_tab_strip_arrow_is_read_from_the_vocabulary(self):
        # The right arrow is half of "tab strip or bare header?" — the half that
        # carries a strip the leading `←` is absent from (the first tab, or a
        # narrow pane). Read such a strip as a bare header and a two-Ask Set
        # becomes no Ask Set at all, which is the failure the `←`-only rule was
        # already written against.
        one_tab = self.pane.replace(self.strip, "☐ Granularity  →")
        self.assertTrue(server._pane_widget(one_tab)["tabs"], "the probe is not a strip")
        refitted = self.pane.replace(self.strip, "☐ Granularity  »")
        self.assertFalse(server._pane_widget(refitted)["tabs"])   # unfitted: read as a header
        w = self._refit(refitted, _TAB_RIGHT="»")
        self.assertEqual([t["label"] for t in w["tabs"]], ["Granularity"])
        self.assertEqual(w["header"], "")


class PaneFixtureMatrixTests(unittest.TestCase):
    """EVERY capture in tests/fixtures/ through EVERY pane parser, held to the
    invariants that must hold for ANY frame of the AskUserQuestion widget.

    This is the test that would have caught `☒` before a human stumbled on it.
    ADR 0020's four version-pinned assumptions were each found by accident,
    months apart, because coverage was written one capture at a time against the
    property that capture was added for — so a NEW capture proved a new property
    and re-proved none of the old ones. Here the fixtures are globbed: adding a
    capture (`tools/capture-widget.py <name> --pane %N`) extends the matrix by
    existing, with no test to remember to write. That is the whole design.

    The naming convention is load-bearing: `ask_*.pane` is a capture of the
    widget and MUST parse as one; anything else must NOT be read as a widget, so
    a captured permission menu or plain input box tightens the discriminator
    rather than merely sitting there.
    """

    @classmethod
    def setUpClass(cls):
        cls.names = sorted(n[:-5] for n in os.listdir(_FIXTURES) if n.endswith(".pane"))

    def test_the_matrix_actually_has_captures_to_run(self):
        # A glob that silently matches nothing passes every test below it — the
        # same shape of silent success this whole slice is about. Named, not
        # counted: each of these covers a shape the others do not (multi-question
        # tab strip, bare header, mid-answer multiSelect, and no widget at all),
        # so losing one must fail loudly rather than shrink the matrix quietly.
        for required in ("ask_multi", "ask_single", "ask_toggled", "idle_box"):
            self.assertIn(required, self.names)

    def test_every_capture_is_read_as_the_shape_its_name_claims(self):
        for name in self.names:
            with self.subTest(fixture=name):
                pr = server._read_pane(_capture(name + ".pane"))
                self.assertTrue(pr.captured)
                widget = bool(pr.widget)
                self.assertEqual(widget, name.startswith("ask_"),
                                 f"{name}: widget detection disagrees with the name")
                self.assertEqual(server._is_question_widget(_capture(name + ".pane")), widget)

    def test_no_widget_capture_produces_a_false_unsent_text_warning(self):
        # THE original bug: the widget's body sits between the same horizontal
        # rules the input box does, so an undetected widget is read as unsent
        # text and the phone paints ⚠ over the question — beside a button that
        # would fire hundreds of BSpace into a live selector (ADR 0020).
        #
        # Asserted in two halves so it cannot pass by construction: the RAW
        # reader must still be fooled (that is the hazard, and if it stops being
        # true the suppression is proving nothing), and `_read_pane` must
        # suppress it (that is the fix).
        for name in (n for n in self.names if n.startswith("ask_")):
            with self.subTest(fixture=name):
                pane = _capture(name + ".pane")
                self.assertTrue(server._pane_input(pane),
                                "the raw reader is no longer fooled — this test "
                                "has stopped testing the suppression")
                self.assertEqual(server._read_pane(pane).unsent, "")

    def test_a_frame_with_no_widget_still_reads_its_input_box(self):
        # The other direction, and the one that needs a NEGATIVE capture: the
        # suppression above must not become "the unsent-text read is off". A
        # `_HEADER_RE` that drifted loose would swallow ordinary frames and take
        # the real ⚠ with it — silently, since nothing on screen would say so.
        for name in (n for n in self.names if not n.startswith("ask_")):
            with self.subTest(fixture=name):
                pane = _capture(name + ".pane")
                pr = server._read_pane(pane)
                self.assertEqual(pr.widget, {})
                self.assertEqual(pr.unsent, server._pane_input(pane))

    def test_every_widget_capture_yields_rows_a_cursor_and_a_prompt(self):
        for name in (n for n in self.names if n.startswith("ask_")):
            with self.subTest(fixture=name):
                pane = _capture(name + ".pane")
                w = server._pane_widget(pane)
                rows = w["rows"]
                self.assertTrue(rows, "no menu rows found")
                # In range and READ, not defaulted. `cursor` is what every
                # keystroke count is measured from, so an unread one is not a
                # small gap — it is ADR 0020's wrong-answer table.
                self.assertIsNotNone(w["cursor"], "cursor was not read off the frame")
                self.assertIn(w["cursor"], range(len(rows)))
                # Some row must be the tool's own option, or there is nothing to
                # answer with; and every row must carry a label to answer by.
                self.assertTrue(w["options"])
                self.assertTrue(all(r["label"] for r in rows))
                # A toggle marker left on a label fails the Ask Set's prefix
                # cross-check and makes every multiSelect ask untappable.
                self.assertFalse([r for r in rows if r["label"][:1] == "["])
                # Exactly one of the two header shapes: a tab strip (multi) or a
                # bare checkbox header (single). Both, or neither, means the
                # anchor read something that is not a widget header.
                self.assertEqual(bool(w["tabs"]) + bool(w["header"]), 1)
                self.assertTrue(server._pane_question(pane), "no prompt read")

    def test_every_widget_capture_reconciles_with_its_own_transcript(self):
        # The pane and the transcript are read TOGETHER (ADR 0020), so the pair
        # is the unit under test: a capture whose rows no longer account for the
        # options its own tool_use sent is a renderer change, and it must fail
        # here rather than degrade to an untappable card nobody investigates.
        for name in (n for n in self.names if n.startswith("ask_")):
            with self.subTest(fixture=name):
                # Non-empty CONTENT, not mere existence: an empty `.jsonl`
                # exists, so an existence test walked such a capture into the
                # reconcile below and it died on a `TypeError` in an assertion
                # about something else. `tools/capture-widget.py` no longer
                # writes the empty file; this is the other half of that
                # agreement, so neither side can drift alone.
                tail = os.path.join(_FIXTURES, name + ".jsonl")
                if not (os.path.exists(tail) and os.path.getsize(tail)):
                    self.skipTest("capture has no transcript tail")
                pane = _capture(name + ".pane")
                tu = server._pending_tool_use(_fixture_rows(name + ".jsonl"))
                self.assertEqual(tu["name"], "AskUserQuestion")
                aset = server._ask_set(tu, server._read_pane(pane))
                self.assertEqual(aset["fallback"], "")
                self.assertTrue(aset["tappable"])
                rows = server._pane_widget(pane)["rows"]
                cursor = server._pane_widget(pane)["cursor"]
                for o in aset["options"]:
                    landed = rows[cursor + o["steps"]]
                    self.assertFalse(landed["affordance"],
                                     f"{o['label']!r} steps onto {landed['label']!r}")
                    self.assertTrue(o["label"].startswith(landed["label"]))

    def test_every_capture_routes_free_text_somewhere_it_can_land(self):
        # The seventh defect, in the matrix so a renderer change breaks it here
        # rather than in someone's afternoon: text typed straight at a widget was
        # measured to vanish, and the Enter after it answered with the highlighted
        # row (ADR 0020). Every widget capture must therefore route through the
        # widget's OWN free-text row, and every non-widget capture must keep the
        # ordinary path.
        for name in self.names:
            with self.subTest(fixture=name):
                pane = _capture(name + ".pane")
                pr = server._read_pane(pane)
                route = server._text_route(pr)
                if not name.startswith("ask_"):
                    self.assertEqual(route.route, "plain")
                    continue
                self.assertEqual(route.route, "affordance",
                                 f"{name}: free text has no route but Esc, which cancels")
                w = server._pane_widget(pane)
                landed = w["rows"][w["cursor"] + route.steps]
                # It lands on the free-text row itself — not on `Chat about this`
                # one seat further down, and not on an option, which would answer
                # a question nobody chose.
                self.assertTrue(landed["affordance"])
                self.assertEqual(landed["label"].rstrip("."), server._FREE_TEXT_ROW)

    def test_every_capture_keeps_its_attributed_twin(self):
        # `-p` drops the ANSI attributes, and the current question TAB is marked
        # by nothing else — ADR 0020's escape hatch (anchor on the highlight
        # attribute if the checkbox header ever breaks) needs the `-e` frame, and
        # it cannot be reconstructed later: the widget will have moved on.
        #
        # The two are separate `capture-pane` calls, so they CAN be different
        # frames — a spinner tick between them is enough. Checked here rather
        # than assumed, with the capture script's own definition of "the same
        # frame" so there is one of it: a twin from another moment is not a twin,
        # and it would be trusted precisely when `.pane` had stopped parsing.
        for name in self.names:
            with self.subTest(fixture=name):
                ansi = os.path.join(_FIXTURES, name + ".ansi")
                self.assertTrue(os.path.exists(ansi), f"{name} has no -e capture")
                self.assertIn("\x1b[", _capture(name + ".ansi"),
                              f"{name}.ansi carries no attributes — was it taken with -e?")
                self.assertTrue(
                    _capture_tool()._same_frame(_capture(name + ".pane"),
                                                _capture(name + ".ansi")),
                    f"{name}: the -p and -e captures are different frames")

    def test_every_capture_is_version_stamped_in_the_readme(self):
        # "Which renderer is this?" must be answerable by reading, not by
        # archaeology. tmux names the window after the running Claude Code
        # version, so `tools/capture-widget.py` records it for free at capture
        # time — this holds the record to existing.
        with open(os.path.join(_FIXTURES, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for name in self.names:
            with self.subTest(fixture=name):
                head = readme.find(f"## `{name}.*`")
                self.assertNotEqual(head, -1, f"{name} is undocumented")
                end = readme.find("\n## ", head + 1)
                section = readme[head:end if end != -1 else len(readme)]
                self.assertTrue(re.search(r"Claude Code \d+\.\d+\.\d+", section),
                                f"{name} carries no Claude Code version")


class TextRouteTests(unittest.TestCase):
    """`_text_route` — where free text can land, decided from one read of the
    screen (ADR 0020's seventh and worst defect).

    The defect it closes was measured, not argued: an external driver replicating
    `respond_run`'s two calls against a live widget left the frame BYTE-IDENTICAL
    across eleven typed characters, and the Enter after them returned an option
    nobody chose. So "type it and press Enter" is not a fallback — it is the bug,
    and every branch here either lands the text somewhere real or refuses."""

    def test_a_plain_input_box_keeps_the_ordinary_path(self):
        r = server._text_route(server._read_pane(_INPUT_PANE))
        self.assertEqual((r.route, r.steps, r.reason), ("plain", None, ""))

    def test_a_widget_routes_through_its_own_free_text_row(self):
        r = server._text_route(server._read_pane(_capture("ask_multi.pane")))
        # Row 2 of four, cursor on row 0 — the count ADR 0020's own table
        # measured as landing on `Type something.`
        self.assertEqual((r.route, r.steps, r.reason), ("affordance", 2, ""))

    def test_a_missing_row_is_named_no_row_and_falls_to_esc(self):
        pane = _capture("ask_multi.pane").replace("Type something.", "Ponder this.")
        r = server._text_route(server._read_pane(pane))
        self.assertEqual((r.route, r.steps, r.reason), ("esc", None, "no-row"))

    def test_an_unread_cursor_is_named_no_cursor_and_never_counted_from_zero(self):
        pane = _capture("ask_multi.pane").replace("❯ 1.", "  1.")
        r = server._text_route(server._read_pane(pane))
        self.assertEqual((r.route, r.steps, r.reason), ("esc", None, "no-cursor"))
        self.assertIsNone(r.steps)   # NOT 2 counted from a defaulted cursor of 0

    def test_an_uncaptured_pane_refuses_outright(self):
        r = server._text_route(server._read_pane(""))
        self.assertEqual((r.route, r.steps, r.reason), ("refuse", None, "no-pane"))
        # "nobody looked" is a different answer from "we looked and saw a box",
        # and conflating them is what put a blind send-keys at an unread screen.
        self.assertNotEqual(r.route, "plain")

    def test_it_takes_a_pane_read_and_a_raw_string_raises(self):
        # Same discipline as `_ask_set`: an argument that can be got wrong
        # silently is the same defect as a defaulted cursor (ADR 0021).
        with self.assertRaises(TypeError):
            server._text_route(_capture("ask_multi.pane"))
        with self.assertRaises(TypeError):
            server._text_route(server._pane_widget(_capture("ask_multi.pane")))


def _retarget(pane: str, question: str, labels: list) -> str:
    """The captured frame as it would render a DIFFERENT question of the same
    Set: the question body below the anchor swapped, the option rows relabelled.

    Derived from the capture rather than typed out, so the framing, the rules and
    the affordance rows stay exactly as tmux painted them — only the two things
    that change when the widget advances a tab do change."""
    lines = pane.split("\n")
    hdr = server._widget_anchor(lines)
    first = next(i for i in range(hdr + 1, len(lines)) if server._OPT_RE.match(lines[i]))
    out = lines[:hdr + 1] + ["", question, ""] + lines[first:]
    n, seen = hdr + 3, 0
    for i in range(n, len(out)):
        m = server._OPT_RE.match(out[i])
        if m and seen < len(labels):
            out[i] = out[i][:m.start(3)] + labels[seen]
            seen += 1
    return "\n".join(out)


class AskSetTests(unittest.TestCase):
    """The **Ask Set** — `_ask_set`, driven off each fixture's transcript tail
    and its pane TOGETHER, because that pairing is ADR 0020's whole claim: the
    transcript says what is asked, the pane says where the widget stands.

    The bug on trial: `_ask_of` returned every question's options concatenated,
    so the phone drew q1's text above four buttons, two of them q2's — and
    stepping the cursor by a button's index landed on the widget's own
    affordances and answered a question nobody had been shown.
    """

    def setUp(self):
        self.multi_pane, self.multi_rows = _capture("ask_multi.pane"), _fixture_rows("ask_multi.jsonl")
        self.single_pane, self.single_rows = _capture("ask_single.pane"), _fixture_rows("ask_single.jsonl")
        self.toggled_pane, self.toggled_rows = _capture("ask_toggled.pane"), _fixture_rows("ask_toggled.jsonl")

    def _set(self, pane, rows):
        return server._ask_set(server._pending_tool_use(rows), server._read_pane(pane))

    def _assert_steps_land_on_their_own_row(self, pane, aset):
        """THE regression. Resolve each option's keystroke count against the rows
        the cursor actually steps through and check where it lands — measured, not
        asserted from the same arithmetic that produced it."""
        w = server._pane_widget(pane)
        rows = w["rows"]
        for o in aset["options"]:
            landed = rows[w["cursor"] + o["steps"]]
            self.assertEqual(landed["label"], rows[o["row"]]["label"])   # row agrees with steps
            self.assertFalse(landed["affordance"],
                             f"{o['label']!r} steps onto the widget's own {landed['label']!r}")
            # Prefix, not equality: the pane truncates at terminal width.
            self.assertTrue(o["label"].startswith(landed["label"]),
                            f"{o['label']!r} lands on {landed['label']!r}")

    def test_only_the_current_asks_options_reach_the_payload(self):
        aset = self._set(self.multi_pane, self.multi_rows)
        q1 = _questions(self.multi_rows)[0]
        self.assertEqual((aset["index"], aset["count"]), (0, 2))     # Ask 1 of 2
        self.assertEqual(aset["header"], "Granularity")
        self.assertFalse(aset["multiSelect"])
        self.assertEqual([o["label"] for o in aset["options"]],
                         [o["label"] for o in q1["options"]])
        # q2's options are the ones that used to ride along and mis-answer.
        for o in _questions(self.multi_rows)[1]["options"]:
            self.assertNotIn(o["label"], [x["label"] for x in aset["options"]])

    def test_each_option_carries_the_description_that_decides_it(self):
        # Median 175 chars, p90 285 (ADR 0020's census), all of it discarded
        # before this. The label alone frequently cannot decide the question.
        aset = self._set(self.multi_pane, self.multi_rows)
        self.assertEqual([o["description"] for o in aset["options"]],
                         [o["description"] for o in _questions(self.multi_rows)[0]["options"]])
        self.assertTrue(all(len(o["description"]) > 100 for o in aset["options"]))

    def test_every_option_steps_to_its_own_row(self):
        aset = self._set(self.multi_pane, self.multi_rows)
        self.assertTrue(aset["tappable"])
        self._assert_steps_land_on_their_own_row(self.multi_pane, aset)

    def test_a_moved_cursor_moves_the_counts(self):
        # On both captures the cursor sits on row 1, where a hardcoded 0 would
        # pass. Move it and the counts must follow — including going NEGATIVE,
        # which is an Up key and not an Up-shaped Down.
        moved = (self.multi_pane.replace("❯ 1. Keep split", "  1. Keep split")
                                .replace("  2. Merge into one", "❯ 2. Merge into one"))
        aset = self._set(moved, self.multi_rows)
        self.assertEqual([o["steps"] for o in aset["options"]], [-1, 0])
        self.assertEqual([o["row"] for o in aset["options"]], [0, 1])   # rows do not move
        self._assert_steps_land_on_their_own_row(moved, aset)

    def test_the_single_question_fixture_is_one_ask_with_its_options(self):
        # 326 of 425 asks hold one question — this path must be trivially right.
        aset = self._set(self.single_pane, self.single_rows)
        q = _questions(self.single_rows)[0]
        self.assertEqual((aset["index"], aset["count"]), (0, 1))
        self.assertEqual(aset["header"], "multiSelect")
        self.assertTrue(aset["tappable"])
        self.assertEqual([o["label"] for o in aset["options"]],
                         [o["label"] for o in q["options"]])
        self.assertEqual(len(aset["options"]), 3)
        self.assertEqual([o["description"] for o in aset["options"]],
                         [o["description"] for o in q["options"]])
        self._assert_steps_land_on_their_own_row(self.single_pane, aset)

    def test_the_pane_says_which_ask_of_the_set_is_current(self):
        # The tab strip cannot say which tab is live (the marker is ANSI that
        # `capture-pane -p` drops), so the current Ask is identified by matching
        # the rendered question text. Advance the frame to q2 and the payload
        # must follow — otherwise it paints q1's answers over q2's question.
        q2 = _questions(self.multi_rows)[1]
        pane = _retarget(self.multi_pane, q2["question"],
                         [o["label"] for o in q2["options"]])
        aset = self._set(pane, self.multi_rows)
        self.assertEqual((aset["index"], aset["count"]), (1, 2))
        self.assertEqual(aset["header"], "Expand/contract")
        self.assertEqual([o["label"] for o in aset["options"]],
                         [o["label"] for o in q2["options"]])
        self._assert_steps_land_on_their_own_row(pane, aset)

    def test_a_hard_wrapped_and_clipped_question_still_matches(self):
        # The pane wraps mid-sentence at terminal width and `_pane_question`
        # clips at 200 chars, so equality would never match a long question and
        # every multi-question Set would fall back.
        rendered = server._pane_question(self.multi_pane)
        q1 = _questions(self.multi_rows)[0]["question"]
        self.assertNotIn(q1, self.multi_pane)          # the frame holds it hard-wrapped
        self.assertTrue(server._same_question(rendered, q1))
        self.assertTrue(server._same_question(rendered[:60], q1))       # …and clipped
        # …but text the tool never sent is a disagreement, not a clipped match.
        self.assertFalse(server._same_question(q1 + " and one more thing?", q1))

    def test_a_pane_that_disagrees_is_read_only_rather_than_wrong(self):
        # The documented fallback. The transcript's content is still sound — we
        # know WHICH Ask — so the options stay on screen to be read; what goes is
        # the tap, because a keystroke measured against rows that do not match is
        # exactly the wrong answer this run exists to remove.
        server._CONTRADICTIONS.clear()
        self.addCleanup(server._CONTRADICTIONS.clear)
        doctored = self.multi_pane.replace("  2. Merge into one", "  2. Merge into two")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            aset = self._set(doctored, self.multi_rows)
        # Said out loud, not merely fielded. The pane's rows no longer account
        # for the options the tool itself sent — the strongest evidence there is
        # that the renderer has moved — and a payload field nobody greps is how
        # ADR 0020's four bugs each survived for months.
        self.assertIn("pane-mismatch", err.getvalue())
        self.assertFalse(aset["tappable"])
        self.assertEqual(aset["fallback"], "pane-mismatch")
        self.assertEqual([o["steps"] for o in aset["options"]], [None, None])
        self.assertEqual([o["row"] for o in aset["options"]], [None, None])
        self.assertTrue(all(o["description"] for o in aset["options"]))   # still readable

    def test_a_pane_showing_a_question_the_tool_never_sent_drops_the_options(self):
        # A stronger fallback, because more is untrustworthy: with no question
        # matched we do not know which Ask is on screen, so its options are not
        # ours to draw. Painting q1's answers under q2's question is the bug.
        q1 = _questions(self.multi_rows)[0]
        pane = _retarget(self.multi_pane, "Something the tool never asked?",
                         [o["label"] for o in q1["options"]])
        aset = self._set(pane, self.multi_rows)
        self.assertEqual(aset["index"], -1)
        self.assertEqual(aset["count"], 2)
        self.assertEqual(aset["options"], [])
        self.assertFalse(aset["tappable"])
        self.assertEqual(aset["fallback"], "unmatched")

    def test_with_no_pane_the_ask_set_is_readable_but_never_tappable(self):
        # The queue's one-liner reads the transcript and captures no pane. It
        # still wants the question text; it must never get a keystroke count,
        # because nothing has been read about where the widget is standing.
        #
        # …nor a POSITION. The content shown is the first Ask's, which is the
        # only sane guess, but with two questions in the Set nobody has read
        # which one the widget is standing on — and `index: 0` would put "ask 1
        # of 2" on the phone as a statement about a screen no one looked at. -1
        # is the same "we cannot say" the `unmatched` branch emits, and the
        # client already hides the counter on it.
        aset = server._ask_set(server._pending_tool_use(self.multi_rows))
        self.assertEqual(aset["index"], -1)
        self.assertEqual(aset["count"], 2)
        self.assertFalse(aset["tappable"])
        self.assertEqual(aset["fallback"], "no-pane")
        self.assertEqual([o["steps"] for o in aset["options"]], [None, None])
        self.assertTrue(all(o["description"] for o in aset["options"]))   # still readable

    def test_a_multi_question_set_the_pane_did_not_place_asserts_no_position(self):
        # The same claim on the branch that matters more, because here a pane WAS
        # captured and it contradicts the transcript: `no-widget` says "WHICH Ask
        # is known", which is true for a Set of one and a guess for a Set of
        # three. ADR 0021: no parse failure may return a usable-looking default.
        server._CONTRADICTIONS.clear()
        self.addCleanup(server._CONTRADICTIONS.clear)
        with contextlib.redirect_stderr(io.StringIO()):
            aset = server._ask_set(server._pending_tool_use(self.multi_rows),
                                   server._read_pane("just some ordinary output\n"))
        self.assertEqual(aset["fallback"], "no-widget")
        self.assertEqual(aset["index"], -1)
        # A Set of one is the case where it IS known — one question, one answer
        # to "which Ask is this?" — so that position keeps being asserted.
        with contextlib.redirect_stderr(io.StringIO()):
            one = server._ask_set(server._pending_tool_use(self.single_rows),
                                  server._read_pane("just some ordinary output\n"))
        self.assertEqual(one["fallback"], "no-widget")
        self.assertEqual(one["index"], 0)

    def test_a_multiselect_ask_carries_its_flag_and_the_ticks_it_can_read(self):
        # `multiSelect` reaches the client because a tap there is a toggle, not
        # an answer. The tick state is READ off the frame — never remembered
        # locally (ADR 0020 answers every step against a fresh pane).
        aset = self._set(self.toggled_pane, self.toggled_rows)
        self.assertTrue(aset["multiSelect"])
        self.assertTrue(aset["tappable"])
        self.assertEqual([o["checked"] for o in aset["options"]], [True, True, False])
        # This capture's cursor sits on row 2 as taken — a real moved cursor.
        self.assertEqual(server._pane_widget(self.toggled_pane)["cursor"], 1)
        self.assertEqual([o["steps"] for o in aset["options"]], [-1, 0, 1])
        self._assert_steps_land_on_their_own_row(self.toggled_pane, aset)

    def test_an_unread_cursor_costs_the_tap_rather_than_guessing_at_zero(self):
        # The rows still account for the options — the ONLY thing missing is
        # where the cursor sits, and `steps` is measured from it. Defaulting to
        # 0 here produced counts that were right often enough to hide being
        # fiction the rest of the time (ADR 0020). Its own fallback name,
        # because a refusal you cannot tell from another refusal is half a
        # silent failure.
        unmarked = self.multi_pane.replace("❯ 1. Keep split", "  1. Keep split")
        aset = self._set(unmarked, self.multi_rows)
        self.assertFalse(aset["tappable"])
        self.assertEqual(aset["fallback"], "no-cursor")
        self.assertEqual([o["steps"] for o in aset["options"]], [None, None])
        self.assertTrue(all(o["description"] for o in aset["options"]))   # still readable

    def test_a_pane_with_no_widget_is_a_contradiction_not_a_missing_reading(self):
        # The transcript says an AskUserQuestion is pending; the pane we DID
        # capture shows no widget. Two sources disagree — which is exactly how
        # all four of ADR 0020's version-pinned assumptions presented, and each
        # one degraded quietly instead of saying so. It is now its own fallback
        # AND a line on stderr, because `fallback` is only seen by whoever is
        # looking at that Run and the log is seen by whoever asks why taps
        # stopped working everywhere at once.
        server._CONTRADICTIONS.clear()
        self.addCleanup(server._CONTRADICTIONS.clear)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            aset = server._ask_set(server._pending_tool_use(self.multi_rows),
                                   server._read_pane("just some ordinary output\n"))
        self.assertEqual(aset["fallback"], "no-widget")
        self.assertFalse(aset["tappable"])
        self.assertIn("disagree", err.getvalue())
        self.assertIn("no-widget", err.getvalue())
        # Deduped: the Board polls every few seconds and a stuck widget would
        # otherwise fill the log with one line per poll until nobody reads it.
        again = io.StringIO()
        with contextlib.redirect_stderr(again):
            server._ask_set(server._pending_tool_use(self.multi_rows),
                            server._read_pane("just some ordinary output\n"))
        self.assertEqual(again.getvalue(), "")

    def test_no_pane_and_no_widget_are_different_refusals(self):
        # "Nobody looked" is honest; "we looked and it is not there" is a bug
        # signal. Collapsing them is what let the widget go undetected for every
        # ask on this renderer without anything looking wrong.
        tu = server._pending_tool_use(self.multi_rows)
        self.assertEqual(server._ask_set(tu, None)["fallback"], "no-pane")
        self.assertEqual(server._ask_set(tu, server._read_pane(""))["fallback"], "no-pane")

    def test_the_pane_argument_cannot_be_got_wrong_silently(self):
        # The predecessor took `(widget, rendered)` where `rendered` had to be
        # `_pane_question(pane)`. Passing the raw pane instead — the natural
        # mistake — was a `str` either way, so it type-checked, matched nothing,
        # and produced an optionless Ask Set with no complaint: the very failure
        # class this slice exists to remove, in the API of the function that
        # enforces it. Now it raises.
        tu = server._pending_tool_use(self.multi_rows)
        with self.assertRaises(TypeError):
            server._ask_set(tu, self.multi_pane)
        with self.assertRaises(TypeError):
            server._ask_set(tu, server._pane_widget(self.multi_pane))

    def test_a_widget_whose_prompt_cannot_be_read_names_no_ask(self):
        # A widget IS up and the Set holds two questions, so "the first one" is
        # a guess dressed as a reading — and the guess puts q1's options under
        # q2's question, which is ADR 0020's opening incident. With no pane at
        # all the same guess is fine, because the caller is told (`no-pane`) and
        # never gets keystrokes for it.
        blanked = _retarget(self.multi_pane, "",
                            [o["label"] for o in _questions(self.multi_rows)[0]["options"]])
        self.assertEqual(server._pane_question(blanked), "")
        aset = self._set(blanked, self.multi_rows)
        self.assertEqual(aset["index"], -1)
        self.assertEqual(aset["fallback"], "unmatched")
        self.assertEqual(aset["options"], [])

    def test_a_set_of_one_still_has_to_match_the_prompt_on_screen(self):
        # A Set of one used to be answered BEFORE `_same_question` was consulted,
        # leaving the option labels as the only cross-check — and labels are the
        # likeliest thing two Asks share. 326 of the 425 asks on disk hold one
        # question, so this is the COMMON case, not a corner.
        #
        # The realistic trigger: Ask A is answered at the desk, Claude raises Ask
        # B, and the transcript tail still carries A's unflushed tool_use. The
        # phone then paints A's options under B's question and a tap answers B
        # with them — ADR 0020's opening incident, in a Set of one.
        server._CONTRADICTIONS.clear()
        self.addCleanup(server._CONTRADICTIONS.clear)
        q = _questions(self.single_rows)[0]
        stale = _retarget(self.single_pane, "Should I delete the production database?",
                          [o["label"] for o in q["options"]])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            aset = self._set(stale, self.single_rows)
        self.assertFalse(aset["tappable"])
        self.assertEqual(aset["fallback"], "unmatched")
        self.assertEqual(aset["index"], -1)
        self.assertEqual(aset["options"], [])      # not ours to draw
        self.assertIn("unmatched", err.getvalue())

    def test_a_set_of_one_with_no_prompt_read_is_still_ask_one(self):
        # The other half, and the reason the check is on the PROMPT rather than
        # on the Set's size: with nothing read off a screen there is nothing to
        # disagree with, and a Set of one has exactly one answer to the question
        # "which Ask is this?". The caller is told (`no-pane`/`no-widget`) and
        # gets no keystrokes for it.
        tu = server._pending_tool_use(self.single_rows)
        self.assertEqual(server._ask_set(tu)["index"], 0)
        self.assertEqual(server._ask_set(tu)["fallback"], "no-pane")
        blanked = _retarget(self.single_pane, "",
                            [o["label"] for o in _questions(self.single_rows)[0]["options"]])
        self.assertEqual(server._pane_question(blanked), "")
        self.assertEqual(self._set(blanked, self.single_rows)["index"], 0)

    def test_an_approval_is_an_ask_set_of_one_with_no_invented_structure(self):
        tu = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
        self.assertEqual(server._ask_set(tu), {})
        self.assertEqual(server._ask_set(None), {})


class ContradictionLogTests(unittest.TestCase):
    """The dedupe on the stderr contradiction report — the one mechanism whose
    entire purpose is NOT swallowing things, and therefore the one place a
    swallow is worst (ADR 0021's escape hatch: silence is the condition all four
    of ADR 0020's bugs needed)."""

    def setUp(self):
        server._CONTRADICTIONS.clear()
        self.addCleanup(server._CONTRADICTIONS.clear)

    def _say(self, where, reason="no-widget"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            server._report_contradiction(where, reason)
        return err.getvalue()

    def test_reports_with_no_key_are_not_all_the_same_report(self):
        # The key is the tool_use id, and `str(tu.get("id") or "")` makes every
        # idless tool_use share ONE dedupe slot: three distinct Runs contradicting
        # at once, two reports silently dropped. An absent key is not evidence
        # that two disagreements are the same disagreement, so it does not dedupe
        # them — the cost is a repeated line on a path that should not occur, and
        # a repeated line is the failure this function is willing to have.
        self.assertTrue(self._say(""))
        self.assertTrue(self._say(""))

    def test_a_known_key_still_says_each_disagreement_once(self):
        # The Board polls every few seconds; a stuck widget must not fill the log.
        self.assertTrue(self._say("toolu_a"))
        self.assertEqual(self._say("toolu_a"), "")
        self.assertTrue(self._say("toolu_a", "pane-mismatch"))   # a NEW disagreement

    def test_crossing_the_bound_evicts_the_oldest_not_everyone(self):
        # The bound was a wholesale `clear()`, so the 257th Run made every live
        # Run re-log once the next time it polled: a BURST in the log, which is
        # precisely the pattern that is supposed to mean "the renderer moved".
        # A signal that fires on its own bookkeeping is worse than no signal.
        for i in range(300):
            self._say(f"toolu_{i}")
        self.assertLessEqual(len(server._CONTRADICTIONS), 256)
        # The recent ones are still known, so they stay quiet…
        for i in range(250, 300):
            self.assertEqual(self._say(f"toolu_{i}"), "", f"toolu_{i} re-logged")
        # …and only the oldest, evicted in order, speak up again.
        self.assertTrue(self._say("toolu_0"))


class AskSetBoardTests(unittest.TestCase):
    """The Ask Set as `/api/board` ships it: one transcript read and one pane
    capture between them (ADR 0014), for the fixture pair that used to produce
    ADR 0020's wrong-answer table."""

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "cached_foreign_runs", "_tail_rows",
                        "_pane_contents", "_ai_title", "_tmux_server_down")}
        self.reads = []
        rows = _fixture_rows("ask_multi.jsonl")
        server._tmux_server_down = lambda: False
        server.cached_foreign_runs = lambda: []
        server._ai_title = lambda sid: "to-tickets"
        server._tail_rows = lambda sid: rows
        server._pane_contents = lambda rid: (self.reads.append(rid)
                                             or _capture("ask_multi.pane"))
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/strength-log",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "",
        }]

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)

    def test_the_focus_carries_the_current_ask_and_only_its_options(self):
        focus = server._board()["focus"]
        self.assertEqual(focus["lane"], "question")
        self.assertTrue(focus["ask"].startswith("Ticket 3 (detector)"))
        self.assertEqual(focus["options"], ["Keep split (Recommended)", "Merge into one"])
        self.assertEqual((focus["askSet"]["index"], focus["askSet"]["count"]), (0, 2))
        self.assertTrue(focus["askSet"]["tappable"])

    def test_the_payload_steps_land_on_their_own_rows(self):
        focus = server._board()["focus"]
        rows = server._pane_widget(_capture("ask_multi.pane"))["rows"]
        for o in focus["askSet"]["options"]:
            landed = rows[focus["cursor"] + o["steps"]]
            self.assertFalse(landed["affordance"])
            self.assertTrue(o["label"].startswith(landed["label"]))

    def test_a_widget_without_a_reconciled_set_reports_no_cursor_at_all(self):
        """`cursor` carries two incompatible spaces — rows for a widget, options
        for a menu — and nothing on the wire says which. The **Ask Set** is what
        tells a consumer it is looking at row-space, so row-space without one is a
        number the client will read in the other space.

        The window is real: a stale widget frame still painted while the pending
        `tool_use` has moved on to an approval leaves `askSet` {} and `options`
        filled from the menu parse. Answering then steps by a row index read as an
        option index — ADR 0020's wrong-space bug, transiently. It refuses instead
        (ADR 0021)."""
        rows = _fixture_rows("ask_multi.jsonl")
        tu = server._pending_tool_use(rows)
        tu["name"] = "Bash"                       # the pending call is now an approval
        tu["input"] = {"command": "ls"}
        server._tail_rows = lambda sid: rows
        focus = server._board()["focus"]
        self.assertEqual(focus["askSet"], {})     # nothing reconciled...
        self.assertIsNone(focus["cursor"])        # ...so no position is offered

    def test_the_pane_is_captured_once_per_poll(self):
        # The Ask Set added no second capture and no second file read: ADR 0014's
        # discipline, which is why `widget` and `rendered` are passed into
        # `_ask_of` rather than read there.
        server._board()
        self.assertEqual(len(self.reads), 1)

    def test_the_payload_says_null_rather_than_zero_for_an_unread_cursor(self):
        # The wire contract changed with this slice: `cursor` is `number | null`,
        # and null means "the frame painted no cursor". It is NOT 0 — 0 is a real
        # position, and the two being indistinguishable is what produced ADR
        # 0020's wrong answers. `askSet.tappable` is false alongside it, which is
        # the field a client should be branching on.
        #
        # NOTE for the client slice: `web/board.js` currently does
        # `const cur = f.cursor || 0`, which turns this null straight back into
        # the defaulted 0 — and it paints option chips off `f.options` without
        # consulting `askSet.tappable`/`fallback` at all. The server now refuses;
        # the phone does not yet.
        server._pane_contents = lambda rid: _capture("ask_multi.pane").replace(
            "❯ 1. Keep split", "  1. Keep split")
        focus = server._board()["focus"]
        self.assertIsNone(focus["cursor"])
        self.assertFalse(focus["askSet"]["tappable"])
        self.assertEqual(focus["askSet"]["fallback"], "no-cursor")

    def test_the_focus_says_where_the_reply_box_text_would_go(self):
        # The phone cannot word its send button without this: on one route,
        # sending prose presses Esc and CANCELS the ask, and that may not be
        # discovered by tapping (ADR 0020).
        focus = server._board()["focus"]
        self.assertEqual(focus["textRoute"], {"route": "affordance", "reason": ""})

    def test_the_destructive_route_is_named_on_the_wire_before_it_is_taken(self):
        server._pane_contents = lambda rid: _capture("ask_multi.pane").replace(
            "Type something.", "Ponder this.")
        self.assertEqual(server._board()["focus"]["textRoute"],
                         {"route": "esc", "reason": "no-row"})

    def test_no_keystroke_count_crosses_the_wire_for_free_text(self):
        # `steps` stays server-side, unlike `askSet[*].steps`: it is measured off
        # a frame that is already up to a poll stale by the time a thumb moves,
        # and `respond_run` re-reads and re-decides. What crosses is enough to
        # word a button and useless for driving one.
        for pane in (_capture("ask_multi.pane"), _INPUT_PANE, ""):
            with self.subTest(pane=pane[:20]):
                server._pane_contents = lambda rid, p=pane: p
                self.assertEqual(set(server._board()["focus"]["textRoute"]),
                                 {"route", "reason"})

    def test_an_idle_run_carries_no_ask_set(self):
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/x",
            "status": "idle", "bridge": "", "updatedAt": time.time() * 1000,
            "snippet": "",
        }]
        server._pane_contents = lambda rid: "just some output\n"
        focus = server._board()["focus"]
        self.assertEqual(focus["askSet"], {})
        self.assertEqual(focus["ask"], "")


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
                       ("cached_runs", "cached_foreign_runs", "_transcript_path",
                        "_pane_contents", "_ai_title", "_tmux_server_down")}
        server._tmux_server_down = lambda: False         # don't shell out to real tmux
        server.cached_foreign_runs = lambda: []          # nor walk `ps` for Foreign Runs
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

    def test_the_current_renderer_raises_no_false_unsent_warning(self):
        # The bug this slice closes. With the widget undetected, `pending` fell
        # through to `_pane_input`, which scraped the widget's body out from
        # between its framing rules — so the phone painted **⚠ unsent text
        # already in this session's input box** over the question itself, beside
        # a 'clear the box' button that fires hundreds of BSpace into a live
        # selector. It fired on all 425 asks, not the 99 multi-question ones.
        for name in ("ask_multi.pane", "ask_single.pane"):
            with self.subTest(name):
                server._pane_contents = lambda rid, n=name: _capture(n)
                focus = server._board()["focus"]
                self.assertEqual(focus["pendingInput"], "")
                self.assertEqual(focus["lane"], "question")


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
    # last assistant turn is a bare Bash tool_use — no prose, so _ask_of's
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
    'approval' with an empty ask and an empty run-up. Pre-existing board gap, not
    the tmux swap (`_ask_of` reads the transcript, not the pane).

    Ticket: .scratch/approval-card-detail/issues/01-approval-cards-show-command.md
    """

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "cached_foreign_runs", "_tail_rows",
                        "_transcript_path", "_pane_contents", "_ai_title",
                        "_tmux_server_down")}
        server._tmux_server_down = lambda: False
        server.cached_foreign_runs = lambda: []
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
        self.assertIn("wc -w", focus.get("ask", ""),
                      "approval card must surface the command being approved")

    def test_bash_command_rides_the_plaintext_ask_field(self):
        # The command is untrusted transcript text; it goes in `ask` (textContent,
        # never innerHTML), so no innerHTML'd field carries an approval `input` —
        # since ADR 0014 that means no **Turn**'s `html` either.
        focus = server._board()["focus"]
        self.assertIn("wc -w", focus["ask"])
        for turn in focus["scrollback"]:
            self.assertNotIn("wc -w", turn.get("html", ""))
        self.assertEqual(focus["lane"], "approval")          # badge unchanged
        self.assertEqual(focus["options"], ["Yes", "No"])    # Yes/No preserved


# Approval tool_uses other than Bash. Each leaves a FLUSHED pending tool_use
# (ADR 0009); the card reads its `input`, not the pane. _lane_of + _ask_of
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
        ask, _, _ = server._ask_of(_GOOD)
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

    def test_the_run_up_prose_above_the_command_is_no_longer_returned(self):
        # That prose used to ride back as ADR 0006's `contextHtml`, clipped to a
        # budget of its own. It is the **Scrollback** now (ADR 0014), bounded per
        # turn by _TURN_MAX, so `_ask_of` yields the **Ask** and nothing
        # else — however much prose sits above the command.
        long_prose = "we were " + "y" * 3000
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": long_prose},
            {"type": "tool_use", "id": "toolu_x", "name": "Bash",
             "input": {"command": "ls"}}]}}]
        server._tail_rows = lambda sid: rows
        # An approval is an **Ask Set** of one and carries no question
        # structure — hence the empty `askSet` (ADR 0020).
        self.assertEqual(server._ask_of(_GOOD), ("ls", [], {}))


class ScrollbackTests(unittest.TestCase):
    """The **Scrollback** — the recent **turns** of the Focus's Session, oldest
    first (ADR 0014). Built from an already-parsed tail, so these exercise
    `_scrollback` directly on transcript rows."""

    def test_turns_arrive_oldest_first_with_their_role(self):
        turns = server._scrollback([
            {"type": "user", "message": {"content": [{"type": "text", "text": "first"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}},
        ])
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        self.assertIn("first", turns[0]["html"])
        self.assertIn("second", turns[-1]["html"])

    def test_escaping_is_total(self):
        # THE load-bearing test (ADR 0006): the client innerHTMLs every turn's
        # html, so transcript markup must arrive as TEXT, never as an element.
        # ADR 0014 widens that exception from one field to N — same function,
        # same guarantee, so the same test has to hold per turn.
        hostile = ("here is <script>alert(1)</script> and an <img src=x onerror=y> "
                   "with `<b>ticks</b>` and a | pipe |")
        turns = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": hostile}]}}])
        html = turns[0]["html"]
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=y&gt;", html)
        self.assertIn("<code>&lt;b&gt;ticks&lt;/b&gt;</code>", html)   # inline code, escaped
        self.assertIn("| pipe |", html)                                # no stray table

    def test_a_run_of_tool_calls_is_one_entry(self):
        # The COMMON case on a working Run: long stretches of tool_use with no
        # prose, one call per assistant row. They were one entry EACH and ate a
        # slot each — 5-8 of the 14 on a live Session, evicting the prose. One
        # contiguous run is now one entry (ADR 0016).
        rows = [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                     "input": {"command": f"ls {i}"}}]}} for i in range(5)]
        entries = server._scrollback(rows)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "work")
        self.assertEqual(entries[0]["n"], 5)
        self.assertEqual([c["name"] for c in entries[0]["calls"]], ["Bash"] * 5)

    def test_prose_between_two_runs_keeps_them_apart(self):
        # The run boundary is what makes coalescing safe to render as one block:
        # a run is a stretch of work BETWEEN two things that were said.
        entries = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Read", "input": {}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "found it"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "b", "name": "Edit", "input": {}}]}},
        ])
        self.assertEqual([e["role"] for e in entries], ["work", "assistant", "work"])

    def test_a_call_carries_what_it_was_doing_not_just_its_name(self):
        # The whole point: `Bash` names the tool and says nothing. Generalised
        # from `_approval_detail`, which answers this for the **Ask** already.
        entries = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Bash",
                 "input": {"command": "git push --force origin main"}}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "b", "name": "Grep",
                 "input": {"pattern": "TODO", "path": "src/"}}]}},
        ])
        self.assertEqual([c["detail"] for c in entries[0]["calls"]],
                         ["git push --force origin main", "TODO in src/"])

    def test_a_calls_detail_is_one_line(self):
        # A heredoc arrives with newlines in it and renders as a single
        # ellipsised line, so the newlines go here rather than in the CSS.
        entries = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Bash",
                 "input": {"command": "git commit -F - <<'EOF'\nsubject\n\nbody\nEOF"}}]}}])
        self.assertEqual(entries[0]["calls"][0]["detail"],
                         "git commit -F - <<'EOF' subject body EOF")

    def test_a_run_keeps_its_true_count_but_bounds_its_details(self):
        rows = [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                     "input": {"command": "x" * 900}}]}}
                for i in range(server._RUN_CALLS * 3)]
        entries = server._scrollback(rows)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["n"], server._RUN_CALLS * 3)         # the count is true
        self.assertEqual(len(entries[0]["calls"]), server._RUN_CALLS)    # the weight is not
        for call in entries[0]["calls"]:
            self.assertLessEqual(len(call["detail"]), server._CALL_MAX + 1)

    def test_prose_and_tools_on_one_row_yield_both_entries(self):
        # No such row has ever been observed — Claude Code splits them — but the
        # alternative to handling it is dropping the call on the floor the day
        # that stops being true.
        entries = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "let me look"},
                {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "x"}}]}}])
        self.assertEqual([e["role"] for e in entries], ["assistant", "work"])
        self.assertIn("let me look", entries[0]["html"])

    def test_an_injected_skill_body_is_not_your_turn(self):
        # `isMeta` is a skill's injected body — 2-7KB of instructions nobody
        # typed, rendered until now as prose you appear to have sent.
        entries = server._scrollback([
            {"type": "user", "isMeta": True,
             "message": {"content": "Base directory for this skill: /x\n\n# Ship\n…"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "on it"}]}},
        ])
        self.assertEqual([e["role"] for e in entries], ["assistant"])

    def test_the_slash_command_you_invoked_survives_the_plumbing(self):
        # With the body dropped, this row is the only trace a bare `/ship`
        # leaves — and it is the one line that is true.
        entries = server._scrollback([
            {"type": "user", "message": {"content":
                "<command-message>ship</command-message>\n<command-name>/ship</command-name>"}},
            {"type": "user", "isMeta": True, "message": {"content": "Base directory…"}},
        ])
        self.assertEqual(entries, [{"role": "command", "cmd": "/ship"}])

    def test_a_tool_result_row_is_not_a_human_turn(self):
        turns = server._scrollback([
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "total 4"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        ])
        self.assertEqual([t["role"] for t in turns], ["assistant"])

    def test_sidechain_rows_are_dropped(self):
        # A sidechain is a subagent's own thread, not this Session's turns.
        turns = server._scrollback([
            {"type": "assistant", "isSidechain": True,
             "message": {"content": [{"type": "text", "text": "subagent chatter"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "mine"}]}},
        ])
        self.assertEqual(len(turns), 1)
        self.assertIn("mine", turns[0]["html"])

    def test_a_plain_string_user_message_is_a_turn(self):
        # message.content is a bare string as often as it is a block list.
        turns = server._scrollback([
            {"type": "user", "message": {"content": "just text, no blocks"}}])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["role"], "user")
        self.assertIn("just text, no blocks", turns[0]["html"])

    def test_angle_wrapped_plumbing_rows_are_dropped(self):
        # Everything angle-wrapped goes, EXCEPT the slash command inside it.
        turns = server._scrollback([
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "<system-reminder>be terse</system-reminder>"}]}},
            {"type": "user", "message": {"content": "a real ask"}},
        ])
        self.assertEqual(len(turns), 1)
        self.assertIn("a real ask", turns[0]["html"])

    def test_a_turn_with_neither_prose_nor_tools_is_dropped(self):
        turns = server._scrollback([
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "   "}]}},
            {"type": "system", "message": {"content": [{"type": "text", "text": "noise"}]}},
        ])
        self.assertEqual(turns, [])

    def test_the_turn_count_is_bounded_to_the_newest(self):
        rows = [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": f"turn {i}"}]}} for i in range(40)]
        turns = server._scrollback(rows)
        self.assertEqual(len(turns), server._SCROLLBACK_TURNS)
        self.assertIn("turn 39", turns[-1]["html"])                     # newest last
        self.assertIn(f"turn {40 - server._SCROLLBACK_TURNS}", turns[0]["html"])

    def test_each_turn_is_clipped(self):
        long_prose = "z" * (server._TURN_MAX * 3)
        turns = server._scrollback([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": long_prose}]}}])
        self.assertLessEqual(len(turns[0]["html"]), server._TURN_MAX + 10)   # + <p>…</p>
        self.assertTrue(turns[0]["html"].endswith("…</p>"))


# An idle Focus whose last turn happens to end in a `?`. The prose-`?` regex in
# `_ask_of` would restate it as an **Ask**, but the turn is now visibly the
# last row of the **Scrollback** — so an idle Run has no Ask at all (CONTEXT.md).
_IDLE_ROWS = [
    {"type": "user", "message": {"content": "which one should we ship?"}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Both work. Do you want the bounded tail or the whole thread?"}]}},
]


class FocusScrollbackTests(unittest.TestCase):
    """`/api/board`'s focus carries the **Scrollback**, and the **Ask** is a
    property of being **Blocked** and of nothing else (ADR 0014)."""

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "cached_foreign_runs", "_tail_rows",
                        "_transcript_path", "_pane_contents", "_ai_title",
                        "_tmux_server_down")}
        self.reads = []
        self.now = time.time() * 1000        # fresh, so the idle Run doesn't dorm
        server._tmux_server_down = lambda: False
        server.cached_foreign_runs = lambda: []
        server._transcript_path = lambda *a, **k: ""
        server._ai_title = lambda sid: "ship the scrollback"
        self._be_idle()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)

    def _run(self, status):
        return [{"id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
                 "status": status, "bridge": "", "updatedAt": self.now, "snippet": ""}]

    def _tail(self, rows):
        def read(sid):
            self.reads.append(sid)
            return rows
        server._tail_rows = read

    def _be_idle(self):
        self._tail(_IDLE_ROWS)
        server._pane_contents = lambda rid: ""
        server.cached_runs = lambda: self._run("idle")

    def _be_blocked(self):
        self._tail(_APPROVAL_ROWS)
        server._pane_contents = lambda rid: _BASH_APPROVAL_PANE
        server.cached_runs = lambda: self._run("waiting")

    def test_the_focus_carries_its_turns_oldest_first(self):
        focus = server._board()["focus"]
        self.assertEqual([t["role"] for t in focus["scrollback"]], ["user", "assistant"])
        self.assertIn("which one should we ship?", focus["scrollback"][0]["html"])

    def test_the_scrollback_costs_no_second_read_of_the_tail(self):
        # ADR 0014: the tail is parsed once and yields both the scrollback and
        # the Ask. An idle Run touches _tail_rows for nothing else.
        server._board()
        self.assertEqual(self.reads, [_GOOD])

    def test_an_idle_focus_ending_in_a_question_has_no_ask(self):
        focus = server._board()["focus"]
        self.assertEqual(focus["lane"], "yourmove")
        self.assertEqual(focus["ask"], "")
        self.assertEqual(focus["options"], [])
        # …and the sentence is not lost: it is the last turn on screen.
        self.assertIn("bounded tail or the whole thread?", focus["scrollback"][-1]["html"])

    def test_a_snoozed_run_that_is_blocked_keeps_its_ask(self):
        # Snooze masks the real lane outright (`lane = "snoozed" if snoozed else
        # _lane_of(r)`), so gating the Ask on the *displayed* lane would strip
        # the blocker from a Blocked Run the moment you pinned it — which is the
        # one moment you meant to answer it. Snooze orders the queue; it does not
        # decide whether a Run is Blocked (CONTEXT.md, **Ask**).
        self._be_blocked()
        server._SNOOZE[_GOOD] = self.now + 60_000
        try:
            focus = server._board(_GOOD)["focus"]
            self.assertEqual(focus["lane"], "snoozed")
            self.assertTrue(focus["ask"], "a snoozed-but-Blocked Focus lost its blocker")
        finally:
            server._SNOOZE.pop(_GOOD, None)

    def test_a_snoozed_run_that_is_idle_still_has_no_ask(self):
        # The other half: snooze does not manufacture an Ask either.
        server._SNOOZE[_GOOD] = self.now + 60_000
        try:
            self.assertEqual(server._board(_GOOD)["focus"]["ask"], "")
        finally:
            server._SNOOZE.pop(_GOOD, None)

    def test_a_blocked_focus_still_gets_its_ask(self):
        self._be_blocked()
        focus = server._board()["focus"]
        self.assertEqual(focus["lane"], "approval")
        self.assertIn("wc -w", focus["ask"])
        self.assertEqual(focus["options"], ["Yes", "No"])

    def test_a_blocked_focus_carries_a_scrollback_too(self):
        self._be_blocked()
        focus = server._board()["focus"]
        self.assertIn("recent dated notes", focus["scrollback"][0]["html"])
        self.assertEqual(focus["scrollback"][-1]["role"], "work")
        self.assertEqual([c["name"] for c in focus["scrollback"][-1]["calls"]], ["Bash"])

    def test_the_pending_call_appears_in_the_run_as_well_as_the_ask(self):
        # The tool_use a **Blocked** Run is waiting on is in the transcript, so it
        # is the last call of the last **work run** as well as the **Ask**. ADR
        # 0014 deleted the ask that repeated the last turn, so this is worth being
        # explicit about: it is NOT that repeat. A run is collapsed until tapped,
        # so on screen the command appears exactly once — in the Ask, which is the
        # actionable copy. Expanded, the run is where it sits in the sequence.
        self._be_blocked()
        focus = server._board()["focus"]
        self.assertIn("wc -w", focus["ask"])
        self.assertIn("wc -w", focus["scrollback"][-1]["calls"][-1]["detail"])

    def test_context_html_is_gone(self):
        # The deliberate overlap of the previous slice is over: the client reads
        # the scrollback now, so the field that carried the single last assistant
        # message no longer ships (ADR 0014).
        self.assertNotIn("contextHtml", server._board()["focus"])

    def test_the_etag_moves_when_a_turn_is_added(self):
        _, before = server._board_payload()
        self._tail(_IDLE_ROWS + [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": "one more turn"}]}}])
        _, after = server._board_payload()
        self.assertNotEqual(before, after)


class BoardPayloadTests(unittest.TestCase):
    """The Board's item dict now carries `bridge` so the client can build the
    deep-link into the Claude app (ADR 0008)."""

    def setUp(self):
        self._saved = (server.cached_runs, server.cached_foreign_runs,
                       server._tmux_server_down)
        server._tmux_server_down = lambda: False         # don't shell out to real tmux
        server.cached_foreign_runs = lambda: []          # nor walk `ps` for Foreign Runs

    def tearDown(self):
        (server.cached_runs, server.cached_foreign_runs,
         server._tmux_server_down) = self._saved

    def test_items_carry_the_bridge_for_the_deep_link(self):
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
            "status": "busy", "bridge": "session_abc", "updatedAt": 5000, "snippet": "",
        }]
        board = server._board()
        # A working Run IS a Focus now — it is the fallback when nothing wants
        # you, because the alternative was a phone holding live Runs with no
        # route to any of them. It does not join `watching`, being the Focus.
        self.assertEqual(board["focus"]["sessionId"], _GOOD)
        self.assertEqual(board["focus"]["bridge"], "session_abc")
        self.assertEqual(board["watching"], [])

    def test_the_workspace_is_the_basename_of_the_cwd(self):
        # Right for a repo AND for this workspace's worktree layout, which
        # flattens the discriminator into the directory name — so two worktrees
        # of one repo are two different Workspaces (ADR 0023).
        self.assertEqual(server._workspace("~/projects/claude-launcher"), "claude-launcher")
        self.assertEqual(server._workspace("~/projects/.worktrees/claude-launcher-recover-filter"),
                         "claude-launcher-recover-filter")
        self.assertEqual(server._workspace("~/projects/x/"), "x")

    def test_no_cwd_means_no_workspace(self):
        # None, not a stand-in. The pane title that used to land here read as a
        # project name without being one, and the phone cannot tell the two apart.
        self.assertIsNone(server._workspace(""))
        self.assertIsNone(server._workspace(None))

    def test_a_board_of_only_working_runs_still_has_a_focus(self):
        # The bug this fixes: `order` is blocked + idle, so an all-busy Board had
        # no focus, hence no `.fhead`, hence no queue pill and no swipe target —
        # live Runs and no route to any of them from a phone (ADR 0023).
        server.cached_runs = lambda: [
            {"id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
             "status": "busy", "bridge": "", "updatedAt": 5000, "snippet": ""},
            {"id": _RUN2, "sessionId": _LIVE, "title": "y", "dir": "~/projects/y",
             "status": "busy", "bridge": "", "updatedAt": 9000, "snippet": ""},
        ]
        board = server._board()
        self.assertEqual(board["focus"]["sessionId"], _LIVE)   # freshest of the working
        self.assertEqual([it["sessionId"] for it in board["watching"]], [_GOOD])

    def test_the_fallback_does_not_pad_the_triage_list(self):
        # `order` is untouched, so `upnext` and `counts.needYou` still mean "wants
        # you" — a working Focus is a route, not a demand.
        server.cached_runs = lambda: [
            {"id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
             "status": "busy", "bridge": "", "updatedAt": 5000, "snippet": ""},
        ]
        board = server._board()
        self.assertEqual(board["upnext"], [])
        self.assertEqual(board["counts"]["needYou"], 0)

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


class TriageOrderTests(unittest.TestCase):
    """The **Board**'s triage list, nested zone -> priority -> lane -> recency.

    The whole reason this class exists: nothing here was covered. `_board`'s
    `(pri, updatedAt)` sort had no server-side test at all, and neither did
    "high priority never dorms" — the only thing asserting anything about `pri`
    was the client's no-cut-in test, which guards the opposite concern (that
    urgency orders the queue and never the **Focus**). So the sort was free to
    be re-nested by accident, which is roughly how it came to nest lane above
    priority in the first place.

    `_board` hands the head of the triage list to the **Focus** when nothing is
    pinned, so the order under test is `[focus] + upnext` and not `upnext`
    alone — the Focus is the first row of the queue here, not a row taken out
    of it.
    """

    # Any string keys `_PRIORITY`; these only have to be distinct and readable
    # in a failure message, so they say what they are rather than pretending to
    # be uuids the sort never parses.
    HI_BLOCKED, HI_IDLE = "s-hi-blocked", "s-hi-idle"
    NO_BLOCKED, NO_IDLE = "s-normal-blocked", "s-normal-idle"
    LO_BLOCKED = "s-low-blocked"

    def setUp(self):
        self._saved = {k: getattr(server, k) for k in
                       ("cached_runs", "cached_foreign_runs", "_tmux_server_down",
                        "_tail_rows", "_pane_contents")}
        self._pris = dict(server._PRIORITY)
        server._tmux_server_down = lambda: False
        server.cached_foreign_runs = lambda: []
        # No transcript and no pane: a `waiting` Run with no flushed tool_use
        # reads as the question lane, which is **Blocked** and is all this sort
        # cares about. The concrete **Ask** is another test's subject.
        server._tail_rows = lambda sid: []
        server._pane_contents = lambda rid: ""
        server._PRIORITY.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(server, k, v)
        server._PRIORITY.clear()
        server._PRIORITY.update(self._pris)

    def _runs(self, *specs):
        """specs are (sessionId, status, pri, age_ms); status "waiting" is
        Blocked and "" is idle. Registers the priorities and installs the list.

        Ages, never absolute stamps: an idle Run older than `_BOARD_DORMANT_MS`
        is not in this queue at all, so a literal `updatedAt` of 5000 — 1970 —
        would quietly dorm every idle row and leave the sort untested.
        """
        now = time.time() * 1000
        rows = []
        for sid, status, pri, age in specs:
            if pri != 1:
                server._PRIORITY[sid] = pri
            rows.append({"id": "run-" + sid, "sessionId": sid, "dir": "~/projects/x",
                         "status": status, "bridge": "", "updatedAt": now - age,
                         "snippet": ""})
        server.cached_runs = lambda: rows

    def _order(self):
        board = server._board()
        head = [board["focus"]["sessionId"]] if board["focus"] else []
        return head + [it["sessionId"] for it in board["upnext"]]

    def test_a_high_idle_run_outranks_a_normal_blocked_one(self):
        # The report, in one assertion. Under the old nesting the lane decided
        # first and this was the other way round, so a `high` you had just
        # marked sat under every Blocked Run on the Board.
        self._runs((self.NO_BLOCKED, "waiting", 1, 1000),
                   (self.HI_IDLE, "", 0, 1000))
        self.assertEqual(self._order(), [self.HI_IDLE, self.NO_BLOCKED])

    def test_a_low_blocked_run_sinks_below_a_normal_idle_one(self):
        # There is no floor under **Blocked**. `low` on a Blocked Run means "I
        # know it is asking, I do not care yet", and the level is worthless if
        # the lane can veto it.
        self._runs((self.LO_BLOCKED, "waiting", 2, 1000),
                   (self.NO_IDLE, "", 1, 1000))
        self.assertEqual(self._order(), [self.NO_IDLE, self.LO_BLOCKED])

    def test_inside_one_level_blocked_still_precedes_idle(self):
        # The old rule is not gone, it is one level deeper.
        self._runs((self.NO_IDLE, "", 1, 1000),
                   (self.NO_BLOCKED, "waiting", 1, 1000))
        self.assertEqual(self._order(), [self.NO_BLOCKED, self.NO_IDLE])

    def test_recency_still_flips_direction_by_lane(self):
        # Blocked oldest-first (you have waited longest); idle freshest-first
        # (staleness reads as done). Both ties are broken inside one priority
        # level, so this is the innermost key and nothing above it can be
        # deciding the result.
        old_b, new_b = self.NO_BLOCKED, self.NO_BLOCKED + "-2"
        new_i, old_i = self.NO_IDLE, self.NO_IDLE + "-2"
        self._runs((new_b, "waiting", 1, 1000), (old_b, "waiting", 1, 90_000),
                   (old_i, "", 1, 90_000), (new_i, "", 1, 1000))
        self.assertEqual(self._order(), [old_b, new_b, new_i, old_i])

    def test_the_three_keys_nest_in_that_order(self):
        # One board carrying every combination, so the nesting is asserted whole
        # rather than one key at a time: priority outermost, then lane, then age.
        self._runs((self.NO_BLOCKED, "waiting", 1, 1000),
                   (self.LO_BLOCKED, "waiting", 2, 1000),
                   (self.HI_IDLE, "", 0, 1000),
                   (self.HI_BLOCKED, "waiting", 0, 1000),
                   (self.NO_IDLE, "", 1, 1000))
        self.assertEqual(self._order(),
                         [self.HI_BLOCKED, self.HI_IDLE,      # high: blocked, then idle
                          self.NO_BLOCKED, self.NO_IDLE,      # normal, same split
                          self.LO_BLOCKED])                   # low, under both idles

    def test_a_working_run_holds_no_priority_level_open(self):
        # "Until that level is exhausted" only means anything because a Run that
        # goes **working** leaves the triage list entirely — it does not sit at
        # the head of `high` blocking the descent. It goes to `watching`, and
        # `counts.needYou` keeps meaning the number of Runs that want you.
        self._runs((self.HI_BLOCKED, "busy", 0, 1000),
                   (self.NO_IDLE, "", 1, 1000))
        board = server._board()
        self.assertEqual([it["sessionId"] for it in board["upnext"]], [])
        self.assertEqual(board["focus"]["sessionId"], self.NO_IDLE)   # the head, not the high one
        self.assertEqual([it["sessionId"] for it in board["watching"]], [self.HI_BLOCKED])
        self.assertEqual(board["counts"]["needYou"], 1)

    def test_high_priority_never_dorms(self):
        # `_BOARD_DORMANT_MS` parks a long-idle Run out of the queue, and `pri`
        # exempts it — untested until now, and load-bearing under the new
        # nesting: a `high` that dormed would be a level that empties for a
        # reason other than its members going working.
        stale = server._BOARD_DORMANT_MS + 1000
        self._runs((self.HI_IDLE, "", 0, stale), (self.NO_IDLE, "", 1, stale))
        board = server._board()
        self.assertEqual(board["focus"]["sessionId"], self.HI_IDLE)
        self.assertEqual([it["sessionId"] for it in board["upnext"]], [])
        self.assertEqual([it["sessionId"] for it in board["dormant"]], [self.NO_IDLE])


class NicknameStoreTests(unittest.TestCase):
    """The **Nickname**: a short name you typed, on the **Session**, kept beside
    `priority` and `snooze` in `.board-state.json` (ADR 0026). Keyed by
    `sessionId`, which is the one id no lifecycle verb changes — so surviving
    close, **Resume**, **Recover**, **Transfer** and a restart is one property of
    the key, tested once here rather than once per verb."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "projects")   # stand-in ~/.claude/projects
        self._saved = (server._STATE_FILE, server._PROJECTS_STATE,
                       dict(server._NICKNAME), dict(server._PRIORITY))
        server._STATE_FILE = os.path.join(self.tmp, "board-state.json")
        server._PROJECTS_STATE = self.state
        server._NICKNAME.clear()

    def tearDown(self):
        (server._STATE_FILE, server._PROJECTS_STATE, nicks, pris) = self._saved
        server._NICKNAME.clear()
        server._NICKNAME.update(nicks)
        server._PRIORITY.clear()
        server._PRIORITY.update(pris)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _transcript(self, sid, cwd="/nowhere"):
        d = os.path.join(self.state, "proj")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sid + ".jsonl")
        with open(p, "w") as fh:
            fh.write(json.dumps({"type": "user", "cwd": cwd,
                                 "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n")
        return p

    def test_a_name_you_typed_comes_back(self):
        self.assertTrue(server.set_nickname(_GOOD, "the auth refactor"))
        self.assertEqual(server._nickname(_GOOD), "the auth refactor")

    def test_no_nickname_is_none_and_never_an_empty_string(self):
        # One representation for "no Nickname", so every render site is one
        # truthiness check instead of a per-surface argument about which empty
        # means which (ADR 0026).
        self.assertIsNone(server._nickname(_GOOD))
        server.set_nickname(_GOOD, "x")
        server.set_nickname(_GOOD, "")
        self.assertIsNone(server._nickname(_GOOD))
        self.assertNotIn(_GOOD, server._NICKNAME)

    def test_an_empty_submit_is_the_delete(self):
        server.set_nickname(_GOOD, "wrong name")
        self.assertTrue(server.set_nickname(_GOOD, ""))
        self.assertIsNone(server._nickname(_GOOD))

    def test_the_cap_is_enforced_here_and_not_only_in_the_field(self):
        # The field is one client; this store outlives it. 24 is roughly what the
        # Focus header's first row can render beside a Workspace, so a name that
        # got past it would be one whose distinguishing half is never shown.
        self.assertFalse(server.set_nickname(_GOOD, "x" * (server.NICKNAME_MAX + 1)))
        self.assertIsNone(server._nickname(_GOOD))
        self.assertTrue(server.set_nickname(_GOOD, "x" * server.NICKNAME_MAX))

    def test_a_bad_session_id_is_refused(self):
        self.assertFalse(server.set_nickname("not-a-session", "name"))
        self.assertEqual(server._NICKNAME, {})

    def test_it_survives_a_restart(self):
        self._transcript(_GOOD)
        server.set_nickname(_GOOD, "the flaky test")
        server._NICKNAME.clear()          # as a fresh process starts
        server._load_state()
        self.assertEqual(server._nickname(_GOOD), "the flaky test")

    def test_load_drops_a_name_whose_transcript_is_gone(self):
        # The one condition under which deleting cannot lose anything: a Session
        # with no transcript cannot be resumed by anything. At load only — never
        # on save, and never in the path of a tap (ADR 0026).
        self._transcript(_GOOD)
        server.set_nickname(_GOOD, "kept")
        server.set_nickname(_UNKNOWN, "orphan")   # no transcript on disk
        server._NICKNAME.clear()
        server._load_state()
        self.assertEqual(server._nickname(_GOOD), "kept")
        self.assertIsNone(server._nickname(_UNKNOWN))

    def test_load_keeps_a_name_whose_cwd_is_merely_gone(self):
        # A deleted working directory is not the prune condition: the Session is
        # still resumable, so its Nickname is still wanted.
        self._transcript(_GONE, cwd=os.path.join(self.tmp, "deleted-worktree"))
        server.set_nickname(_GONE, "the dead worktree")
        server._NICKNAME.clear()
        server._load_state()
        self.assertEqual(server._nickname(_GONE), "the dead worktree")

    def test_the_prune_does_not_touch_priority(self):
        # Priority entries have outlived their transcripts forever and this
        # change is not the place to start collecting them: pruning a name is
        # safe because a nameless Session reads correctly, and that argument does
        # not transfer.
        server._PRIORITY.clear()
        server.set_priority(_UNKNOWN, 0)
        server._load_state()
        self.assertEqual(server._pri(_UNKNOWN), 0)


class NicknameBoardTests(unittest.TestCase):
    """`nickname` rides the item BESIDE `one`, never substituted into it — the
    fallback is a rendering decision and lives in one place in board.js."""

    def setUp(self):
        self._saved = (server.cached_runs, server.cached_foreign_runs,
                       server._tmux_server_down, dict(server._NICKNAME))
        server._tmux_server_down = lambda: False
        server.cached_foreign_runs = lambda: []
        server.cached_runs = lambda: [{
            "id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
            "status": "busy", "bridge": "", "updatedAt": 5000, "snippet": "last thing said",
        }]
        server._NICKNAME.clear()

    def tearDown(self):
        (server.cached_runs, server.cached_foreign_runs,
         server._tmux_server_down, nicks) = self._saved
        server._NICKNAME.clear()
        server._NICKNAME.update(nicks)

    def test_an_unnamed_session_ships_none_not_an_empty_string(self):
        focus = server._board()["focus"]
        self.assertIn("nickname", focus)
        self.assertIsNone(focus["nickname"])

    def test_a_named_session_ships_the_name_and_keeps_one(self):
        server._NICKNAME[_GOOD] = "the auth refactor"
        focus = server._board()["focus"]
        self.assertEqual(focus["nickname"], "the auth refactor")
        self.assertEqual(focus["one"], "last thing said")

    def test_naming_a_session_moves_the_etag(self):
        # Otherwise the phone that just typed the name would be handed a 304 and
        # show the old header until something unrelated churned.
        _, before = server._board_payload()
        server._NICKNAME[_GOOD] = "named"
        _, after = server._board_payload()
        self.assertNotEqual(before, after)

    def test_every_queue_row_carries_it_not_only_the_focus(self):
        # The question *which of these three* is asked on the QUEUE. `aiTitle` is
        # the field that stays Focus-only; this one may not, or the Nickname
        # would answer the question everywhere except where it is asked.
        server.cached_runs = lambda: [
            {"id": _RUN, "sessionId": _GOOD, "title": "x", "dir": "~/projects/x",
             "status": "busy", "bridge": "", "updatedAt": 5000, "snippet": "first"},
            {"id": "cl-2", "sessionId": _LIVE, "title": "y", "dir": "~/projects/y",
             "status": "waiting", "bridge": "", "updatedAt": 4000, "snippet": "second"},
        ]
        server._NICKNAME[_LIVE] = "the flaky test"
        board = server._board(_GOOD)
        rows = [it for g in ("upnext", "watching", "dormant", "snoozed") for it in board[g]]
        self.assertEqual([it["sessionId"] for it in rows], [_LIVE])
        self.assertEqual(rows[0]["nickname"], "the flaky test")
        # Beside `one`, never substituted into it — including on a Blocked lane,
        # where `one` is the **Ask**. Which of the two a row shows is board.js's
        # decision, made once; the server keeps shipping both.
        self.assertEqual(rows[0]["one"], "second")

    def test_a_foreign_row_carries_it_too(self):
        # A **Foreign Run** never takes the Focus, so the header's way in cannot
        # reach one — the long-press on the row is the only route, and a row with
        # no `nickname` could display a name it could never be given (ADR 0026).
        server.cached_foreign_runs = lambda: [{
            "id": "", "sessionId": _LIVE, "title": "byhand", "dir": "~/projects/mine",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "at the Mac",
        }]
        server._NICKNAME[_LIVE] = "mid-migration"
        row = server._board()["foreign"][0]
        self.assertEqual(row["nickname"], "mid-migration")
        self.assertEqual(row["one"], "at the Mac")

    def test_an_unnamed_foreign_row_ships_none_not_an_empty_string(self):
        server.cached_foreign_runs = lambda: [{
            "id": "", "sessionId": _LIVE, "title": "byhand", "dir": "~/projects/mine",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "at the Mac",
        }]
        row = server._board()["foreign"][0]
        self.assertIn("nickname", row)
        self.assertIsNone(row["nickname"])

    def test_the_foreign_row_still_carries_no_triage_state(self):
        # The Nickname is not the thin end of a wedge: it says what the Session is
        # called, where `pri` and `snooze` order a queue these rows are not in.
        server.cached_foreign_runs = lambda: [{
            "id": "", "sessionId": _LIVE, "title": "byhand", "dir": "~/projects/mine",
            "status": "waiting", "bridge": "", "updatedAt": 5000, "snippet": "at the Mac",
        }]
        row = server._board()["foreign"][0]
        self.assertNotIn("pri", row)
        self.assertNotIn("lane", row)


class NicknameApiTests(_HttpCase):
    """`/api/nickname` is ungated — same-origin + JSON, no token, beside
    `priority` and `snooze`. ADR 0007's token guards **Respond** *because
    Respond can approve tool calls*; a Nickname's blast radius is a wrong word
    you retype (ADR 0026)."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (server._STATE_FILE, dict(server._NICKNAME))
        server._STATE_FILE = os.path.join(self.tmp, "board-state.json")
        server._NICKNAME.clear()

    def tearDown(self):
        server._STATE_FILE, nicks = self._saved
        server._NICKNAME.clear()
        server._NICKNAME.update(nicks)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_token_needed(self):
        status, body = self._post("/api/nickname", {"sessionId": _GOOD, "nickname": "auth"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(server._nickname(_GOOD), "auth")

    def test_empty_clears_it(self):
        self._post("/api/nickname", {"sessionId": _GOOD, "nickname": "auth"})
        status, _ = self._post("/api/nickname", {"sessionId": _GOOD, "nickname": ""})
        self.assertEqual(status, 200)
        self.assertIsNone(server._nickname(_GOOD))

    def test_a_name_past_the_cap_is_refused(self):
        status, body = self._post(
            "/api/nickname", {"sessionId": _GOOD, "nickname": "x" * (server.NICKNAME_MAX + 1)})
        self.assertEqual(status, 400)
        self.assertIn(str(server.NICKNAME_MAX), body["message"])
        self.assertIsNone(server._nickname(_GOOD))

    def test_a_bad_session_id_is_refused(self):
        status, _ = self._post("/api/nickname", {"sessionId": "nope", "nickname": "auth"})
        self.assertEqual(status, 400)

    def test_it_is_still_a_same_origin_json_post(self):
        # Ungated is not unguarded: the CSRF posture is the same as every other
        # mutation's.
        status, _ = self._post("/api/nickname", {"sessionId": _GOOD, "nickname": "x"},
                               origin=False)
        self.assertEqual(status, 403)
        status, _ = self._post("/api/nickname", {"sessionId": _GOOD, "nickname": "x"},
                               ctype="text/plain")
        self.assertEqual(status, 415)


class ForeignBoardTests(unittest.TestCase):
    """A **Foreign Run** reaches the Board on its own key — visible, never
    drivable (ADR 0012). The triage lanes stay Managed-only, so nothing here can
    be **Blocked**, take the **Focus**, or enter **Rotation**."""

    # Deliberately the hostile shape: `waiting` (the status a permission prompt
    # leaves — the row the queue would most want) plus a well-formed `id` and
    # `attach` that the real `_foreign_rows` never produces. If the projection
    # ever became a copy, these would leak a pane handle onto the Board.
    FOREIGN = {"id": _RUN, "attach": "tmux attach -t x", "foreign": True, "pid": 400,
               "sessionId": _LIVE, "title": "an ask typed at the Mac",
               "dir": "~/projects/mine", "status": "waiting", "remote": True,
               "bridge": "session_abc", "updatedAt": 5000,
               "snippet": "the last thing it said", "starting": False}

    def setUp(self):
        self._saved = {n: getattr(server, n) for n in
                       ("cached_runs", "cached_foreign_runs", "_tmux_server_down")}
        server._tmux_server_down = lambda: False
        server.cached_runs = lambda: []
        server.cached_foreign_runs = lambda: [dict(self.FOREIGN)]

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(server, n, v)

    def test_it_lands_on_its_own_key_and_in_no_triage_lane(self):
        board = server._board()
        self.assertEqual([it["sessionId"] for it in board["foreign"]], [_LIVE])
        for lane in ("upnext", "watching", "snoozed", "dormant"):
            self.assertEqual(board[lane], [], lane + " must stay Managed-only")

    def test_it_never_takes_the_focus_even_as_the_only_live_run(self):
        # The empty board stays empty. There is no rendered pane to read this
        # Run's blocker from and no Respond to answer it with, so a card for it
        # would be a card you cannot use.
        self.assertIsNone(server._board()["focus"])

    def test_a_pinned_foreign_session_falls_back_instead_of_focusing_it(self):
        # ?focus= is matched against the Managed items alone, so a client that
        # somehow asks to pin a Foreign Run gets the rotation head — here, none.
        self.assertIsNone(server._board(_LIVE)["focus"])

    def test_the_counts_stay_managed_only(self):
        # The summary line reads "N need you"; a Foreign Run needs nothing from
        # the phone, because nothing on the phone can answer it.
        self.assertEqual(server._board()["counts"],
                         {"needYou": 0, "watching": 0, "dormant": 0, "snoozed": 0})

    def test_the_row_shows_what_it_can(self):
        row = server._board()["foreign"][0]
        self.assertEqual(row["workspace"], "mine")        # the Workspace, derived as a queue row's is
        self.assertEqual(row["dir"], "~/projects/mine")
        self.assertEqual(row["status"], "waiting")
        self.assertEqual(row["one"], "the last thing it said")
        self.assertEqual(row["updatedAt"], 5000)

    def test_the_row_carries_no_handle_onto_a_pane(self):
        # Dropped, not blanked: nothing that drives a Run can be handed this row
        # even by mistake, and `id` never reaches a client that might post it back.
        row = server._board()["foreign"][0]
        self.assertNotIn("runId", row)
        self.assertNotIn("id", row)
        self.assertNotIn("attach", row)
        self.assertNotIn("pid", row)                      # Transfer's handle stays server-side

    def test_the_bridge_rides_along_for_the_deep_link(self):
        # The one genuine route onto a Foreign Run from a phone: the Remote
        # Control bridge is Anthropic's cloud, not this terminal.
        self.assertEqual(server._board()["foreign"][0]["bridge"], "session_abc")

    def test_no_dir_means_no_workspace_rather_than_a_stand_in(self):
        # It used to fall back to the opening ask, which put a sentence in the
        # slot that names a project — a Foreign Run with no cwd read as a repo
        # called "an ask typed at the Mac". None, and the phone draws the dash.
        server.cached_foreign_runs = lambda: [dict(self.FOREIGN, dir="")]
        self.assertIsNone(server._board()["foreign"][0]["workspace"])

    def test_newest_activity_first(self):
        server.cached_foreign_runs = lambda: [
            dict(self.FOREIGN, sessionId=_GOOD, updatedAt=1000),
            dict(self.FOREIGN, sessionId=_LIVE, updatedAt=9000),
        ]
        self.assertEqual([it["sessionId"] for it in server._board()["foreign"]],
                         [_LIVE, _GOOD])

    def test_the_etag_moves_when_a_foreign_run_does(self):
        # The section is quiet, not stale: its state is in the hashed body, so a
        # Foreign Run going busy still invalidates the client's cached board.
        _, before = server._board_payload()
        server.cached_foreign_runs = lambda: [dict(self.FOREIGN, status="busy",
                                                   updatedAt=6000, snippet="a new tail")]
        _, after = server._board_payload()
        self.assertNotEqual(before, after)

    def test_the_etag_moves_when_a_foreign_run_ends(self):
        _, listed = server._board_payload()
        server.cached_foreign_runs = lambda: []
        _, gone = server._board_payload()
        self.assertNotEqual(listed, gone)


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


class RecoverApiTests(_HttpCase):
    """POST /api/recover: a sequential, partial-failure-tolerant bulk resume
    (slice 03). Re-runs Resume's guards fresh per member and returns a
    per-Session result array in input order — no real tmux/claude is spawned.

    _LIVE is live (fails the one-live-Run guard), _GONE's cwd is gone, _UNKNOWN
    has no transcript; _GOOD and _GOOD2 are resumable."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {"launch": server.launch_run, "list": server.list_runs,
                      "transcript": server._transcript_path, "cwd": server._session_cwd,
                      "invalidate": server.invalidate_runs}
        cls.launched = []          # resume_ids launch_run was called with, in order
        cls.invalidations = 0

        def fake_launch(workdir, prompt=None, task_id=None, resume_id=None):
            cls.launched.append(resume_id)
            return f"run-{len(cls.launched)}"

        def counting_invalidate():
            cls.invalidations += 1

        server.launch_run = fake_launch
        server.invalidate_runs = counting_invalidate
        # _LIVE has a live Managed Run, so _live_session_ids() == {_LIVE}.
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _LIVE}]
        server._transcript_path = lambda sid, *a, **k: (
            "/x/" + sid + ".jsonl" if sid in (_GOOD, _GOOD2, _LIVE, _GONE) else "")
        server._session_cwd = lambda sid, *a, **k: (
            "/no/such/dir/xyz" if sid == _GONE else os.path.dirname(__file__))
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
        server.invalidate_runs = cls._saved["invalidate"]

    def setUp(self):
        type(self).launched.clear()
        type(self)._saved["invalidate"]()   # clear the memoized walk for a fresh guard
        type(self).invalidations = 0         # count only the POST's own invalidation

    def test_all_valid_batch_resumes_each_in_order(self):
        status, body = self._post("/api/recover", {"sessionIds": [_GOOD, _GOOD2]})
        self.assertEqual(status, 200)
        self.assertEqual(body, [
            {"sessionId": _GOOD, "ok": True, "runId": "run-1"},
            {"sessionId": _GOOD2, "ok": True, "runId": "run-2"},
        ])
        self.assertEqual(self.launched, [_GOOD, _GOOD2])   # once per valid member, in order
        self.assertEqual(self.invalidations, 1)            # once total, after the loop

    def test_mixed_batch_skips_failures_and_keeps_going(self):
        ids = [_GOOD, _LIVE, _GOOD2, "not-a-uuid", _GONE, _UNKNOWN]
        status, body = self._post("/api/recover", {"sessionIds": ids})
        self.assertEqual(status, 200)                      # partial failure is still 200
        self.assertEqual(body, [
            {"sessionId": _GOOD, "ok": True, "runId": "run-1"},
            {"sessionId": _LIVE, "ok": False, "message": "already live"},
            {"sessionId": _GOOD2, "ok": True, "runId": "run-2"},
            {"sessionId": "not-a-uuid", "ok": False, "message": "invalid session id"},
            {"sessionId": _GONE, "ok": False, "message": "session's dir is gone"},
            {"sessionId": _UNKNOWN, "ok": False, "message": "no such session"},
        ])
        self.assertEqual(self.launched, [_GOOD, _GOOD2])   # only the valid members spawned
        self.assertEqual(self.invalidations, 1)            # still once total, not per member

    def test_non_string_members_are_failed_rows_not_a_400(self):
        status, body = self._post("/api/recover", {"sessionIds": [123, {"x": 1}, _GOOD]})
        self.assertEqual(status, 200)
        self.assertEqual(body, [
            {"sessionId": "", "ok": False, "message": "invalid session id"},
            {"sessionId": "", "ok": False, "message": "invalid session id"},
            {"sessionId": _GOOD, "ok": True, "runId": "run-1"},
        ])
        self.assertEqual(self.launched, [_GOOD])

    def test_empty_list_is_an_empty_array_200(self):
        status, body = self._post("/api/recover", {"sessionIds": []})
        self.assertEqual(status, 200)
        self.assertEqual(body, [])
        self.assertEqual(self.launched, [])

    def test_missing_sessionIds_is_a_400(self):
        status, body = self._post("/api/recover", {})
        self.assertEqual(status, 400)
        self.assertIn("must be a list", body["message"])
        self.assertEqual(self.launched, [])

    def test_non_list_sessionIds_is_a_400(self):
        status, body = self._post("/api/recover", {"sessionIds": _GOOD})
        self.assertEqual(status, 400)
        self.assertIn("must be a list", body["message"])
        self.assertEqual(self.launched, [])

    def test_cross_origin_is_blocked(self):
        status, _ = self._post("/api/recover", {"sessionIds": [_GOOD]}, origin=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.launched, [])


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

    def test_parse_tmux_panes_dedupes_grouped_session_rows(self):
        # An Attach (ADR 0011) opens a grouped session sharing the base session's
        # windows, so `list-panes -a` emits every Run's pane once per session in
        # the group. Without dedup the Board doubles every live Run. Keep first.
        out = ("ID1\x1f/dev/ttys001\x1f✳ Fix bug\x1fcap\x1f@4\n"
               "ID2\x1f/dev/ttys002\x1fDefault\x1f\x1f@5\n"
               "ID1\x1f/dev/ttys001\x1f✳ Fix bug\x1fcap\x1f@4\n"
               "ID2\x1f/dev/ttys002\x1fDefault\x1f\x1f@5\n")
        self.assertEqual(
            server._parse_tmux_panes(out),
            [("ID1", "ttys001", "✳ Fix bug", "cap", "@4"),
             ("ID2", "ttys002", "Default", "", "@5")],
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
    no-ops, exactly as close_run does.

    `self.pane` is what `capture-pane` hands back, because text is no longer
    typed at whatever happens to be on screen: it is ROUTED off a read of that
    screen (ADR 0020, `_text_route`). The default here is an ordinary input box,
    which is the plain route and the behaviour this class has always asserted."""

    def setUp(self):
        self._saved_list, self._saved_tmux = server.list_runs, server._tmux
        server.list_runs = lambda: [{"id": _RUN, "sessionId": _GOOD}]
        server.invalidate_runs()
        self.calls = []
        self.resolvable = True
        self.pane = _INPUT_PANE

        def fake(*args, check=True):
            self.calls.append(args)
            if args[0] == "list-panes":
                out = f"{_RUN}\x1f%7\n" if self.resolvable else ""
                return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
            if args[0] == "capture-pane":
                return types.SimpleNamespace(returncode=0, stdout=self.pane, stderr="")
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

    # --- free text is ROUTED, never typed at whatever is on screen (ADR 0020) --

    def test_free_text_steps_to_the_widgets_own_type_something_row_and_types_into_it(self):
        # MEASURED, on 2.1.220. The fixture's cursor is on row 0 and
        # `Type something.` is row 2 — the same count ADR 0020's table measured as
        # landing there. Then the text goes STRAIGHT in: the highlighted row IS
        # the buffer. An Enter to "open" it rejects the tool use instead (the live
        # probe returned `The user doesn't want to proceed with this tool use`),
        # so the ONLY Enter is the submit at the end.
        self.pane = _capture("ask_multi.pane")
        self.assertTrue(server.respond_run(_RUN, "a considered answer"))
        sends = self._sends()
        typed = ("send-keys", "-t", "%7", "-l", "a considered answer")
        self.assertEqual(sends, [("send-keys", "-t", "%7", "Down"),
                                 ("send-keys", "-t", "%7", "Down"),
                                 typed,
                                 ("send-keys", "-t", "%7", "Enter")])

    def test_every_lead_in_key_is_its_own_send_keys_call(self):
        # Also measured: `send-keys Down Down Enter` — one call, three key names —
        # had both Downs SILENTLY DROPPED by the live TUI, and the Enter then
        # answered with the highlighted row. tmux writes a batched call as one
        # burst and the TUI reads it as a single keypress. One key per call is the
        # Ask Set's tap path, and it is the one that works.
        self.pane = _capture("ask_multi.pane")
        server.respond_run(_RUN, "prose")
        for c in self._sends():
            self.assertLessEqual(len(c), 5)          # ("send-keys","-t",pane,[-l,]arg)
            self.assertEqual(len([a for a in c[3:] if a != "-l"]), 1)

    def test_the_step_count_is_measured_in_rows_on_every_widget_shape(self):
        # Rows, not options: `Chat about this` and `Type something` are ordinary
        # numbered rows, and counting in options is the wrong-answer table.
        for name, downs in (("ask_single.pane", 3), ("ask_toggled.pane", 2)):
            with self.subTest(name):
                self.calls, self.pane = [], _capture(name)
                self.assertTrue(server.respond_run(_RUN, "prose"))
                self.assertEqual(self._sends()[:downs],
                                 [("send-keys", "-t", "%7", "Down")] * downs)

    def test_a_widget_with_no_type_something_row_refuses_without_consent(self):
        pane = _capture("ask_multi.pane").replace("Type something.", "Ponder this.")
        self.pane = pane
        self.assertFalse(server.respond_run(_RUN, "prose"))
        self.assertEqual(self._sends(), [])   # not one key, least of all the text

    def test_and_with_consent_it_escapes_first_then_types(self):
        self.pane = _capture("ask_multi.pane").replace("Type something.", "Ponder this.")
        self.assertTrue(server.respond_run(_RUN, "prose", cancel_ask=True))
        sends = self._sends()
        esc = ("send-keys", "-t", "%7", "Escape")
        typed = ("send-keys", "-t", "%7", "-l", "prose")
        self.assertIn(esc, sends)
        self.assertIn(typed, sends)
        self.assertLess(sends.index(esc), sends.index(typed))

    def test_an_unpainted_cursor_takes_the_esc_route_rather_than_counting_from_zero(self):
        # The row is there; the cursor is not. A step count from a defaulted 0 is
        # ADR 0021's archetype, so the route degrades to the honest destructive
        # one — which still needs consent, and still says so.
        self.pane = _capture("ask_multi.pane").replace("❯ 1.", "  1.")
        self.assertFalse(server.respond_run(_RUN, "prose"))
        self.assertEqual(self._sends(), [])
        self.assertTrue(server.respond_run(_RUN, "prose", cancel_ask=True))
        self.assertIn(("send-keys", "-t", "%7", "Escape"), self._sends())

    def test_consent_can_only_permit_the_esc_route_never_select_it(self):
        self.pane = _capture("ask_multi.pane")
        self.assertTrue(server.respond_run(_RUN, "prose", cancel_ask=True))
        self.assertNotIn(("send-keys", "-t", "%7", "Escape"), self._sends())
        self.assertEqual(self._sends()[:2], [("send-keys", "-t", "%7", "Down")] * 2)

    def test_an_unreadable_pane_sends_nothing_at_all(self):
        # THE BUG BEING FIXED MUST NOT SURVIVE AS THE FALLBACK: a blind
        # `send-keys -l` into a screen nobody read is what this whole slice is
        # about (ADR 0021).
        self.pane = ""
        self.assertFalse(server.respond_run(_RUN, "prose"))
        self.assertFalse(server.respond_run(_RUN, "prose", cancel_ask=True))
        self.assertEqual(self._sends(), [])

    def test_the_keys_path_takes_no_capture_at_all(self):
        # An option tap stays ONE round trip: routing is a property of free text.
        server.respond_run(_RUN, "", ["down", "enter"])
        self.assertEqual([c for c in self.calls if c[0] == "capture-pane"], [])


class RespondUnsentGuardTests(_HttpCase):
    """`POST /api/respond` refuses to blind-append onto a box that already holds
    unsent text — and must read that box the same way `/api/board` does.

    It did not. `_pane_input` on a widget frame hands back the QUESTION (the
    widget's body sits between the same horizontal rules the input box does), so
    the board said `pendingInput: ""` while this endpoint answered 409 with the
    question as the offending draft. The phone's prompt for that 409 is "this
    session already has unsent text … send anyway?", and OK means `force` — text
    sent into a live selector, which ADR 0020 measured as silently discarded and
    answered by whatever row the cursor happened to be on. Two endpoints reading
    one screen two ways is worse than either answer.
    """

    @classmethod
    def setUpClass(cls):
        cls._saved = {n: getattr(server, n) for n in
                      ("TOKEN", "_pane_contents", "respond_run")}
        server.TOKEN = "a-shared-secret"
        cls.sent = []
        server.respond_run = lambda rid, text="", keys=None, cancel_ask=False: (
            cls.sent.append((rid, text, keys, cancel_ask)) or True)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        for n, v in cls._saved.items():
            setattr(server, n, v)

    def setUp(self):
        type(self).sent = []

    def _respond(self, text="a considered reply"):
        return self._post("/api/respond",
                          {"runId": _RUN, "text": text, "token": "a-shared-secret"})

    def test_a_widget_frame_is_not_reported_as_unsent_text(self):
        """A widget's body is not a draft — the ⚠ and its clear button may never
        appear over a live question (that was ADR 0020's original bug).

        WHAT THIS USED TO ASSERT, AND WHY IT WAS WRONG. It checked `200` and
        `assertTrue(self.sent)` and stopped there, which enshrined the hole the
        rewording opened: suppressing the false ⚠ removed the ONLY thing standing
        between a free-text send and a bare `-l` + `Enter` into a live selector.
        The old 409 was badly worded (it called the question "unsent text") but it
        did stop the send; after the rewording nothing did, and the endpoint
        answered the Ask with whatever row the cursor sat on. So the assertion is
        not "it went through" — it is that it went through ROUTED."""
        server._pane_contents = lambda rid: _capture("ask_multi.pane")
        status, body = self._respond()
        self.assertEqual(status, 200, body)
        self.assertTrue(self.sent)
        # The route is the point: this frame HAS a `Type something` row, so the
        # send is safe precisely because `respond_run` will step to it. Asserted
        # here rather than trusted, because this endpoint is where a blind send
        # would re-enter.
        self.assertEqual(
            server._text_route(server._read_pane(_capture("ask_multi.pane"))).route,
            "affordance")

    def test_no_text_send_can_reach_a_bare_send_keys_while_a_widget_is_up(self):
        # The finding in one assertion, over every widget capture on disk and the
        # two ways a frame can be unreadable: a text send while a widget owns the
        # screen is either ROUTED or REFUSED, and 200-with-a-blind-`-l` is not one
        # of the outcomes. `_pane_input` is deliberately still fooled by these
        # frames (that is the hazard), so the guard cannot be the unsent read.
        for name in ("ask_multi", "ask_single", "ask_toggled"):
            with self.subTest(name):
                server._pane_contents = lambda rid, n=name: _capture(n + ".pane")
                type(self).sent = []
                status, body = self._respond()
                route = server._text_route(server._read_pane(_capture(name + ".pane")))
                self.assertEqual(route.route, "affordance", body)
                self.assertEqual(status, 200, body)
        # ...and where the routing cannot be worked out, the send is refused with
        # a named reason rather than typed anyway.
        for pane, why in ((_capture("ask_multi.pane").replace("Type something.", "Ponder."), "esc"),
                          (_capture("ask_multi.pane").replace("❯ 1.", "  1."), "esc"),
                          ("", "refuse")):
            with self.subTest(why):
                server._pane_contents = lambda rid, p=pane: p
                type(self).sent = []
                status, body = self._respond()
                self.assertEqual(status, 409)
                self.assertEqual(body["route"], why)
                self.assertEqual(self.sent, [])

    def test_a_real_draft_still_stops_the_send(self):
        # The guard itself is intact — only what counts as "the box" changed.
        server._pane_contents = lambda rid: _INPUT_PANE
        status, body = self._respond()
        self.assertEqual(status, 409)
        self.assertEqual(body["existing"], "draft a reply but do not send")
        self.assertEqual(self.sent, [])

    # --- the destructive route is never the silent default (ADR 0020) ---------

    def _no_row(self):
        return _capture("ask_multi.pane").replace("Type something.", "Ponder this.")

    def test_a_send_that_would_cancel_the_ask_stops_and_says_so_in_words(self):
        server._pane_contents = lambda rid: self._no_row()
        status, body = self._respond()
        self.assertEqual(status, 409)
        self.assertEqual(body["route"], "esc")
        self.assertEqual(body["reason"], "no-row")
        # Plain language, not a code: the phone puts this in front of a person.
        self.assertIn("CANCELS the question", body["message"])
        self.assertEqual(self.sent, [])

    def test_consent_is_its_own_field_and_reaches_respond_run(self):
        # NOT `force`. Agreeing to append below a draft is a different act from
        # agreeing to cancel a question, so it cannot be the same flag.
        server._pane_contents = lambda rid: self._no_row()
        status, _ = self._post("/api/respond", {"runId": _RUN, "text": "prose",
                                                "token": "a-shared-secret", "force": True})
        self.assertEqual(status, 409)
        status, _ = self._post("/api/respond", {"runId": _RUN, "text": "prose",
                                                "token": "a-shared-secret", "cancelAsk": True})
        self.assertEqual(status, 200)
        self.assertEqual(self.sent[-1][3], True)

    def test_an_unreadable_screen_refuses_with_no_send_anyway_offered(self):
        server._pane_contents = lambda rid: ""
        status, body = self._respond()
        self.assertEqual(status, 409)
        self.assertEqual(body["route"], "refuse")
        self.assertEqual(self.sent, [])
        # ...and consent does not buy past it either: there is no route at all.
        status, _ = self._post("/api/respond", {"runId": _RUN, "text": "prose",
                                                "token": "a-shared-secret", "cancelAsk": True})
        self.assertEqual(status, 409)
        self.assertEqual(self.sent, [])

    def test_a_widget_with_the_row_needs_no_consent_and_no_prompt(self):
        server._pane_contents = lambda rid: _capture("ask_multi.pane")
        status, _ = self._respond()
        self.assertEqual(status, 200)
        self.assertEqual(self.sent[-1][3], False)

    def test_the_keys_only_path_is_not_gated_by_any_of_this(self):
        # An option tap on a widget frame: no text, so no route, no capture, no
        # prompt — the Ask Set's own path is untouched.
        server._pane_contents = lambda rid: self._no_row()
        status, _ = self._post("/api/respond", {"runId": _RUN, "keys": ["down", "enter"],
                                                "token": "a-shared-secret"})
        self.assertEqual(status, 200)


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
        # An EMPTY box, read off a frame we actually captured — rules with
        # nothing between them. (This test used to pass `""` for the whole pane,
        # which is `_pane_contents`' "the capture failed" value: it asserted that
        # an unread screen still gets keystrokes, and the case below is now that
        # case, split out and refused.)
        server._pane_contents = lambda rid: _INPUT_PANE.replace(
            "❯ draft a reply but do not send", "❯ ")
        self.assertTrue(server.clear_input(_RUN))
        send = next(c for c in self.calls if c[0] == "send-keys")
        self.assertEqual(send[:3], ("send-keys", "-t", "%3"))
        self.assertEqual(list(send[3:]), ["BSpace"] * 16)   # 0 content + 16 margin

    def test_a_pane_that_could_not_be_read_sends_nothing_at_all(self):
        # `_pane_contents` returns '' on ANY failure, so this is "nobody looked".
        # Sending the margin anyway is a keystroke produced by a failed read —
        # the exact rule ADR 0020 is written around — and 16 BSpace at an
        # unknown screen is not harmless just because it is a small number.
        server._pane_contents = lambda rid: ""
        self.assertFalse(server.clear_input(_RUN))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

    def test_a_numbered_list_in_ordinary_output_does_not_block_the_clear(self):
        # `_parse_selector` fires on any two consecutive numbered lines, and
        # Claude writes numbered lists constantly. Refusing on that would break
        # the clear button on ordinary frames — a guess is still a guess when it
        # errs toward refusing, so only the structural widget check refuses.
        server._pane_contents = lambda rid: "\n".join([
            "1. first thing", "2. second thing", "3. third thing", "",
            "─" * 60, "❯ half a reply", "─" * 60])
        self.assertTrue(server.clear_input(_RUN))
        self.assertTrue([c for c in self.calls if c[0] == "send-keys"])

    def test_backspace_count_is_the_box_length_plus_the_margin(self):
        # A frame that really does carry a box (the read of its CONTENT is what
        # is stubbed here); a box-less frame is refused outright, above.
        server._pane_contents = lambda rid: _INPUT_PANE
        server._pane_input = lambda text: "draft"   # 5 chars
        server.clear_input(_RUN)
        send = next(c for c in self.calls if c[0] == "send-keys")
        self.assertEqual(list(send[3:]), ["BSpace"] * (5 + 16))
        self.assertTrue(all(k == "BSpace" for k in send[3:]))

    def test_a_widget_on_screen_refuses_the_clear_outright(self):
        # There is no input box to empty while a widget owns the screen, and the
        # over-count margin would fire BSpace straight into a live selector —
        # ADR 0020's worst near-miss, which the false ⚠ warning had already put
        # a button next to.
        server._pane_contents = lambda rid: _capture("ask_multi.pane")
        self.assertFalse(server.clear_input(_RUN))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

    def test_a_frame_with_no_input_box_at_all_refuses_the_clear(self):
        # `_pane_input` returns '' for TWO different things and says so
        # deliberately: "no box on screen" and "an empty box". Only the second is
        # safe to backspace into. A captured frame with no rules on it carries no
        # box, so the margin would fire 16 BSpace at whatever IS there — the same
        # act the unread-pane case above refuses, and 16 BSpace at an unknown
        # screen is not harmless just because it is a small number (ADR 0021: a
        # failed or absent reading may never produce an action).
        server._pane_contents = lambda rid: "ordinary output\nno rules on this frame\n"
        self.assertFalse(server.clear_input(_RUN))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

    def test_a_boxless_permission_menu_does_not_get_the_margin(self):
        # The same absence, on the frame where it costs most: a live menu with no
        # input box under it. This is NOT the deliberate non-refusal on
        # `selector` — that one is justified by false positives, since any two
        # consecutive numbered lines of Claude's own prose read as a menu. "No
        # rules were found" has no false positive to trade against: it is a
        # positive finding that there is no box.
        server._pane_contents = lambda rid: "\n".join([
            "Bash(rm -rf build)", "", "❯ 1. Yes", "  2. Yes, and don't ask again",
            "  3. No, and tell Claude what to do differently"])
        self.assertFalse(server.clear_input(_RUN))
        self.assertEqual([c for c in self.calls if c[0] == "send-keys"], [])

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
    def setUp(self):
        self._saved = server.list_runs
        server.invalidate_runs()

    def tearDown(self):
        server.list_runs = self._saved
        server.invalidate_runs()

    def test_collects_nonblank_session_ids(self):
        server.list_runs = lambda: [
            {"sessionId": "s1"}, {"sessionId": ""}, {"sessionId": "s2"},
        ]
        self.assertEqual(server._live_session_ids(), {"s1", "s2"})

    def test_a_session_live_in_a_foreign_terminal_is_guarded(self):
        # The fork bug: this set used to be fed from tmux panes alone, so a
        # Session live in iTerm was resumable and its transcript forked, against
        # CONTEXT.md's "at most one live Run per Session" (ADR 0012).
        server.list_runs = lambda: [
            {"sessionId": _GOOD},
            {"sessionId": _LIVE, "foreign": True, "id": "", "pid": 400},
        ]
        self.assertEqual(server._live_session_ids(), {_GOOD, _LIVE})


class ForeignResumeGuardTests(_HttpCase):
    """/api/resume must refuse a Session that is live in another terminal. This
    *tightens* resume — a Session that was wrongly resumable is now correctly
    refused (ADR 0012)."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {"launch": server.launch_run, "list": server.list_runs,
                      "transcript": server._transcript_path, "cwd": server._session_cwd}
        cls.calls = []
        server.launch_run = lambda *a, **k: (cls.calls.append((a, k)), _RUN)[1]
        # nothing of ours is running; _LIVE is live only as a Foreign Run
        server.list_runs = lambda: [
            {"id": "", "sessionId": _LIVE, "foreign": True, "pid": 400, "attach": ""}]
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

    def test_resume_refuses_a_session_live_in_a_foreign_terminal(self):
        status, body = self._post("/api/resume", {"sessionId": _LIVE})
        self.assertEqual(status, 400)
        self.assertIn("already live", body["message"])
        self.assertEqual(self.calls, [])   # no second Run on that transcript

    def test_resume_of_a_session_with_no_live_run_still_works(self):
        status, body = self._post("/api/resume", {"sessionId": _GOOD})
        self.assertEqual(status, 200)
        self.assertEqual(body["runId"], _RUN)


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


class FencedCodeTests(unittest.TestCase):
    """A fenced block is its own element, not swept into the paragraph collector."""

    def test_a_fenced_block_becomes_one_pre(self):
        html_out = server._md_to_html("```\nfoo()\nbar()\n```")
        self.assertEqual(html_out, "<pre><code>foo()\nbar()</code></pre>")

    def test_newlines_inside_a_fence_survive(self):
        html_out = server._md_to_html("```\na\n\nb\n```")
        self.assertIn("a\n\nb", html_out)
        self.assertNotIn("<p>", html_out)

    def test_a_tilde_fence_works_too(self):
        self.assertEqual(server._md_to_html("~~~\nx\n~~~"), "<pre><code>x</code></pre>")

    def test_an_info_string_is_dropped_not_rendered(self):
        html_out = server._md_to_html("```python\nx = 1\n```")
        self.assertEqual(html_out, "<pre><code>x = 1</code></pre>")

    def test_an_info_string_reaches_no_attribute_however_hostile(self):
        # The renderer emits no attributes at all, so ADR 0006's innerHTML sink
        # gains no new shape from a transcript we do not own.
        html_out = server._md_to_html('```a"b <c> onload=x\nx\n```')
        self.assertEqual(html_out, "<pre><code>x</code></pre>")

    def test_a_longer_fence_carries_inner_fences_whole(self):
        # How a transcript quotes markdown at us: a 5-backtick wrapper around a
        # 3-backtick block. The inner fences must not close the outer one.
        html_out = server._md_to_html("`````\nmd:\n```\ninner\n```\n`````")
        self.assertEqual(
            html_out, "<pre><code>md:\n```\ninner\n```</code></pre>")

    def test_a_shorter_run_does_not_close_a_longer_fence(self):
        html_out = server._md_to_html("````\na\n```\nb\n````")
        self.assertEqual(html_out, "<pre><code>a\n```\nb</code></pre>")

    def test_a_longer_run_does_close_a_shorter_fence(self):
        html_out = server._md_to_html("```\na\n`````")
        self.assertEqual(html_out, "<pre><code>a</code></pre>")

    def test_an_indented_fence_sheds_its_own_indent(self):
        html_out = server._md_to_html("  ```\n  x = 1\n    deeper\n  ```")
        self.assertEqual(html_out, "<pre><code>x = 1\n  deeper</code></pre>")

    def test_markdown_inside_a_fence_stays_literal(self):
        html_out = server._md_to_html("```\n# not a heading\n- not a list\n**not bold**\n```")
        self.assertNotIn("<h3>", html_out)
        self.assertNotIn("<ul>", html_out)
        self.assertNotIn("<strong>", html_out)
        self.assertIn("**not bold**", html_out)

    def test_html_inside_a_fence_is_escaped(self):
        html_out = server._md_to_html("```\n<script>alert(1)</script>\n```")
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_a_table_inside_a_fence_is_not_a_table(self):
        html_out = server._md_to_html("```\n| a | b |\n|---|---|\n| 1 | 2 |\n```")
        self.assertNotIn("<table>", html_out)
        self.assertTrue(html_out.startswith("<pre><code>"))

    def test_an_unterminated_fence_takes_the_rest_of_the_text(self):
        html_out = server._md_to_html("```\nfoo\nbar")
        self.assertEqual(html_out, "<pre><code>foo\nbar</code></pre>")

    def test_prose_around_a_fence_stays_prose(self):
        html_out = server._md_to_html("before\n\n```\ncode\n```\n\nafter")
        self.assertEqual(
            html_out, "<p>before</p>\n<pre><code>code</code></pre>\n<p>after</p>")

    def test_a_fence_after_prose_without_a_blank_line_still_breaks_out(self):
        html_out = server._md_to_html("before\n```\ncode\n```")
        self.assertEqual(html_out, "<p>before</p>\n<pre><code>code</code></pre>")

    def test_an_empty_fence_emits_an_empty_pre(self):
        self.assertEqual(server._md_to_html("```\n```"), "<pre><code></code></pre>")

    def test_two_fences_are_two_blocks(self):
        html_out = server._md_to_html("```\na\n```\n```\nb\n```")
        self.assertEqual(
            html_out, "<pre><code>a</code></pre>\n<pre><code>b</code></pre>")

    def test_a_tilde_fence_is_not_closed_by_a_backtick_fence(self):
        html_out = server._md_to_html("~~~\n```\n~~~")
        self.assertEqual(html_out, "<pre><code>```</code></pre>")

    def test_inline_code_still_renders_outside_a_fence(self):
        self.assertEqual(server._md_to_html("a `b` c"), "<p>a <code>b</code> c</p>")


if __name__ == "__main__":
    unittest.main()
