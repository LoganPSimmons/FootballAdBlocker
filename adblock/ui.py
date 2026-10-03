"""Control window + tray icon.

Closing the window quits the app - every thread, the image model, the hotkeys - and leaves
Chrome unmuted; that is what a viewer expects of a close button (the first one did). Minimize is the
way to get the window out of sight while it keeps working. ``close_to_tray = true`` in
config.toml restores the other behaviour: close hides the window, the tray icon keeps it alive.
"""

from __future__ import annotations

import json
import os
import threading

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from adblock import config as config_module
from adblock.data.espn import EspnClient, GameSnapshot, GameStats
from adblock.detect.detector import AD, GAME
from adblock.stats_widgets import BOTH, TeamComparison, TeamPanel, WinOddsChart, recent_plays, scoring_summary

Qt = QtCore.Qt
STATE_FILE = config_module.ROOT / "ui_state.json"  # last game picked, window position (git-ignored)
PREVIEW_SIZE = (480, 270)
COLOURS = {GAME: "#2e9f5b", AD: "#d2453d"}

STYLE = """
#window { background: #0f141a; }
QLabel, QCheckBox { color: #d9e1ea; font: 14px 'Segoe UI'; }
#pill { color: white; font: 700 22px 'Segoe UI'; border-radius: 8px; padding: 6px 18px; }
#status { color: #9fb0c3; }
#preview { background: #000; border: 1px solid #2a3440; }
#session, #legend { color: #66768a; font-size: 12px; }
QPushButton { color: #e8edf2; background: #1b2530; border: 1px solid #3a4856; border-radius: 6px;
              font: 14px 'Segoe UI'; padding: 8px 14px; }
QPushButton:hover { background: #26405a; }
QPushButton:disabled { color: #56616e; background: #141a21; border-color: #222b35; }
#sayAd, #sayGame { font: 600 17px 'Segoe UI'; padding: 16px; }
#sayAd:enabled { border-color: #d2453d; } #sayGame:enabled { border-color: #2e9f5b; }
QComboBox { color: #e8edf2; background: #1b2530; border: 1px solid #3a4856; border-radius: 6px; padding: 6px; font: 14px 'Segoe UI'; }
QComboBox QAbstractItemView { color: #e8edf2; background: #1b2530; selection-background-color: #26405a; }
QTableWidget { color: #e8edf2; background: #121a22; alternate-background-color: #172029; border: 1px solid #2a3440;
               border-radius: 6px; font: 13px 'Segoe UI'; gridline-color: #2a3440; }
QHeaderView::section { color: #9fb0c3; background: #1b2530; border: none; padding: 4px; font: 600 12px 'Segoe UI'; }
#statsScore { color: white; font: 700 26px 'Segoe UI'; }
#statsStatus, #leaders { color: #9fb0c3; font: 13px 'Segoe UI'; }
#groupTitle { color: #66768a; font: 600 12px 'Segoe UI'; letter-spacing: 2px; }
#panelTitle { color: #66768a; font: 600 12px 'Segoe UI'; letter-spacing: 2px; margin-top: 6px; }
#plays, #scoring { color: #d9e1ea; font: 13px 'Segoe UI'; }
QScrollArea { border: none; background: #0f141a; }
"""


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**values) -> None:
    try:
        STATE_FILE.write_text(json.dumps({**load_state(), **values}, indent=2), encoding="utf-8")
    except OSError:
        pass


def dot_icon(colour: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setBrush(QtGui.QColor(colour))
    painter.setPen(QtGui.QPen(QtGui.QColor("#0f141a"), 4))
    painter.drawEllipse(6, 6, 52, 52)
    painter.end()
    return QtGui.QIcon(pixmap)


class ControlWindow(QtWidgets.QWidget):
    games_loaded = QtCore.Signal(list)  # list[GameSnapshot]; emitted from the ESPN thread
    hidden_to_tray = QtCore.Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("window")
        self.setWindowTitle("Football Ad Blocker")
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        self.pill = QtWidgets.QLabel()
        self.pill.setObjectName("pill")
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.status, 1)

        self.preview = QtWidgets.QLabel()
        self.preview.setObjectName("preview")
        self.preview.setFixedSize(PREVIEW_SIZE[0] + 2, PREVIEW_SIZE[1] + 2)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        legend = QtWidgets.QLabel("Learned graphics on the analysed picture: green = on screen, red = missing, grey = not trusted yet.")
        legend.setObjectName("legend")
        legend.setWordWrap(True)

        self.say_ad = QtWidgets.QPushButton("That's an AD\nhide it")
        self.say_ad.setObjectName("sayAd")
        self.say_game = QtWidgets.QPushButton("That's the GAME\nshow it")
        self.say_game.setObjectName("sayGame")
        self.say_ad.clicked.connect(lambda: controller.correct(AD, "window_button"))
        self.say_game.clicked.connect(lambda: controller.correct(GAME, "window_button"))
        corrections = QtWidgets.QHBoxLayout()
        corrections.addWidget(self.say_ad)
        corrections.addWidget(self.say_game)

        self.auto = QtWidgets.QCheckBox("Hide ads automatically")
        self.auto.setChecked(controller.auto)
        self.auto.toggled.connect(controller.set_auto)
        self.pip = QtWidgets.QCheckBox("Live thumbnail on the ad screen")
        self.pip.setChecked(controller.config.pip)
        self.pip.toggled.connect(controller.set_pip)
        self.graphics = QtWidgets.QCheckBox("Use learned graphics")
        self.graphics.setToolTip("The broadcast's own graphics (scorebug, network logo), learned while you watch: while one is "
                                 "on screen nothing is hidden, and when one comes back the picture is back at once. Untick to "
                                 "let the image model decide alone. Without the image model they are the only detector and stay on.")
        self.graphics.setChecked(controller.config.graphics)
        self.graphics.toggled.connect(controller.set_graphics)
        options = QtWidgets.QGridLayout()
        options.addWidget(self.auto, 0, 0)
        options.addWidget(self.pip, 0, 1)
        options.addWidget(self.graphics, 1, 0)

        self.games = QtWidgets.QComboBox()
        self.games.addItem("Score on the ad screen: none", ("", ""))
        self.games.activated.connect(self.pick_game)
        self.games_loaded.connect(self.fill_games)

        self.session = QtWidgets.QLabel()
        self.session.setObjectName("session")
        relearn = QtWidgets.QPushButton("Relearn graphics")
        relearn.setToolTip("Forget what was learned about this broadcast's scorebug and logos and learn it again.")
        relearn.clicked.connect(controller.relearn)
        folder = QtWidgets.QPushButton("Open recordings")
        folder.clicked.connect(lambda: os.startfile(controller.config.sessions_path))  # noqa: S606 - our own folder
        quit_button = QtWidgets.QPushButton("Quit")
        quit_button.clicked.connect(controller.app.quit)
        self.stats_window = StatsWindow(controller)
        stats_button = QtWidgets.QPushButton("Game stats")
        stats_button.setToolTip("Win odds after every play, both teams compared or one team's numbers, scoring and recent plays "
                                "- all held back by the spoiler delay.")
        stats_button.clicked.connect(self.show_stats)
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(stats_button)
        footer.addWidget(relearn)
        footer.addWidget(folder)
        footer.addStretch(1)
        footer.addWidget(quit_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(legend)
        layout.addLayout(corrections)
        layout.addLayout(options)
        layout.addWidget(self.games)
        layout.addWidget(self.session)
        layout.addLayout(footer)
        self.setFixedWidth(PREVIEW_SIZE[0] + 40)

        state = load_state()
        if "window_pos" in state and QtGui.QGuiApplication.screenAt(QtCore.QPoint(*state["window_pos"])):
            self.move(*state["window_pos"])
        else:
            self.move_off_the_stream()
        self.wanted_event = state.get("event_id", "")
        threading.Thread(target=self.load_games, name="espn-games", daemon=True).start()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def show_stats(self) -> None:
        window = self.stats_window
        if not window.isVisible():
            window.move(self.x() + self.width() + 16, self.y())
            if QtGui.QGuiApplication.screenAt(window.pos()) is None:
                window.move(self.x(), self.y())
        window.show()
        window.raise_()
        window.activateWindow()

    def move_off_the_stream(self) -> None:
        """First launch: open on a screen the stream is *not* on, if there is one. A window in
        front of the stream makes the ad screen step aside (the rule is the same for every app)."""
        screens = QtGui.QGuiApplication.screens()
        stream = self.controller.stream
        if stream is not None:
            centre = QtCore.QPoint((stream.rect[0] + stream.rect[2]) // 2, (stream.rect[1] + stream.rect[3]) // 2)
            busy = QtGui.QGuiApplication.screenAt(centre)
            screens = [screen for screen in screens if screen is not busy] or screens
        area = screens[0].availableGeometry()
        self.move(area.x() + 60, area.y() + 60)

    # --- game list ------------------------------------------------------------------------------
    def load_games(self) -> None:
        try:
            client = EspnClient()
            games = client.scoreboard()
            client.close()
        except Exception:  # offline, or ESPN changed: the score is a nicety, never a blocker
            games = []
        self.games_loaded.emit(games)

    def fill_games(self, games: list[GameSnapshot]) -> None:
        order = {"in": 0, "pre": 1, "post": 2}
        for game in sorted(games, key=lambda g: (order.get(g.state, 3), g.short_name)):
            tag = {"in": "LIVE", "pre": "later", "post": "final"}.get(game.state, "")
            self.games.addItem(f"{game.short_name}   {game.network}   {tag}".strip(), (game.event_id, game.short_name))
        current = self.controller.scores.event_id if self.controller.scores else ""
        index = next((i for i in range(self.games.count()) if self.games.itemData(i)[0] == (current or self.wanted_event)), 0)
        self.games.setCurrentIndex(index)
        if index and not self.controller.scores:  # restore last session's pick
            self.pick_game(index)

    def pick_game(self, index: int) -> None:
        event_id, label = self.games.itemData(index)
        self.controller.set_game("", event_id, label)
        save_state(event_id=event_id)

    # --- live view ------------------------------------------------------------------------------
    def refresh(self) -> None:
        c = self.controller
        self.pill.setText(c.state)
        self.pill.setStyleSheet(f"background: {COLOURS[c.state]};")
        self.status.setText(c.status())
        self.say_ad.setEnabled(c.state != AD)
        self.say_game.setEnabled(c.state != GAME)
        if self.auto.isChecked() != c.auto:
            self.auto.setChecked(c.auto)
        model = c.model_in_use()  # without the model the graphics are the only detector: the box is on and locked
        self.graphics.setEnabled(model)
        wanted = c.config.graphics if model else True
        if self.graphics.isChecked() != wanted:
            self.graphics.blockSignals(True)  # showing the forced state is not the viewer choosing it
            self.graphics.setChecked(wanted)
            self.graphics.blockSignals(False)
        where = c.writer.dir.name if c.writer else "not recording"
        frames = c.writer.frame_count if c.writer else 0
        self.session.setText(f"Recording: {where}   ·   {frames} frames   ·   {c.corrections} correction(s)")
        if self.isVisible():
            picture = c.preview()
            if picture is not None:
                self.show_picture(picture)

    def show_picture(self, image_bgr: np.ndarray) -> None:
        height, width = image_bgr.shape[:2]
        data = np.ascontiguousarray(image_bgr)
        image = QtGui.QImage(data.data, width, height, width * 3, QtGui.QImage.Format.Format_BGR888)
        self.preview.setPixmap(QtGui.QPixmap.fromImage(image).scaled(
            *PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        save_state(window_pos=[self.x(), self.y()])
        if self.controller.config.close_to_tray:
            event.ignore()  # opted in: the blocker keeps running from the tray icon
            self.hide()
            self.hidden_to_tray.emit()
        else:
            event.accept()
            self.stats_window.close()
            self.controller.app.quit()  # shutdown() unmutes Chrome and saves the session


class StatsWindow(QtWidgets.QWidget):
    """Game stats, held back by the spoiler delay: win odds after every play, both teams side
    by side or one team's own numbers and players, the scoring summary and the last plays."""

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("window")
        self.setWindowTitle("Game stats - Football Ad Blocker")
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.resize(760, 900)
        self.shown_key: tuple | None = None
        self.perspective = BOTH

        self.score = QtWidgets.QLabel("Game stats")
        self.score.setObjectName("statsScore")
        self.status = QtWidgets.QLabel("Pick a game in the main window; stats appear once they are older than the spoiler delay.")
        self.status.setObjectName("statsStatus")
        self.status.setWordWrap(True)
        self.picker = QtWidgets.QComboBox()
        self.picker.addItem("Both teams", BOTH)
        self.picker.activated.connect(self.pick)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.score, 1)
        top.addWidget(self.picker)

        self.odds = WinOddsChart()
        self.odds.setFixedHeight(230)
        self.comparison = TeamComparison()
        self.team = TeamPanel()
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.comparison)
        self.stack.addWidget(self.team)
        self.scoring = QtWidgets.QLabel()
        self.scoring.setObjectName("scoring")
        self.scoring.setWordWrap(True)
        self.plays = QtWidgets.QLabel()
        self.plays.setObjectName("plays")
        self.plays.setWordWrap(True)

        def title(text: str) -> QtWidgets.QLabel:
            label = QtWidgets.QLabel(text)
            label.setObjectName("panelTitle")
            return label

        content = QtWidgets.QWidget()
        content.setObjectName("window")
        body = QtWidgets.QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(8)
        body.addWidget(title("WIN ODDS AFTER EVERY PLAY"))
        body.addWidget(self.odds)
        body.addWidget(title("TEAM STATS"))
        body.addWidget(self.stack)
        body.addWidget(title("SCORING"))
        body.addWidget(self.scoring)
        body.addWidget(title("LAST PLAYS"))
        body.addWidget(self.plays)
        body.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 10, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.status)
        layout.addWidget(scroll, 1)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)

    def pick(self, index: int) -> None:
        self.perspective = self.picker.itemData(index) or BOTH
        self.shown_key = None
        self.refresh()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt name
        self.shown_key = None
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        if not self.isVisible():
            return
        stats: GameStats | None = self.controller.stats_current()
        if stats is None:
            waiting = self.controller.stats_status()
            self.status.setText(waiting)
            return
        key = (stats.event_id, stats.fetched_at, len(stats.plays), self.perspective)
        if key == self.shown_key:
            return
        self.shown_key = key
        if self.picker.count() == 1:
            self.picker.addItem(f"{stats.away.location} {stats.away.name}", stats.away.abbreviation)
            self.picker.addItem(f"{stats.home.location} {stats.home.name}", stats.home.abbreviation)
        self.score.setText(stats.score_line())
        self.status.setText(f"{stats.status_detail}   ·   {self.controller.config.stream_delay_s:.0f} s behind the feed on purpose (spoiler guard)")
        self.odds.set_stats(stats, self.perspective)
        if self.perspective == BOTH:
            self.comparison.set_stats(stats)
            self.stack.setCurrentWidget(self.comparison)
        else:
            self.team.set_stats(stats, self.perspective)
            self.stack.setCurrentWidget(self.team)
        self.scoring.setText(scoring_summary(stats))
        self.plays.setText(recent_plays(stats))


class Tray(QtCore.QObject):
    def __init__(self, controller, window: ControlWindow) -> None:
        super().__init__()
        self.controller, self.window = controller, window
        self.icons = {state: dot_icon(colour) for state, colour in COLOURS.items()}
        self.icon = QtWidgets.QSystemTrayIcon(self.icons[GAME])
        menu = QtWidgets.QMenu()
        menu.addAction("Open Football Ad Blocker", self.open)
        menu.addAction("Game stats", window.show_stats)
        menu.addSeparator()
        self.say_ad = menu.addAction("That's an AD - hide it", lambda: controller.correct(AD, "tray"))
        self.say_game = menu.addAction("That's the GAME - show it", lambda: controller.correct(GAME, "tray"))
        self.auto = menu.addAction("Hide ads automatically")
        self.auto.setCheckable(True)
        self.auto.toggled.connect(controller.set_auto)
        menu.addSeparator()
        menu.addAction("Quit", controller.app.quit)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self.clicked)
        self.icon.show()
        self.told = False
        window.hidden_to_tray.connect(self.explain_once)
        self.shown_state = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def open(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def clicked(self, reason) -> None:
        if reason in (QtWidgets.QSystemTrayIcon.ActivationReason.Trigger, QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open()

    def explain_once(self) -> None:
        if not self.told:
            self.told = True
            self.icon.showMessage("Football Ad Blocker is still running",
                                  "It keeps hiding ads from the tray. Right-click the icon to quit.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)

    def refresh(self) -> None:
        c = self.controller
        if c.state != self.shown_state:
            self.shown_state = c.state
            self.icon.setIcon(self.icons[c.state])
        self.icon.setToolTip(f"Football Ad Blocker - {c.state} ({'automatic' if c.auto else 'manual'})")
        self.say_ad.setEnabled(c.state != AD)
        self.say_game.setEnabled(c.state != GAME)
        if self.auto.isChecked() != c.auto:
            self.auto.setChecked(c.auto)


def build_ui(app: QtWidgets.QApplication, controller) -> tuple[ControlWindow, Tray]:
    icon_file = config_module.ROOT / "assets" / "app.ico"
    app.setWindowIcon(QtGui.QIcon(str(icon_file)) if icon_file.exists() else dot_icon(COLOURS[GAME]))
    window = ControlWindow(controller)
    tray = Tray(controller, window)
    window.show()
    return window, tray
