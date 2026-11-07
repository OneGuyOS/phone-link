"""KDE Connect notification panel."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..models import KdeSnapshot, NotificationItem
from .icons import (
    glyph_icon,
    glyph_pixmap,
    icon_from_file,
    notification_badge,
)
from .theme import ACCENT, DANGER, DIM, MUTED, SUCCESS


def _items_signature(items: tuple[NotificationItem, ...]) -> tuple:
    return tuple(
        (
            item.id,
            item.app_name,
            item.title,
            item.text,
            item.ticker,
            item.icon_path,
            item.dismissable,
            item.reply_id,
        )
        for item in items
    )


def notification_matches(item: NotificationItem, query: str) -> bool:
    """Case-insensitive search across app name, heading and body."""

    needle = query.strip().casefold()
    if not needle:
        return True
    haystack = "\n".join((item.app_name, item.heading, item.body)).casefold()
    return needle in haystack


def _short_time(when: datetime) -> str:
    now = datetime.now()
    delta = (now - when).total_seconds()
    if delta < 45:
        return "now"
    if delta < 3600:
        return f"{max(1, int(delta // 60))}m"
    if delta < 86400 and when.date() == now.date():
        return when.strftime("%H:%M")
    if when.year == now.year:
        return when.strftime("%d %b")
    return when.strftime("%d %b %Y")


class NotificationCard(QFrame):
    dismissRequested = Signal(str)
    replyRequested = Signal(str, str)

    def __init__(
        self,
        item: NotificationItem,
        parent: QWidget | None = None,
        *,
        recent: bool = False,
    ) -> None:
        super().__init__(parent)
        self.notification_id = item.id
        self.setObjectName("NotificationCard")
        if recent:
            self.setProperty("recent", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 11, 9, 12)
        layout.setSpacing(10)

        icon = QLabel(self)
        icon.setFixedSize(38, 38)
        pixmap = icon_from_file(item.icon_path, 38)
        if pixmap is None:
            pixmap = notification_badge(item.app_name, 38)
        icon.setPixmap(pixmap)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        content = QVBoxLayout()
        content.setContentsMargins(0, 1, 0, 0)
        content.setSpacing(3)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(7)
        app = QLabel(item.app_name or "Notification", self)
        app.setObjectName("CardApp")
        app.setTextFormat(Qt.TextFormat.PlainText)
        meta.addWidget(app)
        dot = QLabel("·", self)
        dot.setStyleSheet(f"color: {DIM};")
        meta.addWidget(dot)
        stamp_text = _short_time(item.received_at)
        if recent:
            stamp_text = f"{stamp_text} · cleared"
        stamp = QLabel(stamp_text, self)
        stamp.setObjectName("CardTime")
        meta.addWidget(stamp)
        meta.addStretch(1)
        content.addLayout(meta)

        heading = QLabel(item.heading, self)
        heading.setObjectName("CardTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        # Notification text often carries long unbreakable runs (URLs, or the
        # non-breaking-space padding Android apps append).  A wrapped QLabel
        # reports that run as its minimum width, which would stretch the whole
        # card list past the scroll viewport and clip every card on the right.
        heading.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(heading)

        body_text = item.body
        if len(body_text) > 900:
            body_text = body_text[:897].rstrip() + "…"
        if body_text:
            body = QLabel(body_text, self)
            body.setObjectName("CardBody")
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            body.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            content.addWidget(body)

        self.reply_button: QPushButton | None = None
        self.reply_row: QWidget | None = None
        self.reply_edit: QLineEdit | None = None
        self.reply_send: QPushButton | None = None
        if item.reply_id and not recent:
            self.reply_button = QPushButton("Reply", self)
            self.reply_button.setObjectName("GhostButton")
            self.reply_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.reply_button.setToolTip(f"Reply through {item.app_name} on the phone")
            self.reply_button.clicked.connect(self._open_reply)
            content.addWidget(self.reply_button, 0, Qt.AlignmentFlag.AlignLeft)

            self.reply_row = QWidget(self)
            reply_layout = QHBoxLayout(self.reply_row)
            reply_layout.setContentsMargins(0, 2, 0, 0)
            reply_layout.setSpacing(6)
            self.reply_edit = QLineEdit(self.reply_row)
            self.reply_edit.setPlaceholderText("Reply…")
            self.reply_edit.setMaxLength(2000)
            self.reply_edit.returnPressed.connect(self._submit_reply)
            self.reply_send = QPushButton("Send", self.reply_row)
            self.reply_send.setCursor(Qt.CursorShape.PointingHandCursor)
            self.reply_send.clicked.connect(self._submit_reply)
            reply_layout.addWidget(self.reply_edit, 1)
            reply_layout.addWidget(self.reply_send)
            self.reply_row.hide()
            content.addWidget(self.reply_row)

        layout.addLayout(content, 1)

        self.dismiss_button = QPushButton(self)
        self.dismiss_button.setObjectName("DismissButton")
        self.dismiss_button.setIcon(glyph_icon("close", MUTED, 13))
        self.dismiss_button.setIconSize(QSize(13, 13))
        self.dismiss_button.setFixedSize(26, 26)
        self.dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_button.setToolTip("Dismiss on the phone too")
        self.dismiss_button.clicked.connect(lambda: self.dismissRequested.emit(item.id))
        self.dismiss_button.setVisible(item.dismissable and not recent)
        layout.addWidget(self.dismiss_button, 0, Qt.AlignmentFlag.AlignTop)

    def _open_reply(self) -> None:
        if self.reply_button is not None:
            self.reply_button.hide()
        if self.reply_row is not None:
            self.reply_row.show()
        if self.reply_edit is not None:
            self.reply_edit.setFocus()

    def _submit_reply(self) -> None:
        if self.reply_edit is None or self.reply_row is None:
            return
        message = self.reply_edit.text().strip()
        if not message:
            return
        self.replyRequested.emit(self.notification_id, message)
        self.reply_edit.clear()
        self.reply_row.hide()
        if self.reply_button is not None:
            self.reply_button.show()


class FileDropZone(QFrame):
    """Where files from the desktop can be dropped for KDE Connect to send."""

    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self._connected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(9)

        self.icon = QLabel(self)
        self.icon.setFixedSize(22, 22)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon)

        self.label = QLabel(self)
        self.label.setObjectName("DropZoneLabel")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label, 1)

        self.set_connected(False)

    def set_connected(self, connected: bool, device_name: str = "") -> None:
        self._connected = connected
        if connected:
            target = device_name or "the phone"
            self.label.setText(f"Drop files here to send them to {target}")
            self.icon.setPixmap(glyph_pixmap("file", ACCENT, 20))
            self.setCursor(Qt.CursorShape.DragCopyCursor)
        else:
            self.label.setText("Connect a phone to send files")
            self.icon.setPixmap(glyph_pixmap("file", DIM, 20))
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setProperty("connected", connected)
        self._repolish()

    def dragEnterEvent(self, event) -> None:
        if self._connected and self._local_files(event.mimeData()):
            event.acceptProposedAction()
            self._set_active(True)
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._connected and self._local_files(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_active(False)
        paths = self._local_files(event.mimeData()) if self._connected else []
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.filesDropped.emit(paths)

    def _local_files(self, mime) -> list[str]:
        if mime is None or not mime.hasUrls():
            return []
        paths: list[str] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if os.path.isfile(path):
                paths.append(path)
        return paths

    def _set_active(self, active: bool) -> None:
        if bool(self.property("active")) == active:
            return
        self.setProperty("active", active)
        self._repolish()

    def _repolish(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)


class EmptyNotifications(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 30, 26, 30)
        layout.setSpacing(7)
        layout.addStretch(1)
        self.art = QLabel(self)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.art)
        layout.addSpacing(4)
        self.title = QLabel(self)
        self.title.setObjectName("OverlayTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title)
        self.body = QLabel(self)
        self.body.setObjectName("OverlayBody")
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.body)
        self.retry = QPushButton("Try again", self)
        self.retry.setObjectName("GhostButton")
        self.retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry.setToolTip("Refresh the phone and KDE Connect state")
        self.retry.hide()
        layout.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        self.set_empty("All caught up", "New notifications from your phone appear here.", icon="bell", tone=SUCCESS)

    def set_empty(
        self,
        title: str,
        body: str,
        *,
        icon: str = "bell",
        tone: str = MUTED,
        retry: bool = False,
    ) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.art.setPixmap(glyph_pixmap(icon, tone, 58))
        self.retry.setVisible(retry)


class NotificationsPanel(QFrame):
    dismissRequested = Signal(str)
    replyRequested = Signal(str, str)
    historyClearRequested = Signal()
    retryRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._signature: tuple | None = None
        self._device_name = ""
        self._snapshot: KdeSnapshot | None = None
        self._query = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        bell = QLabel(self)
        bell.setFixedSize(34, 34)
        bell.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bell.setPixmap(glyph_pixmap("bell", ACCENT, 25))
        bell.setStyleSheet("background: #17384c; border-radius: 9px;")
        header.addWidget(bell)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Notifications", self)
        title.setObjectName("PanelTitle")
        title_row.addWidget(title)
        self.count = QLabel("0", self)
        self.count.setObjectName("Badge")
        self.count.hide()
        title_row.addWidget(self.count)
        title_row.addStretch(1)
        titles.addLayout(title_row)
        self.subtitle = QLabel("KDE Connect", self)
        self.subtitle.setObjectName("PanelSubtitle")
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        root.addLayout(header)

        separator = QFrame(self)
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(separator)

        self.search = QLineEdit(self)
        self.search.setObjectName("NotificationSearch")
        self.search.setPlaceholderText("Search notifications")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search notifications")
        self.search.textChanged.connect(self._on_search_changed)
        clear_search = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search)
        clear_search.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear_search.activated.connect(self.search.clear)
        self.search.hide()
        root.addWidget(self.search)

        self.stack_host = QWidget(self)
        self.stack = QStackedLayout(self.stack_host)
        self.stack.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea(self.stack_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_host = QWidget(self.scroll)
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 2, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch(1)
        self.scroll.setWidget(self.cards_host)
        self.stack.addWidget(self.scroll)

        self.empty = EmptyNotifications(self.stack_host)
        self.empty.retry.clicked.connect(self.retryRequested)
        self.stack.addWidget(self.empty)
        root.addWidget(self.stack_host, 1)

        self.drop_zone = FileDropZone(self)
        self.drop_zone.filesDropped.connect(self.filesDropped)
        root.addWidget(self.drop_zone)

        self.footer = QLabel("Pair a phone in KDE Connect", self)
        self.footer.setObjectName("Dim")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.footer)

        self._set_empty(
            "Notifications need KDE Connect",
            "Start KDE Connect and pair your phone. Phone Link does not reimplement its protocol.",
            icon="bell",
            tone=MUTED,
        )

    def set_snapshot(self, snapshot: KdeSnapshot) -> None:
        self._device_name = snapshot.device.name if snapshot.device else ""
        self.subtitle.setText(
            f"{self._device_name} · KDE Connect" if self._device_name else "KDE Connect"
        )
        self.drop_zone.set_connected(snapshot.connected, self._device_name)
        count = len(snapshot.notifications)
        self.count.setText("99+" if count > 99 else str(count))
        self.count.setVisible(count > 0)

        self._snapshot = snapshot
        has_items = bool(snapshot.notifications or snapshot.history)
        if not has_items and self.search.text():
            self._query = ""
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.search.setVisible(has_items)

        signature = (
            snapshot.mode,
            snapshot.message,
            snapshot.error,
            _items_signature(snapshot.notifications),
            _items_signature(snapshot.history),
        )
        if signature == self._signature:
            return
        self._signature = signature

        if snapshot.notifications or snapshot.history:
            self._show_cards(snapshot)
            return

        self._set_empty(*self._empty_text(snapshot))
        self.footer.setText(self._footer_text(snapshot))

    def _footer_text(self, snapshot: KdeSnapshot) -> str:
        if snapshot.mode == "offline":
            return "KDE Connect is not running"
        if snapshot.mode == "no-device":
            return "Pair a phone in KDE Connect"
        if snapshot.mode == "unreachable":
            return "Waiting for the phone on the network"
        if snapshot.error:
            return "Notification sync is unavailable"
        if self._device_name:
            return f"Synced with {self._device_name} through KDE Connect"
        return "Synced through KDE Connect"

    def _empty_text(self, snapshot: KdeSnapshot) -> tuple[str, str, str, str, bool]:
        mode = snapshot.mode
        message = snapshot.message.strip()
        if mode == "offline":
            return (
                "KDE Connect is offline",
                message or "Start KDE Connect to sync notifications and battery.",
                "refresh",
                DANGER,
                True,
            )
        if mode == "no-device":
            return (
                "No phone in KDE Connect",
                message or "Pair your phone in KDE Connect first.",
                "phone",
                MUTED,
                False,
            )
        if mode == "unreachable":
            return (
                "Phone not reachable",
                message or "Check that the phone and this computer are on the same network.",
                "phone",
                MUTED,
                True,
            )
        if snapshot.error:
            return (
                "Notifications unavailable",
                message or "KDE Connect did not expose the notification plugin for this phone.",
                "bell",
                DANGER,
                True,
            )
        return (
            "All caught up",
            "New notifications from your phone appear here.",
            "bell",
            SUCCESS,
            False,
        )

    def _show_cards(self, snapshot: KdeSnapshot) -> None:
        active = [
            item
            for item in snapshot.notifications
            if notification_matches(item, self._query)
        ]
        recent = [
            item
            for item in snapshot.history
            if notification_matches(item, self._query)
        ]
        if self._query and not active and not recent:
            self._clear_cards()
            shown = self.search.text().strip()
            if len(shown) > 60:
                shown = shown[:57] + "…"
            self._set_empty(
                "No matches",
                f"No notifications match “{shown}”.",
                "bell",
                MUTED,
                False,
            )
            self.footer.setText(self._footer_text(snapshot))
            return

        scroll_value = self.scroll.verticalScrollBar().value()
        self._clear_cards()
        for item in active:
            card = NotificationCard(item, self.cards_host)
            card.dismissRequested.connect(self.dismissRequested)
            card.replyRequested.connect(self.replyRequested)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        if recent:
            self.cards_layout.insertWidget(
                self.cards_layout.count() - 1, self._recent_header()
            )
            for item in recent:
                card = NotificationCard(item, self.cards_host, recent=True)
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        self.stack.setCurrentWidget(self.scroll)
        self.footer.setText(self._footer_text(snapshot))
        self.scroll.verticalScrollBar().setValue(scroll_value)

    def focus_search(self) -> bool:
        if not self.search.isVisible():
            return False
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()
        return True

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().casefold()
        if query == self._query:
            return
        self._query = query
        if self._snapshot is not None and (
            self._snapshot.notifications or self._snapshot.history
        ):
            self._show_cards(self._snapshot)
            self.scroll.verticalScrollBar().setValue(0)

    def _recent_header(self) -> QWidget:
        row = QWidget(self.cards_host)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 10, 2, 0)
        layout.setSpacing(8)
        label = QLabel("RECENT", row)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        layout.addStretch(1)
        clear = QPushButton("Clear", row)
        clear.setObjectName("GhostButton")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setToolTip("Delete saved notification history from this computer")
        clear.clicked.connect(self.historyClearRequested)
        layout.addWidget(clear)
        return row

    def _set_empty(
        self, title: str, body: str, icon: str, tone: str, retry: bool = False
    ) -> None:
        self.empty.set_empty(title, body, icon=icon, tone=tone, retry=retry)
        self.stack.setCurrentWidget(self.empty)

    def _clear_cards(self) -> None:
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
