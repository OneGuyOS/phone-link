"""Headless UI tests for the agent panel."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel

from phone_link.agent import FILES_ENV, AgentStore, ChatMessage, Provider
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
        self.chat_loader = mock.patch.object(
            agent_panel_module, "load_messages", return_value=[]
        )
        self.chat_loader.start()
        self.addCleanup(self.chat_loader.stop)
        self.chat_saver = mock.patch.object(agent_panel_module, "save_messages")
        self.saved = self.chat_saver.start()
        self.addCleanup(self.chat_saver.stop)
        self.store_saver = mock.patch.object(agent_panel_module, "save_store")
        self.saved_store = self.store_saver.start()
        self.addCleanup(self.store_saver.stop)
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
        self.saved.assert_called_with([])

    def test_sending_records_the_turn_and_earlier_chat(self) -> None:
        provider = Provider(
            id="fake",
            label="Fake local",
            kind="openai",
            base_url="http://127.0.0.1:9/v1",
            model="fake",
        )
        self.store.providers = [provider]
        self.store.active_id = "fake"
        self.panel._provider = provider
        self.panel._messages = [ChatMessage("assistant", "earlier")]
        self.panel.input.setPlainText("next")
        with mock.patch.object(agent_panel_module, "HttpChat") as http:
            self.panel.send()
        expected = [
            ChatMessage("assistant", "earlier"),
            ChatMessage("user", "next"),
        ]
        self.saved.assert_called_with(expected)
        self.assertEqual(http.return_value.start.call_args.args[1], expected)

    def test_reply_is_added_to_the_saved_transcript(self) -> None:
        self.panel._messages = [ChatMessage("user", "hi")]
        bubble = self.panel._add_bubble("assistant")
        self.panel._bubble = bubble
        self.panel._finish("hello there", None, False)
        self.saved.assert_called_with(
            [ChatMessage("user", "hi"), ChatMessage("assistant", "hello there")]
        )

    def _builtin_provider(self, *, builtin: bool = True) -> Provider:
        provider = Provider(
            id="fake",
            label="Fake local",
            kind="openai",
            base_url="http://127.0.0.1:9/v1",
            model="fake",
            system_prompt="Be brief.",
            builtin=builtin,
        )
        self.store.providers = [provider]
        self.store.active_id = "fake"
        self.panel._provider = provider
        return provider

    def test_phone_context_joins_the_system_prompt(self) -> None:
        provider = self._builtin_provider()
        self.panel.context_check.setChecked(True)
        self.panel.set_phone_context_source(
            lambda: "Phone state: battery 50%."
        )
        self.panel.input.setPlainText("what's my battery?")
        with mock.patch.object(agent_panel_module, "HttpChat") as http:
            self.panel.send()
        sent = http.return_value.start.call_args.args[0]
        self.assertEqual(
            sent.system_prompt, "Be brief.\n\nPhone state: battery 50%."
        )
        self.assertEqual(provider.system_prompt, "Be brief.")

    def test_phone_context_is_off_until_enabled(self) -> None:
        self._builtin_provider()
        source = mock.Mock(return_value="Phone state: battery 50%.")
        self.panel.set_phone_context_source(source)
        self.panel.input.setPlainText("hello")
        with mock.patch.object(agent_panel_module, "HttpChat") as http:
            self.panel.send()
        sent = http.return_value.start.call_args.args[0]
        self.assertEqual(sent.system_prompt, "Be brief.")
        source.assert_not_called()

    def test_custom_provider_never_receives_phone_context(self) -> None:
        self._builtin_provider(builtin=False)
        self.panel.context_check.setChecked(True)
        source = mock.Mock(return_value="Phone state: battery 50%.")
        self.panel.set_phone_context_source(source)
        self.panel.input.setPlainText("hello")
        with mock.patch.object(agent_panel_module, "HttpChat") as http:
            self.panel.send()
        sent = http.return_value.start.call_args.args[0]
        self.assertEqual(sent.system_prompt, "Be brief.")
        source.assert_not_called()

    def test_phone_context_toggle_is_saved(self) -> None:
        self.panel.context_check.setChecked(True)
        self.assertTrue(self.store.share_phone_state)
        self.saved_store.assert_called()
        self.panel.context_check.setChecked(False)
        self.assertFalse(self.store.share_phone_state)

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

    def _select_phone(self) -> Provider:
        provider = Provider(
            id="phone",
            label="Phone server",
            kind="openai",
            base_url="http://127.0.0.1:8080/v1",
            model="phone",
            local=True,
        )
        self.store.providers = [provider]
        self.store.active_id = "phone"
        self.panel._provider = provider
        return provider

    def test_phone_provider_starts_the_phone_server_before_sending(self) -> None:
        self._select_phone()
        self.panel.input.setPlainText("hello")
        with mock.patch.object(
            self.panel.phone_starter, "is_ready", return_value=False
        ):
            with mock.patch.object(
                self.panel.phone_starter, "ensure_started"
            ) as ensure:
                self.panel.send()
        ensure.assert_called_once()
        self.assertTrue(self.panel._pending_phone_send)
        self.assertEqual(self.panel.input.toPlainText(), "hello")
        self.assertFalse(self.panel.input.isEnabled())
        self.assertIn("phone model", self.panel.status.text())
        self.panel.stop()
        self.assertFalse(self.panel._pending_phone_send)
        self.assertTrue(self.panel.input.isEnabled())

    def test_phone_start_failure_shows_an_error(self) -> None:
        self._select_phone()
        self.panel.input.setPlainText("hello")
        with mock.patch.object(
            self.panel.phone_starter, "is_ready", return_value=False
        ):
            with mock.patch.object(self.panel.phone_starter, "ensure_started"):
                self.panel.send()
        self.panel._phone_failed("Termux is not installed.")
        self.assertFalse(self.panel._pending_phone_send)
        self.assertTrue(self.panel.input.isEnabled())
        self.assertEqual(self._bubble_roles(), ["error"])

    def test_phone_sees_the_conversation_the_laptop_had(self) -> None:
        self._select_phone()
        self.panel._messages = [
            ChatMessage("user", "asked on the laptop"),
            ChatMessage("assistant", "answered on the laptop"),
        ]
        self.panel.input.setPlainText("what did we say?")
        with mock.patch.object(
            self.panel.phone_starter, "is_ready", return_value=True
        ):
            with mock.patch.object(agent_panel_module, "HttpChat") as http:
                self.panel.send()
        sent = http.return_value.start.call_args.args[1]
        self.assertEqual(
            [message.text for message in sent],
            [
                "asked on the laptop",
                "answered on the laptop",
                "what did we say?",
            ],
        )


    def test_reply_file_blocks_are_saved_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {FILES_ENV: folder}):
                bubble = self.panel._add_bubble("assistant")
                self.panel._bubble = bubble
                self.panel._finish("```file:note.md\nhi\n```", None, False)
            self.assertEqual(
                (Path(folder) / "note.md").read_text(encoding="utf-8"), "hi\n"
            )
        tools = bubble.findChildren(QLabel, "AgentTool")
        self.assertTrue(any("Saved note.md" in label.text() for label in tools))

    def test_unsafe_file_names_are_reported_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {FILES_ENV: folder}):
                bubble = self.panel._add_bubble("assistant")
                self.panel._bubble = bubble
                self.panel._finish("```file:../escape.md\nhi\n```", None, False)
            self.assertEqual(list(Path(folder).iterdir()), [])
        tools = bubble.findChildren(QLabel, "AgentTool")
        self.assertTrue(any("Could not save" in label.text() for label in tools))


class RestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_saved_messages_are_shown_at_startup(self) -> None:
        store = AgentStore(
            providers=[
                Provider(
                    id="fake",
                    label="Fake local",
                    kind="openai",
                    base_url="http://127.0.0.1:9/v1",
                    model="",
                )
            ],
            active_id="fake",
        )
        saved = [
            ChatMessage("user", "earlier question"),
            ChatMessage("assistant", "earlier answer"),
        ]
        with mock.patch.object(
            agent_panel_module, "load_store", return_value=store
        ):
            with mock.patch.object(
                agent_panel_module, "load_messages", return_value=list(saved)
            ):
                with mock.patch.object(agent_panel_module, "save_messages"):
                    panel = AgentPanel(None)
                    panel.show()
                    self.addCleanup(panel.close)
                    roles = []
                    texts = []
                    for index in range(
                        panel._messages_layout.count()
                    ):
                        widget = panel._messages_layout.itemAt(index).widget()
                        if (
                            widget is not None
                            and widget.objectName() == "AgentBubble"
                        ):
                            roles.append(widget.property("role"))
                            texts.append(widget.text_label.text())
        self.assertEqual(panel._messages, saved)
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(texts, ["earlier question", "earlier answer"])


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
