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

    def test_a_young_graphic_has_to_stay_to_bring_the_game_back(self):
        """Live, 2026-09-19 14:47: a region learned minutes earlier matched a commercial's
        static end card for about a second - GAME in the middle of the break."""
        line = Timeline(weak_exit_s=4.0)
        line.feed(True, 10)
        line.feed(False, 30)

        def feed_weak(seconds):
            for _ in range(int(seconds * FPS)):
                line.states.append(line.fusion.update(True, line.t, strong=False))
                line.t += 1 / FPS
            return line.states[-1]

        self.assertEqual(feed_weak(2.5), AD, "an end card's worth of a young graphic is not enough")
        line.feed(False, 10)
        self.assertEqual(feed_weak(4.5), GAME, "but one that stays is believed")

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


class ImageModelDrives(unittest.TestCase):
    """The viewer's choice (2026-09-26): the model's quick verdict drives, graphics only guard."""

    def feed(self, line, game_like, quick, seconds, **kwargs):
        for _ in range(int(seconds * FPS)):
            line.states.append(line.fusion.update(game_like, line.t, quick=quick, **kwargs))
            line.t += 1 / FPS
        return line.states[-1]

    def test_commercial_hides_and_broadcast_shows_without_any_graphic(self):
        line = Timeline(scene_drives=True, drive_enter_s=4.0, drive_exit_s=3.0)
        self.assertEqual(self.feed(line, None, True, 30), GAME)
        self.assertEqual(self.feed(line, None, False, 3.5), GAME, "not before 4 s of 'commercial'")
        self.assertEqual(self.feed(line, None, False, 1.0), AD)
        self.assertEqual(self.feed(line, None, True, 2.5), AD, "not before 3 s of 'broadcast'")
        self.assertEqual(self.feed(line, None, True, 1.0), GAME)

    def test_a_graphic_on_screen_blocks_hiding_and_brings_the_picture_back_at_once(self):
        line = Timeline(scene_drives=True)
        self.assertEqual(self.feed(line, True, False, 60), GAME, "the scorebug is up: the model cannot hide")
        self.assertEqual(self.feed(line, False, False, 5), AD)
        self.assertEqual(self.feed(line, True, False, 1.0), GAME, "graphics back: shown although the model still says ad")

    def test_after_a_viewers_ad_the_model_needs_the_long_rule(self):
        line = Timeline(scene_drives=True, drive_exit_s=3.0, scene_exit_s=12.0)
        self.feed(line, True, True, 30)
        line.fusion.correct(AD)
        self.feed(line, False, False, 5)
        self.assertEqual(self.feed(line, False, True, 11), AD, "a football-looking ad the viewer hid stays hidden")
        self.assertEqual(self.feed(line, False, True, 2), GAME)

    def test_without_an_opinion_the_graphics_rules_take_over(self):
        line = Timeline(scene_drives=True)
        self.feed(line, True, None, 30)
        self.assertEqual(self.feed(line, False, None, 5.5), AD, "model silent: graphics absent 5 s hides as before")
        self.assertEqual(self.feed(line, True, None, 1.0), GAME)


class ImageModel(unittest.TestCase):
    """The scene opinion may veto hiding and slowly bring the picture back - nothing else."""

    def feed(self, line, game_like, scene, seconds):
        for _ in range(int(seconds * FPS)):
            line.states.append(line.fusion.update(game_like, line.t, scene=scene))
            line.t += 1 / FPS
        return line.states[-1]

    def test_studio_show_without_graphics_is_not_hidden(self):
        line = Timeline()
        line.feed(True, 30)
        self.assertEqual(self.feed(line, False, True, 120), GAME, "no logo, but it still looks like the broadcast")
        self.assertEqual(self.feed(line, False, False, 1), AD, "commercials: hidden at once, the 5 s were long over")

    def test_with_a_graphic_on_screen_its_opinion_cannot_hide(self):
        line = Timeline()
        self.assertEqual(self.feed(line, True, False, 300), GAME, "graphics on screen: its opinion is irrelevant")

    def test_alone_it_hides_after_a_sustained_commercial_verdict_and_lets_go_when_that_stops(self):
        """Live 2026-09-26 (Fubo, right after a launch): nothing trusted yet, the model said
        "commercial" through a whole break, and the viewer watched it leak - then asked for this."""
        line = Timeline(scene_enter_s=10.0, unknown_grace_s=3.0)
        self.assertEqual(self.feed(line, None, False, 9.5), GAME, "not before 10 s")
        self.assertEqual(self.feed(line, None, False, 1.0), AD)
        self.assertEqual(self.feed(line, None, False, 60), AD, "unknown graphics do not undo it while it says commercial")
        self.assertEqual(self.feed(line, None, None, 2.5), AD)
        self.assertEqual(self.feed(line, None, None, 1.0), GAME, "no opinion for 3 s: can't tell, show the game")
        self.assertEqual(self.feed(line, None, False, 10.5), AD)
        self.assertEqual(self.feed(line, True, False, 1.0), GAME, "a trusted graphic outranks it at once")

    def test_no_picture_at_all_still_shows_the_game_after_the_grace_period(self):
        line = Timeline(unknown_grace_s=3.0)
        line.feed(True, 30)
        line.feed(False, 30)
        self.assertEqual(line.states[-1], AD)
        self.assertEqual(line.feed(None, 3.5), GAME, "the window went away: nothing to cover")

    def test_return_needs_twelve_seconds_so_a_short_stadium_ad_cannot_reopen_a_break(self):
        line = Timeline()
        line.feed(True, 30)
        self.feed(line, False, False, 30)
        self.assertEqual(self.feed(line, False, True, 10), AD, "a 10 s football-looking ad")
        self.feed(line, False, False, 30)
        self.assertEqual(self.feed(line, False, True, 13), GAME, "the studio show is back, and stays")

    def test_a_cut_to_black_then_the_broadcast_brings_the_game_back_quickly(self):
        """Live 2026-09-26 (ABC): breaks end with a splice to black, then a 20 s sponsor bumper
        with no graphic; the viewer had to click GAME twice. After the black frame the
        three-second verdict is trusted after 2 s instead of the usual 20."""
        line = Timeline(boundary_exit_s=2.0, scene_exit_s=20.0)
        line.feed(True, 30)
        line.feed(False, 30)
        self.assertEqual(line.states[-1], AD)
        f = line.fusion
        for _ in range(2):  # the splice
            f.update(False, line.t, scene=False, black=True)
            line.t += 0.25
        states = []
        for _ in range(int(2.5 * FPS)):  # the bumper: quick verdict says broadcast, the 9 s median still says ad
            states.append(f.update(False, line.t, scene=False, quick=True))
            line.t += 0.25
        self.assertEqual(states[:4], [AD] * 4, "not before 2 s of the quick verdict")
        self.assertEqual(states[-1], GAME)
        for _ in range(int(4 * FPS)):  # the bumper goes on, graphics still missing, the slow verdict still "ad"
            state = f.update(False, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, GAME, "a quick return is not undone by the graphics rule on the next frame")
        for _ in range(int(6 * FPS)):  # it was a football-looking ad after all: hidden again after the usual 5 s
            state = f.update(False, line.t, scene=False, quick=False)
            line.t += 0.25
        self.assertEqual(state, AD)

    def test_a_quick_return_is_not_undone_by_the_model_rule_either(self):
        """Replay 2026-09-26: the bumper came back for one frame and was hidden again, because the
        slow verdict had said "commercial" for well over 10 s and the splice window had closed
        with the AD it belonged to."""
        line = Timeline(boundary_exit_s=2.0, scene_enter_s=10.0)
        line.feed(True, 30)
        f = line.fusion
        for _ in range(int(60 * FPS)):  # a break, nothing trusted any more (graphics unknown), model says commercial
            f.update(None, line.t, scene=False)
            line.t += 0.25
        self.assertEqual(f.state, AD)
        f.update(None, line.t, scene=False, black=True)
        line.t += 0.25
        for _ in range(int(3 * FPS)):
            state = f.update(None, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, GAME)
        for _ in range(int(8 * FPS)):  # the slow verdict still says commercial for a while
            state = f.update(None, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, GAME, "the model rule needs a fresh 10 s, and the quick verdict vetoes it meanwhile")

    def test_without_a_cut_to_black_the_quick_verdict_has_to_wait(self):
        line = Timeline(boundary_exit_s=2.0, scene_exit_s=20.0)
        line.feed(True, 30)
        line.feed(False, 30)
        f = line.fusion
        for _ in range(int(15 * FPS)):  # a football-looking ad in the middle of a break: no splice seen
            state = f.update(False, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, AD)

    def test_a_black_frame_before_the_break_began_is_not_a_boundary(self):
        line = Timeline(boundary_exit_s=2.0)
        line.feed(True, 30)
        f = line.fusion
        f.update(False, line.t, black=True)  # the cut to black that *starts* the break
        line.t += 0.25
        line.feed(False, 30)
        self.assertEqual(line.states[-1], AD)
        for _ in range(int(5 * FPS)):
            state = f.update(False, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, AD)

    def test_after_a_viewers_ad_only_a_cut_to_black_lets_the_model_end_the_break(self):
        line = Timeline(boundary_exit_s=2.0, scene_exit_s=20.0)
        line.feed(True, 30)
        line.fusion.correct(AD)
        line.feed(False, 10)
        f = line.fusion
        for _ in range(int(30 * FPS)):  # the slow path stays closed: the viewer said this is an ad
            state = f.update(False, line.t, scene=True, quick=True)
            line.t += 0.25
        self.assertEqual(state, AD)
        f.update(False, line.t, black=True)  # a splice: something new starts
        line.t += 0.25
        for _ in range(int(3 * FPS)):
            state = f.update(False, line.t, scene=False, quick=True)
            line.t += 0.25
        self.assertEqual(state, GAME)

    def test_a_viewers_ad_hold_listens_to_graphics_only(self):
        line = Timeline()
        line.feed(True, 30)
        line.fusion.correct(AD)  # "hide this", over a football-looking ad the model believes in
        self.assertEqual(self.feed(line, False, True, 60), AD)
        self.assertIsNone(line.fusion.override, "graphics were missing: the hold was confirmed at once")
        self.assertEqual(line.feed(True, 1), GAME, "and the real game still comes back within a second")


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
