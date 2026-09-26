"""ESPN parsing against the response shape observed on 2026-09-19 (trimmed). No network."""

import time
import unittest

from adblock.data import espn


def event(event_id, away, home, state="in", detail="10:37 - 1st", network="FOX", period=1, clock=637.0):
    def competitor(team, side):
        location, name, abbreviation, score, rank = team
        return {"homeAway": side, "score": score, "curatedRank": {"current": rank},
                "team": {"location": location, "name": name, "abbreviation": abbreviation}}

    return {
        "id": event_id, "name": f"{away[0]} {away[1]} at {home[0]} {home[1]}", "shortName": f"{away[2]} @ {home[2]}",
        "status": {"period": period, "clock": clock, "type": {"state": state, "shortDetail": detail}},
        "competitions": [{"competitors": [competitor(home, "home"), competitor(away, "away")],
                          "broadcasts": [{"market": "national", "names": [network]}]}],
    }


UTAH_GAME = event("401856801", ("Utah State", "Aggies", "USU", "0", 99), ("Utah", "Utes", "UTAH", "7", 17))
OTHER_GAME = event("401000000", ("Weber State", "Wildcats", "WEB", "3", 99), ("Utah Tech", "Trailblazers", "UTU", "10", 99))


class Parsing(unittest.TestCase):
    def test_snapshot_fields(self):
        game = espn._snapshot(UTAH_GAME, fetched_at=123.0)
        self.assertEqual((game.event_id, game.short_name, game.state, game.status_detail, game.network),
                         ("401856801", "USU @ UTAH", "in", "10:37 - 1st", "FOX"))
        self.assertEqual((game.away.abbreviation, game.away.score, game.away.rank), ("USU", "0", None))  # 99 = unranked
        self.assertEqual((game.home.abbreviation, game.home.score, game.home.rank), ("UTAH", "7", 17))
        self.assertEqual(game.score_line(), "USU 0   @   #17 UTAH 7")

    def test_pregame_has_no_score_yet(self):
        pregame = event("1", ("Army", "Black Knights", "ARMY", None, 99), ("Navy", "Midshipmen", "NAVY", None, 99), state="pre")
        self.assertEqual(espn._snapshot(pregame, 0.0).score_line(), "ARMY -   @   NAVY -")


class FindGame(unittest.TestCase):
    def setUp(self):
        self.client = espn.EspnClient()
        self.addCleanup(self.client.close)
        self.client.scoreboard = lambda: [espn._snapshot(e, 0.0) for e in (OTHER_GAME, UTAH_GAME)]

    def test_exact_team_beats_substring(self):
        # "utah" is a substring of "Utah Tech" (listed first) but is exactly Utah's location.
        self.assertEqual(self.client.find_game("utah").event_id, "401856801")

    def test_abbreviation_and_partial_name(self):
        self.assertEqual(self.client.find_game("usu").event_id, "401856801")
        self.assertEqual(self.client.find_game("trailblaz").event_id, "401000000")
        self.assertIsNone(self.client.find_game("alabama"))


class SpoilerDelay(unittest.TestCase):
    def test_only_snapshots_older_than_the_delay_are_shown(self):
        poller = espn.ScorePoller("utah", delay_s=45.0)
        now = time.time()
        self.assertIsNone(poller.current())
        for age, score in ((100, "0"), (50, "7"), (10, "14")):  # oldest first, as the poller appends
            snapshot = espn._snapshot(event("1", ("A", "a", "AAA", "0", 99), ("B", "b", "BBB", score, 99)), now - age)
            poller._history.append(snapshot)
        self.assertEqual(poller.current().home.score, "7")  # the 14 is only 10 s old: not yet


class NeverBackwards(unittest.TestCase):
    TEAMS = (("A", "a", "AAA", "0", 99), ("B", "b", "BBB", "7", 99))

    def snap(self, period, clock, state="in"):
        return espn._snapshot(event("1", *self.TEAMS, state=state, period=period, clock=clock), time.time())

    def test_progress_orders_a_game(self):
        order = [self.snap(0, 0, "pre"), self.snap(1, 900), self.snap(1, 4), self.snap(1, 0), self.snap(2, 900),
                 self.snap(4, 0), self.snap(5, 0), self.snap(4, 0, "post")]
        self.assertEqual(sorted(order, key=lambda s: s.progress), order)

    def test_stale_cdn_answer_is_dropped(self):
        poller = espn.ScorePoller("a")
        self.assertTrue(poller.accept(self.snap(1, 4)))
        self.assertTrue(poller.accept(self.snap(1, 0)))  # "End of 1st"
        self.assertFalse(poller.accept(self.snap(1, 4)))  # older cache node answers: ignored
        self.assertTrue(poller.accept(self.snap(1, 0)))  # same state again is fine
        self.assertTrue(poller.accept(self.snap(2, 890)))
        self.assertEqual([s.clock for s in poller._history], [4, 0, 0, 890])

    def test_gives_in_when_the_earlier_data_was_the_glitch(self):
        poller = espn.ScorePoller("a")
        poller.accept(self.snap(4, 0, "post"))  # a bogus "Final"
        results = [poller.accept(self.snap(2, 300)) for _ in range(7)]
        self.assertEqual(results, [False] * 6 + [True])


if __name__ == "__main__":
    unittest.main()


def summary(now: float) -> dict:
    """A trimmed /summary answer (shape observed 2026-09-26): two drives, win probability per play, a box score."""
    def play(play_id, period, clock, text, away, home, seconds_ago, scoring=False):
        wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - seconds_ago))
        return {"id": play_id, "period": {"number": period}, "clock": {"displayValue": clock}, "text": text,
                "awayScore": away, "homeScore": home, "wallclock": wall, "scoringPlay": scoring}

    team = lambda abbr, side, color: {"homeAway": side, "score": "0", "team": {"abbreviation": abbr, "location": abbr, "name": "X", "color": color}}
    return {
        "header": {"id": "401", "competitions": [{"competitors": [team("ISU", "home", "ae192d"), team("UTAH", "away", "be0000")],
                                                  "status": {"type": {"shortDetail": "10:17 - 2nd"}}}]},
        "drives": {"previous": [{"team": {"abbreviation": "UTAH"}, "plays": [
                        play("p1", 1, "14:55", "Kickoff", 0, 0, 600),
                        play("p2", 1, "12:10", "W. Parker run for 54 yds, for a TD", 7, 0, 400, scoring=True)]}],
                   "current": {"team": {"abbreviation": "ISU"}, "plays": [play("p3", 2, "10:17", "J. Raynor pass complete", 7, 0, 20)]}},
        "winprobability": [{"playId": "p1", "homeWinPercentage": 0.45}, {"playId": "p2", "homeWinPercentage": 0.2},
                           {"playId": "p3", "homeWinPercentage": 0.18}, {"playId": "unknown", "homeWinPercentage": 0.5}],
        "boxscore": {"teams": [{"homeAway": "away", "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "272"}]},
                               {"homeAway": "home", "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "101"}]}],
                     "players": [{"team": {"abbreviation": "UTAH"}, "statistics": [
                         {"name": "passing", "text": "Utah Passing", "labels": ["C/ATT", "YDS"],
                          "athletes": [{"athlete": {"displayName": "Devon Dampier"}, "stats": ["12/15", "160"]}]}]}]},
        "leaders": [{"team": {"abbreviation": "UTAH"}, "leaders": [{"name": "passingYards", "displayName": "Passing Yards",
                     "leaders": [{"displayValue": "12/15, 160 YDS", "athlete": {"displayName": "Devon Dampier"}}]}]}],
    }


class Stats(unittest.TestCase):
    def test_parsing(self):
        now = time.time()
        stats = espn._stats(summary(now), fetched_at=now)
        self.assertEqual((stats.away.abbreviation, stats.home.abbreviation, stats.status_detail), ("UTAH", "ISU", "10:17 - 2nd"))
        self.assertEqual(stats.colors, {"ISU": "#ae192d", "UTAH": "#be0000"})
        self.assertEqual([p.text for p in stats.plays], ["Kickoff", "W. Parker run for 54 yds, for a TD", "J. Raynor pass complete"])
        self.assertEqual([w.home_pct for w in stats.win_prob], [0.45, 0.2, 0.18], "a point for an unknown play is dropped")
        self.assertEqual(stats.team_stats, [("Total Yards", "272", "101")])
        self.assertEqual(stats.leaders["UTAH"], [("Passing Yards", "Devon Dampier", "12/15, 160 YDS")])
        self.assertEqual(stats.players["UTAH"][0].rows, [("Devon Dampier", ["12/15", "160"])])
        self.assertEqual(stats.score_line(), "UTAH 7   @   ISU 0")
        self.assertAlmostEqual(stats.win_pct("UTAH"), 0.82)

    def test_the_spoiler_guard_cuts_plays_and_odds_at_their_wall_clock(self):
        now = time.time()
        stats = espn._stats(summary(now), fetched_at=now)
        delayed = stats.delayed(now - 45.0)
        self.assertEqual([p.text for p in delayed.plays], ["Kickoff", "W. Parker run for 54 yds, for a TD"], "the 20 s old play is held back")
        self.assertEqual([w.home_pct for w in delayed.win_prob], [0.45, 0.2])
        self.assertEqual([p.text for p in delayed.scoring_plays], ["W. Parker run for 54 yds, for a TD"])
        older = stats.delayed(now - 500.0)
        self.assertEqual(older.score_line(), "UTAH 0   @   ISU 0", "before the touchdown was 45 s old")
        self.assertEqual(stats.team_stats, older.team_stats, "totals are held back as a whole snapshot instead")
