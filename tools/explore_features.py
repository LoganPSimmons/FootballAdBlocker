"""Measure candidate ad/game indicators on recorded sessions - before any of them touches the live detector.

For every recorded frame: black frame, letterbox / pillarbox bars, shot cut (and the cut rate over
the last minute), grass share, saturation, brightness, motion; optionally Chrome's audio peak log
(tools/peak_logger.py) joined by wall clock: silence dips and sustained level. Each is scored against
the session's labels (viewer corrections applied backwards, adblock/labels.py) two ways:

  overall                every frame
  graphics missing       only frames where the live detector saw no learned graphic - the replays,
                         bumpers, studio stretches and ads where an extra indicator would actually
                         be consulted (with a graphic on screen the answer is already known)

Booleans are reported as "fires in x % of AD frames, y % of GAME frames -> when it fires: z % AD";
continuous features as the AUC of "frame is an ad" plus class medians. Nothing here changes the app.

Usage:  python tools\\explore_features.py                                (all trustworthy sessions)
        python tools\\explore_features.py sessions\\2026-09-26_sep26 --audio audio_peaks.csv --dump out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from adblock.capture import FileReplaySource  # noqa: E402
from adblock.labels import effective_labels  # noqa: E402

SILENT = 0.004  # peak-meter value that counts as silence
SILENT_RUN = 3  # consecutive silent samples (~0.15 s at 20 Hz) that make a "dip"
CUT = 0.4  # half-L1 distance between consecutive colour histograms that counts as a shot cut


def wall_seconds(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def recorded_evidence(session: Path) -> tuple[np.ndarray, np.ndarray]:
    """(t, graphic on screen?) as the live detector logged it; on screen = 1, missing/unknown = 0."""
    path = session / "detector.csv"
    if not path.exists():
        return np.array([]), np.array([])
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return np.array([float(r["t"]) for r in rows]), np.array([r["game_like"] == "1" for r in rows])


def evidence_pairs(session: Path) -> list[tuple[float, bool | None, bool | None]]:
    """(t, graphic on screen, image-model verdict) as the live detector logged it, for the labels."""
    path = session / "detector.csv"
    if not path.exists():
        return []

    def flag(value: str | None) -> bool | None:
        return None if not value else value == "1"

    with open(path, newline="", encoding="utf-8") as handle:
        return [(float(r["t"]), flag(r["game_like"]), flag(r.get("scene"))) for r in csv.DictReader(handle)]


class AudioLog:
    """Chrome's session peak meter, ~20 Hz, as written by peak_logger.py."""

    def __init__(self, path: Path) -> None:
        t, peak = [], []
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                t.append(wall_seconds(row["wall"]))
                peak.append(float(row["peak"]))
        self.t, self.peak = np.array(t), np.array(peak)
        silent = self.peak < SILENT
        dips = []
        run = 0
        for i, s in enumerate(silent):
            run = run + 1 if s else 0
            if run == SILENT_RUN:
                dips.append(self.t[i])
        self.dips = np.array(dips)

    def features(self, wall: float) -> dict:
        i = int(np.searchsorted(self.t, wall))
        j10 = int(np.searchsorted(self.t, wall - 10.0))
        window = self.peak[j10:i]
        d = int(np.searchsorted(self.dips, wall - 30.0))
        out = {"dip_30": bool(d < len(self.dips) and self.dips[d] <= wall)}
        if len(window) >= 20:
            out["p10_10"] = float(np.percentile(window, 10))
            out["mean_10"] = float(window.mean())
            out["crest_10"] = float(window.max() / max(window.mean(), 1e-4))
        else:
            out["p10_10"] = out["mean_10"] = out["crest_10"] = np.nan
        return out


class FrameFeatures:
    def __init__(self) -> None:
        self.prev_hist: np.ndarray | None = None
        self.prev_gray: np.ndarray | None = None
        self.cuts: deque[float] = deque()
        self.grass_hist: deque[tuple[float, float]] = deque()

    def compute(self, image: np.ndarray, t: float) -> dict:
        small = cv2.resize(image, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        lum = float(gray.mean())
        rows, cols = gray.mean(axis=1), gray.mean(axis=0)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        hist = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256]).flatten()
        hist /= max(hist.sum(), 1.0)
        distance = 0.5 * float(np.abs(hist - self.prev_hist).sum()) if self.prev_hist is not None else 0.0
        motion = float(cv2.absdiff(gray, self.prev_gray).mean()) if self.prev_gray is not None else 0.0
        self.prev_hist, self.prev_gray = hist, gray
        if distance > CUT:
            self.cuts.append(t)
        while self.cuts and t - self.cuts[0] > 60.0:
            self.cuts.popleft()
        grass = float(((h >= 30) & (h <= 90) & (s >= 60) & (v >= 50)).mean())
        self.grass_hist.append((t, grass))
        while self.grass_hist and t - self.grass_hist[0][0] > 10.0:
            self.grass_hist.popleft()
        return {
            "lum": lum,
            "black": bool(lum < 20 and float(np.percentile(gray, 99)) < 50),
            "letterbox": bool(rows[:6].mean() < 14 and rows[-6:].mean() < 14 and rows[20:70].mean() > 40),
            "pillarbox": bool(cols[:12].mean() < 14 and cols[-12:].mean() < 14 and cols[40:120].mean() > 40),
            "hist_dist": distance,
            "cut": bool(distance > CUT),
            "cuts_60": float(len(self.cuts)),
            "motion": motion,
            "static": bool(motion < 0.5),
            "grass": grass,
            "grass_10": float(np.median([g for _, g in self.grass_hist])),
            "saturation": float(s.mean()),
            "dark_share": float((gray < 30).mean()),
        }


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Probability that a random positive scores above a random negative (ties count half)."""
    x = np.concatenate([pos, neg])
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(1, len(x) + 1)
    _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    ranks = (np.bincount(inverse, weights=ranks) / counts)[inverse]
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / max(1, len(pos) * len(neg)))


BOOLEANS = ["black", "letterbox", "pillarbox", "cut", "static", "dip_30"]
CONTINUOUS = ["cuts_60", "hist_dist", "motion", "grass", "grass_10", "saturation", "lum", "dark_share",
              "p10_10", "mean_10", "crest_10"]


def report(rows: list[dict], title: str) -> None:
    labels = np.array([r["label"] for r in rows])
    game, ad = labels == "GAME", labels == "AD"
    print(f"\n  --- {title}: {int(ad.sum())} AD frames, {int(game.sum())} GAME frames")
    if not ad.any() or not game.any():
        print("      (one class is empty - nothing to separate)")
        return
    for name in BOOLEANS:
        if name not in rows[0]:
            continue
        fires = np.array([bool(r[name]) for r in rows])
        purity = 100 * ad[fires].mean() if fires.any() else float("nan")
        print(f"      {name:<11} fires in {100 * fires[ad].mean():5.1f}% of AD, {100 * fires[game].mean():5.1f}% of GAME frames"
              f"   -> when it fires: {purity:5.1f}% AD  ({int(fires.sum())} frames)")
    for name in CONTINUOUS:
        if name not in rows[0]:
            continue
        values = np.array([r[name] for r in rows], float)
        ok = ~np.isnan(values)
        if not (ok & ad).any() or not (ok & game).any():
            continue
        a, g = values[ok & ad], values[ok & game]
        print(f"      {name:<11} AUC(ad) {auc(a, g):.3f}   AD median {np.median(a):7.3f} [{np.percentile(a, 10):7.3f}..{np.percentile(a, 90):7.3f}]"
              f"   GAME median {np.median(g):7.3f} [{np.percentile(g, 10):7.3f}..{np.percentile(g, 90):7.3f}]")


def analyse(session: Path, audio: AudioLog | None, t_from: float, t_to: float, dump: Path | None) -> list[dict]:
    source = FileReplaySource(session)
    features = FrameFeatures()
    rows = []
    for row in source.rows:
        t = float(row["t"])
        image = cv2.imread(str(session / row["file"]))
        if image is None:
            continue
        item = {"t": t, "wall": row["wall"], "label": row["state"], **features.compute(image, t)}
        if audio is not None:
            item.update(audio.features(wall_seconds(row["wall"])))
        rows.append(item)
    fixed = effective_labels([r["t"] for r in rows], [r["label"] for r in rows], source.events(), evidence_pairs(session))
    for row, label in zip(rows, fixed):
        row["label"] = label
    ev_t, ev_on = recorded_evidence(session)
    for row in rows:  # what the live detector saw at that moment: nearest evidence row
        if len(ev_t):
            i = min(len(ev_t) - 1, int(np.searchsorted(ev_t, row["t"])))
            row["graphic_on"] = bool(ev_on[i]) if abs(ev_t[i] - row["t"]) < 1.0 else False
        else:
            row["graphic_on"] = False
    rows = [r for r in rows if t_from <= r["t"] <= t_to]
    if dump and rows:
        with open(dump, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(f"\n=== {session.name}: {len(rows)} frames")
    report(rows, "overall")
    report([r for r in rows if not r["graphic_on"]], "graphics missing (where an extra indicator would be consulted)")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--audio", type=Path, help="peak_logger.py CSV to join by wall clock")
    parser.add_argument("--from", dest="t_from", type=float, default=0.0)
    parser.add_argument("--to", dest="t_to", type=float, default=float("inf"))
    parser.add_argument("--dump", type=Path, help="write per-frame features to this CSV (single session)")
    args = parser.parse_args()
    sessions = args.sessions
    if not sessions:
        everything = sorted(p for p in (ROOT / "sessions").glob("*") if (p / "frames.csv").exists())
        sessions = [p for p in everything if not FileReplaySource(p).unreliable_labels and len(FileReplaySource(p)) > 500]
        print(f"{len(sessions)} session(s) with trustworthy labels and more than 500 frames")
    audio = AudioLog(args.audio) if args.audio else None
    pooled = []
    for session in sessions:
        pooled += analyse(session, audio, args.t_from, args.t_to, args.dump if len(sessions) == 1 else None)
    if len(sessions) > 1:
        print("\n=== ALL SESSIONS POOLED")
        report(pooled, "overall")
        report([r for r in pooled if not r["graphic_on"]], "graphics missing (where an extra indicator would be consulted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
