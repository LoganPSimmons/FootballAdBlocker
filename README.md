# Football Ad Blocker

Companion app for watching college football on **YouTube TV in Chrome** (Windows 11). During
an ad break it mutes Chrome and covers the stream with a stats screen; when the game is back
it gets out of the way. It never touches the stream itself — it only looks at what is on
screen. The approach, the measured feasibility results and the roadmap are in [PLAN.md](PLAN.md).

**Where it is now:** it hides ads **by itself**. It learns each broadcast's fixed graphics (the
scorebug, the network's corner logo) during the first minute or two, treats their disappearance
as an ad break, and brings the game back the moment they return. When it is wrong you tell it
with one click — and every correction is recorded next to low-res frames of the stream, which
is the labelled data the later, smarter detector is trained and measured on.

## Use

Double-click **`start_ui.cmd`** (no console), or from PowerShell with the log on screen:

```powershell
.\run.cmd
```

A small **control window** opens (on the screen the game is *not* on, if you have two) and a
**tray icon** appears: green dot = GAME, red = AD. Closing the window does not stop anything —
the blocker keeps running from the tray. **Quit** (window, tray menu or Ctrl+Shift+F9) stops it
and always leaves Chrome unmuted.

| When | What to do |
| --- | --- |
| It covered live play | Click **"That's the game — show it"** on the ad screen itself (or the button in the window / tray menu, or **F9**) |
| An ad is showing | **"That's an AD — hide it"** in the window or tray menu, or **F9** |
| It seems confused about this broadcast | **Relearn graphics** in the window |
| You'd rather toggle by hand | Untick **Hide ads automatically**: the detector keeps watching and logging, F9 toggles |

How it behaves:

- **First 1–2 minutes: nothing is hidden.** It has to see the game's graphics before their
  absence means anything. The window's live view shows what it has learned (green = on screen,
  red = missing).
- Ads are covered and Chrome muted **about 5 s after a break starts** — deliberately slow, so a
  replay or full-screen graphic is never mistaken for an ad. The game is back **within a
  second** of the broadcast returning, usually on the return bumper, before the scorebug.
- A correction takes effect immediately and is **held until the picture agrees with it**, then
  automatic detection carries on. Clicking a few seconds early or late needs no second click.
- **The network's corner logo means "game"**, so the halftime studio show is shown, not hidden
  (it carries the same logo). Say "That's an AD" if you don't want it; that holds until the real
  commercials start.
- If it can't see the stream (other tab, minimized, a window size it hasn't learned yet) it
  shows the game rather than guess. Leaving or entering fullscreen makes it learn again for that
  window size, which takes another minute or two of game picture.
- The score on the ad screen is **45 s late on purpose** (`--delay`): YouTube TV runs behind
  ESPN's feed and must not spoil a play. Pick the game in the window (remembered next time) or
  pass `--game utah`.
- The ad screen steps aside when another app's window is actually *in front of* the stream, and
  returns when Chrome is back on top. Clicking something on the other monitor doesn't move it.
- The stream's Chrome window is recognised by **"YouTube TV" in its title** (`--title`). While
  that window shows another tab, nothing is analysed or recorded.
- Settings can live in `config.toml` (copy `config.example.toml`); the command line wins.
  `.\run.cmd --help` lists everything. The app's log is `logs\app.log`.
- If Chrome ever stays silent after a crash or a killed process, run
  `.\.venv\Scripts\python.exe tools\unmute_chrome.py` (Windows remembers per-app mute; the app
  also fixes this itself at the next start).

## What gets recorded

`sessions/<date>_<label>/` (git-ignored, stays on this PC):

| File | Content |
| --- | --- |
| `frames/000000.jpg …` | 640-px-wide JPEGs at ~4 fps (640×360 when the stream is fullscreen), ~35 KB each → roughly 1.5 GB for a 3.5 h game. `--fps 2` halves that. |
| `frames.csv` | `index,t,wall,file,state,src_w,src_h` — `state` is GAME/AD when the frame was captured |
| `timeline.jsonl` | one JSON object per line: `session_start`, `state` (with its source: detector, a button, the hotkey), **`correction`**, `hotkey`, `auto`, `overlay` (what it displayed), `overlay_visible` (shown/hidden and why), `window`, `capture`, `recording`, `game`, `relearn`, `session_end`. `t` = seconds since session start; hotkey-driven events carry the **key-press time**, so sort by `t` rather than trusting file order. |
| `detector.csv` | one row per analysed frame: `t,app_state,detector_state,game_like,override,auto,scores` |
| `graphics/*.png` | what the detector had learned, saved whenever that changed |
| `session.json` | settings the session was recorded with |

**Labels.** `state` is what you were shown and let stand — a weak label, because you may not
have been watching. A `correction` is the strong one: you looked at the screen and said what
it was. If a session's labels can't be trusted at all (nobody watching, test runs), drop an
`UNRELIABLE_LABELS.txt` with a short explanation into its folder; evaluation skips those.

```powershell
.\.venv\Scripts\python.exe tools\session_info.py --sheet   # summarise the latest session + contact sheet of every state change
.\.venv\Scripts\python.exe tools\evaluate.py --sheet       # replay sessions through the detector: game-seconds hidden, ad-seconds leaked
```

`evaluate.py` replays recordings through exactly the detector that runs live (via
`adblock.capture.FileReplaySource`), so settings (`--enter-ad`, `--exit-frames`) can be tried
on every game you have watched before they touch a live one.

## Setup (once)

```powershell
& "C:\Users\Logan\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .   # unit tests, no screen needed
.\.venv\Scripts\python.exe tools\smoke_app.py                   # end to end against the live stream: presses the hotkey itself
.\.venv\Scripts\python.exe tools\preview_overlay.py             # renders the ad screen to spike_out\overlay_preview.png
```

The ad screen is excluded from every screen-capture API (so the detector never sees it), which
also means no screenshot can show it — `preview_overlay.py` exists for that.
`tools/spike_*.py` are the Phase 0 feasibility checks (capture, occlusion, mute, window
title); rerun them if Chrome, Windows or the GPU driver change and something stops working.
`smoke_app.py` injects key presses, which Windows drops while a system-owned window holds the
foreground — click the video once if it sits waiting.
