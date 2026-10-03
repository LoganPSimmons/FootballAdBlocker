"""The whole detector on a synthetic broadcast: moving texture, plus graphics that come and go."""

import unittest

import cv2
import numpy as np

from adblock.detect.detector import AD, GAME, Detector, FusionSettings

FPS = 4
rng = np.random.default_rng(7)


def texture() -> np.ndarray:
    """A busy, ever-changing 640x360 picture (stands in for both play and commercials)."""
    noise = rng.integers(0, 255, (18, 32, 3), dtype=np.uint8)  # ~0.25 edge density, like real footage
    return cv2.resize(noise, (640, 360), interpolation=cv2.INTER_CUBIC)


def scorebug(image, layout="first half"):
    y = 300 if layout == "first half" else 20  # the "second half" graphics sit somewhere else entirely
    cv2.rectangle(image, (180, y), (460, y + 44), (40, 30, 30), -1)
    cv2.rectangle(image, (180, y), (460, y + 44), (240, 240, 240), 2)
    cv2.putText(image, "AWAY 14   HOME 21", (196, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)


def corner_bug(image):
    cv2.rectangle(image, (548, 12), (620, 40), (20, 20, 20), -1)
    cv2.putText(image, "NET", (556, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


class Broadcast:
    def __init__(self, detector: Detector | None = None):
        self.detector = detector or Detector()
        self.t = 0.0
        self.log: list[tuple[float, str]] = []

    def play(self, seconds, *graphics, quick=None, scene=None, **kwargs):
        """``quick`` / ``scene``: the image model's verdicts during these seconds (None: no opinion)."""
        for _ in range(int(seconds * FPS)):
            frame = texture()
            for draw in graphics:
                draw(frame, **kwargs) if draw is scorebug else draw(frame)
            seen = self.detector.process(frame, self.t, fullscreen=True, scene=scene, quick=quick)
            self.log.append((self.t, seen.state))
            self.t += 1 / FPS
        return seen

    def states_between(self, start, end):
        return {state for t, state in self.log if start <= t < end}


def banner(image):
    """A commercial's static bottom banner: logo, phone number, web address."""
    cv2.rectangle(image, (0, 330), (640, 360), (30, 30, 200), -1)
    cv2.putText(image, "RHP  775.322.9434  rhpinc.net", (60, 352), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


class RepeatedAdBanner(unittest.TestCase):
    def test_what_the_model_calls_a_commercial_teaches_no_graphic(self):
        """Live 2026-09-26: a local ad in every break has a static banner; learned as a graphic it
        forced the picture back mid-ad and undid the viewer's own AD."""
        detector = Detector()
        t = 0.0
        for _ in range(int(240 * FPS)):  # four minutes of ads, the banner on screen throughout
            frame = texture()
            banner(frame)
            seen = detector.process(frame, t, fullscreen=True, scene=False, quick=False)
            t += 1 / FPS
        self.assertIsNone(seen.game_like, "nothing learned from commercials: no graphic vouches")

    def test_a_graphic_seen_mostly_during_commercials_stops_vouching(self):
        detector = Detector()
        t = 0.0
        for _ in range(int(120 * FPS)):  # learned as a graphic on the broadcast (the model undecided)
            frame = texture()
            banner(frame)
            seen = detector.process(frame, t, fullscreen=True)
            t += 1 / FPS
        self.assertTrue(seen.game_like)
        for _ in range(int(150 * FPS)):  # then it keeps appearing in commercials
            frame = texture()
            banner(frame)
            seen = detector.process(frame, t, fullscreen=True, scene=False, quick=False)
            t += 1 / FPS
        self.assertIsNot(seen.game_like, True, "an ad banner does not vouch for the game")


class FullscreenPixelSlack(unittest.TestCase):
    def test_a_one_pixel_change_of_the_fullscreen_window_keeps_the_library(self):
        """Live 2026-09-26 (Fubo): the fullscreen window was reported 1920x1079 for a while; the
        detector started a fresh library for the new size and forgot everything mid-break."""
        detector = Detector()
        t = 0.0

        def frame(height, *graphics):
            small = texture()
            for draw in graphics:
                draw(small)
            return cv2.resize(small, (1920, height), interpolation=cv2.INTER_CUBIC)

        def play(seconds, height, *graphics):
            nonlocal t
            for _ in range(int(seconds * FPS)):
                seen = detector.process(frame(height, *graphics), t, fullscreen=True)
                t += 1 / FPS
            return seen

        self.assertTrue(play(150, 1080, scorebug, corner_bug).game_like)
        self.assertTrue(play(2, 1079, scorebug, corner_bug).game_like, "same graphics, same library")
        self.assertEqual(play(8, 1079).state, AD, "and their absence still counts")


class SyntheticBroadcast(unittest.TestCase):
    def test_learns_graphics_hides_ads_never_hides_play(self):
        tv = Broadcast()
        seen = tv.play(240, scorebug, corner_bug)  # 4 min of play
        self.assertGreaterEqual(len(seen.scores), 2, "should have learned both graphics")
        self.assertTrue(seen.game_like)

        tv.play(20, corner_bug)  # a replay: scorebug gone, corner bug stays
        self.assertEqual(tv.states_between(0, tv.t), {GAME}, "a replay is not an ad")

        break_start = tv.t
        tv.play(60)  # commercials: no graphics at all
        self.assertEqual(tv.states_between(break_start, break_start + 4.5), {GAME}, "entry delay")
        self.assertEqual(tv.states_between(break_start + 6, tv.t), {AD})

        back = tv.t
        tv.play(10, corner_bug)  # return bumper: corner bug only
        self.assertEqual(tv.states_between(back + 1, tv.t), {GAME}, "back within a second")

    def test_correction_teaches_new_graphics(self):
        tv = Broadcast()
        tv.play(240, scorebug, corner_bug)
        tv.play(30)  # a break
        self.assertEqual(tv.detector.state, AD)

        # Second half: the network moved everything. The detector can't know - it stays in AD...
        tv.play(10, scorebug, layout="second half")
        self.assertEqual(tv.detector.state, AD)
        tv.detector.correct(GAME)  # ...until the viewer says "that's the game"
        held_from = tv.t
        seen = tv.play(45, scorebug, layout="second half")
        self.assertEqual(seen.override, GAME, "learned by now, but a viewer-seeded region has to earn trust first")
        seen = tv.play(95, scorebug, layout="second half")
        self.assertEqual(tv.states_between(held_from, tv.t), {GAME})
        self.assertIsNone(seen.override, "new graphics learned from the tagged frames and trusted: automatic again")

        break_start = tv.t
        tv.play(15)
        self.assertEqual(tv.detector.state, AD, "and the next break is caught using the new graphics")
        self.assertEqual(tv.states_between(break_start, break_start + 4.5), {GAME})

    def test_scene_content_tagged_as_game_never_vouches_for_an_ad(self):
        """Halftime, 2026-09-19: the viewer said "that's the game" over a studio show without
        any network logo; the learner took the studio set for graphics, and one of those
        regions then matched a busy truck commercial - GAME in the middle of an ad break."""
        tv = Broadcast()
        tv.play(240, scorebug, corner_bug)
        tv.play(20)  # break -> AD
        self.assertEqual(tv.detector.state, AD)

        studio = texture()  # a locked-off studio shot: lots of edges that simply never move

        def studio_show(seconds):
            for _ in range(int(seconds * FPS)):
                frame = studio.copy()
                frame[100:200, 200:440] = texture()[100:200, 200:440]  # the presenters move a bit
                seen = tv.detector.process(frame, tv.t, fullscreen=True)
                tv.log.append((tv.t, seen.state))
                tv.t += 1 / FPS

        tv.detector.correct(GAME)  # "I want to watch this"
        studio_show(90)
        tv.detector.correct(AD)  # commercials start: the viewer says so
        ads_from = tv.t
        tv.play(30)
        for _ in range(10 * FPS):  # ...one of which happens to look like part of that studio
            frame = texture()
            frame[:, :320] = studio[:, :320]
            tv.log.append((tv.t, tv.detector.process(frame, tv.t, fullscreen=True).state))
            tv.t += 1 / FPS
        tv.play(30)
        self.assertEqual(tv.states_between(ads_from + 2, tv.t), {AD}, "nothing learned from the studio may call an ad GAME")

        back = tv.t
        tv.play(5, scorebug, corner_bug)
        self.assertEqual(tv.states_between(back + 1, tv.t), {GAME}, "the real graphics still bring the game back")

    def test_a_graphic_learned_a_moment_ago_is_not_missed_when_it_leaves(self):
        """Live, 2026-09-19 14:37: an instance started during halftime learned the highlights
        package's bug, the package ended 6 s later, and the booth shot that followed was hidden."""
        tv = Broadcast()
        tv.play(100, corner_bug)  # a segment graphic: long enough to be learned...
        self.assertGreaterEqual(len(tv.detector.process(texture(), tv.t, fullscreen=True).scores), 1)
        gone = tv.t
        tv.play(120)  # ...then the segment ends and the broadcast carries on without it
        self.assertEqual(tv.states_between(gone, tv.t), {GAME}, "too new for its absence to mean anything")

    def test_locked_off_shot_teaches_nothing(self):
        tv = Broadcast()
        booth = texture()
        for _ in range(150 * FPS):  # announcers on camera: the background never changes, only they move
            frame = booth.copy()
            frame[60:300, 150:500] = texture()[60:300, 150:500]
            seen = tv.detector.process(frame, tv.t, fullscreen=True)
            tv.t += 1 / FPS
        self.assertEqual(seen.scores, [], "a background that persists within one shot is scenery, not graphics")

    def test_paused_picture_teaches_nothing(self):
        tv = Broadcast()
        frozen = texture()
        for i in range(200 * FPS):
            seen = tv.detector.process(frozen, i / FPS, fullscreen=True)
        self.assertEqual(seen.scores, [])
        self.assertEqual(seen.state, GAME)


class LearnedGraphicsOff(unittest.TestCase):
    """"Use learned graphics" unticked in the control window: the image model decides alone, and the
    graphics - still learned and drawn - can neither block hiding nor bring the picture back."""

    @staticmethod
    def broadcast(use_graphics: bool) -> Broadcast:
        return Broadcast(Detector(FusionSettings(scene_drives=True), use_graphics=use_graphics))

    def test_graphics_on_screen_no_longer_block_the_model(self):
        for use_graphics, expected in ((True, GAME), (False, AD)):
            tv = self.broadcast(use_graphics)
            seen = tv.play(240, scorebug, corner_bug, quick=True)
            self.assertGreaterEqual(len(seen.scores), 2, "learned either way: the window still shows them")
            self.assertEqual(seen.game_like, True if use_graphics else None, "the state machine hears of them only when they are on")
            seen = tv.play(6, scorebug, corner_bug, quick=False)  # the model calls the picture a commercial, graphics still up
            self.assertEqual(seen.state, expected)

    def test_the_model_alone_hides_and_shows(self):
        tv = self.broadcast(False)
        tv.play(240, scorebug, corner_bug, quick=True)
        self.assertEqual(tv.play(5, quick=False).state, AD, "'commercial' for 4 s hides")
        self.assertEqual(tv.play(2, scorebug, corner_bug, quick=False).state, AD, "graphics back against an active 'commercial' do not show")
        self.assertEqual(tv.play(3.5, scorebug, corner_bug, quick=True).state, GAME, "'broadcast' for 3 s does")

    def test_switching_while_running(self):
        tv = self.broadcast(True)
        tv.play(300, scorebug, corner_bug, quick=True)
        self.assertEqual(tv.play(6, scorebug, corner_bug, quick=False).state, GAME, "on: a graphic on screen blocks hiding")
        tv.detector.set_graphics(False)
        self.assertEqual(tv.play(5, scorebug, corner_bug, quick=False).state, AD, "off: the model hides despite it")
        tv.detector.set_graphics(True)
        self.assertEqual(tv.play(1, scorebug, corner_bug, quick=None).state, GAME, "on again: an established graphic shows the picture at once")

    def test_without_a_model_opinion_nothing_is_ever_hidden(self):
        tv = self.broadcast(False)
        tv.play(240, scorebug, corner_bug)
        tv.play(120)  # a break, but the model has no opinion (not installed, loading): "can't tell" never hides
        self.assertEqual({state for _, state in tv.log}, {GAME})


if __name__ == "__main__":
    unittest.main()
