"""Frame sources: the live Chrome window, and recorded sessions replayed through the same interface.

A source is an iterable of ``Frame``. Live iteration blocks until the next new frame and
ends when the source is stopped or the window goes away; replay iteration yields every
recorded frame in order, as fast as the consumer pulls. Detectors and the evaluator are
written against this one interface so that anything tuned offline runs unchanged live.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import cv2
import numpy as np


UNRELIABLE_MARKER = "UNRELIABLE_LABELS.txt"


@dataclass
class Frame:
    image: np.ndarray  # BGR, uint8
    t: float  # seconds on the session clock (time.perf_counter based when live)
    index: int  # live: capture sequence number; replay: recorded frame index
    label: str = ""  # replay only: the app state recorded with the frame ("GAME" / "AD")


class FrameSource(Protocol):
    def __iter__(self) -> Iterator[Frame]: ...

    def stop(self) -> None: ...


class WgcWindowSource:
    """Windows Graphics Capture of one window, by HWND (the capture path that won Phase 0).

    Captures the window's own surface: other windows on top of it (including our overlay)
    are never in the frame, and it follows the window across moves, resizes and monitors.
    ``min_interval_ms`` asks Windows not to deliver frames more often than that, which is
    what keeps a 2-4 fps consumer cheap. No frames arrive while the window is minimized or
    its content isn't changing.
    """

    def __init__(self, hwnd: int, min_interval_ms: int = 250) -> None:
        from windows_capture import WindowsCapture

        self.hwnd = hwnd
        self._condition = threading.Condition()
        self._latest: Frame | None = None
        self._count = 0
        self._closed = False
        self._capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=hwnd,
            minimum_update_interval=min_interval_ms,
        )
        self._capture.event(self.on_frame_arrived)
        self._capture.event(self.on_closed)
        self._control = self._capture.start_free_threaded()

    # windows-capture dispatches on these exact function names.
    def on_frame_arrived(self, frame, _control) -> None:
        image = frame.frame_buffer[:, :, :3].copy()  # the buffer belongs to the capture thread
        with self._condition:
            self._count += 1
            self._latest = Frame(image, time.perf_counter(), self._count)
            self._condition.notify_all()

    def on_closed(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        """True once the window is gone or stop() was called."""
        return self._closed

    def latest(self) -> Frame | None:
        """Most recent frame without waiting (for the overlay's live thumbnail)."""
        with self._condition:
            return self._latest

    def __iter__(self) -> Iterator[Frame]:
        seen = 0
        while True:
            with self._condition:
                while not self._closed and (self._latest is None or self._latest.index == seen):
                    self._condition.wait(timeout=0.5)
                if self._closed:
                    return
                frame = self._latest
            seen = frame.index
            yield frame

    def stop(self) -> None:
        with self._condition:
            already = self._closed
            self._closed = True
            self._condition.notify_all()
        if not already:
            self._control.stop()


class FileReplaySource:
    """A recorded session (see recorder.py) as a frame source, labels included."""

    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        with open(self.session_dir / "frames.csv", newline="", encoding="utf-8") as handle:
            self.rows = list(csv.DictReader(handle))
        self._stopped = False

    @property
    def metadata(self) -> dict:
        return json.loads((self.session_dir / "session.json").read_text(encoding="utf-8"))

    @property
    def unreliable_labels(self) -> str:
        """Why this session's GAME/AD labels must not be trusted ("" = they are fine).

        A session is marked by dropping an UNRELIABLE_LABELS.txt into its folder: the smoke
        test does it for its scripted key presses; do the same by hand for a game where
        nobody was pressing the hotkey. Evaluation and training must skip marked sessions
        (or honour the usable time range the note describes).
        """
        marker = self.session_dir / UNRELIABLE_MARKER
        return marker.read_text(encoding="utf-8").strip() if marker.exists() else ""

    def events(self) -> list[dict]:
        """The timeline: state changes, hotkey presses, window/capture changes."""
        lines = (self.session_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[Frame]:
        for row in self.rows:
            if self._stopped:
                return
            image = cv2.imread(str(self.session_dir / row["file"]))
            if image is None:  # a frame file lost to a crash mid-write: skip, don't abort the replay
                continue
            yield Frame(image, float(row["t"]), int(row["index"]), row["state"])

    def stop(self) -> None:
        self._stopped = True
