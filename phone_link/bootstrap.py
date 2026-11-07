"""Process-level setup that must happen before Qt is imported."""

from __future__ import annotations

import os


def configure_qt_platform() -> None:
    """Use XWayland when necessary.

    scrcpy renders into a native X11 window. Wine-style reparenting is not
    possible for native Wayland surfaces, so on a Wayland Plasma session we
    run Phone Link itself through XWayland. The user's explicit
    QT_QPA_PLATFORM choice is respected.
    """

    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        return
    if not os.environ.get("DISPLAY"):
        return
    os.environ["QT_QPA_PLATFORM"] = "xcb"
