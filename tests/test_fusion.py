import unittest

from adblock.detect.fusion import AD, GAME, Fusion, FusionSettings

FPS = 4


class Timeline:
    """Feed evidence at 4 fps and remember the states."""

    def __init__(self, **settings):
        self.fusion = Fusion(FusionSettings(**settings))
        self.t = 0.0
        self.states: list[str] = []

    def feed(self, game_like, seconds):
        for _ in range(int(seconds * FPS)):
            self.states.append(self.fusion.update(game_like, self.t))
            self.t += 1 / FPS
        return self.states[-1]


class Automatic(unittest.TestCase):
    def test_unknown_evidence_never_hides_anything(self):
        line = Timeline()
        self.assertEqual(line.feed(None, 600), GAME)
        self.assertEqual(set(line.states), {GAME})

    def test_enters_ad_only_after_uninterrupted_absence(self):
        line = Timeline(enter_ad_s=5.0)
        line.feed(True, 10)
        self.assertEqual(line.feed(False, 4.5), GAME)
        line.feed(True, 0.25)  # one frame of scorebug resets the clock
        self.assertEqual(line.feed(False, 4.5), GAME)
        self.assertEqual(line.feed(False, 1.0), AD)

    def test_returns_after_three_frames_of_graphics(self):
        line = Timeline(exit_ad_frames=3)
        line.feed(True, 5)
        line.feed(False, 10)
        self.assertEqual(line.feed(True, 0.5), AD)  # two frames: not yet
        self.assertEqual(line.feed(True, 0.25), GAME)  # third

    def test_losing_the_evidence_during_a_break_shows_the_game(self):
        line = Timeline(unknown_grace_s=3.0)
        line.feed(True, 5)
        line.feed(False, 10)
        self.assertEqual(line.feed(None, 2.5), AD)
        self.assertEqual(line.feed(None, 1.0), GAME)


class Flapping(unittest.TestCase):
    def test_halftime_graphics_flashing_on_and_off_stop_toggling_the_state(self):
        """Live, 2026-09-19 14:32-14:33: five automatic switches in 45 s while the halftime
        show flashed score graphics - audio cutting in and out."""
        line = Timeline()
        line.feed(True, 30)
        for _ in range(8):  # graphic on for 10 s, off for 15 s, again and again
            line.feed(False, 15)
            line.feed(True, 10)
        switches = sum(a != b for a, b in zip(line.states, line.states[1:]))
        self.assertLessEqual(switches, 5, "damped after the first few")
        self.assertEqual(line.states[-1], GAME)
        self.assertEqual(line.feed(False, 25), GAME, "while flapping, 25 s without graphics is not enough")
        self.assertEqual(line.feed(False, 10), AD, "a real break is still hidden, just later")

    def test_normal_breaks_are_not_damped(self):
        line = Timeline()
        for _ in range(3):  # a break every five minutes is not flapping
            line.feed(True, 300)
            self.assertEqual(line.feed(False, 6), AD)
            line.feed(False, 120)


class Corrections(unittest.TestCase):
    def test_early_ad_click_holds_then_hands_back_to_automatic(self):
        line = Timeline()
        line.feed(True, 10)
        line.fusion.correct(AD)  # timeout called, scorebug still up
        self.assertEqual(line.feed(True, 8), AD)
        self.assertEqual(line.fusion.override, AD)
        line.feed(False, 2)  # the break really starts: evidence agrees
        self.assertIsNone(line.fusion.override)
        self.assertEqual(line.feed(False, 60), AD)
        self.assertEqual(line.feed(True, 1), GAME)  # and the return is automatic

    def test_game_click_during_missing_graphics_holds_until_they_return(self):
        line = Timeline()
        line.feed(True, 10)
        self.assertEqual(line.feed(False, 8), AD)  # false alarm: long graphics-free segment
        line.fusion.correct(GAME)
        self.assertEqual(line.feed(False, 120), GAME)  # held for as long as it takes
        self.assertEqual(line.fusion.override, GAME)
        line.feed(True, 8)  # a halftime score graphic flashes up for a few seconds...
        self.assertEqual(line.fusion.override, GAME, "...which must not end the hold")
        self.assertEqual(line.feed(False, 60), GAME)
        line.feed(True, 21)  # the real thing: graphics that stay
        self.assertIsNone(line.fusion.override)
        self.assertEqual(line.feed(False, 4), GAME)  # and the next break is detected normally
        self.assertEqual(line.feed(False, 2), AD)

    def test_forced_ad_survives_unknown_evidence(self):
        line = Timeline()
        line.fusion.correct(AD)
        self.assertEqual(line.feed(None, 30), AD)  # no mask yet: the viewer's word stands

    def test_correcting_back_is_immediate(self):
        line = Timeline()
        line.feed(True, 10)
        line.fusion.correct(AD)
        line.feed(True, 1)
        line.fusion.correct(GAME)  # "oops"
        self.assertEqual(line.feed(True, 1), GAME)
        line.feed(True, 20)
        self.assertIsNone(line.fusion.override)


if __name__ == "__main__":
    unittest.main()
