"""Enable or disable starting Phone Link at login.

The entry follows the XDG autostart convention: a ``.desktop`` file in
``$XDG_CONFIG_HOME/autostart`` (usually ``~/.config/autostart``).  KDE Plasma
and other desktops launch it with the rest of the session.  Nothing is
installed system-wide, and removing the file turns the feature off again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

AUTOSTART_FILE = "phone-link.desktop"


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "autostart" / AUTOSTART_FILE


def _exec_quote(value: str) -> str:
    """Quote one argument for a desktop entry's ``Exec`` key."""

    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def autostart_entry(
    executable: str | None = None,
    working_dir: str | None = None,
) -> str:
    """The ``.desktop`` contents that run ``python -m phone_link``."""

    executable = executable or sys.executable
    if working_dir is None:
        working_dir = str(Path(__file__).resolve().parent.parent)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Phone Link",
        "Comment=Android phone companion",
        f"Exec={_exec_quote(executable)} -m phone_link",
        "Icon=phone-link",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
    ]
    if working_dir:
        lines.append(f"Path={working_dir}")
    return "\n".join(lines) + "\n"


def is_enabled(path: Path | None = None) -> bool:
    return (path or autostart_path()).is_file()


def set_enabled(
    enabled: bool,
    path: Path | None = None,
    executable: str | None = None,
    working_dir: str | None = None,
) -> Path:
    """Create or remove the autostart entry; returns the file's path."""

    config = path or autostart_path()
    if not enabled:
        config.unlink(missing_ok=True)
        return config
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_name(config.name + ".tmp")
    temporary.write_text(
        autostart_entry(executable=executable, working_dir=working_dir),
        encoding="utf-8",
    )
    os.replace(temporary, config)
    return config
