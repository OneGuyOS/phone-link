"""Echo suppression for optional automatic clipboard sync.

KDE Connect exposes no "clipboard received" signal, so the system clipboard
also changes when the phone sends text to the desktop.  Remembering digests of
what we recently pushed (and never storing the text itself) stops the phone's
auto-share from bouncing the same text back and forth.
"""

from __future__ import annotations

import hashlib


def _digest(text: str) -> bytes:
    return hashlib.sha1(text.encode("utf-8", "replace")).digest()


class ClipboardSyncGate:
    """Decide whether a desktop clipboard change is worth sending."""

    def __init__(self, *, max_length: int = 100_000, echo_window: float = 8.0) -> None:
        self.max_length = max_length
        self.echo_window = echo_window
        self._recent: list[tuple[bytes, float]] = []

    def should_send(self, text: str, now: float) -> bool:
        if not text or len(text) > self.max_length:
            return False
        self._purge(now)
        digest = _digest(text)
        return all(digest != sent for sent, _ in self._recent)

    def note_sent(self, text: str, now: float) -> None:
        self._purge(now)
        self._recent.append((_digest(text), now))
        if len(self._recent) > 20:
            del self._recent[: len(self._recent) - 20]

    def _purge(self, now: float) -> None:
        cutoff = now - self.echo_window
        self._recent = [(digest, at) for digest, at in self._recent if at >= cutoff]
