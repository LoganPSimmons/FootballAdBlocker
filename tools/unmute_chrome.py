"""Unmute Chrome in the Windows volume mixer.

Windows remembers a per-app mute across restarts. The app unmutes on every normal exit and at
start-up, but if it is ever killed mid-break (Task Manager, power loss) this puts Chrome back.

Usage:  python tools\\unmute_chrome.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adblock.actuators import audio_mute  # noqa: E402


def main() -> int:
    before = audio_mute.is_muted()
    if before is None:
        print("chrome.exe has no audio session right now (nothing is playing) - nothing to unmute.")
        return 0
    count = audio_mute.set_muted(False)
    print(f"chrome.exe: {count} audio session(s), was {'muted' if before else 'already unmuted'} -> unmuted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
