"""GAME/AD state machine: turns per-frame evidence and viewer corrections into a state.

Errors are not symmetric (PLAN.md): hiding live play is expensive, leaking a few seconds of
an ad is cheap. So:

  GAME -> AD    only after the broadcast's graphics have been missing for ``enter_ad_s``
                without interruption - and only if evidence is *known*: no mask yet, or a
                picture geometry the mask hasn't been confirmed in, is never a reason to hide
  AD -> GAME    after ``exit_ad_frames`` consecutive frames (< 1 s) with an *established*
                graphic present; a graphic the detector has only known for a short while has
                to stay for ``weak_exit_s`` - a young, small region can match a commercial's
                logo-dense end card for a second or two (live 2026-09-19), never for four
  unknown       while in AD, evidence going unknown for ``unknown_grace_s`` drops back to
                GAME: when we can no longer tell, we show the game
  flapping      two or more short graphics-free "blips" within two minutes (a halftime show
                flashing score graphics on and off; real breaks last minutes, not seconds)
                raise the entry delay to ``calm_enter_ad_s``: sound and picture stop cutting
                in and out, and the doubt is resolved towards showing. Blips are counted from
                the evidence, not from our own switches, so damping can't talk itself out
                of being needed

The optional image model (scene.py) has a say, but limited powers, because its known mistake
is calling football-looking ads the broadcast:

  veto          graphics missing but the picture still looks like the broadcast (studio show,
                bumper, the minutes before anything is learned): don't hide
  slow return   in AD, ``scene_exit_s`` of uninterrupted "looks like the broadcast" brings the
                picture back (a halftime show with no network logo); a 15 s stadium-set ad can't
  quick return  in AD, right after a cut to black (``boundary_window_s``): the *quick* verdict
                (last three seconds) saying "broadcast" for ``boundary_exit_s`` brings the
                picture back. Breaks end through a splice to black, and on ABC/ESPN the
                broadcast comes back on a 20 s sponsor bumper with no graphic at all (live
                2026-09-26: the viewer clicked "GAME" 16 s and 10 s after two such returns).
                Being wrong here shows a football-looking ad for a few seconds - the cheap side
  never         it cannot hide anything by itself; viewer holds listen to graphics only; and
                after the viewer has said "AD", only graphics (or the viewer) end that break

A viewer correction ("that's an ad" / "that's the game") wins immediately and is *held until
the evidence agrees with it*; from then on the machine carries on by itself. So a correction
made a few seconds early or late needs no second click. "Agrees" is deliberately lopsided:
a forced AD is confirmed by one second without graphics, a forced GAME only by graphics that
stay for ``agree_game_s`` - during live play the scorebug and corner bug are up for minutes,
whereas a halftime show flashes score graphics for a few seconds at a time, and releasing
"that's the game" on one of those would re-hide the show the viewer just asked to see.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

GAME, AD = "GAME", "AD"


@dataclass
class FusionSettings:
    enter_ad_s: float = 5.0
    exit_ad_frames: int = 3
    weak_exit_s: float = 4.0  # how long a not-yet-established graphic must stay to bring the game back
    scene_exit_s: float = 20.0  # how long the image model must keep saying "broadcast" to end an AD by itself
    boundary_window_s: float = 15.0  # a cut to black this recent = a splice: the quick verdict may end the AD...
    boundary_exit_s: float = 2.0  # ...once it has said "broadcast" for this long since the black frame
    unknown_grace_s: float = 3.0
    agree_ad_s: float = 1.0  # graphics missing this long = evidence agrees with a forced AD
    agree_game_s: float = 20.0  # graphics on screen this long without a gap = agrees with a forced GAME
    blip_max_s: float = 45.0  # graphics gone for enter_ad_s..this long, then back = a blip, not a break
    flap_blips: int = 2  # this many blips...
    flap_window_s: float = 120.0  # ...within this long = flapping
    calm_enter_ad_s: float = 30.0  # entry delay while flapping


class Fusion:
    def __init__(self, settings: FusionSettings | None = None) -> None:
        self.settings = settings or FusionSettings()
        self.state = GAME
        self.override: str | None = None  # a viewer correction that the evidence hasn't confirmed yet
        self._present_run = 0
        self._strong_run = 0
        self._present_since: float | None = None
        self._absent_since: float | None = None
        self._unknown_since: float | None = None
        self._scene_since: float | None = None  # since when the image model has kept saying "broadcast"
        self._quick_since: float | None = None  # same for its quick (three-second) verdict
        self._scene_may_return = True  # False from a viewer's "AD" until graphics are next seen
        self._black_at: float | None = None  # the last cut to black
        self._ad_since: float | None = None  # when the current AD began
        self._blips: deque[float] = deque(maxlen=16)  # when recent short graphics-free stretches ended

    def correct(self, state: str) -> None:
        """The viewer says what is on screen right now."""
        self.state = state
        self.override = state
        self._ad_since = None
        if state == AD:
            self._scene_may_return = False  # "hide this" outranks the image model's opinion of it

    def update(self, game_like: bool | None, t: float, strong: bool = True, scene: bool | None = None,
               black: bool = False, quick: bool | None = None) -> str:
        """Feed one frame's evidence (True: a broadcast graphic is on screen, False: none is,
        None: can't tell; ``strong``: the graphic on screen is an established one; ``scene``:
        the image model's smoothed opinion - True broadcast, False commercial, None none;
        ``black``: this frame is a cut to black; ``quick``: the image model's opinion over the
        last three seconds only). Returns the state the app should be in."""
        s = self.settings
        if black:
            self._black_at = t
        if self.state == AD and self._ad_since is None:
            self._ad_since = t
        self._scene_since = (t if self._scene_since is None else self._scene_since) if scene else None
        scene_for = t - self._scene_since if self._scene_since is not None else 0.0
        self._quick_since = (t if self._quick_since is None else self._quick_since) if quick else None
        boundary = (self._black_at is not None and self._ad_since is not None and self._black_at > self._ad_since
                    and t - self._black_at <= s.boundary_window_s)
        quick_for = t - max(self._quick_since, self._black_at) if boundary and self._quick_since is not None else 0.0
        self._strong_run = self._strong_run + 1 if game_like and strong else 0
        if game_like is None:
            self._present_run, self._present_since, self._absent_since = 0, None, None
            self._unknown_since = t if self._unknown_since is None else self._unknown_since
        elif game_like:
            if self._absent_since is not None and s.enter_ad_s <= t - self._absent_since < s.blip_max_s:
                self._blips.append(t)
            self._present_run += 1
            self._present_since = t if self._present_since is None else self._present_since
            self._absent_since = self._unknown_since = None
        else:
            self._present_run, self._present_since, self._unknown_since = 0, None, None
            self._absent_since = t if self._absent_since is None else self._absent_since

        present_for = t - self._present_since if self._present_since is not None else 0.0
        if game_like:
            self._scene_may_return = True
        present = self._strong_run >= s.exit_ad_frames or present_for >= s.weak_exit_s
        absent_for = t - self._absent_since if self._absent_since is not None else 0.0

        if self.override is not None:
            agrees = present_for >= s.agree_game_s if self.override == GAME else absent_for >= s.agree_ad_s
            if not agrees:
                return self.state
            self.override = None  # evidence caught up with the viewer: back to automatic

        flapping = sum(t - when <= s.flap_window_s for when in self._blips) >= s.flap_blips
        vetoed = bool(scene) or (boundary and bool(quick))  # the picture looks like the broadcast: don't hide
        if self.state == GAME and absent_for >= (s.calm_enter_ad_s if flapping else s.enter_ad_s) and not vetoed:
            self.state, self._ad_since = AD, t
        elif self.state == AD and (present or (self._scene_may_return
                                               and (scene_for >= s.scene_exit_s or quick_for >= s.boundary_exit_s))):
            self.state = GAME
            if not present:  # brought back by the image model: the graphics rule starts its clock afresh
                self._absent_since = t
        elif self.state == AD and self._unknown_since is not None and t - self._unknown_since >= s.unknown_grace_s:
            self.state = GAME
        if self.state == GAME:
            self._ad_since = None
        return self.state
