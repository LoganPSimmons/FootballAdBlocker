"""End-to-end check of the Phase 1 app against the live stream, no human needed.

Starts ``python -m adblock.app`` as a child process, then from the outside:
  presses the hotkey with injected keystrokes (they take the same RegisterHotKey path as the
  keyboard), and checks that Chrome's audio sessions mute/unmute, the overlay window
  shows/hides over Chrome's rectangle, focus stays where it was and Chrome keeps its window
  state; quits the app with the quit hotkey; then validates the recorded session: timeline
  events, GAME/AD labels on frames, and that the picture kept changing *under the overlay*.

Writes a contact sheet of the recorded frames to spike_out/ so the labels can be eyeballed.

Usage:  python tools\\smoke_app.py [--game utah] [--ad-seconds 8]
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time

import _spike
from _spike import OUT, ROOT, verdict, win32

import cv2
import numpy as np

from adblock.actuators import audio_mute
from adblock.actuators.overlay import WINDOW_TITLE
from adblock.capture import FileReplaySource
from adblock.hotkeys import parse_hotkey

KEYEVENTF_KEYUP = 0x2
MODIFIER_VKS = {0x1: 0x12, 0x2: 0x11, 0x4: 0x10, 0x8: 0x5B}  # alt, ctrl, shift, win


def press(hotkey: str) -> None:
    modifiers, vk = parse_hotkey(hotkey)
    held = [code for bit, code in MODIFIER_VKS.items() if modifiers & bit]
    for code in held + [vk]:
        win32.user32.keybd_event(code, 0, 0, 0)
    for code in [vk] + held[::-1]:
        win32.user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)


def overlay_window() -> int:
    """The app's overlay (visible or not). Found by title, not by pid: on Windows the venv's
    python.exe is a launcher stub, so Popen.pid is not the process that owns the window."""
    for hwnd in win32.top_level_windows():
        if win32.window_title(hwnd) == WINDOW_TITLE:
            return hwnd
    return 0


def injectable() -> bool:
    """Windows silently drops injected input while the foreground window belongs to a
    higher-integrity process (seen: GameInputSvc.exe's hidden window). Physical keys are
    unaffected; only this test's synthetic ones are."""
    foreground = win32.foreground_window()
    return bool(foreground) and win32.process_exe(win32.window_pid(foreground)) != ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", default="utah")
    parser.add_argument("--hotkey", default="F9")
    parser.add_argument("--quit-hotkey", default="ctrl+shift+F9")
    parser.add_argument("--ad-seconds", type=float, default=8.0)
    args = parser.parse_args()

    win32.set_dpi_awareness()
    chrome = _spike.find_target("YouTube TV")
    if not _spike.wait_until(injectable, 180, "a normal foreground window (click once on the video)"):
        print("FAIL: foreground window stayed with a system process; injected keys would be dropped.")
        return 2
    before = {"fullscreen": win32.is_fullscreen(chrome.hwnd), "rect": chrome.rect, "foreground": win32.foreground_window()}
    print(f"Chrome {chrome.hwnd:#x} rect={chrome.rect} fullscreen={before['fullscreen']} "
          f"foreground_is_chrome={before['foreground'] == chrome.hwnd}")

    log_path = OUT / "smoke_app_output.txt"
    with open(log_path, "w", encoding="utf-8") as log:
        app = subprocess.Popen(
            [sys.executable, "-u", "-m", "adblock.app", "--game", args.game, "--label", "smoke", "--delay", "5",
             "--hotkey", args.hotkey, "--quit-hotkey", args.quit_hotkey, "--duration", str(args.ad_seconds + 40)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
    checks: dict[str, bool] = {}
    try:
        time.sleep(7.0)  # start-up: hotkeys registered, capture attached, first ESPN snapshot ageing
        overlay = overlay_window()
        checks["app_running_with_overlay_window"] = app.poll() is None and overlay != 0
        checks["starts_unmuted_and_hidden"] = audio_mute.is_muted() is False and not win32.user32.IsWindowVisible(overlay)

        press(args.hotkey)
        time.sleep(1.0)
        checks["AD_chrome_muted"] = audio_mute.is_muted() is True
        checks["AD_overlay_visible_over_chrome"] = bool(win32.user32.IsWindowVisible(overlay)) and win32.window_rect(overlay) == win32.window_rect(chrome.hwnd)
        checks["AD_overlay_excluded_from_capture"] = win32.display_affinity(overlay) == win32.WDA_EXCLUDEFROMCAPTURE
        checks["AD_focus_unchanged"] = win32.foreground_window() == before["foreground"]
        checks["AD_chrome_window_state_unchanged"] = win32.is_fullscreen(chrome.hwnd) == before["fullscreen"] and win32.window_rect(chrome.hwnd) == before["rect"]
        time.sleep(args.ad_seconds - 1.0)

        press(args.hotkey)
        time.sleep(1.0)
        checks["GAME_chrome_unmuted"] = audio_mute.is_muted() is False
        checks["GAME_overlay_hidden"] = not win32.user32.IsWindowVisible(overlay)
        time.sleep(2.0)

        press(args.quit_hotkey)
        try:
            app.wait(timeout=10)
            checks["quit_hotkey_exits_cleanly"] = app.returncode == 0
        except subprocess.TimeoutExpired:
            checks["quit_hotkey_exits_cleanly"] = False
    finally:
        if app.poll() is None:  # /T: the interpreter is a child of the venv launcher stub
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        audio_mute.set_muted(False)  # whatever happened above, leave Chrome audible

    print("\n--- app output ---")
    print(log_path.read_text(encoding="utf-8").rstrip())

    sessions = sorted((ROOT / "sessions").glob("*_smoke*"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        print("FAIL: no session folder was written")
        return 1
    (sessions[-1] / "SYNTHETIC_LABELS.txt").write_text(
        "Recorded by tools/smoke_app.py: the GAME/AD labels are scripted key presses, not a viewer's judgement.\n"
        "Never train or evaluate a detector on this session.\n", encoding="utf-8")
    replay = FileReplaySource(sessions[-1])
    events = replay.events()
    kinds = [e["event"] for e in events]
    states = [(e["state"], e["source"]) for e in events if e["event"] == "state"]
    frames = list(replay)
    labels = [f.label for f in frames]
    ad = [f for f in frames if f.label == AD_LABEL]
    gray = [cv2.cvtColor(f.image, cv2.COLOR_BGR2GRAY) for f in ad]
    ad_diffs = [float(cv2.absdiff(a, b).mean()) for a, b in zip(gray, gray[1:])]
    span = frames[-1].t - frames[0].t if len(frames) > 1 else 0.0
    sizes = [p.stat().st_size for p in (sessions[-1] / "frames").glob("*.jpg")]

    checks["timeline_has_start_capture_end"] = kinds[0] == "session_start" and "capture" in kinds and kinds[-1] == "session_end"
    checks["timeline_states_AD_then_GAME_by_hotkey"] = states[:2] == [("AD", "hotkey"), ("GAME", "hotkey")]
    checks["timeline_logs_three_hotkey_presses"] = kinds.count("hotkey") == 3
    checks["frames_labelled_GAME_AD_GAME"] = bool(ad) and labels[0] == "GAME" and labels[-1] == "GAME"
    checks["picture_kept_changing_under_overlay"] = len(ad_diffs) >= 3 and sum(d > 0.3 for d in ad_diffs) >= 0.7 * len(ad_diffs)
    checks["overlay_content_logged"] = "overlay" in kinds

    print(f"\nSession: {sessions[-1]}")
    print(f"  {len(frames)} frames over {span:.1f} s = {len(frames) / span if span else 0:.2f} fps; {len(ad)} labelled AD; "
          f"frame size {frames[0].image.shape[1]}x{frames[0].image.shape[0]}; mean JPEG {np.mean(sizes) / 1024:.1f} KB")
    print(f"  AD frame-to-frame diffs: {[round(d, 1) for d in ad_diffs]}")
    for event in events:
        if event["event"] in ("state", "hotkey", "overlay", "capture", "window", "recording"):
            print("  ", {k: v for k, v in event.items() if k != "wall"})

    tiles = []
    for frame in frames[:: max(1, len(frames) // 12)][:12]:
        tile = cv2.resize(frame.image, (320, 180))
        colour = (0, 0, 255) if frame.label == AD_LABEL else (0, 200, 0)
        cv2.putText(tile, f"{frame.label} t={frame.t:.1f}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        tiles.append(tile)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i: i + 4]) for i in range(0, len(tiles), 4)])
    cv2.imwrite(str(OUT / "smoke_app_frames.jpg"), sheet)

    print()
    for name, ok in checks.items():
        print(f"  {verdict(ok)}  {name}")
    ok = all(checks.values())
    _spike.write_json("smoke_app.json", {"session": str(sessions[-1]), "checks": checks, "ok": ok})
    print(f"\nSMOKE {verdict(ok)}   contact sheet: {OUT}\\smoke_app_frames.jpg")
    return 0 if ok else 1


AD_LABEL = "AD"

if __name__ == "__main__":
    raise SystemExit(main())
