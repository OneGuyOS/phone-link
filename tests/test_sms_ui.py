"""Headless UI tests for the SMS messages panel."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from phone_link.sms import SmsMessage, build_conversations
from phone_link.ui.sms_panel import ConversationRow, SmsPanel


def message(
    *,
    thread: int = 9,
    uid: int = 1,
    date_ms: int = 1_762_500_000_000,
    kind: int = 1,
    read: int = 0,
    body: str = "Hello",
    addresses: tuple[str, ...] = ("+15551234567",),
) -> SmsMessage:
    return SmsMessage(
        event=1,
        body=body,
        addresses=addresses,
        date_ms=date_ms,
        type=kind,
        read=read,
        thread_id=thread,
        uid=uid,
        sub_id=1,
    )


class SmsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = SmsPanel(None)
        self.panel.show()
        self.addCleanup(self.panel.close)
        self.panel.set_phone("Galaxy", connected=True, available=True)

    def _rows(self) -> list[ConversationRow]:
        rows = []
        for index in range(self.panel.rows_layout.count()):
            widget = self.panel.rows_layout.itemAt(index).widget()
            if isinstance(widget, ConversationRow):
                rows.append(widget)
        return rows

    def _bubbles(self) -> list:
        bubbles = []
        for index in range(self.panel.bubbles_layout.count()):
            widget = self.panel.bubbles_layout.itemAt(index).widget()
            if widget is not None and widget.objectName() == "SmsBubble":
                bubbles.append(widget)
        return bubbles

    def test_empty_state_explains_when_phone_is_missing(self) -> None:
        panel = SmsPanel(None)
        self.addCleanup(panel.close)
        panel.set_phone("", connected=False, available=False)
        self.assertIn("KDE Connect", panel.empty.title.text())
        self.assertFalse(panel.new_button.isEnabled())

    def test_list_renders_rows_and_unread_badge(self) -> None:
        conversations = build_conversations(
            [
                message(thread=1, uid=1, date_ms=1000, read=0, body="first"),
                message(thread=2, uid=2, date_ms=2000, read=1, body="second"),
            ]
        )
        self.panel.set_conversations(conversations)
        self.assertEqual([row.thread_id for row in self._rows()], [2, 1])
        self.assertTrue(self.panel.unread_badge.isVisible())
        self.assertEqual(self.panel.unread_badge.text(), "1")

    def test_search_filters_by_address_and_body(self) -> None:
        conversations = build_conversations(
            [
                message(
                    thread=1,
                    uid=1,
                    date_ms=1000,
                    body="dinner at eight",
                    addresses=("+15550001111",),
                ),
                message(
                    thread=2,
                    uid=2,
                    date_ms=2000,
                    body="see you",
                    addresses=("+15552223333",),
                ),
            ]
        )
        self.panel.set_conversations(conversations)
        self.panel.search.setText("dinner")
        self.assertEqual([row.thread_id for row in self._rows()], [1])
        self.panel.search.setText("nothing-matches")
        self.assertIn("No matches", self.panel.empty.title.text())
        self.panel.search.clear()
        self.assertEqual(len(self._rows()), 2)

    def test_clicking_a_row_opens_the_thread(self) -> None:
        conversations = build_conversations([message(thread=7, uid=1)])
        self.panel.set_conversations(conversations)
        opened: list[int] = []
        self.panel.threadOpened.connect(opened.append)
        row = self._rows()[0]
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(4, 4),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(4, 4),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(row, press)
        QApplication.sendEvent(row, release)
        self.assertEqual(opened, [7])
        self.assertIs(self.panel.pages.currentWidget(), self.panel.thread_page)
        self.assertFalse(self.panel.search.isVisible())
        self.panel.show_list()
        self.assertTrue(self.panel.search.isVisible())

    def test_thread_renders_bubbles_and_day_separators(self) -> None:
        conversations = build_conversations([message(thread=7, uid=1)])
        self.panel.set_conversations(conversations)
        self.panel.open_thread(7)
        incoming = message(thread=7, uid=1, date_ms=1_700_000_000_000, kind=1)
        outgoing = message(thread=7, uid=2, date_ms=1_700_100_000_000, kind=2)
        self.panel.set_thread(7, (incoming, outgoing), False)
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 2)
        self.assertEqual(bubbles[0].property("outgoing"), False)
        self.assertEqual(bubbles[1].property("outgoing"), True)
        days = [
            self.panel.bubbles_layout.itemAt(index).widget()
            for index in range(self.panel.bubbles_layout.count())
        ]
        self.assertTrue(any(w is not None and w.objectName() == "SmsDay" for w in days))
        self.assertIn("2 messages", self.panel.thread_subtitle.text())

    def test_sending_emits_and_success_keeps_input_clear(self) -> None:
        self.panel.set_conversations(build_conversations([message(thread=7)]))
        self.panel.open_thread(7)
        self.panel.set_thread(7, (message(thread=7),), False)
        sent: list[tuple[int, str]] = []
        self.panel.replyRequested.connect(lambda tid, text: sent.append((tid, text)))
        self.panel.input.setPlainText("Hello back")
        self.panel._send()
        self.assertEqual(sent, [(7, "Hello back")])
        self.assertEqual(self.panel.input.toPlainText(), "")
        self.panel.send_succeeded(7)
        self.assertEqual(self.panel.input.toPlainText(), "")
        self.assertFalse(self.panel.error_label.isVisible())

    def test_failed_send_restores_the_text(self) -> None:
        self.panel.set_conversations(build_conversations([message(thread=7)]))
        self.panel.open_thread(7)
        self.panel.set_thread(7, (message(thread=7),), False)
        self.panel.input.setPlainText("Try again later")
        self.panel._send()
        self.panel.send_failed(7, "Phone not reachable")
        self.assertEqual(self.panel.input.toPlainText(), "Try again later")
        self.assertIn("Phone not reachable", self.panel.error_label.text())

    def test_composer_is_disabled_without_a_phone(self) -> None:
        self.panel.set_phone("", connected=False, available=False)
        self.panel.set_conversations(build_conversations([message(thread=7)]))
        self.panel.open_thread(7)
        self.assertFalse(self.panel.input.isEnabled())
        self.assertFalse(self.panel.send_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
