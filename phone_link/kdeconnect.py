"""KDE Connect client over its existing D-Bus API.

Phone Link does not speak the KDE Connect network protocol.  It talks to the
already-running kdeconnectd exactly as other Plasma components do.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusInterface,
    QDBusMessage,
    QDBusPendingCallWatcher,
    QDBusServiceWatcher,
    QDBusVariant,
)

from .models import KdeDevice, KdeSnapshot, MediaState, NotificationItem, PhoneSink
from .notification_store import (
    DEFAULT_LIMIT,
    delete_history,
    load_history,
    save_history,
)

SERVICE = "org.kde.kdeconnect"
DAEMON_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"
CLIPBOARD_IFACE = "org.kde.kdeconnect.device.clipboard"
SHARE_IFACE = "org.kde.kdeconnect.device.share"
MEDIA_IFACE = "org.kde.kdeconnect.device.mprisremote"
FILES_IFACE = "org.kde.kdeconnect.device.sftp"
FINDMYPHONE_IFACE = "org.kde.kdeconnect.device.findmyphone"
PING_IFACE = "org.kde.kdeconnect.device.ping"
SMS_IFACE = "org.kde.kdeconnect.device.sms"
REMOTE_VOLUME_IFACE = "org.kde.kdeconnect.device.remotesystemvolume"
CONNECTIVITY_IFACE = "org.kde.kdeconnect.device.connectivity_report"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

_MEDIA_ACTIONS = {
    "Play",
    "Pause",
    "PlayPause",
    "Stop",
    "Next",
    "Previous",
    "FastForward",
    "Rewind",
}


class KdeConnectClient(QObject):
    snapshotChanged = Signal(object)
    mediaChanged = Signal(object)
    phoneStorageReady = Signal(str)
    phoneVolumeChanged = Signal(str, int)
    phoneMutedChanged = Signal(str, bool)
    actionError = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        history_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus()
        self._snapshot = KdeSnapshot(mode="offline", message="Connecting to KDE Connect…")
        self._daemon_iface: QDBusInterface | None = None
        self._device_iface: QDBusInterface | None = None
        self._notifications_iface: QDBusInterface | None = None
        self._battery_iface: QDBusInterface | None = None
        self._media_iface: QDBusInterface | None = None
        self._volume_iface: QDBusInterface | None = None
        self._mount_iface: QDBusInterface | None = None
        self._selected: KdeDevice | None = None
        self._ifaces: dict[tuple[str, str], QDBusInterface] = {}
        self._seen_order: dict[str, int] = {}
        self._seen_at: dict[str, datetime] = {}
        self._next_order = 0
        self._started = False
        # The last reason a person-facing action failed, for callers that
        # want to show it inline (the SMS dialog) rather than only in the
        # status bar.  ``actionError`` still carries every failure.
        self.last_error = ""

        # A notification that the phone clears while this app is open moves
        # under "Recent", so a heads-up that vanishes in seconds (common with
        # grouped Gmail notifications) can still be read.  Recent items are
        # saved locally (owner-only file, newest 30) so they survive a restart.
        self._history_path = history_path
        self._history_limit = DEFAULT_LIMIT
        self._last_items: dict[str, NotificationItem] = {}
        self._history: dict[str, NotificationItem] = {
            item.id: item
            for item in load_history(limit=self._history_limit, path=self._history_path)
        }

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(20_000)
        self._refresh_timer.timeout.connect(self.refresh)

        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(80)
        self._coalesce.timeout.connect(self.refresh)

        # Media position advances while a player is running, and the phone does
        # not emit a signal for every second, so poll while it is playing.
        self._media = MediaState()
        self._media_timer = QTimer(self)
        self._media_timer.setInterval(1000)
        self._media_timer.timeout.connect(self._poll_media)
        self._media_signal_timer = QTimer(self)
        self._media_signal_timer.setSingleShot(True)
        self._media_signal_timer.setInterval(120)
        self._media_signal_timer.timeout.connect(self._poll_media)

        self._watcher: QDBusServiceWatcher | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if not self._bus.isConnected():
            self._set_offline("KDE Connect is unavailable because the session D-Bus is not connected.")
            return

        self._watcher = QDBusServiceWatcher(
            SERVICE,
            self._bus,
            QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration,
            self,
        )
        self._watcher.serviceRegistered.connect(self._on_service_registered)
        self._watcher.serviceUnregistered.connect(self._on_service_unregistered)

        self._connect_daemon()
        self.refresh()
        self._refresh_timer.start()

    def stop(self) -> None:
        self._started = False
        self._refresh_timer.stop()
        self._coalesce.stop()
        self._media_timer.stop()
        self._media_signal_timer.stop()
        self._drop_device_interfaces()
        if self._watcher:
            self._watcher.deleteLater()
            self._watcher = None
        for iface in self._ifaces.values():
            try:
                iface.deleteLater()
            except RuntimeError:
                pass
        self._ifaces.clear()
        self._daemon_iface = None

    def refresh(self) -> None:
        if not self._started:
            return
        if not self._bus.isConnected():
            self._set_offline("The session D-Bus is not available.")
            return

        if not self._service_available():
            self._drop_device_interfaces()
            self._daemon_iface = None
            self._set_offline("KDE Connect is not running. Start kdeconnectd or open KDE Connect.")
            return

        if not self._connect_daemon():
            self._set_offline("KDE Connect is installed, but its D-Bus daemon is not responding.")
            return

        reply, error = self._call(DAEMON_PATH, DAEMON_IFACE, "devices")
        if reply is None:
            self._set_offline(error or "Could not ask KDE Connect for its device list.")
            return
        device_ids = _first_list(reply.arguments())

        devices: list[KdeDevice] = []
        for device_id in device_ids:
            device_id = str(device_id)
            path = self._device_path(device_id)
            name = self._get_prop(path, DEVICE_IFACE, "name", device_id)
            kind = self._get_prop(path, DEVICE_IFACE, "type", "phone")
            reachable = bool(self._get_prop(path, DEVICE_IFACE, "isReachable", False))
            paired = bool(self._get_prop(path, DEVICE_IFACE, "isPaired", False))
            devices.append(
                KdeDevice(
                    id=device_id,
                    name=str(name) if name not in (None, "") else device_id,
                    kind=str(kind) if kind not in (None, "") else "phone",
                    reachable=reachable,
                    paired=paired,
                )
            )

        selected = self._choose_device(devices)
        if selected is None:
            self._drop_device_interfaces()
            if not devices:
                message = "No phone found. Pair it in KDE Connect first."
            else:
                message = "KDE Connect knows a device, but no paired, reachable phone is available."
            self._snapshot = KdeSnapshot(
                available=True,
                mode="no-device",
                message=message,
            )
            self.snapshotChanged.emit(self._snapshot)
            return

        if self._selected is None or self._selected.id != selected.id:
            self._drop_device_interfaces()
            self._seen_order.clear()
            self._seen_at.clear()
            self._next_order = 0

        # Device details can change without the ID changing.
        self._selected = selected
        self._connect_device(selected)

        if not selected.paired:
            self._snapshot = KdeSnapshot(
                available=True,
                device=selected,
                mode="no-device",
                message="This phone is not paired with KDE Connect yet.",
            )
            self.snapshotChanged.emit(self._snapshot)
            return
        if not selected.reachable:
            self._apply_media(MediaState())
            self._snapshot = KdeSnapshot(
                available=True,
                device=selected,
                mode="unreachable",
                message="The phone is paired but not reachable. Check that both devices are on the same network.",
            )
            self.snapshotChanged.emit(self._snapshot)
            return

        battery, charging = self._read_battery(selected)
        notifications, notif_error = self._read_notifications(selected)
        self._apply_media(self._read_media(selected))
        history = self._history_items()
        mode = "ready" if notifications else "no-notifications"
        message = (
            "New notifications from your phone appear here."
            if notifications
            else "No active notifications right now."
        )
        if notif_error:
            mode = "no-notifications"
            message = notif_error
        self._snapshot = KdeSnapshot(
            available=True,
            device=selected,
            battery=battery,
            charging=charging,
            notifications=tuple(notifications),
            history=history,
            mode=mode,
            message=message,
            error=notif_error,
        )
        self.snapshotChanged.emit(self._snapshot)

    def dismiss_notification(self, notification_id: str) -> None:
        if not self._selected:
            return
        path = self._notifications_path(self._selected.id) + "/" + notification_id
        reply, error = self._call(path, NOTIFICATION_IFACE, "dismiss")
        if reply is None:
            self.actionError.emit(error or "KDE Connect did not dismiss that notification.")
            return
        # The person dismissed it deliberately; do not archive it as "Recent".
        # Removing it from _last_items is enough: the stale sweep only
        # archives items it still has a cached copy of.
        self._last_items.pop(notification_id, None)
        removed = self._history.pop(notification_id, None) is not None
        self._seen_order.pop(notification_id, None)
        self._seen_at.pop(notification_id, None)
        if removed:
            self._save_history()
        self._schedule_refresh()

    def clear_history(self) -> None:
        if not self._history:
            return
        self._history.clear()
        delete_history(self._history_path)
        self._schedule_refresh()

    def send_clipboard(self, text: str) -> bool:
        if not self._selected or not self._selected.reachable:
            self.actionError.emit("No reachable phone to send the clipboard to.")
            return False
        path = self._device_path(self._selected.id) + "/clipboard"
        reply, error = self._call(path, CLIPBOARD_IFACE, "sendClipboard", text)
        if reply is None:
            self.actionError.emit(
                error or "KDE Connect did not accept the clipboard. Check its clipboard plugin."
            )
            return False
        return True

    def ring_phone(self) -> bool:
        """Make the phone ring at full volume, via KDE Connect's Find My Phone."""

        if not self._phone_ready("ring"):
            return False
        path = self._device_path(self._selected.id) + "/findmyphone"
        reply, error = self._call(path, FINDMYPHONE_IFACE, "ring")
        if reply is None:
            self.actionError.emit(
                error
                or "KDE Connect could not ring the phone. Enable Find My Phone on both devices."
            )
            return False
        return True

    def ping_phone(self, message: str = "") -> bool:
        """Show a ping notification on the phone."""

        if not self._phone_ready("ping"):
            return False
        path = self._device_path(self._selected.id) + "/ping"
        text = str(message or "").strip()
        if text:
            reply, error = self._call(path, PING_IFACE, "sendPing", text)
        else:
            reply, error = self._call(path, PING_IFACE, "sendPing")
        if reply is None:
            self.actionError.emit(
                error
                or "KDE Connect could not ping the phone. Enable the Ping plugin on both devices."
            )
            return False
        return True

    def send_sms(self, addresses: list[str], message: str) -> bool:
        """Send a text message from the phone through KDE Connect's SMS plugin.

        PySide6 cannot marshal this method's exact signature: it takes
        ``avsavx`` (a variant list of address structs, the text, attachment
        URLs and a SIM id), and PySide6 sends an empty list as ``as`` and
        ``-1`` as ``i``.  gdbus lets us spell the GVariant types exactly, and
        glib2 supplies it on every desktop that runs KDE Connect.
        """

        self.last_error = ""

        def fail(reason: str) -> bool:
            self.last_error = reason
            self.actionError.emit(reason)
            return False

        if not self._phone_ready("message"):
            self.last_error = "No reachable phone to message."
            return False
        numbers = [
            str(address).strip() for address in addresses if str(address).strip()
        ]
        text = str(message or "")
        if not numbers:
            return fail("Add at least one phone number for the message.")
        if not text.strip():
            return fail("Type a message before sending it.")
        gdbus = shutil.which("gdbus")
        if not gdbus:
            return fail(
                "Sending a text message needs gdbus (from glib2). Install it, "
                "or send the message from KDE Connect's SMS app."
            )

        argv = gdbus_sms_argv(
            gdbus,
            self._device_path(self._selected.id) + "/sms",
            numbers,
            text,
        )
        try:
            process = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return fail(f"Could not send the message: {exc}")
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "").strip()
            if "No such" in detail or "not provided" in detail:
                detail = (
                    "The phone does not expose KDE Connect's SMS plugin. "
                    "Update KDE Connect on the phone and enable SMS."
                )
            return fail(detail or "KDE Connect did not accept the message.")
        return True

    def open_phone_messages(self) -> bool:
        """Open the phone's own messaging app."""

        if not self._phone_ready("open messages on"):
            return False
        path = self._device_path(self._selected.id) + "/sms"
        reply, error = self._call(path, SMS_IFACE, "launchApp")
        if reply is None:
            self.actionError.emit(
                error
                or "KDE Connect could not open the phone's messages. Enable the SMS plugin."
            )
            return False
        return True

    def phone_sinks(self) -> tuple[PhoneSink, ...]:
        """The phone's audio outputs, or an empty tuple if it does not report any."""

        if not self._selected or not self._selected.reachable:
            return ()
        path = self._device_path(self._selected.id) + "/remotesystemvolume"
        raw = self._get_prop(path, REMOTE_VOLUME_IFACE, "sinks", b"")
        return sinks_from_json(raw)

    def set_phone_volume(self, sink: str, volume: int) -> bool:
        if not self._phone_ready("change the volume"):
            return False
        path = self._device_path(self._selected.id) + "/remotesystemvolume"
        reply, error = self._call(
            path, REMOTE_VOLUME_IFACE, "sendVolume", str(sink), int(volume)
        )
        if reply is None:
            self.actionError.emit(
                error
                or "KDE Connect could not change the phone's volume. "
                "Enable Remote System Volume on both devices."
            )
            return False
        return True

    def set_phone_muted(self, sink: str, muted: bool) -> bool:
        if not self._phone_ready("change the volume"):
            return False
        path = self._device_path(self._selected.id) + "/remotesystemvolume"
        reply, error = self._call(
            path, REMOTE_VOLUME_IFACE, "sendMuted", str(sink), bool(muted)
        )
        if reply is None:
            self.actionError.emit(
                error
                or "KDE Connect could not mute the phone. "
                "Enable Remote System Volume on both devices."
            )
            return False
        return True

    def self_id(self) -> str:
        """This computer's KDE Connect id, as the phone knows it."""

        reply, _error = self._call(DAEMON_PATH, DAEMON_IFACE, "selfId")
        if reply is None:
            return ""
        arguments = reply.arguments()
        return str(arguments[0]) if arguments else ""

    def phone_plugins(self) -> set[str] | None:
        """Plugin ids the KDE Connect daemon has loaded for the phone.

        ``None`` means the daemon did not answer, so callers should not treat
        the empty set as "nothing is available".  The ids carry KDE Connect's
        ``kdeconnect_`` prefix (``kdeconnect_sms``, ...).
        """

        if not self._selected or not self._selected.reachable:
            return set()
        reply, _error = self._call(
            self._device_path(self._selected.id), DEVICE_IFACE, "loadedPlugins"
        )
        if reply is None:
            return None
        arguments = reply.arguments()
        if not arguments:
            return set()
        try:
            return {str(name) for name in arguments[0]}
        except TypeError:
            return None

    def cellular_status(self) -> tuple[str, int] | None:
        """The phone's mobile network type and signal strength (0–4)."""

        if not self._selected or not self._selected.reachable:
            return None
        path = self._device_path(self._selected.id) + "/connectivity_report"
        network = str(
            self._get_prop(path, CONNECTIVITY_IFACE, "cellularNetworkType", "") or ""
        ).strip()
        strength = _int_or(
            self._get_prop(path, CONNECTIVITY_IFACE, "cellularNetworkStrength", -1),
            -1,
        )
        if not network or strength < 0:
            return None
        return network, strength

    def _phone_ready(self, action: str) -> bool:
        if not self._selected or not self._selected.reachable:
            self.actionError.emit(f"No reachable phone to {action}.")
            return False
        return True

    def share_files(self, paths: list[str]) -> bool:
        if not paths:
            return False
        if not self._selected or not self._selected.reachable:
            self.actionError.emit("No reachable phone to send files to.")
            return False
        urls = [
            QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
            for path in paths
        ]
        path = self._device_path(self._selected.id) + "/share"
        reply, error = self._call(path, SHARE_IFACE, "shareUrls", urls)
        if reply is None:
            self.actionError.emit(
                error or "KDE Connect did not accept those files. Check its Share plugin."
            )
            return False
        return True

    def send_reply(self, notification_id: str, message: str) -> bool:
        if not self._selected:
            self.actionError.emit("No phone is connected through KDE Connect.")
            return False
        path = self._notifications_path(self._selected.id) + "/" + notification_id
        reply, error = self._call(path, NOTIFICATION_IFACE, "sendReply", message)
        if reply is None:
            self.actionError.emit(error or "KDE Connect could not send that reply.")
            return False
        return True

    def media_action(self, action: str) -> bool:
        """Send a play/pause/next/previous command to the phone's player."""

        if action not in _MEDIA_ACTIONS:
            self.actionError.emit(f"Unknown media action: {action}")
            return False
        if not self._selected or not self._selected.reachable:
            self.actionError.emit("No reachable phone to control media on.")
            return False
        reply, error = self._call(
            self._media_path(self._selected.id), MEDIA_IFACE, "sendAction", action
        )
        if reply is None:
            self.actionError.emit(error or "The phone did not accept that media command.")
            return False
        self._media_signal_timer.start()
        return True

    def media_seek(self, position_ms: int) -> bool:
        """Move the phone's player to a position, in milliseconds."""

        if not self._selected or not self._selected.reachable:
            self.actionError.emit("No reachable phone to control media on.")
            return False
        if not self._media.can_seek:
            return False
        target = _non_negative_int(position_ms)
        if self._media.length > 0:
            target = min(target, self._media.length)
        offset = target - self._media.position
        if abs(offset) < 250:
            return True
        reply, error = self._call(
            self._media_path(self._selected.id), MEDIA_IFACE, "seek", int(offset)
        )
        if reply is None:
            self.actionError.emit(error or "The phone did not accept that seek.")
            return False
        self._media_signal_timer.start()
        return True

    def open_phone_storage(self) -> bool:
        """Mount the phone's storage over KDE Connect, then report its path.

        The mount is done by the KDE Connect daemon over SFTP; ``phoneStorageReady``
        fires once it is available locally, so the window can open it in the
        desktop's file manager.
        """

        if not self._selected or not self._selected.reachable:
            self.actionError.emit("KDE Connect needs a reachable phone to browse its files.")
            return False
        path = self._device_path(self._selected.id) + "/sftp"
        iface = self._interface(path, FILES_IFACE)
        if iface is None:
            self.actionError.emit("KDE Connect's file-transfer plugin is unavailable.")
            return False

        reply = iface.call("isMounted")
        if reply.type() != QDBusMessage.MessageType.ErrorMessage:
            arguments = reply.arguments()
            if arguments and bool(arguments[0]):
                self._finish_storage(iface)
                return True

        self._mount_iface = iface
        pending = iface.asyncCall("mountAndWait")
        watcher = QDBusPendingCallWatcher(pending, self)
        watcher.finished.connect(self._on_mount_finished)
        return True

    def _on_mount_finished(self, watcher: QDBusPendingCallWatcher) -> None:
        iface = self._mount_iface
        self._mount_iface = None
        watcher.deleteLater()

        reply = watcher.reply()
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            self.actionError.emit(
                "Could not mount the phone's files: "
                + (reply.errorMessage() or "KDE Connect refused the request.")
            )
            return

        arguments = reply.arguments()
        if not (arguments and bool(arguments[0])):
            detail = ""
            if iface is not None:
                error_reply = iface.call("getMountError")
                if (
                    error_reply.type() != QDBusMessage.MessageType.ErrorMessage
                    and error_reply.arguments()
                ):
                    detail = str(error_reply.arguments()[0] or "")
            message = "Could not mount the phone's files."
            if detail:
                message += f" {detail}"
            self.actionError.emit(message)
            return

        self._finish_storage(iface)

    def _finish_storage(self, iface: QDBusInterface | None) -> None:
        if iface is None:
            return
        reply = iface.call("mountPoint")
        if reply.type() == QDBusMessage.MessageType.ErrorMessage or not reply.arguments():
            self.actionError.emit("KDE Connect mounted the phone but did not report where.")
            return
        mount_point = str(reply.arguments()[0] or "")
        if not mount_point:
            self.actionError.emit("KDE Connect mounted the phone but reported an empty path.")
            return
        # sshfs can take a moment to serve the mounted tree, so the nested
        # Android path may not resolve on the first look.
        self._emit_storage_when_ready(mount_point, 0)

    def _emit_storage_when_ready(self, mount_point: str, attempts: int) -> None:
        internal = os.path.join(mount_point, "storage", "emulated", "0")
        if os.path.isdir(internal):
            self.phoneStorageReady.emit(internal)
            return
        if attempts >= 6:
            self.phoneStorageReady.emit(mount_point)
            return
        QTimer.singleShot(
            250,
            self,
            lambda: self._emit_storage_when_ready(mount_point, attempts + 1),
        )

    def _poll_media(self) -> None:
        if not self._started or self._selected is None or not self._selected.reachable:
            return
        self._apply_media(self._read_media(self._selected))

    def _read_media(self, device: KdeDevice) -> MediaState:
        path = self._media_path(device.id)

        def get(name: str, default=None):
            return self._get_prop(path, MEDIA_IFACE, name, default)

        return media_state_from(get)

    def _apply_media(self, state: MediaState) -> None:
        if state == self._media:
            return
        self._media = state
        if state.is_playing:
            if not self._media_timer.isActive():
                self._media_timer.start()
        else:
            self._media_timer.stop()
        self.mediaChanged.emit(state)

    @Slot()
    def _on_media_properties_changed(self) -> None:
        self._media_signal_timer.start()

    @Slot(str, int)
    def _on_phone_volume_changed(self, name: str, volume: int) -> None:
        self.phoneVolumeChanged.emit(str(name), _int_or(volume, 0))

    @Slot(str, bool)
    def _on_phone_muted_changed(self, name: str, muted: bool) -> None:
        self.phoneMutedChanged.emit(str(name), bool(muted))

    def _schedule_refresh(self) -> None:
        if self._started:
            self._coalesce.start()

    @Slot(str)
    def _on_service_registered(self, _service: str) -> None:
        self._connect_daemon()
        self._schedule_refresh()

    @Slot(str)
    def _on_service_unregistered(self, _service: str) -> None:
        self._drop_device_interfaces()
        self._daemon_iface = None
        self._set_offline("KDE Connect was closed. Start it again to resume notification sync.")

    def _connect_daemon(self) -> bool:
        if self._daemon_iface is not None:
            try:
                if self._daemon_iface.isValid():
                    return True
            except RuntimeError:
                pass
            self._daemon_iface = None

        iface = self._interface(DAEMON_PATH, DAEMON_IFACE)
        if iface is None:
            return False
        self._daemon_iface = iface
        for signal_name, slot in (
            ("deviceAdded", self._on_device_changed),
            ("deviceRemoved", self._on_device_changed),
            ("deviceListChanged", self._on_device_list_changed),
        ):
            try:
                getattr(iface, signal_name).connect(slot)
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            iface.deviceVisibilityChanged.connect(self._on_device_visibility)
        except (AttributeError, RuntimeError, TypeError):
            pass
        return True

    def _connect_device(self, device: KdeDevice) -> None:
        path = self._device_path(device.id)
        if self._device_iface is None:
            iface = self._interface(path, DEVICE_IFACE)
            self._device_iface = iface
            if iface is not None:
                for signal_name, slot in (
                    ("nameChanged", self._on_device_name_changed),
                    ("reachableChanged", self._on_reachable_changed),
                    ("pairStateChanged", self._on_pair_state_changed),
                ):
                    try:
                        getattr(iface, signal_name).connect(slot)
                    except (AttributeError, RuntimeError, TypeError):
                        pass

        notif_path = self._notifications_path(device.id)
        if self._notifications_iface is None:
            iface = self._interface(notif_path, NOTIFICATIONS_IFACE)
            self._notifications_iface = iface
            if iface is not None:
                for signal_name in ("notificationPosted", "notificationRemoved", "notificationUpdated"):
                    try:
                        getattr(iface, signal_name).connect(self._on_notification_changed)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                try:
                    iface.allNotificationsRemoved.connect(self._on_all_notifications_removed)
                except (AttributeError, RuntimeError, TypeError):
                    pass

        battery_path = path + "/battery"
        if self._battery_iface is None:
            iface = self._interface(battery_path, BATTERY_IFACE)
            self._battery_iface = iface
            if iface is not None:
                try:
                    iface.refreshed.connect(self._on_battery_refreshed)
                except (AttributeError, RuntimeError, TypeError):
                    pass

        if self._media_iface is None:
            iface = self._interface(path + "/mprisremote", MEDIA_IFACE)
            self._media_iface = iface
            if iface is not None:
                try:
                    iface.propertiesChanged.connect(self._on_media_properties_changed)
                except (AttributeError, RuntimeError, TypeError):
                    pass

        # The phone reports volume changes it made itself (the hardware keys,
        # another app), so an open volume dialog stays truthful.
        if self._volume_iface is None:
            iface = self._interface(path + "/remotesystemvolume", REMOTE_VOLUME_IFACE)
            self._volume_iface = iface
            if iface is not None:
                for signal_name, slot in (
                    ("volumeChanged", self._on_phone_volume_changed),
                    ("mutedChanged", self._on_phone_muted_changed),
                ):
                    try:
                        getattr(iface, signal_name).connect(slot)
                    except (AttributeError, RuntimeError, TypeError):
                        pass

    @Slot(str)
    def _on_device_changed(self, _device_id: str = "") -> None:
        self._schedule_refresh()

    @Slot()
    def _on_device_list_changed(self) -> None:
        self._schedule_refresh()

    @Slot(str, bool)
    def _on_device_visibility(self, _device_id: str = "", _visible: bool = False) -> None:
        self._schedule_refresh()

    @Slot(str)
    def _on_device_name_changed(self, _name: str = "") -> None:
        self._schedule_refresh()

    @Slot(bool)
    def _on_reachable_changed(self, _reachable: bool = False) -> None:
        self._schedule_refresh()

    @Slot(int)
    def _on_pair_state_changed(self, _state: int = 0) -> None:
        self._schedule_refresh()

    @Slot(str)
    def _on_notification_changed(self, _notification_id: str = "") -> None:
        self._schedule_refresh()

    @Slot()
    def _on_all_notifications_removed(self) -> None:
        self._seen_order.clear()
        self._seen_at.clear()
        self._schedule_refresh()

    @Slot(bool, int)
    def _on_battery_refreshed(self, charging: bool, charge: int) -> None:
        if not self._selected:
            return
        snapshot = self._snapshot
        self._snapshot = KdeSnapshot(
            available=snapshot.available,
            device=snapshot.device,
            battery=int(charge),
            charging=bool(charging),
            notifications=snapshot.notifications,
            history=snapshot.history,
            mode=snapshot.mode,
            message=snapshot.message,
            error=snapshot.error,
        )
        self.snapshotChanged.emit(self._snapshot)

    def _read_battery(self, device: KdeDevice) -> tuple[int | None, bool]:
        path = self._device_path(device.id) + "/battery"
        charge = self._get_prop(path, BATTERY_IFACE, "charge", None)
        charging = self._get_prop(path, BATTERY_IFACE, "isCharging", False)
        battery = int(charge) if isinstance(charge, (int, float)) and int(charge) >= 0 else None
        return battery, bool(charging)

    def _read_notifications(self, device: KdeDevice) -> tuple[list[NotificationItem], str | None]:
        notif_path = self._notifications_path(device.id)
        reply, error = self._call(notif_path, NOTIFICATIONS_IFACE, "activeNotifications")
        if reply is None:
            return [], f"Notification sync is unavailable: {error}"
        ids = [str(item) for item in _first_list(reply.arguments())]

        active = set(ids)
        history_changed = False
        for stale in tuple(self._seen_order):
            if stale not in active:
                item = self._last_items.pop(stale, None)
                if item is not None:
                    self._history[stale] = item
                    history_changed = True
                self._seen_order.pop(stale, None)
                self._seen_at.pop(stale, None)
        if self._trim_history():
            history_changed = True

        items: list[NotificationItem] = []
        for public_id in ids:
            path = notif_path + "/" + public_id
            app_name = self._get_prop(path, NOTIFICATION_IFACE, "appName", "Phone")
            title = self._get_prop(path, NOTIFICATION_IFACE, "title", "")
            text = self._get_prop(path, NOTIFICATION_IFACE, "text", "")
            ticker = self._get_prop(path, NOTIFICATION_IFACE, "ticker", "")
            icon_path = self._get_prop(path, NOTIFICATION_IFACE, "iconPath", "")
            dismissable = bool(self._get_prop(path, NOTIFICATION_IFACE, "dismissable", False))
            reply_id = self._get_prop(path, NOTIFICATION_IFACE, "replyId", "")

            if public_id not in self._seen_order:
                self._next_order += 1
                self._seen_order[public_id] = self._next_order
                self._seen_at[public_id] = datetime.now()

            item = NotificationItem(
                id=public_id,
                app_name=str(app_name or "Notification"),
                title=str(title or ""),
                text=str(text or ""),
                ticker=str(ticker or ""),
                icon_path=str(icon_path or ""),
                dismissable=dismissable,
                reply_id=str(reply_id or ""),
                received_at=self._seen_at.get(public_id, datetime.now()),
            )
            # If the phone re-posts the same public ID, it is active again,
            # not historical.
            if self._history.pop(public_id, None) is not None:
                history_changed = True
            self._last_items[public_id] = item
            items.append(item)

        if history_changed:
            self._save_history()

        items.sort(key=lambda item: self._seen_order.get(item.id, 0), reverse=True)
        return items, None

    def _trim_history(self) -> bool:
        overflow = len(self._history) - self._history_limit
        if overflow <= 0:
            return False
        oldest = sorted(
            self._history.values(), key=lambda item: item.received_at
        )[:overflow]
        for item in oldest:
            self._history.pop(item.id, None)
        return bool(oldest)

    def _save_history(self) -> None:
        save_history(
            self._history_items(),
            limit=self._history_limit,
            path=self._history_path,
        )

    def _history_items(self) -> tuple[NotificationItem, ...]:
        return tuple(
            sorted(
                self._history.values(),
                key=lambda item: item.received_at,
                reverse=True,
            )
        )

    def _choose_device(self, devices: list[KdeDevice]) -> KdeDevice | None:
        if not devices:
            return None
        phones = [device for device in devices if device.is_phone]
        pool = phones or devices
        ready = [device for device in pool if device.paired and device.reachable]
        if ready:
            return ready[0]
        paired = [device for device in pool if device.paired]
        if paired:
            return paired[0]
        return pool[0]

    def _set_offline(self, message: str) -> None:
        self._snapshot = KdeSnapshot(available=False, mode="offline", message=message)
        self.snapshotChanged.emit(self._snapshot)

    def _service_available(self) -> bool:
        try:
            return bool(self._bus.interface().isServiceRegistered(SERVICE).value())
        except Exception:
            return False

    def _interface(self, path: str, interface: str) -> QDBusInterface | None:
        key = (path, interface)
        cached = self._ifaces.get(key)
        if cached is not None:
            try:
                if cached.isValid():
                    return cached
            except RuntimeError:
                self._ifaces.pop(key, None)
        try:
            iface = QDBusInterface(SERVICE, path, interface, self._bus)
            iface.setTimeout(2500)
            self._ifaces[key] = iface
            return iface
        except Exception:
            return None

    def _call(self, path: str, interface: str, method: str, *args):
        iface = self._interface(path, interface)
        if iface is None:
            return None, "The KDE Connect daemon is not reachable."
        try:
            reply = iface.call(method, *args)
        except Exception as exc:
            return None, f"The KDE Connect D-Bus call failed: {exc}"
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            return None, reply.errorMessage() or f"D-Bus error {reply.errorName()}"
        return reply, None

    def _get_prop(self, path: str, interface: str, name: str, default=None):
        props = self._interface(path, PROPERTIES_IFACE)
        if props is None:
            return default
        try:
            reply = props.call("Get", interface, name)
        except Exception:
            return default
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            return default
        arguments = reply.arguments()
        if not arguments:
            return default
        value = arguments[0]
        if isinstance(value, QDBusVariant):
            value = value.variant()
        return value if value is not None else default

    def _drop_device_interfaces(self) -> None:
        if self._selected is not None:
            prefix = self._device_path(self._selected.id)
            for key in tuple(self._ifaces):
                if key[0] == prefix or key[0].startswith(prefix + "/"):
                    iface = self._ifaces.pop(key, None)
                    if iface is not None:
                        try:
                            iface.deleteLater()
                        except RuntimeError:
                            pass
        self._device_iface = None
        self._notifications_iface = None
        self._battery_iface = None
        self._media_iface = None
        self._volume_iface = None
        self._mount_iface = None
        self._selected = None
        self._media_timer.stop()
        self._media_signal_timer.stop()
        if self._media != MediaState():
            self._media = MediaState()
            self.mediaChanged.emit(self._media)
        self._last_items.clear()
        # Keep Recent across transient KDE Connect/phone disconnects and app
        # restarts; it is cleared only by the panel's Clear button or trimming.

    def _device_path(self, device_id: str) -> str:
        return f"{DAEMON_PATH}/devices/{device_id}"

    def _notifications_path(self, device_id: str) -> str:
        return self._device_path(device_id) + "/notifications"

    def _media_path(self, device_id: str) -> str:
        return self._device_path(device_id) + "/mprisremote"


def media_state_from(getter) -> MediaState:
    """Build a :class:`MediaState` from a name/default property getter.

    Kept separate from the D-Bus code so it can be tested without a daemon.
    """

    players_value = getter("playerList", [])
    if isinstance(players_value, (str, bytes)):
        players = (str(players_value),)
    else:
        try:
            players = tuple(str(item) for item in players_value)
        except TypeError:
            players = ()
    player = str(getter("player", "") or "")
    title = str(getter("title", "") or "")
    length = _non_negative_int(getter("length", 0))
    position = _non_negative_int(getter("position", 0))
    if length > 0:
        position = min(position, length)
    return MediaState(
        available=bool(players or player or title),
        player=player,
        players=players,
        title=title,
        artist=str(getter("artist", "") or ""),
        album=str(getter("album", "") or ""),
        is_playing=bool(getter("isPlaying", False)),
        position=position,
        length=length,
        can_seek=bool(getter("canSeek", False)),
    )


def gdbus_addresses(addresses: list[str]) -> str:
    """Format SMS recipients as the ``av`` of ``(s)`` structs the plugin wants."""

    structs = ", ".join(
        f"<({json.dumps(str(address), ensure_ascii=False)},)>" for address in addresses
    )
    return f"@av [{structs}]"


def gdbus_sms_argv(
    gdbus: str,
    sms_path: str,
    addresses: list[str],
    message: str,
) -> list[str]:
    """Build the gdbus command that calls the SMS plugin with exact types.

    The signature is ``avsavx``.  Attachment URLs stay empty and the SIM id
    is -1, which lets the phone pick its default SIM.
    """

    return [
        gdbus,
        "call",
        "--session",
        "--dest",
        SERVICE,
        "--object-path",
        sms_path,
        "--method",
        f"{SMS_IFACE}.sendSms",
        gdbus_addresses(addresses),
        json.dumps(str(message), ensure_ascii=False),
        "@av []",
        "int64 -1",
    ]


def sinks_from_json(value) -> tuple[PhoneSink, ...]:
    """Parse the Remote System Volume plugin's ``sinks`` JSON property."""

    text = _text_value(value).strip()
    if not text:
        return ()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return ()
    if isinstance(data, dict):
        data = data.get("sinks", [])
    if not isinstance(data, list):
        return ()

    sinks: list[PhoneSink] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        if not name:
            continue
        maximum = _int_or(entry.get("maxVolume"), 100)
        if maximum <= 0:
            maximum = 100
        volume = _int_or(entry.get("volume"), 0)
        sinks.append(
            PhoneSink(
                name=name,
                description=str(entry.get("description", "") or "").strip(),
                max_volume=maximum,
                volume=max(0, min(volume, maximum)),
                muted=bool(entry.get("muted", False)),
            )
        )
    return tuple(sinks)


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    try:
        return bytes(value).decode("utf-8", errors="replace")
    except (TypeError, ValueError):
        return str(value)


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _non_negative_int(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _first_list(arguments) -> list:
    if not arguments:
        return []
    value = arguments[0]
    if isinstance(value, QDBusVariant):
        value = value.variant()
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
