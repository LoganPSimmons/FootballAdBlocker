# Configuration

Three layers, each overriding the one before:

1. the defaults in `adblock/config.py`,
2. `config.toml` in the data folder - the project folder from a clone, `%LOCALAPPDATA%\FootballAdBlocker`
   for an installed copy (copy `config.example.toml` there and edit) - or any file named with `--config`,
3. the command line (`.\run.cmd --help` lists everything; `start_ui.cmd` and the shortcuts pass no
   arguments, so for them `config.toml` is the place).

A misspelt key in `config.toml` is an error at start-up, not a silent default.

## Settings

| `config.toml` | Command line | Default | What it does |
| --- | --- | --- | --- |
| `hotkey` | `--hotkey` | `"F9"` | Corrects the detector / toggles GAME and AD by hand. Works while Chrome is fullscreen and focused. Examples: `"F9"`, `"pause"`, `"ctrl+alt+m"`. Not `F12` (Windows reserves it), and not a key another program has registered. |
| `quit_hotkey` | `--quit-hotkey` | `"ctrl+shift+F9"` | Quits cleanly (unmutes Chrome) without leaving fullscreen. |
| `title_contains` | `--title` | `"YouTube TV\|Fubo"` | How the stream's Chrome window is recognised: any of these texts in its title, case-insensitive, alternatives separated by `\|`. While that window shows a tab whose title does not match, nothing is analysed, recorded or covered. Add your service here - or, for one session, pick the window by hand under *Chrome window* in the control window: it is then the stream while its title names a service listed here or stays what it was when picked. |
| `game` | `--game` | `""` | Whose score and stats to show on the ad screen: a team name or abbreviation looked up on ESPN's scoreboard (`"utah"`, `"USU"`). The control window has a picker and remembers your choice, so this is mostly for the command line. |
| `label` | `--label` | `""` | Name of the session folder, `sessions/<date>_<label>/`. Defaults to the game, then `"session"`. |
| `stream_delay_s` | `--delay` | `45.0` | The spoiler guard: ESPN data is shown only once it is this many seconds old, because the stream runs behind ESPN's feed. Raise it if the ad screen ever shows a play before you have seen it. |
| `pip` | `--no-pip` | `true` | The small live thumbnail of the stream on the ad screen, so you can see for yourself when the game is back. |
| `auto` | `--no-auto` | `true` | The detector drives: it hides and shows by itself. `false`: it only watches and logs (every decision still lands in the recording), and you toggle with the hotkey or the buttons. The "Hide ads automatically" box in the window and the tray menu. |
| `scene` | `--no-scene` | `true` | Use the image model (needs `requirements-ml.txt`; ignored, with a log line, when it is not installed). `false` runs on the learned graphics alone. |
| `scene_drives` | `--fused` | `true` | The image model drives GAME/AD and the learned graphics only guard it. `false` (`--fused`): the graphics rules drive and the model may only veto hiding and bring the picture back - the behaviour of the first live day, kept for comparison. |
| `graphics` | `--no-graphics` | `true` | Consult the learned graphics: while one is on screen nothing is hidden, and one coming back shows the picture at once. `false`: the image model decides alone; the graphics are still learned, drawn in the window and remembered. The "Use learned graphics" box in the window. Without the image model the graphics are the only detector and this setting is ignored. |
| `enter_ad_s` | `--enter-ad` | `5.0` | Graphics-driven hiding (no model, model undecided, `--fused`): seconds without any learned graphic on screen before the break is hidden. Higher = safer for live play, more ad seconds leaked. |
| `exit_ad_frames` | - | `3` | Graphics-driven return: consecutive frames with an established graphic on screen before the picture comes back (3 frames at 4 fps is under a second). |
| `close_to_tray` | - | `false` | What closing the control window does. `false`: quits the app (every thread, the model, the hotkeys; Chrome unmuted). `true`: hides the window and keeps running from the tray icon. |
| `record` | `--no-record` | `true` | Record the session (frames, timeline, detector evidence) to `sessions/`. See [development.md](development.md) for the format and [../README.md](../README.md#recording-and-privacy) for what it means. |
| `fps` | `--fps` | `4.0` | Frames per second analysed and recorded. 4 lets the evaluator replay the sub-second rules faithfully; 2 halves the disk use. |
| `frame_width` | - | `640` | Width of recorded frames in pixels (640x360 for a fullscreen 16:9 stream). The detector analyses at this width too. |
| `jpeg_quality` | - | `75` | JPEG quality of recorded frames (~35 KB each at 75). |
| `sessions_dir` | - | `"sessions"` | Where recordings go; relative to the data folder (the project folder, or `%LOCALAPPDATA%\FootballAdBlocker` when installed) unless absolute. |

Command line only:

| Flag | What it does |
| --- | --- |
| `--config` *path* | Read settings from this TOML file instead of `config.toml`. |
| `--headless` | No control window, no tray icon: hotkeys only. What the end-to-end smoke test drives. |
| `--duration` *seconds* | Quit by itself after this long (for tests). |

## Examples

Another streaming service, in `config.toml`:

```toml
title_contains = "YouTube TV|Fubo|Sling TV"
```

Prefer the image model on its own from the start (the "Use learned graphics" box unticked):

```toml
graphics = false
```

Watch a game with the score of a specific team, a different hotkey, no recording:

```powershell
.\run.cmd --game "ohio state" --hotkey pause --no-record
```

Keep the app running from the tray when the window is closed:

```toml
close_to_tray = true
```

## The timings that are not settings

The rest of the state machine lives in `FusionSettings` in `adblock/detect/fusion.py`: how long the
model's verdict must hold before it hides (4 s) or shows (3 s; 2 s after a cut to black; 12 s after
you said "AD"), how long a correction is held before the picture is deemed to agree (1 s without
graphics for "AD", 20 s with graphics for "GAME"), the flap damper (two short graphics-free blips
within two minutes raise the graphics-driven entry delay to 30 s), and the grace period after which
losing all evidence during a break shows the game (3 s). They were set from recorded games, and
`tools/evaluate.py` can try other values on every recording before they touch a live one - which is
why they are not in `config.toml`. [how-it-works.md](how-it-works.md) explains each one.
