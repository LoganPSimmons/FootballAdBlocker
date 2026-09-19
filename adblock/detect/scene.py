"""Whole-image opinion: does this frame look like the broadcast, or like a commercial?

CLIP (open_clip ViT-B/32) used zero-shot: the frame's embedding is compared with a handful
of text prompts, no training. Measured on the 2026-09-19 recordings (PLAN.md): 10.6 ms per
frame on the RTX 3060 Ti, 97-99 % per frame, and - the reason it is here - it separates a
logo-less halftime studio show from its commercials, which the graphics detector cannot.
Its known weakness is ads that look like football.

It is an *optional* part (requirements-ml.txt, ~5 GB). The state machine gives it limited
powers on purpose (fusion.py): it can veto hiding and, slowly, bring the picture back; it can
never hide anything by itself.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("adblock")

MODEL, WEIGHTS = "ViT-B-32", "laion2b_s34b_b79k"
BROADCAST_PROMPTS = [
    "a live American football game on television",
    "a replay of a football play",
    "football players on a field in a stadium",
    "a football coach on the sideline",
    "a crowd of fans in a stadium during a game",
    "a sports studio show with hosts at a desk",
    "sports commentators in a broadcast booth",
]
AD_PROMPTS = [
    "a television commercial",
    "an advertisement for a product",
    "a car commercial",
    "a fast food commercial",
    "an insurance commercial",
    "a trailer for a movie or a TV show",
    "a commercial for a phone or internet company",
]
SMOOTH_S = 9  # verdicts are the median of the last nine one-per-second scores (a few seconds late is fine)
MIN_SCORES = 5
MARGIN = 0.15  # |P(broadcast) - 0.5| below this = no opinion


def available() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def verdict_from(scores) -> bool | None:
    """True: looks like the broadcast. False: looks like a commercial. None: no opinion (yet)."""
    if len(scores) < MIN_SCORES:
        return None
    middle = float(np.median(list(scores)[-SMOOTH_S:]))
    return True if middle > MARGIN else False if middle < -MARGIN else None


class SceneModel:
    def __init__(self) -> None:
        import open_clip
        import torch

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, self.preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=WEIGHTS)
        self.model = model.to(self.device).eval()
        with torch.no_grad():
            text = self.model.encode_text(open_clip.get_tokenizer(MODEL)(BROADCAST_PROMPTS + AD_PROMPTS).to(self.device))
        self.text = torch.nn.functional.normalize(text.float(), dim=-1).cpu().numpy()

    def score_embedding(self, embedding: np.ndarray) -> float:
        """P(broadcast) - 0.5 for one L2-normalised image embedding."""
        logits = 100.0 * embedding.astype(np.float32) @ self.text.T
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        return float(probabilities[: len(BROADCAST_PROMPTS)].sum() - 0.5)

    def score(self, image_bgr: np.ndarray) -> float:
        from PIL import Image

        torch = self.torch
        pixels = self.preprocess(Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(self.device)
        with torch.no_grad(), torch.autocast(self.device, dtype=torch.float16, enabled=self.device == "cuda"):
            features = self.model.encode_image(pixels)
        return self.score_embedding(torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy()[0])


class SceneWatcher(threading.Thread):
    """Scores the newest captured frame about once a second, off the detector's thread."""

    def __init__(self, newest_frame: Callable[[], np.ndarray | None], period_s: float = 1.0) -> None:
        super().__init__(name="scene", daemon=True)
        self.newest_frame, self.period_s = newest_frame, period_s
        self.scores: deque[float] = deque(maxlen=SMOOTH_S)
        self.ready = False
        self._halt = threading.Event()

    def verdict(self) -> bool | None:
        return verdict_from(self.scores)

    def latest(self) -> float | None:
        return self.scores[-1] if self.scores else None

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            model = SceneModel()  # a few seconds: weights from disk to the GPU
        except Exception:
            log.exception("image model could not be loaded; carrying on with graphics only")
            return
        self.ready = True
        log.info(f"Image model ready on {model.device}.")
        while not self._halt.is_set():
            started = time.perf_counter()
            try:
                image = self.newest_frame()
                if image is not None:
                    self.scores.append(model.score(image))
            except Exception:
                log.exception("image model failed on a frame")
            self._halt.wait(max(0.05, self.period_s - (time.perf_counter() - started)))
