"""The session format round-trips: what SessionWriter records, FileReplaySource replays."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from adblock.capture import FileReplaySource
from adblock.recorder import SessionWriter, slugify


def picture(width: int, height: int, value: int) -> np.ndarray:
    image = np.full((height, width, 3), value, np.uint8)
    image[: height // 2, : width // 2] = 255 - value  # some structure, so JPEG has something to keep
    return image


class SessionRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.writer = SessionWriter(self.tmp.name, "Utah State @ Utah!", fps=4.0)
        self.addCleanup(self.writer.close)  # Windows can't delete the temp dir while files are open
        self.writer.start(hotkey="F9")
        self.t0 = self.writer.clock0

    def test_folder_is_dated_slugged_and_never_reused(self):
        self.assertRegex(self.writer.dir.name, r"^\d{4}-\d\d-\d\d_utah-state-utah$")
        again = SessionWriter(self.tmp.name, "Utah State @ Utah!")
        self.addCleanup(again.close)
        self.assertEqual(again.dir.name, self.writer.dir.name + "_2")

    def test_frames_events_and_labels_replay_in_order(self):
        states = ["GAME", "GAME", "AD", "AD", "GAME"]
        for i, state in enumerate(states):
            if i in (2, 4):
                self.writer.log("hotkey", self.t0 + i * 0.25 - 0.01, action="toggle")
                self.writer.log("state", self.t0 + i * 0.25 - 0.01, state=state, source="hotkey")
            self.assertTrue(self.writer.add_frame(picture(1920, 1080, 40 * i), self.t0 + i * 0.25, state))
        self.writer.close()

        replay = FileReplaySource(self.writer.dir)
        frames = list(replay)
        self.assertEqual([f.label for f in frames], states)
        self.assertEqual([f.index for f in frames], [0, 1, 2, 3, 4])
        self.assertEqual([f.t for f in frames], [0.0, 0.25, 0.5, 0.75, 1.0])
        self.assertEqual(frames[0].image.shape, (360, 640, 3))
        self.assertGreater(abs(float(frames[4].image.mean()) - float(frames[0].image.mean())), 20)  # really different pictures

        events = replay.events()
        self.assertEqual([e["event"] for e in events], ["session_start", "hotkey", "state", "hotkey", "state", "session_end"])
        self.assertEqual([e["state"] for e in events if e["event"] == "state"], ["AD", "GAME"])
        self.assertEqual(events[-1]["frames"], 5)
        self.assertEqual(replay.metadata["hotkey"], "F9")

    def test_rate_limit_keeps_the_configured_fps(self):
        saved = [self.writer.add_frame(picture(640, 360, 10), self.t0 + i * 0.05, "GAME") for i in range(41)]  # 20 fps in, 2 s
        self.writer.close()
        self.assertEqual(sum(saved), 9)  # t = 0, 0.25, ... 2.0 at 4 fps

    def test_aspect_ratio_is_kept_for_a_non_16_9_window(self):
        self.writer.add_frame(picture(1920, 1032, 90), self.t0, "GAME")  # maximized Chrome, not fullscreen
        self.writer.close()
        frame = next(iter(FileReplaySource(self.writer.dir)))
        self.assertEqual(frame.image.shape, (344, 640, 3))

    def test_replay_survives_a_missing_frame_file(self):
        for i in range(3):
            self.writer.add_frame(picture(640, 360, 50), self.t0 + i, "GAME")
        self.writer.close()
        (Path(self.writer.dir) / "frames" / "000001.jpg").unlink()
        self.assertEqual([f.index for f in FileReplaySource(self.writer.dir)], [0, 2])


class Slugify(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("USU @ UTAH"), "usu-utah")
        self.assertEqual(slugify("///"), "session")


if __name__ == "__main__":
    unittest.main()
