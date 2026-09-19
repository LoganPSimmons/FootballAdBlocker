"""The whole detector on a synthetic broadcast: moving texture, plus graphics that come and go."""

import unittest

import cv2
import numpy as np

from adblock.detect.detector import AD, GAME, Detector

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
    def __init__(self):
        self.detector = Detector()
        self.t = 0.0
        self.log: list[tuple[float, str]] = []

    def play(self, seconds, *graphics, **kwargs):
        for _ in range(int(seconds * FPS)):
            frame = texture()
            for draw in graphics:
                draw(frame, **kwargs) if draw is scorebug else draw(frame)
            seen = self.detector.process(frame, self.t, fullscreen=True)
            self.log.append((self.t, seen.state))
            self.t += 1 / FPS
        return seen

    def states_between(self, start, end):
        return {state for t, state in self.log if start <= t < end}


class SyntheticBroadcast(unittest.TestCase):
    def test_learns_graphics_hides_ads_never_hides_play(self):
        tv = Broadcast()
        seen = tv.play(150, scorebug, corner_bug)  # 2.5 min of play
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
        tv.play(120, scorebug, corner_bug)
        tv.play(30)  # a break
        self.assertEqual(tv.detector.state, AD)

        # Second half: the network moved everything. The detector can't know - it stays in AD...
        tv.play(10, scorebug, layout="second half")
        self.assertEqual(tv.detector.state, AD)
        tv.detector.correct(GAME)  # ...until the viewer says "that's the game"
        held_from = tv.t
        seen = tv.play(45, scorebug, layout="second half")
        self.assertEqual(seen.override, GAME, "learned by now, but a viewer-seeded region has to earn trust first")
        seen = tv.play(75, scorebug, layout="second half")
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
        tv.play(120, scorebug, corner_bug)
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

    def test_paused_picture_teaches_nothing(self):
        tv = Broadcast()
        frozen = texture()
        for i in range(200 * FPS):
            seen = tv.detector.process(frozen, i / FPS, fullscreen=True)
        self.assertEqual(seen.scores, [])
        self.assertEqual(seen.state, GAME)


if __name__ == "__main__":
    unittest.main()
