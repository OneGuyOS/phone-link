"""The version shown to users and packaged in the metadata."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import phone_link

ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    def test_package_version(self) -> None:
        self.assertEqual(phone_link.__version__, "1.0.1")

    def test_command_line_version(self) -> None:
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-m", "phone_link", "--version"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Phone Link 1.0.1")
        self.assertEqual(result.stderr.strip(), "")


if __name__ == "__main__":
    unittest.main()
