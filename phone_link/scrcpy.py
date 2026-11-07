"""Launch scrcpy and adopt its X11 window into the Qt screen container."""

from __future__ import annotations

import re
import uuid

from PySide6.QtCore import (
    QElapsedTimer,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
)

from .x11 import X11Display, X11Error

# A 1080p virtual display at 240 dpi gives Samsung's desktop mode
# (DeX) the full launcher, taskbar and window decorations.
DEX_DISPLAY = "1920x1080/240"

# scrcpy reports the display it created, e.g.
#   INFO: New display: 1920x1080/240 (id=114)
NEW_DISPLAY_RE = re.compile(r"New display: .*\(id=(\d+)\)")


class ScrcpySession(QObject):
    """One scrcpy process tied to one Qt container window."""

    statusChanged = Signal(str, str)  # state, human-readable detail
    embedded = Signal(int)  # X11 window id
    logLine = Signal(str)

    def __init__(
        self,
        scrcpy_path: str | None,
        adb_path: str | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.scrcpy_path = scrcpy_path
        self.adb_path = adb_path
        self.marker = f"PhoneLinkMirror-{uuid.uuid4().hex[:12]}"
        self._process: QProcess | None = None
        self._x11: X11Display | None = None
        self._child_window = 0
        self._parent_xid = 0
        self._width = 1
        self._height = 1
        self._audio = False
        self._dex = False
        self._floating = False
        self._floating_live = False
        self._record_path = ""
        self._log_tail = ""
        self._stopping = False
        self._suppress_stopped = False
        self._embedded = False
        self._ever_embedded = False
        self._had_keyboard_focus = False
        self._saw_crash = False
        self._found_child = 0
        self._found_at = 0
        self._timeout_ms = 30_000
        # SDL3 creates the X11 window before its renderer is ready.  Reparenting
        # during that window makes scrcpy 5 crash, so let it settle first.
        self._embed_settle_ms = 650
        self._elapsed = QElapsedTimer()

        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._poll_for_window)

        # A click on the embedded picture moves keyboard focus into scrcpy, so
        # typing goes to the phone; the app's own shortcuts take it back.
        self._focus_watch = QTimer(self)
        self._focus_watch.setInterval(120)
        self._focus_watch.timeout.connect(self._watch_mirror_clicks)

        # In floating mode there is nothing to embed, so report success once
        # scrcpy has had time to get past its startup handshake.
        self._floating_timer = QTimer(self)
        self._floating_timer.setSingleShot(True)
        self._floating_timer.setInterval(1_200)
        self._floating_timer.timeout.connect(self._on_floating_ready)

    @property
    def is_embedded(self) -> bool:
        return self._embedded

    @property
    def is_floating(self) -> bool:
        return self._floating

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def record_path(self) -> str:
        return self._record_path

    @property
    def display_id(self) -> int | None:
        """The Android display this session shows, if scrcpy created one.

        Only DeX uses a virtual display; scrcpy logs its id once the display
        exists, which is what a screenshot of the desktop needs.
        """

        matches = NEW_DISPLAY_RE.findall(self._log_tail)
        if not matches:
            return None
        return int(matches[-1])

    def start(
        self,
        serial: str,
        parent_xid: int,
        width: int,
        height: int,
        audio: bool | None = None,
        dex: bool = False,
        floating: bool = False,
        record_path: str | None = None,
    ) -> None:
        # Remember where the keyboard was so a restart (DeX toggle, recording)
        # does not move it away from the user's current focus.
        self._had_keyboard_focus = self._keyboard_is_on_mirror()
        self.stop(silent=True)
        if audio is not None:
            self._audio = bool(audio)
        self._embedded = False
        self._ever_embedded = False
        self._saw_crash = False
        self._found_child = 0
        self._found_at = 0
        self._parent_xid = int(parent_xid)
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._dex = bool(dex)
        self._floating = bool(floating)
        self._floating_live = False
        self._record_path = str(record_path or "")
        self._log_tail = ""

        if not self.scrcpy_path:
            self.statusChanged.emit("failed", "scrcpy is not installed. See the setup instructions.")
            return
        if not self._floating and parent_xid == 0:
            self.statusChanged.emit("failed", "The screen container is not ready yet.")
            return

        if not self._floating:
            try:
                self._x11 = X11Display()
            except X11Error as exc:
                self.statusChanged.emit("failed", str(exc))
                return

        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        # Bind each callback to its process.  An old process may finish after a
        # new session has already been started; it must not clear the new one.
        process.readyReadStandardOutput.connect(
            lambda p=process: self._read_process_output(p)
        )
        process.finished.connect(
            lambda code, status, p=process: self._on_process_finished(p, code, status)
        )
        process.errorOccurred.connect(
            lambda error, p=process: self._on_process_error(p, error)
        )

        env = QProcessEnvironment.systemEnvironment()
        # scrcpy's SDL window must be an X11 window so it can be reparented.
        env.insert("SDL_VIDEODRIVER", "x11")
        env.insert("SDL_VIDEO_CENTERED", "1" if self._floating else "0")
        if self.adb_path:
            env.insert("ADB", self.adb_path)
        self._process.setProcessEnvironment(env)

        args = build_scrcpy_args(
            serial=serial,
            marker=self.marker,
            width=self._width,
            height=self._height,
            dex=self._dex,
            floating=self._floating,
            audio=self._audio,
            record=self._record_path or None,
        )
        self._elapsed.start()
        self._stopping = False
        self._suppress_stopped = False
        if self._floating:
            self.statusChanged.emit("starting", "Opening the desktop window…")
            self._floating_timer.start()
        else:
            self._poll.start()
            self.statusChanged.emit("starting", "Starting scrcpy and waiting for the phone.")
        process.start(self.scrcpy_path, args)

    def set_geometry(self, width: int, height: int) -> None:
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        if self._embedded and self._child_window and self._x11:
            self._x11.set_geometry(self._child_window, self._width, self._height)

    def focus(self) -> None:
        if self._embedded and self._child_window and self._x11:
            self._x11.focus(self._child_window)

    def focus_app(self, window: int) -> None:
        """Give keyboard focus back to the app's own X window."""

        if self._x11 is not None and window:
            self._x11.focus(int(window))

    def _keyboard_is_on_mirror(self) -> bool:
        if not (self._embedded and self._child_window and self._x11):
            return False
        return self._x11.input_focus() == self._child_window

    def _watch_mirror_clicks(self) -> None:
        if not (self._embedded and self._child_window and self._x11):
            self._focus_watch.stop()
            return
        if self._x11.take_raw_button_press() and self._x11.pointer_inside(
            self._child_window, self._width, self._height
        ):
            self._x11.focus(self._child_window)

    def refresh(self) -> bool:
        """Make scrcpy repaint after its window was resized externally."""

        if not (self._embedded and self._child_window and self._x11):
            return False
        self._x11.refresh(self._child_window)
        return True

    def rotate(self) -> bool:
        """Ask scrcpy to rotate the device (its MOD+r action).

        scrcpy forces the display rotation in the Android window manager.  It
        works for portrait-locked apps too, which plain ``wm user-rotation``
        does not, and it does not leave the phone's rotation setting changed.
        """

        return self.send_shortcut("r")

    def send_shortcut(self, key: str) -> bool:
        """Send a scrcpy MOD+key shortcut to the display this session owns.

        For DeX this matters: scrcpy dispatches its own shortcuts to the
        virtual display, while ``adb shell input keyevent`` always goes to the
        phone's main display.
        """

        if not (self._embedded and self._child_window and self._x11):
            return False
        self._x11.focus(self._child_window)
        return self._x11.send_key_combo("Alt_L", key)

    def stop(self, block: bool = False, silent: bool = False) -> None:
        self._stopping = True
        self._suppress_stopped = silent
        self._poll.stop()
        self._focus_watch.stop()
        self._floating_timer.stop()
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if block:
                if not process.waitForFinished(1500):
                    process.kill()
                    process.waitForFinished(500)
        self._cleanup_x11()

    def _read_process_output(self, process: QProcess) -> None:
        if process is not self._process:
            return
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not text:
            return
        self._log_tail = (self._log_tail + text)[-14_000:]
        for line in text.splitlines():
            clean = _strip_ansi(line).strip()
            if clean:
                self.logLine.emit(clean)

    def _on_floating_ready(self) -> None:
        process = self._process
        if not self._floating or process is None:
            return
        if process.state() != QProcess.ProcessState.Running:
            return
        self._floating_live = True
        self.statusChanged.emit(
            "floating",
            "The DeX desktop is open in its own window."
            if self._dex
            else "The phone is open in its own window.",
        )

    def _poll_for_window(self) -> None:
        if not self._process or not self._x11:
            self._poll.stop()
            return
        if self._process.state() != QProcess.ProcessState.Running:
            return
        if self._elapsed.elapsed() > self._timeout_ms:
            self._poll.stop()
            self.statusChanged.emit(
                "failed",
                "scrcpy started, but its window did not appear. Check the phone's USB debugging prompt.",
            )
            self.stop(silent=True)
            return

        try:
            child = self._x11.find_window(self.marker)
        except Exception as exc:
            self._poll.stop()
            self.statusChanged.emit("failed", f"Could not inspect the X11 window tree: {exc}")
            self.stop(silent=True)
            return

        if not child:
            return

        if child != self._found_child:
            self._found_child = int(child)
            self._found_at = self._elapsed.elapsed()
            return
        if self._elapsed.elapsed() - self._found_at < self._embed_settle_ms:
            return

        try:
            self._x11.embed(child, self._parent_xid, self._width, self._height)
        except X11Error as exc:
            self._poll.stop()
            self.statusChanged.emit("failed", str(exc))
            self.stop(silent=True)
            return

        self._poll.stop()
        self._child_window = int(child)
        self._embedded = True
        self._ever_embedded = True
        self._x11.watch_raw_button_presses()
        self._focus_watch.start()
        if self._had_keyboard_focus:
            self._x11.focus(self._child_window)
        self.embedded.emit(self._child_window)
        self.statusChanged.emit("embedded", "Live")

    def _on_process_finished(
        self,
        process: QProcess,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if process is not self._process:
            process.deleteLater()
            return
        self._poll.stop()
        self._floating_timer.stop()
        was_stopping = self._stopping
        was_embedded = self._ever_embedded
        was_floating = self._floating
        was_floating_live = self._floating_live
        crashed = exit_status == QProcess.ExitStatus.CrashExit or self._saw_crash
        self._embedded = False
        self._floating = False
        self._floating_live = False
        self._child_window = 0
        self._found_child = 0
        self._cleanup_x11()
        self._process = None

        if was_stopping:
            if not self._suppress_stopped:
                self.statusChanged.emit("stopped", "Mirroring stopped.")
            return

        if was_floating and was_floating_live:
            if not crashed:
                self.statusChanged.emit("stopped", "The floating window was closed.")
                return
            detail = _friendly_failure(self._log_tail)
            suffix = f" {detail}" if detail else ""
            self.statusChanged.emit("failed", f"The floating window closed unexpectedly.{suffix}".strip())
            return

        if was_embedded:
            prefix = "The mirror disconnected."
        elif crashed:
            prefix = "scrcpy crashed while starting the mirror."
        else:
            prefix = "scrcpy could not start the mirror."
        detail = _friendly_failure(self._log_tail)
        suffix = f" {detail}" if detail else ""
        if crashed and not detail and was_embedded:
            suffix = " Reconnect USB debugging and try again."
        self.statusChanged.emit("failed", f"{prefix}{suffix}".strip())

    def _on_process_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if process is not self._process:
            return
        if self._stopping:
            return
        if error == QProcess.ProcessError.Crashed:
            self._saw_crash = True
        mapping = {
            QProcess.ProcessError.FailedToStart: "Could not execute scrcpy. Check the installed path.",
            QProcess.ProcessError.Crashed: "scrcpy crashed.",
            QProcess.ProcessError.Timedout: "scrcpy timed out.",
            QProcess.ProcessError.WriteError: "Could not write to scrcpy.",
            QProcess.ProcessError.ReadError: "Could not read from scrcpy.",
        }
        self.statusChanged.emit("failed", mapping.get(error, "scrcpy encountered an error."))
        self._poll.stop()
        self._floating_timer.stop()
        self._cleanup_x11()

    def _cleanup_x11(self) -> None:
        self._embedded = False
        self._child_window = 0
        if self._x11:
            self._x11.close()
            self._x11 = None


def build_scrcpy_args(
    *,
    serial: str,
    marker: str,
    width: int,
    height: int,
    dex: bool = False,
    floating: bool = False,
    audio: bool = False,
    record: str | None = None,
) -> list[str]:
    """Build the scrcpy command line for one mirror or DeX window."""

    title = (
        # scrcpy publishes its title through the legacy WM_NAME property,
        # which window managers decode as Latin-1.  An em dash here renders as
        # "â€”" in the title bar, so keep this one ASCII.
        f"Phone Link - {'DeX desktop' if dex else 'phone'} ({marker[-4:]})"
        if floating
        else marker
    )
    args = [
        "--serial",
        serial,
        "-w",
        "--disable-screensaver",
        "--window-title",
        title,
        "--no-window-aspect-ratio-lock",
        "--shortcut-mod",
        "lalt",
        "--window-width",
        str(min(max(1, int(width)), 1600)),
        "--window-height",
        str(min(max(1, int(height)), 1600)),
        "--render-fit",
        "letterbox",
    ]
    if dex:
        # Creating a virtual display is what makes Samsung show its full
        # desktop launcher instead of mirroring the phone screen.  In a
        # desktop a right-click must open a context menu, so forward secondary
        # clicks instead of scrcpy's phone defaults (right-click = Back).
        args.append(f"--new-display={DEX_DISPLAY}")
        args.append("--mouse-bind=++++:bhsn")
    if not floating:
        # The embedded window must be borderless so it looks like part of the
        # Qt panel; a floating window keeps its normal decorations.
        args.append("--window-borderless")
    if not audio:
        args.append("--no-audio")
    if record:
        # scrcpy only records from the start of a session, so the panel
        # restarts the visible session with this flag to begin, and restarts
        # it again without the flag to stop and finalize the MP4.
        args.append(f"--record={record}")
    # Audio keeps scrcpy's 50 ms default buffer: USB and the router's 5 GHz
    # band both carry the stream without loss, so the tighter A/V sync wins.
    return args


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _friendly_failure(log: str) -> str:
    lower = log.lower()
    if "audio" in lower and (
        "not supported" in lower
        or "could not capture" in lower
        or "audio capture" in lower
        or "audio encoder" in lower
    ):
        return "The phone could not capture its audio. Turn the speaker button off and try again."
    if "unauthorized" in lower:
        return "Unlock the phone and accept the USB debugging authorization prompt."
    if "no devices" in lower or ("could not find" in lower and "adb device" in lower):
        return "No ADB device is connected."
    if "device disconnected" in lower or "disconnected" in lower:
        return "The phone disconnected."
    if "address already in use" in lower or "port" in lower and "in use" in lower:
        return "An adb or scrcpy port is already in use. Close other scrcpy sessions and retry."
    if "server connection failed" in lower:
        return "The scrcpy server could not connect back to the desktop."
    if "permission" in lower and "denied" in lower:
        return "Android denied the scrcpy server access. Reconnect USB debugging."
    if "camera" in lower and "not found" in lower:
        return "The phone did not expose the requested camera."
    if "encoder" in lower and ("not found" in lower or "error" in lower):
        return "The phone could not start a video encoder. Reconnect the phone and try again."
    if "failed to open" in lower or "could not open" in lower:
        return "The phone could not open the requested display. Reconnect USB debugging."

    # Do not surface routine setup chatter as if it were the error.
    ignored = (
        "file pushed",
        "skipped.",
        "adb device found",
        "adb device not found",
        "scrcpy 5.",
        "renderer:",
        "opengl version",
        "using displaymanager",
        "texture",
        "codec size alignment",
    )
    useful = []
    for line in log.splitlines():
        clean = _strip_ansi(line).strip()
        if not clean:
            continue
        lowered = clean.lower()
        if any(fragment in lowered for fragment in ignored):
            continue
        if clean.startswith(("INFO:", "DEBUG:", "VERBOSE:", "[server] INFO", "[server] DEBUG")):
            continue
        if clean.startswith("/") and "scrcpy-server" in clean:
            continue
        useful.append(clean)
    if useful:
        last = useful[-1]
        return last if len(last) <= 200 else last[:197] + "..."
    return ""
