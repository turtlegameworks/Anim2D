"""Insert FX dialog: pick a preset or template from the thumbnail grid, tune it, choose bone/animation/start time."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QListWidget,
                               QListWidgetItem, QSplitter, QVBoxLayout, QWidget)

from ..models.session import SettingsSession
from ..packaging.package import build_document
from ..presets.library import PresetLibrary
from ..preview.evaluator import PoseEvaluator
from ..ui.preset_browser import PresetBrowser
from ..ui.preview_widget import PreviewPanel
from ..ui.settings_panel import SettingsPanel
from .document import Document, EditorError
from .fx_insert import InsertResult, insert_fx


class InsertFxDialog(QDialog):
    def __init__(self, doc: Document, library: PresetLibrary, parent_bone: str, animation: str | None,
                 start_time: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert FX into skeleton")
        self.resize(1560, 820)
        self.doc = doc
        self.library = library
        self.sessions: dict[str, SettingsSession] = {}
        self.session: SettingsSession | None = None
        self.result: InsertResult | None = None

        # the same thumbnail grid as the FX Preset Builder (search, category, animated thumbnails)
        self.browser = PresetBrowser(library, [])
        for w in (self.browser.fav_button, self.browser.import_button, self.browser.open_templates_button):
            w.hide()
        self.browser.preset_selected.connect(self._preset_changed)
        self.bone_combo = QComboBox()
        self.bone_combo.addItems([b["name"] for b in doc.bones])
        self.bone_combo.setCurrentText(parent_bone)
        self.animation_combo = QComboBox()
        self.animation_combo.setEditable(True)
        self.animation_combo.addItems(doc.animation_names())
        self.animation_combo.setCurrentText(animation or "fx")
        self.animation_combo.setToolTip("Existing animation to add the FX keys to, or type a new name")
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0, 600)
        self.start_spin.setDecimals(3)
        self.start_spin.setSingleStep(1 / doc.fps)
        self.start_spin.setSuffix(" s")
        self.start_spin.setValue(start_time)
        self.bone_combo.currentTextChanged.connect(lambda _: self._validate())
        self.animation_combo.currentTextChanged.connect(lambda _: self._validate())
        self.start_spin.valueChanged.connect(lambda _: self._validate())

        placement = QFormLayout()
        placement.addRow("Attach to bone", self.bone_combo)
        placement.addRow("Animation", self.animation_combo)
        placement.addRow("Start at", self.start_spin)

        self.preview = PreviewPanel()
        self.settings = SettingsPanel("Inserted into the open skeleton (images copied to its images folder)")
        self.settings.hide_output_folder()
        self.settings.value_changed.connect(self._value_changed)
        self.settings.drag_finished.connect(lambda: self.session and self.session.end_merge())
        self.settings.instance_name_changed.connect(self._name_changed)
        self.settings.next_name_button.clicked.connect(self._next_name)
        self.settings.undo_button.clicked.connect(lambda: self.session and self.session.undo() and self._sync())
        self.settings.redo_button.clicked.connect(lambda: self.session and self.session.redo() and self._sync())
        self.settings.reset_button.clicked.connect(lambda: self.session and (self.session.reset(), self._sync()))
        for b in (self.settings.save_variant_button, self.settings.load_variant_button):
            b.hide()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addLayout(placement)
        left_layout.addWidget(self.preview, 1)
        splitter = QSplitter()
        splitter.addWidget(self.browser)
        splitter.addWidget(left)
        splitter.addWidget(self.settings)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 700, 460])

        self.messages = QListWidget()
        self.messages.setMaximumHeight(80)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.insert_button = self.buttons.addButton("Insert FX", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._insert)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.messages)
        layout.addWidget(self.buttons)
        self._preset_changed()

    # --- session ------------------------------------------------------------------------

    def _free_name(self, preset_id: str) -> str:
        short = preset_id.replace("_basic", "")
        used = {b["name"] for b in self.doc.bones}
        for n in range(1, 1000):
            name = f"fx_{short}_{n:02d}"
            if f"{name}_root" not in used:
                return name
        return f"fx_{short}_new"

    def select_preset(self, preset_id: str) -> None:
        self.browser.select(preset_id)

    def _preset_changed(self, preset_id: str | None = None) -> None:
        preset_id = preset_id or self.browser.current_id()
        if not preset_id:
            return
        preset = self.library.get(preset_id)
        if preset_id not in self.sessions:
            self.sessions[preset_id] = SettingsSession(preset, self._free_name(preset_id))
        self.session = self.sessions[preset_id]
        self.settings.build(preset)
        self._sync(reframe=True)

    def _sync(self, reframe: bool = False) -> None:
        s = self.session
        self.settings.set_values(s.values)
        self.settings.name_edit.setText(s.instance_name)
        self.settings.set_undo_state(s.can_undo, s.can_redo)
        self._validate(reframe=reframe)

    def _value_changed(self, name, value, merge) -> None:
        if self.session and self.session.set(name, value, merge=merge):
            self._sync()

    def _name_changed(self, text: str) -> None:
        if self.session:
            self.session.instance_name = text
            self._validate()

    def _next_name(self) -> None:
        if self.session:
            self.session.instance_name = self._free_name(self.session.preset.id)
            self._sync()

    def _validate(self, reframe: bool = False) -> bool:
        errors, warnings = [], []
        settings, report = self.session.validate() if self.session else (None, None)
        if report:
            errors += report.errors
            warnings += report.warnings
        evaluator = None
        if settings is not None:
            if f"{settings.instance_name}_root" in {b["name"] for b in self.doc.bones}:
                errors.append(f"'{settings.instance_name}' is already used in this skeleton; press Next free.")
            try:
                fx_doc, data = build_document(self.session.preset, settings)
                self.preview.canvas.cache.set_images({f"{fx_doc.prefix}/{k}": v for k, v in fx_doc.images.items()})
                evaluator = PoseEvaluator(data)
            except (ValueError, KeyError) as exc:
                errors.append(f"Generation failed: {exc}")
        if self.doc.images_dir is None:
            errors.append("The project has no images folder yet (File > Set Images Folder).")
        if not self.animation_combo.currentText().strip():
            errors.append("Choose or type an animation name.")
        self.preview.set_evaluator(evaluator, keep_framing=not reframe,
                                   message="" if evaluator else "Fix the settings to see the effect")
        self.messages.clear()
        for text in errors:
            item = QListWidgetItem(f"✖ {text}")
            item.setForeground(QColor("#ff6b6b"))
            self.messages.addItem(item)
        for text in warnings:
            item = QListWidgetItem(f"⚠ {text}")
            item.setForeground(QColor("#f0b44c"))
            self.messages.addItem(item)
        if not errors:
            end = self.start_spin.value() + (evaluator.duration if evaluator else 0)
            self.messages.addItem(f"✔ Adds keys to '{self.animation_combo.currentText().strip()}' from "
                                  f"{self.start_spin.value():.3f}s to {end:.3f}s, attached to "
                                  f"'{self.bone_combo.currentText()}'.")
        self.insert_button.setEnabled(not errors)
        return not errors

    def _insert(self) -> None:
        if not self._validate():
            return
        settings, _ = self.session.validate()
        try:
            self.result = insert_fx(self.doc, self.session.preset, settings, self.bone_combo.currentText(),
                                    self.animation_combo.currentText().strip(), self.start_spin.value())
        except EditorError as exc:
            item = QListWidgetItem(f"✖ {exc}")
            item.setForeground(QColor("#ff6b6b"))
            self.messages.insertItem(0, item)
            return
        self.accept()
