"""Wireless debugging helpers built on adb's own pair/connect commands.

Android 11+ exposes a pairing port and a separate connection port.  Phone Link
never implements the pairing protocol; it just runs adb the way a person would.
Pairing is QR-only: the desktop shows a one-time code, the phone scans it and
advertises a matching mDNS service, and ``adb pair`` connects to that service.
"""

from __future__ import annotations

import re
import secrets
import string
import subprocess
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from .mdns import WifiService, query_adb_services

PAIRING_SERVICE = "_adb-tls-pairing._tcp"
CONNECT_SERVICE = "_adb-tls-connect._tcp"

_ADDRESS_RE = re.compile(r"^(?:\[[0-9A-Fa-f:.]+\]|[0-9A-Za-z._-]+):\d{1,5}$")
_QR_CHARS = string.ascii_letters + string.digits

__all__ = [
    "CONNECT_SERVICE",
    "PAIRING_SERVICE",
    "WifiService",
    "WirelessReconnector",
    "find_connect_address",
    "make_qr_pairing",
    "pair_and_connect",
    "pair_with_qr",
    "parse_mdns_services",
    "pick_reconnect_address",
]


def parse_mdns_services(output: str) -> list[WifiService]:
    """Parse ``adb mdns services`` output into pairing/connect addresses."""

    services: list[WifiService] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of discovered"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        name = fields[0]
        address = fields[-1]
        if not _ADDRESS_RE.match(address):
            continue
        if PAIRING_SERVICE in name:
            kind = "pairing"
        elif CONNECT_SERVICE in name:
            kind = "connect"
        else:
            continue
        services.append(WifiService(name=name, kind=kind, address=address))
    return services


def find_connect_address(adb_path: str, timeout: float = 3.0) -> str | None:
    """Return the phone's ``_adb-tls-connect`` address, or ``None``."""

    services = query_adb_services(timeout=timeout)
    connect = [service for service in services if service.kind == "connect"]
    if not connect:
        result = _run(adb_path, ["mdns", "services"], timeout=timeout)
        if result is not None and result.returncode == 0:
            connect = [
                service
                for service in parse_mdns_services(result.stdout)
                if service.kind == "connect"
            ]
    return connect[-1].address if connect else None


def _address_host(address: str) -> str:
    if address.startswith("["):
        close = address.find("]")
        if close != -1:
            return address[1:close]
    return address.rsplit(":", 1)[0]


def pick_reconnect_address(
    services: list[WifiService], last_address: str
) -> str | None:
    """Pick the previously used phone from the current mDNS advertisements.

    Wireless debugging uses a new port each time it restarts, so an exact
    match is preferred but a different port on the same host is accepted.
    Other hosts are ignored: the app must not try to debug a stranger's phone.
    """

    if not last_address:
        return None
    connects = [service for service in services if service.kind == "connect"]
    for service in connects:
        if service.address == last_address:
            return service.address
    host = _address_host(last_address)
    if not host:
        return None
    for service in connects:
        if _address_host(service.address) == host:
            return service.address
    return None


class WirelessReconnector(QObject):
    """Reconnect to the phone after its wireless ADB transport drops.

    Wireless debugging stays enabled on the phone, but an ADB session can end
    when USB is unplugged or the phone's Wi-Fi re-associates.  This finds the
    phone's current port through mDNS and runs ``adb connect`` again, without
    requiring the user to re-scan the pairing QR code.
    """

    connected = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str | None,
        parent: QObject | None = None,
        *,
        query: Callable[..., list[WifiService]] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str] | None] | None = None,
        clock: Callable[[], float] | None = None,
        start: Callable[[], None] | None = None,
        cooldown: float = 20.0,
        timeout: float = 3.0,
    ) -> None:
        super().__init__(parent)
        self.adb_path = adb_path or ""
        self.last_address = ""
        self._query = query or query_adb_services
        self._runner = runner or _run
        self._clock = clock or time.monotonic
        self._start = start or self._start_thread
        self.cooldown = cooldown
        self.timeout = timeout
        self._last_attempt = float("-inf")
        self._busy = False

    def reconnect(self) -> bool:
        """Start one attempt if none is running and the cooldown has passed."""

        if not self.adb_path or not self.last_address or self._busy:
            return False
        now = self._clock()
        if now - self._last_attempt < self.cooldown:
            return False
        self._last_attempt = now
        self._busy = True
        try:
            self._start()
        except Exception:
            self._busy = False
            raise
        return True

    def attempt(self) -> tuple[bool, str]:
        """Run one synchronous attempt; returns ``(ok, address or error)``."""

        if not self.adb_path:
            return False, "adb is not available."
        if not self.last_address:
            return False, "No Wi-Fi phone has been used yet."
        services = self._query(timeout=self.timeout)
        address = pick_reconnect_address(services, self.last_address)
        if address is None:
            return False, "The phone was not found on the network."
        result = self._runner(self.adb_path, ["connect", address], timeout=20.0)
        if result is None:
            return False, "Could not run adb to reconnect."
        text = f"{result.stdout}\n{result.stderr}"
        lowered = text.lower()
        failed = any(
            word in lowered
            for word in ("failed", "cannot", "unable", "refused", "offline")
        )
        if result.returncode != 0 or "connected to" not in lowered or failed:
            detail = _clean_output(text) or "adb connect failed."
            return False, f"Could not reconnect to {address}: {detail}"
        return True, address

    def _start_thread(self) -> None:
        threading.Thread(
            target=self._worker,
            name="phone-link-wifi-reconnect",
            daemon=True,
        ).start()

    def _worker(self) -> None:
        try:
            ok, detail = self.attempt()
        except Exception as exc:  # a background thread must not kill the app
            ok, detail = False, f"Wi-Fi reconnect failed: {exc}"
        self._busy = False
        if ok:
            self.last_address = detail
            self._emit(self.connected, detail)
        else:
            self._emit(self.failed, detail)

    def _emit(self, signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            # The QObject can be gone while the worker is ending.
            pass


def make_qr_pairing() -> tuple[str, str, str]:
    """Create the one-time service name, password and QR payload.

    Android's QR pairing format asks the phone to advertise a pairing service
    under ``S`` and use ``P`` as the pairing secret.  The desktop then runs
    ``adb pair`` against that advertisement.
    """

    service_name = "ADB_WIFI_" + "".join(secrets.choice(_QR_CHARS) for _ in range(8))
    password = "".join(secrets.choice(_QR_CHARS) for _ in range(8))
    payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"
    return service_name, password, payload


def pair_and_connect(
    adb_path: str, pairing_address: str, code: str
) -> tuple[bool, str]:
    """Pair and connect, discovering the connection port over mDNS."""

    pair_result = _run(adb_path, ["pair", pairing_address, code], timeout=30.0)
    if pair_result is None:
        return False, "Could not run adb to pair with the phone."
    pair_text = pair_result.stdout + "\n" + pair_result.stderr
    lowered_pair = pair_text.lower()
    if "successfully paired" not in lowered_pair and "already paired" not in lowered_pair:
        detail = _clean_output(pair_text)
        return False, f"Pairing failed: {detail or 'the phone rejected the code.'}"

    connect_address = find_connect_address(adb_path) or ""
    if not connect_address:
        return False, (
            "Paired, but the phone's connection port was not found on the "
            "network. Open Wireless debugging and keep it enabled."
        )

    connect_result = _run(adb_path, ["connect", connect_address], timeout=20.0)
    if connect_result is None:
        return False, f"Paired, but could not run adb connect to {connect_address}."
    connect_text = connect_result.stdout + "\n" + connect_result.stderr
    lowered = connect_text.lower()
    failed = any(
        word in lowered for word in ("failed", "cannot", "unable", "refused", "offline")
    )
    if "connected to" not in lowered or failed:
        detail = _clean_output(connect_text)
        return False, (
            f"Paired, but connecting to {connect_address} failed: "
            f"{detail or 'check that Wireless debugging is still on.'}"
        )
    return True, f"Connected to {connect_address} over Wi-Fi."


def pair_with_qr(
    adb_path: str,
    service_name: str,
    password: str,
    *,
    timeout: float = 120.0,
    stop: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Wait for the phone to scan our QR code, then pair and connect.

    The phone advertises a ``_adb-tls-pairing._tcp`` instance named after the
    QR's ``S`` field once it scans the code.  We match that instance rather
    than pairing with any phone that happens to be advertising.
    """

    wanted = service_name.lower()
    deadline = time.monotonic() + timeout

    while True:
        if stop is not None and stop():
            return False, "QR pairing was cancelled."
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        services = query_adb_services(timeout=min(2.0, max(0.5, remaining)))
        match = next(
            (
                service
                for service in services
                if service.kind == "pairing"
                and service.name.split(".")[0] == wanted
            ),
            None,
        )
        if match is not None:
            if progress is not None:
                progress("Phone found. Pairing…")
            return pair_and_connect(adb_path, match.address, password)

        if stop is not None and stop():
            return False, "QR pairing was cancelled."
        time.sleep(0.15)

    return False, (
        "The QR code expired before the phone scanned it. Show a new code and try again."
    )


def _run(
    adb_path: str, args: list[str], *, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [adb_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _clean_output(text: str) -> str:
    keep: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue
        lowered = line.lower()
        if "daemon not running" in lowered or "daemon started successfully" in lowered:
            continue
        keep.append(line)
    return " — ".join(keep[-3:])[:400]
