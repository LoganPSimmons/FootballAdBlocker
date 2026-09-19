"""Session recorder: low-res frames + a timeline of state changes and hotkey presses.

Every game watched becomes a labelled dataset as a side effect of using the hotkey:

  sessions/<date>_<label>/
    session.json      settings and context of the recording
    timeline.jsonl    one JSON object per line: session_start, hotkey, state, window, capture, session_end
    frames.csv        index,t,wall,file,state,src_w,src_h   (state = app state when the frame was captured)
    frames/000000.jpg ...
    detector.csv      t,app_state,detector_state,game_like,override,auto,scores   (one row per analysed frame)
    graphics/*.png    what the detector had learned, saved whenever that changed

Labels. ``state`` is what the viewer was shown and let stand - a weak label (they may not have
been watching). ``correction`` events in the timeline are the strong ones: the viewer looked
at the screen and said "that's an ad" / "that's the game".

``t`` is seconds since the session started (monotonic clock) in both files, so frames and
events line up exactly. Everything is appended and flushed as it happens: a crash loses at
most the frame being written. Frames stay local and low-res (personal-use training data).
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from adblock import win32
from adblock.capture import FrameSource

FRAME_COLUMNS = ["index", "t", "wall", "file", "state", "src_w", "src_h"]
DETECTOR_COLUMNS = ["t", "app_state", "detector_state", "game_like", "override", "auto", "scores"]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "session"


def _wall() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class SessionWriter:
    """The on-disk format. No threads, no capture: feed it frames and events."""

    def __init__(self, sessions_root: str | Path, label: str, fps: float = 4.0, width: int = 640, jpeg_quality: int = 75) -> None:
        self.fps, self.width, self.jpeg_quality = fps, width, jpeg_quality
        base = Path(sessions_root) / f"{datetime.now():%Y-%m-%d}_{slugify(label)}"
        self.dir, suffix = base, 1
        while self.dir.exists():  # never write into an earlier session
            suffix += 1
            self.dir = base.with_name(f"{base.name}_{suffix}")
        (self.dir / "frames").mkdir(parents=True)

        self.clock0 = time.perf_counter()
        self.frame_count = 0
        self._last_saved = float("-inf")
        self._lock = threading.Lock()
        self._timeline = open(self.dir / "timeline.jsonl", "a", encoding="utf-8")
        self._frames_file = open(self.dir / "frames.csv", "a", newline="", encoding="utf-8")
        self._frames = csv.writer(self._frames_file)
        self._frames.writerow(FRAME_COLUMNS)
        self._detector_file = open(self.dir / "detector.csv", "a", newline="", encoding="utf-8")
        self._detector = csv.writer(self._detector_file)
        self._detector.writerow(DETECTOR_COLUMNS)
        self._detector_rows = 0

    def seconds(self, at: float | None = None) -> float:
        """Session time of a ``time.perf_counter()`` reading (default: now)."""
        return round((time.perf_counter() if at is None else at) - self.clock0, 3)

    def start(self, **metadata) -> None:
        info = {"label": self.dir.name, "started": _wall(), "fps": self.fps, "frame_width": self.width,
                "jpeg_quality": self.jpeg_quality, **metadata}
        (self.dir / "session.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        self.log("session_start", **metadata)

    def log(self, event: str, at: float | None = None, **fields) -> None:
        line = json.dumps({"t": self.seconds(at), "wall": _wall(), "event": event, **fields})
        with self._lock:
            if not self._timeline.closed:
                self._timeline.write(line + "\n")
                self._timeline.flush()

    def add_frame(self, image: np.ndarray, at: float, state: str) -> bool:
        """Save the frame if one is due at the session's frame rate. ``at`` is perf_counter time."""
        if at - self._last_saved < 1.0 / self.fps - 0.05:  # tolerance: capture timing jitters
            return False
        src_h, src_w = image.shape[:2]
        height = max(1, round(src_h * self.width / src_w))  # keep the aspect ratio: never distort the scorebug
        small = cv2.resize(image, (self.width, height), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            return False
        with self._lock:  # index, file and row together: recorder threads can overlap briefly on a re-attach
            if self._frames_file.closed or at - self._last_saved < 1.0 / self.fps - 0.05:
                return False
            self._last_saved = at
            name = f"frames/{self.frame_count:06d}.jpg"
            (self.dir / name).write_bytes(encoded.tobytes())
            self._frames.writerow([self.frame_count, self.seconds(at), _wall(), name, state, src_w, src_h])
            self._frames_file.flush()
            self.frame_count += 1
        return True

    def add_detection(self, at: float, app_state: str, detector_state: str, game_like: bool | None,
                      override: str | None, auto: bool, scores: list[float]) -> None:
        """One analysed frame's evidence. Flushed every few seconds - it is a lot of tiny rows."""
        with self._lock:
            if self._detector_file.closed:
                return
            self._detector.writerow([self.seconds(at), app_state, detector_state,
                                     "" if game_like is None else int(game_like), override or "", int(auto),
                                     " ".join(f"{score:.2f}" for score in scores)])
            self._detector_rows += 1
            if self._detector_rows % 20 == 0:
                self._detector_file.flush()

    def save_graphics(self, at: float, picture: np.ndarray) -> None:
        folder = self.dir / "graphics"
        folder.mkdir(exist_ok=True)
        ok, encoded = cv2.imencode(".png", picture)
        if ok:
            (folder / f"{self.seconds(at):08.1f}.png").write_bytes(encoded.tobytes())

    def close(self) -> None:
        self.log("session_end", frames=self.frame_count)
        with self._lock:
            self._timeline.close()
            self._frames_file.close()
            self._detector_file.close()


class RecordingThread(threading.Thread):
    """Feeds a live source into a SessionWriter.

    Privacy guard: a frame is only saved while the captured window's title still contains
    ``title_contains`` - if that Chrome window is switched to another tab, recording pauses
    (and says so in the timeline) instead of saving whatever is on screen.
    """

    def __init__(self, source: FrameSource, writer: SessionWriter, get_state: Callable[[], str],
                 hwnd: int, title_contains: str) -> None:
        super().__init__(name="recorder", daemon=True)
        self.source, self.writer, self.get_state = source, writer, get_state
        self.hwnd, self.needle = hwnd, title_contains.lower()
        self.paused = False

    def run(self) -> None:
        for frame in self.source:
            on_stream = self.needle in win32.window_title(self.hwnd).lower()
            if on_stream == self.paused:  # changed
                self.paused = not on_stream
                self.writer.log("recording", status="paused: window is not showing the stream" if self.paused else "resumed")
            if on_stream:
                self.writer.add_frame(frame.image, frame.t, self.get_state())
