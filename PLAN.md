# Football Ad Blocker — plan

Watch college football on the PC; when an ad break starts, the tool mutes the stream and
covers it with a live stats screen (score, win odds, recent plays, box score, other
games); when the game comes back, it gets out of the way. No more manual mute-and-hide.

Status (2026-09-19): **Phase 0 done** (S1–S3 pass, S4 negative — results below). **Phase 1
built and passing its end-to-end check**; its exit criterion (a whole game watched with the
hotkey, labelled session on disk) is the next thing to do — just use it.

## Setup this is designed for

| Fact | Value | Why it matters |
| --- | --- | --- |
| Source | **YouTube TV in Chrome** | Every network comes through one player; no ad markers exposed for live TV, so detection has to be visual/audio. |
| Display | Game on the primary 1920×1080 @ 50 Hz screen | The stats screen covers the game where it is. *Observed 2026-09-19:* Windows reports the primary as `E70-E3\|DELL G2722HS` (looks like a TV + monitor in duplicate mode) **plus an LG ultrawide 2560×1080 to its right** — not a single monitor. Nothing depends on there being only one; see "Capture" in the stack. |
| Chrome | **"Use graphics acceleration" is OFF** (`hardware_acceleration_mode.enabled = false` in Local State) | Every Phase 0 result below was obtained in this state. Capture with acceleration ON is untested — if it is ever turned back on and frames go black, this is why. |
| OS | Windows 11 (build 26200) | Borderless window capture and capture-excluded windows are both available. |
| GPU | RTX 3060 Ti (8 GB) | Plenty for a small vision model at a few frames/sec alongside video playback. |
| Python | 3.11.4 in `.venv` | No ffmpeg / node / uv on PATH (not needed for the core). |

## Approach: a companion app, not a custom player

The app does **not** play the stream. You keep watching YouTube TV in Chrome exactly as
now; the app sits beside it and does three things:

```
  Chrome (YouTube TV) ──frames──▶  DETECTOR ──state──▶  ACTUATORS
        ▲                          scorebug matcher      ├─ mute/unmute Chrome's audio session
        │ untouched: full          + scene classifier    └─ show/hide always-on-top STATS OVERLAY
        │ quality, zero latency    + (later) audio              ▲
        │                          → GAME / AD state            │
  global hotkey ──override──────────────┘            ESPN live data (delayed to match stream)
```

Why not build the player ourselves (embed the site in our own window)? YouTube TV is
DRM-protected and Google blocks sign-in from embedded browsers, so that route is a fight
with no upside. A companion works with any source, never touches the stream, and in GAME
state has zero effect on the picture.

## Detection — the hard part

### Signals, in the order we build them

1. **Scorebug presence (primary).** Every network keeps a score graphic on screen during
   live play; ads never have *this game's* scorebug. No per-network templates: learn it per
   game the way DVR commercial skippers learn channel logos — accumulate edge maps over a
   minute or two of game footage, keep the pixels whose edges persist (the scorebug's fixed
   layout: frame, logos, team abbreviations), then score each frame by how much of that
   mask is present. Deterministic, explainable, needs no training data, <2 ms/frame on CPU.
2. **Scene classifier (secondary).** Frozen image embeddings (CLIP / DINOv2 on the GPU)
   + a small linear probe trained on our own recorded games: "football broadcast" vs
   "everything else". Fixes what the scorebug alone gets wrong (see below).
3. **Stadium-audio detector (optional, only if 1+2 plateau).** Game segments have a
   continuous crowd bed under everything; ads and studio segments don't.
4. **Play-by-play prior (optional).** A timeout / score / punt / end of quarter in the ESPN
   feed means a break is likely soon. A hint for the state machine, never a trigger —
   the feed isn't synchronized tightly enough with the stream to act on directly.

### Errors are not symmetric

| Mistake | Cost | Consequence for design |
| --- | --- | --- |
| Hiding live play (false AD, or slow to return) | **High** — you miss a play | Returning to GAME needs only ~0.5 s of scorebug evidence. When unsure, show the game. |
| Leaking a few seconds of an ad | Low | Entering AD requires several seconds of consistent evidence. |

State machine (thresholds tuned on recorded games, not guessed):

- `GAME → AD`: scorebug absent ≥ ~3 s **and** scene classifier doesn't see football; or
  scorebug absent ≥ ~15–20 s regardless (network promos full of football highlights).
- `AD → GAME`: scorebug present for ~3 consecutive frames; or (once the classifier exists)
  confident football scene for ≥ 2 s — catches the stadium "bumper" shots networks show
  for 5–15 s before the scorebug fades back in.
- **Manual override always wins**: one global hotkey forces GAME/AD; auto resumes at the
  next confident transition. Every override is logged as a label (free training data).
- **Picture-in-picture safety net**: the stats screen carries a small muted live thumbnail
  of the stream, so you can see for yourself when the game is back. Toggleable.

Known scorebug-only failure modes, and what covers them: full-screen stat graphics and
some replay wipes hide the scorebug for 3–10 s (→ entry delay + classifier veto); the
return-from-break bumper has no scorebug (→ classifier; PiP); halftime studio show looks
like one long ad (→ classifier can tell studio from ads; until then a "halftime" hotkey).

### Measure, don't guess: recorder + offline evaluator

You already label ad breaks by hand every game (mute + hide). Phase 1 turns that into a
single hotkey **and records it**: downscaled frames (~2–4 fps, 640×360 JPEG, under 1 GB
per game) plus a timeline of your toggles. Every game watched becomes a labeled dataset. An
offline evaluator replays recorded sessions through any detector config and reports:

- **game-seconds hidden** (the number that must be ≈ 0),
- ad-seconds leaked, false switches, latency of each transition,
- a contact sheet of every disagreement, so label mistakes and detector mistakes can be
  told apart at a glance.

Detectors run in **shadow mode** (log decisions, take no action) until the numbers say
they're ready. Prior hobby projects stalled exactly here — hand-collecting training audio
was "monotonous"; making labels a side effect of normal use is the fix.

## The stats screen

Data: ESPN's public JSON API (no key). **Verified from this PC on 2026-09-19** with three
games in progress:

- `https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard`
  — add `?groups=80&limit=300` for all FBS (75 games today vs 22 in the default view).
  Per game: status/clock/period, broadcast network, and a live `situation` (down &
  distance, yard line, possession, timeouts, red zone, last play).
- `.../summary?event=<id>` — `winprobability` (one point per play), `drives` (every play
  with text, clock, score, and a **`wallclock` timestamp**), `boxscore` (team + player
  stats), `leaders`, `scoringPlays`, `pickcenter`/`odds`. ~44 KB gzipped per poll.
- Quirk found today: `site.api.espn.com` returns **403 to browser-like User-Agents** (and
  PowerShell's default) but 200 to plain clients; `site.web.api.espn.com` accepts both.
  The API is unofficial and can change without notice → keep it behind one small interface.
  Backup source if it ever breaks: CollegeFootballData.com (free key; check tier limits then).

Panels: score header (clock, possession, down & distance, timeouts) · win-probability
chart · current/last drive play list · team stat comparison · player leaders · scoring
summary · betting line · "around the country" ticker (live scores, close games late,
upsets) · break timer (elapsed vs typical break length) · PiP live thumbnail.

**Spoiler guard.** YouTube TV runs roughly 30–60 s behind the stadium and ESPN's feed is
usually ahead of it, so the stats screen could show a touchdown you haven't seen yet. Each
play's `wallclock` lets us show only plays older than *now − stream delay*. Start with a
delay slider (default ~45 s); later estimate it automatically by reading the game clock
off the scorebug and matching it to the play-by-play.

## Risks — settled in Phase 0 (2026-09-19, Utah State @ Utah on FOX, live)

Each spike is a script in `tools/` that prints PASS/FAIL and writes evidence to `spike_out/`
(git-ignored). All of it was measured with Chrome's graphics acceleration **off** (the state
Chrome was already in) and Chrome on the primary 1920×1080 screen.

| # | Question | Result | What was observed |
| --- | --- | --- | --- |
| S1 | Can we capture YouTube TV's video from Chrome, or does DRM give black frames? | **PASS** — no black frames, maximized window and fullscreen | `spike_capture.py`: video area mean luminance 80–130, 0 flat-black frames, picture changing on every sample; PNGs checked by eye (live play with the FOX scorebug). Works through **Windows Graphics Capture** (window, by HWND) and **DXGI duplication** (dxcam). WGC at the full ~50 fps costs ~0.2 of a CPU core in Python; DXGI pulled at 2 fps costs ~0.01–0.05. `windows-capture`'s own DXGI session sometimes returns an **all-black first frame** (edge to edge, toolbar included — an empty frame, not DRM); dxcam never did. **Not tested: acceleration ON.** |
| S2 | Does Chrome keep rendering video while our overlay covers it? | **PASS** — 60 s, fullscreen | `spike_occlusion.py`: frameless topmost no-activate Qt **tool** window (ex-style `0x08000088` = TOOLWINDOW \| NOACTIVATE \| TOPMOST, not layered) + `WDA_EXCLUDEFROMCAPTURE`, painted magenta, covering fullscreen Chrome. Under it the picture changed on 118/118 half-second ticks (WGC) and 117/118 (DXGI); WGC kept receiving **52.5 fps** (58.5 before) so Chrome never throttled; magenta fraction in every capture = 0.0000; foreground window stayed Chrome; Chrome stayed fullscreen with the same rect. `--disable-features=CalculateNativeWinOcclusion` was **not needed**. Contact sheet checked by eye: game clock runs 12:03 → 11:57 underneath the overlay. Quirk: `GetWindowDisplayAffinity` reads 0 while the window is hidden, though the setting survives hide/show — only verify it while shown. *Not yet run against a maximized (non-fullscreen) window; Phase 1's recorder shows this as a side effect.* |
| S3 | Can we mute only Chrome, cleanly? Does loopback keep hearing the stream while muted? | **PASS** | `spike_mute.py`: Chrome's audio process holds a session on **every playback device** (4 here, one active) → enumerate all active render endpoints, not just the default. Muting all 4 takes 11–16 ms; speakers' peak meter drops to exactly 0.0 and returns on unmute; no other app's mute flag changed. **Chrome's own session peak meter is pre-mute: it kept reading ~0.16–0.19 while muted** → a free, driver-independent loudness envelope of the muted stream. WASAPI loopback (Realtek): hears **nothing** during a *session* mute (RMS 0.0), hears the stream at full level during an *endpoint* mute (0.051 vs 0.043 unmuted). So loopback is useless for audio detection as long as we session-mute; the session meter, or a virtual-cable passthrough, are the options if Phase 5 ever needs audio. Windows persists per-app mute, so the app must unmute on exit and at startup. |
| S4 | Does the Chrome window title on YouTube TV name the game? | **NO** | `spike_title.py`: title stayed `Home - YouTube TV - Google Chrome` for the whole game, windowed and fullscreen (it names the YouTube TV section, not the programme). → Game is chosen with `--game <team>` against ESPN's scoreboard (works today: 75 FBS games, lookup by abbreviation/name). Auto-match later by OCR of the scorebug, or of the player's own title ("Utah State at Utah" is drawn over the video when the controls are up). |

The overlay itself is excluded from screen capture (`SetWindowDisplayAffinity`), so the
detector always sees the stream underneath it, never our own stats screen — confirmed for
both capture paths in S2.

**A detection data point S2 handed us for free.** In that one minute of *live* coverage the
scorebug was off screen for roughly 25–30 s in a row (a replay, then a coach lower-third
that replaces the scorebug), while the **"FOX CFB" network bug in the top-right corner
never left**. Two consequences for Phase 3, to be checked against recorded sessions rather
than assumed: (1) the "scorebug absent ≥ 15–20 s ⇒ AD regardless" rule would have hidden
live coverage here, so it must not act alone; (2) the persistent-edge learner should keep
the corner network bug as a **separate mask** — ads don't carry it, replays and graphics
do — which is exactly the veto the scorebug needs.

## Phases

Each phase ends with something usable on a real game day.

**Phase 0 — Feasibility spikes** — ✅ **done 2026-09-19** (results in the table above).
S1–S4. *Exit met:* PNGs of the game captured from Chrome (windowed + fullscreen); the
overlay-covered capture kept updating for 60 s; Chrome mutes/unmutes from a script.

**Phase 1 — One-key ad mode + recorder** — 🟡 **built 2026-09-19; exit needs a full game.**
Global hotkey toggles AD mode: mute Chrome, show the overlay (placeholder: score + break
timer). Recorder writes frames + toggle timeline to `sessions/<date>_<label>/`. *Exit:* a
full game watched using only the hotkey — already better than mute-and-hide — and a labeled
session on disk.

What exists (`.\run.cmd --game <team>`, details in README.md):

- `RegisterHotKey` listener on its own thread: **F9** toggles, **Ctrl+Shift+F9** quits.
  Measured with Chrome fullscreen and focused: key → muted + overlay in **46 ms**, key →
  game back in **14 ms**.
- Overlay = the exact window recipe S2 validated, showing break timer, ESPN score held back
  by the spoiler delay (45 s, simple snapshot ageing until Phase 2's per-play `wallclock`),
  and — **pulled forward from Phase 4 — the live PiP thumbnail**: without it the viewer
  can't tell when the game is back, so the "back to GAME" labels would be late and useless.
- The overlay yields only to a *visible* foreground window that overlaps the stream; it stays
  put for windows on the other monitor.
- Recorder: WGC frames at ~4 fps (`minimum_update_interval` 240 ms → 4.18 fps measured),
  640 px wide, JPEG q75 ≈ 32–40 KB → ~1.5 GB per 3.5 h game (4 fps chosen over 2 so the
  evaluator can replay the state machine's sub-second rules faithfully; `--fps 2` exists).
  Saves nothing while the captured window's title isn't the stream's. Checked on a recorded
  frame: FOX's scorebug is fully legible at that size (scores, `1st 5:35`, play clock, down
  & distance) — enough for edge matching, and plausibly for the later clock OCR.
- Cost while recording: ~5 % of one core and ~145 MB, next to Chrome's ~107 % of a core
  (software video decode, since graphics acceleration is off).
- The WGC source follows size changes without a hiccup (test window resized, sent
  fullscreen on the ultrawide and back mid-capture: frame size followed each time, largest
  gap 0.23 s) — so toggling the player's fullscreen mid-game doesn't break a session.
- A second instance, or a hotkey another program owns, fails at start-up with a clear
  message and exit code 2, before any session folder is created.
- Chrome is unmuted on every exit path (quit hotkey, Ctrl+C, console close) and at start-up.
- `FileReplaySource` replays a session through the live-capture interface;
  `tools/session_info.py` is the first consumer (segments, gaps, label-vs-timeline check,
  contact sheet per key press). `tools/smoke_app.py` runs the real app end to end with
  injected key presses (16 checks, all pass); its sessions are marked `SYNTHETIC_LABELS`.

Learned while building it:

- **An invisible system window can own the foreground.** `GameInputSvc.exe` (SYSTEM) held it
  with a hidden 136×39 window for minutes while fullscreen Chrome was being watched. Any
  rule keyed on "the foreground window" must ignore windows that aren't visible. Side effect:
  Windows silently drops *injected* input in that state (UIPI) — physical keys are fine, only
  the smoke test has to wait for a normal foreground window.
- **The player's own controls dim the bottom of the picture** for ~3 s after any mouse
  movement — exactly where FOX puts the scorebug. The scorebug matcher has to tolerate that.
- The very first synthetic "ad" window happened to contain a **Big 12 promo made of football
  highlights with no scorebug** — the predicted hard case is common, not hypothetical.

**Phase 2 — Stats dashboard.** ESPN client (scoreboard + summary poller, gzip, polite
intervals, spoiler delay via `wallclock`), game picker, the panels above as a local
HTML/JS page inside the overlay. *Exit:* ad-mode screen shows live, correctly delayed data.

**Phase 3 — Scorebug detector in shadow mode + evaluator.** Static-edge mask learner,
matcher, state machine; `evaluate.py` over recorded sessions; live shadow logging against
your manual toggles. *Exit:* on ≥ 2 recorded games from different networks: game-seconds
hidden < ~5 per game, > 90 % of ad time covered.

**Phase 4 — Auto mode.** Detector drives mute + overlay; hotkey becomes the override; PiP
thumbnail; overrides logged as labels. *Exit:* a full game hands-off with at most a couple
of overrides.

**Phase 5 — Learned classifier (and audio only if needed).** Embeddings + linear probe
trained on the recorded sessions, fused into the state machine: faster returns, no false
entries on graphics/replays, halftime handled. Re-run the evaluator to prove each gain.

**Phase 6 — Polish.** Auto game identification, auto stream-delay estimate (scorebug clock
OCR ↔ play-by-play), audio fade instead of hard mute, tray icon + settings, start with
Chrome, upset / close-game alerts, optional multiview support.

## Stack

Python 3.11 in a venv — fastest iteration on the part that's actually uncertain (vision +
ML), and every Windows piece has a solid binding. Revisit a native port only if this ever
needs distributing.

| Concern | Choice |
| --- | --- |
| Capture | **`windows-capture` 2.x — Windows Graphics Capture of the Chrome window by HWND** (won S1/S2). Follows the window across monitors and sizes, never contains other windows, notifications or our overlay, and `minimum_update_interval` throttles delivery so a 2–4 fps consumer doesn't pay for 50 fps (measured: unthrottled 44 fps / 0.40 core; 250 ms → 3.5 fps / 0.09 core; 500 ms → 1.8 fps / 0.08 core — it is a *minimum* interval, so delivery lands a little under the nominal rate). `dxcam` 0.3 (DXGI duplication) also passed and stays as the fallback, but it grabs a *screen region of one output* — anything on top of Chrome lands in the frame, and with two monitors the output has to be chosen. `bettercam` not needed. |
| Vision | OpenCV + NumPy (scorebug); PyTorch + open_clip / DINOv2 → ONNX Runtime later (classifier) |
| Mute | `pycaw` (per-app audio session, on every active playback device). `soundcard` is installed only for the S3 loopback experiment. |
| Overlay | PySide6 frameless, always-on-top, no-activate tool window; `QWebEngineView` hosting the dashboard; capture-excluded |
| Dashboard | Plain HTML/CSS/JS + a small chart lib, fed over a local websocket (also openable in a normal browser for debugging) |
| Data | `httpx` → ESPN, behind a `StatsProvider` interface |
| Hotkeys | Win32 `RegisterHotKey` (works while Chrome is fullscreen and focused) |

```
FootballAdBlocker/
  PLAN.md  README.md  run.cmd  requirements.txt  config.example.toml
  adblock/
    app.py              wires it together: state (GAME/AD), hotkeys, overlay rules, heartbeat
    capture.py          WgcWindowSource (live) + FileReplaySource (same interface, for the evaluator)
    recorder.py         SessionWriter (on-disk format) + RecordingThread (privacy guard, frame rate)
    hotkeys.py          RegisterHotKey listener          config.py   defaults < config.toml < CLI
    win32.py            ctypes helpers: find the stream window, rects, capture exclusion
    detect/             (Phase 3+) scorebug.py · scene.py · audio.py · fusion.py (state machine)
    actuators/          audio_mute.py · overlay.py
    data/espn.py        scoreboard lookup, per-game poller, spoiler delay
  dashboard/            (Phase 2) index.html · app.js · styles.css
  tools/                spike_*.py (Phase 0) · smoke_app.py · session_info.py · preview_overlay.py
                        · unmute_chrome.py · later: evaluate.py · label_review.py
  sessions/             recorded games (git-ignored)      spike_out/   spike evidence (git-ignored)
  tests/                unittest: hotkeys, session round-trip, ESPN parsing, spoiler delay
```

## Defaults chosen (change any of these)

- Bias: never hide live play; tolerate a few leaked ad seconds.
- Overlay covers the Chrome window's rectangle (= whole screen when fullscreen).
- Halftime: treated as a break until the classifier can tell studio from ads; hotkey to watch it.
- One game at a time first; YouTube TV multiview later.
- Personal-use tool: frames recorded for training stay local and low-res.

## Prior art, and what it taught

- **Comskip / MythTV commflag** — logo-persistence + black-frame/silence detection on
  *recordings*; the logo-learning idea carries straight over to live scorebugs.
- **muteButton** — audio-only ML on football; stalled on hand-collecting training data →
  our recorder makes labels a by-product of normal use.
- **MuteLBtv** — mutes MLB.tv by polling the league API for between-innings, with a manual
  `--offset` for stream delay → data feeds can't be the trigger for football (breaks aren't
  that structured), and delay sync matters; ESPN's per-play `wallclock` gives us that.
