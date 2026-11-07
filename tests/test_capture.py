"""Unit tests for screenshots, recording arguments and their ADB plumbing."""

from __future__ import annotations

import re
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent

from phone_link.capture import (
    PNG_SIGNATURE,
    ScreenshotCapture,
    default_recording_folder,
    display_token,
    display_unique_id,
    dumpsys_display_command,
    dumpsys_surfaceflinger_command,
    screencap_command,
    timestamped_name,
    unique_path,
)
from phone_link.scrcpy import ScrcpySession, build_scrcpy_args


def _app() -> QCoreApplication:
    """Reuse the suite's application; create one only when running alone."""

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


FAKE_ADB = Path(__file__).resolve().parent / "fakes" / "adb"
SERIAL = "MOCK123"

DISPLAY_DUMP = """Logical Displays: size=2
  Display 0:
    mDisplayId=0
    mPrimaryDisplayDevice=Built-in Screen(local:1234567890)
    mBaseDisplayInfo=DisplayInfo{"Built-in Screen", displayId 0, uniqueId "local:1234567890"}
  Display 114:
    mDisplayId=114
    mPrimaryDisplayDevice=scrcpy(virtual:com.android.shell,2000,scrcpy,107)
    mBaseDisplayInfo=DisplayInfo{"scrcpy", displayId 114, uniqueId "virtual:com.android.shell,2000,scrcpy,107"}
"""

SURFACE_DISPLAY_DUMP = """Display 1234567890 (HWC display 0): port=195 pnpId=SAM displayName="screen"
Display 11529215047395875271 (Virtual display): displayName="scrcpy" uniqueId="virtual:com.android.shell,2000,scrcpy,107"
"""


class CaptureCommandTests(unittest.TestCase):
    def test_screencap_without_display(self) -> None:
        self.assertEqual(
            screencap_command("/usr/bin/adb", "SERIAL"),
            ["/usr/bin/adb", "-s", "SERIAL", "exec-out", "screencap", "-p"],
        )

    def test_screencap_with_display_token(self) -> None:
        self.assertEqual(
            screencap_command("/usr/bin/adb", "SERIAL", 123),
            ["/usr/bin/adb", "-s", "SERIAL", "exec-out", "screencap", "-d", "123", "-p"],
        )

    def test_dumpsys_commands(self) -> None:
        self.assertEqual(
            dumpsys_display_command("adb", "S"),
            ["adb", "-s", "S", "shell", "dumpsys", "display"],
        )
        self.assertEqual(
            dumpsys_surfaceflinger_command("adb", "S"),
            ["adb", "-s", "S", "shell", "dumpsys", "SurfaceFlinger", "--display-id"],
        )


class DisplayParsingTests(unittest.TestCase):
    def test_unique_id_for_virtual_display(self) -> None:
        self.assertEqual(
            display_unique_id(DISPLAY_DUMP, 114),
            "virtual:com.android.shell,2000,scrcpy,107",
        )

    def test_unique_id_for_main_display(self) -> None:
        self.assertEqual(display_unique_id(DISPLAY_DUMP, 0), "local:1234567890")

    def test_unknown_display(self) -> None:
        self.assertIsNone(display_unique_id(DISPLAY_DUMP, 999))

    def test_display_token(self) -> None:
        self.assertEqual(
            display_token(
                SURFACE_DISPLAY_DUMP, "virtual:com.android.shell,2000,scrcpy,107"
            ),
            11529215047395875271,
        )

    def test_display_token_missing(self) -> None:
        self.assertIsNone(display_token(SURFACE_DISPLAY_DUMP, "virtual:other"))
        self.assertIsNone(display_token(SURFACE_DISPLAY_DUMP, ""))


class NameTests(unittest.TestCase):
    def test_timestamped_png(self) -> None:
        moment = datetime(2026, 11, 7, 15, 4, 5)
        self.assertEqual(timestamped_name(moment), "phone-link-20261107-150405.png")

    def test_timestamped_mp4(self) -> None:
        moment = datetime(2026, 11, 7, 15, 4, 5)
        self.assertEqual(
            timestamped_name(moment, ".mp4"), "phone-link-20261107-150405.mp4"
        )

    def test_unique_path_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "phone-link.png"
            base.write_bytes(b"old")
            second = unique_path(Path(folder), "phone-link.png")
            self.assertEqual(second.name, "phone-link-1.png")
            second.write_bytes(b"new")
            third = unique_path(Path(folder), "phone-link.png")
            self.assertEqual(third.name, "phone-link-2.png")
            self.assertEqual(base.read_bytes(), b"old")

    def test_default_recording_folder(self) -> None:
        folder = default_recording_folder()
        self.assertEqual(folder.name, "Phone Link")
        self.assertEqual(folder.parent.name, "Videos")


class RecordArgumentTests(unittest.TestCase):
    def _args(self, **overrides) -> list[str]:
        values = {
            "serial": "SERIAL",
            "marker": "PhoneLinkMirror-abcd1234",
            "width": 420,
            "height": 900,
        }
        values.update(overrides)
        return build_scrcpy_args(**values)

    def test_record_flag(self) -> None:
        args = self._args(record="/tmp/phone-link.mp4")
        self.assertIn("--record=/tmp/phone-link.mp4", args)

    def test_no_record_flag_by_default(self) -> None:
        self.assertFalse(any(arg.startswith("--record=") for arg in self._args()))

    def test_display_id_from_log(self) -> None:
        _app()
        session = ScrcpySession("/nonexistent/scrcpy", None)
        self.assertIsNone(session.display_id)
        session._log_tail = (
            "INFO: scrcpy 5.0.1 <https://github.com/Genymobile/scrcpy>\n"
            "INFO: New display: 1920x1080/240 (id=114)\n"
        )
        self.assertEqual(session.display_id, 114)


class ScreenshotCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.folder = Path(self._folder.name)
        self._captures: list[ScreenshotCapture] = []

    def tearDown(self) -> None:
        for capture in self._captures:
            capture.stop()
        self._captures.clear()
        # Without a running event loop, deferred deletions would still be
        # pending when the captures are garbage collected; flush them while
        # their child QProcess objects are still valid.
        _app().sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _capture(self, display_id: int | None = None) -> dict[str, list[str]]:
        events: dict[str, list[str]] = {"saved": [], "failed": []}
        capture = ScreenshotCapture(
            str(FAKE_ADB), SERIAL, self.folder, display_id
        )
        self._captures.append(capture)
        capture.saved.connect(events["saved"].append)
        capture.failed.connect(events["failed"].append)
        capture.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not events["saved"] and not events["failed"]:
            _app().processEvents()
            time.sleep(0.01)
        return events

    def test_phone_screenshot(self) -> None:
        events = self._capture()
        self.assertEqual(events["failed"], [])
        self.assertEqual(len(events["saved"]), 1)
        path = Path(events["saved"][0])
        self.assertTrue(path.read_bytes().startswith(PNG_SIGNATURE))
        self.assertRegex(path.name, re.compile(r"^phone-link-\d{8}-\d{6}\.png$"))

    def test_dex_screenshot_uses_display_token(self) -> None:
        events = self._capture(114)
        self.assertEqual(events["failed"], [])
        self.assertEqual(len(events["saved"]), 1)
        self.assertTrue(Path(events["saved"][0]).read_bytes().startswith(PNG_SIGNATURE))

    def test_missing_dex_display_fails(self) -> None:
        events = self._capture(1)
        self.assertEqual(events["saved"], [])
        self.assertEqual(len(events["failed"]), 1)
        self.assertIn("Could not find the DeX display", events["failed"][0])

    def test_missing_adb_reports_failure(self) -> None:
        events: list[str] = []
        capture = ScreenshotCapture("/nonexistent/adb", SERIAL, self.folder)
        self._captures.append(capture)
        capture.failed.connect(events.append)
        capture.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not events:
            _app().processEvents()
            time.sleep(0.01)
        self.assertEqual(len(events), 1)
        self.assertIn("Could not run adb", events[0])


if __name__ == "__main__":
    unittest.main()
