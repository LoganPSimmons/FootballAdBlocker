# Football Ad Blocker

Companion app for watching college football on **YouTube TV in Chrome** (Windows 11). During
an ad break it mutes Chrome and covers the stream with a stats screen; when the game is back
it gets out of the way. It never touches the stream itself — it only looks at what is on
screen. The approach, the measured feasibility results and the roadmap are in [PLAN.md](PLAN.md).

**Where it is now — Phase 1:** *you* are the detector. One hotkey toggles ad mode, and every
press is recorded next to low-res frames of the stream, so each game watched becomes labelled
data for the automatic detector of Phases 3–5.

## Use

```powershell
.\run.cmd --game utah          # --game: whose score to show (team name or abbreviation); optional
```

| Key | What it does |
| --- | --- |
| **F9** | Ad mode on/off: mutes Chrome, covers the stream window with the overlay (break timer, score, small live thumbnail so you can see when the game is back). Works while Chrome is fullscreen and focused. |
| **Ctrl+Shift+F9** | Quit cleanly (always unmutes Chrome). Ctrl+C in the console does the same. |

Good to know:

- The score is shown **45 s late on purpose** (`--delay`): YouTube TV runs behind ESPN's feed and
  the overlay must not spoil a play you haven't seen. For the first 45 s after start-up there is no score yet.
- The overlay steps aside when another app's window is actually *in front of* the stream, and
  returns when Chrome is back on top. Clicking something on the other monitor (or the desktop)
  doesn't move it.
- The stream's Chrome window is recognised by **"YouTube TV" in its title** (`--title`). If you
  switch that window to another tab, recording pauses until you come back — nothing but the
  stream is ever saved.
- Settings can live in `config.toml` (copy `config.example.toml`); the command line wins. `.\run.cmd --help` lists everything.
- If Chrome ever stays silent after a crash or a killed process: `.\.venv\Scripts\python.exe tools\unmute_chrome.py`
  (Windows remembers per-app mute; the app also fixes this itself at the next start).

## What gets recorded

`sessions/<date>_<label>/` (git-ignored, stays on this PC). If you watched a game *without* pressing
the hotkey, drop an `UNRELIABLE_LABELS.txt` (any explanation inside) into its folder so it is never
used as ground truth — `tools/smoke_app.py` does that for its own scripted sessions.

| File | Content |
| --- | --- |
| `frames/000000.jpg …` | 640-px-wide JPEGs at ~4 fps (640×360 when the stream is fullscreen), ~35 KB each → roughly 1.5 GB for a 3.5 h game. `--fps 2` halves that. |
| `frames.csv` | `index,t,wall,file,state,src_w,src_h` — `state` is GAME/AD when the frame was captured |
| `timeline.jsonl` | one JSON object per line: `session_start`, `hotkey`, `state`, `overlay` (what it displayed), `overlay_visible` (shown/hidden and why), `window`, `capture`, `recording`, `session_end`. `t` = seconds since session start; hotkey/state events carry the **key-press time**, so sort by `t` rather than trusting file order. |
| `session.json` | settings the session was recorded with |

```powershell
.\.venv\Scripts\python.exe tools\session_info.py --sheet   # summarise the latest session + contact sheet of every key press
```

`adblock.capture.FileReplaySource` replays a session through the same interface as the live
capture — detectors are developed against recordings and then run unchanged on the live stream.

## Setup (once)

```powershell
& "C:\Users\Logan\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .   # unit tests, no screen needed
.\.venv\Scripts\python.exe tools\smoke_app.py                   # end to end against the live stream: presses the hotkey itself
.\.venv\Scripts\python.exe tools\preview_overlay.py             # renders the overlay to spike_out\overlay_preview.png
```

The overlay is excluded from every screen-capture API (so the detector never sees it), which
also means no screenshot can show it — `preview_overlay.py` exists for that.
`tools/spike_*.py` are the Phase 0 feasibility checks (capture, occlusion, mute, window
title); rerun them if Chrome, Windows or the GPU driver change and something stops working.
`smoke_app.py` injects key presses, which Windows drops while a system-owned window holds the
foreground — click the video once if it sits waiting.
