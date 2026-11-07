"""Persist the agent conversation across restarts and providers.

Phone Link has one chat transcript.  Both built-in providers answer from it,
so switching between Zara Fast and the phone server keeps the same context,
and this module keeps it after the window is closed.  The file lives next to
``agent.json`` in the per-user data directory with owner-only permissions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .agent import ChatMessage, default_config_path

CHAT_FILE = "chat.json"
MAX_STORED_MESSAGES = 200
# A rough token budget for what is sent back: about four characters per token
# for English, leaving room under the 4096-token context for the prompt and
# the reply.
MAX_CONTEXT_CHARS = 12_000


def chat_path() -> Path:
    return default_config_path().with_name(CHAT_FILE)


def load_messages(path: Path | None = None) -> list[ChatMessage]:
    """The saved transcript, oldest first; empty when there is none."""

    config = path or chat_path()
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("messages")
    if not isinstance(items, list):
        return []
    messages: list[ChatMessage] = []
    for item in items[-MAX_STORED_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        text = item.get("text")
        if role in ("user", "assistant") and isinstance(text, str) and text:
            messages.append(ChatMessage(role, text))
    return messages


def save_messages(
    messages: list[ChatMessage], path: Path | None = None
) -> Path:
    """Write the transcript atomically with owner-only permissions."""

    config = path or chat_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "messages": [
            {"role": message.role, "text": message.text}
            for message in messages[-MAX_STORED_MESSAGES:]
        ],
    }
    temporary = config.with_name(config.name + ".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, config)
    try:
        os.chmod(config, 0o600)
    except OSError:
        pass
    return config


def context_messages(
    messages: list[ChatMessage], max_chars: int = MAX_CONTEXT_CHARS
) -> list[ChatMessage]:
    """The most recent messages that fit the context budget.

    Trimming from the front keeps the conversation usable on the 4096-token
    local context without dropping the message being answered.
    """

    picked: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = len(message.text) + 16
        if picked and used + cost > max_chars:
            break
        picked.append(message)
        used += cost
    picked.reverse()
    return picked
