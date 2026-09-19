"""Would a whole-image classifier help? Measured on the recorded sessions (PLAN.md Phase 5 groundwork).

Embeds one frame per second with CLIP (open_clip, on the GPU) and asks three questions:

  1. zero-shot   "football broadcast" vs "commercial" from text prompts alone - no training at all
  2. probe       a linear classifier on the embeddings, trained on some sessions, tested on others
  3. halftime    the case the graphics detector cannot solve: studio show vs commercials, scored
                 against the viewer's (back-dated) corrections

Reference labels for 1 and 2 avoid the weak recorded state and use what is certain instead:
  broadcast  a learned graphic is on screen (the graphics detector's evidence, replayed)
  ad         the replayed detector has been in AD for >= 20 s and stays there >= 10 s more
Frames that are neither (no graphic, not deep inside a break) are left out - and listed, because
they are exactly where a classifier would have to earn its keep.

Needs requirements-ml.txt. Embeddings are cached in learned/embeddings/.

Usage:  python tools\\explore_classifier.py [session ...] [--test SESSION] [--sheet]
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
from adblock.detect.detector import Detector  # noqa: E402
from adblock.labels import effective_labels  # noqa: E402

CACHE = ROOT / "learned" / "embeddings"
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


def embed_session(session: Path, model, preprocess, device) -> dict:
    """One row per second of the session: CLIP embedding + the graphics detector's view of that frame."""
    import torch
    from PIL import Image

    cache = CACHE / f"{session.name}.npz"
    if cache.exists():
        return dict(np.load(cache, allow_pickle=False))
    source = FileReplaySource(session)
    windows = sorted((e["t"], e["fullscreen"]) for e in source.events() if e["event"] == "window")
    detector, rows, batch, embeddings = Detector(), [], [], []
    last_embedded = -1e9

    def flush():
        if batch:
            with torch.no_grad(), torch.autocast(device, dtype=torch.float16):
                features = model.encode_image(torch.stack(batch).to(device))
            embeddings.append(torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy())
            batch.clear()

    for frame in source:
        fullscreen = next((v for t, v in reversed(windows) if t <= frame.t), True)
        seen = detector.process(frame.image, frame.t, fullscreen)
        if frame.t - last_embedded >= 1.0:
            last_embedded = frame.t
            rows.append((frame.t, frame.index, frame.label == "AD", -1 if seen.game_like is None else int(seen.game_like), seen.state == "AD"))
            batch.append(preprocess(Image.fromarray(cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB))))
            if len(batch) == 64:
                flush()
    flush()
    table = np.array(rows, dtype=np.float64)
    data = {"t": table[:, 0], "index": table[:, 1].astype(np.int32), "recorded_ad": table[:, 2].astype(bool),
            "game_like": table[:, 3].astype(np.int8), "detector_ad": table[:, 4].astype(bool),
            "embedding": np.concatenate(embeddings).astype(np.float16)}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **data)
    return data


def reference(data: dict) -> np.ndarray:
    """+1 broadcast, -1 ad, 0 unsure (see module docstring)."""
    t, labels = data["t"], np.zeros(len(data["t"]), np.int8)
    labels[data["game_like"] == 1] = 1
    in_ad = data["detector_ad"]
    for i in np.flatnonzero(in_ad):
        since = i
        while since > 0 and in_ad[since - 1]:
            since -= 1
        until = i
        while until + 1 < len(in_ad) and in_ad[until + 1]:
            until += 1
        if t[i] - t[since] >= 20 and t[until] - t[i] >= 10 and data["game_like"][i] != 1:
            labels[i] = -1
    return labels


def smooth(values: np.ndarray, seconds: int) -> np.ndarray:
    """Running median over +-seconds/2 (rows are one second apart)."""
    half = seconds // 2
    padded = np.pad(values, half, mode="edge")
    return np.array([np.median(padded[i : i + 2 * half + 1]) for i in range(len(values))])


def report(name: str, score: np.ndarray, truth: np.ndarray) -> None:
    """``score`` > 0 means broadcast."""
    known = truth != 0
    for label, s in (("per frame", score), ("5 s median", smooth(score, 5)), ("9 s median", smooth(score, 9))):
        right = (np.sign(s[known]) == truth[known])
        broadcast, ad = truth[known] == 1, truth[known] == -1
        print(f"    {name:<22} {label:<11} accuracy {100 * right.mean():5.1f}%   "
              f"broadcast called ad {100 * (~right[broadcast]).mean():5.2f}% of {broadcast.sum()}   "
              f"ad called broadcast {100 * (~right[ad]).mean():5.2f}% of {ad.sum()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--test", help="session name to hold out for the probe (default: the last one)")
    parser.add_argument("--sheet", action="store_true", help="write the most confident mistakes to spike_out/")
    args = parser.parse_args()

    import open_clip
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"torch {torch.__version__}, device: {device}" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=WEIGHTS)
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(MODEL)

    sessions = args.sessions or sorted(p for p in (ROOT / "sessions").glob("*") if (p / "frames.csv").exists() and "smoke" not in p.name)
    data = {}
    for session in sessions:
        started = time.perf_counter()
        data[session.name] = embed_session(session, model, preprocess, device)
        n = len(data[session.name]["t"])
        print(f"  {session.name}: {n} frames embedded/loaded in {time.perf_counter() - started:.0f} s")

    # speed of the model itself, for the "can it run live?" question
    dummy = torch.zeros(1, 3, 224, 224, device=device)
    with torch.no_grad(), torch.autocast(device, dtype=torch.float16):
        for _ in range(5):
            model.encode_image(dummy)
        if device == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        for _ in range(50):
            model.encode_image(dummy)
        if device == "cuda":
            torch.cuda.synchronize()
    print(f"  one frame through {MODEL}: {1000 * (time.perf_counter() - started) / 50:.1f} ms"
          + (f", {torch.cuda.max_memory_allocated() / 1e6:.0f} MB of VRAM" if device == "cuda" else ""))

    with torch.no_grad():
        text = model.encode_text(tokenizer(BROADCAST_PROMPTS + AD_PROMPTS).to(device))
        text = torch.nn.functional.normalize(text.float(), dim=-1).cpu().numpy()

    def zero_shot(embedding: np.ndarray) -> np.ndarray:
        logits = 100.0 * embedding.astype(np.float32) @ text.T
        probabilities = np.exp(logits - logits.max(axis=1, keepdims=True))
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        return probabilities[:, : len(BROADCAST_PROMPTS)].sum(axis=1) - 0.5  # > 0 = broadcast

    print("\n1. ZERO-SHOT (text prompts only, no training)")
    for name, d in data.items():
        truth = reference(d)
        print(f"  {name}: {int((truth == 1).sum())} broadcast, {int((truth == -1).sum())} ad, {int((truth == 0).sum())} unsure frames")
        report("zero-shot", zero_shot(d["embedding"]), truth)

    print("\n2. LINEAR PROBE (ridge on CLIP embeddings; train on the other sessions, test on one)")
    test_name = args.test or list(data)[-1]
    train = [d for name, d in data.items() if name != test_name]
    x = np.concatenate([d["embedding"].astype(np.float32)[reference(d) != 0] for d in train])
    y = np.concatenate([reference(d)[reference(d) != 0] for d in train]).astype(np.float32)
    mean = x.mean(axis=0)
    weights = np.linalg.solve((x - mean).T @ (x - mean) + 1.0 * np.eye(x.shape[1]), (x - mean).T @ (y - y.mean()))
    bias = y.mean()
    print(f"  trained on {len(y)} frames ({int((y > 0).sum())} broadcast / {int((y < 0).sum())} ad) from {len(train)} sessions; testing on {test_name}")
    probe = lambda d: (d["embedding"].astype(np.float32) - mean) @ weights + bias  # noqa: E731
    report("probe (held-out)", probe(data[test_name]), reference(data[test_name]))

    print("\n3. WHERE THE GRAPHICS DETECTOR IS BLIND: frames with no graphic on screen, scored against the viewer's corrected labels")
    for name, d in data.items():
        session = ROOT / "sessions" / name
        source = FileReplaySource(session)
        if not any(e["event"] == "correction" for e in source.events()):
            continue
        times = [float(r["t"]) for r in source.rows]
        fixed = effective_labels(times, [r["state"] for r in source.rows], source.events())
        viewer = np.array([1 if fixed[int(i)] == "GAME" else -1 for i in d["index"]])
        blind = d["game_like"] != 1
        if blind.sum() < 30:
            continue
        print(f"  {name}: {int(blind.sum())} blind frames ({int((viewer[blind] == 1).sum())} the viewer wanted shown, {int((viewer[blind] == -1).sum())} hidden)")
        for label, score in (("zero-shot", zero_shot(d["embedding"])), ("probe", probe(d))):
            s = smooth(score, 9)
            shown_right = (s[blind & (viewer == 1)] > 0).mean() if (blind & (viewer == 1)).any() else float("nan")
            hidden_right = (s[blind & (viewer == -1)] <= 0).mean() if (blind & (viewer == -1)).any() else float("nan")
            print(f"    {label:<10} (9 s median): would show {100 * shown_right:5.1f}% of what he wanted shown, hide {100 * hidden_right:5.1f}% of what he wanted hidden")

    if args.sheet:
        d, name = data[test_name], test_name
        truth, score = reference(d), probe(d)
        wrong = np.flatnonzero((truth != 0) & (np.sign(score) != truth))
        worst = wrong[np.argsort(-np.abs(score[wrong]))][:24]
        source = FileReplaySource(ROOT / "sessions" / name)
        tiles = []
        for i in worst:
            tile = cv2.resize(cv2.imread(str(ROOT / "sessions" / name / source.rows[int(d["index"][i])]["file"])), (320, 180))
            text_ = f"{'BROADCAST' if truth[i] > 0 else 'AD'} scored {score[i]:+.2f} t={d['t'][i]:.0f}"
            cv2.putText(tile, text_, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
            tiles.append(tile)
        if tiles:
            while len(tiles) % 4:
                tiles.append(np.zeros_like(tiles[0]))
            out = ROOT / "spike_out" / "classifier_mistakes.jpg"
            cv2.imwrite(str(out), np.vstack([np.hstack(tiles[i : i + 4]) for i in range(0, len(tiles), 4)]))
            print(f"\n  wrote {out}: the probe's {len(worst)} most confident mistakes on {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
