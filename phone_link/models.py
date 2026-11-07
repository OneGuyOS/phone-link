"""Small data objects shared by the ADB, KDE Connect and UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def is_wireless(self) -> bool:
        lowered = self.serial.lower()
        return ":" in self.serial or "_tcp" in lowered or "tls-connect" in lowered

    @property
    def connection_label(self) -> str:
        return "Wi-Fi" if self.is_wireless else "USB"

    @property
    def display_name(self) -> str:
        if self.model:
            return self.model.replace("_", " ").strip()
        return self.serial


@dataclass(frozen=True)
class PhoneSnapshot:
    """What ADB currently says about the phone.

    ``selected`` is the device scrcpy will use, if any.  ``error`` is suitable
    for showing directly to a person, rather than being a raw exception.
    """

    devices: tuple[AdbDevice, ...] = ()
    selected: AdbDevice | None = None
    name: str | None = None
    battery: int | None = None
    charging: bool = False
    # Android display rotation: 0 = portrait, 1 = 90°, 2 = upside-down
    # portrait, 3 = 270°.  None means the phone did not report it.
    rotation: int | None = None
    error: str | None = None

    @property
    def connected(self) -> bool:
        return self.selected is not None

    @property
    def connection_label(self) -> str:
        if self.selected:
            return self.selected.connection_label
        return "Offline"


@dataclass(frozen=True)
class KdeDevice:
    id: str
    name: str
    kind: str = "phone"
    reachable: bool = False
    paired: bool = False

    @property
    def is_phone(self) -> bool:
        return self.kind.lower() in {"phone", "tablet"}


@dataclass(frozen=True)
class NotificationItem:
    id: str
    app_name: str
    title: str
    text: str
    ticker: str = ""
    icon_path: str = ""
    dismissable: bool = False
    reply_id: str = ""
    received_at: datetime = field(default_factory=datetime.now)

    @property
    def heading(self) -> str:
        return self.title.strip() or self.ticker.strip() or self.app_name.strip() or "Notification"

    @property
    def body(self) -> str:
        body = self.text.strip()
        if not body or body == self.heading:
            ticker = self.ticker.strip()
            return "" if ticker == self.heading else ticker
        return body


@dataclass(frozen=True)
class MediaState:
    """What the phone's media player is doing, via KDE Connect's MPRIS plugin."""

    available: bool = False
    player: str = ""
    players: tuple[str, ...] = ()
    title: str = ""
    artist: str = ""
    album: str = ""
    is_playing: bool = False
    # Milliseconds.  ``length`` is 0 when the player does not know it.
    position: int = 0
    length: int = 0
    can_seek: bool = False


@dataclass(frozen=True)
class PhoneSink:
    """One audio output on the phone, via KDE Connect's system volume plugin."""

    name: str
    description: str = ""
    max_volume: int = 100
    volume: int = 0
    muted: bool = False

    @property
    def label(self) -> str:
        return self.description.strip() or self.name


@dataclass(frozen=True)
class KdeSnapshot:
    """The KDE Connect side of the window.

    ``mode`` drives the notification panel's empty state:
    ``offline``, ``no-device``, ``unreachable``, ``no-notifications`` or
    ``ready``.
    """

    available: bool = False
    device: KdeDevice | None = None
    battery: int | None = None
    charging: bool = False
    notifications: tuple[NotificationItem, ...] = ()
    # Notifications that were active and then vanished from the phone (read
    # elsewhere, auto-cancelled, replaced by a group).  Saved locally between
    # runs, newest first.
    history: tuple[NotificationItem, ...] = ()
    mode: str = "offline"
    message: str = ""
    error: str | None = None

    @property
    def connected(self) -> bool:
        return self.device is not None and self.device.reachable and self.device.paired
