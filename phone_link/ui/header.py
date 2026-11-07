"""The in-app title bar: phone name, transport and battery."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon_pixmap, glyph_icon, glyph_pixmap
from .theme import (
    ACCENT,
    BORDER,
    CHARGING,
    DANGER,
    DIM,
    SUCCESS,
    SURFACE_ALT,
    TEXT_SOFT,
    WARNING,
)


class BatteryGlyph(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level: int | None = None
        self._charging = False
        self.setFixedSize(31, 18)

    def set_state(self, level: int | None, charging: bool) -> None:
        self._level = level
        self._charging = charging
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(1.5, 2.5, 23.0, 13.0)
        outline = QColor("#536173")
        p.setPen(QPen(outline, 1.35))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, 3.4, 3.4)
        p.drawRoundedRect(QRectF(24.8, 6.0, 2.8, 6.0), 1.2, 1.2)

        level = self._level
        if level is not None:
            if level <= 15:
                fill = QColor(DANGER)
            elif level <= 30:
                fill = QColor(WARNING)
            elif self._charging:
                fill = QColor(CHARGING)
            else:
                fill = QColor(SUCCESS)
            inner = QRectF(body.x() + 2.3, body.y() + 2.3, max(0.0, (body.width() - 4.6) * level / 100.0), body.height() - 4.6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            if inner.width() > 0:
                p.drawRoundedRect(inner, 1.8, 1.8)

        if self._charging:
            path = QPainterPath()
            path.moveTo(14.2, 3.4)
            path.lineTo(9.5, 10.0)
            path.lineTo(13.1, 10.0)
            path.lineTo(11.8, 14.8)
            path.lineTo(17.0, 8.0)
            path.lineTo(13.4, 8.0)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#07141d"))
            p.drawPath(path)
        p.end()


class ConnectionPill(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConnectionPill")
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 0, 10, 0)
        layout.setSpacing(5)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(15, 15)
        self.text_label = QLabel("Offline", self)
        self.text_label.setObjectName("PillText")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        self._apply("Offline", DIM, "wifi")

    def set_connection(self, label: str) -> None:
        online = label in {"USB", "Wi-Fi"}
        glyph = "usb" if label == "USB" else "wifi"
        self._apply(label, ACCENT if online else DIM, glyph)

    def _apply(self, text: str, color: str, glyph: str) -> None:
        self.text_label.setText(text)
        self.text_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        self.icon_label.setPixmap(glyph_pixmap(glyph, color, 15))
        background = "#162b36" if color == ACCENT else SURFACE_ALT
        border = "#255065" if color == ACCENT else BORDER
        self.setStyleSheet(
            f"QFrame#ConnectionPill {{ background: {background}; border: 1px solid {border}; border-radius: 10px; }}"
        )


class HeaderBar(QFrame):
    refreshRequested = Signal()
    clipboardRequested = Signal()
    autoClipboardToggled = Signal(bool)
    wifiRequested = Signal()
    filesRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(78)

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 13, 14, 13)
        root.setSpacing(14)

        self.mark = QLabel(self)
        self.mark.setFixedSize(46, 46)
        self.mark.setPixmap(app_icon_pixmap(46))
        root.addWidget(self.mark)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(1)
        self.brand = QLabel("PHONE LINK", self)
        self.brand.setObjectName("Brand")
        self.phone_name = QLabel("No phone connected", self)
        self.phone_name.setObjectName("PhoneName")
        self.phone_name.setTextFormat(Qt.TextFormat.PlainText)
        self.phone_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.subtitle = QLabel("Waiting for ADB and KDE Connect", self)
        self.subtitle.setObjectName("Subtitle")
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        labels.addWidget(self.brand)
        labels.addWidget(self.phone_name)
        labels.addWidget(self.subtitle)
        root.addLayout(labels, 1)

        self.live_dot = QLabel("●", self)
        self.live_dot.setStyleSheet(f"color: {DIM}; font-size: 14px;")
        root.addWidget(self.live_dot)

        self.connection = ConnectionPill(self)
        root.addWidget(self.connection)

        battery_box = QWidget(self)
        battery_layout = QHBoxLayout(battery_box)
        battery_layout.setContentsMargins(4, 0, 4, 0)
        battery_layout.setSpacing(6)
        self.battery_glyph = BatteryGlyph(battery_box)
        self.battery_text = QLabel("—", battery_box)
        self.battery_text.setObjectName("BatteryText")
        self.battery_text.setMinimumWidth(35)
        battery_layout.addWidget(self.battery_glyph)
        battery_layout.addWidget(self.battery_text)
        root.addWidget(battery_box)

        self.wifi_button = QToolButton(self)
        self.wifi_button.setIcon(glyph_icon("wifi", TEXT_SOFT, 19))
        self.wifi_button.setIconSize(QSize(19, 19))
        self.wifi_button.setToolTip("Connect over Wi-Fi (wireless debugging)")
        self.wifi_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wifi_button.clicked.connect(self.wifiRequested)
        root.addWidget(self.wifi_button)

        self.files_button = QToolButton(self)
        self.files_button.setIcon(glyph_icon("folder", TEXT_SOFT, 19))
        self.files_button.setIconSize(QSize(19, 19))
        self.files_button.setToolTip(
            "Browse the phone's files (mounts it over KDE Connect)"
        )
        self.files_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_button.clicked.connect(self.filesRequested)
        root.addWidget(self.files_button)

        self.clipboard_button = QToolButton(self)
        self.clipboard_button.setIcon(glyph_icon("clipboard", TEXT_SOFT, 19))
        self.clipboard_button.setIconSize(QSize(19, 19))
        self.clipboard_button.setToolTip("Send the desktop clipboard to the phone")
        self.clipboard_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clipboard_button.clicked.connect(self.clipboardRequested)
        root.addWidget(self.clipboard_button)

        self.auto_clipboard_button = QToolButton(self)
        self.auto_clipboard_button.setCheckable(True)
        self.auto_clipboard_button.setIcon(glyph_icon("sync", TEXT_SOFT, 19))
        self.auto_clipboard_button.setIconSize(QSize(19, 19))
        self.auto_clipboard_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_clipboard_button.toggled.connect(self._on_auto_clipboard_toggled)
        root.addWidget(self.auto_clipboard_button)
        self._render_auto_clipboard(False)

        self.phone_button = QToolButton(self)
        self.phone_button.setIcon(glyph_icon("more", TEXT_SOFT, 19))
        self.phone_button.setIconSize(QSize(19, 19))
        self.phone_button.setToolTip("Phone actions: ring, message, volume")
        self.phone_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.phone_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        root.addWidget(self.phone_button)

        self.refresh_button = QToolButton(self)
        self.refresh_button.setIcon(glyph_icon("refresh", TEXT_SOFT, 19))
        self.refresh_button.setIconSize(QSize(19, 19))
        self.refresh_button.setToolTip("Refresh phone and notification state")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.refreshRequested)
        root.addWidget(self.refresh_button)

        self._full_name = "No phone connected"

    def set_data(
        self,
        *,
        phone_name: str | None,
        connection: str,
        battery: int | None,
        charging: bool,
        subtitle: str,
        online: bool,
    ) -> None:
        self._full_name = phone_name or "No phone connected"
        self._elide_name()
        self.subtitle.setText(subtitle)
        self.connection.set_connection(connection)
        self.battery_glyph.set_state(battery, charging)
        self.battery_text.setText("—" if battery is None else f"{battery}%")
        color = SUCCESS if online else DIM
        self.live_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_name()

    def _elide_name(self) -> None:
        metrics = self.phone_name.fontMetrics()
        available = max(180, self.width() - 420)
        self.phone_name.setText(
            metrics.elidedText(self._full_name, Qt.TextElideMode.ElideRight, available)
        )
        self.phone_name.setToolTip(self._full_name if self.phone_name.text() != self._full_name else "")

    def set_phone_menu(self, menu: QMenu) -> None:
        self.phone_button.setMenu(menu)

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_button.setDisabled(refreshing)

    def set_auto_clipboard(self, checked: bool) -> None:
        button = self.auto_clipboard_button
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)
        self._render_auto_clipboard(checked)

    def _on_auto_clipboard_toggled(self, checked: bool) -> None:
        self._render_auto_clipboard(checked)
        self.autoClipboardToggled.emit(checked)

    def _render_auto_clipboard(self, checked: bool) -> None:
        self.auto_clipboard_button.setIcon(
            glyph_icon("sync", ACCENT if checked else TEXT_SOFT, 19)
        )
        self.auto_clipboard_button.setToolTip(
            "Automatic clipboard sync is on — copied text is sent to the phone"
            if checked
            else "Automatically send desktop clipboard changes to the phone"
        )
