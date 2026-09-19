"""ESPN parsing against the response shape observed on 2026-09-19 (trimmed). No network."""

import time
import unittest

from adblock.data import espn


def event(event_id, away, home, state="in", detail="10:37 - 1st", network="FOX"):
    def competitor(team, side):
        location, name, abbreviation, score, rank = team
        return {"homeAway": side, "score": score, "curatedRank": {"current": rank},
                "team": {"location": location, "name": name, "abbreviation": abbreviation}}

    return {
        "id": event_id, "name": f"{away[0]} {away[1]} at {home[0]} {home[1]}", "shortName": f"{away[2]} @ {home[2]}",
        "status": {"type": {"state": state, "shortDetail": detail}},
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


if __name__ == "__main__":
    unittest.main()
