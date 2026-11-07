"""Render an ADB pairing QR code locally, with no online QR service."""

from __future__ import annotations

import io

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

# python3-segno is an optional Debian package.  QR pairing is the only feature
# that needs it, so the rest of the app works without it.
INSTALL_HINT = "sudo apt install python3-segno"


def qr_pixmap(payload: str, max_size: int = 280) -> QPixmap | None:
    """Return a sharp pixmap for ``payload``, or ``None`` if segno is missing."""

    try:
        import segno
    except ImportError:
        return None

    qr = segno.make(payload, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=6, border=2)

    pixmap = QPixmap()
    if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
        return None
    if pixmap.width() > max_size or pixmap.height() > max_size:
        pixmap = pixmap.scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    return pixmap
