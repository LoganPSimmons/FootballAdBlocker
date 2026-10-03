"""Settings: defaults here, overridden by config.toml (optional), overridden by the command line."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config.toml"


@dataclass
class Config:
    hotkey: str = "F9"  # toggles GAME <-> AD; works while Chrome is fullscreen and focused
    quit_hotkey: str = "ctrl+shift+F9"  # clean exit without leaving fullscreen
    title_contains: str = "YouTube TV|Fubo"  # how the stream's Chrome window is recognised: any of these in its title
    game: str = ""  # team to look up on ESPN for the score, e.g. "utah" or "USU"
    label: str = ""  # session folder label; defaults to the game, then "session"
    stream_delay_s: float = 45.0  # spoiler guard: show ESPN data only once it is this old
    pip: bool = True  # small live thumbnail on the overlay, to see when the game is back
    auto: bool = True  # the detector drives GAME/AD; off = it only watches (shadow mode) and you toggle
    scene: bool = True  # also use the image model (needs requirements-ml.txt; ignored if not installed)
    scene_drives: bool = True  # the image model drives GAME/AD; learned graphics only block hiding and speed returns.
    #                            False: the graphics rules drive and the model vetoes / returns (the 2026-09-19 behaviour)
    graphics: bool = True  # the learned graphics (scorebug, corner logo) guard the model: on screen they block hiding,
    #                        coming back they show the picture at once. False: the model decides alone ("Use learned
    #                        graphics" in the control window). Without the image model they stay in use regardless
    enter_ad_s: float = 5.0  # broadcast graphics missing this long -> AD (higher = safer for live play)
    exit_ad_frames: int = 3  # consecutive frames with graphics back -> GAME (~0.75 s at 4 fps)
    close_to_tray: bool = False  # False: closing the control window quits the app. True: it keeps running in the tray
    record: bool = True
    fps: float = 4.0  # recorded frames per second (2-4). ~40 KB per frame: 4 fps is ~1.7 GB per 3.5 h game
    frame_width: int = 640  # 640x360 when the stream is fullscreen 16:9
    jpeg_quality: int = 75
    sessions_dir: str = "sessions"  # relative to the project root unless absolute

    @property
    def sessions_path(self) -> Path:
        path = Path(self.sessions_dir)
        return path if path.is_absolute() else ROOT / path


def load(path: Path | None = None) -> Config:
    """Config from a TOML file. A missing default file just means defaults; typos are errors."""
    explicit = path is not None
    path = path or DEFAULT_PATH
    if not path.exists():
        if explicit:
            raise FileNotFoundError(path)
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"{path.name}: unknown setting(s) {unknown}; valid settings are {sorted(known)}")
    return Config(**data)
