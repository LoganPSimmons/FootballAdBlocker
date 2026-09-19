"""Phase 1: one-key ad mode + session recorder.

    .\\.venv\\Scripts\\python.exe -m adblock.app --game utah

Press the hotkey (default F9) when an ad break starts: Chrome is muted and the overlay covers
the stream. Press it again when the game is back. Every press is logged next to low-res
frames of the stream under sessions/<date>_<label>/ - the labelled data later phases train
and evaluate on. Ctrl+Shift+F9 (or Ctrl+C in the console) quits and always unmutes Chrome.
"""

from __future__ import annotations

import argparse
import ctypes
import signal
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from adblock import __version__, config as config_module, win32
from adblock.actuators import audio_mute
from adblock.actuators.overlay import Overlay
from adblock.capture import WgcWindowSource
from adblock.data.espn import ScorePoller
from adblock.hotkeys import HotkeyError, HotkeyListener
from adblock.recorder import RecordingThread, SessionWriter

GAME, AD = "GAME", "AD"
TICK_MS = 250
REATTACH_EVERY_S = 2.0
REMUTE_EVERY_S = 2.0

_console_handler = None  # keeps the ctypes callback alive for the life of the process


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


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


class Controller(QtCore.QObject):
    hotkey = QtCore.Signal(str, float)  # (action, perf_counter when pressed); emitted from the hotkey thread

    def __init__(self, app: QtWidgets.QApplication, config: config_module.Config) -> None:
        super().__init__()
        self.app, self.config = app, config
        self.state = GAME
        self.break_started = 0.0
        self.stream: win32.WindowInfo | None = None
        self.source: WgcWindowSource | None = None
        self.last_attach_try = self.last_remute = 0.0
        self.last_geometry: tuple | None = None
        self.last_shown: tuple[str, str] | None = None
        self.closed = False

        self.overlay = Overlay(config.hotkey, config.pip)
        self.writer = SessionWriter(config.sessions_path, config.label or config.game or "session",
                                    config.fps, config.frame_width, config.jpeg_quality) if config.record else None
        self.scores = ScorePoller(config.game, config.stream_delay_s) if config.game else None

        self.hotkey.connect(self.on_hotkey)
        self.listener = HotkeyListener({
            config.hotkey: lambda at: self.hotkey.emit("toggle", at),
            config.quit_hotkey: lambda at: self.hotkey.emit("quit", at),
        })
        self.listener.start_and_wait()  # raises HotkeyError if a combination is taken

        if self.writer:
            self.writer.start(app_version=__version__, hotkey=config.hotkey, title_contains=config.title_contains,
                              game_query=config.game, stream_delay_s=config.stream_delay_s, capture="wgc-window",
                              overlay_capture_excluded=self.overlay.capture_excluded)
        if self.scores:
            self.scores.start()
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
        self.log("capture", status="acquired", hwnd=stream.hwnd, title=stream.title)
        say(f"Watching Chrome window {stream.hwnd:#x}: {stream.title!r}")

    def detach(self, reason: str) -> None:
        if self.source:
            self.source.stop()
        self.stream = self.source = self.last_geometry = None
        self.log("capture", status="lost", reason=reason)
        say(f"Stream window lost ({reason}); waiting for it to come back.")
        self.set_state(GAME, "stream_lost")  # nothing left to cover; never leave Chrome muted

    # --- state ------------------------------------------------------------------------------
    def on_hotkey(self, action: str, pressed_at: float) -> None:
        self.log("hotkey", pressed_at, action=action, state_before=self.state)
        if action == "quit":
            self.app.quit()
        else:
            self.set_state(AD if self.state == GAME else GAME, "hotkey", pressed_at)

    def set_state(self, state: str, source: str, at: float | None = None) -> None:
        if state == self.state:
            return
        self.state = state
        sessions = None
        if state == GAME:
            self.overlay.hide()  # picture first: never keep live play hidden a moment longer
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

    def overlay_wanted(self) -> bool:
        """In AD, cover the stream - unless the user has visibly moved on to something else."""
        if self.state != AD or self.stream is None:
            return False
        hwnd = self.stream.hwnd
        if win32.is_minimized(hwnd) or self.config.title_contains.lower() not in win32.window_title(hwnd).lower():
            return False  # minimized, or that window now shows another tab
        foreground = win32.foreground_window()
        if foreground in (0, hwnd, self.overlay.hwnd) or not win32.is_on_screen(foreground):
            return True
        # Another app the user can see, on top of the stream's area: get out of its way.
        # (A window on the other monitor doesn't overlap, so the overlay stays.)
        return not win32.rects_overlap(win32.window_rect(foreground), win32.window_rect(hwnd))

    def refresh_overlay(self) -> None:
        if not self.overlay_wanted():
            self.overlay.hide()
            return
        self.overlay.set_break_seconds(time.perf_counter() - self.break_started)
        if self.scores is None:
            shown = ("", "no game selected  -  start with  --game <team>")
        elif (snapshot := self.scores.current()) is not None:
            shown = (snapshot.score_line(), f"{snapshot.status_detail}   ·   {snapshot.network}".strip(" ·"))
        else:
            shown = ("", self.scores.status or f"score shows once it is {self.config.stream_delay_s:.0f} s old (spoiler guard)")
        if shown != self.last_shown:  # the overlay can't be screenshotted, so the timeline says what it displayed
            self.last_shown = shown
            self.overlay.set_score(*shown)
            self.log("overlay", score=shown[0], detail=shown[1])
        if self.source and (frame := self.source.latest()) is not None:
            self.overlay.set_thumbnail(frame.image)
        self.overlay.cover(win32.window_rect(self.stream.hwnd))

    # --- heartbeat --------------------------------------------------------------------------
    def tick(self) -> None:
        now = time.perf_counter()
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
            say(f"Session saved: {self.writer.dir}  ({self.writer.frame_count} frames)")


def parse_args(argv: list[str]) -> tuple[config_module.Config, float | None]:
    parser = argparse.ArgumentParser(prog="python -m adblock.app", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="TOML settings file (default: config.toml if present)")
    parser.add_argument("--game", help="team to show the score for, e.g. utah or USU")
    parser.add_argument("--label", help="session folder label (default: the game)")
    parser.add_argument("--hotkey", help="ad-mode toggle (default F9)")
    parser.add_argument("--quit-hotkey", dest="quit_hotkey", help="clean exit (default ctrl+shift+F9)")
    parser.add_argument("--title", dest="title_contains", help="text that identifies the stream's Chrome window")
    parser.add_argument("--fps", type=float, help="recorded frames per second (default 4)")
    parser.add_argument("--delay", dest="stream_delay_s", type=float, help="spoiler guard in seconds (default 45)")
    parser.add_argument("--no-record", dest="record", action="store_false", default=None)
    parser.add_argument("--no-pip", dest="pip", action="store_false", default=None)
    parser.add_argument("--duration", type=float, help="quit by itself after this many seconds (for testing)")
    args = parser.parse_args(argv)
    config = config_module.load(args.config)
    for name, value in vars(args).items():
        if value is not None and hasattr(config, name):
            setattr(config, name, value)
    return config, args.duration


def main(argv: list[str] | None = None) -> int:
    try:
        config, duration = parse_args(sys.argv[1:] if argv is None else argv)
    except (ValueError, FileNotFoundError) as exc:
        print(f"config error: {exc}")
        return 2

    app = QtWidgets.QApplication([])  # first: Qt makes the process per-monitor DPI aware
    app.setQuitOnLastWindowClosed(False)
    try:
        controller = Controller(app, config)
    except HotkeyError as exc:
        print(f"hotkey error: {exc}")
        return 2
    app.aboutToQuit.connect(controller.shutdown)
    signal.signal(signal.SIGINT, lambda *_: app.quit())  # the 250 ms tick gives Python the chance to see Ctrl+C
    unmute_on_console_close()
    if duration:
        QtCore.QTimer.singleShot(int(duration * 1000), app.quit)

    say(f"Football Ad Blocker {__version__}:  {config.hotkey.upper()} = ad mode on/off,  {config.quit_hotkey.upper()} = quit.")
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
