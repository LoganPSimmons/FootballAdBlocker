"""Render the "Game stats" window to PNGs without the app running: both teams, then each team.

Fetches the live stats of a game from ESPN (delayed like the app would) and paints the window
with QWidget.grab(), one image per perspective, so the layout can be checked from a script.

Usage:  python tools\\preview_stats.py --game utah [--delay 45] [--out spike_out]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6 import QtWidgets  # noqa: E402

from adblock.data.espn import EspnClient  # noqa: E402
from adblock.stats_widgets import BOTH  # noqa: E402
from adblock.ui import StatsWindow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True)
    parser.add_argument("--delay", type=float, default=45.0)
    parser.add_argument("--out", type=Path, default=ROOT / "spike_out")
    args = parser.parse_args()

    client = EspnClient()
    game = client.find_game(args.game)
    if game is None:
        print(f"no game today matches {args.game!r}")
        return 1
    stats = client.stats(game.event_id).delayed(time.time() - args.delay)
    client.close()

    app = QtWidgets.QApplication([])  # noqa: F841
    controller = SimpleNamespace(stats_current=lambda: stats, stats_status=lambda: "", config=SimpleNamespace(stream_delay_s=args.delay))
    window = StatsWindow(controller)
    window.show()  # refresh() only paints while visible
    args.out.mkdir(exist_ok=True)
    for perspective in (BOTH, stats.away.abbreviation, stats.home.abbreviation):
        index = next(i for i in range(window.picker.count()) if window.picker.itemData(i) == perspective) if window.picker.count() > 1 else 0
        window.picker.setCurrentIndex(index)
        window.pick(index)
        app.processEvents()
        path = args.out / f"stats_{perspective}.png"
        window.grab().save(str(path))
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
