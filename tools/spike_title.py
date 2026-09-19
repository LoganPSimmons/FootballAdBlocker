"""S4 - does the Chrome window title on YouTube TV name the game?

Logs every chrome.exe window title once a second (changes only), then checks the stream
window's titles against today's ESPN slate: if both teams of one game are named in a title,
game selection could be automatic.

PASS = a title names a game.  FAIL = it doesn't (not a blocker: pick from a list instead).

Usage:  python tools\\spike_title.py [--seconds 20]
"""

from __future__ import annotations

import argparse
import time

import _spike
from _spike import OUT, verdict, win32

from adblock.data.espn import EspnClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="YouTube TV")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    win32.set_dpi_awareness()
    seen: dict[int, str] = {}
    stream_titles: list[str] = []
    lines: list[str] = []
    t0 = time.perf_counter()
    while (elapsed := time.perf_counter() - t0) < args.seconds:
        for info in win32.find_chrome_windows():
            if seen.get(info.hwnd) != info.title:
                seen[info.hwnd] = info.title
                line = f"[{elapsed:5.1f}s] hwnd={info.hwnd:#x} fullscreen={win32.is_fullscreen(info.hwnd)} {info.title!r}"
                print(line, flush=True)
                lines.append(line)
                if args.title.lower() in info.title.lower():
                    stream_titles.append(info.title)
        time.sleep(1.0)
    (OUT / "s4_titles.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not stream_titles:
        print(f"FAIL: no chrome.exe window title contained '{args.title}'.")
        return 2

    games = EspnClient().scoreboard()
    named = None
    for title in stream_titles:
        lowered = title.lower()
        for game in games:
            if game.away.location.lower() in lowered and game.home.location.lower() in lowered:
                named = (title, game)
    live = [g for g in games if g.state == "in"]
    print(f"\nESPN: {len(games)} FBS games today, {len(live)} in progress.")
    if named:
        print(f"Title {named[0]!r} names {named[1].name} (event {named[1].event_id}).")
    else:
        print(f"No stream-window title names both teams of any game. Titles seen: {sorted(set(stream_titles))}")
    _spike.write_json("s4_title.json", {
        "stream_titles": sorted(set(stream_titles)),
        "title_names_game": bool(named),
        "matched_event": named[1].event_id if named else None,
        "games_in_progress": [f"{g.short_name} ({g.network})" for g in live],
    })
    print(f"\nS4 {verdict(bool(named))}: window title {'names' if named else 'does NOT name'} the game.")
    return 0 if named else 1


if __name__ == "__main__":
    raise SystemExit(main())
