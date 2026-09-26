"""Widgets for the game stats: the win-odds chart, the two-team comparison, one team's own numbers.

Used by the "Game stats" window (ui.py) and by the ad screen (actuators/overlay.py). They are
plain Qt widgets fed a GameStats (already delayed by the spoiler guard); nothing here talks to
ESPN or knows about the detector.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from adblock.data.espn import GameStats, PlayerGroup

Qt = QtCore.Qt
BOTH = "both"  # perspective: both teams (a comparison), or one team's abbreviation

COMPARISON_KEYS = ["1st Downs", "Total Yards", "Passing", "Rushing", "3rd down efficiency", "Turnovers", "Penalties", "Possession"]


def team_colour(stats: GameStats, abbreviation: str) -> QtGui.QColor:
    """The team's colour, lightened when it would vanish on the dark background."""
    colour = QtGui.QColor(stats.colors.get(abbreviation, "#8899aa"))
    if colour.lightnessF() < 0.35:
        colour = colour.lighter(160)
    return colour


class WinOddsChart(QtWidgets.QWidget):
    """Win probability after every play, drawn as bars.

    Both teams: the bars rise above the 50 % line in the home team's colour when it is the
    favourite and drop below it in the away team's colour when that one is. One team: its own
    odds from 0 to 100 % in its colour. Quarter boundaries are marked.
    """

    def __init__(self, compact: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.stats: GameStats | None = None
        self.perspective = BOTH
        self.compact = compact
        self.setMinimumHeight(140 if compact else 200)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def set_stats(self, stats: GameStats | None, perspective: str = BOTH) -> None:
        self.stats, self.perspective = stats, perspective
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt name
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#121a22"))
        painter.drawRoundedRect(area, 8, 8)
        font = QtGui.QFont("Segoe UI", 9 if self.compact else 10)
        painter.setFont(font)
        stats = self.stats
        if stats is None or not stats.win_prob:
            painter.setPen(QtGui.QColor("#66768a"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "win odds appear once the plays are old enough (spoiler guard)")
            painter.end()
            return

        left, top, right, bottom = area.left() + 46, area.top() + 26, area.right() - 12, area.bottom() - 24
        plot = QtCore.QRectF(left, top, right - left, bottom - top)

        def y_for(pct: float) -> float:
            return plot.bottom() - pct * plot.height()

        # axis labels and guide lines
        painter.setPen(QtGui.QPen(QtGui.QColor("#2a3440"), 1))
        for pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            painter.drawLine(QtCore.QPointF(plot.left(), y_for(pct)), QtCore.QPointF(plot.right(), y_for(pct)))
        painter.setPen(QtGui.QColor("#66768a"))
        for pct in (0.0, 0.5, 1.0):
            painter.drawText(QtCore.QRectF(area.left() + 4, y_for(pct) - 8, 38, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{int(pct * 100)}%")

        points = stats.win_prob
        slots = max(len(points), 60)  # early in the game the bars stay narrow instead of filling the width
        width = plot.width() / slots
        home, away = stats.home.abbreviation, stats.away.abbreviation
        home_colour, away_colour = team_colour(stats, home), team_colour(stats, away)
        single = self.perspective if self.perspective in (home, away) else None
        painter.setPen(Qt.PenStyle.NoPen)
        for i, point in enumerate(points):
            x = plot.left() + i * width
            if single is None:
                favourite_home = point.home_pct >= 0.5
                painter.setBrush(home_colour if favourite_home else away_colour)
                y0, y1 = y_for(0.5), y_for(point.home_pct)
            else:
                pct = point.home_pct if single == home else 1.0 - point.home_pct
                painter.setBrush(home_colour if single == home else away_colour)
                y0, y1 = y_for(0.0), y_for(pct)
            painter.drawRect(QtCore.QRectF(x, min(y0, y1), max(1.0, width - 1.0), max(1.0, abs(y1 - y0))))

        # quarter boundaries
        painter.setPen(QtGui.QPen(QtGui.QColor("#3a4856"), 1, Qt.PenStyle.DashLine))
        last_period = None
        for i, point in enumerate(points):
            period = stats.plays[point.index].period if point.index < len(stats.plays) else 0
            if period < 1:
                continue  # a play without a period (end-of-half bookkeeping) is no boundary
            if last_period is not None and period != last_period:
                x = plot.left() + i * width
                painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
                painter.setPen(QtGui.QColor("#66768a"))
                painter.drawText(QtCore.QRectF(x + 3, plot.bottom() + 4, 60, 18), Qt.AlignmentFlag.AlignLeft, f"Q{period}" if period <= 4 else "OT")
                painter.setPen(QtGui.QPen(QtGui.QColor("#3a4856"), 1, Qt.PenStyle.DashLine))
            last_period = period

        # the current odds, as a caption
        painter.setFont(QtGui.QFont("Segoe UI", 10 if self.compact else 12, QtGui.QFont.Weight.DemiBold))
        latest = points[-1].home_pct
        caption_rect = QtCore.QRectF(plot.left(), area.top() + 4, plot.width(), 20)
        if single is None:
            painter.setPen(home_colour)
            painter.drawText(caption_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{home} {latest * 100:.0f}%")
            painter.setPen(away_colour)
            painter.drawText(caption_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{away} {(1 - latest) * 100:.0f}%")
        else:
            pct = latest if single == home else 1.0 - latest
            painter.setPen(home_colour if single == home else away_colour)
            painter.drawText(caption_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{single} win odds {pct * 100:.0f}%")
        painter.end()


class StatsTable(QtWidgets.QTableWidget):
    """A small read-only table that sizes itself to its rows (explicit heights: the style sheet
    may not have been applied yet when it is filled, e.g. in the previews)."""

    def __init__(self, parent=None, row_height: int = 26, header_height: int = 28) -> None:
        super().__init__(parent)
        self.row_height, self.header_height = row_height, header_height
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def fill(self, headers: list[str], rows: list[list[str]], first_column_left: bool = True) -> None:
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment((Qt.AlignmentFlag.AlignLeft if c == 0 and first_column_left else Qt.AlignmentFlag.AlignCenter)
                                      | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(r, c, item)
        self.horizontalHeader().setFixedHeight(self.header_height)
        for r in range(self.rowCount()):
            self.setRowHeight(r, self.row_height)
        self.setFixedHeight(self.header_height + self.row_height * self.rowCount() + 4)


class TeamComparison(StatsTable):
    """Both teams' numbers side by side; ``keys`` limits the rows (the ad screen shows the main ones)."""

    def __init__(self, keys: list[str] | None = None, parent=None, row_height: int = 26, header_height: int = 28) -> None:
        super().__init__(parent, row_height, header_height)
        self.keys = keys

    def set_stats(self, stats: GameStats) -> None:
        rows = [[label, away, home] for label, away, home in stats.team_stats if self.keys is None or label in self.keys]
        if self.keys:
            rows.sort(key=lambda row: self.keys.index(row[0]))
        self.fill(["", stats.away.abbreviation, stats.home.abbreviation], rows)


class TeamPanel(QtWidgets.QWidget):
    """One team: its numbers, its leaders, and its players by stat group."""

    GROUPS = ("Passing", "Rushing", "Receiving", "Defensive")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.layout_ = QtWidgets.QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(10)
        self.numbers = StatsTable()
        self.leaders = QtWidgets.QLabel()
        self.leaders.setObjectName("leaders")
        self.leaders.setWordWrap(True)
        self.layout_.addWidget(self.numbers)
        self.layout_.addWidget(self.leaders)
        self.groups: list[tuple[QtWidgets.QLabel, StatsTable]] = []
        for _ in self.GROUPS:
            title = QtWidgets.QLabel()
            title.setObjectName("groupTitle")
            table = StatsTable()
            self.layout_.addWidget(title)
            self.layout_.addWidget(table)
            self.groups.append((title, table))
        self.layout_.addStretch(1)

    def set_stats(self, stats: GameStats, abbreviation: str) -> None:
        home = abbreviation == stats.home.abbreviation
        self.numbers.fill(["", abbreviation], [[label, home_value if home else away_value] for label, away_value, home_value in stats.team_stats])
        lines = [f"<b>{category}</b>: {athlete}  <span style='color:#9fb0c3'>{line}</span>"
                 for category, athlete, line in stats.leaders.get(abbreviation, []) if athlete != "?"]
        self.leaders.setText("<br>".join(lines) if lines else "")
        groups = {g.label.split()[-1]: g for g in stats.players.get(abbreviation, [])}
        for wanted, (title, table) in zip(self.GROUPS, self.groups):
            group: PlayerGroup | None = groups.get(wanted)
            if group is None:
                title.hide()
                table.hide()
                continue
            title.setText(wanted)
            table.fill(["Player"] + group.columns, [[athlete] + values for athlete, values in group.rows[:4]])
            title.show()
            table.show()


def tidy(text: str) -> str:
    """Play-by-play text without ESPN's bookkeeping: the leading clock and the formation."""
    if text.startswith("("):
        text = text.split(")", 1)[-1].strip()
    for prefix in ("No Huddle-Shotgun ", "No Huddle ", "Shotgun "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def scoring_summary(stats: GameStats, limit: int = 8) -> str:
    """Scoring plays, newest last, as rich text lines."""
    lines = []
    for play in stats.scoring_plays[-limit:]:
        quarter = f"Q{play.period}" if play.period <= 4 else "OT"
        lines.append(f"<span style='color:#66768a'>{quarter} {play.clock}</span>  <b>{play.team}</b>  {tidy(play.text)}  "
                     f"<span style='color:#9fb0c3'>{play.away_score}-{play.home_score}</span>")
    return "<br>".join(lines) if lines else "<span style='color:#66768a'>no scoring yet</span>"


def recent_plays(stats: GameStats, limit: int = 6) -> str:
    lines = []
    for play in stats.plays[-limit:]:
        quarter = f"Q{play.period}" if play.period <= 4 else "OT"
        lines.append(f"<span style='color:#66768a'>{quarter} {play.clock}</span>  {tidy(play.text)}")
    return "<br>".join(lines) if lines else "<span style='color:#66768a'>plays appear once they are old enough (spoiler guard)</span>"
