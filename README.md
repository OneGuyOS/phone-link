# Phone Link

[![tests](https://github.com/OneGuyOS/phone-link/actions/workflows/tests.yml/badge.svg)](https://github.com/OneGuyOS/phone-link/actions/workflows/tests.yml)

A small Debian / KDE Plasma desktop companion for Android, in the spirit of
Windows Phone Link. One window contains:

- **Left:** the phone screen, mirrored live with **scrcpy over ADB**, in a
  portrait or landscape phone frame, with a **Rotate** button and
  **Back / Home / Recents** buttons underneath. The frame follows the phone when
  it is rotated physically or with the button. A **speaker** button in the strip
  plays the phone's audio on the computer (off by default; toggling it briefly
  restarts the mirror).
- **DeX:** the **DeX** button switches the frame to Samsung's desktop mode on a
  1080p virtual display, with the full launcher, taskbar and windows. **Pop
  out** moves that desktop into its own decorated floating window and restores
  the normal phone mirror in Phone Link, so both views stay usable at once.
  **Dock** brings DeX back into the frame. The last mode is remembered.
- **Now playing:** a bar under the mirror shows the phone's current track and
  controls it — previous, play/pause, next, and a seek slider — through KDE
  Connect's MPRIS remote plugin.
- **Right:** active phone notifications supplied by the **existing KDE Connect
  daemon over D-Bus**, plus a dimmed **Recent** section for notifications the
  phone cleared. Recent items are saved locally across restarts (last 30), and
  a search box filters both lists by app, heading or text.
- **Replies:** notifications from apps that support direct reply get an inline
  **Reply** box; the message is sent through KDE Connect.
- **Clipboard:** the header's clipboard button sends the desktop clipboard to
  the phone on demand. The adjacent sync toggle can push every clipboard change
  automatically; it is off by default. Phone→desktop clipboard is handled by
  KDE Connect's own daemon.
- **Files:** drag files from the desktop onto the drop zone at the bottom of the
  notification panel; KDE Connect's Share plugin sends them to the phone. The
  header's **Files** button mounts the phone's storage over KDE Connect (SFTP
  via sshfs) and opens it in the desktop's file manager, so files can be dragged
  out to the desktop or in to the phone.
- **Wi-Fi:** the header's Wi-Fi button pairs Android wireless debugging inside
  the app using a QR code the phone scans, then connects the phone over the
  network, so the mirror can run without a cable.
- **Phone actions:** the header's ⋯ menu rings the phone (**Find my phone**),
  stops the ring again (**Stop ringing**), pings it, sends a text message from
  its SIM (**Send a text message…**), opens the phone's messaging app, and
  shows **Phone volume** sliders with mute for each audio output. The menu also
  shows the phone's mobile network type and signal strength. These use KDE
  Connect's Find My Phone, Ping, SMS, Remote System Volume and Connectivity
  Report plugins, and each action appears only when the daemon has loaded its
  plugin for the connected phone. KDE Connect has no stop method for Find My
  Phone, so **Stop ringing** presses the phone app's own **Found it** action
  over adb. Android's KDE Connect volume plugin only lets the phone change the
  computer's volume, so **Phone volume** is hidden for Android phones and is
  useful only with a KDE Connect peer that supports being controlled.
- **Phone→desktop extras** need no code in Phone Link: with KDE Connect's
  **Remote input** plugin (mousepad, remote keyboard, presentation remote),
  **Lock device** or **Run command** enabled in the Android app, the phone can
  move the desktop pointer, type, advance slides, lock the session or run a
  configured command. Those requests are handled by the running KDE Connect
  daemon.
- **Top bar:** phone name, **USB or Wi-Fi** transport, battery level and charging
  state.

Mouse clicks, touch input and keyboard events go to the real scrcpy window, so
the usual scrcpy controls apply. There is no custom Android companion app.

**Milestone 3 is complete.** Mirror audio can be forwarded to the computer with
the speaker button; it is off by default and remembered across restarts. Media
controls and phone-file access are in place too: the now-playing bar drives the
phone's player through KDE Connect's MPRIS remote plugin, and the Files button
mounts the phone's internal storage with KDE Connect's SFTP plugin and opens it
in the file manager. Samsung DeX can run in the frame or in a separate floating
window alongside the normal phone mirror.
Automatic clipboard sync works in both directions;
it is off by default because it also forwards passwords and other sensitive
copies. Phone actions (find, stop ringing, ping, SMS, mobile signal and, where
the phone supports it, per-output volume and mute) are exposed through the
header menu. In-app Wi-Fi pairing is QR-only; discovery uses a built-in mDNS query
because adb's own resolver can return nothing on some Linux hosts. QR pairing
and the Wi-Fi mirror were verified end to end with a real phone. Persistent
notification history, search, remembered window geometry and keyboard shortcuts
are also in place.
Milestone 2 (screen mirroring, notification list and history, inline replies,
explicit clipboard send and file drop) is complete.

---

## Quick start

If the dependencies below are already installed:

```bash
cd ~/phone-link
./run.sh
```

Phone Link checks for `adb`, `scrcpy` and KDE Connect at startup. If something
is missing, it explains what is missing and gives only the commands needed to
fix it; you can still open the window and use whatever is available.

### Keyboard shortcuts

The status bar mentions them at startup, and **F1** opens the in-app list.

| Keys | Action |
| --- | --- |
| `F5` or `Ctrl+R` | Refresh the phone and notification state |
| `Ctrl+Shift+R` | Rotate the mirrored phone screen |
| `Ctrl+Shift+D` | Switch the mirror between the phone screen and DeX |
| `Ctrl+Shift+P` | Pop the DeX desktop out, or dock it again |
| `Ctrl+F` | Focus the notification search box |
| `Esc` | Clear the search while it is focused |
| `F11` | Toggle full screen |
| `F1` | Show the keyboard shortcut list |
| `Ctrl+Q` or `Ctrl+W` | Close Phone Link |

Phone Link remembers the window size, position and maximized/full-screen state
between runs. If a display that held the window is gone, it moves the window
onto the primary screen instead of restoring it off-screen.

---

## Install on Debian 13 (trixie)

### 1. System packages

```bash
sudo apt update
sudo apt install python3 python3-pyside6.qtcore python3-pyside6.qtgui \
  python3-pyside6.qtwidgets python3-pyside6.qtdbus python3-segno \
  adb kdeconnect curl xwayland libglib2.0-bin
```

`xwayland` is only used when you are logged into Plasma on Wayland. It is
harmless on a pure X11 session. `python3-segno` renders the pairing QR code;
without it, Wi-Fi pairing is unavailable and the dialog shows the install
command. `libglib2.0-bin` supplies `gdbus`, which the SMS action uses; without
it the other phone actions still work and the SMS dialog explains what to
install.

### 2. scrcpy 5.0.1 (official release)

Debian trixie does not package scrcpy, so install the official static release
under your home directory:

```bash
SCRCPY_VERSION=5.0.1
mkdir -p ~/.local/opt ~/.local/bin
curl -fL \
  "https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY_VERSION}/scrcpy-linux-x86_64-v${SCRCPY_VERSION}.tar.gz" \
  | tar -xz -C ~/.local/opt
ln -sfn "$HOME/.local/opt/scrcpy-linux-x86_64-v${SCRCPY_VERSION}" "$HOME/.local/opt/scrcpy"
printf '#!/bin/sh\nexec "$HOME/.local/opt/scrcpy/scrcpy" "$@"\n' > "$HOME/.local/bin/scrcpy"
chmod +x "$HOME/.local/bin/scrcpy"
```

Verify:

```bash
~/.local/bin/scrcpy --version
```

`run.sh` prepends `~/.local/bin` to `PATH` itself, so a shell restart is not
required for this app.

### 3. Prepare the phone for mirroring

1. On Android, open **Settings → About phone** and tap **Build number** seven
   times to unlock Developer options.
2. Open **Settings → System → Developer options** and enable **USB debugging**.
3. Connect the phone over USB and unlock it. Accept the **Allow USB debugging?**
   prompt on the phone.
4. Check that ADB sees an authorized device:

```bash
adb devices -l
```

The result should contain a line ending in `device`, for example:

```text
R5GL...  device usb:3-2 product:... model:SM_S731U1 ...
```

If the phone says `unauthorized`, unlock it and accept the prompt. If it says
`offline`, try:

```bash
adb kill-server
adb start-server
adb devices -l
```

### 4. Pair KDE Connect

1. Install the open-source **KDE Connect** Android app (F-Droid or Google Play).
2. Put the phone and computer on the same network and pair them in the KDE
   Connect app on either side.
3. In the Android app, open **Plugin settings** and enable **Notifications**.
   Android will also ask for **Notification access** — grant it. Keep the
   Notifications plugin enabled on the desktop side as well.
   For the header's **Send clipboard** button, also enable the **Clipboard**
   plugin on both the phone and the desktop. For the file drop zone, enable the
   **Share** plugin on both ends (it is usually on by default).
   For the header's ⋯ menu, enable **Find My Phone**, **Ping**, **SMS** and
   **Connectivity report** on the phone, and the matching plugins on the
   desktop. Android may ask for phone-state permission for the connectivity
   report. Sending SMS from the desktop also needs `gdbus`
   (`libglib2.0-bin`), which glib2 supplies. **Stop ringing** needs an adb
   connection (USB or the in-app Wi-Fi connection); without adb it tells you
   to tap **Found it** on the phone instead.
4. On the computer, open the **KDE Connect** app from the application menu if
   the daemon is not already running. Verify the device:

```bash
kdeconnect-cli -l
```

The phone must be both **paired** and **reachable** for notifications to appear.
KDE Connect over Wi-Fi and the USB mirror can run at the same time.

### 5. Run

```bash
cd ~/phone-link
chmod +x run.sh
./run.sh
```

Or, equivalently:

```bash
cd ~/phone-link
python3 -m phone_link
```

### Wayland note

The app needs to reparent scrcpy's native window into its own window. That works
with X11/XWayland, not with native Wayland surfaces. On a Plasma Wayland
session, `./run.sh` automatically selects Qt's `xcb` platform when `DISPLAY` is
available. Do not force `QT_QPA_PLATFORM=wayland`; if you need to be explicit:

```bash
QT_QPA_PLATFORM=xcb ./run.sh
```

---

## Wi-Fi ADB

The Wi-Fi button in the header opens **Connect over Wi-Fi**. Pairing uses only
a QR code; there is no numeric pairing form. On Android 11+:

1. On the phone, open **Settings → Developer options → Wireless debugging**.
2. In Phone Link, press the Wi-Fi button. A one-time QR code appears.
3. On the phone, tap **Pair device with QR code** and point its camera at the
   code on the computer screen. No typing is needed.
4. If the code expires, press **Show a new code**.

The QR contains a one-time service name and password generated locally. After
the phone scans it, the phone advertises a `_adb-tls-pairing._tcp` service under
that name; Phone Link finds it over mDNS, runs `adb pair`, then finds the
`_adb-tls-connect._tcp` port and runs `adb connect`. Phone Link never
implements the pairing protocol itself. The connection port is found
automatically; there is no address to type after pairing.

**Discovery does not use `adb mdns services` alone.** On this machine adb's
embedded mDNS client returns an empty list even though the phone is advertising
correctly, so Phone Link sends its own standard mDNS PTR/SRV/A queries and
merges in adb's results when they exist. mDNS still has to be allowed by the
network: the phone and computer must be on the same subnet, and guest Wi-Fi or
client isolation blocks it.

USB is preferred automatically when both transports are present, so unplug the
cable to make the mirror use Wi-Fi. To force a particular device:

```bash
PHONE_LINK_SERIAL=192.168.1.50:5555 ./run.sh
```

The full Wi-Fi path was verified on a real phone: the phone scanned the
in-app QR code, Phone Link paired and connected, USB was unplugged, and scrcpy
mirrored over the wireless transport.

---

## Tests

Install the test-only dependencies once:

```bash
sudo apt install python3-dbus python3-gi imagemagick
```

Run the unit tests:

```bash
cd ~/phone-link
python3 -m unittest discover -s tests -v
```

Run the end-to-end UI test on an X11 or XWayland display:

```bash
cd ~/phone-link
dbus-run-session -- python3 tests/ui_mock_test.py
```

The integration test is deliberately isolated:

- it starts a **mock KDE Connect daemon on a private D-Bus session**, so it
  cannot disturb real notifications;
- it puts **fake `adb` and `scrcpy` executables** first on `PATH`;
- its fake scrcpy draws a test pattern, so no real phone content is captured;
- it captures **only the app window and the fake child window** — never the
  desktop — and writes `/tmp/phone-link-ui-mock.png`;
- it stops the fake mirror and restarts it while simulating an ADB disconnect
  and reconnect.

### What was tested

On Debian 13 trixie, KDE Plasma 6, Wayland with XWayland, PySide6 6.8.2.1,
scrcpy 5.0.1, adb 34.0.5 and KDE Connect 25.04.2:

- Real USB ADB connection to an Android phone, including authorization.
- Live notification delivery from a real, paired phone through the real KDE
  Connect daemon (Messenger); the card and count badge appeared without a
  manual refresh.
- Real scrcpy 5.0.1 window embedded inside the Phone Link window, and the
  scrcpy process staying alive after embedding (after the SDL3 settle delay).
- Startup dependency checks and the missing-tool dialog with targeted install
  commands.
- Full window in the mock environment: header name / transport / battery,
  live mirror state, three notifications sorted newest first, count badge.
- Dismissing a notification through the panel (the mock removes it).
- Android Back/Home/Recents buttons being wired to `adb shell input keyevent`
  (Back verified as key code `4` against the fake ADB). While DeX is embedded
  they are sent as scrcpy's own MOD+b/h/s shortcuts instead, because adb
  keyevents always target the phone's main display rather than the virtual
  desktop; the mock run confirmed the shortcut reached the embedded window and
  that no adb keyevent was sent.
- Screen rotation: the mock run's rotate button switched the phone frame from
  portrait (279×604) to landscape (627×289), checked and unchecked the button,
  and switched back; the display-orientation parser read both the
  `Viewport … orientation=n` form and the `mDisplayRotation=ROTATION_n`
  fallback. The rotate path asks scrcpy to perform its own device rotation, so
  the phone's user-rotation setting is not changed. On the real phone the frame
  followed the phone's reported orientation, the Rotate button rotated the real
  display, and the picture repainted to fill the resized frame (confirmed by the
  owner).
- ADB disconnect → mirror stopped and a clear placeholder; reconnect → mirror
  restarted and the header returned to USB.
- Recent history: a notification the phone cleared while the app was open was
  archived and shown dimmed under **Recent**, while the active count badge
  stayed correct; dismissing an active notification did not archive it.
- Persistent history: the mock UI run wrote an archived item to an isolated
  test data directory; unit tests checked the file is owner-only (`0600`),
  reloaded it with a fresh client, and verified that clearing Recent removed
  the file.
- Search: the notification panel's search box filtered both active and Recent
  cards case-insensitively (`dinner` → 1 Recent card, `battery low` → 1 active
  card), showed a **No matches** state for an unmatched query, and restored the
  full list when cleared.
- Card width: a synthetic notification body with a 300-repeat unbreakable
  `\xa0‌` run stayed inside the scroll viewport (`CARD_FIT 465 465 179
  True`), so a wide notification can no longer push every card past the right
  edge and clip its border and dismiss button.
- Window state: closing the mock window wrote its geometry to the isolated test
  settings (`GEOMETRY_SAVED True`).
- Shortcuts: all ten window shortcuts were registered (`F5`, `Ctrl+R`,
  `Ctrl+Shift+R`, `Ctrl+Shift+D`, `Ctrl+Shift+P`, `Ctrl+F`, `F11`, `F1`,
  `Ctrl+Q`, `Ctrl+W`), and the shortcut dialog rendered with all nine entries
  offscreen.
- DeX: the mock run switched the frame to a 16:9 desktop, showed the Pop out
  button, popped DeX into a second fake scrcpy process while the main mirror
  stayed embedded, docked it again, and switched back to the phone frame. The
  real-phone run created a 1920×1080 DeX virtual display with the full launcher,
  both embedded and in a separate 1280×720 floating window, and the phone
  mirror remained live in both cases. DeX right-clicks open context menus
  (`--mouse-bind=++++:bhsn`) instead of acting as Android Back, and the bottom
  navigation buttons act on the embedded desktop.
- Error states: a simulated KDE Connect offline snapshot showed the
  **KDE Connect is offline** empty state with a visible **Try again** button
  wired to a full refresh.
- Clipboard send: the header button delivered the clipboard text to the mock
  daemon (`sendClipboard`), and a real send from the desktop to the paired
  phone worked.
- Notification reply: the inline Reply box delivered a message for a
  reply-capable notification to the mock daemon (`sendReply`).
- File drop: a local file dropped on the panel's drop zone was delivered to the
  mock daemon as a file URL through `shareUrls`, and a real file dropped on the
  drop zone arrived on the paired phone.
- Automatic clipboard sync: with the toggle on, a fake clipboard change was
  pushed once through `sendClipboard`; an immediate duplicate was suppressed,
  and the toggle persisted only in the test configuration directory. On the real
  phone, automatic sync worked in both directions: desktop→phone through the
  toggle, and phone→desktop through KDE Connect's own daemon.
- QR-only Wi-Fi pairing dialog: the header button opened the dialog, which
  rendered a QR code and completed pairing through a stubbed worker
  (`WIFI_QR 222 222 False`, `WIFI_DIALOG True`).
- Real mDNS discovery: a direct PTR/SRV/A query from Phone Link found the
  phone's actual `_adb-tls-connect._tcp` advertisement at
  `192.168.2.104:45113`, while `adb mdns services` returned nothing on the same
  machine at the same time. `adb connect` to the discovered address succeeded
  (the wireless transport was then disconnected again; USB stayed selected).
- QR payload generation (`WIFI:T:ADB;S:…;P:…;;`) and rendering with
  `python3-segno` (a 222×222 PNG loaded into a QPixmap offscreen).
- Real QR pairing and Wi-Fi mirroring: the phone scanned the in-app QR code,
  Phone Link found the matching pairing service, ran `adb pair`, discovered the
  connection port, and ran `adb connect`; after USB was unplugged, scrcpy was
  running as `scrcpy --serial 192.168.2.104:45113` and the mirror stayed live.
- scrcpy receives an already laid-out phone-frame size at startup instead of
  the unlaid-out 100×30 default.
- Mirror audio: toggling the speaker button in the mock run restarted the
  mirror with audio enabled, re-embedded, and persisted the setting; toggling
  it off restarted it again with `--no-audio` (`AUDIO_ON True True True True`,
  `AUDIO_OFF False True False False`). On the real phone, scrcpy started without
  `--no-audio` and registered a `scrcpy` sink input with PulseAudio, confirming
  the audio path is live; sound itself was not listened to while the phone was
  dozing.
- Media controls: the mock run read the mock MPRIS plugin
  (`MEDIA_READY Mock Song | Test Artist | Mock Player 200000`), enabled the
  transport buttons and seek slider (`MEDIA_CONTROLS True True True True`) and
  sent PlayPause and a 30-second seek (`MEDIA_ACTION PlayPause`,
  `MEDIA_SEEK 30000`). Against the real phone, the client read the live player
  state over D-Bus — player, track, artist, playing, position, length and
  seekable.
- Phone files: the mock run's Files button mounted the mock storage and opened
  it at `.../phone-link-mock-storage/storage/emulated/0` (`FILES_OPEN`). On the
  real phone, KDE Connect's SFTP plugin mounted internal storage at
  `/run/user/1000/<device-id>/storage/emulated/0` (mounted and unmounted during
  the check).
- Phone actions: the mock run's header menu listed the mobile-network status
  (`Mobile network: 5G · signal 3/4`) and all six actions with the phone
  connected (`PHONE_PLUGINS` listed every plugin the mock loaded). Find my
  phone, Ping and Open messages reached the mock daemon
  (`FINDMYPHONE_RUNG`, `PING_RECEIVED`, `SMS_APP_LAUNCHED`); volume and mute
  reached it with the reported sink names (`PHONE_VOLUME music 42`,
  `PHONE_MUTED music True`). Stop ringing sent adb a broadcast for the phone
  app's Found it receiver with this computer's id (`STOP_RING True`,
  `broadcast desktopmock`). Send SMS delivered both recipients, the exact
  text, SIM id `-1` and no attachments through `gdbus` (`SMS_RECEIVED
  +15551234567,+15559876543 See you at seven. -1 attachments=0`). The SMS
  dialog split recipients on commas, semicolons and newlines, and the volume
  dialog emitted a debounced volume change and a mute toggle, then applied a
  phone-reported volume back to its slider. A menu given only the Ping plugin
  hid every other action (`PHONE_LIMITED_ENABLED`).
  Against the real Galaxy S25 FE (KDE Connect 1.37.2), KDE Connect reported
  Find My Phone and Ping working, and the daemon had loaded Find My Phone,
  Ping, SMS and Connectivity Report. Find My Phone rang the phone audibly
  (KDE Connect forced the alarm stream to maximum volume), and pressing
  **Stop ringing** in the app from the open window silenced it. The phone's
  exported Found it receiver also accepted the stop broadcast over adb
  (`Broadcast completed: result=0`) with the daemon's `selfId()`, and KDE
  Connect kept running on the phone afterwards. The phone's `sms` object
  exposes the expected `sendSms(avsavx)` signature and Connectivity Report
  reported `LTE`. The daemon had **not** loaded Remote System Volume: the
  Android app has no plugin for computer-controlled phone volume.
- 63 unit tests covering ADB parsing and selection, name/battery parsing,
  display-rotation parsing (including the window-manager fallback and invalid
  values), scrcpy command construction for the mirror, DeX and floating
  windows, notification fallbacks, KDE snapshot state, recent-history ordering,
  bounds and persistence, the owner-only atomic history file (including corrupt
  and partial records), notification search matching, install-command targeting
  (including python3-segno) and failure-message filtering, clipboard echo
  suppression, wireless-service parsing, mDNS packet parsing (including a
  missing address record), QR payload formatting, media-state construction
  (including clamping and invalid values), duration formatting, the
  phone-action helpers (sink JSON parsing and clamping, the typed gdbus
  arguments for the SMS plugin, the loaded-plugin list, this computer's KDE
  Connect id, and the adb stop-ring broadcast command).

### What was not tested

- Manual touch, mouse and keyboard interaction inside the embedded real scrcpy
  window. scrcpy's shortcut forwarding itself was verified: an XTest MOD+h sent
  to the embedded real scrcpy window moved the phone to its launcher, and a
  right-click on the real floating DeX window opened a context menu. The
  embedded-DeX nav-button route was exercised end to end in the mock run; it was
  not clicked through on the real embedded desktop.
- Every keyboard shortcut was checked as registered, but not synthesized
  through the window manager on the real desktop; the off-screen window restore
  path was not exercised with a multi-monitor setup.
- Rotation while the phone is asleep or keyguard-locked: the frame follows the
  reported orientation, but the real display ignored the rotate request in that
  state. Rotation with the phone awake and unlocked is confirmed.
- Hearing the forwarded audio itself. The PulseAudio sink input exists, but the
  phone was dozing during the check, so actual media sound was not confirmed by
  ear. Play something on the phone with the speaker button on to verify.
- Sending media commands to the real phone. Reading the live player state was
  verified against the real phone, but transport and seek commands were only
  exercised against the mock daemon so the owner's playback was not interrupted.
- Opening the real mounted folder in the desktop file manager: the mock run
  captured the `openUrl` call, and the real mount path was mounted, listed and
  unmounted directly, but Dolphin was not launched during the check.
- Sending SMS through a real SIM. Ringing and pinging the real Galaxy S25 FE
  were confirmed, and the real phone exposes the SMS plugin with the expected
  signature, but no text was sent from the check so the owner's contacts were
  not messaged. The volume path was exercised only against the mock daemon
  because Android KDE
  Connect does not support computer-controlled phone volume; on a real Android
  phone the action is hidden.
- Multiple phones at once; Phone Link selects one device.
- Other distributions, other scrcpy versions, or non-KDE desktops.

---

## Current limitations

- KDE Connect must be installed and running; Phone Link does not reimplement the
  protocol or replace the Android app.
- Only **active** notifications are requested from KDE Connect. Notifications
  the phone clears while Phone Link is open move to **Recent** (last 30). Recent
  items are saved across restarts in
  `$XDG_DATA_HOME/phone-link/history.json` (`~/.local/share/phone-link/` by
  default) with owner-only permissions; **Clear** deletes that file. Active
  notifications that were never archived are not saved, and notifications the
  phone had already cleared before Phone Link started are not recovered.
- KDE Connect's Android app filters ongoing/foreground notifications (clock
  timers, media, group summaries) before they ever reach the desktop.
- The "now / 5m" label is when Phone Link first saw a notification, not the
  phone's original notification timestamp (that is not exposed by the D-Bus
  interface used here).
- Notifications without `dismissable` have no dismiss button.
- The mirrored screen relies on X11/XWayland.
- **DeX** needs Samsung's desktop mode and an Android version that allows
  scrcpy to create a virtual display (Android 15 or newer with scrcpy 5). On
  other phones the DeX button still creates a virtual display, but there is no
  Samsung launcher to show. Popping DeX out starts a second scrcpy process;
  closing the floating window turns DeX off and leaves the phone mirror
  running.
- The phone frame follows the display rotation the phone reports, checked with
  the other ADB state every 2.5 seconds. After **Rotate** is pressed the frame
  switches immediately and holds that orientation for 2.2 seconds so an
  in-flight poll cannot snap it back; if the phone never rotates, the frame
  returns to the reported orientation on the next poll (at most about five
  seconds later).
- **Rotate** uses scrcpy's own device-rotation action (`MOD+r`, sent to the
  embedded scrcpy window). It only changes the live display rotation; it does
  not change Android's auto-rotate setting. It needs the phone's display to be
  on — a sleeping or keyguard-locked phone may ignore it.
- **Speaker** enables scrcpy audio forwarding (`--audio-source=output`, the
  default). While it is on, Android sends all media audio to the computer and
  mutes the phone's own speaker. Audio forwarding needs Android 11 or newer; if
  the phone cannot capture audio, the mirror reports the failure and the speaker
  button can be switched off to reconnect without sound. Changing the setting
  restarts scrcpy, so the mirror blanks for a second or two.
- **Now playing** uses KDE Connect's MPRIS remote plugin. It controls whichever
  player the phone reports as current; if several media apps are active, the
  phone decides which one. Position is refreshed once a second while playing, so
  the slider can lag slightly and is not frame-accurate. Seeking sends the
  difference to the phone; players that report `canSeek = false` leave the
  slider disabled, and apps with no media session do not appear at all.
- **Files** mounts the phone's storage through KDE Connect's SFTP plugin, which
  needs `sshfs` on the computer and the SFTP plugin enabled on the phone. It
  opens the desktop file manager at the phone's internal storage. The mount
  stays until KDE Connect unmounts it (device disconnect or daemon exit); Phone
  Link shows no transfer progress. Android cannot start a drag from its own
  file-manager screen out to the computer, so the drag always starts from the
  computer side of the mounted folder.
- Clipboard is **send-only from this app**: the button pushes the desktop
  clipboard to the phone. Phone→desktop clipboard is done by KDE Connect's
  daemon.
- Wi-Fi pairing uses a QR code and requires Android 11+ wireless debugging, the
  phone on the same network, and a router that allows the two devices to reach
  each other (guest networks often block client-to-client traffic and multicast
  mDNS). Pairing ports change every time the phone opens the pairing dialog.
  The `python3-segno` package renders the QR image. USB is still preferred when
  both transports are present.
- The header's sync toggle enables **automatic desktop→phone clipboard sync**;
  it is off by default and persisted across restarts. While it is on, every
  copied text — including passwords — is sent to the paired phone. Empty text
  and text over 100,000 characters are skipped, and identical text is suppressed
  for 8 seconds so KDE Connect's own echo cannot loop. Changes made while the
  phone is unreachable are not queued. There is no clipboard history.
- Inline replies only appear when the phone app exposes a `replyId`. KDE
  Connect does not report whether the message was delivered.
- **Phone actions** depend on the matching KDE Connect plugins being enabled on
  both ends. Find my phone rings at full volume even when the phone is silent
  (the plugin's behaviour); Ping shows a notification. KDE Connect has no stop
  method, so **Stop ringing** works only while adb can reach the phone: it
  sends the phone app's own **Found it** broadcast. If adb is not connected,
  tap **Found it** on the phone. Sending SMS shells out to
  `gdbus` because PySide6 cannot express the SMS plugin's `avsavx` signature;
  without `gdbus` the dialog explains what to install. Messages have no delivery
  report, attachments are not supported, and a multi-SIM phone uses its default
  SIM (the plugin's `-1`).
- **Phone volume** works only with a peer that implements Remote System Volume.
  Android's KDE Connect app (tested with 1.37.2) does not: its **System
  volume** plugin only lets the phone change the computer's volume, so Phone
  Link hides the phone-volume action for Android phones. With a supporting
  peer the dialog lists the audio outputs that peer reports; the slider moves
  in raw stream steps and shows them as a percentage of that stream's maximum.
  Volume changes the phone reports itself update an open dialog; the dialog
  does not poll, and it reads the sink list only when it opens.
- The ⋯ menu's **mobile-network line** is read when the menu opens (not polled)
  and shows the network type and a 0–4 signal strength, not the operator,
  roaming state or data usage.
- **Contacts** and **call control** are not implemented. KDE Connect's contacts
  plugin exposes only a cache-synchronise call on D-Bus (the vCards live in
  KPeople), and the current SMS/telephony plugins have no PC-initiated
  call-mute method. Incoming calls still reach the notification panel as
  ordinary phone notifications.
- Files are sent by dropping them on the drop zone in the notification panel;
  dropping elsewhere in the window does nothing. Only regular local files are
  sent (directories are ignored). KDE Connect performs the transfer, so Phone
  Link shows no progress bar, completion notice or retry; large files depend on
  the KDE Connect link and the phone's storage.

---

## Troubleshooting

**The mirror says "No ADB device found".**
Connect USB, unlock the phone, enable USB debugging, and run `adb devices -l`.
Accept the authorization prompt. Try `adb kill-server && adb start-server` if
the device is stuck offline.

**scrcpy is not found.**
Check `~/.local/bin/scrcpy --version`. The app's setup dialog prints the official
install block. `run.sh` already adds `~/.local/bin` to `PATH`.

**Mirroring fails only on Wayland.**
Make sure `xwayland` is installed and `echo $DISPLAY` is non-empty. Do not set
`QT_QPA_PLATFORM=wayland`.

**KDE Connect is offline / no notifications.**
Start KDE Connect from the application menu, confirm the phone is paired and
reachable with `kdeconnect-cli -l`, and grant the Android app notification
access and enable its Notifications plugin.

**The notification appears in KDE Connect but not here.**
Press the refresh button in the header. Also check that the phone is reachable;
KDE Connect does not report notifications from an unreachable device.

**The clipboard button does nothing, or the phone never receives it.**
Enable the Clipboard plugin in KDE Connect on both ends. KDE Connect's
notification/listener modes do not require it, but clipboard transfer does.

**An action is missing from the ⋯ menu, or nothing happens.**
The menu shows only actions whose KDE Connect plugin the daemon has loaded for
the connected phone. Enable **Find My Phone**, **Ping** or **SMS** in the
Android KDE Connect app's Plugin settings, and make sure the same plugin is
enabled on the desktop. **Phone volume** never appears for an Android phone
because the Android app does not implement computer-controlled volume. The
phone must be paired and reachable; the plugin calls travel over the existing
KDE Connect link.

**Stop ringing says to connect the phone with adb.**
Find My Phone can only be stopped from the phone over KDE Connect, so Phone
Link uses adb to press the app's own **Found it** action. Connect the phone
over USB or the in-app Wi-Fi button, or tap **Found it** on the phone itself.

**Send a text message fails immediately.**
Install `gdbus` (`sudo apt install libglib2.0-bin`) and grant the Android KDE
Connect app SMS permission, with its SMS plugin enabled. Phone Link uses gdbus
because PySide6 cannot marshal the plugin's exact D-Bus signature.

**No Reply button appears on a notification.**
Only apps that publish a `replyId` through the notification listener can be
replied to; messaging apps vary by version and notification type.

**QR pairing does not connect.**
Keep the phone on **Wireless debugging → Pair device with QR code** with its
camera pointed at the current code. Phone Link finds the phone's pairing
advertisement over mDNS, so both devices must be on the same subnet; guest Wi-Fi
and client isolation block it. Pairing codes expire after two minutes — press
**Show a new code** if the phone reports an expired or invalid code. If the
phone scans but the dialog keeps waiting, turn Wireless debugging off and on
again and reopen the dialog.

**QR pairing says it needs python3-segno.**
Install it with `sudo apt install python3-segno`, then reopen the dialog. The
package only renders the QR image locally; no data is sent to an online QR
service.

**Automatic clipboard sync does not send.**
Turn on the sync toggle next to the clipboard button, enable the Clipboard
plugin on both ends, and make sure the phone is reachable. Empty text, text over
100,000 characters, and a copy identical to one sent in the last 8 seconds are
skipped. Copy something new after the phone reconnects; changes made while it
was unreachable are not queued.

**The now-playing bar stays on "Nothing playing".**
Start playback in an app that publishes a media session (music/podcast/video
players usually do), enable KDE Connect's MPRIS remote plugin in the KDE
Connect settings on the computer, and make sure the phone is paired and
reachable. Apps that only play short sounds without a media session do not
appear.

**The Files button says the file-transfer plugin is unavailable.**
Enable KDE Connect's SFTP plugin in the phone's KDE Connect app and install
`sshfs` on the computer (`sudo apt install sshfs`). The phone must be paired and
reachable over the same network.

**Dropping a file on the drop zone does nothing.**
The phone must be paired and reachable and the **Share** plugin must be enabled
on both ends. The drop zone only accepts regular local files; drag a file, not a
folder or a browser link.

---

## How it works

- `phone_link/adb.py` polls `adb devices -l` in a background thread (every 2.5
  seconds) and reads the model, manufacturer, battery and display rotation with
  `getprop` / `dumpsys battery` / `dumpsys input` (falling back to
  `dumpsys window displays`), all in one shell call. It also sends the
  Find My Phone stop broadcast (`am broadcast` to the KDE Connect app's
  exported `FindMyPhoneReceiver`, action `...findmyphone.foundIt`, with this
  computer's KDE Connect id as the `deviceId` extra).
- `phone_link/scrcpy.py` starts scrcpy as a `QProcess` with its SDL video driver
  forced to X11, finds the scrcpy window by a unique title, waits briefly for
  SDL3 to finish creating it, and reparents it into the Qt container using
  `XReparentWindow` through `ctypes`/libX11. Audio is disabled with
  `--no-audio` unless the speaker button is on; toggling it restarts scrcpy
  with the new flag. Rotation sends Alt+r to that
  embedded window with XTest (libXtst) after focusing it, so scrcpy performs its
  own rotation; the desktop's user-rotation setting is left alone. DeX adds
  `--new-display=1920x1080/240`, which creates the virtual display Samsung's
  desktop mode attaches to. A second, non-embedded scrcpy process owns that
  virtual display when DeX is popped out; it leaves off `--window-borderless`
  so the window manager decorates and moves it normally.
- `phone_link/ui/mirror.py` keeps the phone frame at a 9:19.5 aspect in
  portrait (the inverse in landscape) and a 16:9 frame for DeX, and resizes the
  embedded scrcpy window
  to the new frame so the video re-fits. After a resize settles it asks scrcpy
  to repaint (an X11 expose), because scrcpy can otherwise keep the old frame
  in a corner of the resized window.
- `phone_link/kdeconnect.py` talks only to the running
  `org.kde.kdeconnect` D-Bus service: device properties, the battery interface,
  `activeNotifications()`, notification properties, `dismiss()`, `sendReply()`,
  `sendClipboard()` and `shareUrls()`. Removed notifications are kept in a
  recent list and saved locally by `phone_link/notification_store.py`. It also
  reads the phone's `mprisremote` properties (player, track, position, length)
  and sends `sendAction()` / `seek()` there, and it asks the SFTP plugin to
  `mountAndWait()` so the desktop can open the phone's storage as a folder. It
  also calls Find My Phone's `ring()`, Ping's `sendPing()`, the SMS plugin's
  `sendSms()` / `launchApp()`, and Remote System Volume's `sendVolume()` /
  `sendMuted()`, and reads the Connectivity Report's cellular properties. It
  asks the device interface for `loadedPlugins()` and the menu shows only the
  actions whose plugin is loaded. It reads the daemon's `selfId()` for the
  stop-ring broadcast.
  `sendSms()` goes through `gdbus` so its `avsavx` arguments have exact types.
- `phone_link/ui/phone_actions.py` is the header's phone menu and the SMS and
  volume dialogs, styled like the rest of the window.
- `phone_link/wifi.py` runs `adb pair` / `adb connect` for the in-app wireless
  debugging dialog, generates the QR pairing payload, and matches the phone's
  pairing advertisement; it never implements the pairing protocol itself.
- `phone_link/mdns.py` sends small standard mDNS PTR queries for
  `_adb-tls-pairing._tcp` and `_adb-tls-connect._tcp` and parses the SRV/A
  answers, because adb's own mDNS client can return an empty list on a working
  network.
- `phone_link/ui/qr.py` renders the QR image locally with the `python3-segno`
  package; no QR or pairing data leaves the machine.
- `phone_link/clipboard_sync.py` decides whether an automatic clipboard change
  is worth sending: it skips empty/oversized text and remembers SHA-1 digests
  (never the text) of recent sends for 8 seconds to stop KDE Connect echoes.
- `phone_link/ui/` contains the custom dark Plasma/Breeze-inspired interface;
  `ui/media.py` is the now-playing bar with its transport buttons and seek
  slider.

No phone-screen frames, video, audio or notification content leave the machine.
Notification history is written only to the local owner-only file described
above. Clipboard text goes only to the paired phone through KDE Connect, either
when the clipboard button is pressed or while the optional auto-sync toggle is
on. Phone files are read through a local sshfs mount over the paired KDE
Connect connection. Text messages are handed to the phone through KDE Connect
and sent by its own SIM, not by a web or cloud messaging service. There is no
telemetry and no network service in Phone Link itself.

## Project layout

```text
phone_link/          application code
  ui/                window, header, mirror stage, notification panel
tests/               unit tests, mock KDE daemon, fake adb/scrcpy, UI test
run.sh               launcher
README.md
```

## Components and licenses

Phone Link uses only free and open-source components: scrcpy (Apache-2.0),
KDE Connect (GPL-2.0-or-later), Android platform tools / adb (Apache-2.0), and
Qt / PySide6 (LGPL-3.0 / GPL-2.0). Android is a trademark of Google LLC; this
project is not affiliated with Google or Microsoft.

## License

Phone Link itself is released under the MIT License; see `LICENSE`.

