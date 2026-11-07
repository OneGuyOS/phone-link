"""Automatic reconnection after a wireless ADB transport drops."""

from __future__ import annotations

import subprocess
import unittest

from phone_link.mdns import WifiService
from phone_link.wifi import WirelessReconnector, pick_reconnect_address


def service(address: str, kind: str = "connect", name: str = "") -> WifiService:
    return WifiService(name=name or f"adb-phone-{address}", kind=kind, address=address)


class PickAddressTests(unittest.TestCase):
    def test_exact_address_wins(self) -> None:
        services = [service("192.168.1.136:38033"), service("192.168.1.136:44444")]
        self.assertEqual(
            pick_reconnect_address(services, "192.168.1.136:44444"),
            "192.168.1.136:44444",
        )

    def test_new_port_on_the_same_host_is_accepted(self) -> None:
        services = [service("192.168.1.136:38033")]
        self.assertEqual(
            pick_reconnect_address(services, "192.168.1.136:41000"),
            "192.168.1.136:38033",
        )

    def test_other_hosts_are_ignored(self) -> None:
        services = [service("192.168.1.200:38033")]
        self.assertIsNone(
            pick_reconnect_address(services, "192.168.1.136:41000")
        )

    def test_pairing_services_are_ignored(self) -> None:
        services = [service("192.168.1.136:38033", kind="pairing")]
        self.assertIsNone(
            pick_reconnect_address(services, "192.168.1.136:41000")
        )

    def test_without_a_previous_address_there_is_nothing_to_do(self) -> None:
        services = [service("192.168.1.136:38033")]
        self.assertIsNone(pick_reconnect_address(services, ""))

    def test_ipv6_hosts_compare_without_the_brackets_or_port(self) -> None:
        services = [service("[fe80::1234]:38033")]
        self.assertEqual(
            pick_reconnect_address(services, "[fe80::1234]:41000"),
            "[fe80::1234]:38033",
        )


class ReconnectorTests(unittest.TestCase):
    def test_attempt_connects_to_the_current_port(self) -> None:
        calls: list[list[str]] = []

        def runner(adb_path, args, *, timeout):
            calls.append([adb_path, *args])
            return subprocess.CompletedProcess(
                [adb_path, *args],
                0,
                "connected to 192.168.1.136:38033\n",
                "",
            )

        reconnector = WirelessReconnector(
            "adb",
            query=lambda timeout=3.0: [service("192.168.1.136:38033")],
            runner=runner,
        )
        reconnector.last_address = "192.168.1.136:41000"
        ok, detail = reconnector.attempt()
        self.assertTrue(ok, detail)
        self.assertEqual(detail, "192.168.1.136:38033")
        self.assertEqual(calls, [["adb", "connect", "192.168.1.136:38033"]])

    def test_attempt_reports_when_the_phone_is_not_advertising(self) -> None:
        reconnector = WirelessReconnector(
            "adb",
            query=lambda timeout=3.0: [],
            runner=lambda *args, **kwargs: None,
        )
        reconnector.last_address = "192.168.1.136:41000"
        ok, detail = reconnector.attempt()
        self.assertFalse(ok)
        self.assertIn("not found", detail)

    def test_attempt_reports_a_refused_connection(self) -> None:
        reconnector = WirelessReconnector(
            "adb",
            query=lambda timeout=3.0: [service("192.168.1.136:38033")],
            runner=lambda adb, args, *, timeout: subprocess.CompletedProcess(
                [adb, *args],
                1,
                "failed to connect to '192.168.1.136:38033': Connection refused\n",
                "",
            ),
        )
        reconnector.last_address = "192.168.1.136:41000"
        ok, detail = reconnector.attempt()
        self.assertFalse(ok)
        self.assertIn("Could not reconnect", detail)

    def test_attempt_without_an_address_is_a_no_op(self) -> None:
        reconnector = WirelessReconnector("adb")
        ok, detail = reconnector.attempt()
        self.assertFalse(ok)
        self.assertIn("No Wi-Fi phone", detail)

    def test_reconnect_is_rate_limited(self) -> None:
        now = [100.0]
        starts: list[bool] = []
        reconnector = WirelessReconnector(
            "adb",
            clock=lambda: now[0],
            start=lambda: starts.append(True),
        )
        reconnector.last_address = "192.168.1.136:41000"

        self.assertTrue(reconnector.reconnect())
        self.assertEqual(starts, [True])
        # The worker is still "running"; a second poll must not stack attempts.
        self.assertFalse(reconnector.reconnect())

        reconnector._busy = False
        self.assertFalse(reconnector.reconnect())  # still in the cooldown window
        now[0] += reconnector.cooldown + 1
        self.assertTrue(reconnector.reconnect())
        self.assertEqual(starts, [True, True])


if __name__ == "__main__":
    unittest.main()
