"""Graphics remembered between sessions, so a broadcast seen before starts warm.

A network's corner logo is the same every week and its scorebug frame the same all season;
only team names and logos change. So every graphic that proved itself in a session (minutes
on screen, survived the ambiguity purge) is saved, and the next session starts with them in
its library instead of an empty one.

Safe by construction, because a remembered graphic gets no special rights (detector.py): on
screen, it vouches for the game from the first frame; *off* screen it means nothing until it
has been seen for a minute in the new session. A stale or wrong-network graphic therefore
can never hide anything - it simply never matches and is dropped from the session after ten
minutes. Last week's scorebug half-matches this week's, scores in no-man's-land, and is
purged as ambiguous while the learner picks up the new one, which then replaces it on disk.

Only fullscreen graphics are kept (a browser window's video area differs from day to day).
The file holds plain arrays and JSON, no pickles: learned/graphics.npz, git-ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from adblock.detect.scorebug import Region, around_indices

MAX_REMEMBERED = 32
EXPIRE_DAYS = 120


@dataclass
class Remembered:
    shape: tuple[int, int]  # analysis picture the pixel indices refer to (360, 640)
    region: Region
    seen_s: float  # on-screen time accumulated over all sessions
    last_seen: str  # ISO date of the last session that proved it
    label: str = ""  # where it came from, for humans: "FOX  USU @ UTAH"
    on_screen_this_session: float = field(default=0.0, compare=False)


def load(path: Path) -> list[Remembered]:
    if not path.exists():
        return []
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            items = []
            for i, m in enumerate(meta):
                shape, bbox, on = tuple(m["shape"]), tuple(m["bbox"]), data[f"on_{i}"]
                region = Region(bbox, on, data[f"off_{i}"], around_indices(shape, bbox, on))
                items.append(Remembered(shape, region, float(m["seen_s"]), m["last_seen"], m.get("label", "")))
            return items
    except (OSError, ValueError, KeyError):  # a damaged memory is no memory, never a crash
        return []


def save(path: Path, items: list[Remembered]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {}
    for i, item in enumerate(items):
        arrays[f"on_{i}"] = item.region.on.astype(np.int32)
        arrays[f"off_{i}"] = item.region.off.astype(np.int32)
    meta = [{"shape": list(item.shape), "bbox": [int(v) for v in item.region.bbox], "seen_s": round(item.seen_s, 1),
             "last_seen": item.last_seen, "label": item.label} for item in items]
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, meta=np.array(json.dumps(meta)), **arrays)
    temporary.replace(path)  # never leave a half-written memory behind


def merge(known: list[Remembered], proven: list[Remembered], today: date | None = None) -> list[Remembered]:
    """What to keep on disk: this session's proven graphics replace the remembered ones they
    overlap (same place, fresher pixels), everything else stays until it expires."""
    today = today or date.today()
    kept = list(proven)
    for old in known:
        if date.fromisoformat(old.last_seen) < today - timedelta(days=EXPIRE_DAYS):
            continue
        twin = next((new for new in proven if new.shape == old.shape and _overlap(new.region.bbox, old.region.bbox) > 0.5), None)
        if twin is None:
            kept.append(old)
        else:
            twin.seen_s += old.seen_s
    kept.sort(key=lambda item: (item.last_seen, item.seen_s), reverse=True)
    return kept[:MAX_REMEMBERED]


def _overlap(a: tuple, b: tuple) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
