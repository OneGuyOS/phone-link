"""Phone screenshots over ADB, including the DeX virtual display."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

CAPTURE_TIMEOUT_MS = 25_000
DISPLAY_ID_RE = re.compile(r"\bmDisplayId=(\d+)\b")
# dumpsys display renders this as ``uniqueId "virtual:..."`` inside the
# DisplayInfo line; other dumps use ``uniqueId="..."``.
UNIQUE_ID_RE = re.compile(r"\buniqueId[\s=]+\"?([^\"\s]+)")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def default_recording_folder() -> Path:
    return Path.home() / "Videos" / "Phone Link"


def timestamped_name(moment: datetime | None = None, suffix: str = ".png") -> str:
    moment = moment or datetime.now()
    return f"phone-link-{moment:%Y%m%d-%H%M%S}{suffix}"


def dumpsys_display_command(adb_path: str, serial: str) -> list[str]:
    return [adb_path, "-s", serial, "shell", "dumpsys", "display"]


def dumpsys_surfaceflinger_command(adb_path: str, serial: str) -> list[str]:
    return [
        adb_path,
        "-s",
        serial,
        "shell",
        "dumpsys",
        "SurfaceFlinger",
        "--display-id",
    ]


def screencap_command(
    adb_path: str,
    serial: str,
    display_token: int | None = None,
) -> list[str]:
    """Capture the phone screen, or one display when *display_token* is set.

    ``screencap -d`` wants the SurfaceFlinger display token from
    ``dumpsys SurfaceFlinger --display-id``, not the display id scrcpy logs.
    """

    command = [adb_path, "-s", serial, "exec-out", "screencap", "-p"]
    if display_token is not None:
        command[-1:-1] = ["-d", str(display_token)]
    return command


def display_unique_id(dump: str, display_id: int) -> str | None:
    """Find the uniqueId of the display block carrying *display_id*.

    ``dumpsys display`` lists each logical display under a ``Display N:``
    heading; the uniqueId follows in the DisplayInfo line further down.
    """

    current: int | None = None
    for line in dump.splitlines():
        if "mDisplayId=" in line:
            match = DISPLAY_ID_RE.search(line)
            current = int(match.group(1)) if match else None
            continue
        if current != display_id:
            continue
        match = UNIQUE_ID_RE.search(line)
        if match:
            return match.group(1)
    return None


def display_token(surfaceflinger_dump: str, unique_id: str) -> int | None:
    """Map a display uniqueId to its SurfaceFlinger display token."""

    if not unique_id:
        return None
    pattern = re.compile(r"Display\s+(\d+).*uniqueId=[\"']" + re.escape(unique_id) + r"[\"']")
    for line in surfaceflinger_dump.splitlines():
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def unique_path(folder: Path, name: str) -> Path:
    """A path in *folder* that does not overwrite an existing file."""

    candidate = folder / name
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while candidate.exists():
        candidate = folder / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        clean = line.strip()
        if clean:
            return clean
    return ""


class ScreenshotCapture(QObject):
    """Run the ADB commands for a screenshot without blocking the UI.

    A phone mirror needs one ``screencap`` call.  DeX runs on a virtual display
    whose SurfaceFlinger token has to be looked up first, so the same class
    walks two ``dumpsys`` calls before capturing.
    """

    saved = Signal(str)  # path
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str | None,
        serial: str,
        folder: Path,
        display_id: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = serial
        self.folder = Path(folder)
        self.display_id = display_id
        self._process: QProcess | None = None
        self._stopped = False
        self._unique_id = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CAPTURE_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_timeout)

    def start(self) -> None:
        self.stop()
        self._stopped = False
        if not self.adb_path:
            self.failed.emit("adb is not installed, so screenshots are unavailable.")
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.failed.emit(f"Could not create {self.folder}: {exc}")
            return
        self._unique_id = ""
        self._timer.start()
        if self.display_id is None:
            self._capture(None)
            return
        self._run(
            dumpsys_display_command(self.adb_path, self.serial),
            self._on_display_dump,
        )

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                # The finished callback disposes of a killed process; Qt must
                # not destroy a QProcess while it is still running.
                process.kill()
            else:
                process.deleteLater()

    def _run(self, command: list[str], callback) -> None:
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(
            lambda code, status, p=process, cb=callback: self._on_finished(p, code, cb)
        )
        process.errorOccurred.connect(
            lambda error, p=process: self._on_error(p, error)
        )
        process.start(command[0], command[1:])

    def _on_finished(self, process: QProcess, code: int, callback) -> None:
        if process is not self._process:
            process.deleteLater()
            return
        self._process = None
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        process.deleteLater()
        if self._stopped:
            return
        callback(code, output)

    def _on_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if process is not self._process:
            return
        if error != QProcess.ProcessError.FailedToStart:
            # Any other error also finishes the process, which reports through
            # the output callback.
            return
        self._process = None
        self._timer.stop()
        process.deleteLater()
        if not self._stopped:
            self._fail("Could not run adb to take a screenshot.")

    def _on_display_dump(self, code: int, output: str) -> None:
        if code != 0:
            self._fail(self._adb_error("Could not reach the phone to take a screenshot.", output))
            return
        unique_id = display_unique_id(output, self.display_id or 0)
        if not unique_id:
            self._fail("Could not find the DeX display on the phone.")
            return
        self._unique_id = unique_id
        self._run(
            dumpsys_surfaceflinger_command(self.adb_path, self.serial),
            self._on_surfaceflinger_dump,
        )

    def _on_surfaceflinger_dump(self, code: int, output: str) -> None:
        if code != 0:
            self._fail(self._adb_error("Could not read the phone's displays.", output))
            return
        token = display_token(output, self._unique_id)
        if token is None:
            self._fail("Could not find the DeX display on the phone.")
            return
        self._capture(token)

    def _capture(self, token: int | None) -> None:
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(
            lambda code, status, p=process: self._on_capture_finished(p, code)
        )
        process.errorOccurred.connect(
            lambda error, p=process: self._on_error(p, error)
        )
        command = screencap_command(self.adb_path, self.serial, token)
        process.start(command[0], command[1:])

    def _on_capture_finished(self, process: QProcess, code: int) -> None:
        if process is not self._process:
            process.deleteLater()
            return
        self._process = None
        data = bytes(process.readAllStandardOutput())
        errors = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        process.deleteLater()
        if self._stopped:
            return
        self._timer.stop()
        if code != 0 or not data.startswith(PNG_SIGNATURE):
            self._fail(self._adb_error("Could not capture the phone screen.", errors))
            return
        destination = unique_path(self.folder, timestamped_name())
        temporary = destination.with_name(f".{destination.name}.part")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        except OSError as exc:
            self._fail(f"Could not save the screenshot: {exc}")
            return
        self.saved.emit(str(destination))

    def _adb_error(self, message: str, output: str) -> str:
        detail = _last_line(output)
        return f"{message} {detail}".strip() if detail else message

    def _fail(self, message: str) -> None:
        self._timer.stop()
        self.failed.emit(message)

    def _on_timeout(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
            else:
                process.deleteLater()
        if not self._stopped:
            self.failed.emit("Taking the screenshot took too long. Try again.")
