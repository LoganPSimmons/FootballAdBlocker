"""S1 - can we capture YouTube TV's video from Chrome, or does DRM give black frames?

Captures the Chrome window three ways for a few seconds each:
  wgc      Windows Graphics Capture of the window   (windows-capture)
  dxcam    DXGI desktop duplication, window's region (dxcam)
  wc_dxgi  DXGI desktop duplication, window's region (windows-capture's own session)

For each path it saves first/last frames to spike_out/ and measures the *video area*
(central 60% of the window): mean luminance, fraction of near-black pixels, and how much the
picture changed between samples. A DRM-blacked video is a flat black slab that never changes,
so it is detected from the numbers, not by eye.

Usage:  python tools\\spike_capture.py --label windowed
        python tools\\spike_capture.py --label fullscreen --wait-fullscreen   (then press F in the player)
"""

from __future__ import annotations

import argparse
import time

import _spike
from _spike import OUT, frame_diff, gray_small, luma_stats, save_png_and_preview, verdict, win32


def capture_wgc(hwnd: int, seconds: float, interval: float) -> dict:
    from windows_capture import WindowsCapture

    samples: list[tuple[float, object]] = []
    state = {"callbacks": 0, "t0": None, "next": 0.0, "size": None}
    capture = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @capture.event
    def on_frame_arrived(frame, control):
        now = time.perf_counter()
        if state["t0"] is None:
            state["t0"] = now
        state["callbacks"] += 1
        state["size"] = (frame.width, frame.height)
        elapsed = now - state["t0"]
        if elapsed >= state["next"]:
            samples.append((elapsed, frame.frame_buffer[:, :, :3].copy()))
            state["next"] += interval

    @capture.event
    def on_closed():
        pass

    cpu0, wall0 = time.process_time(), time.perf_counter()
    control = capture.start_free_threaded()
    time.sleep(seconds)  # WGC only delivers frames when the window repaints, so stop on a timer
    control.stop()
    control.wait()
    wall = time.perf_counter() - wall0
    return {
        "samples": samples,
        "delivered_fps": round(state["callbacks"] / wall, 1),
        "frame_size": state["size"],
        "cpu_core_fraction": round((time.process_time() - cpu0) / wall, 3),
    }


def capture_dxcam(rect: win32.Rect, seconds: float, interval: float) -> dict:
    import dxcam

    print(dxcam.device_info().rstrip())
    print(dxcam.output_info().rstrip())
    camera = dxcam.create(output_color="BGR")
    samples: list[tuple[float, object]] = []
    grabs = 0
    cpu0, t0 = time.process_time(), time.perf_counter()
    try:
        while (elapsed := time.perf_counter() - t0) < seconds:
            frame = camera.grab(region=rect)  # None when the desktop hasn't changed since the last grab
            if frame is not None:
                grabs += 1
                samples.append((elapsed, frame))
            time.sleep(interval)
    finally:
        camera.release()
    wall = time.perf_counter() - t0
    return {
        "samples": samples,
        "delivered_fps": round(grabs / wall, 1),
        "frame_size": (rect[2] - rect[0], rect[3] - rect[1]),
        "cpu_core_fraction": round((time.process_time() - cpu0) / wall, 3),
    }


def capture_wc_dxgi(rect: win32.Rect, seconds: float, interval: float) -> dict:
    from windows_capture import DxgiDuplicationSession

    session = DxgiDuplicationSession()
    samples: list[tuple[float, object]] = []
    grabs = 0
    left, top, right, bottom = rect
    cpu0, t0 = time.process_time(), time.perf_counter()
    while (elapsed := time.perf_counter() - t0) < seconds:
        frame = session.acquire_frame(timeout_ms=100)
        if frame is not None:
            grabs += 1
            samples.append((elapsed, frame.to_bgr()[top:bottom, left:right].copy()))
        time.sleep(interval)
    wall = time.perf_counter() - t0
    return {
        "samples": samples,
        "delivered_fps": round(grabs / wall, 1),
        "frame_size": (right - left, bottom - top),
        "cpu_core_fraction": round((time.process_time() - cpu0) / wall, 3),
    }


def analyse(path_name: str, label: str, result: dict) -> dict:
    samples = result.pop("samples")
    report = {"path": path_name, "frames_sampled": len(samples), **result}
    if not samples:
        report.update(ok=False, reason="no frames delivered")
        return report

    stats = [luma_stats(frame) for _, frame in samples]
    grays = [gray_small(frame) for _, frame in samples]
    diffs = [frame_diff(a, b) for a, b in zip(grays, grays[1:])]
    # DRM blacks out only the video; a frame that is black edge to edge (browser toolbar
    # included) is an empty frame from the capture API, which is a different problem.
    empty = sum(s["is_black"] and float(frame.mean()) < 1.0 for (_, frame), s in zip(samples, stats))
    black = sum(s["is_black"] for s in stats) - empty

    save_png_and_preview(samples[0][1], f"s1_{label}_{path_name}_first")
    save_png_and_preview(samples[-1][1], f"s1_{label}_{path_name}_last")
    for index, ((_, frame), stat) in enumerate(zip(samples, stats)):
        if stat["is_black"]:  # keep every black sample: is it DRM, a capture glitch, or a real fade?
            save_png_and_preview(frame, f"s1_{label}_{path_name}_black{index}")

    report.update(
        per_sample=[f"t={t:.1f}s luma={s['mean_luma']:.0f} black={s['black_fraction']:.2f}" for (t, _), s in zip(samples, stats)],
        mean_luma=round(sum(s["mean_luma"] for s in stats) / len(stats), 2),
        max_std_luma=max(s["std_luma"] for s in stats),
        video_black_frames=black,
        empty_frames=empty,
        mean_frame_diff=round(sum(diffs) / len(diffs), 2) if diffs else None,
        changing_pairs=f"{sum(d > 0.5 for d in diffs)}/{len(diffs)}",
    )
    # PASS = real picture in the video area. A static-but-visible picture still passes S1
    # (paused video); it is reported so it can't be mistaken for a clean result.
    report["ok"] = black == 0 and empty < len(stats) and report["max_std_luma"] >= 6.0
    if black:
        report["reason"] = f"{black}/{len(stats)} sampled frames are flat black in the video area only (DRM?)"
    elif empty == len(stats):
        report["reason"] = "every frame is black edge to edge (DRM black in fullscreen, or this capture path is broken)"
    elif not report["ok"]:
        report["reason"] = "video area is nearly uniform - is the stream actually on screen?"
    elif empty:
        report["warning"] = f"{empty} edge-to-edge black frame(s): capture API handed back an empty frame"
    elif diffs and max(diffs) <= 0.5:
        report["warning"] = "picture never changed - video paused?"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="YouTube TV", help="substring of the Chrome window title")
    parser.add_argument("--label", default="windowed", help="names the evidence files (windowed / fullscreen)")
    parser.add_argument("--seconds", type=float, default=4.0, help="capture time per path")
    parser.add_argument("--wait-fullscreen", action="store_true", help="start once the stream window is fullscreen")
    parser.add_argument("--paths", default="wgc,dxcam,wc_dxgi")
    args = parser.parse_args()

    win32.set_dpi_awareness()
    if args.wait_fullscreen:
        if not _spike.wait_until(lambda: _spike.stream_is_fullscreen(args.title), 180, "the stream to go fullscreen"):
            print("FAIL: the stream window never went fullscreen.")
            return 2
        time.sleep(4.0)  # let the player's controls fade out so they aren't in the evidence

    target = _spike.find_target(args.title)
    screen = win32.monitor_rect(target.hwnd)
    region = _spike.clip_rect(target.rect, screen)
    fullscreen = win32.is_fullscreen(target.hwnd)
    print(f"Target: hwnd={target.hwnd:#x} rect={target.rect} fullscreen={fullscreen}")
    print(f"        title={target.title!r}")
    print(f"        foreground window is the target: {win32.foreground_window() == target.hwnd}")

    runners = {
        "wgc": lambda: capture_wgc(target.hwnd, args.seconds, 0.5),
        "dxcam": lambda: capture_dxcam(region, args.seconds, 0.5),
        "wc_dxgi": lambda: capture_wc_dxgi(region, args.seconds, 0.5),
    }
    reports = []
    for name in args.paths.split(","):
        print(f"\n--- {name} ---")
        try:
            report = analyse(name, args.label, runners[name]())
        except Exception as exc:  # a spike should report, not crash, when one path is broken
            report = {"path": name, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        reports.append(report)
        for key, value in report.items():
            print(f"  {key:>18}: {value}")
        print(f"  => {name}: {verdict(report['ok'])}")

    out = _spike.write_json(
        f"s1_{args.label}.json",
        {"label": args.label, "title": target.title, "rect": target.rect, "fullscreen": fullscreen, "paths": reports},
    )
    ok = any(r["ok"] for r in reports)
    print(f"\nS1 [{args.label}] {verdict(ok)}: " + ", ".join(f"{r['path']}={verdict(r['ok'])}" for r in reports))
    print(f"Evidence: {out} and s1_{args.label}_*.png in {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
