# Development

## Layout

```
adblock/
  app.py              the engine: capture, detector thread, state (GAME/AD), corrections, overlay rules, settings
  ui.py               control window + tray icon + the Game stats window
  capture.py          WgcWindowSource (live) + FileReplaySource (same interface, for the evaluator)
  recorder.py         SessionWriter (on-disk format) + RecordingThread (privacy guard, frame rate)
  labels.py           per-frame labels from a recording, with corrections applied backwards
  hotkeys.py          RegisterHotKey listener          config.py   defaults < config.toml < command line
  win32.py            ctypes helpers: find the stream window, rects, z-order, capture exclusion
  paths.py            ROOT (the code) and DATA (what the app writes: the project folder, or %LOCALAPPDATA%\FootballAdBlocker when installed)
  detect/             scorebug.py (graphics learner + matcher) · fusion.py (state machine + corrections)
                      · detector.py (pipeline, video area, graphics library) · scene.py (the image model)
                      · memory.py (graphics remembered between sessions)
  actuators/          audio_mute.py · overlay.py (the ad screen: timer, score, thumbnail, stats panels)
  stats_widgets.py    win-odds chart, team comparison, one team's panel, scoring / plays text
  data/espn.py        scoreboard lookup, score + stats pollers, spoiler delay
tools/                evaluate.py · session_info.py · smoke_app.py · explore_classifier.py · explore_features.py
                      · preview_control_window.py · preview_overlay.py · preview_stats.py · install_shortcuts.py
                      · make_icon.py · unmute_chrome.py · spike_*.py (the feasibility checks)
tests/                unittest: fusion, detector (synthetic broadcast), labels, memory, recorder, ESPN, hotkeys, docs
installer/            build.ps1 (stages an embedded Python + the app, tests it, compiles the setup) · FootballAdBlocker.iss · payload/
docs/                 these pages          sessions/   recordings (git-ignored)      learned/   memory + embedding cache (git-ignored)
```

## Running it

```powershell
.\run.cmd --game utah                 # control window + tray + console log
.\run.cmd --headless --no-auto        # no window: hotkeys only (what the smoke test drives)
.\run.cmd --duration 60 --no-record   # quits by itself; no session folder
```

Only one instance can run: the second fails at start-up over the hotkeys, before any session
folder is created. Stopping an instance you did not start from a terminal: create a file named
`STOP` in its session folder - it quits cleanly within a second (Chrome unmuted, session saved).

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

No screen, Chrome or GPU is needed. `tests/test_detector.py` runs the whole detector on a synthetic
broadcast (moving texture plus drawn graphics that come and go); `tests/test_fusion.py` feeds the
state machine the evidence of the live moments that shaped it; `tests/test_docs.py` fails when a
setting or flag is missing from [configuration.md](configuration.md) or `config.example.toml`.

The end-to-end check needs a live stream in Chrome:

```powershell
.\.venv\Scripts\python.exe tools\smoke_app.py        # starts the app, presses the hotkey itself, checks mute/overlay/recording
```

It injects key presses, which Windows drops while a system-owned window holds the foreground - click
the video once if it sits waiting. Its sessions are marked unreliable (below).

## Recordings

`sessions/<date>_<label>/`:

| File | Content |
| --- | --- |
| `frames/000000.jpg …` | 640-px-wide JPEGs at ~4 fps (640x360 when the stream is fullscreen), ~35 KB each: roughly 1.5 GB for a 3.5 h game. `--fps 2` halves that. |
| `frames.csv` | `index,t,wall,file,state,src_w,src_h` - `state` is GAME/AD when the frame was captured |
| `timeline.jsonl` | one JSON object per line: `session_start`, `state` (with its source: detector, a button, the hotkey), **`correction`**, `hotkey`, `auto`, `graphics`, `overlay` (what it displayed), `overlay_visible` (shown/hidden and why), `window`, `capture`, `recording`, `game`, `relearn`, `session_end`. `t` is seconds since the session started; hotkey-driven events carry the key-press time, so sort by `t` rather than trusting file order. |
| `detector.csv` | one row per analysed frame: `t,app_state,detector_state,game_like,override,auto,scores,scene,scene_score,black` |
| `graphics/*.png` | what the detector had learned, saved whenever that changed |
| `session.json` | the settings the session was recorded with |

**Labels.** `state` is what you were shown and let stand: a weak label, because you may not have
been watching. A `correction` is the strong one: you looked at the screen and said what it was.
`adblock/labels.py` turns both into per-frame ground truth by applying corrections *backwards*: a
click within 45 s of the detector's own switch undoes that switch (everything since it gets your
label); a later "GAME" is a late reaction to a real break and is back-dated 10-45 s to where the
picture started looking like the broadcast; an "AD" on a missed break is back-dated to where the
graphics disappeared. If a session's labels cannot be trusted at all (nobody watching, a test run),
drop a text file named `UNRELIABLE_LABELS.txt` with a short explanation into its folder; the
evaluator skips those unless named explicitly.

```powershell
.\.venv\Scripts\python.exe tools\session_info.py --sheet   # summarise the latest session + a contact sheet of every state change
```

## The evaluator

`tools/evaluate.py` replays recordings through the detector that runs live (via
`FileReplaySource`) and scores it against the labels:

```
=== 2026-09-26_sep26   (7202 frames, replayed in 48 s = 150 fps)
  first graphics learned at 2:01.0; scored range 0:00.0-30:02.3 (30:02.3)
  GAME-SECONDS HIDDEN:    0.0 s     ad-seconds leaked:   41.3 s of 503 s labelled AD (92% covered)
  detector transitions (state, from, to | label at that moment):
    GAME   0:00.0 ->   7:10.8  ( 430.8 s) | label GAME
    AD     7:11.0 ->  10:02.5  ( 171.5 s) | label AD
    ...
```

*Game-seconds hidden* must stay near zero; *ad-seconds leaked* is the price paid for that. The
totals include label noise (a session that starts mid-break, ads under a "GAME" hold), so `--sheet`
writes `detector_transitions.jpg` into each session - three seconds either side of every transition
- and the sheets, not the totals, decide what a number means.

The workflow for any detector change:

1. Baseline first, from a clean copy of the current code so nothing half-edited leaks in:
   ```powershell
   git archive HEAD | tar -x -C ..\baseline   # (or any scratch folder; copy learned\embeddings into it)
   .\.venv\Scripts\python.exe ..\baseline\tools\evaluate.py --scene --drive > before.txt
   ```
2. Make the change; replay the same sessions with the same flags; compare the two outputs
   transition by transition, and look at the contact sheets of every difference.
3. Add a unit test for the moment that motivated the change, and write the measured result into
   [PLAN.md](../PLAN.md).

Flags: `--scene` fuses in the image model (needs `requirements-ml.txt` and the session's cached
embeddings, below); `--drive` lets it drive (the app's default); `--no-graphics` is "Use learned
graphics" unticked; `--enter-ad`, `--exit-frames`, `--scene-exit`, `--boundary-exit`, `--scene-enter`
try other timings; `--with-corrections` also replays your recorded clicks at their times (the live
run reproduced); `--raw-labels` scores against the recorded state without back-dating; `--from` /
`--to` restrict a session to its usable range.

## The image model offline

The evaluator does not run CLIP on every frame; it uses embeddings cached once per session by
`tools/explore_classifier.py` (`learned/embeddings/<session>.npz`, one frame per second):

```powershell
.\.venv\Scripts\python.exe tools\explore_classifier.py sessions\2026-09-26_sep26   # embeds (and caches) the session, then
                                                                                   # measures zero-shot and a linear probe on it
```

The same script is the groundwork for a *trained* classifier: it fits a linear probe on the cached
embeddings, trains on some sessions and tests on another, and lists the frames it gets wrong
(`--sheet`). `tools/explore_features.py` measures cheaper candidate indicators (black frames,
letterbox bars, shot-cut rate, grass, audio level ...) against the labels - the way every candidate
signal is checked before it is allowed anywhere near the live detector.

## Previews

The ad screen cannot be screenshotted (it is excluded from capture by design) and the control window
needs the app running, so three scripts paint them straight to PNG:

```powershell
.\.venv\Scripts\python.exe tools\preview_control_window.py [--no-model] [--graphics-off] [--state AD]
.\.venv\Scripts\python.exe tools\preview_overlay.py [--game utah] [--frame sessions\...\frames\000010.jpg]
.\.venv\Scripts\python.exe tools\preview_stats.py --game utah
```

None of them captures anything, claims a hotkey or shows a window; they are safe to run while the
real app is working. The images in `docs/images/` come from the first two.

## The feasibility checks

`tools/spike_capture.py` (can the video be captured from Chrome, or does DRM give black frames?),
`spike_occlusion.py` (does Chrome keep rendering under the ad screen?), `spike_mute.py` (can Chrome
alone be muted?) and `spike_title.py` (does the window title name the game? - it does not) print
PASS/FAIL and write their evidence to `spike_out/`. Rerun them when Chrome, Windows or the GPU driver
changes and something stops working; their results on the development PC are in
[PLAN.md](../PLAN.md).

## Adapting it

- **Another streaming service**: add its window title to `title_contains`. Capture, muting and the
  overlay do not care what plays in the window.
- **Another network**: nothing to do - the graphics are learned, not configured.
- **Another league or sport**: the image model's prompts (`BROADCAST_PROMPTS` / `AD_PROMPTS` in
  `adblock/detect/scene.py`) say "football"; the graphics learner is sport-agnostic. The score and
  stats come from ESPN's college-football endpoints (`adblock/data/espn.py`, one module to swap).
- **Timings and thresholds**: `FusionSettings` in `fusion.py` and the constants at the top of
  `detector.py` and `scorebug.py`. Each has a comment saying which recorded moment set it. Change
  one, replay every session, read the contact sheets; then commit the number with its reason.

## Building the installer

`installer\build.ps1` makes the downloadable setup program, the same way on a PC and on GitHub
Actions (`.github/workflows/installer.yml` runs it on every `v*` tag and attaches the result to the
release; run it from the Actions tab for an installer without a release):

1. downloads the official Python 3.11 embeddable package (pinned version and SHA-256) into
   `build\stage\python`, bootstraps pip into it and installs `installer\requirements-installer.txt`
   (the app's pins, with the lighter PySide6-Essentials and opencv-python-headless builds);
2. copies the app (`adblock\`, `assets\`, `tools\`, `tests\`, `docs\`, the launchers and text files)
   next to it, plus `installer\payload\`: `INSTALLED`, the marker that sends everything the app
   writes to `%LOCALAPPDATA%\FootballAdBlocker` (`adblock\paths.py`), and `install_image_model.cmd`;
3. runs the unit tests with the staged Python, so what ships is what passed;
4. compiles `installer\FootballAdBlocker.iss` with Inno Setup 6 into
   `dist\FootballAdBlocker-Setup-<version>.exe`; the version is `adblock.__version__`.

```powershell
.\installer\build.ps1              # everything; step 4 needs Inno Setup 6 (winget install JRSoftware.InnoSetup)
.\installer\build.ps1 -SkipInno    # stage and test only: build\stage is a runnable copy
```

The setup program installs under `%LOCALAPPDATA%\Programs\Football Ad Blocker` (per user, no
administrator rights, nothing in the registry but the uninstaller) and offers the image model as a
component: that runs `install_image_model.cmd`, which pip-installs `requirements-ml.txt` into the
copy's own Python and fetches the weights. Shipping a real Python environment rather than a frozen
executable is what makes those 5 GB optional - `python\python.exe -m pip` works in the installed
folder, and so do the tools and the tests. The installer is not code-signed, so SmartScreen warns
once; a signing certificate would remove that.

## Where the history is

[PLAN.md](../PLAN.md): the plan, the feasibility results, what each live game taught, the
measurements behind every rule, and what was tried and rejected. Keep writing to it.
