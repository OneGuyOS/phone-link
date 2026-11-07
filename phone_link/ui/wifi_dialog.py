"""In-app QR pairing for Android wireless debugging."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..wifi import make_qr_pairing, pair_with_qr
from .icons import glyph_pixmap
from .qr import INSTALL_HINT, qr_pixmap
from .theme import ACCENT, ACCENT_SOFT, DANGER, DIM, MUTED, SUCCESS, TEXT


class _QrWorker(QThread):
    done = Signal(bool, str)
    progress = Signal(str)

    def __init__(
        self,
        adb_path: str,
        service_name: str,
        password: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._adb_path = adb_path
        self._service_name = service_name
        self._password = password
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            ok, message = pair_with_qr(
                self._adb_path,
                self._service_name,
                self._password,
                stop=self._stop.is_set,
                progress=self.progress.emit,
            )
        except Exception as exc:  # a worker thread must not kill the app
            ok, message = False, f"QR pairing failed: {exc}"
        if not self._stop.is_set():
            self.done.emit(ok, message)


class WifiConnectDialog(QDialog):
    connected = Signal(str)

    def __init__(self, adb_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.adb_path = adb_path
        self._qr_worker: _QrWorker | None = None
        self._qr_pairing_started = False

        self.setWindowTitle("Phone Link — connect over Wi-Fi")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setMaximumWidth(700)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(13)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(13)
        mark = QLabel(self)
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(glyph_pixmap("wifi", ACCENT, 28))
        mark.setStyleSheet(f"background: {ACCENT_SOFT}; border-radius: 11px;")
        header.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(2)
        title = QLabel("Connect over Wi-Fi", self)
        title.setStyleSheet(f"color: {TEXT}; font-size: 21px; font-weight: 600;")
        heading.addWidget(title)
        subtitle = QLabel(
            "Pair Android wireless debugging by scanning a QR code.",
            self,
        )
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        root.addLayout(header)

        instructions = QLabel(
            "On the phone, open Settings → Developer options → Wireless debugging, "
            "tap Pair device with QR code, and point the phone camera at this code. "
            "Nothing needs to be typed.",
            self,
        )
        instructions.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        instructions.setTextFormat(Qt.TextFormat.PlainText)
        instructions.setWordWrap(True)
        root.addWidget(instructions)

        self.qr_image = QLabel(self)
        self.qr_image.setFixedSize(296, 296)
        self.qr_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_image.setStyleSheet(
            "background: white; color: #20262d; border-radius: 10px; "
            "font-size: 11px; padding: 8px;"
        )
        self.qr_image.setTextFormat(Qt.TextFormat.PlainText)
        self.qr_image.setWordWrap(True)
        root.addWidget(self.qr_image, 0, Qt.AlignmentFlag.AlignHCenter)

        note = QLabel(
            "The code is generated on this computer and expires after two minutes.",
            self,
        )
        note.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        root.addWidget(note)

        self.status = QLabel("", self)
        self.status.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(32)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        buttons.setSpacing(8)
        self.qr_retry = QPushButton("Show a new code", self)
        self.qr_retry.setObjectName("GhostButton")
        self.qr_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.qr_retry.clicked.connect(self._start_qr)
        buttons.addWidget(self.qr_retry)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

        self._start_qr()

    def _set_status(self, message: str, tone: str = DIM) -> None:
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {tone}; font-size: 11px;")

    def _start_qr(self) -> None:
        if self._qr_pairing_started:
            return
        self._stop_qr_worker()
        self.cancel_button.setEnabled(True)
        self.qr_retry.setEnabled(True)

        service_name, password, payload = make_qr_pairing()
        pixmap = qr_pixmap(payload)
        if pixmap is None:
            self.qr_image.clear()
            self.qr_image.setText(
                "QR pairing needs the python3-segno package.\n\n"
                f"{INSTALL_HINT}"
            )
            self.qr_retry.setDisabled(True)
            self._set_status(
                f"Install python3-segno ({INSTALL_HINT}) and reopen this dialog.",
                DANGER,
            )
            return

        self.qr_image.setText("")
        self.qr_image.setPixmap(pixmap)
        self._set_status("Waiting for the phone to scan the code…", MUTED)

        worker = _QrWorker(self.adb_path, service_name, password, self)
        worker.progress.connect(self._on_qr_progress)
        worker.done.connect(self._on_qr_done)
        worker.finished.connect(worker.deleteLater)
        self._qr_worker = worker
        worker.start()

    def _on_qr_progress(self, message: str) -> None:
        # Pairing can take a few seconds; do not let the dialog close under the
        # worker while adb is mid-handshake.
        self._qr_pairing_started = True
        self.qr_retry.setDisabled(True)
        self.cancel_button.setDisabled(True)
        self._set_status(message, ACCENT)

    def _on_qr_done(self, ok: bool, message: str) -> None:
        self._qr_worker = None
        self._qr_pairing_started = False
        self.cancel_button.setEnabled(True)
        self.qr_retry.setEnabled(True)
        self._set_status(message, SUCCESS if ok else DANGER)
        if ok:
            self.cancel_button.setDisabled(True)
            self.qr_retry.setDisabled(True)
            self.connected.emit(message)
            QTimer.singleShot(900, self.accept)

    def _stop_qr_worker(self) -> None:
        worker = self._qr_worker
        self._qr_worker = None
        if worker is None:
            return
        worker.blockSignals(True)
        worker.stop()
        worker.wait(5_000)

    def reject(self) -> None:
        if (
            self._qr_worker is not None
            and self._qr_worker.isRunning()
            and self._qr_pairing_started
        ):
            return
        self._stop_qr_worker()
        super().reject()
