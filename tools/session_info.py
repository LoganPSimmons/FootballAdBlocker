"""Inspect a recorded session by replaying it - the first consumer of FileReplaySource.

Prints the GAME/AD segments from the timeline, frame-rate and gap statistics, and checks
that every frame's recorded label agrees with the state the timeline implies at that instant
(a frame or two of disagreement right at a key press is normal: the label is stamped a few
milliseconds after the press). Optionally writes a contact sheet around every transition,
which is how label mistakes get spotted.

Usage:  python tools\\session_info.py                     (latest session)
        python tools\\session_info.py sessions\\2026-09-19_utah --sheet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from adblock.capture import FileReplaySource  # noqa: E402


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{seconds % 60:04.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", nargs="?", type=Path, help="session folder (default: the most recent one)")
    parser.add_argument("--sheet", action="store_true", help="write transitions.jpg into the session folder")
    args = parser.parse_args()

    session = args.session
    if session is None:
        folders = sorted((p for p in (ROOT / "sessions").glob("*") if (p / "frames.csv").exists()), key=lambda p: p.stat().st_mtime)
        if not folders:
            print("no sessions recorded yet")
            return 1
        session = folders[-1]
    replay = FileReplaySource(session)
    events = sorted(replay.events(), key=lambda e: e["t"])  # hotkey/state carry the key-press time: sort, don't trust file order
    rows = replay.rows
    print(f"Session {session}")
    if replay.unreliable_labels:
        print("  !! UNRELIABLE LABELS - not usable for training or evaluation as is:")
        for line in replay.unreliable_labels.splitlines():
            print(f"     {line}")
    if not rows:
        print("  no frames")
        return 1

    times = np.array([float(r["t"]) for r in rows])
    gaps = np.diff(times)
    ended = any(e["event"] == "session_end" for e in events)
    sizes = sorted({(r["src_w"], r["src_h"]) for r in rows})
    print(f"  frames: {len(rows)} over {clock(times[-1] - times[0])}  ({len(rows) / max(times[-1] - times[0], 1e-9):.2f} fps), "
          f"source sizes {', '.join(f'{w}x{h}' for w, h in sizes)}")
    if len(gaps):
        print(f"  frame gaps: median {np.median(gaps):.2f} s, max {gaps.max():.2f} s at t={clock(times[int(gaps.argmax())])}, "
              f"{int((gaps > 2.0).sum())} gap(s) over 2 s")
    print(f"  ended cleanly: {ended}")

    # Segments implied by the timeline.
    changes = [(e["t"], e["state"], e["source"]) for e in events if e["event"] == "state"]
    end_t = max(times[-1], events[-1]["t"])
    segments, state, start = [], "GAME", 0.0
    for t, new_state, _source in changes:
        segments.append((state, start, t))
        state, start = new_state, t
    segments.append((state, start, end_t))
    ad_total = sum(b - a for s, a, b in segments if s == "AD")
    print(f"  state changes: {len(changes)};  AD time {clock(ad_total)} of {clock(end_t)} ({100 * ad_total / max(end_t, 1e-9):.0f}%)")
    for s, a, b in segments:
        if b - a > 0.05:
            print(f"    {s:<4} {clock(a):>8} -> {clock(b):>8}   ({b - a:6.1f} s)")

    # Frame labels vs timeline.
    def state_at(t: float) -> str:
        current = "GAME"
        for change_t, new_state, _ in changes:
            if change_t <= t:
                current = new_state
        return current

    mismatches = [r for r in rows if r["state"] != state_at(float(r["t"]))]
    near = [r for r in mismatches if any(abs(float(r["t"]) - c[0]) <= 0.5 for c in changes)]
    print(f"  labels: {sum(r['state'] == 'AD' for r in rows)} AD / {sum(r['state'] == 'GAME' for r in rows)} GAME frames; "
          f"{len(mismatches)} disagree with the timeline ({len(near)} of them within 0.5 s of a key press)")
    for event in events:
        if event["event"] in ("capture", "recording", "window"):
            print("   ", {k: v for k, v in event.items() if k != "wall"})

    if args.sheet and changes:
        strips = []
        for change_t, new_state, _ in changes:
            index = int(np.searchsorted(times, change_t))
            picks = [i for i in range(index - 4, index + 4) if 0 <= i < len(rows)]
            tiles = []
            for i in picks:
                image = cv2.imread(str(session / rows[i]["file"]))
                tile = cv2.resize(image, (240, 135))
                colour = (0, 0, 255) if rows[i]["state"] == "AD" else (0, 200, 0)
                cv2.putText(tile, f"{rows[i]['state']} {clock(float(rows[i]['t']))}", (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2)
                tiles.append(tile)
            while len(tiles) < 8:
                tiles.append(np.zeros((135, 240, 3), np.uint8))
            strips.append(np.hstack(tiles))
        cv2.imwrite(str(session / "transitions.jpg"), np.vstack(strips))
        print(f"  wrote {session / 'transitions.jpg'}  (one row per key press: 4 frames before, 4 after)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
