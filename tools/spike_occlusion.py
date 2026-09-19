"""S2 - does Chrome keep rendering video while our overlay covers it?

Chrome stops painting when it believes its window is fully hidden ("native window
occlusion"). Our stats overlay covers Chrome completely, so this has to be measured.

Timeline:  baseline (no overlay)  ->  overlay covers Chrome for --seconds  ->  overlay hidden.

The overlay is the real thing: frameless, always-on-top, no-activate Qt *tool* window with
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), painted solid magenta so that any leak into
a capture is obvious from pixel colour alone. Throughout, twice a second, the script samples
  - the Chrome window through Windows Graphics Capture and through DXGI duplication:
    did the picture change? how many frames did WGC deliver? is there magenta in it?
  - the foreground window, Chrome's rect / fullscreen state, and the window under the
    centre of Chrome (proves the overlay really was on top).

PASS = picture keeps changing under the overlay on at least one capture path, no magenta in
any capture, focus never moved to the overlay, Chrome's window state unchanged.

Usage:  python tools\\spike_occlusion.py --label fullscreen --wait fullscreen
        python tools\\spike_occlusion.py --label windowed   --wait foreground
Click the magenta overlay to abort early.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import threading
import time
from ctypes import wintypes

import _spike
from _spike import OUT, frame_diff, verdict, win32

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
TICK_MS = 500
BASELINE_S = 8.0
AFTER_S = 5.0
CHANGED = 0.3  # mean abs gray-level difference that counts as "the picture changed"
SMALL = (480, 270)

win32.user32.WindowFromPoint.restype = wintypes.HWND
win32.user32.WindowFromPoint.argtypes = [wintypes.POINT]
win32.user32.GetAncestor.restype = wintypes.HWND
win32.user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]


def top_window_at(x: int, y: int) -> int:
    hwnd = win32.user32.WindowFromPoint(wintypes.POINT(x, y))
    return win32.user32.GetAncestor(hwnd, 2) or 0 if hwnd else 0  # GA_ROOT


def shrink(frame_bgr: np.ndarray) -> np.ndarray:
    return cv2.resize(frame_bgr, SMALL, interpolation=cv2.INTER_AREA)


def centre(small: np.ndarray) -> np.ndarray:
    h, w = small.shape[:2]
    return small[h // 5 : h * 4 // 5, w // 5 : w * 4 // 5]


def magenta_fraction(small_bgr: np.ndarray) -> float:
    b, g, r = (centre(small_bgr)[:, :, i].astype(np.int16) for i in range(3))
    return float(((r > 200) & (b > 200) & (g < 70)).mean())


class Overlay(QtWidgets.QWidget):
    clicked = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(255, 0, 255))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.label = QtWidgets.QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color: white; font: 700 40px 'Segoe UI';")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.label)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.clicked.emit()


class Spike(QtCore.QObject):
    def __init__(self, app: QtWidgets.QApplication, target: win32.WindowInfo, args) -> None:
        super().__init__()
        self.app, self.target, self.args = app, target, args
        self.rows: list[dict] = []
        self.sheet: dict[str, list[tuple[float, np.ndarray]]] = {"wgc": [], "dxgi": []}
        self.phase = "baseline"
        self.aborted = False
        self.started_fullscreen = win32.is_fullscreen(target.hwnd)
        self.start_rect = target.rect

        self.overlay = Overlay()
        self.overlay.clicked.connect(self.abort)
        self.overlay_hwnd = int(self.overlay.winId())  # creates the native window, still hidden
        self.affinity_ok = win32.set_exclude_from_capture(self.overlay_hwnd)

        # WGC: count every delivered frame (WGC only delivers when the window repaints, so
        # the delivery rate is itself a measure of whether Chrome is still rendering).
        self.lock = threading.Lock()
        self.wgc_small: np.ndarray | None = None
        self.wgc_frames = 0
        self._wgc_next_copy = 0.0
        from windows_capture import WindowsCapture

        self.wgc = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=target.hwnd)
        self.wgc.event(self.on_frame_arrived)
        self.wgc.event(self.on_closed)
        self.wgc_control = self.wgc.start_free_threaded()

        import dxcam

        self.region = _spike.clip_rect(target.rect, win32.monitor_rect(target.hwnd))
        self.camera = dxcam.create(output_color="BGR")
        self.dxgi_small: np.ndarray | None = None

        self.prev = {"wgc": None, "dxgi": None}
        self.prev_wgc_frames = 0
        self.t0 = time.perf_counter()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)

    # --- WGC callbacks (capture thread) -------------------------------------------------
    def on_frame_arrived(self, frame, control) -> None:
        now = time.perf_counter()
        with self.lock:
            self.wgc_frames += 1
            due = now >= self._wgc_next_copy
            if due:
                self._wgc_next_copy = now + 0.2
        if due:
            small = shrink(frame.frame_buffer[:, :, :3])
            with self.lock:
                self.wgc_small = small

    def on_closed(self) -> None:
        pass

    # --- main-thread timeline -----------------------------------------------------------
    def abort(self) -> None:
        self.aborted = True
        self.finish()

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        if phase == "overlay":
            left, top, right, bottom = self.target.rect
            ratio = self.app.primaryScreen().devicePixelRatio()
            self.overlay.setGeometry(*(int(v / ratio) for v in (left, top, right - left, bottom - top)))
            self.overlay.show()
        else:
            self.overlay.hide()
        print(f"  [{time.perf_counter() - self.t0:5.1f}s] phase -> {phase}", flush=True)

    def tick(self) -> None:
        elapsed = time.perf_counter() - self.t0
        overlay_end = BASELINE_S + self.args.seconds
        phase = "baseline" if elapsed < BASELINE_S else "overlay" if elapsed < overlay_end else "after"
        if phase != self.phase:
            self.set_phase(phase)
        if elapsed >= overlay_end + AFTER_S:
            self.finish()
            return
        if phase == "overlay":
            self.overlay.label.setText(
                f"S2 overlay test\n{overlay_end - elapsed:.0f} s left\n\nclick anywhere to abort"
            )

        with self.lock:
            wgc_small, wgc_frames = self.wgc_small, self.wgc_frames
        grabbed = self.camera.grab(region=self.region)  # None = desktop image unchanged since last grab
        if grabbed is not None:
            self.dxgi_small = shrink(grabbed)

        row = {"t": round(elapsed, 2), "phase": phase}
        for name, small in (("wgc", wgc_small), ("dxgi", self.dxgi_small)):
            if small is None:
                row[f"{name}_diff"], row[f"{name}_magenta"] = None, None
                continue
            gray = cv2.cvtColor(centre(small), cv2.COLOR_BGR2GRAY)
            previous = self.prev[name]
            row[f"{name}_diff"] = round(frame_diff(previous, gray), 3) if previous is not None else None
            row[f"{name}_magenta"] = round(magenta_fraction(small), 4)
            self.prev[name] = gray
            if phase == "overlay" and (not self.sheet[name] or elapsed - self.sheet[name][-1][0] >= 5.0):
                self.sheet[name].append((elapsed, small.copy()))
        row["wgc_fps"] = round((wgc_frames - self.prev_wgc_frames) / (TICK_MS / 1000), 1)
        self.prev_wgc_frames = wgc_frames

        foreground = win32.foreground_window()
        rect = win32.window_rect(self.target.hwnd)
        row.update(
            fg_is_chrome=foreground == self.target.hwnd,
            fg_is_overlay=foreground == self.overlay_hwnd,
            fg_title=win32.window_title(foreground)[:40],
            chrome_rect_same=rect == self.start_rect,
            chrome_fullscreen=win32.is_fullscreen(self.target.hwnd),
            overlay_on_top=top_window_at((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2) == self.overlay_hwnd,
            # Read while shown: GetWindowDisplayAffinity reports 0 for a hidden window even
            # though the setting survives hide/show.
            overlay_affinity=win32.display_affinity(self.overlay_hwnd),
        )
        self.rows.append(row)

    # --- wrap-up ------------------------------------------------------------------------
    def finish(self) -> None:
        self.timer.stop()
        self.overlay.hide()
        self.wgc_control.stop()
        self.camera.release()
        try:
            self.report()
        finally:
            self.app.quit()

    def path_metrics(self, name: str, phase: str) -> dict:
        diffs = [r[f"{name}_diff"] for r in self.rows if r["phase"] == phase and r[f"{name}_diff"] is not None]
        diffs = diffs[2:] if phase == "overlay" else diffs  # skip the show transition itself
        streak = longest = 0
        for d in diffs:
            streak = streak + 1 if d <= CHANGED else 0
            longest = max(longest, streak)
        return {
            "ticks": len(diffs),
            "changed_fraction": round(sum(d > CHANGED for d in diffs) / len(diffs), 3) if diffs else 0.0,
            "longest_frozen_s": longest * TICK_MS / 1000,
            "median_diff": round(float(np.median(diffs)), 2) if diffs else None,
        }

    def report(self) -> None:
        label = self.args.label
        with open(OUT / f"s2_{label}_ticks.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.rows[0].keys()))
            writer.writeheader()
            writer.writerows(self.rows)
        for name, samples in self.sheet.items():
            if samples:
                tiles = []
                for t, small in samples[:12]:
                    tile = small.copy()
                    cv2.putText(tile, f"{name} t={t:.0f}s", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    tiles.append(tile)
                while len(tiles) % 4:
                    tiles.append(np.zeros_like(tiles[0]))
                grid = np.vstack([np.hstack(tiles[i : i + 4]) for i in range(0, len(tiles), 4)])
                cv2.imwrite(str(OUT / f"s2_{label}_{name}_under_overlay.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 85])

        during = [r for r in self.rows if r["phase"] == "overlay"][2:]
        metrics = {n: {p: self.path_metrics(n, p) for p in ("baseline", "overlay", "after")} for n in ("wgc", "dxgi")}
        wgc_fps = {
            p: round(float(np.mean([r["wgc_fps"] for r in self.rows if r["phase"] == p] or [0])), 1)
            for p in ("baseline", "overlay", "after")
        }

        def renders(name: str) -> bool:
            m = metrics[name]["overlay"]
            return m["ticks"] > 0 and m["changed_fraction"] >= 0.5 and m["longest_frozen_s"] <= 8.0

        max_magenta = max([r[f"{n}_magenta"] or 0.0 for r in during for n in ("wgc", "dxgi")] or [0.0])
        checks = {
            "overlay_was_on_top": bool(during) and all(r["overlay_on_top"] for r in during),
            "capture_exclusion_set": self.affinity_ok
            and bool(during)
            and all(r["overlay_affinity"] == win32.WDA_EXCLUDEFROMCAPTURE for r in during),
            "wgc_keeps_rendering": renders("wgc"),
            "dxgi_keeps_rendering": renders("dxgi"),
            "overlay_not_in_captures": max_magenta < 0.02,
            "focus_never_on_overlay": not any(r["fg_is_overlay"] for r in self.rows),
            "focus_stayed_on_chrome": bool(during) and all(r["fg_is_chrome"] for r in during),
            "chrome_rect_unchanged": all(r["chrome_rect_same"] for r in self.rows),
            "chrome_stayed_fullscreen": (not self.started_fullscreen) or all(r["chrome_fullscreen"] for r in self.rows),
            "ran_to_completion": not self.aborted,
        }
        ex_style = win32.window_ex_style(self.overlay_hwnd)
        summary = {
            "label": label,
            "chrome_title": self.target.title,
            "chrome_rect": self.target.rect,
            "started_fullscreen": self.started_fullscreen,
            "overlay_seconds": self.args.seconds,
            "overlay_ex_style": {
                "hex": f"{ex_style:#010x}",
                "WS_EX_TOOLWINDOW": bool(ex_style & win32.WS_EX_TOOLWINDOW),
                "WS_EX_NOACTIVATE": bool(ex_style & win32.WS_EX_NOACTIVATE),
                "WS_EX_TOPMOST": bool(ex_style & win32.WS_EX_TOPMOST),
                "WS_EX_LAYERED": bool(ex_style & win32.WS_EX_LAYERED),
            },
            "wgc_delivered_fps": wgc_fps,
            "max_magenta_fraction_in_captures": max_magenta,
            "metrics": metrics,
            "checks": checks,
        }
        # Either capture path seeing a live picture proves Chrome isn't occlusion-throttled.
        must = [k for k in checks if k not in ("wgc_keeps_rendering", "dxgi_keeps_rendering", "focus_stayed_on_chrome")]
        ok = all(checks[k] for k in must) and (checks["wgc_keeps_rendering"] or checks["dxgi_keeps_rendering"])
        summary["ok"] = ok
        _spike.write_json(f"s2_{label}.json", summary)

        print(f"\nOverlay ex-style: {summary['overlay_ex_style']}")
        print(f"WGC delivered fps  baseline/overlay/after: {wgc_fps}")
        for name in ("wgc", "dxgi"):
            for phase in ("baseline", "overlay", "after"):
                print(f"  {name:>4} {phase:>8}: {metrics[name][phase]}")
        print(f"Max magenta fraction in any capture while overlay was up: {max_magenta:.4f}")
        for key, value in checks.items():
            print(f"  {verdict(value)}  {key}")
        if not checks["focus_stayed_on_chrome"]:
            print("  (focus_stayed_on_chrome is informational: it fails if Chrome wasn't focused to begin with)")
        print(f"\nS2 [{label}] {verdict(ok)}   evidence: {OUT}\\s2_{label}*.*")
        self.exit_code = 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="YouTube TV")
    parser.add_argument("--label", default="fullscreen")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long the overlay covers Chrome")
    parser.add_argument("--wait", choices=["none", "foreground", "fullscreen"], default="none",
                        help="start once the stream window is focused / fullscreen-and-focused")
    args = parser.parse_args()

    app = QtWidgets.QApplication([])  # first: Qt makes the process per-monitor DPI aware
    if args.wait != "none":
        def ready() -> bool:
            focused = _spike.stream_is_foreground(args.title)
            return focused and (args.wait == "foreground" or _spike.stream_is_fullscreen(args.title))

        if not _spike.wait_until(ready, 180, f"the stream window to be {args.wait}"):
            print(f"FAIL: the stream window never became {args.wait}.")
            return 2
        time.sleep(4.0)  # player controls fade out

    target = _spike.find_target(args.title)
    print(f"Target: hwnd={target.hwnd:#x} rect={target.rect} fullscreen={win32.is_fullscreen(target.hwnd)}")
    print(f"Timeline: {BASELINE_S:.0f} s baseline, {args.seconds:.0f} s overlay, {AFTER_S:.0f} s after. Hands off please.")
    spike = Spike(app, target, args)
    spike.exit_code = 1
    app.exec()
    return spike.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
