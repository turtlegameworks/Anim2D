"""Viewport background grid: five styles, off, or any colour. Shared by the Anim2D viewport and
Preview and the FX builder preview.

A style is minor lines, a brighter major line every few cells, and the origin axes. "Custom" uses the
chosen colour for the lines (majors stronger). The cell size follows the zoom, so lines stay 40 to
160 px apart on screen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QTransform


@dataclass(frozen=True)
class GridStyle:
    minor: str  # #RRGGBBAA
    major: str
    axis_x: str  # the horizontal axis (y = 0)
    axis_y: str  # the vertical axis (x = 0)
    major_every: int = 5
    dots: bool = False  # dots at line crossings instead of lines


GRID_STYLES: dict[str, GridStyle] = {
    "Subtle": GridStyle("#ffffff0e", "#ffffff1c", "#ff5a5a5a", "#5aff785a"),
    "Blueprint": GridStyle("#4f8fe048", "#6fb0ff90", "#ff7a7a80", "#7affa080"),
    "High contrast": GridStyle("#ffffff30", "#ffffff60", "#ff5050c0", "#50ff70c0"),
    "Warm": GridStyle("#ffb45a34", "#ffc06a70", "#ff6a5090", "#a0e06a90"),
    "Dots": GridStyle("#ffffff40", "#ffffff80", "#ff5a5a5a", "#5aff785a", dots=True),
}
DEFAULT_STYLE = "Subtle"
OFF = "Off"
CUSTOM = "Custom"
CHOICES = [*GRID_STYLES, CUSTOM, OFF]


def _qcolor(value: str) -> QColor:
    text = value.lstrip("#")
    if len(text) == 8:
        return QColor(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), int(text[6:8], 16))
    return QColor(value)


def style_for(name: str, custom: str | None = None) -> GridStyle | None:
    """The style to draw (None: no grid). Custom: the colour for lines, majors and axes stronger."""
    if name == OFF:
        return None
    if name == CUSTOM and custom:
        c = QColor(custom)
        rgb = c.name()[1:]
        return GridStyle(f"#{rgb}30", f"#{rgb}70", f"#{rgb}c0", f"#{rgb}c0")
    return GRID_STYLES.get(name, GRID_STYLES[DEFAULT_STYLE])


def for_light_background(style: GridStyle) -> GridStyle:
    """White lines vanish on a light background: draw those in black with the same strength."""
    def dark(c: str) -> str:
        return "#000000" + c[7:] if c.lower().startswith("#ffffff") else c
    return GridStyle(dark(style.minor), dark(style.major), style.axis_x, style.axis_y, style.major_every, style.dots)


def draw_grid(p: QPainter, view: QTransform, width: int, height: int, style: GridStyle | None,
              axes: bool = True, light: bool = False) -> None:
    """Draw the grid for a world->screen view over a width x height area. light: the background is light."""
    if style is None:
        return
    if light:
        style = for_light_background(style)
    scale = math.hypot(view.m11(), view.m12()) or 1.0
    step = 100.0
    while step * scale < 40:
        step *= 2
    while step * scale > 160:
        step /= 2
    inv, _ = view.inverted()
    corners = [inv.map(QPointF(x, y)) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
    x0, x1 = min(c.x() for c in corners), max(c.x() for c in corners)
    y0, y1 = min(c.y() for c in corners), max(c.y() for c in corners)
    minor, major = _qcolor(style.minor), _qcolor(style.major)
    xs = [i * step for i in range(math.floor(x0 / step), math.ceil(x1 / step) + 1)]
    ys = [i * step for i in range(math.floor(y0 / step), math.ceil(y1 / step) + 1)]
    is_major = lambda v: round(v / step) % style.major_every == 0  # noqa: E731
    if style.dots:
        p.setPen(Qt.PenStyle.NoPen)
        for x in xs:
            for y in ys:
                s = view.map(QPointF(x, y))
                big = is_major(x) and is_major(y)
                p.setBrush(major if big else minor)
                r = 2.0 if big else 1.2
                p.drawEllipse(QRectF(s.x() - r, s.y() - r, 2 * r, 2 * r))
    else:
        for x in xs:
            p.setPen(QPen(major if is_major(x) else minor, 1))
            s = view.map(QPointF(x, 0))
            p.drawLine(QPointF(s.x(), 0), QPointF(s.x(), height))
        for y in ys:
            p.setPen(QPen(major if is_major(y) else minor, 1))
            s = view.map(QPointF(0, y))
            p.drawLine(QPointF(0, s.y()), QPointF(width, s.y()))
    if axes:
        o = view.map(QPointF(0, 0))
        p.setPen(QPen(_qcolor(style.axis_x), 1))
        p.drawLine(QPointF(0, o.y()), QPointF(width, o.y()))
        p.setPen(QPen(_qcolor(style.axis_y), 1))
        p.drawLine(QPointF(o.x(), 0), QPointF(o.x(), height))



# --- viewport background ----------------------------------------------------------------------------

BACKGROUNDS: dict[str, str | None] = {
    "Default": None,  # Anim2D's own: slightly different for Setup and Animate (checker in the Preview)
    "Charcoal": "#17181c",
    "Slate": "#3a3f4b",
    "Mid grey": "#6b6e75",
    "Light": "#e4e6ea",
    "Checker": "checker",
}
BACKGROUND_CHOICES = [*BACKGROUNDS, CUSTOM]


def background_for(name: str, custom: str | None = None) -> str | None:
    """A colour, "checker", or None for the viewport's own default."""
    if name == CUSTOM:
        return custom or None
    return BACKGROUNDS.get(name)
