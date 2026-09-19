"""Global hotkeys through Win32 RegisterHotKey.

RegisterHotKey is handled by the system before the focused application sees the key, so it
works while Chrome is fullscreen and focused, needs no hooks and no admin rights. The price:
a registered combination is swallowed system-wide while the app runs, and a combination
another program already registered can't be taken (RegisterHotKey fails, we say so).
F12 alone is reserved by Windows for the debugger and can never be registered.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "insert": 0x2D, "delete": 0x2E, "pause": 0x13, "scrolllock": 0x91,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
    **{f"numpad{n}": 0x60 + n for n in range(10)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(n): 0x30 + n for n in range(10)},
}

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class HotkeyError(Exception):
    pass


def parse_hotkey(text: str) -> tuple[int, int]:
    """'F9' -> (0, 0x78);  'ctrl+shift+f9' -> (MOD_CONTROL | MOD_SHIFT, 0x78)."""
    parts = [p.strip().lower() for p in text.split("+")]
    if not parts or not all(parts):
        raise HotkeyError(f"empty hotkey: {text!r}")
    *modifier_names, key_name = parts
    modifiers = 0
    for name in modifier_names:
        if name not in _MODIFIERS:
            raise HotkeyError(f"unknown modifier {name!r} in hotkey {text!r} (use ctrl, alt, shift, win)")
        modifiers |= _MODIFIERS[name]
    if key_name not in _KEYS:
        raise HotkeyError(f"unknown key {key_name!r} in hotkey {text!r} (letters, digits, f1-f24, numpad0-9, pause, ...)")
    if key_name == "f12" and not modifiers:
        raise HotkeyError("F12 alone is reserved by Windows for the debugger; pick another key")
    return modifiers, _KEYS[key_name]


class HotkeyListener(threading.Thread):
    """Owns the registrations and the message loop they need, on its own thread.

    ``bindings`` maps a hotkey string to a callback ``fn(pressed_at)`` where ``pressed_at``
    is ``time.perf_counter()`` when WM_HOTKEY arrived. Callbacks run on this thread: keep
    them tiny (emit a Qt signal, set an event).
    """

    def __init__(self, bindings: dict[str, Callable[[float], None]]) -> None:
        super().__init__(name="hotkeys", daemon=True)
        self._bindings = [(text, *parse_hotkey(text), callback) for text, callback in bindings.items()]
        self._ready = threading.Event()
        self._error: HotkeyError | None = None
        self._thread_id = 0

    def start_and_wait(self) -> None:
        """Start the thread; raise HotkeyError if any combination couldn't be registered."""
        self.start()
        self._ready.wait()
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered: list[int] = []
        try:
            for hotkey_id, (text, modifiers, vk, _callback) in enumerate(self._bindings, start=1):
                if not user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                    code = ctypes.get_last_error()
                    reason = "another program already uses it" if code == ERROR_HOTKEY_ALREADY_REGISTERED else f"Win32 error {code}"
                    self._error = HotkeyError(f"can't register hotkey {text!r}: {reason}")
                    return
                registered.append(hotkey_id)
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and 1 <= message.wParam <= len(self._bindings):
                    self._bindings[message.wParam - 1][3](time.perf_counter())
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self._ready.set()
