"""Tiny asynchronous wrapper around the Android Debug Bridge.

Nothing here talks to the phone protocol itself; adb already does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

from .models import AdbDevice, PhoneSnapshot

_KEY_CODES = {
    "back": "4",
    "home": "3",
    "recents": "187",
}

# KDE Connect's Find My Phone plugin has no "stop" method on its D-Bus
# interface: the ring only ends on the phone, by its "Found it" action.  That
# action is also an exported broadcast receiver, so Phone Link can trigger the
# same stop over adb.  The extra is the *computer's* KDE Connect id, because
# the phone's plugin instance belongs to the paired computer.
FINDMYPHONE_STOP_COMPONENT = (
    "org.kde.kdeconnect_tp/"
    "org.kde.kdeconnect.plugins.findmyphone.FindMyPhoneReceiver"
)
FINDMYPHONE_STOP_ACTION = "org.kde.kdeconnect.plugins.findmyphone.foundIt"


def findmyphone_stop_argv(
    adb_path: str, serial: str, desktop_id: str
) -> list[str]:
    """The adb command that presses the phone's "Found it" button."""

    return [
        adb_path,
        "-s",
        serial,
        "shell",
        "am",
        "broadcast",
        "-n",
        FINDMYPHONE_STOP_COMPONENT,
        "-a",
        FINDMYPHONE_STOP_ACTION,
        "--es",
        "deviceId",
        desktop_id,
    ]


def parse_adb_devices(output: str) -> tuple[AdbDevice, ...]:
    """Parse the stable parts of ``adb devices -l`` output."""

    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*") or line.startswith("adb:") or line.startswith("error:"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[0], fields[1]
        attrs: dict[str, str] = {}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                attrs[key] = value
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                model=attrs.get("model"),
                product=attrs.get("product"),
                device=attrs.get("device"),
                transport_id=attrs.get("transport_id"),
            )
        )
    return tuple(devices)


def choose_device(devices: tuple[AdbDevice, ...], preferred_serial: str | None = None) -> AdbDevice | None:
    """Pick the device scrcpy should use.

    A preferred serial (``PHONE_LINK_SERIAL``) wins. Otherwise USB devices are
    tried before wireless ADB devices.
    """

    usable = [device for device in devices if device.usable]
    if preferred_serial:
        for device in usable:
            if device.serial == preferred_serial:
                return device
        return None
    if not usable:
        return None
    return sorted(usable, key=lambda device: (device.is_wireless, device.serial))[0]


def parse_rotation(output: str) -> int | None:
    """Return the phone's current display rotation from a rotation query.

    ``dumpsys input`` is the fastest source and reports the active viewport as
    ``orientation=0..3``.  Some Android builds do not expose that line, so the
    window-manager fallback uses ``mDisplayRotation=ROTATION_n``.
    """

    lines: list[str] = []
    collecting = False
    found = False
    for line in output.splitlines():
        if line.strip() == "__PL_ROTATION__":
            collecting = True
            found = True
            continue
        if collecting:
            lines.append(line)
    text = "\n".join(lines) if found else output

    match = re.search(r"\borientation\s*=\s*([0-3])\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"mDisplayRotation\s*=\s*ROTATION_([0-3])\b", text)
    if match:
        return int(match.group(1))
    return None


def _describe_adb_failure(devices: tuple[AdbDevice, ...], preferred_serial: str | None) -> str:
    if preferred_serial:
        return f"ADB device {preferred_serial!r} was not found."
    if not devices:
        return "No ADB device found. Connect the phone over USB and enable USB debugging."
    unauthorized = [d for d in devices if d.state == "unauthorized"]
    if unauthorized:
        return "Phone found, but ADB access is not authorized. Unlock it and accept the USB debugging prompt."
    offline = [d for d in devices if d.state == "offline"]
    if offline:
        return "The phone is offline in ADB. Reconnect the cable or run: adb kill-server && adb start-server"
    return "No usable ADB device. Check the USB cable and USB debugging settings."


class AdbMonitor(QObject):
    """Polls adb in the background so the UI thread never blocks on it."""

    snapshotChanged = Signal(object)
    actionError = Signal(str)
    ringStopped = Signal()

    def __init__(self, adb_path: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.adb_path = adb_path or shutil.which("adb")
        self.preferred_serial = os.environ.get("PHONE_LINK_SERIAL") or None
        self.last_snapshot = PhoneSnapshot(
            error="adb was not found. Install Android platform tools." if not self.adb_path else None
        )
        self._lock = threading.Lock()
        self._in_flight = False
        self._stopped = False

    def refresh(self) -> None:
        if self._stopped:
            return
        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True
        threading.Thread(target=self._worker, name="adb-monitor", daemon=True).start()

    def stop(self) -> None:
        self._stopped = True

    def send_key(self, serial: str, action: str) -> None:
        keycode = _KEY_CODES.get(action)
        if not keycode:
            self.actionError.emit(f"Unknown navigation action: {action}")
            return
        if not self.adb_path:
            self.actionError.emit("adb is not installed.")
            return
        threading.Thread(
            target=self._send_key_worker,
            args=(serial, keycode),
            name="adb-keyevent",
            daemon=True,
        ).start()

    def stop_find_my_phone(self, serial: str, desktop_id: str) -> None:
        """Press the phone's "Found it" action to end a Find My Phone ring."""

        if not self.adb_path:
            self.actionError.emit("adb is not installed.")
            return
        threading.Thread(
            target=self._stop_ring_worker,
            args=(serial, desktop_id),
            name="adb-stop-ring",
            daemon=True,
        ).start()

    def _stop_ring_worker(self, serial: str, desktop_id: str) -> None:
        try:
            proc = subprocess.run(
                findmyphone_stop_argv(self.adb_path or "adb", serial, desktop_id),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._safe_emit(self.actionError, f"Could not stop the ring: {exc}")
            return
        output = f"{proc.stdout or ''}{proc.stderr or ''}"
        if proc.returncode != 0 or "Broadcast completed" not in output:
            detail = output.strip() or f"adb exited with code {proc.returncode}"
            self._safe_emit(
                self.actionError, f"The phone did not stop ringing: {detail}"
            )
            return
        self._safe_emit(self.ringStopped)

    def _send_key_worker(self, serial: str, keycode: str) -> None:
        try:
            proc = subprocess.run(
                [self.adb_path, "-s", serial, "shell", "input", "keyevent", keycode],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._safe_emit(self.actionError, f"Could not send the key to the phone: {exc}")
            return
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip() or f"adb exited with code {proc.returncode}"
            self._safe_emit(self.actionError, f"Phone did not accept the key: {detail}")

    def _worker(self) -> None:
        try:
            snapshot = self._collect()
        except Exception as exc:  # defensive: a background thread must not kill the app
            snapshot = PhoneSnapshot(error=f"ADB check failed: {exc}")
        self.last_snapshot = snapshot
        self._safe_emit(self.snapshotChanged, snapshot)
        with self._lock:
            self._in_flight = False

    def _safe_emit(self, signal, *args) -> None:
        if self._stopped:
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            # The Qt object can be torn down while the Python worker is ending.
            pass

    def _run(self, args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        assert self.adb_path
        return subprocess.run(
            [self.adb_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _collect(self) -> PhoneSnapshot:
        if not self.adb_path:
            return PhoneSnapshot(error="adb was not found. Install Android platform tools.")

        try:
            listing = self._run(["devices", "-l"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PhoneSnapshot(error=f"Could not run adb: {exc}")
        if listing.returncode != 0:
            detail = (listing.stderr or listing.stdout).strip()
            return PhoneSnapshot(error=f"adb failed: {detail or 'unknown error'}")

        devices = parse_adb_devices(listing.stdout)
        selected = choose_device(devices, self.preferred_serial)
        if selected is None:
            return PhoneSnapshot(
                devices=devices,
                error=_describe_adb_failure(devices, self.preferred_serial),
            )

        name: str | None = selected.display_name or None
        battery: int | None = None
        charging = False
        rotation: int | None = None
        info_error: str | None = None
        try:
            info = self._run(
                [
                    "-s",
                    selected.serial,
                    "shell",
                    (
                        "echo __PL_MODEL__; getprop ro.product.model; "
                        "echo __PL_MANUFACTURER__; getprop ro.product.manufacturer; "
                        "echo __PL_BATTERY__; dumpsys battery; "
                        "echo __PL_ROTATION__; "
                        "(dumpsys input 2>/dev/null | grep -m1 'Viewport INTERNAL') || "
                        "(dumpsys window displays 2>/dev/null | "
                        "grep -m1 'mDisplayRotation=ROTATION_'); true"
                    ),
                ],
                timeout=8.0,
            )
            if info.returncode == 0:
                name, battery, charging = _parse_device_info(info.stdout, fallback=name)
                rotation = parse_rotation(info.stdout)
            else:
                info_error = "Could not read the phone name or battery over ADB."
        except (OSError, subprocess.TimeoutExpired):
            info_error = "Timed out while reading the phone name or battery over ADB."

        return PhoneSnapshot(
            devices=devices,
            selected=selected,
            name=name,
            battery=battery,
            charging=charging,
            rotation=rotation,
            error=info_error,
        )


def _parse_device_info(output: str, fallback: str | None) -> tuple[str | None, int | None, bool]:
    sections: dict[str, str] = {}
    current = ""
    bucket: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in {"__PL_MODEL__", "__PL_MANUFACTURER__", "__PL_BATTERY__"}:
            if current:
                sections[current] = "\n".join(bucket)
            current = stripped.strip("_")
            bucket = []
        else:
            bucket.append(line)
    if current:
        sections[current] = "\n".join(bucket)

    model = sections.get("PL_MODEL", "").strip()
    manufacturer = sections.get("PL_MANUFACTURER", "").strip()
    battery_text = sections.get("PL_BATTERY", output)
    lines = battery_text.splitlines()

    level: int | None = None
    scale: int | None = None
    status: int | None = None
    powered = False
    for line in lines:
        match = re.match(r"\s*level:\s*(\d+)", line)
        if match:
            level = int(match.group(1))
        match = re.match(r"\s*scale:\s*(\d+)", line)
        if match:
            scale = int(match.group(1))
        match = re.match(r"\s*status:\s*(\d+)", line)
        if match:
            status = int(match.group(1))
        if re.match(r"\s*(AC|USB|Wireless) powered:\s*true", line):
            powered = True

    battery: int | None = None
    if level is not None:
        divisor = scale or 100
        if divisor > 0:
            battery = round(level * 100 / divisor)
            battery = max(0, min(100, battery))

    charging = powered or status in {2, 5}

    model_clean = re.sub(r"\s+", " ", model.replace("_", " ")).strip()
    manufacturer_clean = re.sub(r"\s+", " ", manufacturer.replace("_", " ")).strip()
    if manufacturer_clean:
        # getprop returns ""samsung" on some phones.  Capitalize only the
        # first letter so names such as "OnePlus" keep their spelling.
        manufacturer_clean = manufacturer_clean[0].upper() + manufacturer_clean[1:]
    if not model_clean:
        name = fallback
    elif manufacturer_clean and not model_clean.lower().startswith(manufacturer_clean.lower()):
        name = f"{manufacturer_clean} {model_clean}"
    else:
        name = model_clean
    return name or fallback, battery, charging
