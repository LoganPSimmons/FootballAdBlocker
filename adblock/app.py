"""Football Ad Blocker - the app.

    start_ui.cmd                                   control window + tray icon, no console
    .\\run.cmd [--game utah]                        same, with a console for the log
    .\\run.cmd --headless --no-auto                 no window: hotkeys only (what the smoke test drives)

The detector watches the stream's Chrome window and switches between GAME and AD by itself
(mute Chrome + cover the stream). When it gets it wrong, tell it - "That's the game" on the
overlay, the two buttons in the control window, the tray menu, or F9. A correction takes
effect at once, is held until the picture agrees with it, and is recorded as a label.
Everything lands in sessions/<date>_<label>/ for evaluation and training.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import signal
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from adblock import __version__, config as config_module, win32
from adblock.actuators import audio_mute
from adblock.actuators.overlay import Overlay
from adblock.capture import WgcWindowSource
from adblock.data.espn import ScorePoller
from adblock.detect.detector import AD, GAME, Detector, FusionSettings, Observation
from adblock.hotkeys import HotkeyError, HotkeyListener
from adblock.recorder import RecordingThread, SessionWriter

TICK_MS = 250
REATTACH_EVERY_S = 2.0
REMUTE_EVERY_S = 2.0
NO_FRAMES_S = 2.0  # no analysed frame for this long = the detector can't see the stream

log = logging.getLogger("adblock")
_console_handler = None  # keeps the ctypes callback alive for the life of the process


def say(message: str) -> None:
    log.info(message)


def setup_logging() -> None:
    """Console when there is one, and always logs/app.log (pythonw has no console at all)."""
    folder = config_module.ROOT / "logs"
    folder.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(folder / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S", handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per ESPN poll is noise
    sys.excepthook = lambda kind, value, trace: log.error("unhandled error", exc_info=(kind, value, trace))


def unmute_on_console_close() -> None:
    """Closing the console window kills Python without running any cleanup, and Windows
    remembers a per-app mute forever - so unmute Chrome from the console control handler."""
    global _console_handler

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def handler(event: int) -> bool:
        if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            try:
                import comtypes

                comtypes.CoInitialize()  # the handler runs on a thread of its own
                audio_mute.set_muted(False)
            except Exception:
                pass
        return False  # let the default handling (and Python's Ctrl+C handling) carry on

    _console_handler = handler
    ctypes.WinDLL("kernel32").SetConsoleCtrlHandler(handler, True)


class DetectorThread(threading.Thread):
    """Runs every captured frame through the detector. Like the recorder it never looks at
    the window while it shows another tab: that is reported as "can't see the stream"."""

    def __init__(self, source: WgcWindowSource, detector: Detector, hwnd: int, title_contains: str, emit) -> None:
        super().__init__(name="detector", daemon=True)
        self.source, self.detector, self.hwnd, self.needle, self.emit = source, detector, hwnd, title_contains.lower(), emit

    def run(self) -> None:
        for frame in self.source:
            try:
                if self.needle in win32.window_title(self.hwnd).lower():
                    seen = self.detector.process(frame.image, frame.t, win32.is_fullscreen(self.hwnd))
                else:
                    seen = self.detector.unknown(frame.t)
            except Exception:  # a bad frame must not end detection for the rest of the game
                log.exception("detector failed on a frame")
                continue
            self.emit(seen)


class Controller(QtCore.QObject):
    hotkey = QtCore.Signal(str, float)  # (action, perf_counter when pressed); emitted from the hotkey thread
    observed = QtCore.Signal(object)  # Observation; emitted from the detector thread

    def __init__(self, app: QtWidgets.QApplication, config: config_module.Config) -> None:
        super().__init__()
        self.app, self.config = app, config
        self.state = GAME
        self.state_source = "start"
        self.auto = config.auto
        self.corrections = 0
        self.break_started = 0.0
        self.stream: win32.WindowInfo | None = None
        self.source: WgcWindowSource | None = None
        self.observation: Observation | None = None
        self.observation_at = time.perf_counter()
        self.last_attach_try = self.last_remute = 0.0
        self.last_geometry: tuple | None = None
        self.last_shown: tuple[str, str] | None = None
        self.last_blocker: str | None = None
        self.closed = False

        # Claim the hotkeys before anything touches the disk: a second instance, or a key
        # another program owns, must fail without leaving an empty session folder behind.
        self.hotkey.connect(self.on_hotkey)
        self.listener = HotkeyListener({
            config.hotkey: lambda at: self.hotkey.emit("toggle", at),
            config.quit_hotkey: lambda at: self.hotkey.emit("quit", at),
        })
        self.listener.start_and_wait()  # raises HotkeyError if a combination is taken

        self.detector = Detector(FusionSettings(enter_ad_s=config.enter_ad_s, exit_ad_frames=config.exit_ad_frames))
        self.observed.connect(self.on_observation)
        self.overlay = Overlay(config.hotkey, config.pip)
        self.overlay.show_game.connect(lambda: self.correct(GAME, "overlay_button"))
        self.writer = SessionWriter(config.sessions_path, config.label or config.game or "session",
                                    config.fps, config.frame_width, config.jpeg_quality) if config.record else None
        self.scores: ScorePoller | None = None
        self.game_label = ""

        if self.writer:
            self.writer.start(app_version=__version__, hotkey=config.hotkey, title_contains=config.title_contains,
                              game_query=config.game, stream_delay_s=config.stream_delay_s, capture="wgc-window",
                              auto=config.auto, enter_ad_s=config.enter_ad_s, exit_ad_frames=config.exit_ad_frames,
                              overlay_capture_excluded=self.overlay.capture_excluded)
        if config.game:
            self.set_game(config.game)
        if audio_mute.is_muted():  # a previous run died while muted; GAME means audible
            audio_mute.set_muted(False)
            say("Chrome was muted from an earlier run - unmuted it.")

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)
        self.tick()

    # --- stream window + capture ----------------------------------------------------------
    def log(self, event: str, at: float | None = None, **fields) -> None:
        if self.writer:
            self.writer.log(event, at, **fields)

    def attach(self) -> None:
        stream = win32.find_stream_window(self.config.title_contains)
        if stream is None:
            return
        self.stream = stream
        self.source = WgcWindowSource(stream.hwnd, min_interval_ms=max(50, int(1000 / self.config.fps) - 10))
        if self.writer:
            RecordingThread(self.source, self.writer, lambda: self.state, stream.hwnd, self.config.title_contains).start()
        DetectorThread(self.source, self.detector, stream.hwnd, self.config.title_contains, self.observed.emit).start()
        self.observation_at = time.perf_counter()
        self.log("capture", status="acquired", hwnd=stream.hwnd, title=stream.title)
        say(f"Watching Chrome window {stream.hwnd:#x}: {stream.title!r}")

    def detach(self, reason: str) -> None:
        if self.source:
            self.source.stop()
        self.stream = self.source = self.last_geometry = None
        self.log("capture", status="lost", reason=reason)
        say(f"Stream window lost ({reason}); waiting for it to come back.")
        self.set_state(GAME, "stream_lost")  # nothing left to cover; never leave Chrome muted

    # --- what the viewer can do -------------------------------------------------------------
    def on_hotkey(self, action: str, pressed_at: float) -> None:
        self.log("hotkey", pressed_at, action=action, state_before=self.state)
        if action == "quit":
            self.app.quit()
        else:
            self.correct(AD if self.state == GAME else GAME, "hotkey", pressed_at)

    def correct(self, state: str, via: str, at: float | None = None) -> None:
        """The viewer says what is on screen. Takes effect now, is held until the picture
        agrees, and is the strongest label a session contains."""
        seen = self.observation
        self.corrections += 1
        self.log("correction", at, to=state, via=via, app_state_before=self.state, auto=self.auto,
                 detector_state=seen.state if seen else None, game_like=seen.game_like if seen else None,
                 scores=[round(score, 2) for score in seen.scores] if seen else [])
        self.detector.correct(state)
        self.set_state(state, via, at)

    def set_auto(self, auto: bool) -> None:
        if auto != self.auto:
            self.auto = auto
            self.log("auto", enabled=auto)
            say(f"Automatic detection {'on' if auto else 'off (the detector only watches)'}")

    def set_pip(self, enabled: bool) -> None:
        self.config.pip = enabled
        self.overlay.set_pip_enabled(enabled)

    def set_game(self, query: str, event_id: str = "", label: str = "") -> None:
        """Whose score the overlay shows: a team to look up, or an ESPN event picked from the list."""
        if self.scores:
            self.scores.stop()
        self.scores = ScorePoller(query, self.config.stream_delay_s, event_id=event_id) if (query or event_id) else None
        self.game_label = label or query
        self.last_shown = None
        if self.scores:
            self.scores.start()
        self.log("game", query=query, event_id=event_id, label=self.game_label)

    def relearn(self) -> None:
        self.detector.relearn()
        self.log("relearn")
        say("Forgot the learned graphics; learning them again.")

    # --- state ------------------------------------------------------------------------------
    def on_observation(self, seen: Observation) -> None:
        self.observation, self.observation_at = seen, time.perf_counter()
        if self.writer:
            self.writer.add_detection(seen.t, self.state, seen.state, seen.game_like, seen.override, self.auto, seen.scores)
            if seen.learned and self.source and (frame := self.source.latest()) is not None and self.stream:
                picture = self.detector.picture(frame.image, win32.is_fullscreen(self.stream.hwnd))
                if picture is not None:
                    self.writer.save_graphics(seen.t, picture)
        if seen.learned:
            say(f"Learned graphics: {len(seen.scores)} region(s) " + ", ".join(f"{score:.2f}" for score in seen.scores))
        if self.auto and seen.state != self.state:
            self.set_state(seen.state, "detector")

    def set_state(self, state: str, source: str, at: float | None = None) -> None:
        if state == self.state:
            return
        self.state, self.state_source = state, source
        sessions = None
        if state == GAME:
            self.overlay.hide()  # picture first: never keep live play hidden a moment longer
            self.last_blocker = None  # so the next break logs its overlay_visible afresh
        try:
            sessions = audio_mute.set_muted(state == AD)
        except Exception as exc:  # a COM hiccup must never wedge the state machine
            say(f"audio mute failed: {type(exc).__name__}: {exc}")
        self.last_remute = time.perf_counter()
        self.log("state", at, state=state, source=source, chrome_audio_sessions=sessions)
        if state == AD:
            self.break_started = time.perf_counter() if at is None else at
            self.refresh_overlay()
        latency = f", {1000 * (time.perf_counter() - at):.0f} ms after the key" if at else ""
        say(f"-> {state} ({source}{latency})")

    def overlay_blocker(self) -> str:
        """Why the overlay must stay hidden right now; "" means cover the stream.

        In AD the stream is covered unless the user has put something else in front of it.
        That is decided by z-order, never by focus (see win32.app_window_in_front).
        """
        if self.state != AD or self.stream is None:
            return "no stream window" if self.state == AD else "not in AD"
        hwnd = self.stream.hwnd
        if win32.is_minimized(hwnd):
            return "stream window is minimized"
        if self.config.title_contains.lower() not in win32.window_title(hwnd).lower():
            return "stream window shows another tab"
        in_front = win32.app_window_in_front(hwnd, ignore=(self.overlay.hwnd,))
        if in_front:  # exe only - other apps' window titles are none of the timeline's business
            return f"{win32.process_exe(win32.window_pid(in_front)) or 'another app'} is in front of the stream"
        return ""

    def refresh_overlay(self) -> None:
        blocker = self.overlay_blocker()
        if blocker != self.last_blocker:  # the overlay can't be screenshotted: the timeline is its only witness
            self.last_blocker = blocker
            self.log("overlay_visible", visible=not blocker, reason=blocker)
        if blocker:
            self.overlay.hide()
            return
        self.overlay.set_break_seconds(time.perf_counter() - self.break_started)
        self.overlay.set_reason("detected automatically" if self.state_source == "detector"
                                else "you said so  -  held until the picture agrees" if self.detector.fusion.override == AD
                                else "you said so")
        if self.scores is None:
            shown = ("", "no game selected")
        elif (snapshot := self.scores.current()) is not None:
            shown = (snapshot.score_line(), f"{snapshot.status_detail}   ·   {snapshot.network}".strip(" ·"))
        else:
            shown = ("", self.scores.status or f"score shows once it is {self.config.stream_delay_s:.0f} s old (spoiler guard)")
        if shown != self.last_shown:
            self.last_shown = shown
            self.overlay.set_score(*shown)
            self.log("overlay", score=shown[0], detail=shown[1])
        if self.source and (frame := self.source.latest()) is not None:
            self.overlay.set_thumbnail(frame.image)
        self.overlay.cover(win32.window_rect(self.stream.hwnd))

    # --- what the UI shows ----------------------------------------------------------------------
    def status(self) -> str:
        seen = self.observation
        if self.stream is None:
            return f"Waiting for a Chrome window with '{self.config.title_contains}' in its title."
        if seen is not None and seen.override == AD and not seen.scores:
            return ("You said AD. I haven't learned this broadcast's graphics yet, so I can't tell when the game "
                    "is back: click \"That's the GAME\" when it is.")
        if seen is not None and seen.override:
            return f"You said {seen.override}. Holding that until the picture agrees, then automatic again."
        if seen is None or not seen.scores:
            return "Learning this broadcast's graphics (takes a minute or two of game picture). Nothing is hidden until then."
        if seen.game_like is None:
            return "Can't see the stream right now (other tab, minimized, or a new window size being learned)."
        graphics = f"{sum(score >= 0.6 for score in seen.scores)} of {len(seen.scores)} learned graphics on screen"
        return f"{'Automatic' if self.auto else 'Manual - the detector only watches'}: {graphics}."

    def preview(self):
        """The analysed picture with the learned graphics drawn in (BGR), or the plain frame, or None."""
        if not (self.source and self.stream) or (frame := self.source.latest()) is None:
            return None
        picture = self.detector.picture(frame.image, win32.is_fullscreen(self.stream.hwnd))
        return picture if picture is not None else frame.image

    # --- heartbeat --------------------------------------------------------------------------
    def tick(self) -> None:
        now = time.perf_counter()
        if self.writer and (self.writer.dir / "STOP").exists():  # polite quit request from another process
            self.app.quit()
            return
        if self.stream and (not win32.is_window(self.stream.hwnd) or (self.source and self.source.closed)):
            self.detach("window closed")
        if self.stream is None and now - self.last_attach_try >= REATTACH_EVERY_S:
            self.last_attach_try = now
            self.attach()
        if self.stream:
            geometry = (win32.window_rect(self.stream.hwnd), win32.is_fullscreen(self.stream.hwnd))
            if geometry != self.last_geometry:
                self.last_geometry = geometry
                self.log("window", rect=geometry[0], fullscreen=geometry[1])
            if now - self.observation_at >= NO_FRAMES_S:  # minimized, or the picture froze: can't see the stream
                self.on_observation(self.detector.unknown(now))
        if self.state == AD:
            self.refresh_overlay()
            if now - self.last_remute >= REMUTE_EVERY_S:  # Chrome may open a new audio session mid-break
                self.last_remute = now
                try:
                    audio_mute.set_muted(True)
                except Exception:
                    pass

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.set_state(GAME, "shutdown")
        self.listener.stop()
        if self.scores:
            self.scores.stop()
        if self.source:
            self.source.stop()
        if self.writer:
            self.writer.close()
            say(f"Session saved: {self.writer.dir}  ({self.writer.frame_count} frames, {self.corrections} corrections)")


def parse_args(argv: list[str]) -> tuple[config_module.Config, argparse.Namespace]:
    parser = argparse.ArgumentParser(prog="python -m adblock.app", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="TOML settings file (default: config.toml if present)")
    parser.add_argument("--game", help="team to show the score for, e.g. utah or USU (the control window has a picker)")
    parser.add_argument("--label", help="session folder label (default: the game)")
    parser.add_argument("--hotkey", help="ad-mode toggle / correction (default F9)")
    parser.add_argument("--quit-hotkey", dest="quit_hotkey", help="clean exit (default ctrl+shift+F9)")
    parser.add_argument("--title", dest="title_contains", help="text that identifies the stream's Chrome window")
    parser.add_argument("--fps", type=float, help="analysed and recorded frames per second (default 4)")
    parser.add_argument("--delay", dest="stream_delay_s", type=float, help="spoiler guard in seconds (default 45)")
    parser.add_argument("--enter-ad", dest="enter_ad_s", type=float, help="seconds without graphics before hiding (default 5)")
    parser.add_argument("--no-auto", dest="auto", action="store_false", default=None, help="detector only watches; you toggle")
    parser.add_argument("--no-record", dest="record", action="store_false", default=None)
    parser.add_argument("--no-pip", dest="pip", action="store_false", default=None)
    parser.add_argument("--headless", action="store_true", help="no control window or tray icon: hotkeys only")
    parser.add_argument("--duration", type=float, help="quit by itself after this many seconds (for testing)")
    args = parser.parse_args(argv)
    config = config_module.load(args.config)
    for name, value in vars(args).items():
        if value is not None and hasattr(config, name):
            setattr(config, name, value)
    return config, args


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    try:
        config, args = parse_args(sys.argv[1:] if argv is None else argv)
    except (ValueError, FileNotFoundError) as exc:
        print(f"config error: {exc}")
        return 2

    app = QtWidgets.QApplication([])  # first: Qt makes the process per-monitor DPI aware
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Football Ad Blocker")
    try:
        controller = Controller(app, config)
    except HotkeyError as exc:
        say(f"hotkey error: {exc}")
        if not args.headless:
            QtWidgets.QMessageBox.warning(None, "Football Ad Blocker", f"{exc}\n\nIs Football Ad Blocker already running? "
                                          "Look for its icon in the system tray.")
        return 2
    app.aboutToQuit.connect(controller.shutdown)
    signal.signal(signal.SIGINT, lambda *_: app.quit())  # the 250 ms tick gives Python the chance to see Ctrl+C
    unmute_on_console_close()
    if args.duration:
        QtCore.QTimer.singleShot(int(args.duration * 1000), app.quit)
    if not args.headless:
        from adblock.ui import build_ui

        ui = build_ui(app, controller)  # noqa: F841 - keeps the window and tray icon alive

    say(f"Football Ad Blocker {__version__}: {'automatic' if config.auto else 'manual'}; "
        f"{config.hotkey.upper()} = correct it / toggle, {config.quit_hotkey.upper()} = quit.")
    if controller.writer:
        say(f"Recording to {controller.writer.dir}")
    if controller.stream is None:
        say(f"No Chrome window with '{config.title_contains}' in its title yet - waiting for one.")
    try:
        app.exec()
    finally:
        controller.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
