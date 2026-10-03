"""Where things live: the code (ROOT) and the writable data (DATA).

From a clone both are the project folder, as they always were. An installed copy (installer/)
sits under %LOCALAPPDATA%\\Programs and must not fill up with recordings, so it carries a marker
file named INSTALLED next to this package, and everything the app writes - config.toml,
sessions/, learned/, logs/, ui_state.json - goes to %LOCALAPPDATA%\\FootballAdBlocker instead.
The environment variable FOOTBALL_AD_BLOCKER_DATA overrides either.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # adblock/, assets/, tools/ live here
MARKER = "INSTALLED"
ENV = "FOOTBALL_AD_BLOCKER_DATA"
INSTALLED_DATA_NAME = "FootballAdBlocker"


def data_dir(root: Path = ROOT, environ: dict | None = None) -> Path:
    """The folder for everything the app writes."""
    env = os.environ if environ is None else environ
    override = env.get(ENV)
    if override:
        return Path(override).expanduser()
    if (root / MARKER).exists():
        local = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / INSTALLED_DATA_NAME
    return root


DATA = data_dir()
