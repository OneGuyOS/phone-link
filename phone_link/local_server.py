"""Start the local llama.cpp model on demand.

Phone Link's lightweight provider is an OpenAI-compatible ``llama-server`` on
this computer.  Rather than requiring a separately launched server, the app
starts one the first time the provider is used and stops it when the window
closes.  A server that was already running is left alone.

The binary and model are found in this order:

* ``$PHONE_LINK_LLAMA_SERVER`` and ``$PHONE_LINK_LOCAL_MODEL``;
* ``llama-server`` on ``PATH``, plus the usual build directories;
* a ``.gguf`` in ``$XDG_DATA_HOME/phone-link/models`` (or
  ``~/.local/share/phone-link/models``), then in ``~/zara/models``.

Nothing here downloads a model or talks to a network service; the server is
bound to ``127.0.0.1`` only.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

DEFAULT_PORT = 18089
DEFAULT_ALIAS = "zara-fast"
DEFAULT_CONTEXT = 4096
START_TIMEOUT_MS = 120_000
POLL_MS = 400

SERVER_ENV = "PHONE_LINK_LLAMA_SERVER"
MODEL_ENV = "PHONE_LINK_LOCAL_MODEL"

_MISSING_SERVER = (
    "Could not start the local model: llama.cpp's llama-server was not found. "
    "Install it, or set PHONE_LINK_LLAMA_SERVER to the binary path."
)
_MISSING_MODEL = (
    "Could not start the local model: no .gguf model was found. Set "
    "PHONE_LINK_LOCAL_MODEL to a model file."
)


def _home() -> Path:
    return Path.home()


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else _home() / ".local" / "share"
    return root / "phone-link"


def default_model_dirs() -> list[Path]:
    return [_data_dir() / "models", _home() / "zara" / "models"]


def find_server() -> str | None:
    """The llama-server binary to use, if one can be found."""

    configured = os.environ.get(SERVER_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    found = shutil.which("llama-server")
    if found:
        return found
    for candidate in (
        _home() / "zara" / "llama.cpp" / "build" / "bin" / "llama-server",
        _home() / "llama.cpp" / "build" / "bin" / "llama-server",
        _home() / ".local" / "bin" / "llama-server",
        Path("/usr/local/bin/llama-server"),
        Path("/usr/bin/llama-server"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_model() -> str | None:
    """The first usable GGUF model, preferring the configured one."""

    configured = os.environ.get(MODEL_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    for directory in default_model_dirs():
        if not directory.is_dir():
            continue
        models = sorted(directory.glob("*.gguf"))
        if not models:
            continue
        preferred = [path for path in models if "olmoe" in path.name.lower()]
        return str((preferred or models)[0])
    return None


def default_threads() -> int:
    cores = os.cpu_count() or 4
    return max(1, min(8, cores // 2))


def server_argv(
    server: str,
    model: str,
    *,
    port: int = DEFAULT_PORT,
    context: int = DEFAULT_CONTEXT,
    threads: int = 0,
    alias: str = DEFAULT_ALIAS,
) -> list[str]:
    """The command that starts a private, CPU-only llama.cpp server."""

    args = [
        server,
        "-m",
        model,
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port)),
        "--ctx-size",
        str(int(context)),
        "--parallel",
        "1",
        "--n-gpu-layers",
        "0",
        "--jinja",
        "--alias",
        alias,
        "--threads",
        str(int(threads) if threads else default_threads()),
    ]
    return args


def port_open(port: int = DEFAULT_PORT, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def models_answer(port: int = DEFAULT_PORT, timeout: float = 0.4) -> bool:
    """True when the server is loaded far enough to answer /v1/models."""

    url = f"http://127.0.0.1:{int(port)}/v1/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


class LocalServer(QObject):
    """Own a llama-server process started for this app, if it started one."""

    starting = Signal()
    ready = Signal()
    failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        port: int = DEFAULT_PORT,
        server_path: str | None = None,
        model_path: str | None = None,
        start_timeout_ms: int = START_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        self.port = int(port)
        self._server_path = server_path
        self._model_path = model_path
        self._start_timeout_ms = int(start_timeout_ms)
        self._process: QProcess | None = None
        self._started_by_us = False
        self._waiting = False
        self._deadline = 0.0
        self._stderr = ""
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def is_ready(self) -> bool:
        return models_answer(self.port)

    def ensure_started(self) -> None:
        """Emit ready now, or start a server and emit ready when it answers."""

        if self._waiting:
            return
        if self.is_ready():
            QTimer.singleShot(0, self.ready.emit)
            return

        self._waiting = True
        self._deadline = time.monotonic() + self._start_timeout_ms / 1000.0
        self.starting.emit()

        if port_open(self.port):
            # Something is already listening (probably a server still loading
            # its model); wait for it instead of fighting over the port.
            self._timer.start()
            return

        server = self._server_path or find_server()
        if not server:
            self._fail(_MISSING_SERVER)
            return
        if not os.path.isfile(server):
            self._fail(
                f"Could not start the local model: {server} does not exist. "
                f"Check {SERVER_ENV}."
            )
            return
        model = self._model_path or find_model()
        if not model:
            self._fail(_MISSING_MODEL)
            return
        if not os.path.isfile(model):
            self._fail(
                f"Could not start the local model: {model} does not exist. "
                f"Check {MODEL_ENV}."
            )
            return

        process = QProcess(self)
        self._process = process
        self._started_by_us = True
        self._stderr = ""
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._process_finished)
        process.start(server, server_argv(server, model, port=self.port)[1:])
        self._timer.start()

    def stop(self) -> None:
        """Stop only a server this instance started."""

        self._timer.stop()
        self._waiting = False
        process = self._process
        self._process = None
        if not self._started_by_us or process is None:
            return
        self._started_by_us = False
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(1500):
                process.kill()
                process.waitForFinished(500)
        process.deleteLater()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        self._stderr += bytes(process.readAllStandardError()).decode(
            "utf-8", "replace"
        )
        if len(self._stderr) > 8000:
            self._stderr = self._stderr[-8000:]

    def _process_finished(self, code: int, _status) -> None:
        if not self._waiting:
            return
        self._read_stderr()
        lines = [line.strip() for line in self._stderr.splitlines() if line.strip()]
        detail = lines[-1][-300:] if lines else ""
        message = "The local model server exited before it was ready"
        if code:
            message += f" (code {code})"
        message += f".\n{detail}" if detail else "."
        self._fail(message)

    def _poll(self) -> None:
        if self.is_ready():
            self._timer.stop()
            self._waiting = False
            self.ready.emit()
            return
        if time.monotonic() >= self._deadline:
            self._fail(
                "The local model did not become ready in time. It may still be "
                "loading; try sending again in a moment."
            )
            return
        process = self._process
        if (
            self._started_by_us
            and process is not None
            and process.state() == QProcess.ProcessState.NotRunning
            and not models_answer(self.port, 0.2)
        ):
            # finished() normally catches this; this is a late backstop.
            self._process_finished(-1, QProcess.ExitStatus.NormalExit)

    def _fail(self, message: str) -> None:
        self._timer.stop()
        self._waiting = False
        process = self._process
        self._process = None
        if self._started_by_us and process is not None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(500)
            process.deleteLater()
        self._started_by_us = False
        self.failed.emit(message)
