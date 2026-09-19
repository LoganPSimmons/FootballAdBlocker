"""ESPN's unofficial college-football JSON API - just enough for a live score (Phase 1).

Host matters: site.web.api.espn.com accepts any client; site.api.espn.com answers 403 to
browser-like User-Agents (verified 2026-09-19). The API is undocumented and can change, so
everything ESPN-shaped stays inside this module; callers only see GameSnapshot.

Endpoints used:
  /scoreboard?groups=80&limit=300   every FBS game today (~1.2 MB)  -> find the game once
  /scoreboard/<event id>            one game (~10 KB)               -> poll this
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import httpx

BASE_URL = "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football"
FBS_GROUP = "80"


@dataclass(frozen=True)
class TeamScore:
    abbreviation: str
    location: str  # "Utah State"
    name: str  # "Aggies"
    score: str  # ESPN sends a string; "" before kickoff
    home: bool
    rank: int | None  # AP/CFP rank 1-25, else None

    def matches(self, query: str) -> bool:
        query = query.lower()
        return query == self.abbreviation.lower() or query in f"{self.location} {self.name}".lower()


@dataclass(frozen=True)
class GameSnapshot:
    event_id: str
    name: str  # "Utah State Aggies at Utah Utes"
    short_name: str  # "USU @ UTAH"
    state: str  # "pre" | "in" | "post"
    status_detail: str  # "10:37 - 1st", "Halftime", "Final"
    period: int  # 1-4, 5+ in overtime, 0 before kickoff
    clock: float  # seconds left in the period
    away: TeamScore
    home: TeamScore
    network: str  # "FOX", or "" if ESPN doesn't list one
    fetched_at: float  # time.time() when ESPN answered

    @property
    def progress(self) -> tuple[int, int, float]:
        """Orders snapshots of one game by how far the game has got (bigger = later)."""
        return ({"pre": 0, "in": 1, "post": 2}.get(self.state, 1), self.period, -self.clock)

    def score_line(self) -> str:
        def side(team: TeamScore) -> str:
            rank = f"#{team.rank} " if team.rank else ""
            return f"{rank}{team.abbreviation} {team.score or '-'}"

        return f"{side(self.away)}   @   {side(self.home)}"


def _team(competitor: dict) -> TeamScore:
    team = competitor["team"]
    rank = (competitor.get("curatedRank") or {}).get("current") or competitor.get("rank")
    return TeamScore(
        abbreviation=team.get("abbreviation", "?"),
        location=team.get("location", ""),
        name=team.get("name", ""),
        score=str(competitor.get("score") or ""),
        home=competitor.get("homeAway") == "home",
        rank=rank if isinstance(rank, int) and 1 <= rank <= 25 else None,  # ESPN uses 99 for "unranked"
    )


def _snapshot(event: dict, fetched_at: float) -> GameSnapshot:
    competition = event["competitions"][0]
    teams = [_team(c) for c in competition["competitors"]]
    home = next((t for t in teams if t.home), teams[0])
    away = next((t for t in teams if not t.home), teams[-1])
    full_status = event.get("status") or competition.get("status") or {}
    status = full_status.get("type", {})
    networks = [name for b in competition.get("broadcasts") or [] for name in b.get("names") or []]
    return GameSnapshot(
        event_id=str(event["id"]),
        name=event.get("name", ""),
        short_name=event.get("shortName", ""),
        state=status.get("state", ""),
        status_detail=status.get("shortDetail") or status.get("detail", ""),
        period=int(full_status.get("period") or 0),
        clock=float(full_status.get("clock") or 0.0),
        away=away,
        home=home,
        network=networks[0] if networks else "",
        fetched_at=fetched_at,
    )


class EspnClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, headers={"User-Agent": "FootballAdBlocker/0.1 (personal use)"})

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: str) -> dict:
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def scoreboard(self) -> list[GameSnapshot]:
        """Every FBS game on today's slate."""
        data = self._get("/scoreboard", groups=FBS_GROUP, limit="300")
        now = time.time()
        return [_snapshot(event, now) for event in data.get("events", [])]

    def game(self, event_id: str) -> GameSnapshot:
        return _snapshot(self._get(f"/scoreboard/{event_id}"), time.time())

    def find_game(self, query: str) -> GameSnapshot | None:
        """First game where a team matches ``query`` (abbreviation, or part of "Utah State Aggies").

        Exact abbreviation / location matches win over substring matches, so "utah" finds
        Utah's game rather than Utah State's when both are playing (in different games).
        """
        games = self.scoreboard()
        wanted = query.lower()
        for game in games:
            if any(wanted in (t.abbreviation.lower(), t.location.lower()) for t in (game.away, game.home)):
                return game
        for game in games:
            if game.away.matches(query) or game.home.matches(query):
                return game
        return None


class ScorePoller(threading.Thread):
    """Polls one game's score in the background and serves it *delayed* (spoiler guard).

    YouTube TV runs roughly 30-60 s behind the stadium while ESPN's feed is close to live,
    so the newest snapshot could show a touchdown the viewer hasn't seen yet. ``current()``
    therefore returns the newest snapshot that is at least ``delay_s`` old - which means
    nothing at all for the first ``delay_s`` after start-up. Phase 2 replaces this with
    per-play ``wallclock`` filtering.
    """

    def __init__(self, query: str, delay_s: float = 45.0, interval_s: float = 20.0) -> None:
        super().__init__(name="espn", daemon=True)
        self.query, self.delay_s, self.interval_s = query, delay_s, interval_s
        self.status = f"looking up '{query}' on ESPN ..."
        self.game_name = ""
        self._history: deque[GameSnapshot] = deque(maxlen=64)  # 64 * 20 s: far longer than any delay
        self._lock = threading.Lock()
        self._halt = threading.Event()  # not "_stop": threading.Thread uses that name internally
        self._stale_in_a_row = 0

    def stop(self) -> None:
        self._halt.set()

    def current(self) -> GameSnapshot | None:
        cutoff = time.time() - self.delay_s
        with self._lock:
            history = list(self._history)
        return next((s for s in reversed(history) if s.fetched_at <= cutoff), None)

    def accept(self, snapshot: GameSnapshot) -> bool:
        """Add a snapshot unless it would move the game backwards.

        ESPN's CDN sometimes answers a later request with an older cached state (seen
        2026-09-19: "End of 1st" followed by "0:04 - 1st" 40 s later). A few of those in a
        row are dropped; a long run means the *earlier* data was the glitch, so give in.
        """
        with self._lock:
            if self._history and snapshot.progress < self._history[-1].progress and self._stale_in_a_row < 6:
                self._stale_in_a_row += 1
                return False
            self._stale_in_a_row = 0
            self._history.append(snapshot)
            return True

    def run(self) -> None:
        client = EspnClient()
        event_id = ""
        try:
            while not self._halt.is_set():
                wait = self.interval_s
                try:
                    if not event_id:
                        game = client.find_game(self.query)
                        if game is None:
                            self.status, wait = f"no game today matches '{self.query}'", 120.0
                        else:
                            event_id, self.game_name = game.event_id, game.name
                    if event_id:
                        self.accept(client.game(event_id))
                        self.status = ""
                except (httpx.HTTPError, KeyError, ValueError) as exc:  # offline, or ESPN changed shape
                    self.status = f"ESPN unavailable ({type(exc).__name__})"
                self._halt.wait(wait)
        finally:
            client.close()
