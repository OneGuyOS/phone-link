"""The Phone Link main window."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..adb import AdbMonitor
from ..clipboard_sync import ClipboardSyncGate
from ..dependencies import DependencyReport
from ..kdeconnect import DAEMON_PATH, KdeConnectClient
from ..models import KdeSnapshot, PhoneSnapshot
from ..sms import SmsClient
from .agent_panel import AgentPanel
from .dialogs import ShortcutsDialog
from .header import HeaderBar
from .icons import app_icon
from .media import MediaBar
from .mirror import MirrorPanel
from .notifications import NotificationsPanel
from .phone_actions import PhoneActionsMenu, SmsDialog, VolumeDialog
from .sms_panel import SmsPanel
from .wifi_dialog import WifiConnectDialog


class MainWindow(QMainWindow):
    ADB_REFRESH_MS = 2_500

    def __init__(self, report: DependencyReport) -> None:
        super().__init__()
        self.report = report
        self._settings = QSettings()
        self.setWindowTitle("Phone Link")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 680)
        self.resize(1180, 860)
        self._restore_geometry()
        self._fullscreen_was_maximized = False

        self._adb_snapshot = PhoneSnapshot(error="Looking for a phone over ADB…")
        self._kde_snapshot = KdeSnapshot(mode="offline", message="Starting KDE Connect client…")
        self._mirror_target: str | None = None

        root_widget = QWidget(self)
        root_widget.setObjectName("Root")
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)
        self.setCentralWidget(root_widget)

        self.header = HeaderBar(root_widget)
        self.header.refreshRequested.connect(self.refresh_all)
        self.header.clipboardRequested.connect(self._send_clipboard)
        self.header.wifiRequested.connect(self._open_wifi_pairing)
        self.header.filesRequested.connect(self._open_phone_files)
        root.addWidget(self.header)

        self.phone_menu = PhoneActionsMenu(self)
        self.phone_menu.findRequested.connect(self._find_phone)
        self.phone_menu.stopRingingRequested.connect(self._stop_ringing)
        self.phone_menu.pingRequested.connect(self._ping_phone)
        self.phone_menu.smsRequested.connect(self._open_sms_dialog)
        self.phone_menu.volumeRequested.connect(self._open_phone_volume)
        self.phone_menu.openMessagesRequested.connect(self._open_phone_messages)
        self.phone_menu.aboutToShow.connect(self._refresh_phone_menu)
        self.header.set_phone_menu(self.phone_menu)
        self._volume_dialog: VolumeDialog | None = None

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)

        left_column = QWidget(root_widget)
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.mirror = MirrorPanel(report.scrcpy_path, report.adb_path, left_column)
        self.mirror.setMinimumWidth(330)
        self.mirror.navRequested.connect(self._send_nav)
        self.mirror.retryRequested.connect(self._retry_mirror)
        self.mirror.rotationFailed.connect(self._flash)
        self.mirror.notice.connect(self._flash)
        left_layout.addWidget(self.mirror, 1)

        self.media = MediaBar(left_column)
        self.media.actionRequested.connect(self._media_action)
        self.media.seekRequested.connect(self._media_seek)
        left_layout.addWidget(self.media)

        content.addWidget(left_column, 5)

        self.notifications = NotificationsPanel(root_widget)
        self.notifications.setMinimumWidth(360)
        self.notifications.dismissRequested.connect(self._dismiss_notification)
        self.notifications.replyRequested.connect(self._send_reply)
        self.notifications.historyClearRequested.connect(self._clear_history)
        self.notifications.retryRequested.connect(self.refresh_all)
        self.notifications.filesDropped.connect(self._send_files)

        self.sms_panel = SmsPanel(root_widget)
        self.sms_panel.setMinimumWidth(360)

        self.agent_panel = AgentPanel(report.adb_path, root_widget)
        self.agent_panel.setMinimumWidth(360)

        right_column = QWidget(root_widget)
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(6)
        self.notifications_tab = QPushButton("Notifications", right_column)
        self.messages_tab = QPushButton("Messages", right_column)
        self.agent_tab = QPushButton("Agent", right_column)
        for tab in (self.notifications_tab, self.messages_tab, self.agent_tab):
            tab.setObjectName("PanelTab")
            tab.setCheckable(True)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_row.addWidget(tab)
        tab_row.addStretch(1)
        right_layout.addLayout(tab_row)

        self.right_stack = QStackedWidget(right_column)
        self.right_stack.addWidget(self.notifications)
        self.right_stack.addWidget(self.sms_panel)
        self.right_stack.addWidget(self.agent_panel)
        right_layout.addWidget(self.right_stack, 1)

        self.notifications_tab.clicked.connect(lambda: self._show_right_tab(0))
        self.messages_tab.clicked.connect(lambda: self._show_right_tab(1))
        self.agent_tab.clicked.connect(lambda: self._show_right_tab(2))
        self._show_right_tab(
            self._settings.value("panel/right_tab", 0, type=int)
        )

        content.addWidget(right_column, 4)
        root.addLayout(content, 1)

        status = QStatusBar(self)
        status.setSizeGripEnabled(False)
        status.setFixedHeight(24)
        self.setStatusBar(status)
        self._flash("Ready — press F1 for keyboard shortcuts")

        self.adb = AdbMonitor(report.adb_path, self)
        self.adb.snapshotChanged.connect(self._on_phone_snapshot)
        self.adb.actionError.connect(self._flash)
        self.adb.ringStopped.connect(
            lambda: self._flash("Stopped ringing the phone.")
        )

        self.kde = KdeConnectClient(self)
        self.kde.snapshotChanged.connect(self._on_kde_snapshot)
        self.kde.mediaChanged.connect(self.media.set_media)
        self.kde.phoneStorageReady.connect(self._on_phone_storage_ready)
        self.kde.phoneVolumeChanged.connect(self._apply_phone_volume)
        self.kde.phoneMutedChanged.connect(self._apply_phone_muted)
        self.kde.actionError.connect(self._flash)

        self._sms_plugin_device = ""
        self._sms_plugin_ok = False
        self.sms = SmsClient(self)
        self.sms.changed.connect(self._on_sms_changed)
        self.sms.threadLoading.connect(
            lambda thread_id: self.sms_panel.set_thread_loading(thread_id, True)
        )
        self.sms.threadLoaded.connect(self._on_sms_thread_loaded)
        self.sms.sent.connect(self._on_sms_sent)
        self.sms.sendFailed.connect(self._on_sms_send_failed)
        self.sms.error.connect(self._on_sms_error)
        self.sms_panel.threadOpened.connect(self._load_sms_thread)
        self.sms_panel.loadEarlierRequested.connect(
            lambda thread_id: self.sms.load_thread(thread_id, earlier=True)
        )
        self.sms_panel.replyRequested.connect(self.sms.reply)
        self.sms_panel.newMessageRequested.connect(self._open_sms_dialog)
        self.sms_panel.refreshRequested.connect(self._refresh_sms)

        # Optional automatic clipboard sync.  Off by default: forwarding every
        # copy also forwards passwords and anything else on the clipboard.
        self._clipboard_sync_enabled = False
        self._clipboard_gate = ClipboardSyncGate()
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(350)
        self._clipboard_timer.timeout.connect(self._push_clipboard)
        self.header.autoClipboardToggled.connect(self._set_auto_clipboard)
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_clipboard_changed)
        self._set_auto_clipboard(
            self._settings.value("clipboard/auto_sync", False, type=bool)
        )

        self._adb_timer = QTimer(self)
        self._adb_timer.setInterval(self.ADB_REFRESH_MS)
        self._adb_timer.timeout.connect(self.adb.refresh)

        self.kde.start()
        self.adb.refresh()
        self._adb_timer.start()
        self._update_header()
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for keys, slot in (
            ("F5", self.refresh_all),
            ("Ctrl+R", self.refresh_all),
            ("Ctrl+Shift+R", self._rotate_screen),
            ("Ctrl+Shift+D", self._toggle_desktop),
            ("Ctrl+Shift+P", self._toggle_popout),
            ("Ctrl+F", self._focus_search),
            ("Ctrl+Shift+M", lambda: self._show_right_tab(1)),
            ("Ctrl+Shift+A", self._toggle_right_tab),
            ("F11", self._toggle_fullscreen),
            ("F1", self._show_shortcuts),
            ("Ctrl+Q", self.close),
            ("Ctrl+W", self.close),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _focus_search(self) -> None:
        if not self.notifications.focus_search():
            self._flash("No notifications to search.")

    def _show_right_tab(self, index: int) -> None:
        index = min(2, max(0, int(index)))
        self.right_stack.setCurrentIndex(index)
        self.notifications_tab.setChecked(index == 0)
        self.messages_tab.setChecked(index == 1)
        self.agent_tab.setChecked(index == 2)
        self._settings.setValue("panel/right_tab", index)
        if index == 2:
            self.agent_panel.input.setFocus()
        elif index == 1 and self.sms_panel.current_thread() is not None:
            self.sms_panel.input.setFocus()

    def _toggle_right_tab(self) -> None:
        self._show_right_tab(0 if self.right_stack.currentIndex() == 2 else 2)

    def _rotate_screen(self) -> None:
        if not self.mirror.rotate.isEnabled():
            if self.mirror.is_desktop and not self.mirror.is_floating:
                self._flash("Rotation is unavailable while DeX is in the mirror.")
            else:
                self._flash("Start the mirror before rotating the phone.")
            return
        self.mirror.toggle_rotation()

    def _toggle_desktop(self) -> None:
        if self.mirror.serial is None:
            self._flash("Connect the phone before opening DeX.")
            return
        self.mirror.toggle_desktop()

    def _toggle_popout(self) -> None:
        if not self.mirror.is_desktop:
            self._flash("Turn on DeX before popping it out.")
            return
        self.mirror.toggle_popout()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            if self._fullscreen_was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            return
        self._fullscreen_was_maximized = self.isMaximized()
        self.showFullScreen()

    def _show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def _restore_geometry(self) -> None:
        saved = self._settings.value("window/geometry")
        if saved is not None and self.restoreGeometry(saved):
            self._keep_on_screen()

    def _keep_on_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        frame = self.frameGeometry()
        if any(screen.availableGeometry().intersects(frame) for screen in screens):
            return
        screen = QGuiApplication.primaryScreen() or screens[0]
        self.move(screen.availableGeometry().center() - self.rect().center())

    def refresh_all(self) -> None:
        self.header.set_refreshing(True)
        self.adb.refresh()
        self.kde.refresh()
        QTimer.singleShot(650, lambda: self.header.set_refreshing(False))
        self._flash("Checking phone and KDE Connect…")

    def _on_phone_snapshot(self, snapshot: PhoneSnapshot) -> None:
        self._adb_snapshot = snapshot
        selected = snapshot.selected

        if selected is None:
            if self._mirror_target is not None:
                self._mirror_target = None
                self.mirror.stop_for_disconnect()
            if self.mirror.serial is None:
                body = snapshot.error or "Connect the phone over USB and enable USB debugging."
                self.mirror.show_placeholder(
                    "Waiting for your phone",
                    body,
                    icon="phone",
                    retry=True,
                )
        elif selected.serial != self._mirror_target:
            self._mirror_target = selected.serial
            self.mirror.start(snapshot)

        if selected is not None and snapshot.rotation is not None:
            self.mirror.set_rotation(snapshot.rotation)

        self._update_header()
        self.notifications.set_snapshot(self._kde_snapshot)

    def _on_kde_snapshot(self, snapshot: KdeSnapshot) -> None:
        self._kde_snapshot = snapshot
        self._update_header()
        self.notifications.set_snapshot(snapshot)
        self._sync_sms(snapshot)

    def _sync_sms(self, snapshot: KdeSnapshot) -> None:
        """Point the SMS client at the phone, but only when its plugin is on."""

        device = snapshot.device
        connected = snapshot.connected and device is not None
        available = False
        path = ""
        if connected:
            if self._sms_plugin_device != device.id:
                plugins = self.kde.phone_plugins()
                self._sms_plugin_device = device.id
                self._sms_plugin_ok = (
                    plugins is None or "kdeconnect_sms" in plugins
                )
            available = self._sms_plugin_ok
            if available:
                path = f"{DAEMON_PATH}/devices/{device.id}"
        else:
            self._sms_plugin_device = ""
            self._sms_plugin_ok = False
        self.sms.set_device(path)
        self.sms_panel.set_phone(
            device.name if device is not None else "",
            connected=connected,
            available=available,
            message=snapshot.message if not (connected and available) else "",
        )

    def _refresh_sms(self) -> None:
        self._sms_plugin_device = ""
        self._sms_plugin_ok = False
        self.kde.refresh()
        self.sms.refresh()

    def _on_sms_changed(self) -> None:
        self.sms_panel.set_conversations(self.sms.conversations())
        self._sync_open_sms_thread()

    def _sync_open_sms_thread(self) -> None:
        thread_id = self.sms_panel.current_thread()
        if thread_id is None:
            return
        conversation = self.sms.conversation(thread_id)
        messages = conversation.messages if conversation is not None else ()
        self.sms_panel.set_thread(
            thread_id, messages, self.sms.has_more(thread_id)
        )

    def _load_sms_thread(self, thread_id: int) -> None:
        self.sms_panel.set_thread_loading(thread_id, True)
        self.sms.load_thread(thread_id)

    def _on_sms_thread_loaded(self, thread_id: int) -> None:
        self._sync_open_sms_thread()
        self.sms_panel.set_thread_loading(thread_id, False)

    def _on_sms_sent(self, thread_id: int) -> None:
        self.sms_panel.send_succeeded(thread_id)
        self._flash("Message sent.")

    def _on_sms_send_failed(self, thread_id: int, message: str) -> None:
        self.sms_panel.send_failed(thread_id, message)
        self._flash(message)

    def _on_sms_error(self, message: str) -> None:
        self.sms_panel.set_error(message)
        self._flash(message)

    def _update_header(self) -> None:
        adb = self._adb_snapshot
        kde = self._kde_snapshot
        adb_device = adb.selected
        kde_device = kde.device

        name = adb.name or (adb_device.display_name if adb_device else None)
        if not name and kde_device:
            name = kde_device.name

        if adb_device is not None:
            connection = adb_device.connection_label
            if kde.connected:
                subtitle = f"{connection} mirror · KDE Connect live"
            elif kde.mode == "unreachable":
                subtitle = f"{connection} mirror · KDE phone unreachable"
            elif kde.available:
                subtitle = f"{connection} mirror · no phone in KDE Connect"
            else:
                subtitle = f"{connection} mirror · KDE Connect not running"
        elif kde.connected:
            connection = "Wi-Fi"
            subtitle = "KDE Connect over Wi-Fi · connect USB to mirror"
        elif kde.mode == "unreachable":
            connection = "Wi-Fi"
            subtitle = "KDE Connect phone unreachable · connect USB to mirror"
        else:
            connection = "Offline"
            subtitle = "Connect over USB to mirror · KDE Connect waiting"

        battery = adb.battery
        charging = adb.charging
        if battery is None:
            battery = kde.battery
            charging = charging or kde.charging

        online = adb_device is not None or kde.connected
        self.header.set_data(
            phone_name=name,
            connection=connection,
            battery=battery,
            charging=charging,
            subtitle=subtitle,
            online=online,
        )
        self.setWindowTitle(f"Phone Link — {name}" if name else "Phone Link")

    def _send_nav(self, action: str) -> None:
        selected = self._adb_snapshot.selected
        if selected is None:
            self._flash("No ADB device is connected.")
            return
        if not self.mirror.is_mirroring:
            self._flash("The mirror is not running.")
            return
        self.adb.send_key(selected.serial, action)
        self.mirror.focus_screen()

    def _retry_mirror(self) -> None:
        self.adb.refresh()
        self._flash("Retrying the mirror…")
        snapshot = self._adb_snapshot
        if snapshot.selected is not None:
            self._mirror_target = snapshot.selected.serial
            self.mirror.start(snapshot)
        else:
            self.mirror.show_placeholder(
                "Waiting for your phone",
                snapshot.error or "Connect the phone over USB and enable USB debugging.",
                icon="phone",
                retry=True,
            )

    def _dismiss_notification(self, notification_id: str) -> None:
        self.kde.dismiss_notification(notification_id)

    def _clear_history(self) -> None:
        self.kde.clear_history()

    def _open_wifi_pairing(self) -> None:
        adb_path = self.report.adb_path
        if not adb_path:
            self._flash("adb is not installed, so wireless debugging cannot be paired.")
            return
        dialog = WifiConnectDialog(adb_path, self)
        dialog.connected.connect(self._on_wifi_connected)
        dialog.exec()

    def _on_wifi_connected(self, message: str) -> None:
        self.adb.refresh()
        selected = self._adb_snapshot.selected
        if selected is not None and not selected.is_wireless:
            self._flash(f"{message} Unplug USB to mirror over Wi-Fi.")
        else:
            self._flash(message)

    def _send_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        if not text:
            self._flash("The clipboard is empty — copy something first.")
            return
        if len(text) > 100_000:
            self._flash("That clipboard is too large to send (over 100,000 characters).")
            return
        if self.kde.send_clipboard(text):
            name = self._kde_snapshot.device.name if self._kde_snapshot.device else "the phone"
            self._flash(f"Clipboard sent to {name}.")

    def _send_reply(self, notification_id: str, message: str) -> None:
        # Failures are reported by the client's actionError signal.
        if self.kde.send_reply(notification_id, message):
            self._flash("Reply sent to the phone.")

    def _set_auto_clipboard(self, checked: bool) -> None:
        self._clipboard_sync_enabled = bool(checked)
        self.header.set_auto_clipboard(self._clipboard_sync_enabled)
        self._settings.setValue("clipboard/auto_sync", self._clipboard_sync_enabled)
        if not self._clipboard_sync_enabled:
            self._clipboard_timer.stop()

    def _on_clipboard_changed(self) -> None:
        if self._clipboard_sync_enabled:
            # Clipboard managers often write twice; settle before sending.
            self._clipboard_timer.start()

    def _push_clipboard(self) -> None:
        if not self._clipboard_sync_enabled:
            return
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        now = time.monotonic()
        if not self._clipboard_gate.should_send(text, now):
            return
        if not self._kde_snapshot.connected:
            return
        if self.kde.send_clipboard(text):
            self._clipboard_gate.note_sent(text, now)

    def _send_files(self, paths: list[str]) -> None:
        files = [path for path in paths if os.path.isfile(path)]
        if not files:
            self._flash("Only files can be sent to the phone.")
            return
        name = self._kde_snapshot.device.name if self._kde_snapshot.device else "the phone"
        if self.kde.share_files(files):
            noun = "file" if len(files) == 1 else "files"
            self._flash(f"Sending {len(files)} {noun} to {name}…")

    def _media_action(self, action: str) -> None:
        self.kde.media_action(action)

    def _media_seek(self, position: int) -> None:
        self.kde.media_seek(position)

    def _open_phone_files(self) -> None:
        if not self._kde_snapshot.connected:
            self._flash("Connect the phone through KDE Connect to browse its files.")
            return
        if self.kde.open_phone_storage():
            self._flash("Preparing the phone's files…")

    def _phone_label(self) -> str:
        device = self._kde_snapshot.device
        return device.name if device is not None else "the phone"

    def _refresh_phone_menu(self) -> None:
        connected = self._kde_snapshot.connected
        self.phone_menu.set_available(
            connected, self.kde.phone_plugins() if connected else None
        )
        self.phone_menu.set_cellular_status(
            self.kde.cellular_status() if connected else None,
            connected=connected,
        )

    def _find_phone(self) -> None:
        if self.kde.ring_phone():
            self._flash(
                f"Ringing {self._phone_label()}… Use Stop ringing to end it."
            )

    def _stop_ringing(self) -> None:
        # KDE Connect has no stop method for Find My Phone; the phone's own
        # "Found it" action is a broadcast receiver, so adb can press it.
        adb_device = self.adb.last_snapshot.selected
        if adb_device is None:
            self._flash(
                "Connect the phone with adb to stop the ring from here, "
                "or tap Found it on the phone."
            )
            return
        desktop_id = self.kde.self_id()
        if not desktop_id:
            self._flash(
                "KDE Connect did not report this computer's id; "
                "tap Found it on the phone."
            )
            return
        self.adb.stop_find_my_phone(adb_device.serial, desktop_id)

    def _ping_phone(self) -> None:
        if self.kde.ping_phone():
            self._flash(f"Pinged {self._phone_label()}.")

    def _open_sms_dialog(self) -> None:
        if not self._kde_snapshot.connected:
            self._flash("Connect the phone through KDE Connect to send a message.")
            return
        dialog = SmsDialog(self._recent_recipients(), self)
        try:
            # A failed send keeps the dialog (and the typed message) so the
            # person can fix the number or plugin and try again.
            while dialog.exec() == QDialog.DialogCode.Accepted:
                addresses = dialog.addresses()
                if self.kde.send_sms(addresses, dialog.message()):
                    self._flash(f"Message sent to {', '.join(addresses)}.")
                    self._remember_recipients(addresses)
                    break
                dialog.show_error(
                    self.kde.last_error or "KDE Connect did not send the message."
                )
        finally:
            dialog.deleteLater()

    def _recent_recipients(self) -> list[str]:
        value = self._settings.value("sms/recipients", [])
        if isinstance(value, str):
            return [value] if value else []
        if not value:
            return []
        try:
            return [str(item) for item in value if str(item)][:5]
        except TypeError:
            return []

    def _remember_recipients(self, addresses: list[str]) -> None:
        recent = list(addresses)
        for old in self._recent_recipients():
            if old not in recent:
                recent.append(old)
        self._settings.setValue("sms/recipients", recent[:5])

    def _open_phone_volume(self) -> None:
        if not self._kde_snapshot.connected:
            self._flash("Connect the phone through KDE Connect to change its volume.")
            return
        sinks = self.kde.phone_sinks()
        if not sinks:
            self._flash(
                "This phone does not offer volume control to the computer. "
                "Android's KDE Connect volume plugin only works the other way."
            )
            return
        dialog = VolumeDialog(sinks, self)
        dialog.volumeChanged.connect(self.kde.set_phone_volume)
        dialog.mutedChanged.connect(self.kde.set_phone_muted)
        self._volume_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._volume_dialog = None
            dialog.deleteLater()

    def _apply_phone_volume(self, name: str, volume: int) -> None:
        if self._volume_dialog is not None:
            self._volume_dialog.apply_volume(name, volume)

    def _apply_phone_muted(self, name: str, muted: bool) -> None:
        if self._volume_dialog is not None:
            self._volume_dialog.apply_muted(name, muted)

    def _open_phone_messages(self) -> None:
        if self.kde.open_phone_messages():
            self._flash(f"Opening messages on {self._phone_label()}…")

    def _on_phone_storage_ready(self, path: str) -> None:
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._flash("Phone storage opened — drag files out to copy them.")
        else:
            self._flash(f"Phone files are mounted at {path}")

    def _flash(self, message: str) -> None:
        self.statusBar().showMessage(message, 7_000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()
        self._clipboard_timer.stop()
        self._adb_timer.stop()
        self.mirror.stop()
        self.adb.stop()
        self.agent_panel.shutdown()
        self.sms.stop()
        self.kde.stop()
        event.accept()
