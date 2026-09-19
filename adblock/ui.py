"""Control window + tray icon.

Closing the window does not stop anything: the blocker keeps running from the tray (that is
the point - the window is for looking in and correcting, not for babysitting). Quit is in
the window and in the tray menu, and always leaves Chrome unmuted.
"""

from __future__ import annotations

import json
import os
import threading

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from adblock import config as config_module
from adblock.data.espn import EspnClient, GameSnapshot
from adblock.detect.detector import AD, GAME

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
        legend = QtWidgets.QLabel("What the detector looks for: learned graphics in green when on screen, red when missing.")
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
        self.scene = QtWidgets.QCheckBox("Use the image model")
        self.scene.setToolTip("A whole-picture check that tells a studio show or a replay from a commercial when no "
                              "learned graphic is on screen. It can stop the ad screen from coming up, never cause it.")
        self.scene.setChecked(controller.use_scene)
        self.scene.setEnabled(controller.scene is not None)
        self.scene.toggled.connect(controller.set_scene)
        options = QtWidgets.QGridLayout()
        options.addWidget(self.auto, 0, 0)
        options.addWidget(self.pip, 0, 1)
        options.addWidget(self.scene, 1, 0)

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
        footer = QtWidgets.QHBoxLayout()
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
        event.ignore()  # closing the window leaves the blocker running in the tray
        self.hide()
        self.hidden_to_tray.emit()


class Tray(QtCore.QObject):
    def __init__(self, controller, window: ControlWindow) -> None:
        super().__init__()
        self.controller, self.window = controller, window
        self.icons = {state: dot_icon(colour) for state, colour in COLOURS.items()}
        self.icon = QtWidgets.QSystemTrayIcon(self.icons[GAME])
        menu = QtWidgets.QMenu()
        menu.addAction("Open Football Ad Blocker", self.open)
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
    app.setWindowIcon(dot_icon(COLOURS[GAME]))
    window = ControlWindow(controller)
    tray = Tray(controller, window)
    window.show()
    return window, tray
