#!/usr/bin/env python3
"""A tiny fake kdeconnectd for Phone Link's integration tests.

Run inside ``dbus-run-session`` so it doesn't collide with the real daemon.
It implements only the D-Bus surface Phone Link consumes.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from urllib.parse import unquote

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

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

DEVICE_ID = "mockphone"
DEVICE_PATH = f"{DAEMON_PATH}/devices/{DEVICE_ID}"
NOTIFICATIONS_PATH = f"{DEVICE_PATH}/notifications"
BATTERY_PATH = f"{DEVICE_PATH}/battery"
CLIPBOARD_PATH = f"{DEVICE_PATH}/clipboard"
SHARE_PATH = f"{DEVICE_PATH}/share"
MEDIA_PATH = f"{DEVICE_PATH}/mprisremote"
SFTP_PATH = f"{DEVICE_PATH}/sftp"
FINDMYPHONE_PATH = f"{DEVICE_PATH}/findmyphone"
PING_PATH = f"{DEVICE_PATH}/ping"
SMS_PATH = f"{DEVICE_PATH}/sms"
REMOTE_VOLUME_PATH = f"{DEVICE_PATH}/remotesystemvolume"
CONNECTIVITY_PATH = f"{DEVICE_PATH}/connectivity_report"

MOUNT_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", "/tmp"), "phone-link-mock-storage"
)
INTERNAL_STORAGE = os.path.join(MOUNT_DIR, "storage", "emulated", "0")

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
BUS = dbus.SessionBus()
NAME = dbus.service.BusName(SERVICE, bus=BUS)


class PropertyObject(dbus.service.Object):
    properties: dict[tuple[str, str], object] = {}

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface_name, property_name):
        try:
            return self.properties[(str(interface_name), str(property_name))]
        except KeyError:
            raise dbus.exceptions.DBusException(
                f"Property {interface_name}.{property_name} was not found",
                name="org.freedesktop.DBus.Error.UnknownProperty",
            )

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface_name):
        prefix = str(interface_name)
        return {
            dbus.String(key): value
            for (iface, key), value in self.properties.items()
            if iface == prefix
        }

    @dbus.service.signal(PROPERTIES_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface_name, changed_properties, invalidated_properties):
        pass


class Daemon(PropertyObject):
    @dbus.service.method(DAEMON_IFACE, in_signature="", out_signature="s")
    def selfId(self):
        return dbus.String("desktopmock")

    @dbus.service.method(DAEMON_IFACE, in_signature="", out_signature="as")
    def devices(self):
        return dbus.Array([dbus.String(DEVICE_ID)], signature="s")

    @dbus.service.method(DAEMON_IFACE, in_signature="bb", out_signature="as")
    def devices_filtered(self, only_reachable, only_paired):
        return self.devices()

    # dbus-python cannot overload Python methods by D-Bus signature.  The app
    # calls devices() with no arguments, which is all this mock needs.

    @dbus.service.method(DAEMON_IFACE, in_signature="b", out_signature="as")
    def devices_reachable(self, only_reachable):
        return self.devices()

    @dbus.service.signal(DAEMON_IFACE, signature="s")
    def deviceAdded(self, device_id):
        pass

    @dbus.service.signal(DAEMON_IFACE, signature="s")
    def deviceRemoved(self, device_id):
        pass

    @dbus.service.signal(DAEMON_IFACE, signature="")
    def deviceListChanged(self):
        pass

    @dbus.service.signal(DAEMON_IFACE, signature="sb")
    def deviceVisibilityChanged(self, device_id, visible):
        pass


class Device(PropertyObject):
    # The ids are what KDE Connect reports through loadedPlugins(); they are
    # the plugin object's file name, not the interface suffix.
    LOADED_PLUGINS = [
        "kdeconnect_findmyphone",
        "kdeconnect_ping",
        "kdeconnect_sms",
        "kdeconnect_remotesystemvolume",
        "kdeconnect_connectivity_report",
    ]

    @dbus.service.method(DEVICE_IFACE, in_signature="", out_signature="as")
    def loadedPlugins(self):
        return dbus.Array([dbus.String(name) for name in self.LOADED_PLUGINS], signature="s")

    @dbus.service.signal(DEVICE_IFACE, signature="s")
    def nameChanged(self, name):
        pass

    @dbus.service.signal(DEVICE_IFACE, signature="b")
    def reachableChanged(self, reachable):
        pass

    @dbus.service.signal(DEVICE_IFACE, signature="i")
    def pairStateChanged(self, state):
        pass


class Battery(PropertyObject):
    @dbus.service.signal(BATTERY_IFACE, signature="bi")
    def refreshed(self, charging, charge):
        pass


class Clipboard(PropertyObject):
    @dbus.service.method(CLIPBOARD_IFACE, in_signature="s", out_signature="")
    def sendClipboard(self, content):
        print(f"CLIPBOARD_RECEIVED {len(str(content))}", flush=True)

    @dbus.service.signal(CLIPBOARD_IFACE, signature="b")
    def autoShareDisabledChanged(self, disabled):
        pass


class Share(PropertyObject):
    @dbus.service.method(SHARE_IFACE, in_signature="as", out_signature="")
    def shareUrls(self, urls):
        names = [unquote(str(url).rsplit("/", 1)[-1]) for url in urls]
        print(f"FILES_RECEIVED {len(names)} {','.join(names)}", flush=True)


class MediaRemote(PropertyObject):
    @dbus.service.method(MEDIA_IFACE, in_signature="s", out_signature="")
    def sendAction(self, action):
        print(f"MEDIA_ACTION {action}", flush=True)

    @dbus.service.method(MEDIA_IFACE, in_signature="i", out_signature="")
    def seek(self, offset):
        print(f"MEDIA_SEEK {int(offset)}", flush=True)

    @dbus.service.method(MEDIA_IFACE, in_signature="", out_signature="")
    def requestPlayerList(self):
        pass

    @dbus.service.signal(MEDIA_IFACE, signature="")
    def propertiesChanged(self):
        pass


class FindMyPhone(PropertyObject):
    @dbus.service.method(FINDMYPHONE_IFACE, in_signature="", out_signature="")
    def ring(self):
        print("FINDMYPHONE_RUNG", flush=True)


class Ping(PropertyObject):
    @dbus.service.method(PING_IFACE, in_signature="", out_signature="")
    def sendPing(self):
        print("PING_RECEIVED", flush=True)


class Sms(PropertyObject):
    @dbus.service.method(SMS_IFACE, in_signature="avsavx", out_signature="")
    def sendSms(self, addresses, text, attachments, sub_id):
        numbers = []
        for entry in addresses:
            try:
                numbers.append(str(entry[0]))
            except (TypeError, IndexError):
                numbers.append(str(entry))
        print(
            f"SMS_RECEIVED {','.join(numbers)} {str(text)} {int(sub_id)} "
            f"attachments={len(attachments)}",
            flush=True,
        )

    @dbus.service.method(SMS_IFACE, in_signature="", out_signature="")
    def launchApp(self):
        print("SMS_APP_LAUNCHED", flush=True)


class RemoteVolume(PropertyObject):
    @dbus.service.method(REMOTE_VOLUME_IFACE, in_signature="si", out_signature="")
    def sendVolume(self, name, volume):
        print(f"PHONE_VOLUME {str(name)} {int(volume)}", flush=True)

    @dbus.service.method(REMOTE_VOLUME_IFACE, in_signature="sb", out_signature="")
    def sendMuted(self, name, muted):
        print(f"PHONE_MUTED {str(name)} {bool(muted)}", flush=True)

    @dbus.service.signal(REMOTE_VOLUME_IFACE, signature="si")
    def volumeChanged(self, name, volume):
        pass

    @dbus.service.signal(REMOTE_VOLUME_IFACE, signature="sb")
    def mutedChanged(self, name, muted):
        pass


class Connectivity(PropertyObject):
    pass


class Sftp(PropertyObject):
    mounted = False

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="b")
    def isMounted(self):
        return dbus.Boolean(self.mounted)

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="b")
    def mountAndWait(self):
        Sftp.mounted = True
        return dbus.Boolean(True)

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="s")
    def mountPoint(self):
        return dbus.String(MOUNT_DIR)

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="a{sv}")
    def getDirectories(self):
        return dbus.Dictionary(
            {INTERNAL_STORAGE: dbus.String("Internal storage")}, signature="sv"
        )

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="")
    def unmount(self):
        Sftp.mounted = False

    @dbus.service.method(FILES_IFACE, in_signature="", out_signature="s")
    def getMountError(self):
        return dbus.String("")


class Notifications(PropertyObject):
    active_ids: list[str] = []
    notification_objects: dict[str, "Notification"] = {}

    @dbus.service.method(NOTIFICATIONS_IFACE, in_signature="", out_signature="as")
    def activeNotifications(self):
        return dbus.Array([dbus.String(i) for i in self.active_ids], signature="s")

    @dbus.service.signal(NOTIFICATIONS_IFACE, signature="s")
    def notificationPosted(self, public_id):
        pass

    @dbus.service.signal(NOTIFICATIONS_IFACE, signature="s")
    def notificationRemoved(self, public_id):
        pass

    @dbus.service.signal(NOTIFICATIONS_IFACE, signature="s")
    def notificationUpdated(self, public_id):
        pass

    @dbus.service.signal(NOTIFICATIONS_IFACE, signature="")
    def allNotificationsRemoved(self):
        pass


class Notification(PropertyObject):
    @dbus.service.method(NOTIFICATION_IFACE, in_signature="", out_signature="")
    def dismiss(self):
        public_id = next(
            (key for key, obj in notifications.notification_objects.items() if obj is self),
            None,
        )
        if public_id is not None:
            remove_notification(public_id)

    @dbus.service.method(NOTIFICATION_IFACE, in_signature="s", out_signature="")
    def sendReply(self, message):
        public_id = next(
            (key for key, obj in notifications.notification_objects.items() if obj is self),
            None,
        )
        print(f"REPLY_RECEIVED {public_id} {message}", flush=True)

    @dbus.service.signal(NOTIFICATION_IFACE, signature="")
    def ready(self):
        pass


daemon = Daemon(BUS, DAEMON_PATH)
device = Device(BUS, DEVICE_PATH)
battery = Battery(BUS, BATTERY_PATH)
notifications = Notifications(BUS, NOTIFICATIONS_PATH)
clipboard = Clipboard(BUS, CLIPBOARD_PATH)
share = Share(BUS, SHARE_PATH)
media = MediaRemote(BUS, MEDIA_PATH)
sftp = Sftp(BUS, SFTP_PATH)
findmyphone = FindMyPhone(BUS, FINDMYPHONE_PATH)
ping = Ping(BUS, PING_PATH)
sms = Sms(BUS, SMS_PATH)
remote_volume = RemoteVolume(BUS, REMOTE_VOLUME_PATH)
connectivity = Connectivity(BUS, CONNECTIVITY_PATH)

device.properties = {
    (DEVICE_IFACE, "name"): dbus.String("Pixel 8 Pro"),
    (DEVICE_IFACE, "type"): dbus.String("phone"),
    (DEVICE_IFACE, "isReachable"): dbus.Boolean(True),
    (DEVICE_IFACE, "isPaired"): dbus.Boolean(True),
    (DEVICE_IFACE, "isPairRequested"): dbus.Boolean(False),
    (DEVICE_IFACE, "isPairRequestedByPeer"): dbus.Boolean(False),
    (DEVICE_IFACE, "pairState"): dbus.Int32(2),
    (DEVICE_IFACE, "verificationKey"): dbus.String("mock"),
}
battery.properties = {
    (BATTERY_IFACE, "charge"): dbus.Int32(76),
    (BATTERY_IFACE, "isCharging"): dbus.Boolean(False),
}
notifications.properties = {}
clipboard.properties = {
    (CLIPBOARD_IFACE, "isAutoShareDisabled"): dbus.Boolean(False),
}
share.properties = {}
media.properties = {
    (MEDIA_IFACE, "title"): dbus.String("Mock Song"),
    (MEDIA_IFACE, "artist"): dbus.String("Test Artist"),
    (MEDIA_IFACE, "album"): dbus.String("Mock Album"),
    (MEDIA_IFACE, "player"): dbus.String("Mock Player"),
    (MEDIA_IFACE, "playerList"): dbus.Array(
        [dbus.String("Mock Player")], signature="s"
    ),
    (MEDIA_IFACE, "isPlaying"): dbus.Boolean(True),
    (MEDIA_IFACE, "position"): dbus.Int32(30000),
    (MEDIA_IFACE, "length"): dbus.Int32(200000),
    (MEDIA_IFACE, "canSeek"): dbus.Boolean(True),
    (MEDIA_IFACE, "volume"): dbus.Int32(50),
}
sftp.properties = {}
findmyphone.properties = {}
ping.properties = {}
sms.properties = {}
remote_volume.properties = {
    (REMOTE_VOLUME_IFACE, "sinks"): dbus.ByteArray(
        json.dumps(
            [
                {
                    "name": "music",
                    "description": "Media",
                    "maxVolume": 100,
                    "volume": 35,
                    "muted": False,
                },
                {
                    "name": "call",
                    "description": "Calls",
                    "maxVolume": 20,
                    "volume": 12,
                    "muted": False,
                },
            ]
        ).encode("utf-8")
    ),
}
connectivity.properties = {
    (CONNECTIVITY_IFACE, "cellularNetworkType"): dbus.String("5G"),
    (CONNECTIVITY_IFACE, "cellularNetworkStrength"): dbus.Int32(3),
    (CONNECTIVITY_IFACE, "iconName"): dbus.String("network-cellular-5g"),
    (CONNECTIVITY_IFACE, "isConnected"): dbus.Boolean(True),
}

_NEXT_ID = 0


def add_notification(app_name: str, title: str, text: str) -> str:
    global _NEXT_ID
    public_id = str(_NEXT_ID)
    _NEXT_ID += 1
    path = f"{NOTIFICATIONS_PATH}/{public_id}"
    obj = Notification(BUS, path)
    obj.properties = {
        (NOTIFICATION_IFACE, "internalId"): dbus.String(f"internal-{public_id}"),
        (NOTIFICATION_IFACE, "appName"): dbus.String(app_name),
        (NOTIFICATION_IFACE, "title"): dbus.String(title),
        (NOTIFICATION_IFACE, "text"): dbus.String(text),
        (NOTIFICATION_IFACE, "ticker"): dbus.String(text),
        (NOTIFICATION_IFACE, "iconPath"): dbus.String(""),
        (NOTIFICATION_IFACE, "dismissable"): dbus.Boolean(True),
        (NOTIFICATION_IFACE, "hasIcon"): dbus.Boolean(False),
        (NOTIFICATION_IFACE, "silent"): dbus.Boolean(False),
        (NOTIFICATION_IFACE, "replyId"): dbus.String(f"reply-{public_id}"),
    }
    notifications.notification_objects[public_id] = obj
    notifications.active_ids.append(public_id)
    notifications.notificationPosted(public_id)
    return public_id


def remove_notification(public_id: str) -> None:
    if public_id not in notifications.active_ids:
        return
    notifications.active_ids.remove(public_id)
    obj = notifications.notification_objects.pop(public_id, None)
    if obj is not None:
        obj.remove_from_connection()
    notifications.notificationRemoved(public_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="post a third notification after 3 seconds")
    parser.add_argument(
        "--expire",
        type=float,
        default=0,
        metavar="SECONDS",
        help="remove the first notification after N seconds (simulates the phone clearing it)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    os.makedirs(INTERNAL_STORAGE, exist_ok=True)

    first_id = add_notification("Messages", "Sam", "Are we still on for dinner at 7?")
    add_notification("KDE Connect", "Battery low", "Your phone battery has reached 20%.")
    if args.live:
        GLib.timeout_add(3000, lambda: (add_notification("Calendar", "Stand-up in 10 min", "Design sync • 09:30"), False)[1])
    if args.expire > 0:
        GLib.timeout_add(
            int(args.expire * 1000),
            lambda: (remove_notification(first_id), False)[1],
        )

    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    if not args.quiet:
        print(f"mock kdeconnectd ready on {SERVICE} (device {DEVICE_ID})", flush=True)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
