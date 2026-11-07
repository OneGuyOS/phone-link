"""Minimal libX11 binding used to adopt scrcpy's window.

This is deliberately small: scrcpy creates a normal X11 window and we make it
a child of the Qt screen container.  Input continues to be handled by scrcpy's
native window, so keyboard and mouse forwarding stay intact.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Iterator


class X11Error(RuntimeError):
    pass


_AnyPropertyType = 0
_UTF8_STRING = "UTF8_STRING"
_NET_WM_NAME = "_NET_WM_NAME"
_RevertToParent = 2
_CurrentTime = 0


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


class X11Display:
    """A private Xlib connection.

    All methods are expected to be called from the Qt main thread.  The object
    never destroys X windows it did not create.
    """

    def __init__(self, display_name: str | None = None) -> None:
        self.display_name = display_name or os.environ.get("DISPLAY")
        if not self.display_name:
            raise X11Error("DISPLAY is not set; X11 is required to embed scrcpy.")

        library = ctypes.util.find_library("X11")
        if not library:
            raise X11Error("libX11 was not found.")
        self._lib = ctypes.CDLL(library)
        self._configure_signatures()

        # libXtst lets Phone Link request scrcpy's own "rotate device" action
        # with a synthetic MOD+r key, without touching the phone's rotation
        # settings.  It is optional; the mirror still works without it.
        xtst = ctypes.util.find_library("Xtst")
        self._xtst = ctypes.CDLL(xtst) if xtst else None
        if self._xtst is not None:
            self._xtst.XTestFakeKeyEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            self._xtst.XTestFakeKeyEvent.restype = ctypes.c_int

        self._dpy = self._lib.XOpenDisplay(self.display_name.encode("utf-8"))
        if not self._dpy:
            raise X11Error(f"Could not open X display {self.display_name!r}.")
        self._utf8_atom = self._lib.XInternAtom(self._dpy, _UTF8_STRING.encode(), False)
        self._net_wm_name_atom = self._lib.XInternAtom(self._dpy, _NET_WM_NAME.encode(), False)
        # Keep the ctypes callback alive for the lifetime of this object.
        self._error_cb = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))(
            self._on_x_error
        )
        self._last_error = 0

    def _configure_signatures(self) -> None:
        lib = self._lib
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.restype = ctypes.c_int
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        lib.XStringToKeysym.restype = ctypes.c_ulong
        lib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XKeysymToKeycode.restype = ctypes.c_ubyte
        lib.XQueryTree.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        lib.XQueryTree.restype = ctypes.c_int
        lib.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
        lib.XFetchName.restype = ctypes.c_int
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.XGetWindowProperty.restype = ctypes.c_int
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XFree.restype = ctypes.c_int
        lib.XReparentWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        lib.XReparentWindow.restype = ctypes.c_int
        lib.XMoveResizeWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        lib.XMoveResizeWindow.restype = ctypes.c_int
        lib.XClearArea.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_int,
        ]
        lib.XClearArea.restype = ctypes.c_int
        lib.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XMapRaised.restype = ctypes.c_int
        lib.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XRaiseWindow.restype = ctypes.c_int
        lib.XSetWindowBorderWidth.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint]
        lib.XSetWindowBorderWidth.restype = ctypes.c_int
        lib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        lib.XSetInputFocus.restype = ctypes.c_int
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XSync.restype = ctypes.c_int
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XFlush.restype = ctypes.c_int
        lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        lib.XQueryPointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        lib.XQueryPointer.restype = ctypes.c_int

    @property
    def display(self) -> int:
        if not self._dpy:
            raise X11Error("The X display connection is closed.")
        return self._dpy

    def close(self) -> None:
        if getattr(self, "_dpy", None):
            self._lib.XCloseDisplay(self._dpy)
            self._dpy = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _on_x_error(self, _display, event) -> int:
        try:
            self._last_error = int(event.contents.error_code)
        except Exception:
            self._last_error = -1
        return 0

    def _with_errors_ignored(self, func):
        """Run one X request, then swallow any async error it generated.

        The handler is only installed across our own request + XSync.  Qt's
        xcb-backed windowing is unaffected by Xlib's global handler.
        """

        previous = self._lib.XSetErrorHandler(ctypes.cast(self._error_cb, ctypes.c_void_p))
        self._last_error = 0
        try:
            result = func()
            self._lib.XSync(self._dpy, False)
            return result
        finally:
            if previous:
                self._lib.XSetErrorHandler(previous)

    def _query_tree(self, window: int) -> list[int]:
        root = ctypes.c_ulong()
        parent = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        ok = self._lib.XQueryTree(
            self._dpy,
            window,
            ctypes.byref(root),
            ctypes.byref(parent),
            ctypes.byref(children),
            ctypes.byref(count),
        )
        if not ok or not children:
            return []
        try:
            return [int(children[index]) for index in range(count.value)]
        finally:
            self._lib.XFree(children)

    def _window_title(self, window: int) -> str:
        # Modern toolkits set _NET_WM_NAME (UTF-8).  SDL3 does as well, but
        # fall back to WM_NAME for older X clients.
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        prop = ctypes.c_void_p()
        status = self._lib.XGetWindowProperty(
            self._dpy,
            window,
            self._net_wm_name_atom,
            0,
            1024,
            False,
            _AnyPropertyType,
            ctypes.byref(actual_type),
            ctypes.byref(actual_format),
            ctypes.byref(nitems),
            ctypes.byref(bytes_after),
            ctypes.byref(prop),
        )
        if status == 0 and prop and nitems.value:
            try:
                raw = ctypes.string_at(prop, int(nitems.value))
                return raw.decode("utf-8", errors="replace")
            finally:
                self._lib.XFree(prop)

        name = ctypes.c_char_p()
        if self._lib.XFetchName(self._dpy, window, ctypes.byref(name)) and name.value:
            try:
                return name.value.decode("utf-8", errors="replace")
            finally:
                self._lib.XFree(name)
        return ""

    def find_window(self, title: str) -> int | None:
        """Return the deepest window carrying *title* — normally the client."""

        root = self._lib.XDefaultRootWindow(self._dpy)
        stack: list[tuple[int, int]] = [(root, 0)]
        best: tuple[int, int] | None = None
        visited = 0
        while stack:
            window, depth = stack.pop()
            visited += 1
            if visited > 20000:
                break
            if window != root and self._window_title(window) == title:
                if best is None or depth > best[1]:
                    best = (window, depth)
            for child in self._query_tree(window):
                stack.append((child, depth + 1))
        return best[0] if best else None

    def _query_cursor(self) -> tuple[int, int] | None:
        root = self._lib.XDefaultRootWindow(self._dpy)
        root_return = ctypes.c_ulong()
        child_return = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        win_x = ctypes.c_int()
        win_y = ctypes.c_int()
        mask = ctypes.c_uint()
        ok = self._lib.XQueryPointer(
            self._dpy,
            root,
            ctypes.byref(root_return),
            ctypes.byref(child_return),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(win_x),
            ctypes.byref(win_y),
            ctypes.byref(mask),
        )
        if not ok:
            return None
        return int(root_x.value), int(root_y.value)

    def embed(self, child: int, parent: int, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))

        def operation() -> None:
            self._lib.XSetWindowBorderWidth(self._dpy, child, 0)
            self._lib.XReparentWindow(self._dpy, child, parent, 0, 0)
            self._lib.XMoveResizeWindow(self._dpy, child, 0, 0, width, height)
            self._lib.XMapRaised(self._dpy, child)
            self._lib.XFlush(self._dpy)

        self._with_errors_ignored(operation)
        if self._last_error:
            raise X11Error(f"X11 refused to embed the scrcpy window (error {self._last_error}).")
        # Focus is a best-effort operation; a refused focus must not turn a
        # successfully embedded picture into a failure.
        self.focus(child)

    def set_geometry(self, child: int, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))

        def operation() -> None:
            self._lib.XMoveResizeWindow(self._dpy, child, 0, 0, width, height)
            self._lib.XFlush(self._dpy)

        self._with_errors_ignored(operation)

    def refresh(self, child: int) -> None:
        """Ask the embedded window to repaint itself.

        SDL (and therefore scrcpy) does not necessarily redraw after its X11
        window is resized by another client.  Clearing the area with exposures
        enabled makes SDL deliver a window-exposed event, which scrcpy handles
        by rendering the current frame at the new window size.
        """

        def operation() -> None:
            self._lib.XClearArea(self._dpy, child, 0, 0, 0, 0, True)
            self._lib.XFlush(self._dpy)

        self._with_errors_ignored(operation)

    def raise_window(self, child: int) -> None:
        def operation() -> None:
            self._lib.XMapRaised(self._dpy, child)
            self._lib.XFlush(self._dpy)

        self._with_errors_ignored(operation)

    def focus(self, child: int) -> None:
        def operation() -> None:
            self._lib.XSetInputFocus(self._dpy, child, _RevertToParent, _CurrentTime)
            self._lib.XFlush(self._dpy)

        self._with_errors_ignored(operation)

    def send_key_combo(self, *keysyms: str) -> bool:
        """Send a modifier+key chord through XTest to the focused window.

        XTest events are ordinary X11 input events, so the scrcpy window
        handles them exactly as if the user had pressed the keys.  Returns
        False when libXtst or a keycode is unavailable.
        """

        if self._xtst is None or not self._dpy or not keysyms:
            return False
        keycodes: list[int] = []
        for name in keysyms:
            keysym = self._lib.XStringToKeysym(name.encode("ascii"))
            if not keysym:
                return False
            keycode = int(self._lib.XKeysymToKeycode(self._dpy, keysym))
            if not keycode:
                return False
            keycodes.append(keycode)
        for keycode in keycodes:
            self._xtst.XTestFakeKeyEvent(self._dpy, keycode, True, 0)
        for keycode in reversed(keycodes):
            self._xtst.XTestFakeKeyEvent(self._dpy, keycode, False, 0)
        self._lib.XFlush(self._dpy)
        self._lib.XSync(self._dpy, False)
        return True

    def iter_window_titles(self) -> Iterator[tuple[int, str]]:
        """Debug helper for tests."""

        root = self._lib.XDefaultRootWindow(self._dpy)
        stack: list[int] = [root]
        while stack:
            window = stack.pop()
            if window != root:
                title = self._window_title(window)
                if title:
                    yield window, title
            stack.extend(self._query_tree(window))
