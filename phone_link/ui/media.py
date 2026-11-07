"""The now-playing bar: controls the phone's media player over KDE Connect."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..models import MediaState
from .icons import glyph_icon
from .theme import ACCENT, DIM, TEXT_SOFT


def format_duration(milliseconds: int) -> str:
    """Format a millisecond count as ``m:ss`` (or ``h:mm:ss`` when long)."""

    seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class ElidedLabel(QLabel):
    """A single-line label that elides instead of forcing the bar wider."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_text = ""

    def set_full_text(self, text: str) -> None:
        if text == self._full_text:
            return
        self._full_text = text
        self.setToolTip(text if len(text) > 48 else "")
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = self.width()
        if width <= 0:
            self.setText(self._full_text)
            return
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, width
            )
        )


class SeekSlider(QSlider):
    """A horizontal slider that also jumps to a click on the groove."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                int(event.position().x()),
                max(1, self.width()),
            )
            self.setValue(value)
        super().mousePressEvent(event)


class MediaBar(QFrame):
    """A compact bar showing what the phone is playing, with transport buttons."""

    actionRequested = Signal(str)
    seekRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaBar")
        self.setFixedHeight(88)
        self._media = MediaState()
        self._dragging = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 9, 14, 9)
        root.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        heading = QLabel("NOW PLAYING", self)
        heading.setObjectName("SectionLabel")
        top.addWidget(heading)
        top.addStretch(1)
        self.player_label = QLabel("", self)
        self.player_label.setObjectName("Dim")
        top.addWidget(self.player_label)
        root.addLayout(top)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.previous = self._button(
            "previous", "Previous track", lambda: self.actionRequested.emit("Previous")
        )
        self.play = self._button(
            "play", "Play on the phone", lambda: self.actionRequested.emit("PlayPause")
        )
        self.next = self._button(
            "next", "Next track", lambda: self.actionRequested.emit("Next")
        )
        row.addWidget(self.previous)
        row.addWidget(self.play)
        row.addWidget(self.next)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(6, 0, 0, 0)
        text_column.setSpacing(0)
        self.title_label = ElidedLabel(self)
        self.title_label.setObjectName("MediaTitle")
        self.artist_label = ElidedLabel(self)
        self.artist_label.setObjectName("Dim")
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.artist_label)
        row.addLayout(text_column, 1)

        self.time_label = QLabel("", self)
        self.time_label.setObjectName("Dim")
        row.addWidget(self.time_label)
        root.addLayout(row)

        self.slider = SeekSlider(self)
        self.slider.setObjectName("MediaSeek")
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        self.slider.setToolTip("Seek within the current track")
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.valueChanged.connect(self._on_slider_value)
        root.addWidget(self.slider)

        self.set_media(MediaState())

    def _button(self, icon: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(glyph_icon(icon, TEXT_SOFT, 20))
        button.setIconSize(QSize(20, 20))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def set_media(self, media: MediaState) -> None:
        self._media = media
        available = media.available
        if available:
            self.player_label.setText(media.player)
            self.title_label.set_full_text(media.title or "Unknown track")
            self.artist_label.set_full_text(media.artist or media.album)
        else:
            self.player_label.setText("")
            self.title_label.set_full_text("Nothing playing")
            self.artist_label.set_full_text(
                "Start something on the phone to control it here."
            )
        self.artist_label.setVisible(bool(self.artist_label.full_text()))

        self.previous.setEnabled(available)
        self.next.setEnabled(available)
        self.play.setEnabled(available)
        self.play.setIcon(
            glyph_icon(
                "pause" if media.is_playing else "play",
                ACCENT if available else DIM,
                20,
            )
        )
        self.play.setToolTip(
            "Pause the phone" if media.is_playing else "Play on the phone"
        )

        seekable = available and media.can_seek and media.length > 0
        self.slider.setEnabled(seekable)
        if not self._dragging:
            self.slider.setRange(0, media.length if media.length > 0 else 0)
            self.slider.setValue(min(media.position, self.slider.maximum()))
        self._update_time(media.position if available else 0)

    def _update_time(self, position: int) -> None:
        if not self._media.available:
            self.time_label.setText("")
            return
        if self._media.length > 0:
            self.time_label.setText(
                f"{format_duration(position)} / {format_duration(self._media.length)}"
            )
        else:
            self.time_label.setText(format_duration(position))

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        self.seekRequested.emit(self.slider.value())

    def _on_slider_value(self, value: int) -> None:
        if self._dragging and self._media.length > 0:
            self.time_label.setText(
                f"{format_duration(value)} / {format_duration(self._media.length)}"
            )
