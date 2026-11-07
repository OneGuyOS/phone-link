"""Unit tests for close-to-tray behaviour, without a real tray."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from phone_link.ui.main_window import MainWindow


class FakeEvent:
    def __init__(self) -> None:
        self.ignored = False
        self.accepted = False

    def ignore(self) -> None:
        self.ignored = True

    def accept(self) -> None:
        self.accepted = True


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.synced = 0

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def sync(self) -> None:
        self.synced += 1


class FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple] = []

    def showMessage(self, *args) -> None:
        self.messages.append(args)


class FakeWindow:
    def __init__(self) -> None:
        self._settings = FakeSettings()
        self._tray: FakeTray | None = FakeTray()
        self._really_quitting = False
        self._tray_hint_shown = False
        self.hidden = 0
        self.save_count = 0

    def saveGeometry(self):
        self.save_count += 1
        return b"geometry"

    def hide(self) -> None:
        self.hidden += 1


class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_close_hides_instead_of_quitting(self) -> None:
        window = FakeWindow()
        event = FakeEvent()
        MainWindow.closeEvent(window, event)
        self.assertTrue(event.ignored)
        self.assertFalse(event.accepted)
        self.assertEqual(window.hidden, 1)
        self.assertEqual(window.save_count, 1)
        self.assertEqual(window._settings.synced, 1)
        self.assertEqual(len(window._tray.messages), 1)

    def test_the_tray_hint_is_shown_once(self) -> None:
        window = FakeWindow()
        MainWindow.closeEvent(window, FakeEvent())
        MainWindow.closeEvent(window, FakeEvent())
        self.assertEqual(window.hidden, 2)
        self.assertEqual(len(window._tray.messages), 1)

    def test_quit_marks_the_window_before_closing(self) -> None:
        class QuitWindow:
            def __init__(self) -> None:
                self._really_quitting = False
                self.closed = False

            def close(self) -> None:
                self.closed = True
                self.was_marked = self._really_quitting

        window = QuitWindow()
        MainWindow.quit_app(window)
        self.assertTrue(window._really_quitting)
        self.assertTrue(window.closed)
        self.assertTrue(window.was_marked)


if __name__ == "__main__":
    unittest.main()
