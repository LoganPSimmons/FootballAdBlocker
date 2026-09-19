"""GAME/AD state machine: turns per-frame evidence and viewer corrections into a state.

Errors are not symmetric (PLAN.md): hiding live play is expensive, leaking a few seconds of
an ad is cheap. So:

  GAME -> AD    only after the broadcast's graphics have been missing for ``enter_ad_s``
                without interruption - and only if evidence is *known*: no mask yet, or a
                picture geometry the mask hasn't been confirmed in, is never a reason to hide
  AD -> GAME    after ``exit_ad_frames`` consecutive frames with a graphic present (< 1 s)
  unknown       while in AD, evidence going unknown for ``unknown_grace_s`` drops back to
                GAME: when we can no longer tell, we show the game

A viewer correction ("that's an ad" / "that's the game") wins immediately and is *held until
the evidence agrees with it*; from then on the machine carries on by itself. So a correction
made a few seconds early or late needs no second click, and "that's the game" during a studio
show keeps it on screen until the real scorebug is back.
"""

from __future__ import annotations

from dataclasses import dataclass

GAME, AD = "GAME", "AD"


@dataclass
class FusionSettings:
    enter_ad_s: float = 5.0
    exit_ad_frames: int = 3
    unknown_grace_s: float = 3.0
    agree_ad_s: float = 1.0  # graphics missing this long = evidence agrees with a forced AD


class Fusion:
    def __init__(self, settings: FusionSettings | None = None) -> None:
        self.settings = settings or FusionSettings()
        self.state = GAME
        self.override: str | None = None  # a viewer correction that the evidence hasn't confirmed yet
        self._present_run = 0
        self._absent_since: float | None = None
        self._unknown_since: float | None = None

    def correct(self, state: str) -> None:
        """The viewer says what is on screen right now."""
        self.state = state
        self.override = state

    def update(self, game_like: bool | None, t: float) -> str:
        """Feed one frame's evidence (True: a broadcast graphic is on screen, False: none is,
        None: can't tell). Returns the state the app should be in."""
        s = self.settings
        if game_like is None:
            self._present_run, self._absent_since = 0, None
            self._unknown_since = t if self._unknown_since is None else self._unknown_since
        elif game_like:
            self._present_run += 1
            self._absent_since = self._unknown_since = None
        else:
            self._present_run, self._unknown_since = 0, None
            self._absent_since = t if self._absent_since is None else self._absent_since

        present = self._present_run >= s.exit_ad_frames
        absent_for = t - self._absent_since if self._absent_since is not None else 0.0

        if self.override is not None:
            agrees = present if self.override == GAME else absent_for >= s.agree_ad_s
            if not agrees:
                return self.state
            self.override = None  # evidence caught up with the viewer: back to automatic

        if self.state == GAME and absent_for >= s.enter_ad_s:
            self.state = AD
        elif self.state == AD and present:
            self.state = GAME
        elif self.state == AD and self._unknown_since is not None and t - self._unknown_since >= s.unknown_grace_s:
            self.state = GAME
        return self.state
