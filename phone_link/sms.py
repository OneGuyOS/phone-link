"""SMS conversations through KDE Connect's conversations plugin.

KDE Connect's conversations API is the one piece of the daemon that PySide6
cannot drive directly: its methods take ``int64`` arguments (``xii``) and its
signals carry a variant of a nested struct array.  PySide6 always marshals
Python ints as ``i`` and cannot read the ``QDBusArgument`` the signal
delivers, so this module talks to the daemon through ``gdbus`` instead.

``gdbus call`` returns and accepts GVariant text, and ``gdbus monitor`` prints
signals as GVariant text on stdout.  That text looks like Python literals
except for type words (``int64 5``), annotations (``@as [...]``), variant
wrappers (``<...>``) and both quote styles, which :func:`gvariant_to_python`
normalises before ``ast.literal_eval`` parses it.

SMS bodies are personal data.  Nothing here writes them to disk; the only
copy is the one the KDE Connect daemon and the phone already keep.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .kdeconnect import DAEMON_PATH, SERVICE

CONVERSATIONS_IFACE = "org.kde.kdeconnect.device.conversations"

EVENT_MESSAGE = 1

TYPE_INBOX = 1
TYPE_SENT = 2
TYPE_DRAFT = 3
TYPE_OUTBOX = 4
TYPE_FAILED = 5
TYPE_QUEUED = 6

_TYPE_WORDS = {
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
    "byte",
    "double",
    "boolean",
    "object",
    "signature",
    "handle",
    "variant",
}
_BOOL_WORDS = {"true": "True", "false": "False", "nothing": "None"}

_SIGNAL_RE = re.compile(
    r":\s*" + re.escape(CONVERSATIONS_IFACE) + r"\.([A-Za-z]+)\s*\((.*)\)\s*$"
)
_SPACE_RE = re.compile(r"\s+")


class GvariantError(ValueError):
    """The daemon printed something this parser does not understand."""


def gvariant_to_python(text: str) -> Any:
    """Parse the GVariant text ``gdbus`` prints into Python values.

    The output is *almost* a Python literal.  This normaliser removes variant
    wrappers and type annotations, maps GVariant booleans, and leaves strings
    alone.  Strings may be single- or double-quoted: gdbus picks the style
    that needs fewer escapes, so a message containing an apostrophe arrives
    double-quoted.  Both styles are copied through so Python sees them.
    """

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                if c == quote:
                    break
            continue
        if ch in "<>":
            i += 1
            continue
        if ch == "@":
            # @as [...], @av [...], @a(xsss) [], @a{sv} {...}
            i += 1
            while i < n and (text[i].isalnum() or text[i] in "_{}()"):
                i += 1
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            word = text[start:i]
            if word in _TYPE_WORDS:
                while i < n and text[i].isspace():
                    i += 1
                continue
            out.append(_BOOL_WORDS.get(word, word))
            continue
        out.append(ch)
        i += 1
    source = "".join(out)
    try:
        return ast.literal_eval(source)
    except (SyntaxError, ValueError) as exc:
        raise GvariantError(str(exc)) from exc


def parse_monitor_line(line: str) -> tuple[str, Any] | None:
    """Return ``(signal_name, arguments)`` for a conversations signal line."""

    for candidate in line.rstrip("\n").splitlines():
        match = _SIGNAL_RE.search(candidate)
        if not match:
            continue
        try:
            return match.group(1), gvariant_to_python(match.group(2))
        except GvariantError:
            return None
    return None


@dataclass(frozen=True)
class SmsAttachment:
    """An attachment's identity; the thumbnail is not kept in memory."""

    part_id: int = 0
    mime_type: str = ""
    unique_identifier: str = ""

    @property
    def label(self) -> str:
        kind = self.mime_type.split("/", 1)[0].strip()
        if kind == "image":
            return "Photo"
        if kind == "video":
            return "Video"
        if kind == "audio":
            return "Audio"
        return "Attachment"


@dataclass(frozen=True)
class SmsMessage:
    event: int
    body: str
    addresses: tuple[str, ...]
    date_ms: int
    type: int
    read: int
    thread_id: int
    uid: int
    sub_id: int
    attachments: tuple[SmsAttachment, ...] = field(default_factory=tuple)

    @property
    def date(self) -> datetime:
        try:
            return datetime.fromtimestamp(self.date_ms / 1000)
        except (OverflowError, OSError, ValueError):
            return datetime.fromtimestamp(0)

    @property
    def address(self) -> str:
        return self.addresses[0] if self.addresses else "Unknown number"

    @property
    def is_incoming(self) -> bool:
        return self.type == TYPE_INBOX

    @property
    def is_outgoing(self) -> bool:
        return self.type in (TYPE_SENT, TYPE_OUTBOX, TYPE_QUEUED)

    @property
    def is_failed(self) -> bool:
        return self.type == TYPE_FAILED

    @property
    def is_draft(self) -> bool:
        return self.type == TYPE_DRAFT

    @property
    def unread(self) -> bool:
        return self.is_incoming and not self.read

    @property
    def preview(self) -> str:
        text = _SPACE_RE.sub(" ", self.body).strip()
        if text:
            return text
        if self.attachments:
            count = len(self.attachments)
            names = {attachment.label for attachment in self.attachments}
            kind = names.pop() if len(names) == 1 else "Attachments"
            return kind if count == 1 else f"{count} attachments"
        return "Empty message"

    @property
    def key(self) -> tuple:
        if self.uid:
            return (self.thread_id, self.uid)
        return (
            self.thread_id,
            self.date_ms,
            self.type,
            self.body,
            self.addresses,
        )

    @property
    def sort_key(self) -> tuple:
        return (self.date_ms, self.uid)


@dataclass(frozen=True)
class SmsConversation:
    thread_id: int
    messages: tuple[SmsMessage, ...]

    @property
    def latest(self) -> SmsMessage | None:
        return self.messages[-1] if self.messages else None

    @property
    def address(self) -> str:
        for message in reversed(self.messages):
            if message.address:
                return message.address
        return "Unknown number"

    @property
    def unread_count(self) -> int:
        return sum(1 for message in self.messages if message.unread)

    @property
    def preview(self) -> str:
        latest = self.latest
        return latest.preview if latest is not None else ""

    @property
    def sort_key(self) -> tuple:
        latest = self.latest
        return latest.sort_key if latest is not None else (0, 0)

    def messages_for_display(self) -> tuple[SmsMessage, ...]:
        return tuple(sorted(self.messages, key=lambda message: message.sort_key))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def _flatten_addresses(value: Any) -> tuple[str, ...]:
    addresses: list[str] = []
    if not isinstance(value, (list, tuple)):
        return ()
    for entry in value:
        if isinstance(entry, (list, tuple)):
            if entry:
                addresses.append(_as_str(entry[0]).strip())
        else:
            addresses.append(_as_str(entry).strip())
    return tuple(address for address in addresses if address)


def message_from_struct(value: Any) -> SmsMessage | None:
    """Build an :class:`SmsMessage` from the D-Bus ``(isa(s)xiixixa(xsss))``."""

    if not isinstance(value, (list, tuple)) or len(value) < 10:
        return None
    event = _as_int(value[0])
    if event != EVENT_MESSAGE:
        return None
    attachments: list[SmsAttachment] = []
    raw_attachments = value[9]
    if isinstance(raw_attachments, (list, tuple)):
        for entry in raw_attachments:
            if not isinstance(entry, (list, tuple)) or len(entry) < 4:
                continue
            attachments.append(
                SmsAttachment(
                    part_id=_as_int(entry[0]),
                    mime_type=_as_str(entry[1]),
                    unique_identifier=_as_str(entry[3]),
                )
            )
    return SmsMessage(
        event=event,
        body=_as_str(value[1]),
        addresses=_flatten_addresses(value[2]),
        date_ms=_as_int(value[3]),
        type=_as_int(value[4]),
        read=_as_int(value[5]),
        thread_id=_as_int(value[6]),
        uid=_as_int(value[7]),
        sub_id=_as_int(value[8]),
        attachments=tuple(attachments),
    )


def message_from_signal(payload: Any) -> SmsMessage | None:
    """Unwrap the single variant argument of ``conversationUpdated``."""

    if isinstance(payload, (list, tuple)) and len(payload) == 1:
        payload = payload[0]
    return message_from_struct(payload)


def conversations_from_payload(payload: Any) -> tuple[SmsMessage, ...]:
    """Unwrap ``activeConversations()``'s single ``av`` argument."""

    if isinstance(payload, (list, tuple)) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, (list, tuple)):
        return ()
    messages = [message_from_struct(entry) for entry in payload]
    return tuple(message for message in messages if message is not None)


def build_conversations(
    messages: Iterable[SmsMessage],
) -> tuple[SmsConversation, ...]:
    """Group messages by thread, newest conversation first."""

    threads: dict[int, list[SmsMessage]] = {}
    for message in messages:
        threads.setdefault(message.thread_id, []).append(message)
    conversations = [
        SmsConversation(
            thread_id=thread_id,
            messages=tuple(sorted(items, key=lambda message: message.sort_key)),
        )
        for thread_id, items in threads.items()
    ]
    conversations.sort(key=lambda conversation: conversation.sort_key, reverse=True)
    return tuple(conversations)


def conversation_reply_argv(
    gdbus: str,
    device_path: str,
    thread_id: int,
    message: str,
) -> list[str]:
    """The gdbus call that replies inside an existing conversation."""

    return [
        gdbus,
        "call",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        device_path,
        "--method",
        f"{CONVERSATIONS_IFACE}.replyToConversation",
        f"int64 {int(thread_id)}",
        json.dumps(str(message), ensure_ascii=False),
        "@av []",
    ]


def request_conversation_argv(
    gdbus: str,
    device_path: str,
    thread_id: int,
    start: int,
    end: int,
) -> list[str]:
    """The gdbus call that asks for messages ``[start, end)`` from newest."""

    return [
        gdbus,
        "call",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        device_path,
        "--method",
        f"{CONVERSATIONS_IFACE}.requestConversation",
        f"int64 {int(thread_id)}",
        f"int32 {int(start)}",
        f"int32 {int(end)}",
    ]


def active_conversations_argv(gdbus: str, device_path: str) -> list[str]:
    return [
        gdbus,
        "call",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        device_path,
        "--method",
        f"{CONVERSATIONS_IFACE}.activeConversations",
    ]


def request_all_threads_argv(gdbus: str, device_path: str) -> list[str]:
    return [
        gdbus,
        "call",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        device_path,
        "--method",
        f"{CONVERSATIONS_IFACE}.requestAllConversationThreads",
    ]


def monitor_argv(gdbus: str, device_path: str) -> list[str]:
    return [
        gdbus,
        "monitor",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        device_path,
    ]


class SmsClient(QObject):
    """Keeps conversations from KDE Connect in memory and sends replies.

    The daemon only pushes messages through signals, so a long-lived
    ``gdbus monitor`` supplies live updates; ``gdbus call`` handles seeding
    the list and paging a thread.  All processes run without blocking the UI.
    """

    changed = Signal()
    threadLoading = Signal(int)
    threadLoaded = Signal(int)
    sent = Signal(int)
    sendFailed = Signal(int, str)
    error = Signal(str)

    REQUEST_PAGE = 60
    FALLBACK_LOAD_MS = 8000
    CALL_TIMEOUT_MS = 15000
    RESTART_MS = 3000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._gdbus = shutil.which("gdbus") or ""
        self._device_path = ""
        self._monitor: QProcess | None = None
        self._monitor_error_shown = False
        self._calls: dict[QProcess, Any] = {}
        self._call_timers: dict[QProcess, QTimer] = {}
        self._messages: dict[int, dict[tuple, SmsMessage]] = {}
        self._requested_end: dict[int, int] = {}
        self._known_count: dict[int, int] = {}
        self._loading: set[int] = set()
        self._stopping = False
        self._seeded = False

    # -- lifecycle ---------------------------------------------------------

    def set_device(self, device_path: str) -> None:
        """Track one phone's conversations; an empty path stops everything."""

        device_path = str(device_path or "")
        if device_path == self._device_path:
            return
        self._stop_workers()
        self._messages.clear()
        self._requested_end.clear()
        self._known_count.clear()
        self._loading.clear()
        self._seeded = False
        self._monitor_error_shown = False
        self._device_path = device_path
        if device_path:
            self._start_workers()
        self.changed.emit()

    def stop(self) -> None:
        self._stopping = True
        self._device_path = ""
        self._stop_workers()
        self._messages.clear()
        self._requested_end.clear()
        self._known_count.clear()
        self._loading.clear()
        self._seeded = False

    def refresh(self) -> None:
        """Ask the daemon for the conversation list again."""

        if not self._device_path:
            return
        self._loading.clear()
        self._seeded = False
        self._seed()

    def _stop_workers(self) -> None:
        if self._monitor is not None:
            monitor = self._monitor
            self._monitor = None
            try:
                monitor.kill()
                monitor.waitForFinished(500)
            except RuntimeError:
                pass
            monitor.deleteLater()
        for process in tuple(self._calls):
            self._end_call(process, kill=True)

    def _start_workers(self) -> None:
        self._stopping = False
        if not self._gdbus:
            self.error.emit(
                "Reading SMS conversations needs gdbus (from glib2). "
                "Install libglib2.0-bin, or read messages in KDE Connect."
            )
            return
        self._spawn_monitor()
        self._seed()

    # -- monitor -----------------------------------------------------------

    def _spawn_monitor(self) -> None:
        if not self._device_path or not self._gdbus:
            return
        monitor = QProcess(self)
        monitor.setProgram(self._gdbus)
        monitor.setArguments(monitor_argv(self._gdbus, self._device_path)[1:])
        monitor.readyReadStandardOutput.connect(self._read_monitor)
        monitor.finished.connect(self._on_monitor_finished)
        monitor.start()
        self._monitor = monitor

    def _read_monitor(self) -> None:
        monitor = self._monitor
        if monitor is not None:
            self._read_monitor_from(monitor)

    def _read_monitor_from(self, monitor: QProcess) -> None:
        data = bytes(monitor.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            parsed = parse_monitor_line(line)
            if parsed is not None:
                self._handle_signal(*parsed)

    def _handle_signal(self, name: str, payload: Any) -> None:
        if name in ("conversationCreated", "conversationUpdated"):
            message = message_from_signal(payload)
            if message is not None:
                self._store(message)
                self.changed.emit()
            return
        if name == "conversationLoaded":
            values = payload if isinstance(payload, (list, tuple)) else ()
            if len(values) >= 2:
                thread_id = _as_int(values[0])
                count = _as_int(values[1])
                self._known_count[thread_id] = max(
                    count, self._known_count.get(thread_id, 0)
                )
                self._loading.discard(thread_id)
                self.threadLoaded.emit(thread_id)
            return
        if name == "conversationRemoved":
            values = payload if isinstance(payload, (list, tuple)) else ()
            if values:
                thread_id = _as_int(values[0])
                if self._messages.pop(thread_id, None) is not None:
                    self._requested_end.pop(thread_id, None)
                    self._known_count.pop(thread_id, None)
                    self.changed.emit()
            return

    def _on_monitor_finished(self, *_args) -> None:
        monitor = self._monitor
        self._monitor = None
        if monitor is None:
            return
        self._read_monitor_from(monitor)
        stderr = bytes(monitor.readAllStandardError()).decode("utf-8", "replace")
        monitor.deleteLater()
        if self._stopping or not self._device_path:
            return
        if (
            not self._monitor_error_shown
            and stderr.strip()
            and (
                "UnknownObject" in stderr
                or "No such object" in stderr
                or "No such interface" in stderr
            )
        ):
            self._monitor_error_shown = True
            self.error.emit(
                "This phone does not expose KDE Connect's SMS conversations. "
                "Enable the SMS plugin on the phone and in KDE Connect."
            )
        QTimer.singleShot(self.RESTART_MS, self._restart_monitor)

    def _restart_monitor(self) -> None:
        if self._stopping or not self._device_path or self._monitor is not None:
            return
        self._spawn_monitor()
        if not self._seeded:
            self._seed()

    # -- reading -----------------------------------------------------------

    def _seed(self) -> None:
        self._call(
            active_conversations_argv(self._gdbus, self._device_path),
            self._seed_done,
        )

    def _seed_done(self, ok: bool, stdout: str, stderr: str) -> None:
        if not ok:
            self.error.emit(_failure("Could not read conversations", stdout, stderr))
            return
        try:
            payload = gvariant_to_python(stdout)
        except GvariantError as exc:
            self.error.emit(f"Could not read conversations: {exc}")
            return
        messages = conversations_from_payload(payload)
        for message in messages:
            self._store(message)
        self._seeded = True
        # The daemon caches the threads it has seen.  Asking for every thread
        # makes the phone announce the ones missing from that cache.
        self._call(
            request_all_threads_argv(self._gdbus, self._device_path),
            lambda *_args: None,
        )
        self.changed.emit()

    def load_thread(self, thread_id: int, *, earlier: bool = False) -> None:
        """Ask the phone for a thread's newest messages or the next page back."""

        thread_id = int(thread_id)
        if not self._device_path or not self._gdbus:
            return
        if thread_id in self._loading:
            return
        start = self._requested_end.get(thread_id, 0) if earlier else 0
        end = start + self.REQUEST_PAGE
        self._requested_end[thread_id] = max(
            self._requested_end.get(thread_id, 0), end
        )
        self._loading.add(thread_id)
        self.threadLoading.emit(thread_id)
        self._call(
            request_conversation_argv(
                self._gdbus, self._device_path, thread_id, start, end
            ),
            lambda ok, stdout, stderr: self._load_done(
                thread_id, ok, stdout, stderr
            ),
        )
        QTimer.singleShot(
            self.FALLBACK_LOAD_MS, lambda: self._load_timeout(thread_id)
        )

    def _load_done(
        self, thread_id: int, ok: bool, stdout: str, stderr: str
    ) -> None:
        if not ok:
            self._loading.discard(thread_id)
            self.error.emit(_failure("Could not load that conversation", stdout, stderr))
            self.threadLoaded.emit(thread_id)

    def _load_timeout(self, thread_id: int) -> None:
        if thread_id in self._loading:
            self._loading.discard(thread_id)
            self.threadLoaded.emit(thread_id)

    # -- sending -----------------------------------------------------------

    def reply(self, thread_id: int, text: str) -> None:
        thread_id = int(thread_id)
        message = str(text).strip()
        if not message:
            return
        if not self._device_path or not self._gdbus:
            self.sendFailed.emit(thread_id, "No phone is connected through KDE Connect.")
            return
        self._call(
            conversation_reply_argv(
                self._gdbus, self._device_path, thread_id, message
            ),
            lambda ok, stdout, stderr: self._reply_done(
                thread_id, ok, stdout, stderr
            ),
        )

    def _reply_done(
        self, thread_id: int, ok: bool, stdout: str, stderr: str
    ) -> None:
        if ok:
            self.sent.emit(thread_id)
            return
        self.sendFailed.emit(
            thread_id, _failure("KDE Connect did not send the message", stdout, stderr)
        )

    # -- state -------------------------------------------------------------

    def conversations(self) -> tuple[SmsConversation, ...]:
        flattened = [
            message
            for thread in self._messages.values()
            for message in thread.values()
        ]
        return build_conversations(flattened)

    def messages(self, thread_id: int) -> tuple[SmsMessage, ...]:
        thread = self._messages.get(int(thread_id), {})
        return tuple(sorted(thread.values(), key=lambda message: message.sort_key))

    def conversation(self, thread_id: int) -> SmsConversation | None:
        messages = self.messages(thread_id)
        if not messages:
            return None
        return SmsConversation(thread_id=int(thread_id), messages=messages)

    def has_more(self, thread_id: int) -> bool:
        """Whether the phone knows messages older than the ones loaded."""

        loaded = len(self._messages.get(int(thread_id), {}))
        known = self._known_count.get(int(thread_id), 0)
        return known > loaded

    def _store(self, message: SmsMessage) -> None:
        thread = self._messages.setdefault(message.thread_id, {})
        thread[message.key] = message

    # -- gdbus calls -------------------------------------------------------

    def _call(self, argv: list[str], callback) -> None:
        if not self._gdbus:
            callback(False, "", "gdbus is not installed")
            return
        process = QProcess(self)
        process.setProgram(argv[0])
        process.setArguments(argv[1:])
        self._calls[process] = callback
        process.finished.connect(lambda *_: self._call_finished(process))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self.CALL_TIMEOUT_MS)
        timer.timeout.connect(lambda: self._call_timeout(process))
        self._call_timers[process] = timer
        timer.start()
        process.start()

    def _call_finished(self, process: QProcess) -> None:
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        stderr = bytes(process.readAllStandardError()).decode("utf-8", "replace")
        ok = process.exitCode() == 0
        callback = self._end_call(process, kill=False)
        if callback is not None:
            callback(ok, stdout, stderr)

    def _call_timeout(self, process: QProcess) -> None:
        if process not in self._calls:
            return
        callback = self._end_call(process, kill=True)
        if callback is not None:
            callback(False, "", "KDE Connect did not answer in time.")

    def _end_call(self, process: QProcess, *, kill: bool):
        timer = self._call_timers.pop(process, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        callback = self._calls.pop(process, None)
        try:
            if kill and process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(500)
        except RuntimeError:
            pass
        process.deleteLater()
        return callback


def _failure(prefix: str, stdout: str, stderr: str) -> str:
    detail = (stderr or stdout or "").strip()
    if detail:
        detail = detail.splitlines()[0]
        return f"{prefix}: {detail}"
    return prefix + "."
