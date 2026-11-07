"""Unit tests for the read-only phone snapshot sent with a request."""

from __future__ import annotations

import unittest
from datetime import datetime

from phone_link.models import (
    AdbDevice,
    KdeDevice,
    KdeSnapshot,
    MediaState,
    NotificationItem,
    PhoneSnapshot,
)
from phone_link.phone_context import (
    MAX_CONVERSATIONS,
    MAX_NOTIFICATIONS,
    build_phone_context,
)
from phone_link.sms import SmsConversation, SmsMessage

NOW = datetime(2026, 11, 7, 18, 45)


def message(
    thread_id: int,
    body: str,
    *,
    read: int = 0,
    date_ms: int = 1_700_000_000_000,
) -> SmsMessage:
    return SmsMessage(
        event=1,
        body=body,
        addresses=("+15551234567",),
        date_ms=date_ms,
        type=1,
        read=read,
        thread_id=thread_id,
        uid=0,
        sub_id=1,
    )


class SnapshotTests(unittest.TestCase):
    def test_disconnected_phone_adds_nothing(self) -> None:
        self.assertEqual(build_phone_context(now=NOW), "")

    def test_full_snapshot(self) -> None:
        phone = PhoneSnapshot(
            selected=AdbDevice(serial="R5GL163NGDA", state="device", model="SM_S731B"),
            name="Galaxy S25 FE",
            battery=78,
            charging=True,
        )
        kde = KdeSnapshot(
            available=True,
            device=KdeDevice(
                id="abc", name="Galaxy S25 FE", reachable=True, paired=True
            ),
            battery=50,
            notifications=(
                NotificationItem(
                    id="1",
                    app_name="Signal",
                    title="Alice",
                    text="Dinner at 7?",
                ),
                NotificationItem(
                    id="2", app_name="Gmail", title="Subject", text=""
                ),
            ),
        )
        media = MediaState(
            available=True,
            title="Song",
            artist="Artist",
            is_playing=True,
        )
        conversations = (
            SmsConversation(
                thread_id=1,
                messages=(
                    message(1, "See you at 7", read=0),
                    message(1, "Bring the car", read=0),
                ),
            ),
        )
        text = build_phone_context(
            phone, kde, media, conversations, now=NOW
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "Phone state from Phone Link (read-only; this is what the "
                    "app can see right now, captured 2026-11-07 18:45). Use it "
                    "when the user asks about the phone; anything not listed is "
                    "unavailable through Phone Link.",
                    "- Device: Galaxy S25 FE, USB, battery 78%, charging",
                    '- Now playing: "Song" by Artist (playing)',
                    "Active notifications, newest first (2):",
                    "- Signal · Alice: Dinner at 7?",
                    "- Gmail · Subject",
                    "Unread text conversations, newest first (1):",
                    '- +15551234567: 2 unread. Latest: "Bring the car"',
                ]
            ),
        )

    def test_kde_only_still_describes_the_phone(self) -> None:
        kde = KdeSnapshot(
            available=True,
            device=KdeDevice(
                id="abc", name="Galaxy", reachable=True, paired=True
            ),
            battery=40,
        )
        text = build_phone_context(kde=kde, now=NOW)
        self.assertIn("- Device: Galaxy, KDE Connect, battery 40%", text)
        self.assertNotIn("Now playing", text)

    def test_media_paused_and_without_title(self) -> None:
        phone = PhoneSnapshot(
            selected=AdbDevice(serial="R5", state="device"),
            battery=10,
        )
        paused = build_phone_context(
            phone,
            media=MediaState(available=True, title="Song", is_playing=False),
            now=NOW,
        )
        self.assertIn('Now playing: "Song" (paused)', paused)
        empty = build_phone_context(
            phone, media=MediaState(available=True, title=""), now=NOW
        )
        self.assertNotIn("Now playing", empty)

    def test_notifications_are_capped_and_report_the_rest(self) -> None:
        phone = PhoneSnapshot(
            selected=AdbDevice(serial="R5", state="device"), battery=10
        )
        notifications = tuple(
            NotificationItem(
                id=str(index),
                app_name="App",
                title=f"Title {index}",
                text="Body",
            )
            for index in range(MAX_NOTIFICATIONS + 3)
        )
        text = build_phone_context(
            phone,
            KdeSnapshot(
                available=True,
                device=KdeDevice(id="a", name="Phone", reachable=True, paired=True),
                notifications=notifications,
            ),
            now=NOW,
        )
        self.assertEqual(text.count("- App · Title"), MAX_NOTIFICATIONS)
        self.assertIn("- … 3 more not shown", text)
        self.assertNotIn("Title 10", text)

    def test_sms_capped_unread_only_and_truncated(self) -> None:
        phone = PhoneSnapshot(
            selected=AdbDevice(serial="R5", state="device"), battery=10
        )
        conversations = tuple(
            SmsConversation(
                thread_id=index,
                messages=(message(index, "x" * 400),),
            )
            for index in range(MAX_CONVERSATIONS + 2)
        ) + (
            SmsConversation(
                thread_id=99,
                messages=(message(99, "already read", read=1),),
            ),
        )
        text = build_phone_context(
            phone, conversations=conversations, now=NOW
        )
        self.assertEqual(text.count("unread. Latest:"), MAX_CONVERSATIONS)
        self.assertIn("- … 2 more not shown", text)
        self.assertNotIn("already read", text)
        self.assertNotIn("x" * 400, text)
        self.assertIn("…", text)

    def test_long_notification_text_is_truncated(self) -> None:
        phone = PhoneSnapshot(
            selected=AdbDevice(serial="R5", state="device"), battery=10
        )
        kde = KdeSnapshot(
            available=True,
            device=KdeDevice(id="a", name="Phone", reachable=True, paired=True),
            notifications=(
                NotificationItem(
                    id="1",
                    app_name="Mail",
                    title="Build finished",
                    text="z" * 500,
                ),
            ),
        )
        text = build_phone_context(phone, kde, now=NOW)
        self.assertIn("…", text)
        self.assertNotIn("z" * 500, text)


if __name__ == "__main__":
    unittest.main()
