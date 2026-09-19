"""Graphics remembered between sessions: a warm start that can never hide anything by mistake."""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from adblock.detect import memory
from adblock.detect.detector import AD, GAME, Detector
from adblock.detect.scorebug import Region
from tests.test_detector import FPS, Broadcast, corner_bug, scorebug, texture


def region(x0, y0, x1, y1, pixels=900):
    return Region((x0, y0, x1, y1), np.arange(pixels, dtype=np.int32), np.arange(pixels, pixels + 50, dtype=np.int32))


class OnDisk(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learned" / "graphics.npz"
            items = [memory.Remembered((360, 640), region(540, 6, 624, 45), 812.5, "2026-09-19", "FOX  USU @ UTAH")]
            memory.save(path, items)
            back = memory.load(path)
            self.assertEqual(len(back), 1)
            self.assertEqual((back[0].shape, back[0].region.bbox, back[0].seen_s, back[0].label),
                             ((360, 640), (540, 6, 624, 45), 812.5, "FOX  USU @ UTAH"))
            np.testing.assert_array_equal(back[0].region.on, items[0].region.on)

    def test_missing_or_damaged_file_is_just_no_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "graphics.npz"
            self.assertEqual(memory.load(path), [])
            path.write_bytes(b"not an npz")
            self.assertEqual(memory.load(path), [])

    def test_merge_refreshes_overlapping_keeps_others_expires_old(self):
        today = date(2026, 9, 26)
        known = [
            memory.Remembered((360, 640), region(540, 6, 624, 45), 800.0, "2026-09-19", "FOX last week"),
            memory.Remembered((360, 640), region(20, 300, 200, 350), 500.0, "2026-09-12", "ESPN"),
            memory.Remembered((360, 640), region(300, 10, 380, 40), 400.0, (today - timedelta(days=200)).isoformat(), "long gone"),
        ]
        proven = [memory.Remembered((360, 640), region(541, 7, 625, 46), 900.0, today.isoformat(), "FOX today")]
        kept = memory.merge(known, proven, today)
        self.assertEqual([k.label for k in kept], ["FOX today", "ESPN"])
        self.assertEqual(kept[0].seen_s, 1700.0, "same graphic: on-screen time accumulates across sessions")


class WarmStart(unittest.TestCase):
    def first_session(self):
        tv = Broadcast()
        tv.play(330, scorebug, corner_bug)
        proven = tv.detector.proven("synthetic")
        self.assertGreaterEqual(len(proven), 2)
        return proven

    def test_a_broadcast_seen_before_needs_no_warm_up(self):
        tv = Broadcast()
        tv.detector = Detector(remembered=self.first_session())
        seen = tv.play(2, scorebug, corner_bug)
        self.assertTrue(seen.game_like, "recognised within the first frames, nothing to learn first")
        tv.play(75, scorebug, corner_bug)  # on screen for a minute in *this* session: now absence counts
        tv.play(12)
        self.assertEqual(tv.detector.state, AD, "a break 80 s after launch is caught (cold start: not before ~3 min)")
        back = tv.t
        tv.play(6, corner_bug)
        self.assertIn(GAME, tv.states_between(back, tv.t))

    def test_remembered_graphics_of_another_broadcast_never_hide_anything(self):
        tv = Broadcast()
        tv.detector = Detector(remembered=self.first_session())
        tv.play(50)  # a different network: none of the remembered graphics ever shows up
        self.assertEqual(tv.states_between(0, tv.t), {GAME})
        self.assertIsNone(tv.detector.process(texture(), tv.t, fullscreen=True).game_like, "unknown, not 'no game'")

    def test_relearn_also_drops_the_remembered_graphics_for_this_session(self):
        detector = Detector(remembered=self.first_session())
        detector.relearn()
        seen = detector.process(texture(), 0.0, fullscreen=True)
        self.assertEqual(seen.scores, [])


if __name__ == "__main__":
    unittest.main()
