"""Frame in, GAME/AD out: video area -> edge map -> learned graphics -> state machine.

One Detector serves the live app and the offline evaluator alike (frames come from
capture.WgcWindowSource or capture.FileReplaySource), so anything measured on recordings is
what runs live.

Picture geometry. The learned graphics are pixel-exact, so they are kept per geometry: the
captured frame's size plus where the video sits inside it. Fullscreen = the whole frame. In a
normal browser window the video area is found from where the picture *changes* (browser
chrome and letterbox bars never do). A geometry nobody has learned yet yields "unknown"
evidence, which the state machine never treats as a reason to hide anything.

Graphics library. Every region the learner finds is kept and tracked, not replaced: after a
studio show the old scorebug must still be recognised the moment it returns. A region only
leaves when it hasn't been seen for 45 minutes. In particular it is *not* dropped for being on
screen while the viewer says "ad": the halftime studio show carries the network's corner bug,
viewers reasonably call that a break, and losing the corner bug would bring back false alarms
on every replay - trading a cheap mistake (an ad leaks) for the expensive one (play hidden).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from adblock.detect import scorebug
from adblock.detect.fusion import AD, GAME, Fusion, FusionSettings

RELEARN_EVERY_S = 30.0
FORGET_AFTER_S = 45 * 60.0
AD_TAG_AFTER_S = 10.0  # a forced AD the evidence still contradicts after this long tags frames as ads
AREA_MIN_S, AREA_MIN_FRAMES = 8.0, 24

Rect = tuple[int, int, int, int]


class VideoArea:
    """Where the video sits inside frames of one size (left, top, right, bottom), or None while unsure."""

    def __init__(self) -> None:
        self._known: dict[tuple[int, int], Rect] = {}
        self._size: tuple[int, int] | None = None
        self._changed: np.ndarray | None = None
        self._previous: np.ndarray | None = None
        self._since = 0.0
        self._frames = 0

    def forget(self) -> None:
        self._known.clear()
        self._size = None

    def locate(self, image: np.ndarray, t: float, fullscreen: bool | None) -> Rect | None:
        height, width = image.shape[:2]
        if fullscreen:
            return (0, 0, width, height)
        if (width, height) in self._known:
            return self._known[(width, height)]
        small = cv2.cvtColor(cv2.resize(image, (width // 2, height // 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        if self._size != (width, height):
            self._size, self._changed, self._previous = (width, height), np.zeros(small.shape, bool), small
            self._since, self._frames = t, 0
            return None
        self._changed |= cv2.absdiff(small, self._previous) > 12
        self._previous = small
        self._frames += 1
        if t - self._since < AREA_MIN_S or self._frames < AREA_MIN_FRAMES:
            return None
        rows = np.flatnonzero(self._changed.mean(axis=1) >= 0.3)
        columns = np.flatnonzero(self._changed.mean(axis=0) >= 0.3)
        if len(rows) and len(columns):
            left, top, right, bottom = 2 * columns[0], 2 * rows[0], 2 * (columns[-1] + 1), 2 * (rows[-1] + 1)
            aspect = (right - left) / max(1, bottom - top)
            if abs(aspect - 16 / 9) < 0.04 and right - left >= 0.5 * width:  # a 16:9 picture, not a stray animation
                inset = 3  # stay clear of the seam between video and letterbox
                self._known[(width, height)] = (left + inset, top + inset, right - inset, bottom - inset)
                return self._known[(width, height)]
        if t - self._since > 60.0:  # dark or static picture: start the estimate over
            self._size = None
        return None


@dataclass
class TrackedRegion:
    region: scorebug.Region
    seen_s: float  # cumulative seconds it has been on screen
    last_seen: float
    score: float = 0.0


@dataclass
class Observation:
    t: float
    state: str  # what the state machine wants the app to be in
    game_like: bool | None  # this frame's evidence (None: can't tell)
    override: str | None  # viewer correction still being held
    scores: list[float] = field(default_factory=list)  # one per tracked region
    boxes: list[Rect] = field(default_factory=list)  # their boxes, in 640-px analysis space
    video_rect: Rect | None = None  # where the video is inside the captured frame
    learned: bool = False  # the graphics library changed on this frame

    @property
    def best(self) -> float:
        return max(self.scores, default=0.0)


class Detector:
    def __init__(self, settings: FusionSettings | None = None) -> None:
        self.fusion = Fusion(settings)
        self.area = VideoArea()
        self._lock = threading.Lock()  # process() runs on the detector thread, correct() on the UI thread
        self._learners: dict[tuple, scorebug.MaskLearner] = {}
        self._library: dict[tuple, list[TrackedRegion]] = {}
        self._last_learn: dict[tuple, float] = {}
        self._corrected_at: float | None = None
        self._last_t = 0.0

    @property
    def state(self) -> str:
        return self.fusion.state

    def correct(self, state: str) -> None:
        with self._lock:
            self.fusion.correct(state)
            self._corrected_at = self._last_t

    def relearn(self) -> None:
        """Forget everything learned (button in the UI, for when the picture geometry or the
        broadcast's graphics were misjudged)."""
        with self._lock:
            self._learners.clear()
            self._library.clear()
            self._last_learn.clear()
            self.area.forget()

    def unknown(self, t: float) -> Observation:
        """No usable picture right now (window shows another tab, is minimized, no frames)."""
        with self._lock:
            self._last_t = t
            return Observation(t, self.fusion.update(None, t), None, self.fusion.override)

    def process(self, image: np.ndarray, t: float, fullscreen: bool | None = None) -> Observation:
        with self._lock:
            return self._process(image, t, fullscreen)

    def _process(self, image: np.ndarray, t: float, fullscreen: bool | None) -> Observation:
        elapsed, self._last_t = min(1.0, max(0.0, t - self._last_t)), t
        rect = self.area.locate(image, t, fullscreen)
        if rect is None:
            return Observation(t, self.fusion.update(None, t), None, self.fusion.override)
        gray = scorebug.analysis_gray(image, rect)
        edges = scorebug.edge_map(gray)
        key = (image.shape[1], image.shape[0], rect, edges.shape)
        learner = self._learners.setdefault(key, scorebug.MaskLearner(edges.shape))
        library = self._library.setdefault(key, [])

        flat = edges.reshape(-1)
        for tracked in library:
            tracked.score = tracked.region.score(flat)
            if tracked.score >= scorebug.PRESENT:
                tracked.seen_s += elapsed
                tracked.last_seen = t
        game_like = any(tr.score >= scorebug.PRESENT for tr in library) if library else None
        state = self.fusion.update(game_like, t)

        learner.add(gray, edges, t, self._tag(t))
        learned = False
        if t - self._last_learn.get(key, float("-inf")) >= RELEARN_EVERY_S:
            self._last_learn[key] = t
            learned = self._learn(learner, library, t, flat)
        return Observation(t, state, game_like, self.fusion.override, [tr.score for tr in library],
                           [tr.region.bbox for tr in library], rect, learned)

    def _tag(self, t: float) -> int:
        """What the viewer's latest correction says about the current frame, if anything."""
        if self.fusion.override == GAME:
            return scorebug.TAG_GAME  # they just looked at it: the game is on screen
        if self.fusion.override == AD and self._corrected_at is not None and t - self._corrected_at >= AD_TAG_AFTER_S:
            return scorebug.TAG_AD  # kept out of learning ("ad" clicks can be early, so only once it has lasted)
        return scorebug.TAG_NONE

    def _learn(self, learner: scorebug.MaskLearner, library: list[TrackedRegion], t: float, flat: np.ndarray) -> bool:
        mask = learner.learn()
        before = [tr.region.bbox for tr in library]
        if mask is not None:
            for region in mask.regions:
                same = next((tr for tr in library if _overlap(tr.region.bbox, region.bbox) > 0.5), None)
                if same is not None:
                    same.region = region  # same graphic, fresher pixels
                else:
                    library.append(TrackedRegion(region, float(mask.seed_frames), t, region.score(flat)))
        library[:] = [tr for tr in library if t - tr.last_seen < FORGET_AFTER_S]
        after = [tr.region.bbox for tr in library]  # "changed" = a graphic came or went, not a box that wobbled a pixel
        return len(after) != len(before) or any(_overlap(a, b) < 0.8 for a, b in zip(after, before))

    def picture(self, image: np.ndarray, fullscreen: bool | None = None) -> np.ndarray | None:
        """The analysed picture with the learned graphics drawn in, for the UI."""
        with self._lock:
            rect = self.area._known.get((image.shape[1], image.shape[0])) if not fullscreen else (0, 0, image.shape[1], image.shape[0])
            if rect is None:
                return None
            gray = scorebug.analysis_gray(image, rect)
            canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            key = (image.shape[1], image.shape[0], rect, gray.shape)
            for tracked in self._library.get(key, []):
                present = tracked.score >= scorebug.PRESENT
                flat = canvas.reshape(-1, 3)
                flat[tracked.region.on] = (80, 255, 80) if present else (60, 60, 255)
                x0, y0, x1, y1 = tracked.region.bbox
                cv2.rectangle(canvas, (x0, y0), (x1, y1), (80, 255, 80) if present else (60, 60, 255), 1)
            return canvas


def _overlap(a: Rect, b: Rect) -> float:
    """Intersection over union of two boxes."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


__all__ = ["AD", "GAME", "Detector", "Observation", "FusionSettings"]
