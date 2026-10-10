# How it works

The app is a companion, not a player: Chrome keeps playing the stream exactly as before, and the
app watches that window, decides GAME or AD, and acts on the audio and the screen.

```
Chrome (the stream) ──frames, ~4 fps──▶  DETECTOR  ──GAME / AD──▶  ACTUATORS
      ▲ untouched: full quality,         image model                ├─ mute / unmute Chrome's audio sessions
      │ zero latency                     + learned graphics         └─ show / hide the ad screen
      │                                  + state machine                (always on top, invisible to capture)
      │                                       ▲                                  ▲
 corrections: ad-screen button,               │                                  │
 window, tray, F9 ────────────────────────────┘      ESPN score + stats, delayed ─┘
                                                     recorder: frames + timeline + evidence
```

Everything is one Python process: PySide6 for the windows, OpenCV and NumPy for the graphics,
PyTorch and open_clip for the image model, ctypes for the handful of Win32 calls. Each module's
docstring explains the *why* of what it does, usually with the recorded moment that caused it;
[PLAN.md](../PLAN.md) has the measurements.

## Seeing the stream

- **Capture**: Windows Graphics Capture of the Chrome window by handle (`windows-capture`), throttled
  to about four frames a second. It follows the window across monitors and sizes, and never contains
  other windows, notifications or the app's own ad screen. Frames are analysed and recorded at 640
  pixels wide.
- **The video area**: fullscreen, the whole frame. In a normal window the player's video sits inside
  browser chrome and letterbox bars, so the app finds it from where the picture *changes* over the
  first ten seconds or so. Learned graphics are pixel-exact, so they are kept per geometry (frame
  size plus video rectangle); every fullscreen size shares one library because it analyses to the
  same 640x360.
- **Privacy guard**: frames are analysed and recorded only while the window's title names the
  stream (`title_contains`; for a window picked by hand in the control window, also the title it
  had when picked). Another tab, a minimized window or a frozen picture means "can't tell", which
  never hides anything.
- **The ad screen is invisible to capture** (`WDA_EXCLUDEFROMCAPTURE`), so the detector always sees
  the stream underneath it, never its own stats screen.

## Signal 1: the image model

CLIP (ViT-B/32 through open_clip, zero-shot: no training at all). Once a second the newest frame is
embedded and compared with seven "broadcast" prompts (*a live American football game on television*,
*a replay of a football play*, *a sports studio show with hosts at a desk*, ...) and seven
"commercial" prompts (*a car commercial*, *a trailer for a movie or a TV show*, ...); the softmax
mass on the broadcast side minus one half is the frame's score. On an RTX 3060 Ti that is about
10 ms per frame and under 1 GB of VRAM.

Two verdicts are read off the scores: the **quick** one is the median of the last three (the one
that drives), the **slow** one the median of the last nine. A median inside ±0.15 is "undecided".
Measured on the first recorded game: 97-99 % of frames right, and - the reason it is here - it tells
a logo-less halftime studio show from its commercials, which the graphics cannot. Its known weakness
is **ads that look like football**: a spot shot in a stadium, a promo made of highlights. Those show
for a few seconds, the cheap mistake.

## Signal 2: the learned graphics

During live coverage a few small graphics sit at fixed positions for minutes - the scorebug's frame,
team names and logos, the network's corner bug - while everything in an ad changes within 15-60 s.
DVR commercial skippers find channel logos the same way on recordings; here it runs live, with no
templates and nothing to configure per network:

- Each analysed frame becomes a binary **edge map**. The learner keeps about eight minutes of them,
  one per second, and looks for the pixels whose edges persist *together* (present in ≥ 90 % of the
  frames that match a first guess, iterated). The frames it learns from must span several visibly
  different scenes, so a locked-off booth or studio shot cannot pass its background off as a
  graphic. It re-learns every 30 s.
- Persistent pixels are grouped into **regions scored separately**, because they come and go
  separately: replays and full-screen graphics drop the scorebug but keep the corner bug. A region's
  score is the share of its edge pixels present, minus the share of the quiet pixels around it that
  now carry edges, capped at its excess over the edge density of the picture around it - a graphic
  that is there scores about 0.9, a busy crowd shot hitting a small logo by chance at most 0.6.
- **Trust is earned.** A region's *absence* only counts once it has been on screen for 60 s (a
  halftime highlights bug learned a moment before the package ended once hid the broadcast that
  followed). A region alone brings the picture back within a second only after three minutes on
  screen; a younger one must stay four seconds (a young region once matched a commercial's end card
  for a second). Regions learned from frames you tagged "GAME" need 60 s on screen before they count
  at all. A region that is mostly on screen while the model calls the picture a commercial is an
  ad's own banner and is dropped. Regions that keep scoring in no-man's-land are scenery, not
  graphics, and are purged; a region unseen for 45 minutes leaves.
- **Memory between sessions**: fullscreen regions with three minutes or more on screen are saved
  to `learned/graphics.npz` (at most 32, expiring after 120 days). A remembered graphic vouches for
  the game from the first frame of the next session, but cannot hide anything until it has been
  seen for a minute there - so a stale or wrong-network graphic is harmless.
- **Corrections steer the learner**: frames after "That's the GAME" seed the mask (a changed
  scorebug layout is picked up within about 30 s of the click), frames under a held "AD" and frames
  the model calls a commercial are left out.

The control window's live view shows all of this: the analysed picture with each learned region
drawn in green (on screen), red (missing) or grey (not trusted yet).

## The state machine

`adblock/detect/fusion.py`. The one rule behind every number: **hiding live play is the expensive
mistake, leaking a few seconds of an ad is the cheap one**, so entering AD needs sustained evidence
and returning to GAME needs little. Which signal drives depends on what is installed and switched on:

| Mode | Hides when | Shows when | How you get it |
| --- | --- | --- | --- |
| **Image model drives, learned graphics guard** (the default) | no trusted graphic on screen **and** the quick verdict has said "commercial" for 4 s | a trusted graphic is back (unless the quick verdict is actively saying "commercial"); or "broadcast" for 3 s; or 2 s of "broadcast" after a cut to black | image model installed |
| **Image model alone** | "commercial" for 4 s | "broadcast" for 3 s; 2 s after a cut to black; or no "commercial" verdict at all for 3 s (can't tell → show) | untick **Use learned graphics** (`graphics = false`, `--no-graphics`) |
| **Learned graphics alone** | no trusted graphic on screen for 5 s (30 s while flapping, see below) | a trusted graphic is back: 3 frames for an established one, 4 s for a young one | no image model installed, or `--no-scene` |
| **Fused: graphics drive, model guards** | no graphic for 5 s and the model does not say "broadcast"; or no graphic and "commercial" for 10 s | a graphic is back; "broadcast" for 12 s; 2 s after a cut to black | `--fused` (`scene_drives = false`), the first live day's behaviour |

Whenever the model has no opinion - loading, undecided, switched off - the graphics rules of the
third row take over in the first two modes (and in the second row nothing can hide until it has one).
In every mode "can't tell" never hides anything, and losing all evidence during a break for 3 s
without the model calling it a commercial shows the game.

**After you said "AD"**, only the real graphics, a cut to black followed by 2 s of "broadcast", or
12 s of "broadcast" end that break - not the usual 3 s - so a football-looking ad you hid stays
hidden. The 12 s was 20 s until FOX's Big Noon crew came back on a fan-cam with no corner bug and
no cut to black; 12 s means a 10 s stadium-set ad still cannot reopen a break while a 15 s one shows
its last three seconds.

**Corrections are held until the picture agrees.** A click takes effect immediately and overrides
everything; the hold is released when the evidence catches up, and automatic detection carries on
from there - so a click a few seconds early or late needs no second click. "Agrees" is deliberately
lopsided: an "AD" is confirmed by one second without graphics (or, with the model driving, one
second of "commercial" with no graphic up); a "GAME" only by graphics that *stay* for 20 s (or 20 s
of "broadcast"), because during live play the scorebug is up for minutes whereas a halftime show
flashes score graphics for a few seconds at a time, and releasing your "show it" on one of those
would re-hide the show you just asked for.

**Flapping** (graphics-driven modes): two or more short graphics-free blips (5-45 s, then the
graphic is back) within two minutes mean a show flashing score graphics on and off, not breaks;
the entry delay rises to 30 s and the doubt is resolved towards showing. Blips are counted from the
evidence, not from the app's own switches, so damping cannot talk itself out of being needed.

## Doing something about it

- **Mute**: Chrome's audio sessions are muted the way the Volume Mixer does it - per app, on every
  playback device Chrome has a session on (it holds one on each), nothing else touched. Re-applied
  every two seconds during a break because Chrome may open a new session mid-break, and undone on
  every exit path, at the next start, and when the console window is closed. Windows remembers a
  per-app mute forever, hence `tools/unmute_chrome.py` for the case of a killed process.
- **The ad screen**: a frameless, always-on-top, no-activate Qt *tool* window excluded from
  capture. All four properties matter: a tool window is ignored by Chrome's occlusion tracker, so
  the video keeps rendering (and stays capturable) underneath; no-activate means showing it never
  takes focus or knocks Chrome out of fullscreen. It covers the Chrome window's rectangle wherever
  it is. It yields when an ordinary window of yours is stacked *above* the stream and overlaps it
  by at least 100x100 px - decided by z-order, never by focus, because the desktop or an invisible
  system window can own the foreground without being in the way. Every show and hide is logged
  with its reason.
- **Hotkeys**: Win32 `RegisterHotKey`, which the system handles before the focused application sees
  the key, so it works while Chrome is fullscreen; no hooks, no admin rights. The price is that the
  combination is swallowed system-wide while the app runs.

## The stats screen and the spoiler guard

ESPN's public college-football JSON API, read-only, no key: the scoreboard to find the game, one
small endpoint for the score, and the summary (win probability per play, box score, leaders, every
play with a wall-clock time) every 30 s for the stats. The stream runs 30-60 s behind ESPN's feed,
so everything is served **delayed**: the score as a snapshot at least `stream_delay_s` old, and the
plays and win-probability points additionally cut at each play's wall clock, with the score line on
the stats screen being the score *as of the last play served*. Nothing on the ad screen can be ahead
of what you have seen. ESPN's CDN occasionally answers with an older game state; the poller drops
answers that would move the game backwards.

## What is recorded

Every session is a labelled dataset as a side effect of watching: low-res frames, the timeline of
states, clicks and overlay decisions, and the detector's evidence per frame. The state you were
shown and let stand is a weak label; a correction is a strong one, applied backwards over the
stretch it says was wrong. `tools/evaluate.py` replays any recording through exactly the detector
code that runs live and reports *game-seconds hidden* and *ad-seconds leaked* - the way every
change here is judged. [development.md](development.md) has the format and the workflow.

## Measured so far

From [PLAN.md](../PLAN.md), where the details and the contact sheets are:

- FOX, 2026-09-19 (graphics only): every break found, entered ~5 s after the first ad, left within
  a second of the broadcast returning, 0 s of play hidden. The scorebug alone was missing in 15 % of
  live frames; scorebug and corner bug together in none.
- ABC/ESPN, 2026-09-26: the corner bug leaves during post-score packages, and every break ends with
  a cut to black and a 20 s sponsor bumper without any graphic. With the image model: every break
  found, 0.0 s of broadcast hidden in the unaided replay; without it about 12 s hidden after every score.
- Eight labelled sessions (3.5 h, 56 min of ads), the model driving: 3.6 s of broadcast hidden
  outside two stretches of label noise, 83 % of ad time covered, 72 transitions. The fused build:
  10.9 s hidden, 84 % covered, 59 transitions - the cheap side paying for the expensive side.
