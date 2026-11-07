#!/usr/bin/env bash
# Install Phone Link for the current user: a launcher, a menu entry and an icon.
# No root and nothing system-wide.  Re-run after moving the checkout.
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
UNINSTALL=0

usage() {
    cat <<EOF
Usage: ./scripts/install.sh [--prefix DIR] [--uninstall]

Installs a phone-link launcher, desktop entry and icon under DIR
(default: ~/.local).  --uninstall removes them again.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            [ $# -ge 2 ] || { echo "install.sh: --prefix needs a directory" >&2; exit 2; }
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            PREFIX="${1#--prefix=}"
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

PREFIX="${PREFIX%/}"
LAUNCHER="$PREFIX/bin/phone-link"
DESKTOP="$PREFIX/share/applications/phone-link.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/phone-link.svg"

if [ "$UNINSTALL" = 1 ]; then
    rm -f "$LAUNCHER" "$DESKTOP" "$ICON"
    echo "Removed the Phone Link launcher, desktop entry and icon from $PREFIX."
    echo "Autostart, if you enabled it in the tray menu, is separate:"
    echo "  ${XDG_CONFIG_HOME:-$HOME/.config}/autostart/phone-link.desktop"
    exit 0
fi

PYTHON="${PYTHON:-$(command -v python3 || true)}"
if [ -z "$PYTHON" ]; then
    echo "install.sh: python3 was not found on PATH." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import PySide6' >/dev/null 2>&1; then
    echo "install.sh: PySide6 is not available for $PYTHON." >&2
    echo "Install the Debian packages listed in README.md first." >&2
    exit 1
fi

mkdir -p "$PREFIX/bin" \
         "$PREFIX/share/applications" \
         "$PREFIX/share/icons/hicolor/scalable/apps"

cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Installed by Phone Link's install.sh; re-run it if the checkout moves.
cd "$SOURCE_DIR" || exit 1
exec "$PYTHON" -m phone_link "\$@"
EOF
chmod 0755 "$LAUNCHER"

"$PYTHON" - "$SOURCE_DIR" "$LAUNCHER" "$SOURCE_DIR/data/phone-link.desktop" "$DESKTOP" <<'PY'
import sys
from pathlib import Path

source, launcher, template, destination = sys.argv[1:5]
sys.path.insert(0, source)

from phone_link.autostart import exec_quote

text = Path(template).read_text(encoding="utf-8")
Path(destination).write_text(
    text.replace("@EXEC@", exec_quote(launcher)), encoding="utf-8"
)
PY

install -m 0644 "$SOURCE_DIR/data/phone-link.svg" "$ICON"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Installed Phone Link:"
echo "  launcher: $LAUNCHER"
echo "  menu:     $DESKTOP"
echo "  icon:     $ICON"
echo
echo "Run \"$LAUNCHER\", or find Phone Link in the application menu."
echo "Make sure $PREFIX/bin is on PATH if you want to type \"phone-link\"."
