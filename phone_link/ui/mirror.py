"""The mirrored phone: a native scrcpy window plus Android navigation."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..apps import is_valid_package
from ..models import PhoneSnapshot
from ..scrcpy import ScrcpySession
from .icons import empty_state_pixmap, glyph_icon, glyph_pixmap
from .theme import DANGER, DIM, MUTED, SUCCESS, TEXT_SOFT

PHONE_ASPECT = 9.0 / 19.5
DESKTOP_ASPECT = 16.0 / 9.0


def in_app_mirror_audio(enabled: bool, floating_dex: bool) -> bool:
    """Whether the in-app mirror should forward audio.

    When DeX is popped out, the floating window owns the audio.  Starting the
    in-app phone mirror with sound too would play the same stream twice.
    """

    return bool(enabled and not floating_dex)


class NativeScreenHost(QWidget):
    """A real X11 window that scrcpy's window can be reparented into."""

    resized = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NativeScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        self.setPalette(palette)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit(self.width(), self.height())


class MirrorPlaceholder(QWidget):
    """Shown instead of the native host while the mirror is not embeddable."""

    retryRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MirrorPlaceholder")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 24)
        layout.setSpacing(8)
        layout.addStretch(1)

        self.art = QLabel(self)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setPixmap(empty_state_pixmap(76))
        layout.addWidget(self.art)

        layout.addSpacing(6)
        self.title = QLabel("Waiting for your phone", self)
        self.title.setObjectName("OverlayTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.body = QLabel(
            "Connect it over USB with USB debugging enabled.\n"
            "The mirrored screen will appear here.",
            self,
        )
        self.body.setObjectName("OverlayBody")
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        layout.addWidget(self.body)

        layout.addSpacing(10)
        self.retry = QPushButton("Try again", self)
        self.retry.setObjectName("PrimaryButton")
        self.retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry.clicked.connect(self.retryRequested)
        self.retry.hide()
        layout.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_state(
        self,
        title: str,
        body: str,
        *,
        icon: str = "phone",
        tone: str = MUTED,
        retry: bool = False,
    ) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.art.setPixmap(glyph_pixmap(icon, tone, 64))
        self.retry.setVisible(retry)


class NavigationBar(QFrame):
    actionRequested = Signal(str)

    _BUTTONS = (
        ("back", "Back", "Android back (adb keyevent 4)"),
        ("home", "Home", "Android home (adb keyevent 3)"),
        ("recents", "Recents", "Android recent apps (adb keyevent 187)"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavBar")
        self.setFixedHeight(60)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.buttons: dict[str, QPushButton] = {}
        for name, label, tooltip in self._BUTTONS:
            button = QPushButton(label, self)
            button.setObjectName("NavButton")
            button.setIcon(glyph_icon(name, TEXT_SOFT, 18))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, action=name: self.actionRequested.emit(action))
            self.buttons[name] = button
            layout.addWidget(button, 1)
        self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        for button in self.buttons.values():
            button.setEnabled(enabled)


class PhoneStage(QWidget):
    """Keeps the phone frame at a portrait or landscape phone aspect."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(210, 300)
        self._landscape = False
        self._desktop = False
        self.frame = QFrame(self)
        self.frame.setObjectName("PhoneFrame")
        self.stack = QStackedLayout(self.frame)
        self.stack.setContentsMargins(7, 7, 7, 7)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self.host = NativeScreenHost(self.frame)
        self.placeholder = MirrorPlaceholder(self.frame)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.host)

    @property
    def is_landscape(self) -> bool:
        return self._landscape

    @property
    def is_desktop(self) -> bool:
        return self._desktop

    def set_landscape(self, landscape: bool) -> None:
        landscape = bool(landscape)
        if landscape == self._landscape:
            return
        self._landscape = landscape
        self._layout_frame()

    def set_desktop(self, desktop: bool) -> None:
        desktop = bool(desktop)
        if desktop == self._desktop:
            return
        self._desktop = desktop
        self._layout_frame()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_frame()

    def _layout_frame(self) -> None:
        if self._desktop:
            aspect = DESKTOP_ASPECT
        else:
            aspect = (1.0 / PHONE_ASPECT) if self._landscape else PHONE_ASPECT
        avail_w = max(1, self.width() - 2)
        avail_h = max(1, self.height() - 2)
        width = min(avail_w, int(avail_h * aspect))
        height = min(avail_h, int(width / aspect))
        if width < 1 or height < 1:
            return
        x = (self.width() - width) // 2
        y = (self.height() - height) // 2
        self.frame.setGeometry(x, y, width, height)


class MirrorPanel(QWidget):
    navRequested = Signal(str)
    retryRequested = Signal()
    rotationFailed = Signal(str)
    notice = Signal(str)
    appsRequested = Signal()

    def __init__(
        self,
        scrcpy_path: str | None,
        adb_path: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = ScrcpySession(scrcpy_path, adb_path, self)
        # A second scrcpy process holds the DeX virtual display when it is
        # popped out.  The main session keeps mirroring the phone screen, so
        # both views can be used at the same time.
        self._float_session = ScrcpySession(scrcpy_path, adb_path, self)
        self._serial: str | None = None
        self._device_name = ""
        self._embedded = False
        self._last_state = "idle"
        self._rotation = 0
        self._landscape = False
        self._settings = QSettings()
        self._audio = bool(self._settings.value("mirror/audio", False, type=bool))
        self._dex = bool(self._settings.value("mirror/dex", False, type=bool))
        self._floating = False
        # App streaming is deliberately transient: unlike DeX it is not
        # remembered across restarts.
        self._app_package: str | None = None
        self._app_label = ""
        self._last_snapshot: PhoneSnapshot | None = None
        # A rotation command takes a moment to reach the phone.  Do not let an
        # in-flight poll snap the frame back to the old orientation.
        self._rotation_hold = QElapsedTimer()
        # ADB can answer before the window has been laid out.  Starting scrcpy
        # with the 100x30 default widget size makes it create an unusably small
        # X11 window, so wait for a real geometry before launching.
        self._pending_snapshot: PhoneSnapshot | None = None
        self._start_deadline = QElapsedTimer()
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.setInterval(50)
        self._start_timer.timeout.connect(self._maybe_start_pending)
        # scrcpy does not always redraw when its X11 window is resized by
        # another client, which leaves the old frame in a corner of the new
        # window.  Nudge it to repaint once a resize (or rotation) settles.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(160)
        self._repaint_timer.timeout.connect(self._session.refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        strip.setSpacing(8)
        self.title_label = QLabel("MIRRORED SCREEN", self)
        self.title_label.setObjectName("SectionLabel")
        strip.addWidget(self.title_label)

        self.desktop = QToolButton(self)
        self.desktop.setObjectName("DesktopButton")
        self.desktop.setIcon(glyph_icon("monitor", MUTED, 16))
        self.desktop.setIconSize(QSize(16, 16))
        self.desktop.setText("DeX")
        self.desktop.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.desktop.setCheckable(True)
        self.desktop.setChecked(self._dex)
        self.desktop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop.clicked.connect(self._on_desktop_clicked)
        self.desktop.setEnabled(False)
        strip.addWidget(self.desktop)

        self.apps = QToolButton(self)
        self.apps.setObjectName("AppsButton")
        self.apps.setIcon(glyph_icon("apps", MUTED, 16))
        self.apps.setIconSize(QSize(16, 16))
        self.apps.setText("Apps")
        self.apps.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.apps.setToolTip(
            "Open an installed Android app on its own virtual display (Ctrl+Shift+O)"
        )
        self.apps.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apps.clicked.connect(self._on_apps_clicked)
        self.apps.setEnabled(False)
        strip.addWidget(self.apps)

        self.popout = QToolButton(self)
        self.popout.setObjectName("PopoutButton")
        self.popout.setIcon(glyph_icon("monitor", MUTED, 16))
        self.popout.setIconSize(QSize(16, 16))
        self.popout.setText("Pop out")
        self.popout.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.popout.setCheckable(True)
        self.popout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.popout.clicked.connect(self._on_popout_clicked)
        self.popout.setVisible(self._dex)
        self.popout.setEnabled(False)
        strip.addWidget(self.popout)

        self.rotate = QToolButton(self)
        self.rotate.setObjectName("RotateButton")
        self.rotate.setIcon(glyph_icon("rotate", MUTED, 16))
        self.rotate.setIconSize(QSize(16, 16))
        self.rotate.setCheckable(True)
        self.rotate.setToolTip("Rotate the phone screen (Ctrl+Shift+R)")
        self.rotate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rotate.clicked.connect(self._on_rotate_clicked)
        self.rotate.setEnabled(False)
        strip.addWidget(self.rotate)

        self.speaker = QToolButton(self)
        self.speaker.setObjectName("SpeakerButton")
        self.speaker.setIconSize(QSize(16, 16))
        self.speaker.setCheckable(True)
        self.speaker.setChecked(self._audio)
        self.speaker.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speaker.clicked.connect(self._on_speaker_clicked)
        self._update_speaker()
        strip.addWidget(self.speaker)
        strip.addStretch(1)
        self.input_hint = QLabel("", self)
        self.input_hint.setObjectName("Dim")
        strip.addWidget(self.input_hint)
        self.live_label = QLabel("", self)
        self.live_label.setObjectName("StatusDot")
        strip.addWidget(self.live_label)
        root.addLayout(strip)

        self.stage = PhoneStage(self)
        root.addWidget(self.stage, 1)

        self.nav = NavigationBar(self)
        self.nav.actionRequested.connect(self._on_nav_action)
        root.addWidget(self.nav)

        self.stage.placeholder.retryRequested.connect(self.retryRequested)
        self.stage.host.resized.connect(self._on_host_resized)
        self._session.statusChanged.connect(self._on_status)
        self._session.embedded.connect(self._on_embedded)
        self._float_session.statusChanged.connect(self._on_float_status)
        self.stage.set_desktop(self._dex)
        self._update_dex_ui()
        self.show_placeholder(
            "Waiting for your phone",
            "Connect it over USB with USB debugging enabled.\nThe mirrored screen will appear here.",
        )

    @property
    def serial(self) -> str | None:
        return self._serial

    @property
    def is_mirroring(self) -> bool:
        return self._embedded

    @property
    def is_landscape(self) -> bool:
        return self._landscape

    @property
    def is_desktop(self) -> bool:
        return self._dex

    @property
    def is_floating(self) -> bool:
        return self._floating

    @property
    def app_package(self) -> str | None:
        return self._app_package

    @property
    def is_app_streaming(self) -> bool:
        return self._app_package is not None

    @property
    def _shows_desktop(self) -> bool:
        """True while the in-app view is DeX (not the floating window)."""

        return self._dex and not self._floating

    def set_rotation(self, rotation: int | None) -> None:
        """Follow the phone's reported display rotation."""

        if rotation is None:
            return
        if self._rotation_hold.isValid() and self._rotation_hold.elapsed() < 2200:
            return
        self._apply_rotation(int(rotation) % 4)

    def _apply_rotation(self, rotation: int) -> None:
        self._rotation = rotation
        self._landscape = rotation in (1, 3)
        self.stage.set_landscape(self._landscape)
        self.rotate.setChecked(self._landscape)
        if self._landscape:
            self.rotate.setToolTip("Return the phone to portrait (Ctrl+Shift+R)")
        else:
            self.rotate.setToolTip("Rotate the phone to landscape (Ctrl+Shift+R)")

    def toggle_rotation(self) -> bool:
        """Flip between portrait and landscape; False while unavailable."""

        if not self.rotate.isEnabled():
            return False
        target = 0 if self._landscape else 1
        if not self._session.rotate():
            # Put the button back where it was; scrcpy did not accept the key.
            self.rotate.setChecked(self._landscape)
            self.rotationFailed.emit("scrcpy's rotate control is unavailable.")
            return False
        self._rotation_hold.start()
        self._apply_rotation(target)
        return True

    def _on_rotate_clicked(self, _checked: bool = False) -> None:
        self.toggle_rotation()

    _NAV_SHORTCUTS = {"back": "b", "home": "h", "recents": "s"}

    def _on_nav_action(self, action: str) -> None:
        """Route navigation to the display currently shown.

        While DeX or an app stream is embedded, ``adb shell input keyevent``
        targets the phone's main display, not the virtual display.  scrcpy's
        own MOD+b/h/s shortcuts act on the display the session owns, so use
        those.
        """

        shortcut = self._NAV_SHORTCUTS.get(action)
        if (
            shortcut
            and (self._shows_desktop or self._app_package is not None)
            and self._session.send_shortcut(shortcut)
        ):
            return
        self.navRequested.emit(action)

    def _update_speaker(self) -> None:
        enabled = self.speaker.isChecked()
        self.speaker.setIcon(
            glyph_icon("speaker" if enabled else "speaker-off", SUCCESS if enabled else MUTED, 16)
        )
        if enabled:
            self.speaker.setToolTip(
                "Phone audio plays on this computer (click to mute; restarts the mirror)"
            )
        else:
            self.speaker.setToolTip(
                "Play the phone's audio on this computer (restarts the mirror)"
            )

    def _on_speaker_clicked(self, _checked: bool = False) -> None:
        self._audio = self.speaker.isChecked()
        self._settings.setValue("mirror/audio", self._audio)
        self._settings.sync()
        self._update_speaker()
        if (
            self._last_snapshot is not None
            and self._serial is not None
            and (self._embedded or self._last_state in ("starting", "failed"))
        ):
            self.start(self._last_snapshot, force=True)

    def _set_rotate_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled) and not self._shows_desktop
        self.rotate.setEnabled(enabled)
        if not enabled:
            self._rotation_hold.invalidate()

    def _set_mode_enabled(self, enabled: bool) -> None:
        self.desktop.setEnabled(enabled)
        # Stop app stays available even if the phone dropped, so the mode can
        # always be left behind.
        self.apps.setEnabled(enabled or self._app_package is not None)
        self.popout.setEnabled(enabled and self._dex)

    def _update_dex_ui(self) -> None:
        streaming = self._app_package is not None
        self.desktop.setChecked(self._dex)
        self.popout.setVisible(self._dex)
        self.popout.setChecked(self._floating)
        self.popout.setText("Dock" if self._floating else "Pop out")
        self.stage.set_desktop(self._shows_desktop)
        if streaming:
            self.title_label.setText("APP STREAM")
        elif self._shows_desktop:
            self.title_label.setText("SAMSUNG DEX")
        else:
            self.title_label.setText("MIRRORED SCREEN")
        self.title_label.setToolTip(self._app_label if streaming else "")
        self.apps.setText("Stop app" if streaming else "Apps")
        self.apps.setToolTip(
            "Stop the streamed app and return to the phone mirror"
            if streaming
            else "Open an installed Android app on its own virtual display (Ctrl+Shift+O)"
        )
        self.apps.setIcon(
            glyph_icon("close" if streaming else "apps", SUCCESS if streaming else MUTED, 16)
        )
        if self._dex:
            self.desktop.setToolTip(
                "Return to the phone screen (restarts the mirror)"
                if not self._floating
                else "Close the floating DeX window"
            )
            self.popout.setToolTip(
                "Dock the floating DeX window back into Phone Link"
                if self._floating
                else "Open DeX in its own floating window, keeping the phone mirror here"
            )
        else:
            self.desktop.setToolTip(
                "Show Samsung DeX here (uses a virtual display; restarts the mirror)"
            )
        self.desktop.setIcon(glyph_icon("monitor", SUCCESS if self._shows_desktop else MUTED, 16))
        self.popout.setIcon(
            glyph_icon("monitor", SUCCESS if self._floating else MUTED, 16)
        )
        if self._embedded:
            self._set_rotate_enabled(True)
        else:
            self._set_rotate_enabled(False)
        self._set_mode_enabled(self._serial is not None)

    def toggle_desktop(self) -> bool:
        """Switch the in-app view between the phone screen and DeX."""

        if self._serial is None:
            return False
        self.set_desktop(not self._dex)
        return True

    def set_desktop(self, enabled: bool, *, notify: bool = True) -> None:
        enabled = bool(enabled)
        if enabled == self._dex:
            return
        if enabled:
            # DeX and an app stream both own a virtual display; only one can
            # occupy the frame.
            self._app_package = None
            self._app_label = ""
        self._dex = enabled
        self._settings.setValue("mirror/dex", self._dex)
        self._settings.sync()
        if not enabled and self._floating:
            self._stop_floating()
        self._update_dex_ui()
        if notify:
            self.notice.emit(
                "DeX desktop opened in the mirror."
                if enabled
                else "Phone screen restored in the mirror."
            )
        if self._last_snapshot is not None and self._serial is not None:
            self.start(self._last_snapshot, force=True)

    def _on_desktop_clicked(self, _checked: bool = False) -> None:
        # The button is checkable; set_desktop owns the canonical state.
        self.set_desktop(self.desktop.isChecked())

    def _on_apps_clicked(self) -> None:
        if self._app_package is not None:
            self.stop_app_stream()
        else:
            self.appsRequested.emit()

    def stream_app(self, package: str, label: str = "") -> bool:
        """Run *package* on a fresh virtual display in the mirror frame."""

        package = (package or "").strip()
        if not is_valid_package(package):
            self.notice.emit("That is not a valid Android package name.")
            return False
        if self._serial is None:
            self.notice.emit("Connect the phone before opening an app.")
            return False
        if self._floating:
            self._stop_floating()
        if self._dex:
            self._dex = False
            self._settings.setValue("mirror/dex", False)
            self._settings.sync()
        self._app_package = package
        self._app_label = (label or "").strip() or package
        self._update_dex_ui()
        if self._last_snapshot is not None:
            self.start(self._last_snapshot, force=True)
        self.notice.emit(
            f"Opening {self._app_label} on its own display. "
            "Use Stop app to return to the phone screen."
        )
        return True

    def stop_app_stream(self) -> None:
        """Close the app's virtual display and show the phone again."""

        if self._app_package is None:
            return
        self._app_package = None
        self._app_label = ""
        self._update_dex_ui()
        if self._last_snapshot is not None and self._serial is not None:
            self.start(self._last_snapshot, force=True)
        self.notice.emit("App stream stopped; the phone screen is back.")

    def toggle_popout(self) -> bool:
        """Open DeX in a floating window, or dock it again."""

        if not self._dex or self._serial is None:
            return False
        if self._floating:
            self.dock()
        else:
            self.pop_out()
        return True

    def _on_popout_clicked(self, _checked: bool = False) -> None:
        if self.popout.isChecked() and not self._floating:
            self.pop_out()
        elif not self.popout.isChecked() and self._floating:
            self.dock()

    def pop_out(self) -> None:
        if self._floating or self._serial is None:
            return
        self._floating = True
        self._update_dex_ui()
        # The floating DeX window carries audio.  The in-app phone mirror is
        # restarted without sound below, so the stream is not played twice.
        self._float_session.start(
            self._serial,
            0,
            1280,
            720,
            audio=self._audio,
            dex=True,
            floating=True,
        )
        if self._last_snapshot is not None:
            # Bring the normal phone mirror back into the app while DeX lives
            # in its own window.
            self.start(self._last_snapshot, force=True)
        self.notice.emit("DeX opened in a floating window; the phone mirror is still here.")

    def dock(self) -> None:
        if not self._floating:
            return
        self._stop_floating()
        self._update_dex_ui()
        if self._last_snapshot is not None and self._serial is not None:
            self.start(self._last_snapshot, force=True)
        self.notice.emit("DeX docked back into Phone Link.")

    def _stop_floating(self) -> None:
        if self._float_session.is_running:
            self._float_session.stop(silent=True)
        self._floating = False

    def _on_float_status(self, state: str, detail: str) -> None:
        if state == "floating":
            return
        if state not in ("failed", "stopped"):
            return
        if not self._floating:
            return
        # The floating window closed or failed.  The in-app mirror is already
        # showing the phone screen, so leave it alone and drop the DeX mode.
        self._floating = False
        self._dex = False
        self._settings.setValue("mirror/dex", False)
        self._settings.sync()
        self._update_dex_ui()
        if state == "failed":
            self.notice.emit(detail or "The DeX window could not open.")
        else:
            self.notice.emit("DeX window closed.")
        # The in-app mirror was started silent while DeX owned the audio.
        # Restart it now that it is the only view left.
        if self._last_snapshot is not None and self._serial is not None:
            self.start(self._last_snapshot, force=True)

    def start(self, snapshot: PhoneSnapshot, force: bool = False) -> None:
        device = snapshot.selected
        if device is None:
            self.stop_for_disconnect()
            return
        name = snapshot.name or device.display_name
        self._last_snapshot = snapshot
        if (
            self._serial == device.serial
            and (self._embedded or self._floating or self._last_state == "starting")
            and not force
        ):
            self._device_name = name
            self._maybe_start_pending()
            return
        same_device = self._serial == device.serial
        self._serial = device.serial
        self._device_name = name
        if not same_device:
            self._rotation_hold.invalidate()
            self._apply_rotation(0)
        self._embedded = False
        self._last_state = "starting"
        self._update_dex_ui()
        self._show_overlay()
        if self._app_package is not None:
            target = f"{self._app_label} on a virtual display"
            title = "Restarting the app…" if force else "Starting the app…"
            icon = "apps"
        elif self._shows_desktop:
            target = "the DeX desktop"
            title = "Restarting the mirror…" if force else "Starting the mirror…"
            icon = "monitor"
        else:
            target = name
            title = "Restarting the mirror…" if force else "Starting the mirror…"
            icon = "phone"
        self.stage.placeholder.set_state(
            title,
            f"Preparing a live view of {target}.",
            icon=icon,
            tone=MUTED,
        )
        self.live_label.setText("Starting")
        self.live_label.setStyleSheet(f"color: {DIM}; font-size: 11px; font-weight: 600;")
        self.input_hint.setText("")
        self.nav.set_enabled(False)
        self.stage.host.winId()  # realize the native container before reparenting
        self._pending_snapshot = snapshot
        self._start_deadline.start()
        self._maybe_start_pending()

    def _maybe_start_pending(self) -> None:
        pending = self._pending_snapshot
        if pending is None:
            self._start_timer.stop()
            return
        device = pending.selected
        if device is None or device.serial != self._serial:
            self._pending_snapshot = None
            self._start_timer.stop()
            return

        host = self.stage.host
        # The host sits under the placeholder in the stack, so Qt does not lay
        # it out until it is shown.  Use the phone frame's geometry instead;
        # the session is resized to the host as soon as embedding finishes.
        frame = self.stage.frame
        if self.stage.is_desktop:
            laid_out = self.isVisible() and frame.width() >= 480 and frame.height() >= 270
        elif self.stage.is_landscape:
            laid_out = self.isVisible() and frame.width() >= 320 and frame.height() >= 140
        else:
            laid_out = self.isVisible() and frame.width() >= 200 and frame.height() >= 380
        # Do not wait forever (a hidden or degenerate window should still try),
        # but give Qt enough time to finish the first layout pass.
        if not laid_out and self._start_deadline.elapsed() < 3000:
            self._start_timer.start()
            return

        self._pending_snapshot = None
        self._start_timer.stop()
        if self.stage.is_desktop:
            min_height = 270
        else:
            min_height = 240 if self.stage.is_landscape else 480
        self._session.start(
            device.serial,
            int(host.winId()),
            max(frame.width() - 14, 240),
            max(frame.height() - 14, min_height),
            audio=in_app_mirror_audio(self._audio, self._floating),
            dex=self._shows_desktop,
            app=self._app_package,
        )

    def restart(self) -> None:
        self._pending_snapshot = None
        self._start_timer.stop()
        self._stop_floating()
        self._serial = None
        self._last_state = "idle"
        self._rotation_hold.invalidate()
        self._apply_rotation(0)
        self._update_dex_ui()
        self.retryRequested.emit()

    def stop_for_disconnect(self) -> None:
        self._pending_snapshot = None
        self._start_timer.stop()
        if self._serial is not None or self._embedded:
            self._session.stop(silent=True)
        if self._float_session.is_running:
            self._float_session.stop(silent=True)
        self._floating = False
        self._serial = None
        self._device_name = ""
        self._app_package = None
        self._app_label = ""
        self._embedded = False
        self._last_state = "idle"
        self._rotation_hold.invalidate()
        self._apply_rotation(0)
        self._update_dex_ui()
        self.show_placeholder(
            "Waiting for your phone",
            "Connect it over USB with USB debugging enabled.\nThe mirrored screen will appear here.",
        )

    def stop(self) -> None:
        self._pending_snapshot = None
        self._start_timer.stop()
        self._session.stop(block=True, silent=True)
        self._float_session.stop(block=True, silent=True)
        self._serial = None
        self._embedded = False
        self._floating = False
        self._app_package = None
        self._app_label = ""
        self._update_dex_ui()

    def focus_screen(self) -> None:
        if self._embedded:
            self._session.focus()

    def show_placeholder(
        self,
        title: str,
        body: str,
        *,
        icon: str = "phone",
        tone: str = MUTED,
        retry: bool = False,
    ) -> None:
        self._show_overlay()
        self.stage.placeholder.set_state(title, body, icon=icon, tone=tone, retry=retry)
        self.live_label.setText("Offline")
        self.live_label.setStyleSheet(f"color: {DIM}; font-size: 11px; font-weight: 600;")
        self.input_hint.setText("")
        self.nav.set_enabled(False)
        self._set_rotate_enabled(False)
        self._set_mode_enabled(False)

    def _show_overlay(self) -> None:
        self.stage.stack.setCurrentWidget(self.stage.placeholder)

    def _show_screen(self) -> None:
        self.stage.stack.setCurrentWidget(self.stage.host)

    def _on_embedded(self, window_id: int) -> None:
        self._embedded = True
        self._last_state = "embedded"
        self._show_screen()
        self.nav.set_enabled(self._serial is not None)
        self._update_dex_ui()
        self.live_label.setText("● Live")
        self.live_label.setStyleSheet(f"color: {SUCCESS}; font-size: 11px; font-weight: 600;")
        self.input_hint.setText("DeX floating" if self._floating else "Input passes through")
        self._session.set_geometry(self.stage.host.width(), self.stage.host.height())
        self._repaint_timer.start()
        self._session.focus()

    def _on_status(self, state: str, detail: str) -> None:
        self._last_state = state
        if state == "starting":
            if self.stage.stack.currentWidget() is self.stage.host:
                self._show_overlay()
            return
        if state == "embedded":
            return
        self._embedded = False
        self.nav.set_enabled(False)
        self._set_rotate_enabled(False)
        self._show_overlay()
        if state == "failed":
            title = "App unavailable" if self._app_package is not None else "Mirror unavailable"
            tone = DANGER
            icon = "apps" if self._app_package is not None else "phone"
        else:
            title = "App stopped" if self._app_package is not None else "Mirror stopped"
            tone = MUTED
            icon = "apps" if self._app_package is not None else "phone"
        self.stage.placeholder.set_state(title, detail, icon=icon, tone=tone, retry=True)
        self.live_label.setText("Offline")
        self.live_label.setStyleSheet(f"color: {DANGER}; font-size: 11px; font-weight: 600;")
        self.input_hint.setText("")
        self._update_dex_ui()

    def _on_host_resized(self, width: int, height: int) -> None:
        if self._embedded:
            self._session.set_geometry(width, height)
            # Wait until the layout stops changing (rotation, window drag)
            # before asking for the repaint.
            self._repaint_timer.start()
        elif self._pending_snapshot is not None:
            self._maybe_start_pending()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.Show:
            if self._pending_snapshot is not None:
                self._maybe_start_pending()
            elif self._embedded:
                self._session.set_geometry(self.stage.host.width(), self.stage.host.height())
        return super().event(event)
