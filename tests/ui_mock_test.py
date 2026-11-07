#!/usr/bin/env python3
"""End-to-end UI test with a mock phone, mock KDE Connect and fake scrcpy.

Run it on an X11 or XWayland display:

    dbus-run-session -- python3 tests/ui_mock_test.py

The fake scrcpy draws a test pattern, so no real phone content is captured.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"
FAKES = TESTS / "fakes"
SHOT = Path("/tmp/phone-link-ui-mock-app.png")
SHOT_SCREEN = Path("/tmp/phone-link-ui-mock-screen.png")
SHOT_FINAL = Path("/tmp/phone-link-ui-mock.png")
ADB_LOG = Path("/tmp/phone-link-fake-adb.log")
OFFLINE = Path("/tmp/phone-link-fake-adb-offline")
ROTATION_FILE = Path("/tmp/phone-link-fake-adb-rotation")
SCRCPY_KEYS = Path("/tmp/phone-link-fake-scrcpy-keys.log")
DROP_FILE = Path("/tmp/phone-link-drop-test.txt")
CONFIG_DIR = Path("/tmp/phone-link-test-config")
DATA_DIR = Path("/tmp/phone-link-test-data")

# Keep QSettings (and therefore the auto-clipboard toggle) and notification
# history out of the real user directories during the test.
os.environ["XDG_CONFIG_HOME"] = str(CONFIG_DIR)
os.environ["XDG_DATA_HOME"] = str(DATA_DIR)
shutil.rmtree(CONFIG_DIR, ignore_errors=True)
shutil.rmtree(DATA_DIR, ignore_errors=True)

os.environ["PATH"] = f"{FAKES}:{os.environ.get('PATH', '')}"
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ["PHONE_LINK_FAKE_ADB_LOG"] = str(ADB_LOG)
os.environ["PHONE_LINK_FAKE_ADB_OFFLINE_FILE"] = str(OFFLINE)
os.environ["PHONE_LINK_FAKE_ADB_ROTATION_FILE"] = str(ROTATION_FILE)
os.environ["PHONE_LINK_FAKE_SCRCPY_KEYS"] = str(SCRCPY_KEYS)
for path in (ADB_LOG, OFFLINE, ROTATION_FILE, SCRCPY_KEYS):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def wait_for_mock() -> None:
    import dbus

    bus = dbus.SessionBus()
    for _ in range(50):
        if bus.name_has_owner("org.kde.kdeconnect"):
            return
        time.sleep(0.1)
    raise RuntimeError("mock kdeconnectd did not appear on the private session bus")


def main() -> int:
    mock = subprocess.Popen(
        [sys.executable, str(TESTS / "mock_kdeconnect.py"), "--live", "--expire", "2.5"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_mock()

        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import QApplication

        sys.path.insert(0, str(BASE))
        from phone_link.dependencies import check_dependencies
        from phone_link.ui.main_window import MainWindow
        from phone_link.ui.notifications import NotificationCard
        from phone_link.ui.phone_actions import PhoneActionsMenu
        from phone_link.ui.theme import apply_theme

        app = QApplication([])
        app.setOrganizationName("Phone Link Test")
        app.setApplicationName("Phone Link Test")
        apply_theme(app)
        report = check_dependencies(app.platformName())
        print("PLATFORM", app.platformName())
        print("CHECK", [(item.key, item.ok) for item in report.items])
        print("PATHS", report.adb_path, report.scrcpy_path, report.kdeconnect_path)
        for item in report.items:
            if not item.ok:
                print("PROBLEM", item.key, item.detail)

        window = MainWindow(report)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window.resize(1180, 860)
        window.show()
        QTimer.singleShot(250, lambda: window.move(80, 80))

        results: dict[str, object] = {}

        def capture() -> None:
            window.raise_()
            window.activateWindow()
            print("HEADER", window.header.phone_name.text(), "|", window.header.subtitle.text())
            print("HEADER2", window.header.connection.text_label.text(), window.header.battery_text.text())
            print("MIRROR", window.mirror.live_label.text(), window.mirror.is_mirroring)
            print("SESSION", window.mirror._session.is_running, window.mirror._session.is_embedded)
            print("KDE", window._kde_snapshot.mode, len(window._kde_snapshot.notifications))
            for item in window._kde_snapshot.notifications:
                print("NOTIFICATION", item.app_name, "|", item.heading, "|", item.body)
            print("CARDS", window.notifications.cards_layout.count() - 1)
            print(
                "RECENT",
                len(window._kde_snapshot.history),
                "CARDS_RECENT",
                sum(
                    1
                    for index in range(window.notifications.cards_layout.count())
                    if (
                        (widget := window.notifications.cards_layout.itemAt(index).widget())
                        is not None
                        and widget.property("recent")
                    )
                ),
            )
            print("BADGE", window.notifications.count.text(), window.notifications.count.isVisible())

            from phone_link.notification_store import load_history

            saved = load_history()
            print("HISTORY_SAVED", len(saved))

            panel = window.notifications
            print("SEARCH_VISIBLE", panel.search.isVisible())
            panel.search.setText("dinner")
            print(
                "SEARCH_DINNER",
                panel.cards_layout.count() - 1,
                panel.stack.currentWidget() is panel.scroll,
            )
            panel.search.setText("battery low")
            print(
                "SEARCH_BATTERY",
                panel.cards_layout.count() - 1,
                panel.stack.currentWidget() is panel.scroll,
            )
            panel.search.setText("nothing-matches-this")
            print(
                "SEARCH_NONE",
                panel.empty.title.text(),
                "|",
                panel.stack.currentWidget() is panel.empty,
            )
            panel.search.clear()
            print("SEARCH_CLEARED", panel.cards_layout.count() - 1)

            from phone_link.models import KdeSnapshot

            panel.set_snapshot(
                KdeSnapshot(mode="offline", message="KDE Connect test offline")
            )
            print(
                "RETRY_OFFLINE",
                panel.empty.title.text(),
                panel.empty.retry.isVisible(),
            )
            panel.set_snapshot(window._kde_snapshot)

            print(
                "SHORTCUTS",
                ",".join(item.key().toString() for item in window._shortcuts),
            )
            from phone_link.ui.dialogs import ShortcutsDialog

            shortcuts_dialog = ShortcutsDialog(window)
            print("SHORTCUTS_DIALOG", len(shortcuts_dialog.SHORTCUTS))
            shortcuts_dialog.deleteLater()

            def wait_until(predicate, timeout: float = 20.0) -> bool:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    QApplication.processEvents()
                    if predicate():
                        return True
                    time.sleep(0.05)
                return False

            dex = window.mirror.desktop
            popout = window.mirror.popout
            print(
                "DEX_READY",
                dex.isEnabled(),
                window.mirror.is_desktop,
                dex.isChecked(),
                popout.isVisible(),
            )
            dex.click()
            dex_on = wait_until(
                lambda: window.mirror.is_mirroring
                and window.mirror._session._dex
                and window.mirror.stage.is_desktop
            )
            print(
                "DEX_ON",
                dex_on,
                dex.isChecked(),
                window.mirror.is_desktop,
                window.mirror.stage.is_desktop,
                window.mirror.stage.frame.width() > window.mirror.stage.frame.height(),
                popout.isVisible(),
                popout.isEnabled(),
                window.mirror._session._dex,
            )

            # While DeX is embedded, the bottom navigation buttons must act on
            # the virtual display.  adb keyevents always target the phone's
            # main display, so the app sends scrcpy's own MOD+b shortcut.
            SCRCPY_KEYS.unlink(missing_ok=True)
            adb_before = ADB_LOG.read_text(encoding="utf-8") if ADB_LOG.exists() else ""
            window.mirror.nav.buttons["back"].click()
            nav_dex = wait_until(
                lambda: SCRCPY_KEYS.exists()
                and "alt+b" in SCRCPY_KEYS.read_text(encoding="utf-8"),
                timeout=4.0,
            )
            adb_after = ADB_LOG.read_text(encoding="utf-8") if ADB_LOG.exists() else ""
            print(
                "NAV_DEX",
                nav_dex,
                "adb-keyevent"
                if "keyevent" in adb_after[len(adb_before) :]
                else "no-adb-keyevent",
            )

            popout.click()
            popped = wait_until(
                lambda: window.mirror.is_floating
                and window.mirror.is_mirroring
                and window.mirror._float_session.is_running
            )
            print(
                "POPOUT",
                popped,
                window.mirror.is_floating,
                window.mirror.is_mirroring,
                window.mirror._float_session.is_running,
                window.mirror._session._dex,
                window.mirror.stage.is_desktop,
                popout.text(),
                popout.isChecked(),
            )

            popout.click()
            docked = wait_until(
                lambda: not window.mirror.is_floating
                and window.mirror.is_mirroring
                and window.mirror._session._dex
                and not window.mirror._float_session.is_running
            )
            print(
                "DOCKED",
                docked,
                window.mirror.is_floating,
                window.mirror._session._dex,
                window.mirror.stage.is_desktop,
                popout.text(),
                popout.isChecked(),
            )

            dex.click()
            dex_off = wait_until(
                lambda: window.mirror.is_mirroring and not window.mirror._session._dex
            )
            print(
                "DEX_OFF",
                dex_off,
                dex.isChecked(),
                window.mirror.is_desktop,
                window.mirror.stage.is_desktop,
                popout.isVisible(),
            )

            rotate = window.mirror.rotate
            print(
                "ROTATE_READY",
                rotate.isEnabled(),
                window.mirror.stage.is_landscape,
                window.mirror.is_landscape,
            )
            rotate.click()
            time.sleep(0.4)
            QApplication.processEvents()
            frame = window.mirror.stage.frame
            print(
                "ROTATE_LANDSCAPE",
                window.mirror.is_landscape,
                rotate.isChecked(),
                frame.width() > frame.height(),
                frame.width(),
                frame.height(),
            )
            rotate.click()
            time.sleep(0.4)
            QApplication.processEvents()
            print(
                "ROTATE_PORTRAIT",
                window.mirror.is_landscape,
                rotate.isChecked(),
                frame.height() > frame.width(),
                frame.width(),
                frame.height(),
            )

            speaker = window.mirror.speaker
            print(
                "AUDIO_READY",
                speaker.isChecked(),
                window.mirror._audio,
                window.mirror._session._audio,
            )
            speaker.click()
            for _ in range(60):
                QApplication.processEvents()
                if window.mirror.is_mirroring and window.mirror._session._audio:
                    break
                time.sleep(0.1)
            from PySide6.QtCore import QSettings

            print(
                "AUDIO_ON",
                speaker.isChecked(),
                window.mirror.is_mirroring,
                window.mirror._session._audio,
                QSettings().value("mirror/audio", None, type=bool),
            )
            speaker.click()
            for _ in range(60):
                QApplication.processEvents()
                if window.mirror.is_mirroring and not window.mirror._session._audio:
                    break
                time.sleep(0.1)
            print(
                "AUDIO_OFF",
                speaker.isChecked(),
                window.mirror.is_mirroring,
                window.mirror._session._audio,
                QSettings().value("mirror/audio", None, type=bool),
            )

            # The now-playing bar reads the mock MPRIS plugin and sends
            # transport commands back through it.
            media = window.media
            for _ in range(50):
                QApplication.processEvents()
                if media.title_label.full_text() == "Mock Song":
                    break
                time.sleep(0.1)
            print(
                "MEDIA_READY",
                media.title_label.full_text(),
                "|",
                media.artist_label.full_text(),
                "|",
                media.player_label.text(),
                media.slider.maximum(),
            )
            media.play.click()
            time.sleep(0.25)
            QApplication.processEvents()
            media.slider.setValue(60000)
            media.slider.sliderReleased.emit()
            time.sleep(0.25)
            QApplication.processEvents()
            print(
                "MEDIA_CONTROLS",
                media.previous.isEnabled(),
                media.play.isEnabled(),
                media.next.isEnabled(),
                media.slider.isEnabled(),
                media.time_label.text(),
            )

            # The Files button mounts the phone over KDE Connect and opens the
            # result.  The fake daemon reports a temp directory; capture the
            # openUrl call instead of launching a file manager.
            from phone_link.ui import main_window as main_window_module

            opened: list[str] = []
            real_desktop_services = main_window_module.QDesktopServices

            class FakeDesktopServices:
                @staticmethod
                def openUrl(url):
                    opened.append(url.toLocalFile())
                    return True

            main_window_module.QDesktopServices = FakeDesktopServices
            try:
                window.header.files_button.click()
                for _ in range(50):
                    QApplication.processEvents()
                    if opened:
                        break
                    time.sleep(0.1)
            finally:
                main_window_module.QDesktopServices = real_desktop_services
            print("FILES_OPEN", opened)

            # Phone actions: the header menu, then each KDE Connect call.
            phone = window.phone_menu
            window._refresh_phone_menu()
            print(
                "PHONE_MENU",
                window.header.phone_button.menu() is phone,
                [action.text() for action in phone.actions() if action.text()],
            )
            print(
                "PHONE_MENU_ENABLED",
                [action.isEnabled() for action, _plugin in phone._actions],
            )
            print("PHONE_PLUGINS", sorted(window.kde.phone_plugins() or ()))
            limited = PhoneActionsMenu()
            limited.set_available(True, {"kdeconnect_ping"})
            print(
                "PHONE_LIMITED_ENABLED",
                [action.isEnabled() for action, _plugin in limited._actions],
                [action.isVisible() for action, _plugin in limited._actions],
            )
            limited.deleteLater()
            print("CELLULAR", window.kde.cellular_status())
            print("PHONE_STATUS", phone._status.text())
            window._find_phone()
            adb_before = ADB_LOG.read_text(encoding="utf-8") if ADB_LOG.exists() else ""
            window._stop_ringing()
            stopped = wait_until(
                lambda: "broadcast" in ADB_LOG.read_text(encoding="utf-8")
                if ADB_LOG.exists()
                else False,
                timeout=4.0,
            )
            adb_after = ADB_LOG.read_text(encoding="utf-8") if ADB_LOG.exists() else ""
            print(
                "STOP_RING",
                stopped,
                [
                    line
                    for line in adb_after[len(adb_before) :].splitlines()
                    if "broadcast" in line
                ],
            )
            window._ping_phone()
            window._open_phone_messages()
            sinks = window.kde.phone_sinks()
            print(
                "PHONE_SINKS",
                [
                    (sink.name, sink.label, sink.max_volume, sink.volume, sink.muted)
                    for sink in sinks
                ],
            )
            print("PHONE_SET_VOLUME", window.kde.set_phone_volume("music", 42))
            print("PHONE_SET_MUTED", window.kde.set_phone_muted("music", True))
            print(
                "SMS_SEND",
                window.kde.send_sms(
                    ["+15551234567", "+15559876543"], "See you at seven."
                ),
                window.kde.last_error,
            )

            # The dialogs themselves (not shown modally) must parse addresses
            # and turn slider/mute changes into signals.
            from phone_link.ui.phone_actions import SmsDialog, VolumeDialog

            sms_dialog = SmsDialog(["+15551234567"], window)
            sms_dialog.recipients.setText("+15551234567, +15559876543; +15550000000")
            sms_dialog.body.setPlainText("Dialog message\nsecond line")
            print(
                "SMS_DIALOG",
                sms_dialog.addresses(),
                repr(sms_dialog.message()),
                sms_dialog.counter.text(),
            )
            sms_dialog.deleteLater()

            volume_dialog = VolumeDialog(sinks, window)
            seen: list[tuple] = []
            volume_dialog.volumeChanged.connect(
                lambda name, value: seen.append(("volume", name, value))
            )
            volume_dialog.mutedChanged.connect(
                lambda name, muted: seen.append(("muted", name, muted))
            )
            volume_dialog.slider.setValue(55)
            volume_dialog._flush_volume()
            volume_dialog.mute.setChecked(True)
            volume_dialog.apply_volume("music", 20)
            print(
                "VOLUME_DIALOG",
                volume_dialog.current_sink(),
                volume_dialog.slider.value(),
                volume_dialog.value_label.text(),
                volume_dialog.mute.isChecked(),
                seen,
            )
            volume_dialog.deleteLater()

            host = window.mirror.stage.host
            point = host.mapTo(window, host.rect().topLeft())
            host_size = host.size()
            print("WINDOW", window.size().width(), window.size().height(), "HOST", point.x(), point.y(), host_size.width(), host_size.height())
            print("XIDS", int(window.winId()), int(host.winId()), window.mirror._session._child_window)
            results["host"] = (point.x(), point.y(), host_size.width(), host_size.height())

            # Capture windows directly, never the user's desktop.
            app_shot = subprocess.run(
                ["import", "-window", str(int(window.winId())), str(SHOT)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            print("APPSHOT", app_shot.returncode, app_shot.stderr.strip(), SHOT.exists(), SHOT.stat().st_size if SHOT.exists() else 0)
            child = int(window.mirror._session._child_window or 0)
            child_shot = None
            if child:
                child_shot = subprocess.run(
                    ["import", "-window", str(child), str(SHOT_SCREEN)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                print("CHILDSHOT", child_shot.returncode, child_shot.stderr.strip(), SHOT_SCREEN.exists(), SHOT_SCREEN.stat().st_size if SHOT_SCREEN.exists() else 0)

            if SHOT.exists():
                if SHOT_SCREEN.exists() and child:
                    composite = subprocess.run(
                        [
                            "convert",
                            str(SHOT),
                            str(SHOT_SCREEN),
                            "-geometry",
                            f"+{point.x()}+{point.y()}",
                            "-composite",
                            str(SHOT_FINAL),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    print("COMPOSITE", composite.returncode, composite.stderr.strip(), SHOT_FINAL.exists(), SHOT_FINAL.stat().st_size if SHOT_FINAL.exists() else 0)
                else:
                    SHOT.replace(SHOT_FINAL)

            # Clipboard send goes through the header button to the mock daemon.
            # Replace the clipboard with a fake one so the test never reads or
            # overwrites the real desktop clipboard (on X11 setText can race
            # the session clipboard manager).
            from phone_link.ui import main_window as main_window_module

            class FakeClipboard:
                def text(self) -> str:
                    return "mock clipboard test"

            class FakeGuiApplication:
                @staticmethod
                def clipboard() -> FakeClipboard:
                    return FakeClipboard()

            real_gui_application = main_window_module.QGuiApplication
            main_window_module.QGuiApplication = FakeGuiApplication
            try:
                window.header.clipboard_button.click()
                # Optional automatic sync: on, push once, then push the same
                # text again (it must be suppressed as an echo/duplicate).
                window.header.auto_clipboard_button.click()
                auto_enabled = window._clipboard_sync_enabled
                window._push_clipboard()
                window._push_clipboard()
                window.header.auto_clipboard_button.click()
            finally:
                main_window_module.QGuiApplication = real_gui_application
            print("CLIPBOARD_BUTTON True")
            print(
                "AUTO_CLIPBOARD",
                auto_enabled,
                window._clipboard_sync_enabled,
                window.header.auto_clipboard_button.isChecked(),
            )

            # QR-only wireless pairing dialog.  Replace the pairing worker's
            # network wait with an immediate success so the test stays offline.
            from phone_link.ui import wifi_dialog as wifi_dialog_module

            real_pair_with_qr = wifi_dialog_module.pair_with_qr
            wifi_dialog_module.pair_with_qr = lambda *args, **kwargs: (
                True,
                "Connected to 192.168.1.50:37777 over Wi-Fi.",
            )

            def drive_wifi() -> None:
                dialog = window.findChild(wifi_dialog_module.WifiConnectDialog)
                if dialog is None:
                    return
                pixmap = dialog.qr_image.pixmap()
                print("WIFI_QR", pixmap.width(), pixmap.height(), pixmap.isNull())
                print("WIFI_STATUS", dialog.status.text())

            QTimer.singleShot(150, drive_wifi)
            window.header.wifi_button.click()
            wifi_dialog_module.pair_with_qr = real_pair_with_qr
            print("WIFI_DIALOG True")

            # Drop a local file on the drop zone, exactly as a desktop drag
            # would deliver it.  The mock daemon records the shareUrls call.
            from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
            from PySide6.QtGui import QDragEnterEvent, QDropEvent

            DROP_FILE.write_text("phone link drop test\n", encoding="utf-8")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(DROP_FILE))])
            zone = window.notifications.drop_zone
            QApplication.sendEvent(
                zone,
                QDragEnterEvent(
                    QPoint(12, 12),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )
            QApplication.sendEvent(
                zone,
                QDropEvent(
                    QPointF(12, 12),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )
            print("DROPZONE", zone.label.text(), "ACTIVE", zone.property("active"))
            print("DROP_BUTTON True")

            # Reply through a card's inline reply row.
            active = window._kde_snapshot.notifications
            if active:
                target = active[0]
                for index in range(window.notifications.cards_layout.count()):
                    widget = window.notifications.cards_layout.itemAt(index).widget()
                    if (
                        isinstance(widget, NotificationCard)
                        and widget.notification_id == target.id
                        and widget.reply_button is not None
                    ):
                        widget.reply_button.click()
                        widget.reply_edit.setText("On my way")
                        widget.reply_send.click()
                        print("REPLY_BUTTON", target.id)
                        break

            # Exercise the Android navigation path.
            window.mirror.nav.buttons["back"].click()
            QTimer.singleShot(600, dismiss)

        def dismiss() -> None:
            notifications = window._kde_snapshot.notifications
            if notifications:
                target = notifications[0]
                results["dismissed"] = target.id
                print("DISMISS", target.id, target.app_name, target.heading)
                window.notifications.dismissRequested.emit(target.id)
            QTimer.singleShot(1400, start_disconnect)

        def start_disconnect() -> None:
            remaining = len(window._kde_snapshot.notifications)
            print("REMAINING", remaining)
            print("DISMISSED_RESULT", results.get("dismissed"), "remaining", remaining)
            print("RECENT_AFTER_DISMISS", len(window._kde_snapshot.history))
            OFFLINE.touch()
            print("ADB_OFFLINE requested")
            QTimer.singleShot(5200, check_offline)

        def check_offline() -> None:
            print("OFFLINE state", window.mirror.live_label.text(), "mirroring", window.mirror.is_mirroring)
            print("OFFLINE header", window.header.phone_name.text(), "|", window.header.subtitle.text(), "|", window.header.connection.text_label.text())
            print("OFFLINE placeholder", window.mirror.stage.placeholder.title.text(), "|", window.mirror.stage.placeholder.body.text())
            print("OFFLINE adb", window._adb_snapshot.error)
            print("OFFLINE kde", window._kde_snapshot.mode, len(window._kde_snapshot.notifications))
            OFFLINE.unlink(missing_ok=True)
            print("ADB_RECONNECT requested")
            QTimer.singleShot(5800, finish)

        def finish() -> None:
            print("RECONNECTED mirroring", window.mirror.is_mirroring, window.mirror.live_label.text())
            print("RECONNECTED header", window.header.phone_name.text(), "|", window.header.subtitle.text())
            try:
                print("ADB_LOG", ADB_LOG.read_text(encoding="utf-8").strip())
            except FileNotFoundError:
                print("ADB_LOG missing")
            DROP_FILE.unlink(missing_ok=True)
            # Regression: notification text with a long unbreakable run must
            # not stretch the card list past the scroll viewport, which used
            # to clip every card's right border and dismiss button.
            from datetime import datetime

            from phone_link.models import KdeDevice, KdeSnapshot, NotificationItem
            from phone_link.ui.notifications import NotificationsPanel

            fit = NotificationsPanel()
            fit.resize(window.notifications.width(), 600)
            fit.show()
            fit.set_snapshot(
                KdeSnapshot(
                    available=True,
                    device=KdeDevice(
                        id="probe", name="Probe", reachable=True, paired=True
                    ),
                    notifications=(
                        NotificationItem(
                            id="wide",
                            app_name="Gmail",
                            title="Wide card",
                            text="Body " + "\xa0‌" * 300 + " end",
                            dismissable=True,
                            received_at=datetime.now(),
                        ),
                    ),
                    mode="ready",
                )
            )
            app.processEvents()
            app.processEvents()
            print(
                "CARD_FIT",
                fit.cards_host.width(),
                fit.scroll.viewport().width(),
                fit.cards_host.minimumSizeHint().width(),
                fit.cards_host.width() <= fit.scroll.viewport().width(),
            )
            fit.close()
            fit.deleteLater()
            # Close the window so the scrcpy QProcess and monitor threads are
            # torn down deterministically; otherwise Python may linger at exit.
            window.close()
            print(
                "GEOMETRY_SAVED",
                bool(window._settings.value("window/geometry")),
            )
            app.quit()

        QTimer.singleShot(6000, capture)
        app.exec()
        return 0
    finally:
        mock.terminate()
        try:
            output = mock.communicate(timeout=2)[0] or ""
        except subprocess.TimeoutExpired:
            mock.kill()
            output = mock.communicate()[0] or ""
        if output.strip():
            print("MOCK LOG", output.strip())


if __name__ == "__main__":
    raise SystemExit(main())
