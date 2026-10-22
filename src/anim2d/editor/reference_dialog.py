"""View > Reference footage: pick a video or image sequence to animate over, and place it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from .reference import FILTER, ReferenceSettings
from .value_bar import ValueBar


def _spin(low: float, high: float, decimals: int, step: float, suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setSuffix(suffix)
    return spin


class ReferenceDialog(QDialog):
    """window: the EditorWindow (doc, viewport, reference_media, load_reference)."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.setWindowTitle("Reference footage")
        self.setModal(False)
        self.window_ = window
        self.file_label = QLabel()
        self.file_label.setWordWrap(True)
        choose = QPushButton("Open video or images…")
        choose.clicked.connect(self.choose)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove)
        self.show_box = QCheckBox("Show")
        self.front_box = QCheckBox("In front of the skeleton")
        self.flip_box = QCheckBox("Mirror")
        self.opacity = ValueBar(0, 100, 50)
        self.scale = _spin(0.01, 50, 3, 0.05, " ×")
        self.x = _spin(-100000, 100000, 1, 10, " px")
        self.y = _spin(-100000, 100000, 1, 10, " px")
        self.start = _spin(-3600, 3600, 3, 1 / 30, " s")
        self.speed = _spin(0.05, 20, 3, 0.1, " ×")
        fit = QPushButton("Fit to view")
        fit.setToolTip("Centre the footage in the viewport and scale it to fit")
        fit.clicked.connect(self.fit)
        match = QPushButton("Match animation length")
        match.setToolTip("Set the speed so the whole footage plays over the current animation")
        match.clicked.connect(self.match_length)
        self.info = QLabel()
        self.info.setStyleSheet("color: #8f8f8f;")
        self.info.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(choose)
        top.addWidget(self.remove_button)
        checks = QHBoxLayout()
        for box in (self.show_box, self.front_box, self.flip_box):
            checks.addWidget(box)
        form = QFormLayout()
        form.addRow("Opacity", self.opacity)
        form.addRow("Scale", self.scale)
        pos = QHBoxLayout()
        pos.addWidget(self.x)
        pos.addWidget(self.y)
        form.addRow("Position X / Y", pos)
        form.addRow("Starts at", self.start)
        form.addRow("Speed", self.speed)
        buttons = QHBoxLayout()
        buttons.addWidget(fit)
        buttons.addWidget(match)
        layout = QVBoxLayout(self)
        layout.addWidget(self.file_label)
        layout.addLayout(top)
        layout.addLayout(checks)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.info)
        self.load_values()
        for box in (self.show_box, self.front_box, self.flip_box):
            box.toggled.connect(self.apply)
        self.opacity.valueChanged.connect(self.apply)
        for spin in (self.scale, self.x, self.y, self.start, self.speed):
            spin.valueChanged.connect(self.apply)
        media = window.reference_media
        media.loaded.connect(self.show_info)
        media.progress.connect(lambda n: self.info.setText(f"Decoding the video… {n} frames"))
        media.failed.connect(lambda msg: self.info.setText(f"Can't use it: {msg}"))
        self.show_info()

    def settings(self) -> ReferenceSettings:
        return ReferenceSettings.of(self.window_.doc)

    def load_values(self) -> None:
        s = self.settings()
        self._loading = True
        self.file_label.setText(f"<b>{Path(s.path).name}</b><br>{s.path}" if s.path else "No reference footage yet.")
        self.show_box.setChecked(s.on)
        self.front_box.setChecked(s.front)
        self.flip_box.setChecked(s.flip)
        self.opacity.set(round(s.opacity * 100))
        for spin, value in ((self.scale, s.scale), (self.x, s.x), (self.y, s.y), (self.start, s.start),
                            (self.speed, s.speed)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.remove_button.setEnabled(bool(s.path))
        self._loading = False

    def apply(self) -> None:
        if self._loading:
            return
        s = self.settings()
        s.on, s.front, s.flip = self.show_box.isChecked(), self.front_box.isChecked(), self.flip_box.isChecked()
        s.opacity = self.opacity.value() / 100
        s.scale, s.x, s.y = self.scale.value(), self.x.value(), self.y.value()
        s.start, s.speed = self.start.value(), self.speed.value()
        s.store(self.window_.doc)
        self.window_.viewport.update()

    def choose(self) -> None:
        start = self.settings().path or str(self.window_._start_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Reference footage", start, FILTER)
        if path:
            self.open(Path(path))

    def open(self, path: Path) -> None:
        s = self.settings()
        s.path = str(path)
        s.on = True
        s.store(self.window_.doc)
        self.load_values()
        self.info.setText("Loading…")
        self.window_.load_reference(fit=True)

    def remove(self) -> None:
        self.window_.doc.project_settings.pop("reference", None)
        self.window_.doc.dirty = True
        self.window_.reference_media.clear()
        self.load_values()
        self.show_info()
        self.window_.viewport.update()

    def fit(self) -> None:
        self.window_.fit_reference()
        self.load_values()

    def match_length(self) -> None:
        media, st = self.window_.reference_media, self.window_.state
        length = self.window_.doc.animation_duration(st.animation) if st.animation else 0
        if media.duration > 0 and length > 0:
            self.speed.setValue(media.duration / length)
            self.start.setValue(0.0)

    def show_info(self) -> None:
        media = self.window_.reference_media
        if media.loading:
            self.info.setText("Decoding the video…")
        elif media.frames:
            size = media.size
            self.info.setText(f"{len(media.frames)} frame(s), {size.width()}×{size.height()}, "
                              f"{media.duration:.2f} s. The timeline time picks the frame.")
        elif not self.settings().path:
            self.info.setText("Videos (MP4, MOV, AVI, WebM …), a numbered image sequence, or one image.")
        self.load_values()
