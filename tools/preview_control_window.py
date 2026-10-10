"""Render the control window to a PNG without the app running - to check a layout change, and for the docs.

A stand-in controller feeds the window a plausible moment: GAME, three learned graphics (two on
screen, one missing), the image model happy, a session recording. Nothing is captured, no hotkey is
claimed and no window appears on screen (Qt's WA_DontShowOnScreen: the widget is "shown" for
painting purposes only - Qt's offscreen platform would do the same but has no fonts on Windows), so
this is safe to run while the real app is working.

Usage:  python tools\\preview_control_window.py [--out docs\\images\\control-window.png] [--frame image.jpg]
                                               [--no-model] [--graphics-off] [--state AD] [--search utah]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from adblock.data.espn import GameSnapshot, TeamScore  # noqa: E402
from adblock.detect.detector import AD, GAME  # noqa: E402
from adblock.ui import ControlWindow  # noqa: E402

ON_SCREEN, MISSING = (80, 255, 80), (60, 60, 255)  # the colours detector.picture() uses (BGR)


def analysed_picture(frame: Path | None) -> np.ndarray:
    """What the window's live view shows: the grey analysed picture with the learned graphics drawn in."""
    if frame is not None:
        gray = cv2.cvtColor(cv2.resize(cv2.imread(str(frame)), (640, 360)), cv2.COLOR_BGR2GRAY)
    else:
        rng = np.random.default_rng(3)
        gray = cv2.resize(rng.integers(40, 140, (18, 32), dtype=np.uint8), (640, 360), interpolation=cv2.INTER_CUBIC)
    canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(5)
    for (x0, y0, x1, y1), colour in (((180, 300, 460, 344), ON_SCREEN),  # a scorebug, on screen
                                      ((548, 12, 620, 40), ON_SCREEN),  # the corner logo, on screen
                                      ((20, 20, 150, 56), MISSING)):  # a segment graphic that has left
        dots = rng.random((y1 - y0, x1 - x0)) < 0.3  # the graphic's edge pixels
        canvas[y0:y1, x0:x1][dots] = colour
        cv2.rectangle(canvas, (x0, y0), (x1, y1), colour, 1)
    return canvas


def stand_in(args: argparse.Namespace) -> SimpleNamespace:
    picture = analysed_picture(args.frame)
    c = SimpleNamespace(state=args.state, auto=True, corrections=1, stream=None, scores=None, use_scene=True, chosen_window=0)
    c.config = SimpleNamespace(pip=True, graphics=not args.graphics_off, sessions_path=ROOT / "sessions",
                               stream_delay_s=45.0, close_to_tray=False, title_contains="YouTube TV|Fubo")
    c.scene = None if args.no_model else SimpleNamespace(ready=True, failed=False)
    c.model_in_use = lambda: c.scene is not None
    c.writer = SimpleNamespace(dir=SimpleNamespace(name="2026-10-03_utah"), frame_count=4783)
    if args.no_model:
        status = "Automatic: 2 of 3 learned graphics on screen. No image model installed (requirements-ml.txt): graphics only."
    elif args.graphics_off:
        status = "Automatic: the image model decides alone (learned graphics off). Image model: looks like the broadcast."
    else:
        status = "Automatic: 2 of 3 learned graphics on screen. Image model: looks like the broadcast."
    c.status = lambda: status
    c.preview = lambda: picture
    c.stats_current = lambda: None
    c.stats_status = lambda: "No game selected: pick one in the main window (or start with --game <team>)."
    c.set_auto = c.set_pip = c.set_graphics = c.correct = c.set_game = lambda *_: None
    c.relearn = lambda: None
    c.chrome_windows = lambda: [(0x1234, "Home - YouTube TV   ·   fullscreen"),
                                (0x5678, "Fubo - Watch & DVR Live Sports & TV Online   ·   2560x1032")]
    c.watched_window = lambda: "Home - YouTube TV   ·   fullscreen"
    c.choose_window = lambda *_: None
    c.app = SimpleNamespace(quit=lambda: None)
    return c


def fake_games() -> list[GameSnapshot]:
    """A few made-up games, so the game list's search box can be pictured without ESPN."""
    def team(abbreviation: str, location: str, name: str, home: bool) -> TeamScore:
        return TeamScore(abbreviation, location, name, "", home, None)

    games = [("Utah State", "Aggies", "USU", "Utah", "Utes", "UTAH", "FOX", "in"),
             ("Ohio State", "Buckeyes", "OSU", "Michigan", "Wolverines", "MICH", "CBS", "pre"),
             ("Texas", "Longhorns", "TEX", "Oklahoma", "Sooners", "OU", "ABC", "in"),
             ("Utah", "Utes", "UTAH", "BYU", "Cougars", "BYU", "ESPN", "post"),
             ("Boise State", "Broncos", "BSU", "UNLV", "Rebels", "UNLV", "CBSSN", "pre")]
    return [GameSnapshot(str(i), f"{a_loc} {a_name} at {h_loc} {h_name}", f"{a} @ {h}", state, "", 0, 0.0,
                         team(a, a_loc, a_name, False), team(h, h_loc, h_name, True), network, 0.0)
            for i, (a_loc, a_name, a, h_loc, h_name, h, network, state) in enumerate(games, 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "spike_out" / "control_window.png")
    parser.add_argument("--frame", type=Path, help="image to analyse for the live view (default: synthetic texture)")
    parser.add_argument("--no-model", action="store_true", help="render the state without the image model installed")
    parser.add_argument("--graphics-off", action="store_true", help="render with 'Use learned graphics' unticked")
    parser.add_argument("--state", choices=(GAME, AD), default=GAME)
    parser.add_argument("--search", help="also render the game list opened with this typed into its search box (made-up games)")
    args = parser.parse_args()

    ControlWindow.load_games = lambda self: None  # no ESPN call: the picker stays at "none" and the picture is deterministic
    app = QtWidgets.QApplication([])
    window = ControlWindow(stand_in(args))
    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()  # "visible" to Qt (refresh() paints the live view only then), never on the screen
    app.processEvents()
    window.refresh()
    app.processEvents()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(args.out))
    print(f"wrote {args.out}  ({window.width()}x{window.height()})")
    if args.search is not None:
        window.fill_games(fake_games())
        window.games.popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.games.showPopup()
        window.games.search.setText(args.search)
        app.processEvents()
        out = args.out.with_name(f"{args.out.stem}_search{args.out.suffix}")
        window.games.popup.grab().save(str(out))
        window.games.hidePopup()
        print(f"wrote {out}  ({window.games.popup.width()}x{window.games.popup.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
