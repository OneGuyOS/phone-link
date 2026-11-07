"""The Messages tab: SMS conversations from KDE Connect's SMS plugin."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..sms import SmsConversation, SmsMessage
from .agent_panel import ChatInput
from .icons import glyph_icon, glyph_pixmap
from .notifications import short_time
from .theme import ACCENT, MUTED


def _day_label(when: datetime) -> str:
    today = datetime.now().date()
    day = when.date()
    if day == today:
        return "Today"
    delta = (today - day).days
    if delta == 1:
        return "Yesterday"
    if day.year == today.year:
        return day.strftime("%d %b")
    return day.strftime("%d %b %Y")


def _clamp(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed


class ConversationRow(QFrame):
    """One conversation in the inbox list."""

    clicked = Signal(int)

    def __init__(
        self, conversation: SmsConversation, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.thread_id = conversation.thread_id
        self.setObjectName("SmsConversation")
        self.setProperty("unread", conversation.unread_count > 0)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 10)
        layout.setSpacing(10)

        avatar = QLabel(self)
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(
            glyph_pixmap("phone", ACCENT if conversation.unread_count else MUTED, 18)
        )
        avatar.setStyleSheet("background: #131a22; border-radius: 17px;")
        layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 1, 0, 0)
        body.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(7)
        address = QLabel(conversation.address, self)
        address.setObjectName("SmsAddress")
        address.setTextFormat(Qt.TextFormat.PlainText)
        address.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        top.addWidget(address, 1)
        latest = conversation.latest
        stamp = QLabel(short_time(latest.date) if latest is not None else "", self)
        stamp.setObjectName("CardTime")
        top.addWidget(stamp)
        body.addLayout(top)

        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(7)
        preview_text = conversation.preview
        if latest is not None and latest.is_outgoing and not latest.is_failed:
            preview_text = f"You: {preview_text}"
        preview = QLabel(_clamp(preview_text), self)
        preview.setObjectName("SmsPreview")
        preview.setTextFormat(Qt.TextFormat.PlainText)
        preview.setProperty(
            "failed", bool(latest is not None and latest.is_failed)
        )
        preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        preview_row.addWidget(preview, 1)
        if conversation.unread_count:
            badge = QLabel(str(conversation.unread_count), self)
            badge.setObjectName("SmsUnread")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedHeight(18)
            badge.setMinimumWidth(18)
            preview_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(preview_row)

        layout.addLayout(body, 1)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit(self.thread_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.thread_id)
            event.accept()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    """One SMS message, shown full width like the agent transcript."""

    def __init__(self, message: SmsMessage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outgoing = message.is_outgoing or message.is_draft
        self.setObjectName("SmsBubble")
        self.setProperty("outgoing", outgoing)
        self.setProperty("failed", message.is_failed)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(4)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(7)
        if message.is_failed:
            caption = "Not sent"
        elif message.is_draft:
            caption = "Draft"
        elif outgoing:
            caption = "You"
        else:
            caption = message.address
        who = QLabel(caption, self)
        who.setObjectName("SmsBubbleWho")
        who.setTextFormat(Qt.TextFormat.PlainText)
        who.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        meta.addWidget(who, 1)
        stamp = QLabel(message.date.strftime("%H:%M"), self)
        stamp.setObjectName("SmsBubbleTime")
        meta.addWidget(stamp)
        layout.addLayout(meta)

        if message.attachments:
            kinds: list[str] = []
            for attachment in message.attachments:
                if attachment.label not in kinds:
                    kinds.append(attachment.label)
            media = QLabel(" · ".join(kinds), self)
            media.setObjectName("SmsAttachment")
            media.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(media)

        text = QLabel(message.body, self)
        text.setObjectName("SmsBubbleText")
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        text.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        text.setVisible(bool(message.body))
        layout.addWidget(text)


class EmptySms(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
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
        self.retry.hide()
        layout.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_empty(
        self,
        title: str,
        body: str,
        *,
        icon: str = "message",
        tone: str = MUTED,
        retry: bool = False,
    ) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.art.setPixmap(glyph_pixmap(icon, tone, 54))
        self.retry.setVisible(retry)


class SmsPanel(QFrame):
    """Inbox and thread view for the phone's SMS conversations."""

    threadOpened = Signal(int)
    loadEarlierRequested = Signal(int)
    replyRequested = Signal(int, str)
    newMessageRequested = Signal()
    refreshRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._conversations: tuple[SmsConversation, ...] = ()
        self._thread_id: int | None = None
        self._thread_messages: tuple[SmsMessage, ...] = ()
        self._has_more = False
        self._loading = False
        self._ready = False
        self._device_name = ""
        self._status_message = ""
        self._query = ""
        self._pending_text = ""
        self._pending_thread: int | None = None
        self._sending = False
        self._loading_earlier = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(9)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        icon = QLabel(self)
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(glyph_pixmap("message", ACCENT, 23))
        icon.setStyleSheet("background: #17384c; border-radius: 9px;")
        header.addWidget(icon)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Messages", self)
        title.setObjectName("PanelTitle")
        title_row.addWidget(title)
        self.unread_badge = QLabel("0", self)
        self.unread_badge.setObjectName("Badge")
        self.unread_badge.hide()
        title_row.addWidget(self.unread_badge)
        title_row.addStretch(1)
        titles.addLayout(title_row)
        self.subtitle = QLabel("KDE Connect", self)
        self.subtitle.setObjectName("PanelSubtitle")
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)

        self.new_button = QPushButton("New", self)
        self.new_button.setObjectName("AgentIconButton")
        self.new_button.setToolTip("Send a text message")
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_button.clicked.connect(self.newMessageRequested)
        header.addWidget(self.new_button)
        self.refresh_button = QPushButton(self)
        self.refresh_button.setObjectName("AgentIconButton")
        self.refresh_button.setIcon(glyph_icon("refresh", MUTED, 14))
        self.refresh_button.setIconSize(QSize(14, 14))
        self.refresh_button.setFixedWidth(30)
        self.refresh_button.setToolTip("Reload conversations from the phone")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.refreshRequested)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        separator = QFrame(self)
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(separator)

        self.search = QLineEdit(self)
        self.search.setObjectName("NotificationSearch")
        self.search.setPlaceholderText("Search conversations")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search conversations")
        self.search.textChanged.connect(self._on_search_changed)
        clear_search = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search)
        clear_search.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear_search.activated.connect(self.search.clear)
        self.search.hide()
        root.addWidget(self.search)

        self.pages = QStackedWidget(self)
        self.list_page = self._build_list_page()
        self.thread_page = self._build_thread_page()
        self.pages.addWidget(self.list_page)
        self.pages.addWidget(self.thread_page)
        root.addWidget(self.pages, 1)

        self._show_empty(
            "Messages need KDE Connect",
            "Start KDE Connect and pair your phone to read its texts here.",
            icon="message",
            tone=MUTED,
        )

    # -- construction ------------------------------------------------------

    def _build_list_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.list_host = QWidget(page)
        self.list_stack = QStackedLayout(self.list_host)
        self.list_stack.setContentsMargins(0, 0, 0, 0)

        self.list_scroll = QScrollArea(self.list_host)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.rows_host = QWidget(self.list_scroll)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 2, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch(1)
        self.list_scroll.setWidget(self.rows_host)
        self.list_stack.addWidget(self.list_scroll)

        self.empty = EmptySms(self.list_host)
        self.empty.retry.clicked.connect(self.refreshRequested)
        self.list_stack.addWidget(self.empty)
        layout.addWidget(self.list_host, 1)
        return page

    def _build_thread_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(7)
        self.back_button = QPushButton(self)
        self.back_button.setObjectName("AgentIconButton")
        self.back_button.setIcon(glyph_icon("back", MUTED, 14))
        self.back_button.setIconSize(QSize(14, 14))
        self.back_button.setFixedWidth(30)
        self.back_button.setToolTip("Back to all conversations")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.show_list)
        bar.addWidget(self.back_button)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.thread_title = QLabel("", self)
        self.thread_title.setObjectName("SmsThreadTitle")
        self.thread_title.setTextFormat(Qt.TextFormat.PlainText)
        self.thread_title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        titles.addWidget(self.thread_title)
        self.thread_subtitle = QLabel("", self)
        self.thread_subtitle.setObjectName("PanelSubtitle")
        self.thread_subtitle.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.thread_subtitle)
        bar.addLayout(titles, 1)

        self.thread_refresh = QPushButton(self)
        self.thread_refresh.setObjectName("AgentIconButton")
        self.thread_refresh.setIcon(glyph_icon("refresh", MUTED, 14))
        self.thread_refresh.setIconSize(QSize(14, 14))
        self.thread_refresh.setFixedWidth(30)
        self.thread_refresh.setToolTip("Reload this conversation")
        self.thread_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thread_refresh.clicked.connect(self._reload_thread)
        bar.addWidget(self.thread_refresh)
        layout.addLayout(bar)

        self.thread_scroll = QScrollArea(self)
        self.thread_scroll.setObjectName("AgentScroll")
        self.thread_scroll.setWidgetResizable(True)
        self.thread_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thread_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.thread_host = QWidget(self.thread_scroll)
        self.thread_host.setObjectName("AgentSurface")
        self.bubbles_layout = QVBoxLayout(self.thread_host)
        self.bubbles_layout.setContentsMargins(2, 2, 6, 4)
        self.bubbles_layout.setSpacing(7)
        self.bubbles_layout.addStretch(1)
        self.thread_scroll.setWidget(self.thread_host)
        self.thread_scroll.verticalScrollBar().valueChanged.connect(
            self._maybe_load_earlier
        )
        layout.addWidget(self.thread_scroll, 1)

        self.thread_notice = QLabel("", self)
        self.thread_notice.setObjectName("Muted")
        self.thread_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.thread_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thread_notice)

        composer = QHBoxLayout()
        composer.setContentsMargins(0, 0, 0, 0)
        composer.setSpacing(7)
        self.input = ChatInput(self)
        self.input.setObjectName("SmsComposer")
        self.input.setPlaceholderText("Text message…  Enter to send")
        self.input.setFixedHeight(64)
        self.input.sendRequested.connect(self._send)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton("Send", self)
        self.send_button.setObjectName("AgentSend")
        self.send_button.setFixedHeight(34)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._send)
        composer.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(composer)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("SmsError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        return page

    # -- phone state -------------------------------------------------------

    def set_phone(
        self,
        name: str,
        *,
        connected: bool,
        available: bool,
        message: str = "",
    ) -> None:
        self._device_name = str(name or "")
        self._ready = bool(connected and available)
        self._status_message = str(message or "")
        if self._ready:
            target = self._device_name or "the phone"
            self.subtitle.setText(f"SMS · {target}")
            self.input.setPlaceholderText("Text message…  Enter to send")
        elif connected and not available:
            self.subtitle.setText("KDE Connect SMS is off")
            self.input.setPlaceholderText("Messaging is unavailable for this phone")
        else:
            self.subtitle.setText("KDE Connect")
            self.input.setPlaceholderText("Connect a phone to send texts")
        self.new_button.setEnabled(self._ready)
        self.refresh_button.setEnabled(self._ready)
        self.set_composer_enabled(self._ready)
        self._refresh_list()

    def set_composer_enabled(self, enabled: bool) -> None:
        self.input.setEnabled(enabled and self._thread_id is not None)
        self.send_button.setEnabled(enabled and self._thread_id is not None)

    # -- inbox -------------------------------------------------------------

    def set_conversations(self, conversations) -> None:
        self._conversations = tuple(conversations)
        unread = sum(
            1 for conversation in self._conversations if conversation.unread_count
        )
        self.unread_badge.setText("99+" if unread > 99 else str(unread))
        self.unread_badge.setVisible(unread > 0)
        self._update_search_visibility()
        if not self._conversations and self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._query = ""
        self._refresh_list()
        if self._thread_id is not None:
            self._refresh_thread_header()

    def _visible_conversations(self) -> tuple[SmsConversation, ...]:
        if not self._query:
            return self._conversations
        needle = self._query
        matches = []
        for conversation in self._conversations:
            haystack = conversation.address
            if conversation.latest is not None:
                haystack += "\n" + conversation.latest.body
            if needle in haystack.casefold():
                matches.append(conversation)
        return tuple(matches)

    def _refresh_list(self) -> None:
        if not self._conversations:
            if self._ready:
                self._show_empty(
                    "No messages yet",
                    "Texts on your phone appear here.",
                    icon="message",
                    tone=MUTED,
                )
            elif self._device_name:
                self._show_empty(
                    "Messaging is unavailable",
                    self._status_message
                    or "Enable KDE Connect's SMS plugin on the phone and in KDE Connect.",
                    icon="message",
                    tone=MUTED,
                )
            else:
                self._show_empty(
                    "Messages need KDE Connect",
                    self._status_message
                    or "Start KDE Connect and pair your phone to read its texts here.",
                    icon="message",
                    tone=MUTED,
                )
            return

        visible = self._visible_conversations()
        if not visible:
            shown = self.search.text().strip()
            if len(shown) > 60:
                shown = shown[:57] + "…"
            self._show_empty(
                "No matches",
                f"No conversations match “{shown}”.",
                icon="message",
                tone=MUTED,
            )
            return

        value = self.list_scroll.verticalScrollBar().value()
        self._clear_rows()
        for conversation in visible:
            row = ConversationRow(conversation, self.rows_host)
            row.clicked.connect(self.open_thread)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.list_stack.setCurrentWidget(self.list_scroll)
        self.list_scroll.verticalScrollBar().setValue(value)

    def _clear_rows(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_empty(
        self,
        title: str,
        body: str,
        *,
        icon: str = "message",
        tone: str = MUTED,
        retry: bool = False,
    ) -> None:
        self.empty.set_empty(title, body, icon=icon, tone=tone, retry=retry)
        self.list_stack.setCurrentWidget(self.empty)

    def _update_search_visibility(self) -> None:
        on_list = self.pages.currentWidget() is self.list_page
        self.search.setVisible(on_list and bool(self._conversations))

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().casefold()
        if query == self._query:
            return
        self._query = query
        self._refresh_list()

    # -- thread ------------------------------------------------------------

    def current_thread(self) -> int | None:
        return self._thread_id

    def open_thread(self, thread_id: int) -> None:
        self._thread_id = int(thread_id)
        conversation = next(
            (
                item
                for item in self._conversations
                if item.thread_id == self._thread_id
            ),
            None,
        )
        self.thread_title.setText(
            conversation.address if conversation is not None else "Conversation"
        )
        self._thread_messages = ()
        self._has_more = False
        self._loading = True
        self.error_label.hide()
        self._render_thread()
        self.pages.setCurrentWidget(self.thread_page)
        self._update_search_visibility()
        self.set_composer_enabled(self._ready)
        self.threadOpened.emit(self._thread_id)

    def show_list(self) -> None:
        self._thread_id = None
        self._thread_messages = ()
        self._loading = False
        self.error_label.hide()
        self.pages.setCurrentWidget(self.list_page)
        self._update_search_visibility()
        self.set_composer_enabled(False)

    def set_thread(self, thread_id: int, messages, has_more: bool) -> None:
        if self._thread_id != int(thread_id):
            return
        messages = tuple(messages)
        unchanged = (
            messages == self._thread_messages and bool(has_more) == self._has_more
        )
        was_loading = self._loading
        self._thread_messages = messages
        self._has_more = bool(has_more)
        self._loading = False
        if self._thread_messages and self.thread_title.text() in ("", "Conversation"):
            self.thread_title.setText(self._thread_messages[0].address)
        if unchanged:
            self._refresh_thread_header()
            self._update_thread_notice()
            if was_loading:
                self._loading_earlier = False
            return
        self._render_thread(preserve=self._loading_earlier)
        self._loading_earlier = False
        self._refresh_thread_header()

    def set_thread_loading(self, thread_id: int, loading: bool) -> None:
        if self._thread_id != int(thread_id):
            return
        self._loading = loading
        if loading and not self._thread_messages:
            self._render_thread()
        else:
            self._update_thread_notice()

    def _reload_thread(self) -> None:
        if self._thread_id is None:
            return
        self._loading_earlier = False
        self.threadOpened.emit(self._thread_id)

    def _refresh_thread_header(self) -> None:
        count = len(self._thread_messages)
        noun = "message" if count == 1 else "messages"
        target = self._device_name or "the phone"
        self.thread_subtitle.setText(f"{count} {noun} · SMS via {target}")

    def _render_thread(self, *, preserve: bool = False) -> None:
        bar = self.thread_scroll.verticalScrollBar()
        distance = bar.maximum() - bar.value()
        self._clear_bubbles()
        if not self._thread_messages and not self._loading:
            notice = QLabel("No messages in this conversation yet.", self.thread_host)
            notice.setObjectName("Dim")
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.bubbles_layout.insertWidget(
                self.bubbles_layout.count() - 1, notice
            )
        last_day = None
        for message in sorted(
            self._thread_messages, key=lambda item: item.sort_key
        ):
            day = message.date.date()
            if day != last_day:
                last_day = day
                label = QLabel(_day_label(message.date), self.thread_host)
                label.setObjectName("SmsDay")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.bubbles_layout.insertWidget(
                    self.bubbles_layout.count() - 1, label
                )
            bubble = MessageBubble(message, self.thread_host)
            self.bubbles_layout.insertWidget(
                self.bubbles_layout.count() - 1, bubble
            )
        self._refresh_thread_header()
        self._update_thread_notice()
        if preserve:
            QTimer.singleShot(
                0, lambda: bar.setValue(max(0, bar.maximum() - distance))
            )
        else:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _update_thread_notice(self) -> None:
        if self._loading:
            self.thread_notice.setText("Loading conversation…")
            self.thread_notice.show()
        elif self._has_more:
            self.thread_notice.setText("Scroll up to load older messages")
            self.thread_notice.show()
        else:
            self.thread_notice.hide()

    def _maybe_load_earlier(self) -> None:
        if (
            self._thread_id is None
            or not self._has_more
            or self._loading
            or self._loading_earlier
            or not self._thread_messages
        ):
            return
        bar = self.thread_scroll.verticalScrollBar()
        if bar.value() <= 0:
            self._loading_earlier = True
            self.loadEarlierRequested.emit(self._thread_id)

    def _clear_bubbles(self) -> None:
        while self.bubbles_layout.count() > 1:
            item = self.bubbles_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # -- sending -----------------------------------------------------------

    def _send(self) -> None:
        if self._thread_id is None or not self._ready or self._sending:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self._sending = True
        self._pending_text = text
        self._pending_thread = self._thread_id
        self.input.clear()
        self.error_label.hide()
        self.send_button.setEnabled(False)
        self.replyRequested.emit(self._thread_id, text)

    def send_succeeded(self, thread_id: int) -> None:
        if thread_id == self._pending_thread:
            self._pending_text = ""
            self._pending_thread = None
        self._sending = False
        self.send_button.setEnabled(self._ready and self._thread_id is not None)
        self.error_label.hide()

    def send_failed(self, thread_id: int, message: str) -> None:
        self._sending = False
        if thread_id == self._pending_thread and self._pending_text:
            if self._thread_id == thread_id and not self.input.toPlainText():
                self.input.setPlainText(self._pending_text)
        self._pending_text = ""
        self._pending_thread = None
        self.send_button.setEnabled(self._ready and self._thread_id is not None)
        self.set_error(message)

    def set_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))
