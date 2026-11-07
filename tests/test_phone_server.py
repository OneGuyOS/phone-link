"""Unit tests for starting the Termux model server over ADB."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QCoreApplication, Qt

from phone_link import phone_server
from phone_link.phone_server import START_COMMAND, TERMUX_ACTIVITY, PhoneStarter


class FakeAdb:
    """Record adb commands and answer like a phone with Termux installed."""

    def __init__(
        self,
        *,
        packages: str = "package:com.termux\n",
        forward_rc: int = 0,
        forward_err: str = "",
        window: str = "",
    ) -> None:
        self.calls: list[list[str]] = []
        self.packages = packages
        self.forward_rc = forward_rc
        self.forward_err = forward_err
        self.window = window

    def __call__(self, args: list[str]) -> SimpleNamespace:
        self.calls.append(args)
        if args[1] == "forward":
            return SimpleNamespace(
                returncode=self.forward_rc, stdout="", stderr=self.forward_err
            )
        if args[1:3] == ["shell", "pm"]:
            return SimpleNamespace(
                returncode=0, stdout=self.packages, stderr=""
            )
        if args[1:3] == ["shell", "dumpsys"]:
            return SimpleNamespace(
                returncode=0, stdout=self.window, stderr=""
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def typed(self) -> list[list[str]]:
        return [call for call in self.calls if call[1:4] == ["shell", "input", "text"]]


class PhoneStarterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        settle = mock.patch.object(phone_server, "KEYGUARD_SETTLE_S", 0.0)
        settle.start()
        self.addCleanup(settle.stop)

    def _starter(self, adb: FakeAdb, **kwargs) -> PhoneStarter:
        defaults = dict(timeout_s=2.0, poll_s=0.01, focus_delay_s=0.0)
        defaults.update(kwargs)
        return PhoneStarter("/usr/bin/adb", runner=adb, **defaults)

    def _collect(self, starter: PhoneStarter):
        ready = threading.Event()
        failed: list[str] = []
        starter.ready.connect(ready.set, Qt.ConnectionType.DirectConnection)
        starter.failed.connect(
            failed.append, Qt.ConnectionType.DirectConnection
        )
        return ready, failed

    def test_start_forwards_types_the_command_and_reports_ready(self) -> None:
        adb = FakeAdb()
        starter = self._starter(adb)
        ready, failed = self._collect(starter)
        with mock.patch.object(
            phone_server, "models_answer", side_effect=[False, True]
        ):
            starter.ensure_started()
            self.assertTrue(ready.wait(2))
        self.assertEqual(failed, [])
        self.assertIn(
            ["/usr/bin/adb", "forward", "tcp:8080", "tcp:8080"], adb.calls
        )
        self.assertIn(
            [
                "/usr/bin/adb",
                "shell",
                "am",
                "start",
                "-n",
                TERMUX_ACTIVITY,
            ],
            adb.calls,
        )
        self.assertEqual(
            adb.typed(), [["/usr/bin/adb", "shell", "input", "text", START_COMMAND]]
        )
        self.assertIn(
            ["/usr/bin/adb", "shell", "input", "keyevent", "66"], adb.calls
        )

    def test_missing_termux_is_reported_before_typing(self) -> None:
        adb = FakeAdb(packages="package:com.android.settings\n")
        starter = self._starter(adb)
        ready, failed = self._collect(starter)
        with mock.patch.object(phone_server, "models_answer", return_value=False):
            starter.ensure_started()
            self.assertTrue(_wait_for(lambda: failed, 2))
        self.assertFalse(ready.is_set())
        self.assertIn("Termux is not installed", failed[0])
        self.assertEqual(adb.typed(), [])

    def test_locked_phone_is_reported_before_typing(self) -> None:
        adb = FakeAdb(
            window="    isKeyguardShowing=true mDreamingLockscreen=false\n"
        )
        starter = self._starter(adb)
        _ready, failed = self._collect(starter)
        with mock.patch.object(phone_server, "models_answer", return_value=False):
            starter.ensure_started()
            self.assertTrue(_wait_for(lambda: failed, 2))
        self.assertIn("locked", failed[0])
        self.assertEqual(adb.typed(), [])
        self.assertNotIn(
            ["/usr/bin/adb", "shell", "am", "start", "-n", TERMUX_ACTIVITY],
            adb.calls,
        )

    def test_forward_failure_is_reported(self) -> None:
        adb = FakeAdb(forward_rc=1, forward_err="error: no devices/emulators found")
        starter = self._starter(adb)
        _ready, failed = self._collect(starter)
        with mock.patch.object(phone_server, "models_answer", return_value=False):
            starter.ensure_started()
            self.assertTrue(_wait_for(lambda: failed, 2))
        self.assertIn("no devices", failed[0])

    def test_timeout_mentions_unlocking_the_phone(self) -> None:
        adb = FakeAdb()
        starter = self._starter(adb, timeout_s=0.1, poll_s=0.01)
        ready, failed = self._collect(starter)
        with mock.patch.object(phone_server, "models_answer", return_value=False):
            starter.ensure_started()
            self.assertTrue(_wait_for(lambda: failed, 2))
        self.assertFalse(ready.is_set())
        self.assertIn("unlocked", failed[0])

    def test_stop_cancels_waiting_without_an_error(self) -> None:
        adb = FakeAdb()
        starter = self._starter(adb, timeout_s=5.0, poll_s=0.01)
        _ready, failed = self._collect(starter)
        with mock.patch.object(phone_server, "models_answer", return_value=False):
            starter.ensure_started()
            self.assertTrue(_wait_for(lambda: adb.typed(), 2))
            starter.stop()
            if starter._thread is not None:
                starter._thread.join(2)
        self.assertEqual(failed, [])


def _wait_for(test, timeout: float) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.01)
    return False


if __name__ == "__main__":
    unittest.main()
