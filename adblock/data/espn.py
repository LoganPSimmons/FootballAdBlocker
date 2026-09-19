"""ESPN's unofficial college-football JSON API - just enough for a live score (Phase 1).

Host matters: site.web.api.espn.com accepts any client; site.api.espn.com answers 403 to
browser-like User-Agents (verified 2026-09-19). The API is undocumented and can change, so
everything ESPN-shaped stays inside this module; callers only see GameSnapshot.

Endpoints used:
  /scoreboard?groups=80&limit=300   every FBS game today (~1.2 MB)  -> find the game once
  /scoreboard/<event id>            one game (~10 KB)               -> poll this
"""

from __future__ import annotations

import time
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
    away: TeamScore
    home: TeamScore
    network: str  # "FOX", or "" if ESPN doesn't list one
    fetched_at: float  # time.time() when ESPN answered

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
    status = (event.get("status") or competition.get("status") or {}).get("type", {})
    networks = [name for b in competition.get("broadcasts") or [] for name in b.get("names") or []]
    return GameSnapshot(
        event_id=str(event["id"]),
        name=event.get("name", ""),
        short_name=event.get("shortName", ""),
        state=status.get("state", ""),
        status_detail=status.get("shortDetail") or status.get("detail", ""),
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
