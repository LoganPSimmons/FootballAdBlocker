"""The always-on-top screen that covers Chrome during an ad break.

Window recipe validated by spike S2 (tools/spike_occlusion.py), all four parts matter:
  Qt.Tool                   -> WS_EX_TOOLWINDOW: Chrome's occlusion tracker ignores tool windows,
                               so the video keeps rendering (and stays capturable) underneath
  WindowDoesNotAcceptFocus  -> WS_EX_NOACTIVATE + WA_ShowWithoutActivating: showing it never takes
                               focus from Chrome or knocks Chrome out of fullscreen
  WindowStaysOnTopHint      -> above fullscreen Chrome
  WDA_EXCLUDEFROMCAPTURE    -> invisible to every capture API, so the detector never sees it

Phase 1 content is a placeholder (break timer, delayed score, live thumbnail); Phase 2
replaces the body with the dashboard in a QWebEngineView.
"""

from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from adblock import win32

Qt = QtCore.Qt
WINDOW_TITLE = "Football Ad Blocker overlay"
PIP_WIDTH = 400

STYLE = """
#overlay { background: #0b0f14; }
QLabel { color: #e8edf2; font-family: 'Segoe UI'; }
#caption { color: #7d8a99; font-size: 28px; font-weight: 600; letter-spacing: 6px; }
#timer { font-size: 150px; font-weight: 700; }
#score { font-size: 64px; font-weight: 700; }
#detail { color: #9fb0c3; font-size: 30px; }
#reason, #pipCaption { color: #5d6b7a; font-size: 20px; }
#showGame { color: #e8edf2; background: #1b2530; border: 2px solid #3a4856; border-radius: 10px;
            font: 600 26px 'Segoe UI'; padding: 16px 28px; }
#showGame:hover { background: #26405a; border-color: #5b8fc2; }
#pip { border: 2px solid #2a3440; background: #000; }
"""


class Overlay(QtWidgets.QWidget):
    show_game = QtCore.Signal()  # the viewer clicked "that's the game": the costliest mistake, fixed where it shows

    def __init__(self, hotkey_text: str, show_pip: bool = True) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("overlay")
        self.setWindowTitle(WINDOW_TITLE)  # never drawn (frameless); lets tools find the window
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(STYLE)

        def label(name: str, text: str = "") -> QtWidgets.QLabel:
            widget = QtWidgets.QLabel(text)
            widget.setObjectName(name)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return widget

        self.timer = label("timer", "0:00")
        self.score = label("score")
        self.detail = label("detail")
        centre = QtWidgets.QVBoxLayout()
        centre.addStretch(3)
        self.reason = label("reason")
        centre.addWidget(label("caption", "AD BREAK"))
        centre.addWidget(self.timer)
        centre.addWidget(self.reason)
        centre.addSpacing(30)
        centre.addWidget(self.score)
        centre.addWidget(self.detail)
        centre.addStretch(2)

        self.pip = label("pip")
        self.pip.setFixedSize(PIP_WIDTH + 4, PIP_WIDTH * 9 // 16 + 4)
        pip_column = QtWidgets.QVBoxLayout()
        pip_column.addWidget(self.pip)
        self.pip_caption = label("pipCaption", "live picture (muted)")
        pip_column.addWidget(self.pip_caption)
        footer = QtWidgets.QHBoxLayout()
        button = QtWidgets.QPushButton(f"That's the game  -  show it   ({hotkey_text.upper()})")
        button.setObjectName("showGame")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the overlay never takes focus from Chrome
        button.clicked.connect(self.show_game)
        footer.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        footer.addStretch(1)
        footer.addLayout(pip_column)
        self.set_pip_enabled(show_pip)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.addLayout(centre, 1)
        root.addLayout(footer)

        self.hwnd = int(self.winId())  # creates the native window while it is still hidden
        self.capture_excluded = win32.set_exclude_from_capture(self.hwnd)

    def cover(self, rect: win32.Rect) -> None:
        """Show over ``rect`` (physical pixels) without activating. Safe to call repeatedly."""
        left, top, right, bottom = rect
        screen = QtGui.QGuiApplication.screenAt(QtCore.QPoint(left + 1, top + 1)) or QtGui.QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio()  # 1.0 on both of Logan's screens; mixed-DPI setups are untested
        geometry = QtCore.QRect(*(round(v / ratio) for v in (left, top, right - left, bottom - top)))
        if geometry != self.geometry():
            self.setGeometry(geometry)
        if not self.isVisible():
            self.show()

    def set_reason(self, text: str) -> None:
        """Why the overlay is up: detected automatically, or because the viewer said so."""
        self.reason.setText(text)

    def set_pip_enabled(self, enabled: bool) -> None:
        self.pip_enabled = enabled
        self.pip.setVisible(enabled)
        self.pip_caption.setVisible(enabled)

    def set_break_seconds(self, seconds: float) -> None:
        self.timer.setText(f"{int(seconds) // 60}:{int(seconds) % 60:02d}")

    def set_score(self, line: str, detail: str) -> None:
        self.score.setText(line)
        self.detail.setText(detail)

    def set_thumbnail(self, image_bgr: np.ndarray | None) -> None:
        if not self.pip_enabled or image_bgr is None:
            return
        height, width = image_bgr.shape[:2]
        data = np.ascontiguousarray(image_bgr)
        image = QtGui.QImage(data.data, width, height, width * 3, QtGui.QImage.Format.Format_BGR888)
        pixmap = QtGui.QPixmap.fromImage(image).scaled(  # fromImage copies, so `data` may go away after this
            PIP_WIDTH, PIP_WIDTH * 9 // 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.pip.setPixmap(pixmap)
