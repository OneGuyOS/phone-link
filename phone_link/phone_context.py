"""A compact, read-only snapshot of the phone for the agent's prompt.

Phone Link already knows the phone's battery, what is playing, the active
notifications and the SMS conversations it has loaded.  When the user turns on
**Phone context**, that information is appended to the system prompt of the
built-in providers so questions like "what's on my phone?" or "summarize my
unread texts" work.

The snapshot is generated per request and is never added to the saved chat or
written anywhere.  It is only used for the built-in providers, which run on
this computer or over the ADB link; a custom endpoint never receives it.
"""

from __future__ import annotations

from datetime import datetime

from .models import KdeSnapshot, MediaState, NotificationItem, PhoneSnapshot
from .sms import SmsConversation

MAX_NOTIFICATIONS = 8
MAX_CONVERSATIONS = 5
MAX_NOTIFICATION_CHARS = 160
MAX_MESSAGE_CHARS = 180


def _one_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _device_line(phone: PhoneSnapshot, kde: KdeSnapshot) -> str:
    device = phone.selected
    name = phone.name or (device.display_name if device else "")
    if not name and kde.device is not None:
        name = kde.device.name
    parts: list[str] = []
    if name:
        parts.append(name)
    if device is not None:
        parts.append(device.connection_label)
    elif kde.connected:
        parts.append("KDE Connect")
    battery = phone.battery if phone.battery is not None else kde.battery
    if battery is not None:
        parts.append(f"battery {battery}%")
    charging = phone.charging or kde.charging
    if charging:
        parts.append("charging")
    return "Device: " + ", ".join(parts) if parts else ""


def _media_line(media: MediaState) -> str:
    if not media.available or not media.title.strip():
        return ""
    title = _one_line(media.title, MAX_NOTIFICATION_CHARS)
    line = f'Now playing: "{title}"'
    if media.artist.strip():
        line += f" by {_one_line(media.artist, 80)}"
    line += " (playing)" if media.is_playing else " (paused)"
    return line


def _notification_lines(
    notifications: tuple[NotificationItem, ...],
) -> list[str]:
    if not notifications:
        return []
    shown = notifications[:MAX_NOTIFICATIONS]
    lines = [
        f"Active notifications, newest first ({len(notifications)}):"
    ]
    for item in shown:
        heading = _one_line(item.heading, MAX_NOTIFICATION_CHARS)
        body = _one_line(item.body, MAX_NOTIFICATION_CHARS)
        app = item.app_name.strip()
        prefix = f"{app} · " if app else ""
        line = f"- {prefix}{heading}"
        if body and body != heading:
            line += f": {body}"
        lines.append(line)
    if len(notifications) > len(shown):
        lines.append(
            f"- … {len(notifications) - len(shown)} more not shown"
        )
    return lines


def _sms_lines(conversations: tuple[SmsConversation, ...]) -> list[str]:
    unread = [item for item in conversations if item.unread_count > 0]
    if not unread:
        return []
    shown = unread[:MAX_CONVERSATIONS]
    lines = [
        f"Unread text conversations, newest first ({len(unread)}):"
    ]
    for conversation in shown:
        latest = conversation.latest
        preview = (
            _one_line(latest.preview, MAX_MESSAGE_CHARS)
            if latest is not None
            else ""
        )
        count = conversation.unread_count
        line = (
            f"- {conversation.address}: {count} unread"
            + (f'. Latest: "{preview}"' if preview else "")
        )
        lines.append(line)
    if len(unread) > len(shown):
        lines.append(f"- … {len(unread) - len(shown)} more not shown")
    return lines


def build_phone_context(
    phone: PhoneSnapshot | None = None,
    kde: KdeSnapshot | None = None,
    media: MediaState | None = None,
    conversations: tuple[SmsConversation, ...] = (),
    *,
    now: datetime | None = None,
) -> str:
    """The prompt block, or an empty string when the phone is not connected."""

    phone = phone or PhoneSnapshot()
    kde = kde or KdeSnapshot()
    media = media or MediaState()
    if not phone.connected and not kde.connected:
        return ""

    captured = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lines = [
        "Phone state from Phone Link (read-only; this is what the app can see "
        f"right now, captured {captured}). Use it when the user asks about "
        "the phone; anything not listed is unavailable through Phone Link."
    ]
    device = _device_line(phone, kde)
    if device:
        lines.append("- " + device)
    playing = _media_line(media)
    if playing:
        lines.append("- " + playing)
    for line in _notification_lines(kde.notifications):
        lines.append(line)
    for line in _sms_lines(conversations):
        lines.append(line)
    return "\n".join(lines)
