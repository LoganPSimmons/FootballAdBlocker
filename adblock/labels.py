"""Ground truth from a recorded session: what the viewer let stand, fixed up by their corrections.

Each recorded frame carries the app state at that moment. On its own that is a weak label:
when the detector switches wrongly and the viewer needs ten seconds to reach the button,
those ten seconds are recorded with the wrong state. A correction says more than "from now
on": it says the stretch that led up to it was wrong. So corrections are applied backwards:

  undoing a detector switch   the detector went to AD (or GAME) by itself and the viewer said
                              the opposite within ``UNDO_WITHIN_S``: everything since that
                              switch gets the viewer's label - the switch itself was wrong
  a late return               the viewer said GAME long after the detector's AD (live
                              2026-09-26: a 165 s break, then the broadcast came back on a
                              bumper without graphics and the viewer clicked 6 s later). The
                              break was real; only the return was late. The label goes back to
                              where the picture started looking like the broadcast again (a
                              graphic on screen, or the image model's verdict), between
                              ``LATE_MIN_S`` and ``LATE_MAX_S`` before the click
  a missed break              the viewer said AD while the detector had stayed in GAME (or had
                              returned to it long ago): the label goes back to where the
                              broadcast's graphics disappeared (the run of "no graphic on
                              screen" evidence that leads up to the click). If graphics were on
                              screen when they clicked - hiding a studio show, say - nothing is
                              back-dated.

Everything is capped at ``MAX_BACKDATE_S``: a correction is not evidence about ten minutes ago.
"""

from __future__ import annotations

from typing import Callable, Sequence

MAX_BACKDATE_S = 300.0
UNDO_WITHIN_S = 45.0  # a click this soon after the detector's own switch says the switch was wrong
LATE_MIN_S, LATE_MAX_S = 10.0, 45.0  # a later "GAME" is a late reaction: the return lies this far back


def effective_labels(frame_times: list[float], frame_states: list[str], events: list[dict],
                     evidence: Sequence[tuple] | None = None) -> list[str]:
    """Per-frame GAME/AD labels with corrections applied backwards.

    ``events`` is the session timeline; ``evidence`` the rows of detector.csv as
    ``(t, game_like)`` or ``(t, game_like, scene)`` - graphic on screen (True/False/None) and
    the image model's verdict (True broadcast / False commercial / None). Without evidence,
    missed breaks are not back-dated and a late return is taken to lie ``LATE_MIN_S`` back.
    """
    labels = list(frame_states)
    events = sorted(events, key=lambda e: e["t"])
    switches = [e for e in events if e["event"] == "state"]
    rows = [(row[0], row[1], row[2] if len(row) > 2 else None) for row in (evidence or [])]
    for click in (e for e in events if e["event"] == "correction"):
        said, at = click["to"], click["t"]
        before = [s for s in switches if s["t"] < at - 1e-6]
        last = before[-1] if before else None
        undoing = last is not None and last["source"] == "detector" and last["state"] != said
        start = None
        if undoing and at - last["t"] <= UNDO_WITHIN_S:
            start = last["t"]  # the viewer is undoing what the detector just did
        elif said == "AD":
            start = _run_start(rows, at, lambda on_screen, _scene: not on_screen)  # since when no graphic?
        elif undoing:  # a late "GAME": since when has the picture looked like the broadcast?
            run = _run_start(rows, at, lambda on_screen, scene: bool(on_screen) or bool(scene))
            start = max(run if run is not None else at - LATE_MIN_S, at - LATE_MAX_S, last["t"])
        if start is None:
            continue
        start = max(start, at - MAX_BACKDATE_S)
        for i, t in enumerate(frame_times):
            if start <= t < at:
                labels[i] = said
    return labels


def _run_start(rows: list[tuple], at: float, holds: Callable[[object, object], bool]) -> float | None:
    """Start of the run of evidence rows satisfying ``holds`` that leads up to ``at`` (None if none)."""
    start = None
    for t, on_screen, scene in reversed([row for row in rows if row[0] <= at]):
        if not holds(on_screen, scene):
            break
        start = t
    return start
