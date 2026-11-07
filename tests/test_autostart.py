"""Unit tests for the XDG autostart entry."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phone_link.autostart import (
    autostart_entry,
    autostart_path,
    is_enabled,
    set_enabled,
)


class AutostartTests(unittest.TestCase):
    def test_path_follows_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}):
                self.assertEqual(
                    autostart_path(),
                    Path(folder) / "autostart" / "phone-link.desktop",
                )

    def test_path_defaults_to_dot_config(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(
                os.environ, {"HOME": home, "XDG_CONFIG_HOME": ""}
            ):
                self.assertEqual(
                    autostart_path(),
                    Path(home) / ".config" / "autostart" / "phone-link.desktop",
                )

    def test_entry_runs_this_package_from_its_folder(self) -> None:
        entry = autostart_entry(
            executable="/usr/bin/python3", working_dir="/opt/phone link"
        )
        self.assertIn('Exec="/usr/bin/python3" -m phone_link', entry)
        self.assertIn("Path=/opt/phone link", entry)
        self.assertIn("Type=Application", entry)
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)
        self.assertTrue(entry.endswith("\n"))

    def test_exec_quoting(self) -> None:
        entry = autostart_entry(
            executable='/tmp/my "phone" $dir/back\\slash',
            working_dir="",
        )
        self.assertIn(
            'Exec="/tmp/my \\"phone\\" \\$dir/back\\\\slash" -m phone_link',
            entry,
        )
        self.assertNotIn("Path=", entry)

    def test_enable_and_disable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "autostart" / "phone-link.desktop"
            self.assertFalse(is_enabled(path))
            set_enabled(
                True,
                path,
                executable="/usr/bin/python3",
                working_dir="/src",
            )
            self.assertTrue(is_enabled(path))
            self.assertIn(
                "Exec=", path.read_text(encoding="utf-8")
            )
            self.assertFalse(path.with_name("phone-link.desktop.tmp").exists())
            set_enabled(False, path)
            self.assertFalse(is_enabled(path))
            set_enabled(False, path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
