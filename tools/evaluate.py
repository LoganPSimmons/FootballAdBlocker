"""Replay recorded sessions through the detector and measure it against the recorded labels.

The numbers that matter (PLAN.md): **game-seconds hidden** must be ~0; ad-seconds leaked is
the price we accept for that. Also lists every detector transition, and with --sheet writes a
contact sheet around each one - that is how detector mistakes and label mistakes are told apart.

Labels are the app state recorded with each frame, with the viewer's corrections applied
backwards (adblock/labels.py): undoing a detector switch relabels the whole stretch since it.
Sessions carrying UNRELIABLE_LABELS.txt are skipped unless named explicitly; use --from/--to
to restrict a named session to its usable range.

Usage:  python tools\\evaluate.py                                   (all trustworthy sessions)
        python tools\\evaluate.py sessions\\2026-09-19_utah-hands-on --from 730 --sheet
        python tools\\evaluate.py --enter-ad 4 --exit-frames 2      (try other state-machine settings)
        python tools\\evaluate.py --scene --drive                   (the app's default: the image model drives)
        python tools\\evaluate.py --scene --drive --no-graphics     ("Use learned graphics" unticked)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from adblock.capture import FileReplaySource  # noqa: E402
from adblock.labels import effective_labels  # noqa: E402
from collections import deque  # noqa: E402

from adblock.detect.detector import Detector, FusionSettings  # noqa: E402
from adblock.detect.scene import FAST_S, SMOOTH_S, verdict_from  # noqa: E402
from adblock.paths import DATA  # noqa: E402


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{seconds % 60:04.1f}"


def recorded_evidence(session: Path) -> list[tuple[float, bool | None, bool | None]]:
    """(t, game_like, scene) as the live detector logged it, for back-dating corrections."""
    path = session / "detector.csv"
    if not path.exists():
        return []
    import csv

    def flag(value: str | None) -> bool | None:
        return None if not value else value == "1"

    with open(path, newline="", encoding="utf-8") as handle:
        return [(float(r["t"]), flag(r["game_like"]), flag(r.get("scene"))) for r in csv.DictReader(handle)]


_scene_model = None


def scene_scores(session: Path) -> list[tuple[float, float]]:
    """(t, image-model score) once a second, from the embeddings tools/explore_classifier.py cached."""
    global _scene_model
    cache = DATA / "learned" / "embeddings" / f"{session.name}.npz"
    if not cache.exists():
        raise SystemExit(f"no cached embeddings for {session.name}: run tools\explore_classifier.py on it first")
    if _scene_model is None:
        from adblock.detect.scene import SceneModel

        _scene_model = SceneModel()
    data = np.load(cache, allow_pickle=False)
    return [(float(t), _scene_model.score_embedding(e)) for t, e in zip(data["t"], data["embedding"])]


def replay(session: Path, settings: FusionSettings, with_corrections: bool = False, raw_labels: bool = False,
           scene: bool = False, graphics: bool = True) -> list[dict]:
    """One dict per recorded frame: t, label, detector state, evidence.

    Unaided by default - what the detector does on its own. ``with_corrections`` also replays
    the viewer's recorded clicks at their timestamps, reproducing the live run (held
    corrections, re-seeded learning).
    """
    source = FileReplaySource(session)
    windows = sorted((e["t"], e["fullscreen"]) for e in source.events() if e["event"] == "window")
    clicks = sorted((e["t"], e["to"]) for e in source.events() if e["event"] == "correction") if with_corrections else []

    def fullscreen_at(t: float) -> bool:
        current = windows[0][1] if windows else True
        for change_t, value in windows:
            if change_t <= t:
                current = value
        return current

    detector = Detector(settings, use_graphics=graphics)
    rows = []
    pending = scene_scores(session) if scene else []
    recent: deque[float] = deque(maxlen=SMOOTH_S)  # causal, like the live SceneWatcher
    for frame in source:
        while clicks and clicks[0][0] <= frame.t:
            detector.correct(clicks.pop(0)[1])
        while pending and pending[0][0] <= frame.t:
            recent.append(pending.pop(0)[1])
        seen = detector.process(frame.image, frame.t, fullscreen_at(frame.t), verdict_from(recent) if scene else None,
                                verdict_from(recent, FAST_S, FAST_S) if scene else None)
        rows.append({"t": frame.t, "index": frame.index, "label": frame.label, "state": seen.state,
                     "game_like": seen.game_like, "best": seen.best, "regions": len(seen.scores), "learned": seen.learned})
    if not raw_labels:  # corrections say the stretch leading up to them was wrong: apply them backwards
        fixed = effective_labels([r["t"] for r in rows], [r["label"] for r in rows], source.events(), recorded_evidence(session))
        for row, label in zip(rows, fixed):
            row["label"] = label
    return rows


def segments(rows: list[dict], key: str) -> list[tuple[str, float, float]]:
    out, start = [], 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i][key] != rows[start][key]:
            out.append((rows[start][key], rows[start]["t"], rows[i - 1]["t"]))
            start = i
    return out


def contact_sheet(session: Path, rows: list[dict], moments: list[float], path: Path) -> None:
    source = FileReplaySource(session)
    times = np.array([r["t"] for r in rows])
    strips = []
    for moment in moments[:40]:
        centre = int(np.searchsorted(times, moment))
        tiles = []
        for i in range(centre - 12, centre + 12, 3):  # ~3 s before to ~3 s after, every 0.75 s
            if not 0 <= i < len(rows):
                tiles.append(np.zeros((135, 240, 3), np.uint8))
                continue
            row = rows[i]
            image = cv2.imread(str(session / source.rows[i]["file"]))
            tile = cv2.resize(image, (240, 135))
            colour = (0, 0, 255) if row["state"] == "AD" else (0, 200, 0)
            cv2.putText(tile, f"{row['state']} {clock(row['t'])} s={row['best']:.2f}", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
            cv2.putText(tile, f"label {row['label']}", (4, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            tiles.append(tile)
        strips.append(np.hstack(tiles))
    if strips:
        cv2.imwrite(str(path), np.vstack(strips), [cv2.IMWRITE_JPEG_QUALITY, 80])


def evaluate(session: Path, settings: FusionSettings, t_from: float, t_to: float, sheet: bool,
             with_corrections: bool, raw_labels: bool, scene: bool = False, graphics: bool = True) -> dict:
    started = time.perf_counter()
    rows = replay(session, settings, with_corrections, raw_labels, scene, graphics)
    took = time.perf_counter() - started
    scored = [r for r in rows if t_from <= r["t"] <= t_to]
    steps = np.diff([r["t"] for r in scored], append=scored[-1]["t"]).clip(0, 1.0)  # seconds each frame stands for
    hidden = float(sum(s for r, s in zip(scored, steps) if r["state"] == "AD" and r["label"] == "GAME"))
    leaked = float(sum(s for r, s in zip(scored, steps) if r["state"] == "GAME" and r["label"] == "AD"))
    label_ad = float(sum(s for r, s in zip(scored, steps) if r["label"] == "AD"))
    total = float(steps.sum())
    first_mask = next((r["t"] for r in rows if r["regions"]), None)

    print(f"\n=== {session.name}   ({len(rows)} frames, replayed in {took:.0f} s = {len(rows) / took:.0f} fps)")
    print(f"  first graphics learned at {clock(first_mask) if first_mask is not None else 'never'}; "
          f"scored range {clock(scored[0]['t'])}-{clock(scored[-1]['t'])} ({clock(total)})")
    print(f"  GAME-SECONDS HIDDEN: {hidden:6.1f} s     ad-seconds leaked: {leaked:6.1f} s of {label_ad:.0f} s labelled AD "
          f"({100 * (1 - leaked / label_ad) if label_ad else 0:.0f}% covered)")
    print("  detector transitions (state, from, to | label at that moment):")
    changes = []
    for state, a, b in segments(rows, "state"):
        label = next(r["label"] for r in rows if r["t"] >= a)
        print(f"    {state:<4} {clock(a):>8} -> {clock(b):>8}  ({b - a:6.1f} s) | label {label}")
        if a > rows[0]["t"]:
            changes.append(a)
    if sheet:
        path = session / "detector_transitions.jpg"
        contact_sheet(session, rows, changes, path)
        print(f"  wrote {path}  (one row per detector transition, ~3 s either side)")
    return {"hidden": hidden, "leaked": leaked, "label_ad": label_ad, "total": total}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--from", dest="t_from", type=float, default=0.0)
    parser.add_argument("--to", dest="t_to", type=float, default=float("inf"))
    parser.add_argument("--sheet", action="store_true", help="write detector_transitions.jpg into each session")
    parser.add_argument("--enter-ad", type=float, default=FusionSettings.enter_ad_s)
    parser.add_argument("--exit-frames", type=int, default=FusionSettings.exit_ad_frames)
    parser.add_argument("--scene", action="store_true",
                        help="fuse in the image model (needs requirements-ml.txt and cached embeddings)")
    parser.add_argument("--scene-exit", type=float, default=FusionSettings.scene_exit_s,
                        help="seconds of uninterrupted 'looks like the broadcast' that end an AD (default 12)")
    parser.add_argument("--boundary-exit", type=float, default=FusionSettings.boundary_exit_s,
                        help="after a cut to black, seconds of the quick verdict 'broadcast' that end an AD (default 2; 0 disables)")
    parser.add_argument("--scene-enter", type=float, default=FusionSettings.scene_enter_s,
                        help="seconds of 'commercial' with no graphic on screen that start an AD (default 10; 0 disables)")
    parser.add_argument("--drive", action="store_true", help="the image model drives the state (the app's default); needs --scene")
    parser.add_argument("--no-graphics", dest="graphics", action="store_false",
                        help="the image model decides alone, the learned graphics are not consulted (the control window's "
                             "'Use learned graphics' unticked); needs --scene")
    parser.add_argument("--raw-labels", action="store_true",
                        help="score against the recorded state as is, without applying corrections backwards")
    parser.add_argument("--with-corrections", action="store_true",
                        help="also replay the viewer's recorded clicks (default: the detector unaided)")
    args = parser.parse_args()
    settings = FusionSettings(enter_ad_s=args.enter_ad, exit_ad_frames=args.exit_frames, scene_exit_s=args.scene_exit,
                              boundary_exit_s=args.boundary_exit if args.boundary_exit > 0 else float("inf"),
                              scene_enter_s=args.scene_enter if args.scene_enter > 0 else float("inf"),
                              scene_drives=args.drive)

    sessions = args.sessions
    if not sessions:
        everything = sorted(p for p in (DATA / "sessions").glob("*") if (p / "frames.csv").exists())
        sessions = [p for p in everything if not FileReplaySource(p).unreliable_labels]
        skipped = len(everything) - len(sessions)
        print(f"{len(sessions)} session(s) with trustworthy labels ({skipped} skipped: UNRELIABLE_LABELS.txt)")
    totals = [evaluate(s, settings, args.t_from, args.t_to, args.sheet, args.with_corrections, args.raw_labels, args.scene,
                       args.graphics) for s in sessions]
    if len(totals) > 1:
        print(f"\nTOTAL  game-seconds hidden {sum(t['hidden'] for t in totals):.1f} s, "
              f"ad-seconds leaked {sum(t['leaked'] for t in totals):.1f} of {sum(t['label_ad'] for t in totals):.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
