"""Draw the app icon (a football on the app's dark tile) and write assets/app.ico + app.png.

Run once; the results are committed, so nothing needs this at run time. Drawn with Qt, large
and antialiased - Windows scales it down for the taskbar, the Start menu and shortcuts.

Usage:  python tools\\make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6 import QtCore, QtGui  # noqa: E402

SIZE = 256


def draw() -> QtGui.QImage:
    image = QtGui.QImage(SIZE, SIZE, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor("#0f141a"))
    p.drawRoundedRect(QtCore.QRectF(8, 8, SIZE - 16, SIZE - 16), 52, 52)

    p.translate(SIZE / 2, SIZE / 2)
    p.rotate(-32)
    ball = QtGui.QPainterPath()  # a pointed oval: two arcs meeting at the tips
    ball.moveTo(-98, 0)
    ball.cubicTo(-60, -74, 60, -74, 98, 0)
    ball.cubicTo(60, 74, -60, 74, -98, 0)
    gradient = QtGui.QLinearGradient(0, -60, 0, 60)
    gradient.setColorAt(0.0, QtGui.QColor("#b5672f"))
    gradient.setColorAt(1.0, QtGui.QColor("#6f3a18"))
    p.setBrush(gradient)
    p.setPen(QtGui.QPen(QtGui.QColor("#3d1f0c"), 5))
    p.drawPath(ball)

    white = QtGui.QPen(QtGui.QColor("#f4f1ea"), 9, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap)
    p.setPen(white)
    p.drawLine(QtCore.QPointF(-40, 0), QtCore.QPointF(40, 0))  # the lace...
    for x in (-30, -10, 10, 30):  # ...and its stitches
        p.drawLine(QtCore.QPointF(x, -15), QtCore.QPointF(x, 15))
    p.setClipPath(ball)
    p.setPen(QtGui.QPen(QtGui.QColor("#f4f1ea"), 11))
    for x in (-68, 68):  # the stripes near the tips
        p.drawLine(QtCore.QPointF(x, -60), QtCore.QPointF(x, 60))
    p.end()
    return image


def main() -> int:
    app = QtGui.QGuiApplication([])  # noqa: F841 - Qt needs one for fonts/painting
    folder = ROOT / "assets"
    folder.mkdir(exist_ok=True)
    image = draw()
    ok = image.save(str(folder / "app.ico")) and image.save(str(folder / "app.png"))
    print(("wrote " if ok else "FAILED to write ") + str(folder / "app.ico"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
