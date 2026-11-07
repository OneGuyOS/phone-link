"""Small painted icons so Phone Link does not depend on an icon theme."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .theme import ACCENT, MUTED, TEXT


def _canvas(size: int, dpr: int = 2) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pixmap, painter


def app_icon_pixmap(size: int = 128) -> QPixmap:
    pixmap, painter = _canvas(size, 2)
    rect = QRectF(2, 2, size - 4, size - 4)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#1f7fca"))
    gradient.setColorAt(0.55, QColor("#3daee9"))
    gradient.setColorAt(1.0, QColor("#56c8f5"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, size * 0.25, size * 0.25)

    painter.setPen(QPen(QColor("#f4fbff"), size * 0.052, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    phone = QRectF(size * 0.30, size * 0.19, size * 0.31, size * 0.55)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(phone, size * 0.055, size * 0.055)
    painter.drawLine(
        QPointF(size * 0.395, size * 0.65),
        QPointF(size * 0.515, size * 0.65),
    )

    # A little link glyph, to read as "phone + connection".
    painter.setBrush(QColor("#eaf8ff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(size * 0.53, size * 0.53, size * 0.23, size * 0.23))
    painter.setBrush(QColor("#1f7fca"))
    painter.drawEllipse(QRectF(size * 0.585, size * 0.585, size * 0.12, size * 0.12))
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


def glyph_pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    pixmap, p = _canvas(size, 2)
    p.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(color), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "back":
        path = QPainterPath()
        path.moveTo(15.0, 5.0)
        path.lineTo(8.0, 12.0)
        path.lineTo(15.0, 19.0)
        p.drawPath(path)
    elif name == "home":
        path = QPainterPath()
        path.moveTo(4.2, 10.8)
        path.lineTo(12.0, 4.2)
        path.lineTo(19.8, 10.8)
        path.moveTo(6.4, 9.8)
        path.lineTo(6.4, 19.6)
        path.lineTo(17.6, 19.6)
        path.lineTo(17.6, 9.8)
        p.drawPath(path)
    elif name == "recents":
        p.drawRoundedRect(QRectF(4.0, 6.0, 10.0, 12.0), 2.4, 2.4)
        p.drawRoundedRect(QRectF(10.0, 10.0, 10.0, 12.0), 2.4, 2.4)
    elif name == "bell":
        path = QPainterPath()
        path.moveTo(6.2, 16.4)
        path.cubicTo(7.4, 14.8, 7.4, 13.8, 7.4, 11.0)
        path.cubicTo(7.4, 7.7, 9.2, 5.7, 12.0, 5.7)
        path.cubicTo(14.8, 5.7, 16.6, 7.7, 16.6, 11.0)
        path.cubicTo(16.6, 13.8, 16.6, 14.8, 17.8, 16.4)
        path.lineTo(6.2, 16.4)
        p.drawPath(path)
        p.drawLine(QPointF(10.2, 19.0), QPointF(13.8, 19.0))
    elif name == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5.0, 5.0, 14.0, 14.0), 60)
        path.arcTo(QRectF(5.0, 5.0, 14.0, 14.0), 60, 280)
        p.drawPath(path)
        p.drawLine(QPointF(17.9, 4.5), QPointF(18.2, 9.0))
        p.drawLine(QPointF(18.2, 9.0), QPointF(13.8, 8.7))
    elif name == "rotate":
        p.drawArc(QRectF(4.5, 4.5, 15.0, 15.0), 40 * 16, 280 * 16)
        p.drawLine(QPointF(15.7, 3.1), QPointF(20.0, 4.3))
        p.drawLine(QPointF(20.0, 4.3), QPointF(18.5, 8.3))
    elif name in ("speaker", "speaker-off"):
        path = QPainterPath()
        path.moveTo(4.0, 9.5)
        path.lineTo(7.2, 9.5)
        path.lineTo(11.0, 6.2)
        path.lineTo(11.0, 17.8)
        path.lineTo(7.2, 14.5)
        path.lineTo(4.0, 14.5)
        path.closeSubpath()
        p.drawPath(path)
        if name == "speaker":
            p.drawArc(QRectF(12.0, 8.5, 6.0, 7.0), -60 * 16, 120 * 16)
            p.drawArc(QRectF(11.0, 5.8, 9.5, 12.4), -55 * 16, 110 * 16)
        else:
            p.drawLine(QPointF(14.3, 9.3), QPointF(19.5, 14.7))
            p.drawLine(QPointF(19.5, 9.3), QPointF(14.3, 14.7))
    elif name in ("play", "pause", "previous", "next"):
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        if name == "play":
            path = QPainterPath()
            path.moveTo(8.0, 5.0)
            path.lineTo(19.0, 12.0)
            path.lineTo(8.0, 19.0)
            path.closeSubpath()
            p.drawPath(path)
        elif name == "pause":
            p.drawRoundedRect(QRectF(6.8, 5.0, 3.6, 14.0), 1.2, 1.2)
            p.drawRoundedRect(QRectF(13.6, 5.0, 3.6, 14.0), 1.2, 1.2)
        elif name == "previous":
            path = QPainterPath()
            path.moveTo(18.0, 5.0)
            path.lineTo(9.0, 12.0)
            path.lineTo(18.0, 19.0)
            path.closeSubpath()
            p.drawPath(path)
            p.drawRoundedRect(QRectF(5.0, 5.0, 2.4, 14.0), 1.0, 1.0)
        else:
            path = QPainterPath()
            path.moveTo(6.0, 5.0)
            path.lineTo(15.0, 12.0)
            path.lineTo(6.0, 19.0)
            path.closeSubpath()
            p.drawPath(path)
            p.drawRoundedRect(QRectF(16.6, 5.0, 2.4, 14.0), 1.0, 1.0)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3.5, 7.2)
        path.lineTo(9.2, 7.2)
        path.lineTo(11.0, 9.4)
        path.lineTo(20.5, 9.4)
        path.lineTo(20.5, 19.0)
        path.lineTo(3.5, 19.0)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "close":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
    elif name == "external":
        path = QPainterPath()
        path.moveTo(13.5, 5.0)
        path.lineTo(19.0, 5.0)
        path.lineTo(19.0, 10.5)
        path.moveTo(11.0, 13.0)
        path.lineTo(19.0, 5.0)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(5.0, 8.0, 9.5, 11.0), 1.6, 1.6)
    elif name == "chevron":
        path = QPainterPath()
        path.moveTo(9.0, 5.0)
        path.lineTo(16.0, 12.0)
        path.lineTo(9.0, 19.0)
        p.drawPath(path)
    elif name == "phone":
        p.drawRoundedRect(QRectF(7.0, 3.0, 10.0, 18.0), 2.0, 2.0)
        p.drawLine(QPointF(10.2, 17.0), QPointF(13.8, 17.0))
    elif name == "monitor":
        p.drawRoundedRect(QRectF(3.2, 4.8, 17.6, 11.8), 1.8, 1.8)
        p.drawLine(QPointF(12.0, 16.6), QPointF(12.0, 20.0))
        p.drawLine(QPointF(8.4, 20.0), QPointF(15.6, 20.0))
    elif name == "wifi":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4.0, 6.0, 16.0, 11.0), 55)
        path.arcTo(QRectF(4.0, 6.0, 16.0, 11.0), 55, 70)
        path.arcMoveTo(QRectF(7.0, 9.5, 10.0, 8.0), 55)
        path.arcTo(QRectF(7.0, 9.5, 10.0, 8.0), 55, 70)
        p.drawPath(path)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(12.0, 18.0), 1.1, 1.1)
    elif name == "usb":
        p.drawLine(QPointF(12.0, 4.0), QPointF(12.0, 19.0))
        p.drawLine(QPointF(12.0, 8.0), QPointF(7.5, 12.0))
        p.drawLine(QPointF(12.0, 11.0), QPointF(16.5, 15.0))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(12.0, 5.0), 1.4, 1.4)
        p.drawRect(QRectF(10.9, 18.0, 2.2, 2.2))
    elif name == "clipboard":
        p.drawRoundedRect(QRectF(5.5, 4.6, 13.0, 15.8), 2.0, 2.0)
        p.drawRoundedRect(QRectF(9.0, 3.0, 6.0, 3.8), 1.1, 1.1)
    elif name == "sync":
        p.drawArc(QRectF(5.0, 5.0, 14.0, 14.0), 40 * 16, 140 * 16)
        p.drawArc(QRectF(5.0, 5.0, 14.0, 14.0), 220 * 16, 140 * 16)
        p.drawLine(QPointF(16.9, 5.4), QPointF(18.3, 9.9))
        p.drawLine(QPointF(18.3, 9.9), QPointF(13.8, 9.4))
        p.drawLine(QPointF(7.1, 18.6), QPointF(5.7, 14.1))
        p.drawLine(QPointF(5.7, 14.1), QPointF(10.2, 14.6))
    elif name == "file":
        path = QPainterPath()
        path.moveTo(6.5, 3.5)
        path.lineTo(13.8, 3.5)
        path.lineTo(18.5, 8.2)
        path.lineTo(18.5, 20.5)
        path.lineTo(6.5, 20.5)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(13.8, 3.7), QPointF(13.8, 8.3))
        p.drawLine(QPointF(13.8, 8.3), QPointF(18.3, 8.3))
    elif name == "check":
        path = QPainterPath()
        path.moveTo(5.0, 12.5)
        path.lineTo(10.0, 17.0)
        path.lineTo(19.0, 7.0)
        p.drawPath(path)
    elif name == "warning":
        path = QPainterPath()
        path.moveTo(12.0, 3.8)
        path.lineTo(21.0, 19.8)
        path.lineTo(3.0, 19.8)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12.0, 9.0), QPointF(12.0, 14.2))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(12.0, 17.2), 1.0, 1.0)
    elif name == "more":
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        for x in (5.6, 12.0, 18.4):
            p.drawEllipse(QPointF(x, 12.0), 1.45, 1.45)
    elif name == "message":
        p.drawRoundedRect(QRectF(3.6, 4.8, 16.8, 12.4), 3.0, 3.0)
        path = QPainterPath()
        path.moveTo(8.2, 16.8)
        path.lineTo(8.2, 20.4)
        path.lineTo(12.6, 16.8)
        p.drawPath(path)
    else:
        p.drawEllipse(QPointF(12, 12), 6, 6)
    p.end()
    return pixmap


def glyph_icon(name: str, color: str = MUTED, size: int = 20) -> QIcon:
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(name, color, size), QIcon.Mode.Normal)
    icon.addPixmap(glyph_pixmap(name, "#525e6c", size), QIcon.Mode.Disabled)
    return icon


def notification_badge(app_name: str, size: int = 40) -> QPixmap:
    pixmap, painter = _canvas(size, 2)
    digest = hashlib.sha1(app_name.encode("utf-8", errors="replace")).digest()
    palette = [
        ("#2f6fed", "#5a9cff"),
        ("#0f9d78", "#4ed7ac"),
        ("#b758d6", "#e18bf5"),
        ("#d96b3d", "#f3a26d"),
        ("#c64b72", "#f084a3"),
        ("#4c7a8f", "#80b5ca"),
    ]
    start, end = palette[digest[0] % len(palette)]
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor(start))
    gradient.setColorAt(1, QColor(end))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    letter = (app_name.strip()[:1] or "?").upper()
    font = QFont("Noto Sans")
    font.setPixelSize(int(size * 0.48))
    font.setWeight(QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return pixmap


def icon_from_file(path: str, size: int = 40) -> QPixmap | None:
    if not path:
        return None
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    scaled = pixmap.scaled(
        size * 2,
        size * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(2)
    return scaled


def empty_state_pixmap(size: int = 84) -> QPixmap:
    pixmap, p = _canvas(size, 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(61, 174, 233, 28))
    p.drawEllipse(QRectF(4, 4, size - 8, size - 8))
    p.setPen(QPen(QColor(ACCENT), size * 0.035, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size / 2
    path = QPainterPath()
    path.moveTo(s - 15, s + 9)
    path.cubicTo(s - 8, s + 2, s - 8, s - 3, s - 8, s - 10)
    path.cubicTo(s - 8, s - 21, s + 1, s - 27, s, s - 27)
    path.cubicTo(s + 1, s - 27, s + 8, s - 21, s + 8, s - 10)
    path.cubicTo(s + 8, s - 3, s + 8, s + 2, s + 15, s + 9)
    path.lineTo(s - 15, s + 9)
    p.drawPath(path)
    p.drawLine(QPointF(s - 7, s + 15), QPointF(s + 5, s + 15))
    p.end()
    return pixmap


def text_color() -> QColor:
    return QColor(TEXT)
