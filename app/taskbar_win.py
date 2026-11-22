"""REQ-28, Windows only: the coloured dot on the taskbar button (ITaskbarList3.SetOverlayIcon), flashing the
button (FlashWindowEx) and the tray icon. Used by statusicon.StatusIcon from ONE thread (the COM object lives
there). Every method raises on failure; StatusIcon logs that once and carries on."""
from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from app import statusicon

CLSID_TaskbarList = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITaskbarList3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
HRINIT, SET_OVERLAY_ICON = 3, 18          # vtable slots (IUnknown 0-2, ITaskbarList 3-7, ...)
FLASHW_STOP, FLASHW_TRAY, FLASHW_TIMERNOFG = 0, 2, 12
IMAGE_ICON, LR_LOADFROMFILE, SM_CXSMICON = 1, 0x10, 49


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


_u32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
_ole = ctypes.WinDLL("ole32") if os.name == "nt" else None
if _u32 is not None:
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.IsIconic.argtypes = _u32.IsWindowVisible.argtypes = _u32.IsWindow.argtypes = [wintypes.HWND]
    _u32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    _u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
    _u32.LoadImageW.restype = wintypes.HANDLE
    _ole.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
    _ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID),
                                      ctypes.POINTER(ctypes.c_void_p)]


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def _guid(text: str) -> GUID:
    g = GUID()
    _check(_ole.CLSIDFromString(text, ctypes.byref(g)), "CLSIDFromString")
    return g


class WinTaskbar:
    """Platform for StatusIcon. find_hwnd() returns the window handle or 0 (not created yet: retried later)."""

    def __init__(self, find_hwnd, tray=None):
        self.find_hwnd, self.tray = find_hwnd, tray
        self._hwnd = 0
        self._tb = None
        self._icons: dict[str, int] = {}

    # ---------------------------------------------------------------- window
    def hwnd(self) -> int:
        if not (self._hwnd and _u32.IsWindow(self._hwnd)):
            self._hwnd = self.find_hwnd() or 0
        return self._hwnd

    def _need_hwnd(self) -> int:
        h = self.hwnd()
        if not h:
            raise OSError("the AI Team Lite window does not exist yet")
        return h

    def window_in_front(self) -> bool:
        h = self.hwnd()
        return bool(h) and _u32.GetForegroundWindow() == h and not _u32.IsIconic(h)

    def window_visible(self) -> bool:
        h = self.hwnd()
        return bool(h) and bool(_u32.IsWindowVisible(h))

    # ---------------------------------------------------------------- overlay
    def _call(self, slot: int, argtypes: list, *args) -> int:
        vtbl = ctypes.cast(self._tb, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[slot])
        return fn(self._tb, *args)

    def _taskbar(self):
        if self._tb is None:
            _ole.CoInitializeEx(None, 2)  # apartment-threaded; S_FALSE when already done on this thread
            ptr = ctypes.c_void_p()
            clsid, iid = _guid(CLSID_TaskbarList), _guid(IID_ITaskbarList3)
            _check(_ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)),
                   "CoCreateInstance(TaskbarList)")
            self._tb = ptr
            _check(self._call(HRINIT, []), "ITaskbarList3.HrInit")
        return self._tb

    def _icon(self, colour: str) -> int:
        if colour not in self._icons:
            size = _u32.GetSystemMetrics(SM_CXSMICON) or 16
            path = Path(tempfile.gettempdir()) / f"aiteamlite-dot-{colour}-{size}.ico"
            statusicon.dot_image(colour, size).save(path, format="ICO", sizes=[(size, size)])
            h = _u32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            self._icons[colour] = h
        return self._icons[colour]

    def set_overlay(self, colour: str, tooltip: str) -> int:
        h = self._need_hwnd()
        self._taskbar()
        hr = self._call(SET_OVERLAY_ICON, [wintypes.HWND, wintypes.HICON, wintypes.LPCWSTR], h, self._icon(colour),
                        tooltip)
        _check(hr, "ITaskbarList3.SetOverlayIcon")
        return hr

    # ---------------------------------------------------------------- flash and tray
    def flash(self, on: bool) -> int:
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), self._need_hwnd(),
                          FLASHW_TRAY | FLASHW_TIMERNOFG if on else FLASHW_STOP, 0, 0)
        return _u32.FlashWindowEx(ctypes.byref(info))  # returns the previous flash state, not an error code

    def set_tray(self, colour: str, tooltip: str) -> None:
        if self.tray is not None:
            self.tray.set_status(colour, tooltip)


def find_own_window(title: str) -> int:
    """The top-level window of THIS process with exactly this title (pywebview's window), else 0."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _lp):
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid():
            buf = ctypes.create_unicode_buffer(512)
            _u32.GetWindowTextW(h, buf, 512)
            if buf.value == title:
                found.append(h)
                return False
        return True
    _u32.EnumWindows(each, 0)
    return found[0] if found else 0
