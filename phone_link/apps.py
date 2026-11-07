"""List installed launchable Android apps for app streaming."""

from __future__ import annotations

import re

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

LIST_TIMEOUT_MS = 20_000
PACKAGE_PREFIX = "package:"
APP_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
# ``cmd package query-activities --brief`` prints one component per line, for
# example "    com.android.settings/.Settings".
COMPONENT_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_.]*)/([A-Za-z0-9_.$]+)\s*$"
)


def is_valid_package(name: str) -> bool:
    """Whether *name* looks like an Android app package name."""

    return bool(APP_PACKAGE_RE.fullmatch((name or "").strip()))


def parse_packages(output: str) -> list[str]:
    """Extract package names from an ADB package/activity listing.

    ``cmd package query-activities --brief`` prints component lines.  Older
    tools print ``package:`` lines; accept those too.  ADB may print warnings
    or a ``SecurityException`` on the same stream, so ignore every line that is
    neither form.  The result is sorted for a predictable picker.
    """

    packages: set[str] = set()
    for line in output.splitlines():
        component = COMPONENT_RE.match(line)
        if component is not None:
            name = component.group(1)
            if is_valid_package(name):
                packages.add(name)
            continue
        line = line.strip()
        if not line.startswith(PACKAGE_PREFIX):
            continue
        name = line[len(PACKAGE_PREFIX) :].strip()
        if is_valid_package(name):
            packages.add(name)
    return sorted(packages, key=str.casefold)


def package_list_command(adb_path: str, serial: str) -> list[str]:
    """The ADB command that lists launchable packages."""

    return [
        adb_path,
        "-s",
        serial,
        "shell",
        "cmd",
        "package",
        "query-activities",
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    ]


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        clean = line.strip()
        if clean:
            return clean
    return ""


class AppListLoader(QObject):
    """Ask ADB for the installed apps without blocking the dialog.

    QProcess is used instead of a thread so the work is cancelled cleanly when
    the picker closes.
    """

    loaded = Signal(object)  # list[str]
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str | None,
        serial: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = serial
        self._process: QProcess | None = None
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LIST_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_timeout)

    def start(self) -> None:
        self.stop()
        self._stopped = False
        if not self.adb_path:
            self.failed.emit("adb is not installed, so the app list is unavailable.")
            return

        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(
            lambda code, status, p=process: self._on_finished(p, code, status)
        )
        process.errorOccurred.connect(
            lambda error, p=process: self._on_error(p, error)
        )
        command = package_list_command(self.adb_path, self.serial)
        self._timer.start()
        process.start(command[0], command[1:])

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                # Let the finished callback dispose of a killed process; Qt
                # must not destroy a QProcess while it is still running.
                process.kill()
            else:
                process.deleteLater()

    def _on_finished(
        self,
        process: QProcess,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if process is not self._process:
            process.deleteLater()
            return
        self._process = None
        self._timer.stop()
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        errors = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        process.deleteLater()
        if self._stopped:
            return

        packages = parse_packages(output)
        if packages:
            self.loaded.emit(packages)
            return
        detail = _last_line(errors) or _last_line(output)
        if exit_code != 0 and not detail:
            detail = f"adb exited with status {exit_code}."
        suffix = f" {detail}" if detail else ""
        self.failed.emit(f"Could not read the phone's app list.{suffix}".strip())

    def _on_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if process is not self._process:
            return
        if error != QProcess.ProcessError.FailedToStart:
            # A crash or read error also emits finished, which reports the
            # problem with the process output.
            return
        self._process = None
        self._timer.stop()
        process.deleteLater()
        if not self._stopped:
            self.failed.emit("Could not run adb to list the phone's apps.")

    def _on_timeout(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                # Let the finished callback dispose of a killed process; Qt
                # must not destroy a QProcess while it is still running.
                process.kill()
            else:
                process.deleteLater()
        if not self._stopped:
            self.failed.emit("Listing the phone's apps took too long. Try again.")
