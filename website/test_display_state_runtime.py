"""Behavioral regression suite for the display's production JavaScript functions."""

import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase


@skipUnless(shutil.which("node"), "Node.js is required for display runtime tests")
class DisplayStateRuntimeTests(SimpleTestCase):
    def test_schedule_and_assignment_runtime(self):
        test_file = Path(__file__).with_name("display_state.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(test_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
