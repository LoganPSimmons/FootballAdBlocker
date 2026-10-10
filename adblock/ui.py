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
import time
from typing import Callable

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from adblock import config as config_module
from adblock.data.espn import EspnClient, GameSnapshot, GameStats
from adblock.detect.detector import AD, GAME
from adblock.stats_widgets import BOTH, TeamComparison, TeamPanel, WinOddsChart, recent_plays, scoring_summary

Qt = QtCore.Qt
STATE_FILE = config_module.DATA / "ui_state.json"  # last game picked, window position (git-ignored)
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
QLineEdit { color: #e8edf2; background: #121a22; border: 1px solid #3a4856; border-radius: 6px; padding: 6px; font: 14px 'Segoe UI'; }
#searchPopup { background: #1b2530; border: 1px solid #3a4856; }
#searchList { color: #e8edf2; background: #1b2530; border: none; font: 14px 'Segoe UI'; outline: 0; }
#searchList::item { padding: 2px 8px; }
#searchList::item:hover { background: #22303e; }
#searchList::item:selected { background: #26405a; color: white; }
#fieldLabel { color: #9fb0c3; }
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


def field_label(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("fieldLabel")
    return label


SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1  # more text an entry can be found by (the game's full name, say)


class WordFilter(QtCore.QSortFilterProxyModel):
    """Keeps the rows that contain every word typed, in any order, in their text or their SEARCH_ROLE text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.words: list[str] = []

    def set_words(self, text: str) -> None:
        self.words = text.lower().split()
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, row: int, parent: QtCore.QModelIndex) -> bool:  # noqa: N802 - Qt name
        if not self.words:
            return True
        source = self.sourceModel()
        index = source.index(row, 0, parent)
        haystack = f"{source.data(index, Qt.ItemDataRole.DisplayRole) or ''} {source.data(index, SEARCH_ROLE) or ''}".lower()
        return all(word in haystack for word in self.words)


class PopupFrame(QtWidgets.QFrame):
    """The list's own window; it remembers when it was last closed."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.hidden_at = 0.0

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt name
        self.hidden_at = time.monotonic()
        super().hideEvent(event)


class SearchField(QtWidgets.QLineEdit):
    """The search box; the keys that mean the list (arrows, Enter, Escape) are handed to its owner."""

    def __init__(self, owner: "SearchableComboBox", parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.owner = owner

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 - Qt name
        if not self.owner.list_key(event.key()):
            super().keyPressEvent(event)


class SearchableComboBox(QtWidgets.QComboBox):
    """A combo box whose list opens with a search box on top. Type to narrow the list (every word must
    occur somewhere in the entry), Up/Down to move, Enter or a click to pick, Escape to close; typing
    into the closed box opens the list with that letter. The wheel never changes the pick by accident.
    (No event filters: PySide6 calls a Python eventFilter for ChildAdded while the child is still being
    built, which fails with a SystemError - hence the two small subclasses above.)"""

    MAX_ROWS = 12

    def __init__(self, placeholder: str = "Type to search") -> None:
        super().__init__()
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(20)
        self.filter = WordFilter(self)
        self.filter.setSourceModel(self.model())
        self.popup = PopupFrame(self)
        self.popup.setObjectName("searchPopup")
        self.search = SearchField(self, self.popup)
        self.search.setObjectName("search")
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.narrow)
        self.list = QtWidgets.QListView(self.popup)
        self.list.setObjectName("searchList")
        self.list.setModel(self.filter)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.clicked.connect(self.pick)
        box = QtWidgets.QVBoxLayout(self.popup)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(6)
        box.addWidget(self.search)
        box.addWidget(self.list)

    def showPopup(self) -> None:  # noqa: N802 - Qt name
        if time.monotonic() - self.popup.hidden_at < 0.25:  # the click that just closed the list must not reopen it
            return
        self.search.clear()
        self.narrow("")
        row_height = self.list.sizeHintForRow(0) if self.filter.rowCount() else 28
        height = self.search.sizeHint().height() + row_height * max(1, min(self.count(), self.MAX_ROWS)) + 2 * self.list.frameWidth() + 20
        width = max(self.width(), 320)
        below = self.mapToGlobal(QtCore.QPoint(0, self.height()))
        area = (QtGui.QGuiApplication.screenAt(below) or self.screen()).availableGeometry()
        y = below.y() if below.y() + height <= area.bottom() else self.mapToGlobal(QtCore.QPoint(0, 0)).y() - height
        x = min(max(below.x(), area.left()), area.right() - width + 1)
        self.popup.setGeometry(x, max(y, area.top()), width, height)
        self.popup.show()
        self.search.setFocus()

    def hidePopup(self) -> None:  # noqa: N802 - Qt name
        self.popup.hide()
        super().hidePopup()

    def narrow(self, text: str) -> None:
        self.filter.set_words(text)
        wanted = self.filter.index(0, 0) if text.strip() else self.filter.mapFromSource(self.model().index(self.currentIndex(), 0))
        if not wanted.isValid() and self.filter.rowCount():
            wanted = self.filter.index(0, 0)
        self.list.setCurrentIndex(wanted)
        if wanted.isValid():
            self.list.scrollTo(wanted, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def move_highlight(self, step: int) -> None:
        count = self.filter.rowCount()
        if count:
            row = max(0, min(count - 1, self.list.currentIndex().row() + step))
            self.list.setCurrentIndex(self.filter.index(row, 0))
            self.list.scrollTo(self.list.currentIndex())

    def pick(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        row = self.filter.mapToSource(index).row()
        self.hidePopup()
        self.setCurrentIndex(row)
        self.activated.emit(row)

    def list_key(self, key: int) -> bool:
        """A key pressed in the search box that the list answers; False when the box should keep it."""
        steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: self.MAX_ROWS, Qt.Key.Key_PageUp: -self.MAX_ROWS}
        if key in steps:
            self.move_highlight(steps[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.pick(self.list.currentIndex())
        elif key == Qt.Key.Key_Escape:
            self.hidePopup()
        else:
            return False
        return True

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 - Qt name
        text = event.text()
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        if text.strip() and text.isprintable() and not event.modifiers() & modifiers:
            self.showPopup()
            self.search.setText(text)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802 - Qt name
        event.ignore()  # scrolling past the box must not change the pick


class RefreshingComboBox(QtWidgets.QComboBox):
    """A combo box whose entries are rebuilt every time its list opens, for things that come and go."""

    def __init__(self, rebuild: Callable[[], None]) -> None:
        super().__init__()
        self.rebuild = rebuild
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(20)

    def showPopup(self) -> None:  # noqa: N802 - Qt name
        self.rebuild()
        super().showPopup()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802 - Qt name
        event.ignore()


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

        self.games = SearchableComboBox("Type to search: team, network, live / later / final")
        self.games.setToolTip("Whose score and stats the ad screen shows - today's games from ESPN's scoreboard. "
                              "Open the list and type to search it.")
        self.games.addItem("none - the ad screen shows no score", ("", ""))
        self.games.activated.connect(self.pick_game)
        self.games_loaded.connect(self.fill_games)
        self.windows = RefreshingComboBox(self.fill_windows)
        self.windows.setToolTip("Which Chrome window is the stream. Automatic takes the first window whose title names a "
                                "service (title_contains in config.toml); pick one yourself when there are several, or "
                                "when the service is not listed - it is then watched while its title stays what it is now.")
        self.windows.activated.connect(self.pick_window)
        self.fill_windows()
        pickers = QtWidgets.QGridLayout()
        pickers.setColumnStretch(1, 1)
        pickers.addWidget(field_label("Game"), 0, 0)
        pickers.addWidget(self.games, 0, 1)
        pickers.addWidget(field_label("Chrome window"), 1, 0)
        pickers.addWidget(self.windows, 1, 1)

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
        layout.addLayout(pickers)
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
            self.games.setItemData(self.games.count() - 1, game.name, SEARCH_ROLE)  # "Utah State Aggies at Utah Utes"
            self.games.setItemData(self.games.count() - 1, game.name, Qt.ItemDataRole.ToolTipRole)
        current = self.controller.scores.event_id if self.controller.scores else ""
        index = next((i for i in range(self.games.count()) if self.games.itemData(i)[0] == (current or self.wanted_event)), 0)
        self.games.setCurrentIndex(index)
        if index and not self.controller.scores:  # restore last session's pick
            self.pick_game(index)

    def pick_game(self, index: int) -> None:
        event_id, label = self.games.itemData(index)
        self.controller.set_game("", event_id, label)
        save_state(event_id=event_id)

    # --- Chrome window ---------------------------------------------------------------------------
    def fill_windows(self) -> None:
        """Rebuilt every time the list opens: Chrome windows come and go."""
        chosen = self.controller.chosen_window
        self.windows.clear()
        self.windows.addItem(self.automatic_entry(), 0)
        for hwnd, description in self.controller.chrome_windows():
            self.windows.addItem(description, hwnd)
        self.windows.setCurrentIndex(max(0, self.windows.findData(chosen)))

    def automatic_entry(self) -> str:
        c = self.controller
        if c.chosen_window:
            return "Automatic - the first window whose title names the stream"
        watched = c.watched_window()
        return f"Automatic: {watched}" if watched else f"Automatic: waiting for a window titled '{c.config.title_contains}'"

    def pick_window(self, index: int) -> None:
        self.controller.choose_window(self.windows.itemData(index) or 0)

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
        self.windows.setItemText(0, self.automatic_entry())
        wanted = max(0, self.windows.findData(c.chosen_window))  # back at "Automatic" once a picked window has closed
        if self.windows.currentIndex() != wanted:
            self.windows.setCurrentIndex(wanted)
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
