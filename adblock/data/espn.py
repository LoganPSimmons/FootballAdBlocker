"""ESPN's unofficial college-football JSON API: the live score (Phase 1) and the game stats (Phase 2).

Host matters: site.web.api.espn.com accepts any client; site.api.espn.com answers 403 to
browser-like User-Agents (verified 2026-09-19). The API is undocumented and can change, so
everything ESPN-shaped stays inside this module; callers only see GameSnapshot / GameStats.

Endpoints used:
  /scoreboard?groups=80&limit=300   every FBS game today (~1.2 MB)  -> find the game once
  /scoreboard/<event id>            one game (~10 KB)               -> poll this for the score
  /summary?event=<event id>         win probability per play, box score, leaders, every play
                                    with a wall-clock time (~44 KB gzipped) -> poll for the stats

Spoiler guard: the stream runs 30-60 s behind ESPN's feed. Scores are served as snapshots
at least ``delay_s`` old; stats the same, and their plays and win-probability points are
additionally cut at each play's wall clock, so a touchdown shows up on the stats screen only
once it is older than the delay.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime

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


@dataclass(frozen=True)
class Play:
    index: int  # order in the game
    play_id: str
    period: int
    clock: str  # "10:17"
    text: str
    away_score: int
    home_score: int
    wall: float  # epoch seconds when the play was entered; 0.0 if ESPN gave none
    scoring: bool
    team: str  # abbreviation of the team with the ball, "" if unknown


@dataclass(frozen=True)
class WinProbPoint:
    index: int  # the play's index
    home_pct: float  # 0..1
    wall: float


@dataclass(frozen=True)
class PlayerGroup:
    label: str  # "Passing"
    columns: list[str]  # ["C/ATT", "YDS", ...]
    rows: list[tuple[str, list[str]]]  # (athlete, values)


@dataclass
class GameStats:
    event_id: str
    fetched_at: float
    status_detail: str
    away: TeamScore
    home: TeamScore
    colors: dict[str, str] = field(default_factory=dict)  # abbreviation -> "#rrggbb"
    team_stats: list[tuple[str, str, str]] = field(default_factory=list)  # (label, away value, home value)
    leaders: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # abbr -> (category, athlete, line)
    players: dict[str, list[PlayerGroup]] = field(default_factory=dict)  # abbr -> stat groups
    plays: list[Play] = field(default_factory=list)  # every play, in order
    win_prob: list[WinProbPoint] = field(default_factory=list)  # one per play that has one
    scoring: list[Play] = field(default_factory=list)  # ESPN's own scoring list: the same plays with clean text

    def delayed(self, cutoff: float) -> "GameStats":
        """The same stats with every play (and win-probability point) after ``cutoff`` removed."""
        plays = [p for p in self.plays if p.wall <= cutoff or p.wall == 0.0]
        kept = {p.index for p in plays}
        scoring = [p for p in self.scoring if p.wall <= cutoff or p.wall == 0.0]
        return replace(self, plays=plays, win_prob=[w for w in self.win_prob if w.index in kept], scoring=scoring)

    @property
    def scoring_plays(self) -> list[Play]:
        return self.scoring or [p for p in self.plays if p.scoring]

    def score_line(self) -> str:
        """The score as of the last play served (so it never runs ahead of the plays shown)."""
        away, home = (self.plays[-1].away_score, self.plays[-1].home_score) if self.plays else ("-", "-")

        def side(team: TeamScore, score) -> str:
            return f"{'#' + str(team.rank) + ' ' if team.rank else ''}{team.abbreviation} {score}"

        return f"{side(self.away, away)}   @   {side(self.home, home)}"

    def win_pct(self, abbreviation: str) -> float | None:
        """Latest win probability of one team, 0..1."""
        if not self.win_prob:
            return None
        home = self.win_prob[-1].home_pct
        return home if abbreviation == self.home.abbreviation else 1.0 - home


def _wall(text: str | None) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _stats(data: dict, fetched_at: float) -> GameStats:
    header = data.get("header", {}).get("competitions", [{}])[0]
    competitors = header.get("competitors", [])
    teams = [_team(c) for c in competitors]
    home = next((t for t in teams if t.home), teams[0])
    away = next((t for t in teams if not t.home), teams[-1])
    colors = {c["team"].get("abbreviation", "?"): "#" + c["team"]["color"] for c in competitors if c.get("team", {}).get("color")}
    status = header.get("status", {}).get("type", {})

    plays: list[Play] = []
    drives = data.get("drives", {})
    for drive in list(drives.get("previous", [])) + ([drives["current"]] if drives.get("current") else []):
        team = (drive.get("team") or {}).get("abbreviation", "")
        for raw in drive.get("plays", []):
            plays.append(Play(len(plays), str(raw.get("id", "")), int((raw.get("period") or {}).get("number") or 0),
                              (raw.get("clock") or {}).get("displayValue", ""), raw.get("text", ""),
                              int(raw.get("awayScore") or 0), int(raw.get("homeScore") or 0), _wall(raw.get("wallclock")),
                              bool(raw.get("scoringPlay")), team))
    by_id = {p.play_id: p for p in plays}
    scoring = []
    for raw in data.get("scoringPlays", []):
        match = by_id.get(str(raw.get("id", "")))
        scoring.append(Play(match.index if match else -1, str(raw.get("id", "")), int((raw.get("period") or {}).get("number") or 0),
                            (raw.get("clock") or {}).get("displayValue", ""), raw.get("text", ""),
                            int(raw.get("awayScore") or 0), int(raw.get("homeScore") or 0), match.wall if match else 0.0,
                            True, (raw.get("team") or {}).get("abbreviation", "")))
    win_prob = []
    for raw in data.get("winprobability", []):
        play = by_id.get(str(raw.get("playId", "")))
        if play is not None and raw.get("homeWinPercentage") is not None:
            win_prob.append(WinProbPoint(play.index, float(raw["homeWinPercentage"]), play.wall))

    box = data.get("boxscore", {})
    columns = {t.get("homeAway"): {s.get("name"): (s.get("label") or s.get("name"), s.get("displayValue", ""))
                                   for s in t.get("statistics", [])} for t in box.get("teams", [])}
    team_stats = []
    for name, (label, away_value) in columns.get("away", {}).items():
        team_stats.append((label, away_value, columns.get("home", {}).get(name, ("", ""))[1]))

    players: dict[str, list[PlayerGroup]] = {}
    for entry in box.get("players", []):
        abbr = (entry.get("team") or {}).get("abbreviation", "?")
        groups = []
        for group in entry.get("statistics", []):
            rows = [((a.get("athlete") or {}).get("displayName", "?"), list(a.get("stats", []))) for a in group.get("athletes", [])]
            if rows:
                groups.append(PlayerGroup((group.get("text") or group.get("name", "")).title(), list(group.get("labels", [])), rows))
        players[abbr] = groups

    leaders: dict[str, list[tuple[str, str, str]]] = {}
    for entry in data.get("leaders", []):
        abbr = (entry.get("team") or {}).get("abbreviation", "?")
        leaders[abbr] = [(cat.get("displayName") or cat.get("name", ""), (top.get("athlete") or {}).get("displayName", "?"),
                          top.get("displayValue", ""))
                         for cat in entry.get("leaders", []) for top in cat.get("leaders", [])[:1]]

    return GameStats(str(data.get("header", {}).get("id", "")), fetched_at, status.get("shortDetail") or status.get("detail", ""),
                     away, home, colors, team_stats, leaders, players, plays, win_prob, scoring)


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

    def stats(self, event_id: str) -> GameStats:
        """Win probability, box score, leaders and every play of one game."""
        return _stats(self._get("/summary", event=event_id), time.time())

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

    def __init__(self, query: str, delay_s: float = 45.0, interval_s: float = 20.0, event_id: str = "") -> None:
        super().__init__(name="espn", daemon=True)
        self.query, self.delay_s, self.interval_s = query, delay_s, interval_s
        self.event_id = event_id  # known already (picked from the list in the UI): skip the lookup
        self.status = "" if event_id else f"looking up '{query}' on ESPN ..."
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
        event_id = self.event_id
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
                            self.event_id = event_id  # so the UI's picker can show what was found
                    if event_id:
                        self.accept(client.game(event_id))
                        self.status = ""
                except (httpx.HTTPError, KeyError, ValueError) as exc:  # offline, or ESPN changed shape
                    self.status = f"ESPN unavailable ({type(exc).__name__})"
                self._halt.wait(wait)
        finally:
            client.close()


class StatsPoller(threading.Thread):
    """Polls one game's stats (the summary endpoint) and serves them *delayed*, like ScorePoller.

    The game is whatever ``scores`` has resolved (it looks the event up from a team name), so
    this thread just waits for its event id. ``current()`` returns the newest stats fetched at
    least ``delay_s`` ago, with the plays and win-probability points cut at ``now - delay_s``
    by their wall clock.
    """

    def __init__(self, scores: "ScorePoller", delay_s: float = 45.0, interval_s: float = 30.0) -> None:
        super().__init__(name="espn-stats", daemon=True)
        self.scores, self.delay_s, self.interval_s = scores, delay_s, interval_s
        self.status = ""
        self._history: deque[GameStats] = deque(maxlen=16)
        self._lock = threading.Lock()
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def current(self) -> GameStats | None:
        cutoff = time.time() - self.delay_s
        with self._lock:
            history = list(self._history)
        newest = next((s for s in reversed(history) if s.fetched_at <= cutoff), None)
        return newest.delayed(cutoff) if newest else None

    def run(self) -> None:
        client = EspnClient()
        try:
            while not self._halt.is_set():
                wait = self.interval_s
                event_id = self.scores.event_id
                if not event_id:
                    wait = 5.0  # the score poller is still looking the game up
                else:
                    try:
                        stats = client.stats(event_id)
                        with self._lock:
                            self._history.append(stats)
                        self.status = ""
                    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                        self.status = f"ESPN stats unavailable ({type(exc).__name__})"
                self._halt.wait(wait)
        finally:
            client.close()
