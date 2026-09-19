"""Ground truth from a recorded session: what the viewer let stand, fixed up by their corrections.

Each recorded frame carries the app state at that moment. On its own that is a weak label:
when the detector switches wrongly and the viewer needs ten seconds to reach the button,
those ten seconds are recorded with the wrong state. A correction says more than "from now
on": it says the stretch that led up to it was wrong. So corrections are applied backwards:

  undoing a detector switch   the detector went to AD (or GAME) by itself and the viewer then
                              said the opposite: everything since that switch gets the
                              viewer's label
  a missed break              the viewer said AD while the detector had stayed in GAME:
                              the label goes back to where the broadcast's graphics
                              disappeared (the run of "no graphic on screen" evidence that
                              leads up to the click). If graphics were on screen when they
                              clicked - hiding a studio show, say - nothing is back-dated.

Both are capped at ``MAX_BACKDATE_S``: a correction is not evidence about ten minutes ago.
"""

from __future__ import annotations

MAX_BACKDATE_S = 300.0


def effective_labels(frame_times: list[float], frame_states: list[str], events: list[dict],
                     evidence: list[tuple[float, bool | None]] | None = None) -> list[str]:
    """Per-frame GAME/AD labels with corrections applied backwards.

    ``events`` is the session timeline; ``evidence`` the (t, game_like) rows of detector.csv
    (optional: without it, missed breaks are not back-dated).
    """
    labels = list(frame_states)
    events = sorted(events, key=lambda e: e["t"])
    switches = [e for e in events if e["event"] == "state"]
    for click in (e for e in events if e["event"] == "correction"):
        said, at = click["to"], click["t"]
        before = [s for s in switches if s["t"] < at - 1e-6]
        start = None
        if before and before[-1]["source"] == "detector" and before[-1]["state"] != said:
            start = before[-1]["t"]  # the viewer is undoing what the detector just did
        elif said == "AD" and evidence:
            run_start = None  # walk back over the evidence: since when was no graphic on screen?
            for t, game_like in reversed([row for row in evidence if row[0] <= at]):
                if game_like:
                    break
                run_start = t
            start = run_start
        if start is None:
            continue
        start = max(start, at - MAX_BACKDATE_S)
        for i, t in enumerate(frame_times):
            if start <= t < at:
                labels[i] = said
    return labels
