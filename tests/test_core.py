"""Small unit tests for the non-UI layers."""

from __future__ import annotations

import re
import stat
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from phone_link.adb import (
    _describe_adb_failure,
    _parse_device_info,
    choose_device,
    findmyphone_stop_argv,
    parse_adb_devices,
    parse_rotation,
)
from phone_link.clipboard_sync import ClipboardSyncGate
from phone_link.dependencies import CheckItem, DependencyReport, installation_text
from phone_link.kdeconnect import (
    KdeConnectClient,
    gdbus_addresses,
    gdbus_sms_argv,
    media_state_from,
    sinks_from_json,
)
from phone_link.mdns import parse_mdns_response
from phone_link.models import (
    AdbDevice,
    KdeDevice,
    KdeSnapshot,
    MediaState,
    NotificationItem,
    PhoneSink,
)
from phone_link.notification_store import (
    MAX_FIELD_CHARS,
    delete_history,
    load_history,
    save_history,
)
from phone_link.scrcpy import DEX_DISPLAY, MirrorQuality, _friendly_failure, build_scrcpy_args
from phone_link.ui.mirror import in_app_mirror_audio
from phone_link.ui.notifications import notification_matches
from phone_link.wifi import make_qr_pairing, parse_mdns_services


DEVICES_OUTPUT = """List of devices attached
R58M90ABCDE            device usb:3-2 product:r13sue model:SM_S731U1 device:r13s transport_id:1
192.168.1.50:5555      device product:r13sue model:SM_S731U1 device:r13s transport_id:2
emulator-5554          unauthorized transport_id:3
"""


class AdbParsingTests(unittest.TestCase):
    def test_parse_devices(self) -> None:
        devices = parse_adb_devices(DEVICES_OUTPUT)
        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0].serial, "R58M90ABCDE")
        self.assertEqual(devices[0].display_name, "SM S731U1")
        self.assertFalse(devices[0].is_wireless)
        self.assertTrue(devices[1].is_wireless)
        self.assertEqual(devices[2].state, "unauthorized")

    def test_usb_wins_over_wireless(self) -> None:
        devices = parse_adb_devices(DEVICES_OUTPUT)
        selected = choose_device(devices)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.serial, "R58M90ABCDE")

    def test_preferred_serial(self) -> None:
        devices = parse_adb_devices(DEVICES_OUTPUT)
        selected = choose_device(devices, "192.168.1.50:5555")
        self.assertIsNotNone(selected)
        self.assertTrue(selected.is_wireless)
        self.assertIsNone(choose_device(devices, "missing"))

    def test_unauthorized_message(self) -> None:
        devices = parse_adb_devices(DEVICES_OUTPUT)
        message = _describe_adb_failure(devices[2:], None)
        self.assertIn("authorized", message)

    def test_parse_info_and_battery(self) -> None:
        output = """__PL_MODEL__
SM_S731U1
__PL_MANUFACTURER__
samsung
__PL_BATTERY__
Current Battery Service state:
  AC powered: false
  USB powered: true
  status: 2
  level: 80
  scale: 100
"""
        name, battery, charging = _parse_device_info(output, fallback="fallback")
        self.assertEqual(name, "Samsung SM S731U1")
        self.assertEqual(battery, 80)
        self.assertTrue(charging)

    def test_parse_info_scaled_battery(self) -> None:
        output = """__PL_BATTERY__
  level: 3800
  scale: 5000
  status: 3
"""
        _, battery, charging = _parse_device_info(output, fallback=None)
        self.assertEqual(battery, 76)
        self.assertFalse(charging)


class RotationParsingTests(unittest.TestCase):
    def test_viewport_orientation(self) -> None:
        output = (
            "__PL_ROTATION__\n"
            "Viewport INTERNAL: displayId=0, orientation=1, densityDpi=450\n"
        )
        self.assertEqual(parse_rotation(output), 1)

    def test_display_rotation_fallback(self) -> None:
        output = (
            "__PL_ROTATION__\n"
            "mDisplayRotation=ROTATION_3 mRotation=ROTATION_3\n"
        )
        self.assertEqual(parse_rotation(output), 3)

    def test_marker_section_wins(self) -> None:
        output = (
            "orientation=0 in some unrelated line\n"
            "__PL_ROTATION__\n"
            "Viewport INTERNAL: orientation=2, logicalFrame=[0, 0, 2340, 1080]\n"
        )
        self.assertEqual(parse_rotation(output), 2)

    def test_missing_or_invalid_rotation(self) -> None:
        self.assertIsNone(parse_rotation("__PL_ROTATION__\nno rotation here\n"))
        self.assertIsNone(parse_rotation("Viewport INTERNAL: orientation=9\n"))
        self.assertIsNone(parse_rotation(""))


class ModelTests(unittest.TestCase):
    def test_notification_fallbacks(self) -> None:
        item = NotificationItem(id="1", app_name="Messages", title="", text="", ticker="Hello")
        self.assertEqual(item.heading, "Hello")
        self.assertEqual(item.body, "")
        item = NotificationItem(id="2", app_name="Messages", title="Sam", text="", ticker="Hi")
        self.assertEqual(item.heading, "Sam")
        self.assertEqual(item.body, "Hi")

    def test_kde_snapshot_connected(self) -> None:
        device = KdeDevice(id="x", name="Phone", reachable=True, paired=True)
        self.assertTrue(KdeSnapshot(device=device).connected)
        device = KdeDevice(id="x", name="Phone", reachable=False, paired=True)
        self.assertFalse(KdeSnapshot(device=device).connected)


class MediaStateTests(unittest.TestCase):
    @staticmethod
    def _getter(values: dict):
        return lambda name, default=None: values.get(name, default)

    def test_builds_state_from_properties(self) -> None:
        state = media_state_from(
            self._getter(
                {
                    "playerList": ["Mock Player"],
                    "player": "Mock Player",
                    "title": "Mock Song",
                    "artist": "Test Artist",
                    "album": "Mock Album",
                    "isPlaying": True,
                    "position": 30000,
                    "length": 200000,
                    "canSeek": True,
                }
            )
        )
        self.assertTrue(state.available)
        self.assertEqual(state.player, "Mock Player")
        self.assertEqual(state.players, ("Mock Player",))
        self.assertEqual(state.title, "Mock Song")
        self.assertEqual(state.artist, "Test Artist")
        self.assertTrue(state.is_playing)
        self.assertTrue(state.can_seek)
        self.assertEqual((state.position, state.length), (30000, 200000))

    def test_no_player_is_unavailable(self) -> None:
        state = media_state_from(self._getter({}))
        self.assertEqual(state, MediaState())
        self.assertFalse(state.available)

    def test_position_is_clamped_to_length(self) -> None:
        state = media_state_from(
            self._getter({"player": "P", "length": 1000, "position": 5000})
        )
        self.assertEqual(state.position, 1000)

    def test_bad_numbers_become_zero(self) -> None:
        state = media_state_from(
            self._getter({"player": "P", "length": "oops", "position": -20})
        )
        self.assertEqual((state.position, state.length), (0, 0))

    def test_format_duration(self) -> None:
        from phone_link.ui.media import format_duration

        self.assertEqual(format_duration(0), "0:00")
        self.assertEqual(format_duration(65_000), "1:05")
        self.assertEqual(format_duration(3_725_000), "1:02:05")
        self.assertEqual(format_duration(-500), "0:00")


class PhoneActionTests(unittest.TestCase):
    def test_sinks_from_json_parses_and_clamps(self) -> None:
        raw = (
            b'[{"name": "music", "description": "Media", "maxVolume": 15, '
            b'"volume": 18, "muted": true}, {"description": "No name"}]'
        )
        sinks = sinks_from_json(raw)
        self.assertEqual(len(sinks), 1)
        sink = sinks[0]
        self.assertIsInstance(sink, PhoneSink)
        self.assertEqual(sink.name, "music")
        self.assertEqual(sink.label, "Media")
        self.assertEqual(sink.max_volume, 15)
        self.assertEqual(sink.volume, 15)
        self.assertTrue(sink.muted)

    def test_sinks_from_json_accepts_wrapped_lists(self) -> None:
        sinks = sinks_from_json('{"sinks": [{"name": "call", "volume": 3}]}')
        self.assertEqual(len(sinks), 1)
        self.assertEqual(sinks[0].max_volume, 100)
        self.assertEqual(sinks[0].label, "call")

    def test_sinks_from_json_ignores_junk(self) -> None:
        for value in (None, b"", "not json", b"{}", b'[{"volume": 3}]'):
            with self.subTest(value=value):
                self.assertEqual(sinks_from_json(value), ())

    def test_gdbus_addresses_are_variants_of_structs(self) -> None:
        self.assertEqual(
            gdbus_addresses(["+15551234567"]),
            '@av [<("+15551234567",)>]',
        )
        self.assertEqual(
            gdbus_addresses(["1", "2"]),
            '@av [<("1",)>, <("2",)>]',
        )

    def test_gdbus_sms_argv_uses_exact_dbus_types(self) -> None:
        argv = gdbus_sms_argv(
            "/usr/bin/gdbus",
            "/modules/kdeconnect/devices/abc/sms",
            ["+15551234567"],
            'See you at 7',
        )
        self.assertEqual(argv[0], "/usr/bin/gdbus")
        self.assertEqual(argv[argv.index("--object-path") + 1], "/modules/kdeconnect/devices/abc/sms")
        self.assertEqual(
            argv[argv.index("--method") + 1],
            "org.kde.kdeconnect.device.sms.sendSms",
        )
        self.assertEqual(
            argv[-4:],
            ['@av [<("+15551234567",)>]', '"See you at 7"', "@av []", "int64 -1"],
        )

    def test_gdbus_sms_argv_quotes_the_message_for_gvariant(self) -> None:
        argv = gdbus_sms_argv("gdbus", "/x", ["1"], 'he said "hi"\nok')
        self.assertEqual(argv[-4], '@av [<("1",)>]')
        self.assertEqual(argv[-3], '"he said \\"hi\\"\\nok"')


class PhonePluginTests(unittest.TestCase):
    class _Reply:
        def __init__(self, argument: object) -> None:
            self._argument = argument

        def arguments(self) -> list[object]:
            return [self._argument]

    def setUp(self) -> None:
        from PySide6.QtCore import QCoreApplication

        self._app = QCoreApplication.instance() or QCoreApplication([])
        self.client = KdeConnectClient()
        self.client._selected = KdeDevice(
            id="abc", name="Phone", reachable=True, paired=True
        )

    def test_phone_plugins_returns_daemon_plugin_ids(self) -> None:
        self.client._call = lambda *_args, **_kwargs: (
            self._Reply(["kdeconnect_ping", "kdeconnect_sms"]),
            None,
        )
        self.assertEqual(
            self.client.phone_plugins(), {"kdeconnect_ping", "kdeconnect_sms"}
        )

    def test_phone_plugins_is_none_when_the_daemon_does_not_answer(self) -> None:
        self.client._call = lambda *_args, **_kwargs: (None, "no daemon")
        self.assertIsNone(self.client.phone_plugins())

    def test_phone_plugins_is_empty_without_a_reachable_phone(self) -> None:
        self.client._selected = None
        self.assertEqual(self.client.phone_plugins(), set())

    def test_self_id_reads_the_daemon(self) -> None:
        self.client._call = lambda *_args, **_kwargs: (
            self._Reply("c37a01638fef4bb39e4ea9715c3b8997"),
            None,
        )
        self.assertEqual(
            self.client.self_id(), "c37a01638fef4bb39e4ea9715c3b8997"
        )

    def test_self_id_is_empty_when_the_daemon_does_not_answer(self) -> None:
        self.client._call = lambda *_args, **_kwargs: (None, "no daemon")
        self.assertEqual(self.client.self_id(), "")


class FindMyPhoneStopTests(unittest.TestCase):
    def test_stop_argv_presses_the_phones_found_it_receiver(self) -> None:
        argv = findmyphone_stop_argv("adb", "MOCK123", "desktop-id")
        self.assertEqual(argv[:4], ["adb", "-s", "MOCK123", "shell"])
        self.assertEqual(
            argv[argv.index("-n") + 1],
            "org.kde.kdeconnect_tp/"
            "org.kde.kdeconnect.plugins.findmyphone.FindMyPhoneReceiver",
        )
        self.assertEqual(
            argv[argv.index("-a") + 1],
            "org.kde.kdeconnect.plugins.findmyphone.foundIt",
        )
        self.assertEqual(argv[-2:], ["deviceId", "desktop-id"])


class DependencyCommandTests(unittest.TestCase):
    @staticmethod
    def report(*missing: str) -> DependencyReport:
        items = []
        for key in ("adb", "scrcpy", "kdeconnect", "daemon", "x11", "qr"):
            items.append(
                CheckItem(
                    key=key,
                    label=key,
                    ok=key not in missing,
                    detail="",
                    severity="required" if key in {"adb", "scrcpy", "kdeconnect"} else "warning",
                )
            )
        return DependencyReport(
            adb_path=None,
            scrcpy_path=None,
            kdeconnect_path=None,
            qt_platform="xcb",
            x11_display=":0",
            daemon_registered=False,
            items=tuple(items),
        )

    def test_install_commands_are_targeted(self) -> None:
        text = installation_text(self.report("adb"))
        self.assertEqual(text, "sudo apt install adb")
        text = installation_text(self.report("kdeconnect"))
        self.assertEqual(text, "sudo apt install kdeconnect")
        text = installation_text(self.report("adb", "scrcpy", "kdeconnect"))
        self.assertIn("sudo apt install adb kdeconnect curl", text)
        self.assertIn("Genymobile/scrcpy", text)

    def test_no_commands_when_ready(self) -> None:
        self.assertEqual(installation_text(self.report()), "")

    def test_daemon_warning_includes_start_hint(self) -> None:
        text = installation_text(self.report("daemon"))
        self.assertIn("kdeconnectd", text)

    def test_qr_command_is_targeted(self) -> None:
        text = installation_text(self.report("qr"))
        self.assertEqual(text, "sudo apt install python3-segno")


class FailureMessageTests(unittest.TestCase):
    def test_unauthorized(self) -> None:
        self.assertIn("authorization", _friendly_failure("adb: error: device unauthorized"))
        self.assertIn("No ADB device", _friendly_failure("ERROR: Could not find ADB device"))

    def test_push_chatter_is_not_an_error(self) -> None:
        message = _friendly_failure(
            "/home/me/scrcpy-server: 1 file pushed, 0 skipped. 500 MB/s\n"
            "[server] INFO: Device: [samsung] SM-S731U1\n"
        )
        self.assertEqual(message, "")


class NotificationHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        from PySide6.QtCore import QCoreApplication

        self._app = QCoreApplication.instance() or QCoreApplication([])
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._path = Path(self._temp.name) / "history.json"
        self.client = KdeConnectClient(history_path=self._path)
        self.client._history_limit = 3

    @staticmethod
    def _item(identifier: str, minute: int) -> NotificationItem:
        return NotificationItem(
            id=identifier,
            app_name="Mail",
            title=f"Message {identifier}",
            text="",
            received_at=datetime(2026, 1, 1, 12, minute),
        )

    def test_history_is_newest_first(self) -> None:
        self.client._history = {
            "a": self._item("a", 1),
            "b": self._item("b", 5),
            "c": self._item("c", 3),
        }
        self.assertEqual(
            [item.id for item in self.client._history_items()], ["b", "c", "a"]
        )

    def test_history_is_bounded(self) -> None:
        self.client._history = {
            str(index): self._item(str(index), index) for index in range(5)
        }
        self.client._trim_history()
        self.assertEqual(sorted(self.client._history), ["2", "3", "4"])

    def test_history_is_saved_and_reloaded(self) -> None:
        self.client._history = {
            "a": self._item("a", 1),
            "b": self._item("b", 5),
        }
        self.client._save_history()

        other = KdeConnectClient(history_path=self._path)
        self.assertEqual([item.id for item in other._history_items()], ["b", "a"])
        self.assertEqual(other._history["b"].title, "Message b")

    def test_clear_history_removes_saved_file(self) -> None:
        self.client._history = {"a": self._item("a", 1)}
        self.client._save_history()
        self.assertTrue(self._path.exists())

        self.client.clear_history()
        self.assertEqual(self.client._history, {})
        self.assertFalse(self._path.exists())


class NotificationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "nested" / "history.json"

    @staticmethod
    def _item(identifier: str, minute: int, **overrides) -> NotificationItem:
        values = {
            "id": identifier,
            "app_name": "Mail",
            "title": f"Message {identifier}",
            "text": f"Body {identifier}",
            "ticker": "",
            "received_at": datetime(2026, 1, 1, 12, minute),
        }
        values.update(overrides)
        return NotificationItem(**values)

    def test_round_trip_preserves_fields(self) -> None:
        item = self._item(
            "a",
            1,
            ticker="Ticker",
            icon_path="/tmp/icon.png",
            dismissable=True,
            reply_id="reply-1",
        )
        self.assertTrue(save_history([item], path=self.path))
        loaded = load_history(path=self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0], item)

    def test_saved_file_is_owner_only(self) -> None:
        self.assertTrue(save_history([self._item("a", 1)], path=self.path))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_limit_keeps_newest(self) -> None:
        items = [self._item(str(index), index) for index in range(4)]
        self.assertTrue(save_history(items, limit=2, path=self.path))
        self.assertEqual([item.id for item in load_history(path=self.path)], ["3", "2"])

    def test_long_fields_are_truncated(self) -> None:
        item = self._item("a", 1, text="x" * (MAX_FIELD_CHARS + 500))
        save_history([item], path=self.path)
        self.assertEqual(len(load_history(path=self.path)[0].text), MAX_FIELD_CHARS)

    def test_bad_json_is_ignored(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_history(path=self.path), [])

    def test_invalid_records_are_skipped(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            '{"version":1,"items":['
            '{"id":"good","app_name":"Mail","title":"Hi",'
            '"text":"","received_at":"2026-01-01T12:00:00"},'
            '{"id":"bad-time","app_name":"Mail","title":"Hi",'
            '"text":"","received_at":"yesterday"},'
            '{"id":"","app_name":"Mail"},'
            '"not an object"'
            "]}",
            encoding="utf-8",
        )
        loaded = load_history(path=self.path)
        self.assertEqual([item.id for item in loaded], ["good"])

    def test_delete_is_quiet_when_missing(self) -> None:
        delete_history(path=self.path)
        self.assertFalse(self.path.exists())


class NotificationSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = NotificationItem(
            id="1",
            app_name="Messages",
            title="Sam",
            text="Dinner at 7?",
        )

    def test_matches_app_heading_and_body(self) -> None:
        self.assertTrue(notification_matches(self.item, "messages"))
        self.assertTrue(notification_matches(self.item, "DINNER"))
        self.assertTrue(notification_matches(self.item, "sam"))

    def test_empty_query_matches_everything(self) -> None:
        self.assertTrue(notification_matches(self.item, ""))
        self.assertTrue(notification_matches(self.item, "   "))

    def test_non_match(self) -> None:
        self.assertFalse(notification_matches(self.item, "battery"))


class WifiDiscoveryTests(unittest.TestCase):
    MDNS_OUTPUT = """List of discovered mdns services
adb-MOCK123-a._adb-tls-pairing._tcp.    192.168.1.50:37123
adb-MOCK123-a._adb-tls-connect._tcp.    192.168.1.50:37777
printer._ipp._tcp.                      192.168.1.60:631
not an address
"""

    def test_parse_mdns_services(self) -> None:
        services = parse_mdns_services(self.MDNS_OUTPUT)
        self.assertEqual([service.kind for service in services], ["pairing", "connect"])
        self.assertEqual(services[0].address, "192.168.1.50:37123")
        self.assertEqual(services[1].address, "192.168.1.50:37777")


class MdnsDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _name(name: str) -> bytes:
        out = b""
        for label in name.split("."):
            out += bytes([len(label)]) + label.encode()
        return out + b"\x00"

    @classmethod
    def _rr(cls, name: str, rtype: int, rdata: bytes) -> bytes:
        return (
            cls._name(name)
            + struct.pack("!HHIH", rtype, 1, 120, len(rdata))
            + rdata
        )

    @classmethod
    def _packet(cls, records: list[bytes]) -> bytes:
        return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)

    def test_parses_pairing_and_connect_services(self) -> None:
        connect = "adb-MOCK-a._adb-tls-connect._tcp.local"
        pairing = "ADB_WIFI_test._adb-tls-pairing._tcp.local"
        target = "Android_MOCK.local"
        packet = self._packet(
            [
                self._rr("_adb-tls-connect._tcp.local", 12, self._name(connect)),
                self._rr("_adb-tls-pairing._tcp.local", 12, self._name(pairing)),
                self._rr(connect, 33, struct.pack("!HHH", 0, 0, 45113) + self._name(target)),
                self._rr(pairing, 33, struct.pack("!HHH", 0, 0, 37123) + self._name(target)),
                self._rr(target, 1, bytes([192, 168, 1, 50])),
            ]
        )
        services = parse_mdns_response(packet, source="192.168.1.50")
        self.assertEqual(
            [(service.kind, service.address) for service in services],
            [("connect", "192.168.1.50:45113"), ("pairing", "192.168.1.50:37123")],
        )

    def test_falls_back_to_packet_source_without_address_record(self) -> None:
        pairing = "ADB_WIFI_test._adb-tls-pairing._tcp.local"
        packet = self._packet(
            [
                self._rr("_adb-tls-pairing._tcp.local", 12, self._name(pairing)),
                self._rr(
                    pairing,
                    33,
                    struct.pack("!HHH", 0, 0, 37123) + self._name("Android_MOCK.local"),
                ),
            ]
        )
        services = parse_mdns_response(packet, source="10.0.0.7")
        self.assertEqual(
            [(service.kind, service.address) for service in services],
            [("pairing", "10.0.0.7:37123")],
        )

    def test_malformed_packet_is_ignored(self) -> None:
        self.assertEqual(parse_mdns_response(b"\x00\x01", source="10.0.0.7"), [])

    def test_qr_payload_format(self) -> None:
        service_name, password, payload = make_qr_pairing()
        match = re.fullmatch(
            r"WIFI:T:ADB;S:([A-Za-z0-9_]+);P:([A-Za-z0-9]+);;", payload
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), service_name)
        self.assertEqual(match.group(2), password)
        self.assertTrue(service_name.startswith("ADB_WIFI_"))
        self.assertGreaterEqual(len(password), 6)


class ClipboardSyncGateTests(unittest.TestCase):
    def test_skips_empty_and_oversized(self) -> None:
        gate = ClipboardSyncGate(max_length=10)
        self.assertFalse(gate.should_send("", 0))
        self.assertFalse(gate.should_send("x" * 11, 0))
        self.assertTrue(gate.should_send("x" * 10, 0))

    def test_recent_send_is_suppressed(self) -> None:
        gate = ClipboardSyncGate(echo_window=5)
        self.assertTrue(gate.should_send("hello", 100))
        gate.note_sent("hello", 100)
        self.assertFalse(gate.should_send("hello", 100.5))
        self.assertFalse(gate.should_send("hello", 104.9))

    def test_send_is_allowed_after_echo_window(self) -> None:
        gate = ClipboardSyncGate(echo_window=5)
        gate.note_sent("hello", 100)
        self.assertTrue(gate.should_send("hello", 106))

    def test_different_text_is_sent(self) -> None:
        gate = ClipboardSyncGate()
        gate.note_sent("hello", 100)
        self.assertTrue(gate.should_send("world", 101))


class ScrcpyCommandTests(unittest.TestCase):
    def build(self, **overrides):
        options = {
            "serial": "R58M90ABCDE",
            "marker": "PhoneLinkMirror-abcd1234",
            "width": 600,
            "height": 900,
            "dex": False,
            "floating": False,
            "audio": False,
        }
        options.update(overrides)
        return build_scrcpy_args(**options)

    def test_embedded_phone_mirror_is_borderless(self) -> None:
        args = self.build()
        self.assertIn("--window-borderless", args)
        self.assertIn("--no-audio", args)
        self.assertNotIn(f"--new-display={DEX_DISPLAY}", args)
        self.assertNotIn("--mouse-bind=++++:bhsn", args)
        self.assertEqual(args[args.index("--window-title") + 1], "PhoneLinkMirror-abcd1234")

    def test_embedded_dex_creates_a_virtual_display(self) -> None:
        args = self.build(dex=True)
        self.assertIn(f"--new-display={DEX_DISPLAY}", args)
        self.assertIn("--window-borderless", args)
        # A DeX desktop needs real secondary clicks for context menus.
        self.assertIn("--mouse-bind=++++:bhsn", args)

    def test_floating_dex_keeps_window_decorations(self) -> None:
        args = self.build(dex=True, floating=True, audio=False)
        self.assertIn(f"--new-display={DEX_DISPLAY}", args)
        self.assertNotIn("--window-borderless", args)
        self.assertIn("--no-audio", args)
        self.assertIn("--mouse-bind=++++:bhsn", args)
        title = args[args.index("--window-title") + 1]
        self.assertIn("DeX desktop", title)
        # scrcpy publishes WM_NAME as a Latin-1 string, so a non-ASCII dash
        # would show up as "â€”" in the title bar.
        self.assertTrue(title.isascii())

    def test_audio_only_when_requested(self) -> None:
        args = self.build(audio=True)
        self.assertNotIn("--no-audio", args)
        # Both transports are lossless, so scrcpy's low-latency default stands.
        self.assertNotIn("--audio-buffer", args)

    def test_floating_dex_can_carry_audio(self) -> None:
        args = self.build(dex=True, floating=True, audio=True)
        self.assertNotIn("--no-audio", args)

    def test_wireless_audio_uses_the_default_buffer(self) -> None:
        args = self.build(serial="192.168.1.50:5555", audio=True)
        self.assertNotIn("--no-audio", args)
        self.assertNotIn("--audio-buffer", args)

    def test_default_quality_adds_no_video_flags(self) -> None:
        args = self.build(quality=MirrorQuality())
        for flag in ("--video-bit-rate=", "--max-size=", "--max-fps="):
            self.assertFalse(any(arg.startswith(flag) for arg in args))
        self.assertNotIn("--turn-screen-off", args)

    def test_quality_flags_are_passed_through(self) -> None:
        quality = MirrorQuality(
            bit_rate=16_000_000,
            max_size=720,
            max_fps=30,
            turn_screen_off=True,
        )
        args = self.build(quality=quality)
        self.assertIn("--video-bit-rate=16000000", args)
        self.assertIn("--max-size=720", args)
        self.assertIn("--max-fps=30", args)
        self.assertIn("--turn-screen-off", args)

    def test_single_quality_setting_leaves_the_others_at_defaults(self) -> None:
        args = self.build(quality=MirrorQuality(max_fps=24))
        self.assertIn("--max-fps=24", args)
        self.assertFalse(any(arg.startswith("--max-size") for arg in args))
        self.assertFalse(any(arg.startswith("--video-bit-rate") for arg in args))
        self.assertNotIn("--turn-screen-off", args)


class DeXAudioTests(unittest.TestCase):
    def test_in_app_mirror_keeps_audio_while_dex_is_embedded(self) -> None:
        self.assertTrue(in_app_mirror_audio(True, False))

    def test_floating_dex_owns_the_audio(self) -> None:
        self.assertFalse(in_app_mirror_audio(True, True))

    def test_speaker_off_means_no_audio_anywhere(self) -> None:
        self.assertFalse(in_app_mirror_audio(False, False))
        self.assertFalse(in_app_mirror_audio(False, True))


if __name__ == "__main__":
    unittest.main()
