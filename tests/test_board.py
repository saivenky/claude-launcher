"""Puts the board client's tests on the same command as everything else.

`web/board.js` carries the Focus discipline (CONTEXT.md: **Focus**,
**Rotation**) — it decides which Run you are looking at and when that changes —
and none of it is visible to the Python suite. tests/test_board.js drives it
under node; this shim keeps that on `python3 -m unittest discover -s tests`, so
it cannot rot unrun behind a command nobody remembers to type.
"""

import os
import shutil
import subprocess
import unittest

_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_board.js")


@unittest.skipUnless(shutil.which("node"), "node not installed — board client tests skipped")
class BoardClientTest(unittest.TestCase):
    def test_focus_discipline(self):
        p = subprocess.run([shutil.which("node"), _JS], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            self.fail("tests/test_board.js failed:\n" + p.stdout + p.stderr)
