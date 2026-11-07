"""App streaming: package parsing and the background app-list loader."""

from __future__ import annotations

import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from phone_link.apps import (
    AppListLoader,
    is_valid_package,
    package_list_command,
    parse_packages,
)


class PackageParsingTests(unittest.TestCase):
    def test_parses_launchable_activity_components(self) -> None:
        output = "\n".join(
            (
                "122 activities found:",
                "  Activity #0:",
                "    priority=0 preferredOrder=0 match=0x108000 isDefault=true",
                "    com.android.settings/.Settings",
                "  Activity #1:",
                "    com.google.android.apps.maps/com.google.android.maps.MapsActivity",
                "  Activity #2:",
                "    com.android.settings/.SubSettings",
            )
        )
        self.assertEqual(
            parse_packages(output),
            ["com.android.settings", "com.google.android.apps.maps"],
        )

    def test_parses_package_lines_as_a_fallback(self) -> None:
        output = "\n".join(
            (
                "package:com.zebra.app",
                "package:com.alpha.app",
                "package:com.alpha.app",
                "",
            )
        )
        self.assertEqual(
            parse_packages(output),
            ["com.alpha.app", "com.zebra.app"],
        )

    def test_ignores_adb_warnings_and_non_package_lines(self) -> None:
        output = "\n".join(
            (
                "Error: java.lang.SecurityException: Shell does not have permission",
                " com.android.server.am.ActivityManagerService.handleIncomingUser",
                "package:com.example.good",
                "package:not a package",
                "package:nodot",
                "package:",
                "  Activity #0:",
                "    not a component",
            )
        )
        self.assertEqual(parse_packages(output), ["com.example.good"])

    def test_empty_output_has_no_packages(self) -> None:
        self.assertEqual(parse_packages(""), [])

    def test_package_name_validation(self) -> None:
        self.assertTrue(is_valid_package("com.example.app"))
        self.assertTrue(is_valid_package(" com.example.app_2 "))
        self.assertFalse(is_valid_package(""))
        self.assertFalse(is_valid_package("nodot"))
        self.assertFalse(is_valid_package(".com.example"))
        self.assertFalse(is_valid_package("com..example"))
        self.assertFalse(is_valid_package("com.example app"))

    def test_package_list_command(self) -> None:
        self.assertEqual(
            package_list_command("/usr/bin/adb", "SERIAL"),
            [
                "/usr/bin/adb",
                "-s",
                "SERIAL",
                "shell",
                "cmd",
                "package",
                "query-activities",
                "--brief",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
            ],
        )


class AppListLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_script(self, body: str) -> str:
        directory = tempfile.mkdtemp(prefix="phone-link-apps-")
        path = Path(directory) / "fake-adb"
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
        return str(path)

    def run_loader(self, adb_path: str, timeout: float = 5.0):
        loader = AppListLoader(adb_path, "SERIAL")
        outcome: dict[str, object] = {}
        loop = QEventLoop()

        def loaded(packages) -> None:
            outcome["packages"] = packages
            loop.quit()

        def failed(message: str) -> None:
            outcome["error"] = message
            loop.quit()

        loader.loaded.connect(loaded)
        loader.failed.connect(failed)
        QTimer.singleShot(int(timeout * 1000), loop.quit)
        loader.start()
        loop.exec()
        loader.stop()
        loader.deleteLater()
        return outcome

    def test_loader_reads_packages_from_adb(self) -> None:
        script = self.make_script(
            "printf '2 activities found:\\n"
            "    com.example.demo/.Main\\n"
            "    com.example.other/.Main\\n'"
        )
        outcome = self.run_loader(script)
        self.assertEqual(
            outcome.get("packages"),
            ["com.example.demo", "com.example.other"],
        )

    def test_loader_reports_an_empty_or_failed_listing(self) -> None:
        script = self.make_script("echo 'adb: no devices' >&2; exit 1")
        outcome = self.run_loader(script)
        self.assertIn("adb: no devices", str(outcome.get("error", "")))

    def test_loader_reports_a_missing_adb(self) -> None:
        outcome = self.run_loader("/nonexistent/phone-link-adb")
        self.assertIn("Could not run adb", str(outcome.get("error", "")))

    def test_loader_can_be_stopped(self) -> None:
        script = self.make_script("sleep 5; echo 'package:com.example.late'")
        loader = AppListLoader(script, "SERIAL")
        loader.start()
        loader.stop()
        # The stopped loader must not report the script's eventual output.
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        self.assertIsNone(loader._process)


if __name__ == "__main__":
    unittest.main()
