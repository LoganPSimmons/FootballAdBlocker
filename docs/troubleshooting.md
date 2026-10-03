# Troubleshooting

The first place to look is always `logs\app.log` (the console shows the same when started with
`run.cmd`), and for anything about *when* it hid or showed, the session's `timeline.jsonl`
(`sessions\<date>_<label>\`): every state change carries its source (detector, a button, the
hotkey), every ad-screen show/hide carries its reason. Both are in the data folder: the project
folder from a clone, `%LOCALAPPDATA%\FootballAdBlocker` for an installed copy (*Recordings and
settings* in the Start menu).

## "Waiting for a Chrome window with 'YouTube TV|Fubo' in its title"

The app recognises the stream's Chrome window by its title. Hover the Chrome taskbar button or
look at the tab: if the service's name is not in the title, add what is there to `title_contains`
in `config.toml` (alternatives separated by `|`, case does not matter). Chrome must be a normal
window - an app-mode or kiosk window has a different title; a second Chrome profile is fine.

## The window's live view stays black, or nothing is ever learned

- The Chrome window shows another tab, or is minimized: the app only looks while the title matches,
  and never at a minimized window (the status line says "Can't see the stream").
- Chrome's *Use graphics acceleration when available* is on. Everything was developed with it
  **off** (`chrome://settings/system`); with it on, DRM-protected video may capture as flat black.
  `tools\spike_capture.py` measures exactly this and says PASS or FAIL.
- A normal (not fullscreen) window: the app first has to find where the video sits inside it,
  which takes ~10 s of moving picture. A paused or static picture teaches nothing.
- Learning the graphics needs a minute or two of *game* picture with the scorebug up. During a
  break, a studio show or a locked-off booth shot nothing is learned (by design: a static
  background is not a graphic). Click **Relearn graphics** if what it learned looks wrong in the
  window (grey regions are not trusted yet, green are on screen, red are missing).

## It hid live play

Click **That's the game** - the correction takes effect at once and is held until the graphics
have stayed on screen for 20 s (or the model has called it the broadcast that long). Then, once
the game is over, find out why:

```powershell
.\.venv\Scripts\python.exe tools\session_info.py --sheet          # the latest session: a contact sheet around every state change
.\.venv\Scripts\python.exe tools\evaluate.py --scene --drive --sheet sessions\<the session>
```

Usual causes, in order of likelihood:

- The image model called the broadcast a commercial for 4 s with no learned graphic on screen (a
  fan-cam, a sponsor billboard, a stadium shot at an odd angle). The graphics are the guard against
  this: if they were not learned yet (cold start, a window size it had not seen), there was nothing
  to stop it. The 20 s hold on your click keeps it from happening again on the same shot.
- The first break after a launch, before anything was learned: the model drives alone then.
  Remembered graphics (`learned/graphics.npz`, written at the end of every session) shorten this
  to a second for a network seen before - in fullscreen only.
- The flap damper and the other graphics rules only apply when the model has no opinion; see
  [how-it-works.md](how-it-works.md) for which rule fired, and `detector.csv` for the evidence
  per frame.

If the model keeps getting a particular broadcast wrong, untick **Use learned graphics** to see
whether it is the graphics or the model that is confused, or run `--fused` to let the graphics
drive. Either way the recording is the evidence a fix is measured against.

## Ads keep showing for a few seconds

That is the cheap side of the trade-off and partly by design: the ad screen comes up 5-7 s into a
break, and an ad that looks like football (a stadium-set spot, a highlights promo) shows until the
model changes its mind. Click **That's an AD**; after your click only the real graphics, a cut to
black followed by the broadcast, or 12 s of broadcast end that break, so a football-looking ad the
model believes in stays hidden.

## The ad screen does not appear although the tray icon is red

The state is AD (Chrome is muted) but the overlay is held back. `timeline.jsonl` has an
`overlay_visible` event with the reason:

- *another app is in front of the stream* - some window of yours is stacked above Chrome and
  overlaps it. The app yields on purpose (you put it there); bring Chrome to the front.
- *stream window is minimized* / *shows another tab* - nothing to cover.
- Two monitors: the ad screen covers the Chrome window's rectangle wherever it is, including on a
  screen left of the primary (negative coordinates). Mixed DPI scaling between monitors is untested.

## Chrome stays silent after the app died

Windows remembers a per-app mute. The app unmutes on every normal exit path and at the next
start, but after a kill from Task Manager or a power loss:

```powershell
.\.venv\Scripts\python.exe tools\unmute_chrome.py
```

(or tick Chrome back on in the Volume Mixer).

## "hotkey error" at start-up

- The app is already running - look for the dot in the system tray. A second instance refuses to
  start rather than fight over the hotkeys.
- Another program owns the combination: choose another with `hotkey` / `quit_hotkey`. `F12` can
  never be used (Windows reserves it for the debugger).

## The image model

- *"Image model not installed (requirements-ml.txt): running on graphics only"* - install it (an
  installed copy: *Install the image model* in the Start menu); the
  app works without it but only the learned graphics decide then, which is not enough on every network.
- *"image model could not be loaded"* in the log (and *could not be loaded* in the status line) -
  almost always a PyTorch build that does not match the GPU driver. Install the build
  [pytorch.org](https://pytorch.org/get-started/locally/) recommends for your machine (CPU-only is
  fine, slower), then `pip install open_clip_torch`.
- The first start downloads the weights (~600 MB) from Hugging Face; without internet that start
  fails and the app carries on with graphics only.
- *"Image model: loading"* for more than a minute - look in the log; the GPU may be out of memory.

## No score or stats on the ad screen

Pick the game in the control window (or `--game <team>`); the list comes from ESPN's scoreboard
for today. The score appears once it is 45 s old (the spoiler guard), so the first break of the
session can be too early for it. "no game selected" on the ad screen means nothing was picked.
ESPN's API is unofficial: if it changes, `logs\app.log` shows the error and the rest of the app is
unaffected.

## The window opened on top of the stream

The control window opens on a screen the stream is *not* on, if there is one, and remembers where
you move it (`ui_state.json`). On one screen, move it aside or minimize it; it keeps working from
the tray.

## Still stuck

Open an issue with `logs\app.log`, the session's `timeline.jsonl` and `detector.csv`, which
service and network you were watching, and whether Chrome was fullscreen. The frames themselves
are rarely needed.
