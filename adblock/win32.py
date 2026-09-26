"""Thin ctypes wrappers for the handful of Win32 calls the app needs.

Deliberately no pywin32: everything is user32 / dwmapi / kernel32 through ctypes, with
argtypes declared so 64-bit handles are never truncated.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

Rect = tuple[int, int, int, int]  # left, top, right, bottom - physical pixels

CHROME_EXE = "chrome.exe"
CHROME_WINDOW_CLASS = "Chrome_WidgetWin_1"  # also used by every Electron app, so always check the exe

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11

GW_HWNDPREV = 3  # the window above this one in the z-order
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _declare(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)


_declare(user32.EnumWindows, wintypes.BOOL, _WNDENUMPROC, wintypes.LPARAM)
_declare(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_declare(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_declare(user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_declare(user32.GetWindowTextLengthW, ctypes.c_int, wintypes.HWND)
_declare(user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_declare(user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_declare(user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_declare(user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_declare(user32.GetForegroundWindow, wintypes.HWND)
_declare(user32.GetWindow, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_declare(user32.MonitorFromWindow, wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD)
_declare(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_declare(user32.GetWindowLongPtrW, ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int)
_declare(user32.SetWindowDisplayAffinity, wintypes.BOOL, wintypes.HWND, wintypes.DWORD)
_declare(user32.GetWindowDisplayAffinity, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_declare(user32.SetProcessDpiAwarenessContext, wintypes.BOOL, ctypes.c_void_p)
_declare(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
_declare(kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_declare(kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_declare(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    exe: str  # lower-case basename, e.g. "chrome.exe"
    rect: Rect  # visible bounds (DWM extended frame bounds), physical pixels

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def set_dpi_awareness() -> None:
    """Per-monitor-v2 DPI awareness so window rects are physical pixels.

    Must run before any window is created. Fails harmlessly if something (e.g. Qt)
    already set it.
    """
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_exe(pid: int) -> str:
    """Lower-case basename of the process image, or "" if it can't be opened."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)


def window_rect(hwnd: int) -> Rect:
    """Visible window bounds. GetWindowRect includes invisible resize borders; DWM's doesn't."""
    rect = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def monitor_rect(hwnd: int) -> Rect:
    """Full rectangle of the monitor the window is (mostly) on."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    r = info.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def is_on_screen(hwnd: int) -> bool:
    """A window the user can actually see. Invisible helper windows can hold the foreground:
    on 2026-09-19 GameInputSvc.exe's hidden 136x39 'GameInputServiceWindow' (a SYSTEM
    service) sat there for minutes while fullscreen Chrome was what the user was watching."""
    return bool(user32.IsWindowVisible(hwnd)) and not is_minimized(hwnd) and not is_cloaked(hwnd)


def overlap_size(a: Rect, b: Rect) -> tuple[int, int]:
    """Width and height of the intersection of two rects (0, 0 when they don't intersect)."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return (width, height) if width > 0 and height > 0 else (0, 0)


def app_window_in_front(hwnd: int, ignore: tuple[int, ...] = (), min_overlap: int = 100) -> int:
    """The first ordinary application window stacked *above* ``hwnd`` that covers part of it, or 0.

    Z-order, not focus, is what says the user has put something in front of the stream: the
    foreground window can be invisible (GameInputSvc), or be the desktop - which spans every
    monitor yet sits behind everything. Topmost and tool windows are skipped: widgets, OSDs
    and our own overlay float above Chrome without meaning the user has moved on.
    """
    target = window_rect(hwnd)
    above = user32.GetWindow(hwnd, GW_HWNDPREV)
    for _ in range(2000):  # the z-order can change under us; never walk forever
        if not above:
            return 0
        if above not in ignore and is_on_screen(above) and not window_ex_style(above) & (WS_EX_TOPMOST | WS_EX_TOOLWINDOW):
            width, height = overlap_size(window_rect(above), target)
            if width >= min_overlap and height >= min_overlap:
                return above
        above = user32.GetWindow(above, GW_HWNDPREV)
    return 0


def is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return hr == 0 and cloaked.value != 0


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_fullscreen(hwnd: int) -> bool:
    """True when the window covers its whole monitor (Chrome's F11 / video fullscreen)."""
    return window_rect(hwnd) == monitor_rect(hwnd)


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_style(hwnd: int) -> int:
    return user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def window_ex_style(hwnd: int) -> int:
    return user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


def set_exclude_from_capture(hwnd: int, exclude: bool = True) -> bool:
    """Hide a window of *this process* from screen capture (WGC, DXGI duplication, PrintWindow)."""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))


def display_affinity(hwnd: int) -> int:
    affinity = wintypes.DWORD(0)
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    return affinity.value


def describe_window(hwnd: int) -> WindowInfo:
    pid = window_pid(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        title=window_title(hwnd),
        class_name=window_class(hwnd),
        pid=pid,
        exe=process_exe(pid),
        rect=window_rect(hwnd),
    )


def top_level_windows() -> list[int]:
    hwnds: list[int] = []

    @_WNDENUMPROC
    def _collect(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(_collect, 0)
    return hwnds


def title_matches(title: str, title_contains: str | None) -> bool:
    """Does the window title name the stream? ``title_contains`` lists alternatives separated
    by ``|`` ("YouTube TV|Fubo"), matched case-insensitively; an empty setting matches nothing."""
    needles = [part.strip().lower() for part in (title_contains or "").split("|") if part.strip()]
    return any(needle in title.lower() for needle in needles)


def find_chrome_windows(title_contains: str | None = None) -> list[WindowInfo]:
    """Visible, titled, top-level windows that belong to chrome.exe.

    Best candidate first: title matches (case-insensitive) beat non-matches, then bigger
    beats smaller. With ``title_contains`` set, non-matching windows are still returned
    (after the matches) so callers can report what *was* found.
    """
    found: list[WindowInfo] = []
    for hwnd in top_level_windows():
        if not user32.IsWindowVisible(hwnd) or is_cloaked(hwnd):
            continue
        if window_class(hwnd) != CHROME_WINDOW_CLASS:
            continue
        info = describe_window(hwnd)
        if info.exe != CHROME_EXE or not info.title:
            continue
        found.append(info)
    found.sort(key=lambda w: (title_matches(w.title, title_contains), w.width * w.height), reverse=True)
    return found


def find_stream_window(title_contains: str = "YouTube TV") -> WindowInfo | None:
    """The Chrome window showing the stream: a title match, else None."""
    for info in find_chrome_windows(title_contains):
        if title_matches(info.title, title_contains) and not is_minimized(info.hwnd):
            return info
    return None
