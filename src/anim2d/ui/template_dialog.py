"""Dialog that registers a Spine 3.8 JSON export as a user template."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout)

from ..models.preset import ID_PATTERN, PresetError
from ..presets.library import KNOWN_GENERATORS, PresetLibrary
from ..templates.register import ExportAnalysis, analyze_export, register_template, suggest_template_id

INTRO = (
    "<b>Edit a preset in Spine and bring it back</b><ol style='margin-left:-20px'>"
    "<li>Generate a package here (or use any Spine 3.8 project) and import it into Spine.</li>"
    "<li>Edit bones, slots, images and keys in Spine.</li>"
    "<li>In Spine choose <b>Export → JSON</b> with <b>Nonessential data</b> ticked.</li>"
    "<li>Pick that JSON below. The images folder is found from the export when possible.</li></ol>"
    "Templates keep your Spine data and get the common controls: colour, opacity, transform, "
    "mirror, duration and start delay."
)
ROLES = ("primary", "secondary", "keep")


class TemplateImportDialog(QDialog):
    def __init__(self, library: PresetLibrary, user_root: Path, start_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import template from Spine")
        self.resize(720, 760)
        self.library = library
        self.user_root = Path(user_root)
        self.start_dir = start_dir
        self.analysis: ExportAnalysis | None = None
        self.registered_id: str | None = None
        self._id_edited = False

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self.json_edit = QLineEdit()
        self.json_edit.setReadOnly(True)
        json_browse = QToolButton()
        json_browse.setText("…")
        json_browse.clicked.connect(self._browse_json)
        json_row = QHBoxLayout()
        json_row.addWidget(self.json_edit, 1)
        json_row.addWidget(json_browse)

        self.images_edit = QLineEdit()
        self.images_edit.setReadOnly(True)
        images_browse = QToolButton()
        images_browse.setText("…")
        images_browse.clicked.connect(self._browse_images)
        images_row = QHBoxLayout()
        images_row.addWidget(self.images_edit, 1)
        images_row.addWidget(images_browse)

        self.animation_combo = QComboBox()
        self.animation_combo.setEnabled(False)
        self.animation_combo.activated.connect(lambda _: self._analyze())
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._name_edited)
        self.id_edit = QLineEdit()
        self.id_edit.textEdited.connect(self._id_text_edited)
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(sorted({"custom", *(p.category for p in library.presets.values())}))
        self.category_combo.setCurrentText("custom")
        self.description_edit = QLineEdit()
        self.replace_check = QCheckBox("Replace existing template (the old version is moved to _backup)")
        self.replace_check.setVisible(False)
        self.replace_check.toggled.connect(self._update_state)

        form = QFormLayout()
        form.addRow("Spine JSON export", json_row)
        form.addRow("Images folder", images_row)
        form.addRow("Animation", self.animation_combo)
        form.addRow("Template name", self.name_edit)
        form.addRow("Template id", self.id_edit)
        form.addRow("Category", self.category_combo)
        form.addRow("Description", self.description_edit)
        form.addRow("", self.replace_check)

        self.roles_table = QTableWidget(0, 3)
        self.roles_table.setHorizontalHeaderLabels(["Slot", "Colours used", "Follows"])
        self.roles_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.roles_table.verticalHeader().setVisible(False)
        self.roles_table.setToolTip("Which colour control recolours each slot. 'keep' leaves the colours "
                                    "you animated in Spine untouched.")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #b8bcc6")
        self.messages = QListWidget()
        self.messages.setMaximumHeight(110)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.register_button = self.buttons.addButton("Register template", QDialogButtonBox.ButtonRole.AcceptRole)
        self.register_button.setEnabled(False)
        self.buttons.accepted.connect(self._register)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(QLabel("Tint roles (detected from the slot colours; change if needed)"))
        layout.addWidget(self.roles_table, 1)
        layout.addWidget(self.summary)
        layout.addWidget(self.messages)
        layout.addWidget(self.buttons)
        self._update_state()

    # --- input ---------------------------------------------------------------------------

    def _browse_json(self) -> None:
        start = str(self.start_dir or Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose Spine JSON export", start, "Spine JSON (*.json)")
        if path:
            self.set_json(Path(path))

    def _browse_images(self) -> None:
        start = self.images_edit.text() or self.json_edit.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose images folder", start)
        if folder:
            self.images_edit.setText(folder)
            self._analyze()

    def set_json(self, path: Path, images_dir: Path | None = None) -> None:
        self.json_edit.setText(str(path))
        self.images_edit.setText(str(images_dir) if images_dir else "")
        self.animation_combo.clear()
        self._analyze()
        if not self.name_edit.text():
            self.name_edit.setText(path.stem.replace("_", " ").replace("-", " ").title())
            self._name_edited(self.name_edit.text())

    def _name_edited(self, text: str) -> None:
        if not self._id_edited:
            self.id_edit.setText(suggest_template_id(text))
        self._update_state()

    def _id_text_edited(self, _text: str) -> None:
        self._id_edited = True
        self._update_state()

    # --- analysis ------------------------------------------------------------------------

    def _analyze(self) -> None:
        if not self.json_edit.text():
            return
        animation = self.animation_combo.currentText() or None
        images = Path(self.images_edit.text()) if self.images_edit.text() else None
        self.analysis = a = analyze_export(Path(self.json_edit.text()), images, animation)
        if a.animations and self.animation_combo.count() != len(a.animations):
            self.animation_combo.blockSignals(True)
            self.animation_combo.clear()
            self.animation_combo.addItems(a.animations)
            self.animation_combo.setCurrentIndex(-1 if a.animation is None else a.animations.index(a.animation))
            self.animation_combo.blockSignals(False)
        self.animation_combo.setEnabled(len(a.animations) > 1)
        if a.images_dir and not self.images_edit.text():
            self.images_edit.setText(str(a.images_dir))

        self.roles_table.setRowCount(len(a.slots))
        for row, slot in enumerate(a.slots):
            name_item = QTableWidgetItem(slot.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            colors = QTableWidgetItem(", ".join(f"#{c}" for c in slot.colors[:4]) +
                                      (" …" if len(slot.colors) > 4 else ""))
            colors.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if len(slot.colors) == 1:
                colors.setBackground(QColor(f"#{slot.colors[0]}"))
                light = QColor(f"#{slot.colors[0]}").lightness() > 128
                colors.setForeground(QColor("#000") if light else QColor("#fff"))
            combo = QComboBox()
            combo.addItems(ROLES)
            combo.setCurrentText(slot.role)
            self.roles_table.setItem(row, 0, name_item)
            self.roles_table.setItem(row, 1, colors)
            self.roles_table.setCellWidget(row, 2, combo)
        if a.ok:
            prefix = f" Removed instance prefix '{a.old_prefix}_'." if a.old_prefix else ""
            self.summary.setText(
                f"Spine {a.spine_version} · animation '{a.animation}' · {a.source_duration:.3f}s · "
                + ", ".join(f"{v} {k}" for k, v in a.counts.items()) + f".{prefix}")
        else:
            self.summary.setText("")
        self._update_state()

    def roles(self) -> dict[str, str]:
        result = {}
        for row in range(self.roles_table.rowCount()):
            result[self.roles_table.item(row, 0).text()] = self.roles_table.cellWidget(row, 2).currentText()
        return result

    def _update_state(self) -> None:
        errors, warnings = [], []
        if self.analysis is not None:
            errors += self.analysis.errors
            warnings += self.analysis.warnings
        preset_id = self.id_edit.text().strip()
        existing = self.library.presets.get(preset_id)
        exists_in_user = (self.user_root / preset_id).exists() if preset_id else False
        self.replace_check.setVisible(exists_in_user)
        if self.analysis is not None:
            if not ID_PATTERN.match(preset_id):
                errors.append("Template id: lowercase letters, digits and underscores, starting with a letter.")
            elif existing is not None and existing.source == "built-in":
                errors.append(f"'{preset_id}' is a built-in preset id; choose another id.")
            elif exists_in_user and not self.replace_check.isChecked():
                errors.append(f"A template '{preset_id}' already exists. Choose another id or tick Replace.")
            if not self.name_edit.text().strip():
                errors.append("Give the template a name.")
        self.messages.clear()
        for text in errors:
            item = QListWidgetItem(f"✖ {text}")
            item.setForeground(QColor("#ff6b6b"))
            self.messages.addItem(item)
        for text in warnings:
            item = QListWidgetItem(f"⚠ {text}")
            item.setForeground(QColor("#f0b44c"))
            self.messages.addItem(item)
        if self.analysis is None:
            self.messages.addItem("Choose a Spine JSON export to begin.")
        elif not errors:
            self.messages.addItem("✔ Ready to register")
        self.register_button.setEnabled(self.analysis is not None and not errors)

    def _register(self) -> None:
        if self.analysis is None:
            return
        try:
            preset = register_template(
                self.analysis, self.user_root, preset_id=self.id_edit.text().strip(),
                name=self.name_edit.text(), category=self.category_combo.currentText(),
                description=self.description_edit.text(), roles=self.roles(),
                replace=self.replace_check.isChecked(), known_generators=KNOWN_GENERATORS)
        except PresetError as exc:
            item = QListWidgetItem(f"✖ {exc}")
            item.setForeground(QColor("#ff6b6b"))
            self.messages.insertItem(0, item)
            return
        self.registered_id = preset.id
        self.accept()
