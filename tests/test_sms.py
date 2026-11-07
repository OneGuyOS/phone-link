"""Unit tests for the SMS conversation parser and client helpers."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from phone_link.sms import (
    SmsAttachment,
    SmsClient,
    SmsMessage,
    active_conversations_argv,
    build_conversations,
    conversation_reply_argv,
    conversations_from_payload,
    gvariant_to_python,
    GvariantError,
    message_from_signal,
    message_from_struct,
    monitor_argv,
    parse_monitor_line,
    request_all_threads_argv,
    request_conversation_argv,
)


DEVICE = "/modules/kdeconnect/devices/abc"


def message(
    *,
    thread: int = 9,
    uid: int = 41,
    date_ms: int = 1_762_500_000_000,
    kind: int = 1,
    read: int = 0,
    body: str = "Hello",
    addresses: tuple[str, ...] = ("+15551234567",),
    attachments: tuple[SmsAttachment, ...] = (),
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
        attachments=attachments,
    )


class GvariantTests(unittest.TestCase):
    def test_type_words_and_annotations_are_removed(self) -> None:
        self.assertEqual(
            gvariant_to_python("(int64 50, uint64 8, @as [], @a(xsss) [])"),
            (50, 8, [], []),
        )

    def test_single_quoted_strings_keep_escapes(self) -> None:
        self.assertEqual(
            gvariant_to_python(r"(1, 'it\'s — fine\n')"),
            (1, "it's — fine\n"),
        )

    def test_double_quoted_strings_with_apostrophes(self) -> None:
        # gdbus switches to double quotes when the body contains a quote.
        self.assertEqual(
            gvariant_to_python('(1, "Don\'t forget milk", [(\'+1555\',)])'),
            (1, "Don't forget milk", [("+1555",)]),
        )

    def test_booleans_and_nothing(self) -> None:
        self.assertEqual(
            gvariant_to_python("(true, false, nothing)"), (True, False, None)
        )

    def test_variant_wrappers(self) -> None:
        self.assertEqual(gvariant_to_python("(<(1, 2)>, <'x'>)"), ((1, 2), "x"))

    def test_garbage_raises(self) -> None:
        with self.assertRaises(GvariantError):
            gvariant_to_python("(1, 2")


class MonitorLineTests(unittest.TestCase):
    def test_conversation_updated_line(self) -> None:
        line = (
            DEVICE
            + ": org.kde.kdeconnect.device.conversations.conversationUpdated "
            + '(<(1, "Don\'t forget milk", [(\'+15551234567\',)], 1762500000000, '
            + "1, 0, 9, 41, 1, @a(xsss) [])>,)"
        )
        name, payload = parse_monitor_line(line)
        self.assertEqual(name, "conversationUpdated")
        parsed = message_from_signal(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.body, "Don't forget milk")
        self.assertEqual(parsed.thread_id, 9)
        self.assertEqual(parsed.addresses, ("+15551234567",))

    def test_conversation_loaded_line(self) -> None:
        line = (
            DEVICE
            + ": org.kde.kdeconnect.device.conversations.conversationLoaded "
            + "(int64 9, uint64 12)"
        )
        self.assertEqual(parse_monitor_line(line), ("conversationLoaded", (9, 12)))

    def test_unrelated_and_broken_lines(self) -> None:
        self.assertIsNone(parse_monitor_line("Monitoring signals on object foo"))
        self.assertIsNone(
            parse_monitor_line(
                DEVICE
                + ": org.kde.kdeconnect.device.conversations.conversationUpdated (bad"
            )
        )


class MessageTests(unittest.TestCase):
    def test_message_from_struct(self) -> None:
        struct = (
            1,
            "Hi there",
            [("+15551234567",)],
            1_762_500_000_000,
            2,
            1,
            9,
            41,
            1,
            [(7, "image/jpeg", "base64", "unique-7")],
        )
        parsed = message_from_struct(struct)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.body, "Hi there")
        self.assertEqual(parsed.addresses, ("+15551234567",))
        self.assertEqual(parsed.type, 2)
        self.assertEqual(parsed.uid, 41)
        self.assertEqual(
            parsed.attachments,
            (SmsAttachment(7, "image/jpeg", "unique-7"),),
        )

    def test_non_message_events_and_short_structs_are_ignored(self) -> None:
        self.assertIsNone(message_from_struct((2, "not a message")))
        self.assertIsNone(message_from_struct((1, "short")))

    def test_message_properties(self) -> None:
        incoming = message(kind=1, read=0)
        self.assertTrue(incoming.is_incoming)
        self.assertTrue(incoming.unread)
        self.assertFalse(incoming.is_outgoing)
        self.assertFalse(message(kind=2, read=1).unread)
        self.assertTrue(message(kind=5).is_failed)
        self.assertTrue(message(kind=3).is_draft)

    def test_preview_collapses_whitespace(self) -> None:
        self.assertEqual(message(body="  a\n\n b  ").preview, "a b")
        self.assertEqual(
            message(
                body="",
                attachments=(SmsAttachment(1, "image/jpeg", "u"),),
            ).preview,
            "Photo",
        )
        self.assertEqual(
            message(
                body="",
                attachments=(
                    SmsAttachment(1, "image/jpeg", "u"),
                    SmsAttachment(2, "video/mp4", "v"),
                ),
            ).preview,
            "2 attachments",
        )

    def test_date_is_local_epoch(self) -> None:
        parsed = message(date_ms=0)
        self.assertEqual(int(parsed.date.timestamp()), 0)


class ConversationTests(unittest.TestCase):
    def test_grouping_and_ordering(self) -> None:
        old = message(thread=1, uid=1, date_ms=1000)
        new = message(thread=2, uid=2, date_ms=2000)
        newest = message(thread=1, uid=3, date_ms=3000)
        conversations = build_conversations([old, new, newest])
        self.assertEqual([item.thread_id for item in conversations], [1, 2])
        self.assertEqual(len(conversations[0].messages), 2)
        self.assertEqual(conversations[0].latest.uid, 3)
        self.assertEqual(conversations[0].address, "+15551234567")

    def test_unread_count_counts_incoming_unread_only(self) -> None:
        conversation = build_conversations(
            [
                message(thread=4, uid=1, read=0, kind=1),
                message(thread=4, uid=2, read=0, kind=2),
                message(thread=4, uid=3, read=1, kind=1),
            ]
        )[0]
        self.assertEqual(conversation.unread_count, 1)

    def test_active_payload_unwrapping(self) -> None:
        payload = (
            [
                (
                    1,
                    "x",
                    [],
                    0,
                    1,
                    0,
                    9,
                    41,
                    1,
                    [],
                )
            ],
        )
        self.assertEqual(len(conversations_from_payload(payload)), 1)
        self.assertEqual(conversations_from_payload("nope"), ())


class ArgvTests(unittest.TestCase):
    def test_reply_argv(self) -> None:
        argv = conversation_reply_argv("/usr/bin/gdbus", DEVICE, 9, 'Hi "you"')
        self.assertEqual(argv[:5], ["/usr/bin/gdbus", "call", "--session", "--dest", "org.kde.kdeconnect"])
        self.assertEqual(argv[argv.index("--object-path") + 1], DEVICE)
        self.assertEqual(
            argv[argv.index("--method") + 1],
            "org.kde.kdeconnect.device.conversations.replyToConversation",
        )
        self.assertEqual(argv[-3:], ["int64 9", '"Hi \\"you\\""', "@av []"])

    def test_request_argv(self) -> None:
        argv = request_conversation_argv("/usr/bin/gdbus", DEVICE, 9, 60, 120)
        self.assertEqual(argv[-3:], ["int64 9", "int32 60", "int32 120"])
        self.assertTrue(argv[argv.index("--method") + 1].endswith(".requestConversation"))

    def test_seed_and_monitor_argv(self) -> None:
        seed = active_conversations_argv("/usr/bin/gdbus", DEVICE)
        self.assertTrue(seed[seed.index("--method") + 1].endswith(".activeConversations"))
        every = request_all_threads_argv("/usr/bin/gdbus", DEVICE)
        self.assertTrue(
            every[every.index("--method") + 1].endswith(
                ".requestAllConversationThreads"
            )
        )
        monitor = monitor_argv("/usr/bin/gdbus", DEVICE)
        self.assertEqual(monitor[1:3], ["monitor", "--session"])
        self.assertEqual(monitor[-1], DEVICE)


class ClientStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.client = SmsClient()
        self.addCleanup(self.client.deleteLater)

    def test_messages_group_and_page_state(self) -> None:
        self.client._store(message(thread=9, uid=1, date_ms=1000))
        self.client._store(message(thread=9, uid=2, date_ms=2000))
        self.client._store(message(thread=9, uid=2, date_ms=2000, body="updated"))
        messages = self.client.messages(9)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1].body, "updated")
        self.assertEqual(self.client.conversation(9).thread_id, 9)
        self.assertFalse(self.client.has_more(9))
        self.client._known_count[9] = 5
        self.assertTrue(self.client.has_more(9))

    def test_conversations_are_newest_first(self) -> None:
        self.client._store(message(thread=1, uid=1, date_ms=1000))
        self.client._store(message(thread=2, uid=2, date_ms=2000))
        self.assertEqual(
            [item.thread_id for item in self.client.conversations()], [2, 1]
        )


if __name__ == "__main__":
    unittest.main()
