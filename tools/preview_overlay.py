"""Render the ad-break overlay to a PNG without showing it on screen.

The real overlay is excluded from every screen-capture API (that is the point of it), so a
screenshot can never show what it looks like. QWidget.grab() paints the widget straight into
an image instead. Optionally uses a recorded frame as the live thumbnail.

Usage:  python tools\\preview_overlay.py [--frame sessions\\...\\frames\\000010.jpg] [--out spike_out\\overlay_preview.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from adblock.actuators.overlay import Overlay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frame", type=Path, help="image to use as the live thumbnail")
    parser.add_argument("--out", type=Path, default=ROOT / "spike_out" / "overlay_preview.png")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--game", help="team whose live game fills the stats panel (fetched from ESPN, delayed --delay s)")
    parser.add_argument("--delay", type=float, default=45.0)
    args = parser.parse_args()

    app = QtWidgets.QApplication([])  # noqa: F841 - must exist before any widget
    overlay = Overlay("F9")
    width, height = (int(v) for v in args.size.split("x"))
    overlay.resize(width, height)
    overlay.target_width = width
    overlay.set_break_seconds(83)
    overlay.set_score("USU 0   @   #17 UTAH 7", "10:37 - 1st   ·   FOX")
    if args.frame:
        overlay.set_thumbnail(cv2.imread(str(args.frame)))
    if args.game:
        import time

        from adblock.data.espn import EspnClient

        client = EspnClient()
        game = client.find_game(args.game)
        if game is None:
            print(f"no game today matches {args.game!r}")
            return 1
        stats = client.stats(game.event_id).delayed(time.time() - args.delay)
        client.close()
        overlay.set_score(stats.score_line(), f"{stats.status_detail}   ·   {game.network}")
        overlay.set_stats(stats)
    args.out.parent.mkdir(exist_ok=True)
    overlay.grab().save(str(args.out))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
