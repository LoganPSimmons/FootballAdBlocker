import unittest

from adblock.labels import effective_labels

TIMES = [float(t) for t in range(0, 100, 2)]  # a frame every 2 s


def states(*spans):
    """states(("GAME", 0, 40), ("AD", 40, 100)) -> one recorded state per frame in TIMES."""
    return [next(name for name, a, b in spans if a <= t < b) for t in TIMES]


def at(labels, t):
    return labels[TIMES.index(float(t))]


class BackDating(unittest.TestCase):
    def test_undoing_a_detector_switch_relabels_the_whole_stretch(self):
        recorded = states(("GAME", 0, 40), ("AD", 40, 52), ("GAME", 52, 100))  # hidden for 12 s before the click
        events = [
            {"t": 40.0, "event": "state", "state": "AD", "source": "detector"},
            {"t": 52.0, "event": "correction", "to": "GAME", "via": "overlay_button"},
            {"t": 52.0, "event": "state", "state": "GAME", "source": "overlay_button"},
        ]
        labels = effective_labels(TIMES, recorded, events)
        self.assertEqual(set(labels), {"GAME"}, "the detector's AD was wrong from its first second")

    def test_a_false_game_inside_a_break(self):
        recorded = states(("AD", 0, 30), ("GAME", 30, 36), ("AD", 36, 100))
        events = [
            {"t": 30.0, "event": "state", "state": "GAME", "source": "detector"},
            {"t": 36.0, "event": "correction", "to": "AD", "via": "window_button"},
            {"t": 36.0, "event": "state", "state": "AD", "source": "window_button"},
        ]
        self.assertEqual(set(effective_labels(TIMES, recorded, events)), {"AD"})

    def test_missed_break_goes_back_to_where_the_graphics_vanished(self):
        recorded = states(("GAME", 0, 60), ("AD", 60, 100))  # detector never switched; viewer clicked at 60
        events = [
            {"t": 60.0, "event": "correction", "to": "AD", "via": "hotkey"},
            {"t": 60.0, "event": "state", "state": "AD", "source": "hotkey"},
        ]
        evidence = [(float(t), t < 44) for t in range(0, 100)]  # graphics gone from t=44 on
        labels = effective_labels(TIMES, recorded, events, evidence)
        self.assertEqual(at(labels, 42), "GAME")
        self.assertEqual(at(labels, 44), "AD")
        self.assertEqual(at(labels, 58), "AD")

    def test_hiding_something_with_graphics_on_screen_is_not_back_dated(self):
        recorded = states(("GAME", 0, 60), ("AD", 60, 100))  # "hide the studio show", network logo still up
        events = [{"t": 60.0, "event": "correction", "to": "AD", "via": "window_button"},
                  {"t": 60.0, "event": "state", "state": "AD", "source": "window_button"}]
        evidence = [(float(t), True) for t in range(0, 100)]
        self.assertEqual(effective_labels(TIMES, recorded, events, evidence), recorded)

    def test_a_viewers_own_earlier_click_is_not_overwritten(self):
        recorded = states(("GAME", 0, 20), ("AD", 20, 50), ("GAME", 50, 100))
        events = [
            {"t": 20.0, "event": "correction", "to": "AD", "via": "hotkey"},
            {"t": 20.0, "event": "state", "state": "AD", "source": "hotkey"},
            {"t": 50.0, "event": "correction", "to": "GAME", "via": "hotkey"},  # undoes their own AD, not the detector's
            {"t": 50.0, "event": "state", "state": "GAME", "source": "hotkey"},
        ]
        self.assertEqual(effective_labels(TIMES, recorded, events), recorded)

    def test_a_late_game_click_is_a_late_return_not_a_wrong_break(self):
        times = [float(t) for t in range(0, 1000, 5)]
        recorded = ["GAME" if t < 100 else "AD" if t < 900 else "GAME" for t in times]
        events = [{"t": 100.0, "event": "state", "state": "AD", "source": "detector"},
                  {"t": 900.0, "event": "correction", "to": "GAME", "via": "tray"},
                  {"t": 900.0, "event": "state", "state": "GAME", "source": "tray"}]
        labels = effective_labels(times, recorded, events)
        self.assertEqual(labels[times.index(500.0)], "AD", "the break was real; only the return was late")
        self.assertEqual(labels[times.index(880.0)], "AD", "without evidence the return is taken to be 10 s back")
        self.assertEqual(labels[times.index(895.0)], "GAME")
        # With evidence: the image model saw the broadcast from t=870 on (graphics still missing).
        evidence = [(float(t), False, t >= 870) for t in range(0, 1000)]
        labels = effective_labels(times, recorded, events, evidence)
        self.assertEqual(labels[times.index(865.0)], "AD")
        self.assertEqual(labels[times.index(870.0)], "GAME")
        # A run longer than LATE_MAX_S is capped: the viewer would not have waited that long.
        evidence = [(float(t), False, t >= 700) for t in range(0, 1000)]
        labels = effective_labels(times, recorded, events, evidence)
        self.assertEqual(labels[times.index(850.0)], "AD")
        self.assertEqual(labels[times.index(860.0)], "GAME")

    def test_a_late_ad_click_after_a_detector_return_is_a_new_break(self):
        times = [float(t) for t in range(0, 400, 2)]
        recorded = ["AD" if t < 100 else "GAME" if t < 300 else "AD" for t in times]
        events = [{"t": 100.0, "event": "state", "state": "GAME", "source": "detector"},
                  {"t": 300.0, "event": "correction", "to": "AD", "via": "hotkey"},
                  {"t": 300.0, "event": "state", "state": "AD", "source": "hotkey"}]
        evidence = [(float(t), t < 280) for t in range(0, 400)]  # graphics gone from t=280
        labels = effective_labels(times, recorded, events, evidence)
        self.assertEqual(labels[times.index(200.0)], "GAME", "the detector's return 200 s earlier was right")
        self.assertEqual(labels[times.index(278.0)], "GAME")
        self.assertEqual(labels[times.index(282.0)], "AD")


if __name__ == "__main__":
    unittest.main()
