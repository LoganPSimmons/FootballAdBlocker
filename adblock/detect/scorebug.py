"""Persistent broadcast graphics (scorebug, network bug): learned per session, no templates.

During game coverage a few small graphics sit at fixed positions for minutes - the scorebug's
frame, team names and logos, the network's corner bug - while everything in an ad changes
within 15-60 s. DVR commercial skippers find channel logos the same way on recordings; here it
runs live:

  edges     each analysed frame -> binary edge map (640 px wide, Canny, dilated 3x3)
  learner   keeps ~8 minutes of edge maps (one per second) and looks for the pixels whose
            edges persist *together*: start from overall frequency, then keep the pixels
            present in >= 90 % of the frames that match the first guess, and repeat
  regions   persistent pixels are grouped into regions that are scored separately, because
            they come and go separately: replays, full-screen graphics and the return-from-
            break bumper drop the scorebug but keep the corner bug (seen on FOX, 2026-09-19)
  score     per region: share of its edge pixels present, minus the share of its "quiet"
            pixels (persistently edge-free ones around it) that now have edges. A graphic
            that is on screen scores ~0.9; busy random texture ~0; a flat frame 0.

Viewer corrections steer the learner: frames tagged GAME seed the mask (so a new scorebug
layout is picked up right after "that's the game"); frames tagged AD are simply left out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

ANALYSIS_WIDTH = 640
BORDER = 4  # px ignored around the analysed picture: crop seams and letterbox lines live there
SAMPLE_PERIOD_S = 1.0
BUFFER_SAMPLES = 480  # ~8 minutes of history
MIN_SAMPLES = 40  # no mask before ~40 s of changing picture
MIN_SEED_FRAMES = 30
PERSISTENT = 0.90  # a mask pixel has an edge in >= 90 % of game-like frames
QUIET = 0.03  # a quiet pixel has one in <= 3 %
MIN_REGION_PIXELS = 80
MAX_MASK_FRACTION = 0.08  # more persistent edges than this = a paused/static picture, not graphics
PRESENT = 0.6  # region score that counts as "on screen" (2026-09-19 FOX: ads <= 0.43, game >= 0.83)

TAG_NONE, TAG_GAME, TAG_AD = 0, 1, -1


def analysis_gray(image_bgr: np.ndarray, rect: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """The video part of a captured frame as 640-px-wide grayscale."""
    if rect is not None:
        left, top, right, bottom = rect
        image_bgr = image_bgr[top:bottom, left:right]
    height, width = image_bgr.shape[:2]
    if width != ANALYSIS_WIDTH:
        size = (ANALYSIS_WIDTH, max(1, round(height * ANALYSIS_WIDTH / width)))
        image_bgr = cv2.resize(image_bgr, size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def edge_map(gray: np.ndarray) -> np.ndarray:
    """Boolean edge map, dilated so a one-pixel wobble (scaling, JPEG) still lines up."""
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 130)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8)) > 0
    edges[:BORDER] = edges[-BORDER:] = False
    edges[:, :BORDER] = edges[:, -BORDER:] = False
    return edges


@dataclass
class Region:
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 in analysis pixels
    on: np.ndarray = field(repr=False)  # flat indices: persistent edge pixels
    off: np.ndarray = field(repr=False)  # flat indices: persistently quiet pixels around them

    def score(self, edges_flat: np.ndarray) -> float:
        hit = float(edges_flat[self.on].mean())
        noise = float(edges_flat[self.off].mean()) if len(self.off) else 0.0
        return hit - noise

    @property
    def where(self) -> str:
        """Rough position, for humans: 'bottom-centre', 'top-right', ..."""
        x0, y0, x1, y1 = self.bbox
        return f"{'top' if (y0 + y1) / 2 < 120 else 'bottom' if (y0 + y1) / 2 > 240 else 'middle'}-" \
               f"{'left' if (x0 + x1) / 2 < 213 else 'right' if (x0 + x1) / 2 > 427 else 'centre'}"


@dataclass
class Mask:
    shape: tuple[int, int]
    regions: list[Region]
    seed_frames: int  # how many game-like frames it was learned from
    seeded: bool = False  # learned from frames the viewer tagged as GAME (may be scene content, not graphics)

    def scores(self, edges: np.ndarray) -> list[float]:
        flat = edges.reshape(-1)
        return [region.score(flat) for region in self.regions]

    def picture(self, gray: np.ndarray | None = None) -> np.ndarray:
        """BGR visualisation: persistent edges green, quiet pixels dark red, region boxes yellow."""
        height, width = self.shape
        canvas = np.zeros((height, width, 3), np.uint8) if gray is None else cv2.cvtColor(gray // 2, cv2.COLOR_GRAY2BGR)
        flat = canvas.reshape(-1, 3)
        for region in self.regions:
            flat[region.off] = (40, 40, 140)
            flat[region.on] = (80, 255, 80)
            x0, y0, x1, y1 = region.bbox
            cv2.rectangle(canvas, (x0, y0), (x1, y1), (0, 220, 255), 1)
        return canvas


class MaskLearner:
    """Rolling history of edge maps (in the 640-px analysis space), and the mask they imply."""

    def __init__(self, shape: tuple[int, int], capacity: int = BUFFER_SAMPLES) -> None:
        self.shape = shape
        self.capacity = capacity
        height, width = shape
        self._packed = np.zeros((capacity, height, (width + 7) // 8), np.uint8)
        self._tags = np.zeros(capacity, np.int8)
        self._sum_all = np.zeros(shape, np.int32)  # running per-pixel edge counts, so that
        self._sum_game = np.zeros(shape, np.int32)  # learn() never has to scan whole frames
        self._count = 0
        self._last_t = float("-inf")
        self._last_gray: np.ndarray | None = None

    def __len__(self) -> int:
        return min(self._count, self.capacity)

    def _unpack(self, packed: np.ndarray) -> np.ndarray:
        return np.unpackbits(packed, axis=-1)[..., : self.shape[1]].astype(bool)

    def add(self, gray: np.ndarray, edges: np.ndarray, t: float, tag: int = TAG_NONE) -> bool:
        """Keep one sample per second - and none while the picture stands still (paused video)."""
        if t - self._last_t < SAMPLE_PERIOD_S:
            return False
        small = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA)
        moving = self._last_gray is None or float(cv2.absdiff(small, self._last_gray).mean()) > 0.4
        self._last_t, self._last_gray = t, small
        if not moving:
            return False
        slot = self._count % self.capacity
        if self._count >= self.capacity:  # evict the oldest sample from the running sums
            old = self._unpack(self._packed[slot])
            self._sum_all -= old
            if self._tags[slot] == TAG_GAME:
                self._sum_game -= old
        self._packed[slot] = np.packbits(edges, axis=1)
        self._tags[slot] = tag
        self._sum_all += edges
        if tag == TAG_GAME:
            self._sum_game += edges
        self._count += 1
        return True

    # --- learning -----------------------------------------------------------------------
    def _series(self, pixels: np.ndarray) -> np.ndarray:
        """History of the given pixels as a (samples, pixels) boolean matrix, in flat-index order.

        Only the bands of rows that contain any of them are unpacked: broadcast graphics hug
        the top and bottom of the picture, so most of the frame is never touched.
        """
        n = len(self)
        rows = np.flatnonzero(pixels.any(axis=1))
        out = np.empty((n, int(pixels.sum())), bool)
        column = 0
        for band in np.split(rows, np.flatnonzero(np.diff(rows) > 8) + 1) if len(rows) else []:
            top, bottom = band[0], band[-1] + 1
            window = pixels[top:bottom]
            width = int(window.sum())
            for start in range(0, n, 128):
                out[start : start + 128, column : column + width] =                     self._unpack(self._packed[start : min(start + 128, n), top:bottom])[:, window]
            column += width
        return out

    def learn(self) -> Mask | None:
        """The mask the history implies, or None if it doesn't (yet) imply one.

        Frames the viewer confirmed as GAME are tried as the starting point first; if they
        contain nothing persistent (a graphics-free segment), fall back to the unseeded guess.
        """
        n = len(self)
        if n < MIN_SAMPLES:
            return None
        known_game = self._tags[:n] == TAG_GAME
        if known_game.sum() >= MIN_SEED_FRAMES:
            mask = self._learn(seeded=True)
            if mask is not None:
                return mask
        return self._learn(seeded=False)

    def _learn(self, seeded: bool) -> Mask | None:
        n = len(self)
        tags = self._tags[:n]
        usable = tags != TAG_AD
        known_game = tags == TAG_GAME
        height, width = self.shape
        max_pixels = int(MAX_MASK_FRACTION * height * width)

        # Candidates: anything that could end up persistent. Everything below works on their
        # history only, a few thousand pixels instead of 230 000.
        for share in (0.35, 0.5, 0.65, 0.8):  # a busy picture (crowd shots) makes everything "frequent":
            candidates = self._sum_all >= share * n  # raise the bar until the set is small again
            if candidates.sum() <= 4 * max_pixels:
                break
        else:
            return None
        if seeded:
            candidates |= self._sum_game >= 0.8 * known_game.sum()
        if candidates.sum() < MIN_REGION_PIXELS:
            return None
        series = self._series(candidates)

        if seeded:  # the viewer told us which frames are the game: start from those
            seed = known_game
        else:  # otherwise game coverage has to dominate the history for the first guess to find it
            guess = series[usable].mean(axis=0) >= 0.5
            if guess.sum() < MIN_REGION_PIXELS:
                return None
            seed = usable & (series[:, guess].mean(axis=1) >= 0.7)
        for _ in range(3):
            if seed.sum() < MIN_SEED_FRAMES:
                return None
            persistent_c = series[seed].mean(axis=0) >= PERSISTENT
            if not MIN_REGION_PIXELS <= persistent_c.sum() <= max_pixels:
                return None
            seed = usable & (series[:, persistent_c].mean(axis=1) >= 0.7)
            if seeded:
                seed |= known_game

        persistent = np.zeros(self.shape, bool)
        persistent[candidates] = persistent_c
        regions = self._regions(persistent, seed)
        return Mask(self.shape, regions, int(seed.sum()), seeded) if regions else None

    def history_scores(self, region: Region) -> np.ndarray:
        """The region's score on every frame of the history (same formula as Region.score)."""
        pixels = np.zeros(self.shape, bool)
        flat = pixels.reshape(-1)
        flat[region.on] = flat[region.off] = True
        series = self._series(pixels)
        is_on = np.isin(np.flatnonzero(flat), region.on)
        hit = series[:, is_on].mean(axis=1)
        noise = series[:, ~is_on].mean(axis=1) if (~is_on).any() else 0.0
        return hit - noise

    def _regions(self, persistent: np.ndarray, seed: np.ndarray) -> list[Region]:
        height, width = self.shape
        blobs = cv2.dilate(persistent.astype(np.uint8), np.ones((9, 9), np.uint8))  # letters -> one logo
        count, labels, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
        regions = []
        for label in range(1, count):
            on = persistent & (labels == label)
            if on.sum() < MIN_REGION_PIXELS:
                continue
            x, y, w, h = stats[label, :4]
            x0, y0, x1, y1 = max(0, x - 6), max(0, y - 6), min(width, x + w + 6), min(height, y + h + 6)
            # Quiet pixels: edge-free in the seed frames even after the 3x3 dilation, i.e.
            # reliably flat parts of the graphic (or of its surroundings).
            box = np.zeros(self.shape, bool)
            box[y0:y1, x0:x1] = ~persistent[y0:y1, x0:x1]
            quiet = np.zeros(self.shape, bool)
            quiet[box] = self._series(box)[seed].mean(axis=0) <= QUIET
            regions.append(Region((int(x0), int(y0), int(x1), int(y1)), np.flatnonzero(on), np.flatnonzero(quiet)))
        return regions
