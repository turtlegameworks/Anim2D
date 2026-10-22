"""Edit > Retime: scale time, reverse, or loop a section, for the animation or the selected keys."""

from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .document import Document, KeyRef
from . import retime


def _seconds(value: float, high: float = 600.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, high)
    spin.setDecimals(3)
    spin.setSuffix(" s")
    spin.setValue(value)
    return spin


class RetimeDialog(QDialog):
    def __init__(self, doc: Document, animation: str, selected: list[KeyRef], time: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Retime {animation}")
        self.doc, self.animation, self.selected = doc, animation, selected
        duration = doc.animation_duration(animation)
        self.scope_all = QRadioButton(f"Whole animation ({duration:.3f} s)")
        self.scope_keys = QRadioButton(f"Selected keys ({len(selected)})")
        self.scope_keys.setEnabled(bool(selected))
        (self.scope_keys if selected else self.scope_all).setChecked(True)
        scope = QButtonGroup(self)
        scope.addButton(self.scope_all)
        scope.addButton(self.scope_keys)

        self.action_scale = QRadioButton("Scale time")
        self.action_reverse = QRadioButton("Reverse")
        self.action_loop = QRadioButton("Loop a section")
        self.action_scale.setChecked(True)
        actions = QButtonGroup(self)
        for b in (self.action_scale, self.action_reverse, self.action_loop):
            actions.addButton(b)

        self.factor = QDoubleSpinBox()
        self.factor.setRange(0.01, 100)
        self.factor.setDecimals(3)
        self.factor.setSingleStep(0.1)
        self.factor.setValue(1.0)
        self.factor.setSuffix(" ×")
        self.length = _seconds(duration, 3600)
        self.factor.valueChanged.connect(lambda v: self._sync_length(v * duration))
        self.length.valueChanged.connect(lambda v: self._sync_factor(v / duration if duration else 1.0))
        scale_page = QWidget()
        f = QFormLayout(scale_page)
        f.addRow("Speed factor", self.factor)
        f.addRow("New length", self.length)
        f.addRow(QLabel("Above 1 is slower (longer), below 1 is faster. Selected keys scale from the first one."))
        reverse_page = QWidget()
        QVBoxLayout(reverse_page).addWidget(QLabel("Plays backwards: the whole animation, or the span of the "
                                                   "selected keys. Curves are mirrored."))
        self.loop_start = _seconds(0.0)
        self.loop_end = _seconds(duration or 1.0)
        self.loop_times = QSpinBox()
        self.loop_times.setRange(1, 100)
        loop_page = QWidget()
        f = QFormLayout(loop_page)
        f.addRow("From", self.loop_start)
        f.addRow("To", self.loop_end)
        f.addRow("Extra repeats", self.loop_times)
        f.addRow(QLabel("Keys from 'From' up to 'To' repeat right after 'To'; later keys move on."))
        self.pages = QStackedWidget()
        for page in (scale_page, reverse_page, loop_page):
            self.pages.addWidget(page)
        for i, b in enumerate((self.action_scale, self.action_reverse, self.action_loop)):
            b.toggled.connect(lambda on, i=i: on and self._page(i))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Apply to"))
        layout.addWidget(self.scope_all)
        layout.addWidget(self.scope_keys)
        layout.addWidget(QLabel("Action"))
        for b in (self.action_scale, self.action_reverse, self.action_loop):
            layout.addWidget(b)
        layout.addWidget(self.pages)
        layout.addWidget(buttons)
        self._busy = False

    def _page(self, i: int) -> None:
        self.pages.setCurrentIndex(i)
        loop = i == 2
        self.scope_all.setEnabled(not loop)
        self.scope_keys.setEnabled(bool(self.selected) and not loop)
        if loop:
            self.scope_all.setChecked(True)

    def _sync_length(self, length: float) -> None:
        if not self._busy:
            self._busy = True
            self.length.setValue(length)
            self._busy = False

    def _sync_factor(self, factor: float) -> None:
        if not self._busy:
            self._busy = True
            self.factor.setValue(factor)
            self._busy = False

    def apply(self) -> str:
        """Run the chosen action (one undo step). Returns a status message."""
        refs = self.selected if self.scope_keys.isChecked() else None
        if self.action_scale.isChecked():
            n = retime.scale_time(self.doc, self.animation, self.factor.value(), refs)
            return f"Scaled {n} key(s) ×{self.factor.value():g}"
        if self.action_reverse.isChecked():
            n = retime.reverse(self.doc, self.animation, refs)
            return f"Reversed {n} key(s)"
        n = retime.loop_section(self.doc, self.animation, self.loop_start.value(), self.loop_end.value(),
                                self.loop_times.value())
        return f"Looped the section: {n} key(s) added"
