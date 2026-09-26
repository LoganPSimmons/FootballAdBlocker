# Football Ad Blocker

Companion app for watching college football on **YouTube TV or Fubo in Chrome** (Windows 11). During
an ad break it mutes Chrome and covers the stream with a stats screen; when the game is back
it gets out of the way. It never touches the stream itself — it only looks at what is on
screen. The approach, the measured feasibility results and the roadmap are in [PLAN.md](PLAN.md).

**Where it is now:** it hides ads **by itself**. An image model looks at the whole picture once
a second and decides "broadcast" or "commercial"; a few seconds of "commercial" hides, a few
seconds of "broadcast" shows. The broadcast's fixed graphics (the scorebug, the network's corner
logo), learned during the first minute or two and remembered between sessions, guard it: while
one is on screen nothing is hidden, and when one comes back the picture is back at once. When it
is wrong you tell it with one click — and every correction is recorded next to low-res frames of
the stream, which is the labelled data the later, smarter detector is trained and measured on.

## Use

Start it from the **"Football Ad Blocker" shortcut** on the Desktop or in the Start menu (no
console; right-click it to pin it to the taskbar or Start - Windows doesn't let programs pin
themselves). `start_ui.cmd` does the same; from PowerShell with the log on screen:

```powershell
.\run.cmd
.\.venv\Scripts\python.exe tools\install_shortcuts.py            # (re)create the shortcuts; --remove deletes them
```

A small **control window** opens (on the screen the game is *not* on, if you have two) and a
**tray icon** appears: green dot = GAME, red = AD. **Closing the window quits the app** - every
process, the image model's GPU memory, the hotkeys - and always leaves Chrome unmuted; so do
Quit in the tray menu and Ctrl+Shift+F9. Minimize it to get it out of sight while it works.
(`close_to_tray = true` in `config.toml` makes closing hide it to the tray instead.)

| When | What to do |
| --- | --- |
| It covered live play | Click **"That's the game — show it"** on the ad screen itself (or the button in the window / tray menu, or **F9**) |
| An ad is showing | **"That's an AD — hide it"** in the window or tray menu, or **F9** |
| It seems confused about this broadcast | **Relearn graphics** in the window |
| You'd rather toggle by hand | Untick **Hide ads automatically**: the detector keeps watching and logging, F9 toggles |
| You want the numbers | **Game stats** in the window or the tray menu: win odds after every play, both teams side by side or one team's own stats, leaders and players, the scoring summary and the last plays - the same 45 s behind the feed as the score |

The **ad screen** itself carries the stats screen on its right: the win-odds chart, the two
teams' main numbers side by side and the scoring summary, all held back by the spoiler delay
(nothing on it can be ahead of what you have seen). In a small Chrome window there is no room
for it and only the timer, score and thumbnail show.

How it behaves:

- **No training per game.** It finds each broadcast's graphics by itself while you watch, and it
  **remembers** the ones that proved themselves (`learned/graphics.npz`), so a network you have
  watched before is recognised in the first second - only team-specific parts are learned afresh.
- **First minutes on a network it has never seen: only the image model can hide.** The graphics
  have to be learned (a minute or so) and seen to stay for another minute before their absence
  means anything; with remembered graphics that shrinks to about a minute. Until then a break is
  hidden only when the image model has called the picture a commercial for 10 s. The window's
  live view shows what it has learned (green = on screen, red = missing).
- Ads are covered and Chrome muted **about 5-7 s after a break starts** (the model's verdict is
  a three-second median, then 4 s of it) — deliberately slow, so a replay or full-screen graphic
  is never mistaken for an ad. The game is back **within a second** of a learned graphic
  returning, or about 4-5 s after the picture starts looking like the broadcast again (the return
  bumper), or 2 s after a cut to black followed by the broadcast.
- A correction takes effect immediately and is **held until the picture agrees with it**, then
  automatic detection carries on. Clicking a few seconds early or late needs no second click.
  ("Agrees" = no graphics for a second after "AD"; graphics that *stay* for 20 s after "GAME".)
- If the picture keeps flipping between graphics and none (a halftime show flashing scores),
  it stops toggling and leans towards showing: hiding then needs 30 s without graphics.
- **The network's corner logo means "game".** A halftime studio show mostly does *not* carry it
  (seen on FOX), so the graphics alone can't tell it from commercials. That is what the optional
  **image model** is for (below): with it the show stays up and its commercials are hidden.
  Without it, click "That's the GAME" to watch the show and "That's an AD" for its commercials.
- **Networks differ.** On FOX the corner logo stays through replays, packages and the return
  bumper, so the graphics alone are nearly enough. On ABC/ESPN (seen 2026-09-26) the corner logo
  *leaves* during post-score packages and on some live shots, and every break ends with a cut to
  black and a ~20 s sponsor bumper that carries no graphic at all - there the image model does
  the work: it keeps the picture up through the packages and brings it back on the bumper.
- **Image model (`requirements-ml.txt`, ~5 GB, uses the GPU a little) - it drives.** A
  whole-picture check once a second: does this look like the broadcast or like a commercial?
  Its three-second verdict saying "commercial" for 4 s hides; "broadcast" for 3 s shows; right
  after a cut to black, 2 s. A learned graphic on screen blocks hiding, and one coming back
  shows the picture at once - the graphics never do anything else. Once you have said "AD",
  only the real graphics, a cut to black followed by the broadcast, or 12 s of broadcast end
  that break. Its weakness is ads that look like football - those show until it changes its
  mind. Without the model (not installed, or "Use the image model" unticked / `--no-scene`)
  the graphics rules drive as they did on the first live day; `--fused` keeps the graphics
  driving with the model only vetoing and returning.
- If it can't see the stream (other tab, minimized, a window size it hasn't learned yet) it
  shows the game rather than guess. Leaving or entering fullscreen makes it learn again for that
  window size, which takes another minute or two of game picture.
- The score on the ad screen is **45 s late on purpose** (`--delay`): YouTube TV runs behind
  ESPN's feed and must not spoil a play. Pick the game in the window (remembered next time) or
  pass `--game utah`.
- The ad screen steps aside when another app's window is actually *in front of* the stream, and
  returns when Chrome is back on top. Clicking something on the other monitor doesn't move it.
- The stream's Chrome window is recognised by **"YouTube TV" or "Fubo" in its title** (`--title`,
  alternatives separated by `|`). While that window shows another tab, nothing is analysed or
  recorded. Switching services or channels needs no restart; the graphics are learned afresh.
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
| `detector.csv` | one row per analysed frame: `t,app_state,detector_state,game_like,override,auto,scores,scene,scene_score` |
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
.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt   # optional: the image model (PyTorch + CLIP, ~5 GB)
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
