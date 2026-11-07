"""Start the llama.cpp server inside Termux on the phone.

The phone's OpenAI endpoint is available through ``adb forward``, but the
server itself has to be launched inside Termux.  Termux's ``RUN_COMMAND``
intent requires a permission a desktop app cannot hold, so this module uses
ADB instead: it brings Termux to the front, types the start command the setup
instructions install, presses Enter, and then waits for ``/v1/models``.

The phone must be unlocked (or have no secure lock) for the keystrokes to
reach Termux.  Nothing is sent over the network; every step is an ``adb``
command over the existing USB or Wi-Fi ADB connection.
"""

from __future__ import annotations

import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

from .local_server import models_answer

TERMUX_HOME = "/data/data/com.termux/files/home"
START_COMMAND = f"{TERMUX_HOME}/start-phone-llama.sh"
TERMUX_ACTIVITY = "com.termux/.HomeActivity"
DEFAULT_PORT = 8080
START_TIMEOUT_S = 120.0
POLL_S = 1.0
FOCUS_DELAY_S = 1.5

_MISSING_TERMUX = (
    "Could not start the phone model: Termux is not installed. Install it "
    "from F-Droid, then follow the phone setup instructions in the README."
)
_FAILED_START = (
    "The phone model did not come up. Make sure the phone is unlocked, that "
    "Termux is installed, and that ~/start-phone-llama.sh exists in Termux "
    "(see the README's phone setup)."
)
_LOCKED = (
    "The phone is locked, so Phone Link cannot type the start command. "
    "Unlock the phone and send the message again."
)
KEYGUARD_SETTLE_S = 0.5


class PhoneStarter(QObject):
    """Bring Termux up and run the phone's start script through ADB."""

    starting = Signal()
    ready = Signal()
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str | None,
        parent: QObject | None = None,
        *,
        port: int = DEFAULT_PORT,
        command: str = START_COMMAND,
        timeout_s: float = START_TIMEOUT_S,
        poll_s: float = POLL_S,
        focus_delay_s: float = FOCUS_DELAY_S,
        runner=None,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path or "adb"
        self.port = int(port)
        self.command = command
        self.timeout_s = float(timeout_s)
        self.poll_s = float(poll_s)
        self.focus_delay_s = float(focus_delay_s)
        self._run_adb = runner or self._run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False

    # -- public -----------------------------------------------------------

    def is_ready(self) -> bool:
        return models_answer(self.port)

    def ensure_started(self) -> None:
        """Start the phone server now, or emit ready if it already answers."""

        if self._active:
            return
        if self.is_ready():
            self.ready.emit()
            return
        self._active = True
        self._stop.clear()
        self.starting.emit()
        self._thread = threading.Thread(
            target=self._run_steps,
            name="phone-link-phone-start",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop waiting for the server.  A server already starting is left on."""

        self._stop.set()
        self._active = False

    # -- worker -----------------------------------------------------------

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def _call(self, *args: str) -> subprocess.CompletedProcess:
        command = [self.adb_path, *args]
        try:
            return self._run_adb(command)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def _fail(self, message: str) -> None:
        if not self._stop.is_set():
            self.failed.emit(message)

    def _run_steps(self) -> None:
        try:
            self._start()
        except Exception as exc:
            self._fail(f"Could not start the phone model: {exc}")
        finally:
            self._active = False

    def _start(self) -> None:
        if self._stop.is_set():
            return

        forward = self._call(
            "forward", f"tcp:{self.port}", f"tcp:{self.port}"
        )
        if forward.returncode != 0:
            detail = (forward.stderr or "").strip()
            self._fail(
                "Could not forward the phone's model port: "
                + (detail or "adb forward failed")
            )
            return

        packages = self._call("shell", "pm", "list", "packages", "com.termux")
        if packages.returncode != 0 or "package:com.termux" not in packages.stdout:
            self._fail(_MISSING_TERMUX)
            return

        # Wake the screen and drop a non-secure keyguard so keystrokes land in
        # Termux.  A secure lock cannot be dismissed, so check before typing:
        # the command must not be typed into a PIN field.
        self._call("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        self._call("shell", "wm", "dismiss-keyguard")
        if self._stop.wait(KEYGUARD_SETTLE_S):
            return
        windows = self._call("shell", "dumpsys", "window")
        if "isKeyguardShowing=true" in windows.stdout:
            self._fail(_LOCKED)
            return

        self._call("shell", "am", "start", "-n", TERMUX_ACTIVITY)
        if self._stop.wait(self.focus_delay_s):
            return

        self._call("shell", "input", "text", self.command)
        self._call("shell", "input", "keyevent", "66")

        deadline = time.monotonic() + self.timeout_s
        while not self._stop.is_set():
            if models_answer(self.port):
                self.ready.emit()
                return
            if time.monotonic() >= deadline:
                break
            if self._stop.wait(self.poll_s):
                return
        if not self._stop.is_set():
            self.failed.emit(_FAILED_START)
