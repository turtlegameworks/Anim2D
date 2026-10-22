"""Centre panel: preview canvas, background choice and playback controls."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

from ..preview.evaluator import PoseEvaluator
from ..preview.grid import CHOICES as GRID_CHOICES, CUSTOM as GRID_CUSTOM, OFF as GRID_OFF, draw_grid, style_for
from ..preview.qt_renderer import ImageCache, draw_frame, fit_view, world_bounds

BACKGROUNDS = {
    "Dark": QColor("#1e1f26"),
    "Mid grey": QColor("#5a5d66"),
    "Light": QColor("#e9e9ee"),
    "Checker": None,
}
LOOP_GAP = 0.25  # seconds of blank time between preview loops, so one-shots read clearly


class PreviewCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 280)
        self.cache = ImageCache()
        self.evaluator: PoseEvaluator | None = None
        self.bounds = QRectF()
        self.time = 0.0
        self.background = "Dark"
        self.grid = GRID_OFF  # a grid style from preview.grid
        self.grid_color: str | None = None  # for the Custom style
        self.message = ""

    def set_evaluator(self, evaluator: PoseEvaluator | None, keep_framing: bool) -> None:
        self.evaluator = evaluator
        if evaluator is not None:
            bounds = world_bounds(evaluator)
            # Only grow the framing while editing, so the view doesn't jump on every slider tick.
            self.bounds = self.bounds.united(bounds) if keep_framing and not self.bounds.isEmpty() else bounds
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = BACKGROUNDS.get(self.background)
        if bg is None:
            size = 16
            for y in range(0, self.height(), size):
                for x in range(0, self.width(), size):
                    p.fillRect(x, y, size, size, QColor("#c8c8cc") if (x // size + y // size) % 2 else QColor("#f2f2f4"))
        else:
            p.fillRect(self.rect(), bg)
        light = bg is None or bg.lightness() > 128
        view = fit_view(self.bounds, self.width(), self.height())

        # Origin axes: +X right, +Y up (Spine convention); drawn by the grid when it is on.
        origin = view.map(0.0, 0.0)
        axis = QColor(0, 0, 0, 60) if light else QColor(255, 255, 255, 45)
        style = style_for(self.grid, self.grid_color)
        if style is not None:
            draw_grid(p, view, self.width(), self.height(), style, light=light)
        else:
            p.setPen(QPen(axis, 1, Qt.PenStyle.DashLine))
            p.drawLine(0, round(origin[1]), self.width(), round(origin[1]))
            p.drawLine(round(origin[0]), 0, round(origin[0]), self.height())
        p.setPen(axis.darker(50) if light else QColor(255, 255, 255, 110))
        p.drawText(round(origin[0]) + 6, round(origin[1]) - 6, "+X →  +Y ↑")

        if self.evaluator is not None:
            draw_frame(p, self.evaluator.draw_items(self.time), self.cache, view)
        p.resetTransform()
        p.setOpacity(1)
        p.setPen(QColor(0, 0, 0, 160) if light else QColor(255, 255, 255, 150))
        p.drawText(10, 20, "Approximate preview of current settings - verify in Spine")
        if self.evaluator is not None and self.evaluator.limitations:
            p.setPen(QColor("#d08a2a") if light else QColor("#f0b44c"))
            p.drawText(10, 38, "Preview limits: " + ", ".join(self.evaluator.limitations))
        if self.message:
            p.setPen(QColor("#e05555"))
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message)
        p.end()


class PreviewPanel(QWidget):
    background_changed = Signal(str)
    grid_changed = Signal(str, str)  # style, custom colour ("" when none)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = PreviewCanvas()
        self.fps = 30.0
        self.playing = True
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._play_start_time = 0.0

        self.play_button = QPushButton("Pause")
        self.play_button.setToolTip("Play / pause the preview")
        self.play_button.clicked.connect(self.toggle_play)
        self.restart_button = QPushButton("Restart")
        self.restart_button.clicked.connect(self.restart)
        self.loop_check = QCheckBox("Loop preview")
        self.loop_check.setChecked(True)
        self.loop_check.setToolTip("Repeat the preview. This only affects the preview, not the generated animation.")
        self.speed_combo = QComboBox()
        for label, speed in (("0.1x", 0.1), ("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)):
            self.speed_combo.addItem(label, speed)
        self.speed_combo.setCurrentIndex(3)
        self.speed_combo.setToolTip("Preview playback speed only - never changes the generated duration")
        self.speed_combo.currentIndexChanged.connect(self._rebase_clock)
        self.bg_combo = QComboBox()
        self.bg_combo.addItems(list(BACKGROUNDS))
        self.bg_combo.currentTextChanged.connect(self._set_background)
        self.grid_combo = QComboBox()
        self.grid_combo.addItems([f"{n} colour…" if n == GRID_CUSTOM else n for n in GRID_CHOICES])
        self.grid_combo.setCurrentText(GRID_OFF)
        self.grid_combo.setToolTip("Background grid: five styles, a colour of your choice, or off")
        self.grid_combo.activated.connect(self._grid_picked)

        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.setToolTip("Scrub through the generated animation")
        self.scrub.sliderMoved.connect(self._scrubbed)
        self.scrub.sliderPressed.connect(self.pause)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(200)
        self.duration_label = QLabel()
        self.duration_label.setStyleSheet("color: #9aa0aa")
        self.duration_label.setWordWrap(True)  # long names must not set the panel's minimum width

        controls = QHBoxLayout()  # playback, then the view settings on a second row so the panel can be narrow
        controls.addWidget(self.play_button)
        controls.addWidget(self.restart_button)
        controls.addWidget(self.loop_check)
        controls.addStretch(1)
        controls.addWidget(QLabel("Speed"))
        controls.addWidget(self.speed_combo)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Background"))
        view_row.addWidget(self.bg_combo, 1)
        view_row.addWidget(QLabel("Grid"))
        view_row.addWidget(self.grid_combo, 1)

        timeline = QHBoxLayout()
        timeline.addWidget(self.scrub, 1)
        timeline.addWidget(self.time_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(timeline)
        layout.addWidget(self.duration_label)
        layout.addLayout(controls)
        layout.addLayout(view_row)
        self._clock.start()
        self._timer.start()

    @property
    def duration(self) -> float:
        return self.canvas.evaluator.duration if self.canvas.evaluator else 0.0

    def set_evaluator(self, evaluator: PoseEvaluator | None, keep_framing: bool = True, message: str = "") -> None:
        self.canvas.message = message
        self.canvas.set_evaluator(evaluator, keep_framing)
        if evaluator is not None:
            self.duration_label.setText(
                f"Generated animation: {evaluator.animation_name}  ·  {self.duration:.3f} s including start delay")
        else:
            self.duration_label.setText("Generated animation: -")
        self._update_time_ui()

    def set_background(self, name: str) -> None:
        if name in BACKGROUNDS:
            self.bg_combo.setCurrentText(name)

    def set_grid(self, style: str, color: str | None = None) -> None:
        if style not in GRID_CHOICES:
            return
        self.canvas.grid, self.canvas.grid_color = style, color or self.canvas.grid_color
        self.grid_combo.setCurrentIndex(GRID_CHOICES.index(style))
        self.canvas.update()
        self.grid_changed.emit(style, self.canvas.grid_color or "")

    def _grid_picked(self, index: int) -> None:
        style = GRID_CHOICES[index]
        color = None
        if style == GRID_CUSTOM:
            from PySide6.QtWidgets import QColorDialog
            picked = QColorDialog.getColor(QColor(self.canvas.grid_color or "#4f8fe0"), self, "Grid colour")
            if not picked.isValid():
                self.grid_combo.setCurrentIndex(GRID_CHOICES.index(self.canvas.grid))
                return
            color = picked.name()
        self.set_grid(style, color)

    def _set_background(self, name: str) -> None:
        self.canvas.background = name
        self.canvas.update()
        self.background_changed.emit(name)

    def toggle_play(self) -> None:
        self.pause() if self.playing else self.play()

    def play(self) -> None:
        if self.canvas.time >= self.duration:
            self.canvas.time = 0.0
        self.playing = True
        self.play_button.setText("Pause")
        self._rebase_clock()

    def pause(self) -> None:
        self.playing = False
        self.play_button.setText("Play")

    def restart(self) -> None:
        self.canvas.time = 0.0
        self.play()

    def _rebase_clock(self) -> None:
        self._play_start_time = self.canvas.time
        self._clock.restart()

    def _tick(self) -> None:
        if not self.playing or self.duration <= 0:
            return
        speed = float(self.speed_combo.currentData())
        t = self._play_start_time + self._clock.elapsed() / 1000.0 * speed
        if t > self.duration:
            if self.loop_check.isChecked():
                if t > self.duration + LOOP_GAP:
                    self.canvas.time = 0.0
                    self._rebase_clock()
                    t = 0.0
                # During the gap the effect has ended, so the canvas shows it hidden.
            else:
                t = self.duration
                self.pause()
        self.canvas.time = t
        self.canvas.update()
        self._update_time_ui()

    def _scrubbed(self, value: int) -> None:
        self.canvas.time = self.duration * value / 1000
        self.canvas.update()
        self._update_time_ui()

    def _update_time_ui(self) -> None:
        t = min(self.canvas.time, self.duration)
        if not self.scrub.isSliderDown():
            self.scrub.blockSignals(True)
            self.scrub.setValue(round(1000 * t / self.duration) if self.duration else 0)
            self.scrub.blockSignals(False)
        frames = round(self.duration * self.fps)
        self.time_label.setText(f"{t:.3f} s / {self.duration:.3f} s   ·   frame {round(t * self.fps)}/{frames} "
                                f"@ {self.fps:g} fps")
