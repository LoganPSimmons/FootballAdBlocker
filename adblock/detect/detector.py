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

Switching the graphics off ("Use learned graphics" in the control window, ``use_graphics``)
keeps all of this running - learning, tracking, the picture in the window, the memory - but
tells the state machine nothing about them: the evidence is "unknown" throughout and the image
model decides alone, exactly as it does in the minutes before anything has been learned.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from datetime import date

from adblock.detect import scorebug
from adblock.detect.fusion import AD, GAME, Fusion, FusionSettings
from adblock.detect.memory import Remembered

RELEARN_EVERY_S = 30.0
FORGET_AFTER_S = 45 * 60.0
AD_TAG_AFTER_S = 10.0  # a forced AD the evidence still contradicts after this long tags frames as ads
TRUST_AFTER_S = 60.0  # on-screen time a region learned from viewer-tagged frames needs before it counts
MISSABLE_S = 60.0  # on-screen time (since learned) before a region's *absence* can mean "ad"
FAST_RETURN_S = 180.0  # on-screen time before a region alone may bring the game back within a second
FORGET_YOUNG_AFTER_S = 10 * 60.0  # a region with little on-screen time that hasn't been seen this long goes
ESTABLISHED_S = 300.0  # on-screen time after which a region is never purged as ambiguous
AMBIGUOUS = (0.35, 0.75)  # a useful graphic scores ~0.9 or ~0 on the history, rarely in between
SUSPECT_AFTER_S, SUSPECT_SHARE = 8.0, 0.5  # on screen this long, mostly during "commercial" verdicts = an ad banner
MAX_AMBIGUOUS_SHARE = 0.15
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
            left, top, right, bottom = (int(v) for v in (2 * columns[0], 2 * rows[0], 2 * (columns[-1] + 1), 2 * (rows[-1] + 1)))
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
    last_seen: float
    score: float = 0.0
    seen_s: float = 0.0  # cumulative seconds it has been on screen since it was learned
    seeded: bool = False  # learned from viewer-tagged frames: has to earn trust (TRUST_AFTER_S)
    proven: bool = False  # remembered from an earlier session: may bring the game back fast from the start
    ad_seen_s: float = 0.0  # of seen_s, the seconds the image model called the picture a commercial

    @property
    def trusted(self) -> bool:
        return (not self.seeded or self.seen_s >= TRUST_AFTER_S) and not self.suspect

    @property
    def suspect(self) -> bool:
        """Mostly on screen while the picture looked like a commercial: an ad's own banner.
        Live 2026-09-26: a local ad repeating in every break has a static bottom banner; the
        learner took it for a broadcast graphic and it overruled the viewer's own "AD"."""
        return self.seen_s >= SUSPECT_AFTER_S and self.ad_seen_s > SUSPECT_SHARE * self.seen_s

    @property
    def present(self) -> bool:
        return self.score >= self.region.threshold


@dataclass
class Observation:
    t: float
    state: str  # what the state machine wants the app to be in
    game_like: bool | None  # this frame's evidence (None: can't tell)
    override: str | None  # viewer correction still being held
    scores: list[float] = field(default_factory=list)  # one per tracked region
    boxes: list[Rect] = field(default_factory=list)  # their boxes, in 640-px analysis space
    on_screen: int = 0  # how many of the tracked graphics are on screen right now
    scene: bool | None = None  # the image model's opinion that went into this decision
    video_rect: Rect | None = None  # where the video is inside the captured frame
    learned: bool = False  # the graphics library changed on this frame
    black: bool = False  # a cut to black (the splice between two things: ads, or an ad and the broadcast)

    @property
    def best(self) -> float:
        return max(self.scores, default=0.0)


class Detector:
    def __init__(self, settings: FusionSettings | None = None, remembered: list[Remembered] | None = None,
                 use_graphics: bool = True) -> None:
        self.fusion = Fusion(settings)
        self.remembered = list(remembered or [])  # graphics proven in earlier sessions (memory.py)
        self.use_graphics = use_graphics  # False: learned, tracked and drawn, but the state machine hears nothing of them
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

    def set_graphics(self, enabled: bool) -> None:
        """"Use learned graphics" in the control window. Off, the image model decides alone."""
        if enabled != self.use_graphics:
            with self._lock:
                self.use_graphics = enabled

    def relearn(self) -> None:
        """Forget everything learned (button in the UI, for when the picture geometry or the
        broadcast's graphics were misjudged) - remembered graphics included, for this session."""
        with self._lock:
            self._learners.clear()
            self._library.clear()
            self._last_learn.clear()
            self.remembered = []
            self.area.forget()

    def proven(self, label: str = "") -> list[Remembered]:
        """Fullscreen graphics worth remembering: minutes on screen in this session."""
        with self._lock:
            today = date.today().isoformat()
            return [Remembered(key[1], tr.region, tr.seen_s, today, label)
                    for key, library in self._library.items() if key[0] == FULLSCREEN
                    for tr in library if tr.seen_s >= FAST_RETURN_S and not tr.suspect]

    def unknown(self, t: float) -> Observation:
        """No usable picture right now (window shows another tab, is minimized, no frames)."""
        with self._lock:
            self._last_t = t
            return Observation(t, self.fusion.update(None, t), None, self.fusion.override)

    def process(self, image: np.ndarray, t: float, fullscreen: bool | None = None, scene: bool | None = None,
                quick: bool | None = None) -> Observation:
        with self._lock:
            return self._process(image, t, fullscreen, scene, quick)

    def _process(self, image: np.ndarray, t: float, fullscreen: bool | None, scene: bool | None, quick: bool | None) -> Observation:
        elapsed, self._last_t = min(1.0, max(0.0, t - self._last_t)), t
        rect = self.area.locate(image, t, fullscreen)
        if rect is None:
            return Observation(t, self.fusion.update(None, t, scene=scene), None, self.fusion.override, scene=scene)
        gray = scorebug.analysis_gray(image, rect)
        black = is_black(gray)
        edges = scorebug.edge_map(gray)
        key = geometry_key(image.shape, rect, edges.shape)
        learner = self._learners.setdefault(key, scorebug.MaskLearner(edges.shape))
        if key not in self._library:
            # A warm start: remembered graphics join the library with no on-screen time, so
            # they can vouch for the game at once but their absence means nothing yet.
            self._library[key] = [TrackedRegion(item.region, t, proven=True) for item in self.remembered
                                  if key[0] == FULLSCREEN and item.shape == edges.shape]
        library = self._library[key]

        flat = edges.reshape(-1)
        commercial = quick is False or scene is False  # the image model's view of this frame
        for tracked in library:
            tracked.score = tracked.region.score(flat)
            if tracked.present:
                tracked.seen_s += elapsed
                tracked.last_seen = t
                if commercial:
                    tracked.ad_seen_s += elapsed
        # A graphic on screen vouches for the game as soon as it is trusted. Its absence only
        # means something once it has been around for a while: a segment graphic learned a
        # moment ago (a halftime highlights bug, live 2026-09-19) says nothing when it leaves.
        on_screen = [tr for tr in library if tr.trusted and tr.present]
        strong = any(tr.seen_s >= FAST_RETURN_S or tr.proven for tr in on_screen)  # live 2026-09-26: 4 s returns after every launch
        if on_screen:
            game_like = True
        elif any(tr.trusted and tr.seen_s >= MISSABLE_S for tr in library):
            game_like = False
        else:
            game_like = None
        if not self.use_graphics:  # the viewer's choice: the model decides alone, as in the minutes before anything is learned
            game_like, strong = None, False
        state = self.fusion.update(game_like, t, strong, scene, black, quick)

        tag = self._tag(t)
        if tag == scorebug.TAG_NONE and commercial:
            tag = scorebug.TAG_AD  # what looks like a commercial teaches no graphics (a repeated ad's banner would)
        learner.add(gray, edges, t, tag)
        learned = False
        if t - self._last_learn.get(key, float("-inf")) >= RELEARN_EVERY_S:
            self._last_learn[key] = t
            learned = self._learn(learner, library, t, flat)
        return Observation(t, state, game_like, self.fusion.override, [tr.score for tr in library],
                           [tr.region.bbox for tr in library], sum(tr.present for tr in library), scene, rect, learned, black)

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
            for region in sorted(mask.regions, key=lambda r: -len(r.on)):  # whole graphics before their parts
                same = next((tr for tr in library if _overlap(tr.region.bbox, region.bbox) > 0.5), None)
                if same is not None:
                    same.region = region  # same graphic, fresher pixels
                elif any(_inside(region.bbox, tr.region.bbox) > 0.7 for tr in library):
                    continue  # a fragment of a graphic we already track: small, unspecific, and not needed
                elif not _ambiguous(learner, region):
                    library[:] = [tr for tr in library if _inside(tr.region.bbox, region.bbox) <= 0.7]  # it replaces its parts
                    library.append(TrackedRegion(region, t, region.score(flat), seeded=mask.seeded))
        # A tracked graphic can grow (refreshed pixels, bigger box) until it contains what used
        # to be a neighbour: the part goes, the whole stays.
        library[:] = [part for part in library
                      if not any(whole is not part and len(whole.region.on) > len(part.region.on)
                                 and _inside(part.region.bbox, whole.region.bbox) > 0.7 for whole in library)]
        # Young regions must keep separating the history cleanly; established ones are never
        # purged (the corner bug must survive a 20-minute halftime whatever it looks like there).
        library[:] = [tr for tr in library
                      if t - tr.last_seen < (FORGET_AFTER_S if tr.seen_s >= FAST_RETURN_S else FORGET_YOUNG_AFTER_S)
                      and (tr.seen_s >= ESTABLISHED_S or not _ambiguous(learner, tr.region))
                      and not tr.suspect]
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
            key = geometry_key(image.shape, rect, gray.shape)
            for tracked in self._library.get(key, []):
                colour = (150, 150, 150) if not tracked.trusted else (80, 255, 80) if tracked.present else (60, 60, 255)
                flat = canvas.reshape(-1, 3)
                flat[tracked.region.on] = colour
                x0, y0, x1, y1 = tracked.region.bbox
                cv2.rectangle(canvas, (x0, y0), (x1, y1), colour, 1)
            return canvas


FULLSCREEN = "fullscreen"


def geometry_key(shape: tuple, rect: Rect, analysis_shape: tuple[int, int]) -> tuple:
    """Which graphics library a picture belongs to. Fullscreen pictures share one whatever their
    exact pixel size - a 1920x1079 window (Fubo, live 2026-09-26) analyses to the same 640x360 as
    1920x1080, and starting afresh for it forgot everything mid-break. A video area inside a
    browser window is pixel-exact."""
    if rect == (0, 0, shape[1], shape[0]):
        return (FULLSCREEN, analysis_shape)
    return (shape[1], shape[0], rect, analysis_shape)


BLACK_MEAN, BLACK_BRIGHT, BLACK_BRIGHT_SHARE = 16.0, 60, 0.005


def is_black(gray: np.ndarray) -> bool:
    """A cut to black: a dark frame with (almost) no bright pixels at all - a night-game shadow
    is dark on average but full of bright spots. Measured on the recordings: 4 fps catches
    the splice to black before both ABC returns of 2026-09-26 (2.2 s and 0.7 s of black)."""
    return float(gray.mean()) < BLACK_MEAN and float((gray > BLACK_BRIGHT).mean()) < BLACK_BRIGHT_SHARE


def _ambiguous(learner: scorebug.MaskLearner, region: scorebug.Region) -> bool:
    """True if the region often scores in the no-man's-land between "on screen" and "gone" -
    the signature of scene content (a studio set, a crowd) rather than a broadcast graphic,
    and the kind of region that matches a busy commercial by chance."""
    if len(learner) < 60:
        return False
    scores = learner.history_scores(region)
    return float(((scores >= AMBIGUOUS[0]) & (scores < AMBIGUOUS[1])).mean()) > MAX_AMBIGUOUS_SHARE


def _inside(a: Rect, b: Rect) -> float:
    """Share of box ``a`` that lies inside box ``b``."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / max(1, (a[2] - a[0]) * (a[3] - a[1]))


def _overlap(a: Rect, b: Rect) -> float:
    """Intersection over union of two boxes."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


__all__ = ["AD", "GAME", "Detector", "Observation", "FusionSettings"]
