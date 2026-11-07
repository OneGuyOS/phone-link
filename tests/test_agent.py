"""Unit tests for the agent chat backends."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from phone_link.agent import (
    DEFAULT_SYSTEM_PROMPT,
    FILES_ENV,
    AgentStore,
    ChatMessage,
    Provider,
    adb_forward,
    agent_files_dir,
    chat_url,
    chunk_text,
    claude_argv,
    claude_result_text,
    claude_text_delta,
    claude_tool,
    codex_argv,
    codex_text,
    codex_tool,
    conversation_prompt,
    describe_http_error,
    load_store,
    models_url,
    parse_file_blocks,
    provider_key,
    save_file_blocks,
    save_store,
    sse_payloads,
)


class UrlTests(unittest.TestCase):
    def test_chat_url_appends_v1_and_path(self) -> None:
        self.assertEqual(
            chat_url("http://127.0.0.1:18089/v1"),
            "http://127.0.0.1:18089/v1/chat/completions",
        )
        self.assertEqual(
            chat_url("https://example.com/openai/v1/"),
            "https://example.com/openai/v1/chat/completions",
        )
        self.assertEqual(
            chat_url("http://localhost:11434"),
            "http://localhost:11434/v1/chat/completions",
        )
        self.assertEqual(
            chat_url("https://host/v1/chat/completions"),
            "https://host/v1/chat/completions",
        )

    def test_models_url(self) -> None:
        self.assertEqual(
            models_url("http://127.0.0.1:18089/v1"),
            "http://127.0.0.1:18089/v1/models",
        )
        self.assertEqual(
            models_url("https://example.com/v1/"),
            "https://example.com/v1/models",
        )


class StreamTests(unittest.TestCase):
    def test_sse_payloads_skips_comments_and_stops_at_done(self) -> None:
        lines = [
            "",
            ": keep-alive",
            'data: {"choices": []}',
            "event: message",
            "data: [DONE]",
            'data: {"never": true}',
        ]
        self.assertEqual(list(sse_payloads(lines)), [{"choices": []}])

    def test_sse_payloads_ignores_bad_json(self) -> None:
        self.assertEqual(list(sse_payloads(["data: {nope", "data: [1, 2]"])), [])

    def test_chunk_text_shapes(self) -> None:
        self.assertEqual(
            chunk_text({"choices": [{"delta": {"content": "hello"}}]}), "hello"
        )
        self.assertEqual(chunk_text({"choices": [{"text": "legacy"}]}), "legacy")
        self.assertEqual(
            chunk_text(
                {"choices": [{"delta": {"content": [{"text": "a"}, {"text": "b"}]}}]}
            ),
            "ab",
        )
        self.assertEqual(chunk_text({"choices": []}), "")
        self.assertEqual(chunk_text({}), "")

    def test_http_error_describe(self) -> None:
        body = io.BytesIO(
            json.dumps({"error": {"message": "bad key"}}).encode("utf-8")
        )
        error = urllib.error.HTTPError(
            "https://host/v1/chat/completions", 401, "Unauthorized", {}, body
        )
        message = describe_http_error(error)
        self.assertIn("HTTP 401", message)
        self.assertIn("bad key", message)
        self.assertIn(
            "Could not reach",
            describe_http_error(urllib.error.URLError("refused")),
        )


class PromptTests(unittest.TestCase):
    def test_conversation_prompt_includes_history(self) -> None:
        prompt = conversation_prompt(
            [ChatMessage("user", "hi"), ChatMessage("assistant", "hello")],
            "Be brief.",
        )
        self.assertIn("System: Be brief.", prompt)
        self.assertIn("User: hi", prompt)
        self.assertIn("Assistant: hello", prompt)
        self.assertTrue(prompt.endswith("Assistant:"))

    def test_conversation_prompt_can_omit_system(self) -> None:
        prompt = conversation_prompt(
            [ChatMessage("user", "hi")], "Be brief.", include_system=False
        )
        self.assertNotIn("System: Be brief.", prompt)

    def test_claude_argv(self) -> None:
        provider = Provider(
            id="claude-cli",
            label="Claude",
            kind="claude",
            model="claude-sonnet-5-5",
            system_prompt="Be concise.",
            permission_mode="acceptEdits",
        )
        args = claude_argv("/usr/bin/claude", provider, "User: hi")
        self.assertEqual(args[0], "/usr/bin/claude")
        self.assertIn("-p", args)
        self.assertIn("stream-json", args)
        self.assertIn("--include-partial-messages", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(args[args.index("--append-system-prompt") + 1], "Be concise.")
        self.assertEqual(args[args.index("-p") + 1], "User: hi")

    def test_codex_argv_uses_workspace_sandbox(self) -> None:
        provider = Provider(
            id="codex-cli",
            label="Codex",
            kind="codex",
            sandbox="read-only",
            working_dir="/tmp/space",
        )
        args = codex_argv("/usr/bin/codex", provider, "User: hi")
        self.assertEqual(args[:2], ["/usr/bin/codex", "exec"])
        self.assertIn("--json", args)
        self.assertEqual(args[args.index("--cd") + 1], "/tmp/space")
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertEqual(args[-1], "User: hi")


class ParseTests(unittest.TestCase):
    def test_claude_stream_events(self) -> None:
        delta = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hi"},
            },
        }
        self.assertEqual(claude_text_delta(delta), "hi")
        start = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            },
        }
        self.assertEqual(claude_tool(start), "Read")
        self.assertEqual(claude_text_delta({"type": "result"}), "")

    def test_claude_result_text(self) -> None:
        self.assertEqual(
            claude_result_text({"type": "result", "result": "done"}), "done"
        )
        self.assertEqual(
            claude_result_text({"type": "result", "result": {"text": "x"}}), "x"
        )
        self.assertEqual(claude_result_text({"type": "assistant"}), "")

    def test_codex_items(self) -> None:
        self.assertEqual(
            codex_text({"item": {"type": "agent_message", "text": "hey"}}), "hey"
        )
        self.assertEqual(
            codex_tool({"item": {"type": "command_execution", "command": ["ls", "-l"]}}),
            "$ ls -l",
        )
        self.assertEqual(codex_tool({"item": {"type": "reasoning"}}), "")


class StoreTests(unittest.TestCase):
    def test_provider_key_uses_the_stored_key(self) -> None:
        provider = Provider(id="custom-1", label="Custom", api_key=" stored ")
        self.assertEqual(provider_key(provider), "stored")
        provider.api_key = ""
        self.assertEqual(provider_key(provider), "")

    def test_store_navigation(self) -> None:
        store = AgentStore(
            providers=[
                Provider(id="a", label="A"),
                Provider(id="b", label="B"),
            ],
            active_id="b",
        )
        self.assertEqual(store.active().id, "b")
        self.assertEqual(store.by_id("a").label, "A")
        self.assertIsNone(store.by_id("missing"))
        store.remove("a")
        self.assertEqual([provider.id for provider in store.providers], ["b"])

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            store = load_store(path)
            self.assertEqual(
                [provider.id for provider in store.providers],
                ["zara-fast", "phone"],
            )
            custom = Provider(
                id="custom-1",
                label="Mine",
                base_url="http://127.0.0.1:9000/v1",
                model="tiny",
                api_key="secret",
            )
            store.providers.append(custom)
            store.active_id = custom.id
            save_store(store, path)

            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertFalse(path.with_name("agent.json.tmp").exists())

            loaded = load_store(path)
            self.assertEqual(loaded.active_id, "custom-1")
            mine = loaded.by_id("custom-1")
            self.assertIsNotNone(mine)
            self.assertEqual(mine.api_key, "secret")
            self.assertEqual(mine.base_url, "http://127.0.0.1:9000/v1")

    def test_overrides_apply_to_builtin_presets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            store = load_store(path)
            fast = store.by_id("zara-fast")
            fast.model = "zara-test"
            save_store(store, path)
            reloaded = load_store(path)
            self.assertEqual(reloaded.by_id("zara-fast").model, "zara-test")
            self.assertTrue(reloaded.by_id("zara-fast").builtin)

    def test_removed_builtins_do_not_come_back(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "active_id": "gemini",
                        "providers": [
                            {
                                "id": "gemini",
                                "label": "Gemini",
                                "builtin": True,
                                "api_key": "old",
                            },
                            {
                                "id": "zara-bonsai",
                                "label": "Bonsai",
                                "builtin": True,
                                "model": "zara-bonsai",
                            },
                            {
                                "id": "custom-1",
                                "label": "Mine",
                                "builtin": False,
                                "base_url": "http://127.0.0.1:9000/v1",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = load_store(path)
            self.assertEqual(
                [provider.id for provider in store.providers],
                ["zara-fast", "phone", "custom-1"],
            )
            self.assertEqual(store.active_id, "")
            self.assertEqual(store.active().id, "zara-fast")

    def test_phone_model_defaults_to_the_documented_alias(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": [
                            {
                                "id": "phone",
                                "label": "Phone server",
                                "builtin": True,
                                "base_url": "http://127.0.0.1:8080/v1",
                                "model": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = load_store(path)
            self.assertEqual(store.by_id("phone").model, "phone")

    def test_legacy_default_prompt_picks_up_the_file_convention(self) -> None:
        legacy = (
            "You are the assistant built into Phone Link, a KDE desktop "
            "companion for an Android phone. Be concise and practical. You do "
            "not automatically see the phone's state; ask for or use details "
            "the user provides."
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": [
                            {
                                "id": "zara-fast",
                                "label": "Zara Fast",
                                "system_prompt": legacy,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = load_store(path)
            self.assertEqual(store.by_id("zara-fast").system_prompt, DEFAULT_SYSTEM_PROMPT)

    def test_broken_config_falls_back_to_presets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            path.write_text("{not json", encoding="utf-8")
            store = load_store(path)
            self.assertEqual(
                [provider.id for provider in store.providers],
                ["zara-fast", "phone"],
            )
            self.assertTrue(store.by_id("zara-fast").builtin)


class SavedFileTests(unittest.TestCase):
    def test_parse_reads_file_blocks_only(self) -> None:
        text = (
            "Here you go.\n"
            "```file:notes.md\n"
            "# Notes\n"
            "body\n"
            "```\n"
            "And code:\n"
            "```python\nprint('no')\n```\n"
            "```file: todo.txt\nsecond\n```"
        )
        self.assertEqual(
            parse_file_blocks(text),
            [("notes.md", "# Notes\nbody\n"), ("todo.txt", "second\n")],
        )

    def test_parse_skips_names_that_could_escape_the_folder(self) -> None:
        for name in ("../secret", "/etc/passwd", "sub/dir.txt", ".hidden", ".."):
            text = f"```file:{name}\nonly content\n```"
            self.assertEqual(parse_file_blocks(text), [], name)

    def test_save_writes_text_and_reports_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "saved"
            outcomes = save_file_blocks(
                "```file:notes.md\nhello\n```", destination
            )
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0].name, "notes.md")
            self.assertEqual(outcomes[0].error, "")
            self.assertEqual(outcomes[0].path, destination / "notes.md")
            self.assertEqual(
                (destination / "notes.md").read_text(encoding="utf-8"), "hello\n"
            )

    def test_save_reports_a_bad_name_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            outcomes = save_file_blocks(
                "```file:../escape.txt\nnope\n```", Path(folder)
            )
            self.assertEqual(len(outcomes), 1)
            self.assertIsNone(outcomes[0].path)
            self.assertIn("plain filename", outcomes[0].error)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_save_refuses_an_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            text = "```file:big.txt\n" + "x" * (1_000_001) + "\n```"
            outcomes = save_file_blocks(text, Path(folder))
            self.assertEqual(len(outcomes), 1)
            self.assertIn("larger than", outcomes[0].error)
            self.assertFalse((Path(folder) / "big.txt").exists())

    def test_files_folder_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {FILES_ENV: folder}):
                self.assertEqual(agent_files_dir(), Path(folder))

    def test_default_files_folder_is_in_documents(self) -> None:
        with mock.patch.dict(os.environ, {FILES_ENV: ""}):
            self.assertEqual(
                agent_files_dir(), Path.home() / "Documents" / "Phone Link"
            )


class AdbForwardTests(unittest.TestCase):
    def test_forward_is_the_direction_that_reaches_the_phone(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=completed) as run:
            ok, error = adb_forward("/usr/bin/adb", 8080)
        self.assertTrue(ok)
        self.assertEqual(error, "")
        command = run.call_args.args[0]
        self.assertEqual(
            command, ["/usr/bin/adb", "forward", "tcp:8080", "tcp:8080"]
        )

    def test_failure_returns_adb_message(self) -> None:
        completed = SimpleNamespace(
            returncode=1, stdout="", stderr="error: no devices/emulators found"
        )
        with mock.patch("subprocess.run", return_value=completed):
            ok, error = adb_forward("/usr/bin/adb", 8080)
        self.assertFalse(ok)
        self.assertIn("no devices", error)


if __name__ == "__main__":
    unittest.main()
