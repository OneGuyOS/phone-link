"""Headless UI tests for the agent panel."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from phone_link.agent import AgentStore, Provider
from phone_link.ui import agent_panel as agent_panel_module
from phone_link.ui.agent_panel import AgentPanel, ChatInput


class AgentPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.store = AgentStore(
            providers=[
                Provider(
                    id="fake",
                    label="Fake local",
                    kind="openai",
                    base_url="http://127.0.0.1:9/v1",
                    model="",
                    local=True,
                )
            ],
            active_id="fake",
        )
        self.loader = mock.patch.object(
            agent_panel_module, "load_store", return_value=self.store
        )
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.panel = AgentPanel(None)
        self.panel.show()
        self.addCleanup(self.panel.close)

    def _bubble_roles(self) -> list[str]:
        roles = []
        for index in range(self.panel._messages_layout.count()):
            widget = self.panel._messages_layout.itemAt(index).widget()
            if widget is not None and widget.objectName() == "AgentBubble":
                roles.append(widget.property("role"))
        return roles

    def test_provider_combo_lists_the_active_provider(self) -> None:
        self.assertEqual(self.panel.provider_combo.currentData(), "fake")
        self.assertEqual(self.panel._provider.label, "Fake local")
        self.assertIn("Fake local", self.panel.status.text())

    def test_empty_message_is_ignored(self) -> None:
        self.panel.input.setPlainText("   ")
        self.panel.send()
        self.assertEqual(self.panel._messages, [])
        self.assertEqual(self._bubble_roles(), [])

    def test_provider_without_model_shows_an_error(self) -> None:
        self.panel.input.setPlainText("hello")
        self.panel.send()
        self.assertEqual(self.panel._messages, [])
        self.assertEqual(self._bubble_roles(), ["error"])
        self.assertIsNone(self.panel._chat)

    def test_new_chat_clears_the_transcript(self) -> None:
        self.panel.input.setPlainText("hello")
        self.panel.send()
        self.assertEqual(self._bubble_roles(), ["error"])
        self.panel.new_chat()
        self.assertEqual(self._bubble_roles(), [])
        self.assertEqual(self.panel.input.toPlainText(), "")

    def test_zara_fast_starts_the_managed_server_before_sending(self) -> None:
        provider = Provider(
            id="zara-fast",
            label="Zara Fast",
            kind="openai",
            base_url="http://127.0.0.1:9/v1",
            model="zara-fast",
            local=True,
        )
        self.store.providers = [provider]
        self.store.active_id = "zara-fast"
        self.panel._provider = provider
        self.panel.input.setPlainText("hello")
        with mock.patch.object(
            self.panel.local_server, "is_ready", return_value=False
        ):
            with mock.patch.object(
                self.panel.local_server, "ensure_started"
            ) as ensure:
                self.panel.send()
        ensure.assert_called_once()
        self.assertTrue(self.panel._pending_local_send)
        self.assertEqual(self.panel.input.toPlainText(), "hello")
        self.assertFalse(self.panel.input.isEnabled())
        self.assertEqual(self._bubble_roles(), [])
        self.panel.stop()
        self.assertFalse(self.panel._pending_local_send)
        self.assertTrue(self.panel.input.isEnabled())


class ChatInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_enter_sends_and_shift_enter_does_not(self) -> None:
        box = ChatInput()
        box.show()
        self.addCleanup(box.close)
        sent: list[int] = []
        box.sendRequested.connect(lambda: sent.append(1))

        enter = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(box, enter)
        self.assertEqual(len(sent), 1)

        shift_enter = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
        )
        QApplication.sendEvent(box, shift_enter)
        self.assertEqual(len(sent), 1)
        self.assertIn("\n", box.toPlainText())


if __name__ == "__main__":
    unittest.main()
