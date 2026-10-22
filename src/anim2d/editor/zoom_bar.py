"""The zoom bar on the left edge of a viewport, like Spine's: + / slider / −, 100% and fit."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider, QToolButton, QVBoxLayout, QWidget

MIN_ZOOM, MAX_ZOOM = 0.05, 16.0
STEPS = 1000  # slider positions (logarithmic in zoom)


def zoom_to_slider(zoom: float) -> int:
    zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
    return round((math.log(zoom) - math.log(MIN_ZOOM)) / (math.log(MAX_ZOOM) - math.log(MIN_ZOOM)) * STEPS)


def slider_to_zoom(value: int) -> float:
    return math.exp(math.log(MIN_ZOOM) + value / STEPS * (math.log(MAX_ZOOM) - math.log(MIN_ZOOM)))


class ZoomBar(QWidget):
    def __init__(self, viewport):
        super().__init__(viewport)
        self.viewport = viewport
        self.setObjectName("ZoomBar")
        self.setStyleSheet("#ZoomBar { background: rgba(40, 40, 40, 170); border-radius: 4px; }"
                           "QToolButton { min-width: 22px; min-height: 22px; padding: 0; }")

        def button(text: str, tip: str, func) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(func)
            return b
        self.plus = button("+", "Zoom in", lambda: self.step(1.25))
        self.minus = button("−", "Zoom out", lambda: self.step(1 / 1.25))
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, STEPS)
        self.slider.setFixedHeight(120)
        self.slider.valueChanged.connect(self._slid)
        self.actual = button("🔍", "Zoom 100%", lambda: viewport.set_zoom(1.0))
        self.fit = button("⛶", "Fit: frame the skeleton", viewport.frame_all)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 4, 3, 4)
        layout.setSpacing(2)
        for w in (self.plus, self.slider, self.minus, self.actual, self.fit):
            layout.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        self.adjustSize()
        self.sync()

    def step(self, factor: float) -> None:
        self.viewport.set_zoom(self.viewport.zoom * factor)

    def _slid(self, value: int) -> None:
        self.viewport.set_zoom(slider_to_zoom(value))

    def sync(self) -> None:
        """Follow the viewport's zoom (wheel, fit, 100%)."""
        self.slider.blockSignals(True)
        self.slider.setValue(zoom_to_slider(self.viewport.zoom))
        self.slider.blockSignals(False)

    def place(self) -> None:
        self.move(6, max(6, self.viewport.height() - self.height() - 8))
