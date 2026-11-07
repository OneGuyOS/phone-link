"""Local storage for the recent-notification list.

History is deliberately small and private: at most 30 entries, written to
``$XDG_DATA_HOME/phone-link/history.json`` (``~/.local/share`` by default) with
mode 0600, and never sent anywhere.  Clearing the Recent list deletes the file.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import NotificationItem

HISTORY_VERSION = 1
DEFAULT_LIMIT = 30
MAX_FIELD_CHARS = 4000


def history_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "phone-link" / "history.json"


def load_history(
    *,
    limit: int = DEFAULT_LIMIT,
    path: str | os.PathLike[str] | None = None,
) -> list[NotificationItem]:
    """Read saved history, newest first.  A missing or bad file means empty."""

    if limit <= 0:
        return []
    target = Path(path) if path is not None else history_path()
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []

    records = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []

    loaded: dict[str, NotificationItem] = {}
    for record in records:
        item = _item_from_record(record)
        if item is not None:
            loaded[item.id] = item
    return sorted(loaded.values(), key=lambda item: item.received_at, reverse=True)[:limit]


def save_history(
    items: Iterable[NotificationItem],
    *,
    limit: int = DEFAULT_LIMIT,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Write the newest ``limit`` items atomically with owner-only permissions."""

    if limit <= 0:
        return False
    target = Path(path) if path is not None else history_path()
    ordered = sorted(items, key=lambda item: item.received_at, reverse=True)[:limit]
    payload = {
        "version": HISTORY_VERSION,
        "items": [_record_from_item(item) for item in ordered],
    }

    temp_name: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".history-", suffix=".tmp", dir=target.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
        return True
    except OSError:
        return False
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def delete_history(path: str | os.PathLike[str] | None = None) -> None:
    target = Path(path) if path is not None else history_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _record_from_item(item: NotificationItem) -> dict:
    return {
        "id": _clean(item.id, 200),
        "app_name": _clean(item.app_name, 200),
        "title": _clean(item.title, MAX_FIELD_CHARS),
        "text": _clean(item.text, MAX_FIELD_CHARS),
        "ticker": _clean(item.ticker, MAX_FIELD_CHARS),
        "icon_path": _clean(item.icon_path, 1024),
        "dismissable": bool(item.dismissable),
        "reply_id": _clean(item.reply_id, 200),
        "received_at": item.received_at.isoformat(timespec="seconds"),
    }


def _item_from_record(record: object) -> NotificationItem | None:
    if not isinstance(record, dict):
        return None
    identifier = _clean(record.get("id"), 200)
    if not identifier:
        return None
    received_at = _read_time(record.get("received_at"))
    if received_at is None:
        return None
    return NotificationItem(
        id=identifier,
        app_name=_clean(record.get("app_name"), 200) or "Notification",
        title=_clean(record.get("title"), MAX_FIELD_CHARS),
        text=_clean(record.get("text"), MAX_FIELD_CHARS),
        ticker=_clean(record.get("ticker"), MAX_FIELD_CHARS),
        icon_path=_clean(record.get("icon_path"), 1024),
        dismissable=bool(record.get("dismissable", False)),
        reply_id=_clean(record.get("reply_id"), 200),
        received_at=received_at,
    )


def _read_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _clean(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = str(value)
    # A D-Bus string should be valid Unicode, but never let one malformed value
    # make the whole history file unwritable.
    text = text.encode("utf-8", "replace").decode("utf-8")
    return text[:limit]
