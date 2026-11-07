"""Unit tests for the saved agent transcript."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phone_link.agent import ChatMessage
from phone_link.chat_store import (
    MAX_CONTEXT_CHARS,
    MAX_STORED_MESSAGES,
    chat_path,
    context_messages,
    load_messages,
    save_messages,
)


class StoreTests(unittest.TestCase):
    def test_round_trip_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            messages = [
                ChatMessage("user", "hello"),
                ChatMessage("assistant", "hi there"),
            ]
            save_messages(messages, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(path.with_name("chat.json.tmp").exists())
            loaded = load_messages(path)
            self.assertEqual(
                [(message.role, message.text) for message in loaded],
                [("user", "hello"), ("assistant", "hi there")],
            )

    def test_missing_or_corrupt_file_is_an_empty_chat(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            self.assertEqual(load_messages(path), [])
            path.write_text("{nope", encoding="utf-8")
            self.assertEqual(load_messages(path), [])

    def test_invalid_records_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "messages": [
                            {"role": "user", "text": "keep"},
                            {"role": "system", "text": "drop"},
                            {"role": "assistant", "text": ""},
                            "nonsense",
                            {"role": "assistant", "text": "also keep"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [(message.role, message.text) for message in load_messages(path)],
                [("user", "keep"), ("assistant", "also keep")],
            )

    def test_only_the_recent_messages_are_stored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            messages = [
                ChatMessage("user" if index % 2 == 0 else "assistant", f"m{index}")
                for index in range(MAX_STORED_MESSAGES + 5)
            ]
            save_messages(messages, path)
            loaded = load_messages(path)
            self.assertEqual(len(loaded), MAX_STORED_MESSAGES)
            self.assertEqual(loaded[-1].text, f"m{MAX_STORED_MESSAGES + 4}")

    def test_empty_chat_clears_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            save_messages([ChatMessage("user", "hello")], path)
            save_messages([], path)
            self.assertEqual(load_messages(path), [])

    def test_default_path_sits_next_to_the_agent_config(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": folder}):
                self.assertEqual(
                    chat_path(), Path(folder) / "phone-link" / "chat.json"
                )


class ContextTests(unittest.TestCase):
    def test_recent_messages_win_when_the_budget_is_tight(self) -> None:
        messages = [
            ChatMessage("user", "a" * 100),
            ChatMessage("assistant", "b" * 100),
            ChatMessage("user", "latest"),
        ]
        trimmed = context_messages(messages, max_chars=50)
        self.assertEqual([m.text for m in trimmed], ["latest"])

    def test_short_conversation_is_sent_whole(self) -> None:
        messages = [
            ChatMessage("user", "hi"),
            ChatMessage("assistant", "hello"),
        ]
        self.assertEqual(context_messages(messages), messages)

    def test_the_last_message_is_always_kept(self) -> None:
        huge = "x" * (MAX_CONTEXT_CHARS + 100)
        messages = [ChatMessage("assistant", "old"), ChatMessage("user", huge)]
        self.assertEqual([m.role for m in context_messages(messages)], ["user"])

    def test_order_is_preserved(self) -> None:
        messages = [
            ChatMessage("user", "one"),
            ChatMessage("assistant", "two"),
            ChatMessage("user", "three"),
        ]
        self.assertEqual(
            [m.text for m in context_messages(messages, max_chars=45)],
            ["two", "three"],
        )


if __name__ == "__main__":
    unittest.main()
