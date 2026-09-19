"""Shared bits for the Phase 0 spike scripts (tools/spike_*.py).

Importing this module puts the repo root on sys.path (so ``import adblock`` works when a
spike is run as ``python tools\\spike_x.py``) and creates ``spike_out/``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "spike_out"
OUT.mkdir(exist_ok=True)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from adblock import win32  # noqa: E402

# A frame counts as "DRM black" when the video area is essentially one flat black slab.
BLACK_MEAN_MAX = 4.0
BLACK_FRACTION_MIN = 0.97


def video_area(frame: np.ndarray) -> np.ndarray:
    """Central 60% x 60% of a captured window: inside the player, clear of browser chrome."""
    h, w = frame.shape[:2]
    return frame[int(h * 0.2) : int(h * 0.8), int(w * 0.2) : int(w * 0.8)]


def gray_small(frame_bgr: np.ndarray, width: int = 320) -> np.ndarray:
    """Small grayscale copy of the video area, for cheap frame-to-frame differencing."""
    crop = video_area(frame_bgr)
    h, w = crop.shape[:2]
    small = cv2.resize(crop, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def luma_stats(frame_bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(video_area(frame_bgr), cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    black_fraction = float((gray < 8).mean())
    return {
        "mean_luma": round(mean, 2),
        "std_luma": round(float(gray.std()), 2),
        "black_fraction": round(black_fraction, 4),
        "is_black": bool(mean <= BLACK_MEAN_MAX and black_fraction >= BLACK_FRACTION_MIN),
    }


def frame_diff(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    """Mean absolute difference in gray levels (0 = identical frames)."""
    if a_gray.shape != b_gray.shape:
        return 255.0
    return float(cv2.absdiff(a_gray, b_gray).mean())


def save_png_and_preview(frame_bgr: np.ndarray, stem: str) -> None:
    """Full-size PNG as evidence, plus a 960px-wide JPEG that is cheap to eyeball."""
    cv2.imwrite(str(OUT / f"{stem}.png"), frame_bgr)
    h, w = frame_bgr.shape[:2]
    scale = 960 / w if w > 960 else 1.0
    preview = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / f"{stem}_preview.jpg"), preview, [cv2.IMWRITE_JPEG_QUALITY, 85])


def write_json(name: str, data: dict) -> Path:
    path = OUT / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def clip_rect(rect: win32.Rect, bounds: win32.Rect) -> win32.Rect:
    return (max(rect[0], bounds[0]), max(rect[1], bounds[1]), min(rect[2], bounds[2]), min(rect[3], bounds[3]))


def find_target(title_contains: str) -> win32.WindowInfo:
    """The Chrome window to test against, or exit with a list of what was found instead."""
    target = win32.find_stream_window(title_contains)
    if target is None:
        print(f"FAIL: no visible chrome.exe window with '{title_contains}' in its title.")
        others = win32.find_chrome_windows()
        for info in others:
            print(f"      found instead: hwnd={info.hwnd:#x} {info.rect} {info.title!r}")
        if not others:
            print("      (no chrome.exe windows at all)")
        print("      Put the stream on screen, or pass --title with part of the tab's title.")
        sys.exit(2)
    return target


def wait_until(predicate, timeout: float, what: str) -> bool:
    """Poll until ``predicate()`` is true. Lets a spike start itself the moment the user has
    put Chrome in the state under test, instead of racing a fixed countdown."""
    print(f"Waiting up to {timeout:.0f} s for {what} ...", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def stream_is_fullscreen(title_contains: str) -> bool:
    target = win32.find_stream_window(title_contains)
    return target is not None and win32.is_fullscreen(target.hwnd)


def stream_is_foreground(title_contains: str) -> bool:
    target = win32.find_stream_window(title_contains)
    return target is not None and win32.foreground_window() == target.hwnd


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"
