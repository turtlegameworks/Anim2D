"""Add image sequence: pick a numbered image set and how it plays."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QSpinBox, QVBoxLayout)

from .document import Document
from .sequences import find_sequences

MODES = [("Forward", "forward"), ("Reverse", "reverse"), ("Ping-pong", "pingpong")]


class SequenceDialog(QDialog):
    def __init__(self, doc: Document, slot: str, animation: str | None, time: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Image sequence in {slot}")
        self.sequences = find_sequences(doc.image_paths())
        self.images = QComboBox()
        for prefix, frames in self.sequences.items():
            self.images.addItem(f"{prefix}…  ({len(frames)} frames)", prefix)
        self.animation = QComboBox()
        self.animation.addItems(doc.animation_names())
        if animation in doc.animation_names():
            self.animation.setCurrentText(animation)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 3600)
        self.start.setDecimals(3)
        self.start.setSuffix(" s")
        self.start.setValue(time)
        self.fps = QSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setValue(24)
        self.fps.setSuffix(" fps")
        self.mode = QComboBox()
        for label, key in MODES:
            self.mode.addItem(label, key)
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 100)
        self.hold = QCheckBox("Keep the last frame showing")
        self.hold.setChecked(True)
        self.info = QLabel()
        self.info.setStyleSheet("color: #a9a9a9;")
        form = QFormLayout()
        form.addRow("Images", self.images)
        form.addRow("Animation", self.animation)
        form.addRow("Start", self.start)
        form.addRow("Frame rate", self.fps)
        form.addRow("Play", self.mode)
        form.addRow("Times", self.cycles)
        form.addRow(self.hold)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add sequence")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.sequences) and bool(doc.animation_names()))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.info)
        layout.addWidget(buttons)
        for w in (self.images, self.mode):
            w.currentIndexChanged.connect(self._update)
        for w in (self.fps, self.cycles):
            w.valueChanged.connect(self._update)
        if not self.sequences:
            self.info.setText("No numbered images found (name them like fire_01.png, fire_02.png …).")
        elif not doc.animation_names():
            self.info.setText("Create an animation first.")
        else:
            self._update()

    def frames(self) -> list[str]:
        return self.sequences.get(self.images.currentData(), [])

    def _update(self) -> None:
        n = len(self.frames())
        mode = self.mode.currentData()
        per_cycle = n if mode != "pingpong" else max(1, 2 * n - 2)
        seconds = per_cycle * self.cycles.value() / self.fps.value()
        self.info.setText(f"{n} images · {per_cycle * self.cycles.value()} frames · {seconds:.2f} s")

    def choice(self) -> dict:
        return {"images": self.frames(), "animation": self.animation.currentText(), "start": self.start.value(),
                "fps": float(self.fps.value()), "mode": self.mode.currentData(), "cycles": self.cycles.value(),
                "hold_last": self.hold.isChecked()}
