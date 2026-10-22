"""Small icons for the Weights view, drawn here (our own simple shapes, no third-party artwork).

icon(name) returns a QIcon for: add, subtract, replace, smooth, auto, prune, bind, remove, update, pick.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 32  # drawn at 2x and shown at 16 px, so it stays sharp on high-DPI screens
GREY = QColor("#d5d5d5")
GREEN, RED, BLUE, AMBER = QColor("#52d273"), QColor("#ff6b6b"), QColor("#6fb0ff"), QColor("#ffcf5a")


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _drop(p: QPainter, color: QColor, rect: QRectF) -> None:
    """A brush drop: round at the bottom, pointed at the top."""
    path = QPainterPath()
    cx, w, h = rect.center().x(), rect.width(), rect.height()
    path.moveTo(cx, rect.top())
    path.cubicTo(cx + w * 0.55, rect.top() + h * 0.45, cx + w * 0.5, rect.bottom(), cx, rect.bottom())
    path.cubicTo(cx - w * 0.5, rect.bottom(), cx - w * 0.55, rect.top() + h * 0.45, cx, rect.top())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _badge(p: QPainter, color: QColor, sign: str) -> None:
    """A small round badge at the bottom right with +, - or =."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(17, 17, 14, 14))
    p.setPen(_pen(QColor("white"), 2.4))
    if sign in "+-":
        p.drawLine(QPointF(20.5, 24), QPointF(27.5, 24))
    if sign == "+":
        p.drawLine(QPointF(24, 20.5), QPointF(24, 27.5))
    if sign == "=":
        p.drawLine(QPointF(20.5, 22), QPointF(27.5, 22))
        p.drawLine(QPointF(20.5, 26), QPointF(27.5, 26))


def _draw(name: str, p: QPainter) -> None:
    pen = _pen(GREY, 2.6)
    if name in ("add", "subtract", "replace"):
        _drop(p, GREY, QRectF(3, 3, 18, 24))
        _badge(p, {"add": GREEN, "subtract": RED, "replace": BLUE}[name],
               {"add": "+", "subtract": "-", "replace": "="}[name])
    elif name == "smooth":
        _drop(p, GREY, QRectF(8, 3, 16, 24))
        p.setPen(_pen(QColor("#2a2a2a"), 2))
        p.drawArc(QRectF(11, 13, 10, 10), 200 * 16, 100 * 16)
    elif name == "auto":  # two arrows going round
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(6, 6, 20, 20), 30 * 16, 150 * 16)
        p.drawArc(QRectF(6, 6, 20, 20), 210 * 16, 150 * 16)
        p.setBrush(GREY)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(26, 4), QPointF(28, 13), QPointF(19, 11)])
        p.drawPolygon([QPointF(6, 28), QPointF(4, 19), QPointF(13, 21)])
    elif name == "prune":  # a line with a short tick cut off
        p.setPen(pen)
        p.drawLine(QPointF(3, 16), QPointF(29, 16))
        for x in (8, 14, 20):
            p.drawLine(QPointF(x, 11), QPointF(x, 21))
        p.setPen(_pen(RED, 2.6))
        p.drawLine(QPointF(25, 9), QPointF(25, 13))
    elif name == "bind":  # two chain links
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.translate(16, 16)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-13, -5, 15, 10), 5, 5)
        p.drawRoundedRect(QRectF(-2, -5, 15, 10), 5, 5)
        p.restore()
    elif name == "remove":  # x in a circle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GREY)
        p.drawEllipse(QRectF(4, 4, 24, 24))
        p.setPen(_pen(QColor("#3a3a3a"), 3))
        p.drawLine(QPointF(11, 11), QPointF(21, 21))
        p.drawLine(QPointF(21, 11), QPointF(11, 21))
    elif name == "update":  # chain links with a refresh arrow
        _draw("bind", p)
        p.setPen(_pen(AMBER, 2.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(17, 17, 12, 12), 0, 270 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(AMBER)
        p.drawPolygon([QPointF(29, 19), QPointF(30, 25), QPointF(25, 23)])
    elif name == "pick":  # a bone with a pointer: pick bones in the viewport
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GREY)
        p.drawPolygon([QPointF(4, 26), QPointF(9, 18), QPointF(22, 5), QPointF(12, 22)])
        p.drawEllipse(QRectF(2, 23, 6, 6))
        p.setBrush(AMBER)
        p.drawPolygon([QPointF(18, 16), QPointF(30, 22), QPointF(25, 24), QPointF(28, 30), QPointF(26, 31),
                       QPointF(23, 25), QPointF(19, 28)])


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    pix = QPixmap(SIZE, SIZE)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _draw(name, p)
    p.end()
    pix.setDevicePixelRatio(2.0)
    return QIcon(pix)
