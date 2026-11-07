"""Startup checks for the external pieces Phone Link relies on."""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass

from PySide6.QtDBus import QDBusConnection

KDE_SERVICE = "org.kde.kdeconnect"

APT_COMMAND = (
    "sudo apt install python3 python3-pyside6.qtcore python3-pyside6.qtgui "
    "python3-pyside6.qtwidgets python3-pyside6.qtdbus python3-segno "
    "adb kdeconnect"
)

SCRCPY_INSTALL = """\
SCRCPY_VERSION=5.0.1
mkdir -p ~/.local/opt ~/.local/bin
curl -fL \\
  "https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY_VERSION}/scrcpy-linux-x86_64-v${SCRCPY_VERSION}.tar.gz" \\
  | tar -xz -C ~/.local/opt
ln -sfn "$HOME/.local/opt/scrcpy-linux-x86_64-v${SCRCPY_VERSION}" "$HOME/.local/opt/scrcpy"
printf '#!/bin/sh\\nexec "$HOME/.local/opt/scrcpy/scrcpy" "$@"\\n' > "$HOME/.local/bin/scrcpy"
chmod +x "$HOME/.local/bin/scrcpy"
"""


@dataclass(frozen=True)
class CheckItem:
    key: str
    label: str
    ok: bool
    detail: str
    severity: str = "required"


@dataclass(frozen=True)
class DependencyReport:
    adb_path: str | None
    scrcpy_path: str | None
    kdeconnect_path: str | None
    qt_platform: str
    x11_display: str | None
    daemon_registered: bool
    items: tuple[CheckItem, ...]

    @property
    def problems(self) -> tuple[CheckItem, ...]:
        return tuple(item for item in self.items if not item.ok)

    @property
    def has_problems(self) -> bool:
        return bool(self.problems)

    @property
    def missing_required(self) -> tuple[CheckItem, ...]:
        return tuple(item for item in self.problems if item.severity == "required")

    def by_key(self, key: str) -> CheckItem | None:
        return next((item for item in self.items if item.key == key), None)


def check_dependencies(qt_platform: str) -> DependencyReport:
    adb_path = shutil.which("adb")
    scrcpy_path = shutil.which("scrcpy")
    kdeconnect_path = shutil.which("kdeconnect-cli") or shutil.which("kdeconnectd")
    x11_display = os.environ.get("DISPLAY") or None
    qr_ok = importlib.util.find_spec("segno") is not None

    daemon_registered = False
    bus_error = ""
    try:
        bus = QDBusConnection.sessionBus()
        if bus.isConnected():
            reply = bus.interface().isServiceRegistered(KDE_SERVICE)
            daemon_registered = bool(reply.value())
        else:
            bus_error = "The session D-Bus is not available."
    except Exception as exc:  # QtDBus can fail in unusual session setups
        bus_error = str(exc)

    items = [
        CheckItem(
            key="adb",
            label="Android Debug Bridge (adb)",
            ok=bool(adb_path),
            detail=adb_path or "Not found on PATH.",
        ),
        CheckItem(
            key="scrcpy",
            label="scrcpy",
            ok=bool(scrcpy_path),
            detail=scrcpy_path or "Not found on PATH. Debian trixie does not package scrcpy; use the official release.",
        ),
        CheckItem(
            key="kdeconnect",
            label="KDE Connect",
            ok=bool(kdeconnect_path),
            detail=kdeconnect_path or "Not found. Install the kdeconnect package.",
        ),
        CheckItem(
            key="daemon",
            label="KDE Connect daemon",
            ok=daemon_registered,
            detail=(
                "Running on the session bus."
                if daemon_registered
                else (bus_error or "Not running. Start kdeconnectd or log in to Plasma.")
            ),
            severity="warning",
        ),
        CheckItem(
            key="x11",
            label="X11 / XWayland display",
            ok=qt_platform == "xcb" and bool(x11_display),
            detail=(
                f"Qt is using {qt_platform}; DISPLAY={x11_display}."
                if qt_platform == "xcb" and x11_display
                else "Screen embedding needs an X11 or XWayland display. On Plasma Wayland, install xwayland and launch with QT_QPA_PLATFORM=xcb."
            ),
            severity="warning",
        ),
        CheckItem(
            key="qr",
            label="QR pairing (python3-segno)",
            ok=qr_ok,
            detail=(
                "python3-segno is available."
                if qr_ok
                else "Not installed. Wi-Fi pairing uses a QR code and needs python3-segno."
            ),
            severity="warning",
        ),
    ]

    return DependencyReport(
        adb_path=adb_path,
        scrcpy_path=scrcpy_path,
        kdeconnect_path=kdeconnect_path,
        qt_platform=qt_platform,
        x11_display=x11_display,
        daemon_registered=daemon_registered,
        items=tuple(items),
    )


def installation_text(report: DependencyReport) -> str:
    """Only show the commands that address a problem in this report."""

    keys = {item.key for item in report.problems}
    chunks: list[str] = []
    apt_packages = [
        package
        for key, package in (
            ("adb", "adb"),
            ("kdeconnect", "kdeconnect"),
            ("qr", "python3-segno"),
        )
        if key in keys
    ]
    if "scrcpy" in keys:
        apt_packages.append("curl")
    if apt_packages:
        chunks.append("sudo apt install " + " ".join(apt_packages))
    if "scrcpy" in keys:
        chunks.append(SCRCPY_INSTALL.rstrip())
    if "daemon" in keys and "kdeconnect" not in keys:
        chunks.append("kdeconnectd  # or log out and back in to Plasma")
    if "x11" in keys:
        chunks.append("sudo apt install xwayland\n# then launch Phone Link normally; it selects xcb automatically")
    return "\n\n".join(chunks)
