"""Minimal mDNS discovery for Android wireless debugging.

``adb mdns services`` is the obvious way to find the phone, but its embedded
mDNS client can silently return nothing on some Linux hosts even when the
phone is advertising correctly.  Phone Link therefore also sends the standard
``_adb-tls-pairing._tcp`` and ``_adb-tls-connect._tcp`` PTR queries itself and
reads the SRV/A answers.  Nothing about the ADB pairing protocol is
reimplemented here; these are just DNS records on the local network.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

PAIRING_TYPE = "_adb-tls-pairing._tcp.local"
CONNECT_TYPE = "_adb-tls-connect._tcp.local"

_MDNS_ADDRESS = "224.0.0.251"
_MDNS_PORT = 5353
_MAX_PACKET = 9000

_TYPE_A = 1
_TYPE_PTR = 12
_TYPE_AAAA = 28
_TYPE_SRV = 33
_CLASS_IN = 1


@dataclass(frozen=True)
class WifiService:
    name: str
    kind: str  # "pairing" or "connect"
    address: str


@dataclass(frozen=True)
class _Record:
    name: str
    kind: str
    target: str = ""
    port: int = 0
    address: str = ""
    source: str = ""


def parse_mdns_response(data: bytes, source: str = "") -> list[WifiService]:
    """Parse one mDNS response packet into pairing/connect services."""

    try:
        return _resolve(_parse_packet(data, source))
    except (ValueError, struct.error, UnicodeDecodeError):
        return []


def query_adb_services(timeout: float = 3.0) -> list[WifiService]:
    """Ask the local network for Android wireless-debugging services.

    Returns an empty list if port 5353 cannot be shared, if multicast is
    blocked, or if no phone answers.  Never raises for network failures.
    """

    try:
        sock = _open_socket()
    except OSError:
        return []

    records: list[_Record] = []
    try:
        deadline = time.monotonic() + max(0.5, timeout)
        for service in (CONNECT_TYPE, PAIRING_TYPE):
            _safe_query(sock, service)
        next_query = time.monotonic() + 1.0

        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if now >= next_query:
                for service in (CONNECT_TYPE, PAIRING_TYPE):
                    _safe_query(sock, service)
                next_query = now + 1.0
            sock.settimeout(max(0.01, min(0.25, deadline - now)))
            try:
                data, peer = sock.recvfrom(_MAX_PACKET)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                records.extend(_parse_packet(data, peer[0]))
            except (ValueError, struct.error, UnicodeDecodeError):
                continue
    finally:
        sock.close()

    return _resolve(records)


def _open_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", _MDNS_PORT))
        membership = struct.pack(
            "4s4s",
            socket.inet_aton(_MDNS_ADDRESS),
            socket.inet_aton("0.0.0.0"),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        return sock
    except OSError:
        sock.close()
        raise


def _safe_query(sock: socket.socket, service: str) -> None:
    try:
        sock.sendto(_query_packet(service), (_MDNS_ADDRESS, _MDNS_PORT))
    except OSError:
        pass


def _query_packet(service: str) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    question = _encode_name(service) + struct.pack("!HH", _TYPE_PTR, _CLASS_IN)
    return header + question


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.strip(".").split("."):
        encoded = label.encode("utf-8")
        if not encoded or len(encoded) > 63:
            raise ValueError("invalid DNS label")
        out.append(len(encoded))
        out.extend(encoded)
    out.append(0)
    return bytes(out)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    end: int | None = None
    hops = 0
    total = 0

    while True:
        if offset >= len(data):
            raise ValueError("truncated DNS name")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated DNS pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if pointer >= len(data):
                raise ValueError("DNS pointer out of range")
            if end is None:
                end = offset + 2
            offset = pointer
            hops += 1
            if hops > 16:
                raise ValueError("DNS pointer loop")
            continue
        if length & 0xC0:
            raise ValueError("invalid DNS label")
        if offset + 1 + length > len(data):
            raise ValueError("truncated DNS label")
        label = data[offset + 1 : offset + 1 + length].decode("utf-8", "replace")
        labels.append(label)
        total += len(label) + 1
        if total > 255:
            raise ValueError("DNS name too long")
        offset += 1 + length

    return ".".join(labels).strip(".").lower(), (end if end is not None else offset)


def _parse_packet(data: bytes, source: str) -> list[_Record]:
    if len(data) < 12:
        raise ValueError("short DNS packet")
    _, flags, questions, answers, authorities, additional = struct.unpack(
        "!HHHHHH", data[:12]
    )
    if not flags & 0x8000:  # a query, including our own multicast echo
        return []

    offset = 12
    for _ in range(questions):
        _, offset = _read_name(data, offset)
        if offset + 4 > len(data):
            raise ValueError("truncated DNS question")
        offset += 4

    total = answers + authorities + additional
    if total > 512:
        raise ValueError("too many DNS records")
    records: list[_Record] = []
    for _ in range(total):
        name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("truncated DNS record")
        rtype, _rclass, _ttl, length = struct.unpack(
            "!HHIH", data[offset : offset + 10]
        )
        offset += 10
        start = offset
        end = start + length
        if end > len(data):
            raise ValueError("DNS record data out of range")

        if rtype == _TYPE_PTR:
            target, _ = _read_name(data, start)
            records.append(_Record(name=name, kind="ptr", target=target, source=source))
        elif rtype == _TYPE_SRV and length >= 7:
            port = struct.unpack("!H", data[start + 4 : start + 6])[0]
            target, _ = _read_name(data, start + 6)
            records.append(
                _Record(name=name, kind="srv", target=target, port=port, source=source)
            )
        elif rtype == _TYPE_A and length == 4:
            records.append(
                _Record(
                    name=name,
                    kind="a",
                    address=socket.inet_ntoa(data[start:end]),
                    source=source,
                )
            )
        elif rtype == _TYPE_AAAA and length == 16:
            records.append(
                _Record(
                    name=name,
                    kind="aaaa",
                    address=socket.inet_ntop(socket.AF_INET6, data[start:end]),
                    source=source,
                )
            )
        offset = end

    return records


def _resolve(records: list[_Record]) -> list[WifiService]:
    kinds: dict[str, str] = {}
    for record in records:
        if record.kind != "ptr":
            continue
        if record.name == CONNECT_TYPE:
            kinds[record.target] = "connect"
        elif record.name == PAIRING_TYPE:
            kinds[record.target] = "pairing"

    servers = {record.name: record for record in records if record.kind == "srv"}
    ipv4 = {record.name: record.address for record in records if record.kind == "a"}
    ipv6 = {record.name: record.address for record in records if record.kind == "aaaa"}

    services: list[WifiService] = []
    seen: set[tuple[str, str]] = set()
    for instance, kind in kinds.items():
        server = servers.get(instance)
        if server is None or not server.port:
            continue
        # Prefer the advertised IPv4 address, then the packet's IPv4 source
        # (Android sometimes splits SRV and A records across packets), then a
        # global IPv6 address.  A raw link-local IPv6 address would need a
        # zone index and is not useful to `adb connect`.
        host = ipv4.get(server.target) or server.source or ipv6.get(server.target)
        if not host:
            continue
        if ":" in host:
            host = f"[{host}]"
        address = f"{host}:{server.port}"
        key = (kind, address)
        if key in seen:
            continue
        seen.add(key)
        services.append(WifiService(name=instance, kind=kind, address=address))

    services.sort(key=lambda service: (service.kind, service.address))
    return services
