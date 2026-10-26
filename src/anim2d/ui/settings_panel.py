"""Right panel: output settings plus one editor per preset parameter, grouped."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from ..models.effect import Color
from ..models.params import ParameterSpec
from ..models.preset import PresetDefinition

GROUP_TITLES = {
    "appearance": "Appearance",
    "transform": "Transform",
    "timing": "Timing",
    "effect": "Effect",
    "advanced": "Advanced",
}
SLIDER_STEPS = 1000


class ParamEditor(QWidget):
    """One row: control(s) + reset button. Emits (name, value, merge)."""

    changed = Signal(str, object, bool)
    drag_finished = Signal()

    def __init__(self, spec: ParameterSpec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self._updating = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tip = spec.description or spec.label
        if spec.min is not None:
            tip += f"\nRange {spec.min:g} to {spec.max:g}{spec.unit}, default {spec.default}{spec.unit}"
        self.setToolTip(tip)

        if spec.type in ("integer", "number"):
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(0, SLIDER_STEPS)
            if spec.type == "integer":
                self.spin = QSpinBox()
                self.spin.setRange(int(spec.min), int(spec.max))
                self.slider.setRange(int(spec.min), int(spec.max))
            else:
                self.spin = QDoubleSpinBox()
                self.spin.setRange(spec.min, spec.max)
                span = spec.max - spec.min
                self.spin.setDecimals(3 if span <= 5 else 1)
                self.spin.setSingleStep(span / 100)
            self.spin.setSuffix(f" {spec.unit}" if spec.unit else "")
            self.spin.setKeyboardTracking(False)
            self.spin.setMinimumWidth(86)
            self.slider.valueChanged.connect(self._slider_moved)
            self.slider.sliderReleased.connect(self.drag_finished.emit)
            self.spin.valueChanged.connect(lambda v: self._emit(v, False))
            layout.addWidget(self.slider, 1)
            layout.addWidget(self.spin)
        elif spec.type == "boolean":
            self.check = QCheckBox()
            self.check.setAccessibleName(spec.label)  # its label sits beside it, in the form
            self.check.toggled.connect(lambda v: self._emit(v, False))
            layout.addWidget(self.check)
            layout.addStretch(1)
        elif spec.type == "color":
            self.value_hex = str(spec.default)
            self.swatch = QPushButton()
            self.swatch.setMinimumWidth(110)
            self.swatch.clicked.connect(self._pick_color)
            layout.addWidget(self.swatch)
            layout.addStretch(1)
        elif spec.type == "choice":
            self.combo = QComboBox()
            self.combo.addItems(list(spec.choices))
            self.combo.currentTextChanged.connect(lambda v: self._emit(v, False))
            layout.addWidget(self.combo, 1)

        self.reset_button = QToolButton()
        self.reset_button.setText("↺")
        self.reset_button.setToolTip(f"Reset {spec.label} to {spec.default}")
        self.reset_button.clicked.connect(lambda: self._emit(spec.default, False))
        layout.addWidget(self.reset_button)

    def _emit(self, value: Any, merge: bool) -> None:
        if not self._updating:
            self.changed.emit(self.spec.name, value, merge)

    def _slider_moved(self, position: int) -> None:
        if self._updating:
            return
        if self.spec.type == "integer":
            value: Any = position
        else:
            value = self.spec.min + (self.spec.max - self.spec.min) * position / SLIDER_STEPS
            value = round(value, 4)
        self._emit(value, self.slider.isSliderDown())

    def _pick_color(self) -> None:
        current = Color.from_hex(self.value_hex)
        initial = QColor.fromRgbF(current.r, current.g, current.b, current.a)
        chosen = QColorDialog.getColor(initial, self, self.spec.label, QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if chosen.isValid():
            self._emit(Color(chosen.redF(), chosen.greenF(), chosen.blueF(), chosen.alphaF()).to_hex(), False)

    def set_value(self, value: Any) -> None:
        self._updating = True
        try:
            if self.spec.type in ("integer", "number"):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    number = float(self.spec.default)
                self.spin.setValue(int(number) if self.spec.type == "integer" else number)
                if self.spec.type == "integer":
                    self.slider.setValue(int(number))
                elif not self.slider.isSliderDown():
                    span = self.spec.max - self.spec.min
                    self.slider.setValue(round((number - self.spec.min) / span * SLIDER_STEPS) if span else 0)
            elif self.spec.type == "boolean":
                self.check.setChecked(bool(value))
            elif self.spec.type == "color":
                self.value_hex = str(value)
                try:
                    c = Color.from_hex(self.value_hex)
                    text_color = "#000" if (c.r * 0.3 + c.g * 0.59 + c.b * 0.11) > 0.55 else "#fff"
                    self.swatch.setStyleSheet(f"background: #{self.value_hex[:6]}; color: {text_color};")
                except ValueError:
                    self.swatch.setStyleSheet("")
                self.swatch.setText(f"#{self.value_hex}")
            elif self.spec.type == "choice":
                self.combo.setCurrentText(str(value))
            self.reset_button.setEnabled(value != self.spec.default)
        finally:
            self._updating = False


class CollapsibleGroup(QWidget):
    def __init__(self, title: str, collapsed: bool, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.toggle.toggled.connect(self._toggled)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(14, 2, 0, 6)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.body)
        self._toggled(not collapsed)

    def _toggled(self, open_: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)
        self.body.setVisible(open_)


class SettingsPanel(QWidget):
    value_changed = Signal(str, object, bool)
    drag_finished = Signal()
    instance_name_changed = Signal(str)
    browse_output = Signal()

    def __init__(self, profile_label: str, parent=None):
        super().__init__(parent)
        self.editors: dict[str, ParamEditor] = {}

        output = QGroupBox("Output")
        form = QFormLayout(output)
        self.name_edit = QLineEdit()
        self.name_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[a-z0-9_]{0,48}"), self))
        self.name_edit.setToolTip("Prefix for every bone, slot, attachment, image folder and animation of this effect")
        self.name_edit.textEdited.connect(self.instance_name_changed.emit)
        self.next_name_button = QToolButton()
        self.next_name_button.setText("Next free")
        self.next_name_button.setToolTip("Pick the next unused fx_<preset>_NN name in the output folder")
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(self.next_name_button)
        form.addRow("Instance name", name_row)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        browse = QToolButton()
        browse.setText("Browse…")
        browse.setToolTip("Choose the folder packages are written to")
        browse.clicked.connect(self.browse_output.emit)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)
        form.addRow("Output folder", out_row)
        self._output_form = form
        profile = QLabel(profile_label)
        profile.setWordWrap(True)
        profile.setStyleSheet("color: #9aa0aa")
        form.addRow("Target", profile)

        buttons = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Undo last change (Ctrl+Z)")
        self.redo_button = QPushButton("Redo")
        self.redo_button.setToolTip("Redo (Ctrl+Y)")
        self.reset_button = QPushButton("Reset all")
        self.save_variant_button = QPushButton("Save variant…")
        self.load_variant_button = QPushButton("Load variant…")
        for b in (self.undo_button, self.redo_button, self.reset_button):
            buttons.addWidget(b)
        variant_row = QHBoxLayout()
        variant_row.addWidget(self.save_variant_button)
        variant_row.addWidget(self.load_variant_button)

        self.params_host = QWidget()
        self.params_layout = QVBoxLayout(self.params_host)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.params_host)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.preset_title = QLabel()
        self.preset_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.preset_title)
        layout.addWidget(output)
        layout.addLayout(buttons)
        layout.addLayout(variant_row)
        layout.addWidget(scroll, 1)

    def hide_output_folder(self) -> None:
        """For hosts that insert the FX instead of writing a package (the Anim2D editor)."""
        self._output_form.setRowVisible(1, False)
        self._output_form.setRowVisible(2, False)

    def build(self, preset: PresetDefinition) -> None:
        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # Detach now: deleteLater alone leaves the old editors painted until the next event loop pass.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.editors.clear()
        self.preset_title.setText(preset.name)
        groups: dict[str, CollapsibleGroup] = {}
        for group in GROUP_TITLES:
            specs = [s for s in preset.parameters if s.group == group]
            if not specs and group != "timing":
                continue
            box = CollapsibleGroup(GROUP_TITLES[group], collapsed=(group == "advanced"))
            groups[group] = box
            self.params_layout.addWidget(box)
            for spec in specs:
                editor = ParamEditor(spec)
                editor.changed.connect(self.value_changed.emit)
                editor.drag_finished.connect(self.drag_finished.emit)
                label = QLabel(spec.label)
                label.setToolTip(editor.toolTip())
                box.form.addRow(label, editor)
                self.editors[spec.name] = editor
        loop = QCheckBox("Loop")
        loop.setEnabled(False)
        if preset.supports_loop:
            loop.setToolTip("Looping presets are not implemented yet")
        else:
            loop.setToolTip("Not available: this preset is a one-shot effect")
        loop_label = QLabel("Loop mode")
        loop_label.setEnabled(False)
        groups["timing"].form.addRow(loop_label, loop)
        self.params_layout.addStretch(1)

    def set_values(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            if name in self.editors:
                self.editors[name].set_value(value)

    def set_undo_state(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
