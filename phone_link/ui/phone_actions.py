"""Phone actions that KDE Connect can run: ring, message and volume."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..models import PhoneSink
from .icons import glyph_icon
from .theme import DANGER, DIM, MUTED, TEXT, TEXT_SOFT


class PhoneActionsMenu(QMenu):
    """The header's phone menu.

    A menu rather than more header buttons: these are occasional actions, and
    the menu also has room for the mobile-network status.
    """

    findRequested = Signal()
    stopRingingRequested = Signal()
    pingRequested = Signal()
    smsRequested = Signal()
    volumeRequested = Signal()
    openMessagesRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = self.addAction("Mobile network: checking…")
        self._status.setEnabled(False)
        self.addSeparator()

        self.find_action = self.addAction(glyph_icon("phone", TEXT_SOFT, 16), "Find my phone")
        self.find_action.triggered.connect(lambda _checked=False: self.findRequested.emit())
        self.stop_action = self.addAction(
            glyph_icon("close", TEXT_SOFT, 16), "Stop ringing"
        )
        self.stop_action.triggered.connect(
            lambda _checked=False: self.stopRingingRequested.emit()
        )
        self.ping_action = self.addAction(glyph_icon("bell", TEXT_SOFT, 16), "Ping phone")
        self.ping_action.triggered.connect(lambda _checked=False: self.pingRequested.emit())
        self.sms_action = self.addAction(
            glyph_icon("message", TEXT_SOFT, 16), "Send a text message…"
        )
        self.sms_action.triggered.connect(lambda _checked=False: self.smsRequested.emit())
        self.addSeparator()
        self.volume_action = self.addAction(
            glyph_icon("speaker", TEXT_SOFT, 16), "Phone volume…"
        )
        self.volume_action.triggered.connect(lambda _checked=False: self.volumeRequested.emit())
        self.messages_action = self.addAction(
            glyph_icon("external", TEXT_SOFT, 16), "Open messages on the phone"
        )
        self.messages_action.triggered.connect(
            lambda _checked=False: self.openMessagesRequested.emit()
        )

        # Each action needs a plugin the desktop daemon must have loaded for
        # this device.  Not every KDE Connect client supports every plugin:
        # Android's volume plugin only lets the phone control the computer,
        # so ``remotesystemvolume`` never loads for an Android phone.
        self._actions = (
            (self.find_action, "kdeconnect_findmyphone"),
            (self.stop_action, "kdeconnect_findmyphone"),
            (self.ping_action, "kdeconnect_ping"),
            (self.sms_action, "kdeconnect_sms"),
            (self.volume_action, "kdeconnect_remotesystemvolume"),
            (self.messages_action, "kdeconnect_sms"),
        )

    def set_available(self, available: bool, plugins: set[str] | None = None) -> None:
        """Show and enable the actions the phone supports.

        ``plugins`` is the daemon's loaded-plugin list; ``None`` means it
        could not be read, so every action stays visible and the daemon
        reports any failure when the action runs.  Actions whose plugin is
        absent are hidden rather than disabled: for example, Android's
        KDE Connect volume plugin only lets the phone control the computer,
        so the phone-volume action would never work there.
        """

        for action, plugin in self._actions:
            supported = plugins is None or plugin in plugins
            action.setVisible(supported)
            action.setEnabled(available and supported)

    def set_cellular_status(
        self, status: tuple[str, int] | None, *, connected: bool = True
    ) -> None:
        if not connected:
            self._status.setText("Mobile network: phone not connected")
            return
        if status is None:
            self._status.setText("Mobile network: no report from the phone")
            return
        network, strength = status
        bars = max(0, min(int(strength), 4))
        self._status.setText(f"Mobile network: {network} · signal {bars}/4")


class SmsDialog(QDialog):
    """Compose one text message; the phone sends it through KDE Connect."""

    def __init__(self, recent: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phone Link — send a text message")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)

        title = QLabel("Send a text message", self)
        title.setStyleSheet(f"color: {TEXT}; font-size: 20px; font-weight: 600;")
        root.addWidget(title)
        intro = QLabel(
            "Your phone sends the message with its own SIM. KDE Connect's SMS plugin must be on.",
            self,
        )
        intro.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        root.addWidget(intro)
        root.addSpacing(4)

        recipient_label = QLabel("Recipient", self)
        recipient_label.setStyleSheet(
            f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
        )
        root.addWidget(recipient_label)
        self.recipients = QLineEdit(self)
        self.recipients.setPlaceholderText("Phone number, or several separated by commas")
        if recent:
            self.recipients.setText(", ".join(recent))
        self.recipients.setClearButtonEnabled(True)
        root.addWidget(self.recipients)

        message_label = QLabel("Message", self)
        message_label.setStyleSheet(
            f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
        )
        root.addWidget(message_label)
        self.body = QPlainTextEdit(self)
        self.body.setPlaceholderText("Type the message…")
        self.body.setFixedHeight(128)
        root.addWidget(self.body)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.counter = QLabel("0 characters", self)
        self.counter.setStyleSheet(f"color: {DIM}; font-size: 11px;")
        footer.addWidget(self.counter)
        footer.addStretch(1)
        self.error = QLabel("", self)
        self.error.setStyleSheet(f"color: {DANGER}; font-size: 11px;")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        footer.addWidget(self.error)
        root.addLayout(footer)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("GhostButton")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.send_button = QPushButton("Send", self)
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._submit)
        buttons.addWidget(self.send_button)
        root.addLayout(buttons)

        self.body.textChanged.connect(self._update_counter)
        self.recipients.returnPressed.connect(self.body.setFocus)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._submit)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._submit)
        self.recipients.setFocus()
        self._update_counter()

    def addresses(self) -> list[str]:
        raw = self.recipients.text().replace(";", ",").replace("\n", ",")
        numbers: list[str] = []
        for part in raw.split(","):
            number = part.strip()
            if number and number not in numbers:
                numbers.append(number)
        return numbers

    def message(self) -> str:
        return self.body.toPlainText()

    def _update_counter(self) -> None:
        count = len(self.body.toPlainText())
        self.counter.setText(f"{count} character{'s' if count != 1 else ''}")

    def _submit(self) -> None:
        self.error.clear()
        numbers = self.addresses()
        if not numbers:
            self.show_error("Add at least one phone number.")
            return
        if any(not any(character.isdigit() for character in number) for number in numbers):
            self.show_error("That does not look like a phone number.")
            return
        if not self.message().strip():
            self.show_error("Type a message before sending it.")
            return
        self.accept()

    def show_error(self, message: str) -> None:
        self.error.setText(message)


class VolumeDialog(QDialog):
    """The phone's audio outputs, each with a volume slider and a mute toggle.

    Changes are debounced so dragging a slider does not flood the phone, and
    updates the phone reports itself are applied when the person is not
    dragging.
    """

    volumeChanged = Signal(str, int)
    mutedChanged = Signal(str, bool)

    def __init__(self, sinks: tuple[PhoneSink, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phone Link — phone volume")
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.setModal(True)
        self.setMinimumWidth(430)

        self._sinks = {sink.name: sink for sink in sinks}
        self._current = sinks[0].name if sinks else ""
        self._last_sent = self._sinks[self._current].volume if self._current else 0
        self._user_dragging = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        title = QLabel("Phone volume", self)
        title.setStyleSheet(f"color: {TEXT}; font-size: 20px; font-weight: 600;")
        root.addWidget(title)
        intro = QLabel(
            "Changes apply to the phone immediately. KDE Connect's Remote System Volume plugin must be on.",
            self,
        )
        intro.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # A button with a menu rather than a combo box: the button carries a
        # visible menu arrow with the application's own styling, and the menu
        # already matches the header's action menu.
        self.sink_actions: dict[str, QAction] = {}
        self.sink_button = QPushButton(self)
        self.sink_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sink_menu = QMenu(self.sink_button)
        for sink in sinks:
            action = self.sink_menu.addAction(sink.label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, name=sink.name: self._load_sink(name)
            )
            self.sink_actions[sink.name] = action
        self.sink_button.setMenu(self.sink_menu)
        self.sink_button.setStyleSheet("text-align: left; padding-left: 12px;")
        self.sink_button.setVisible(len(sinks) > 1)
        root.addWidget(self.sink_button)

        self.sink_label = QLabel(self)
        self.sink_label.setStyleSheet(f"color: {TEXT_SOFT}; font-size: 13px; font-weight: 600;")
        self.sink_label.setTextFormat(Qt.TextFormat.PlainText)
        self.sink_label.setVisible(len(sinks) <= 1)
        root.addWidget(self.sink_label)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(12)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setObjectName("PhoneVolume")
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self._on_slider_value)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        slider_row.addWidget(self.slider, 1)
        self.value_label = QLabel("0%", self)
        self.value_label.setStyleSheet(
            f"color: {TEXT}; font-size: 13px; font-weight: 600;"
        )
        self.value_label.setMinimumWidth(42)
        self.value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        slider_row.addWidget(self.value_label)
        root.addLayout(slider_row)

        self.mute = QCheckBox("Mute this output", self)
        self.mute.toggled.connect(self._on_mute_toggled)
        root.addWidget(self.mute)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.setObjectName("PrimaryButton")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(140)
        self._send_timer.timeout.connect(self._flush_volume)

        self._load_sink(self._current)

    def current_sink(self) -> str:
        return self._current

    def apply_volume(self, name: str, volume: int) -> None:
        """Apply a volume the phone reported itself."""

        sink = self._sinks.get(name)
        if sink is None:
            return
        volume = max(0, min(int(volume), sink.max_volume))
        self._sinks[name] = replace(sink, volume=volume)
        if name != self._current:
            return
        if self._user_dragging:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(volume)
        self.slider.blockSignals(False)
        self._last_sent = volume
        self._update_value_label(volume)

    def apply_muted(self, name: str, muted: bool) -> None:
        sink = self._sinks.get(name)
        if sink is None:
            return
        self._sinks[name] = replace(sink, muted=bool(muted))
        if name != self._current:
            return
        self.mute.blockSignals(True)
        self.mute.setChecked(bool(muted))
        self.mute.blockSignals(False)

    def _load_sink(self, name: str) -> None:
        sink = self._sinks.get(name)
        if sink is None:
            return
        self._current = name
        self._send_timer.stop()
        self.slider.blockSignals(True)
        self.slider.setRange(0, sink.max_volume)
        self.slider.setValue(sink.volume)
        self.slider.blockSignals(False)
        self.mute.blockSignals(True)
        self.mute.setChecked(sink.muted)
        self.mute.blockSignals(False)
        self._last_sent = sink.volume
        self._update_value_label(sink.volume)
        self.sink_label.setText(sink.label)
        self.sink_button.setText(sink.label)
        for action_name, action in self.sink_actions.items():
            action.setChecked(action_name == name)

    def _on_slider_pressed(self) -> None:
        self._user_dragging = True

    def _on_slider_value(self, value: int) -> None:
        self._update_value_label(value)
        if not self._user_dragging:
            self._send_timer.start()

    def _on_slider_released(self) -> None:
        self._user_dragging = False
        self._flush_volume()

    def _on_mute_toggled(self, muted: bool) -> None:
        sink = self._sinks.get(self._current)
        if sink is None or sink.muted == bool(muted):
            return
        self._sinks[self._current] = replace(sink, muted=bool(muted))
        self.mutedChanged.emit(self._current, bool(muted))

    def _flush_volume(self) -> None:
        value = self.slider.value()
        if value == self._last_sent:
            return
        self._last_sent = value
        sink = self._sinks.get(self._current)
        if sink is not None:
            self._sinks[self._current] = replace(sink, volume=value)
        self.volumeChanged.emit(self._current, value)

    def _update_value_label(self, value: int) -> None:
        # Android reports a raw stream volume with its own maximum (often 15
        # or 25), not a percentage.
        sink = self._sinks.get(self._current)
        maximum = sink.max_volume if sink is not None else 100
        percent = round(value * 100 / maximum) if maximum > 0 else 0
        self.value_label.setText(f"{percent}%")
