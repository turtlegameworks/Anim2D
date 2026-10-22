"""Unity bridge settings: which Unity project, where in Assets, and whether to export on every save."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from .document import Document, EditorError
from . import unity_bridge
from .unity_bridge import UnitySettings


class UnityDialog(QDialog):
    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Unity bridge")
        s = UnitySettings.of(doc)
        self.project = QLineEdit(s.project)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.project)
        row.addWidget(browse)
        self.folder = QLineEdit(s.folder)
        self.version = QComboBox()
        self.version.addItems(["4.0", "3.8"])
        self.version.setCurrentText(s.version)
        self.pma = QCheckBox("Premultiplied alpha (spine-unity's default materials)")
        self.pma.setChecked(s.premultiply_alpha)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.05, 4)
        self.scale.setSingleStep(0.25)
        self.scale.setValue(s.scale)
        self.on_save = QCheckBox("Export to Unity every time the project is saved")
        self.on_save.setChecked(s.on_save)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #a9a9a9;")
        form = QFormLayout()
        form.addRow("Unity project", row)
        form.addRow("Folder in project", self.folder)
        form.addRow("spine-unity version", self.version)
        form.addRow("Atlas scale", self.scale)
        form.addRow(self.pma)
        form.addRow(self.on_save)
        script = QPushButton("Install auto-refresh script in Unity")
        script.setToolTip("Adds Assets/Editor/Anim2DBridge.cs, which refreshes Unity's assets after each export "
                          "even while Unity is in the background")
        script.clicked.connect(self._install)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        export = buttons.addButton("Save && export now", QDialogButtonBox.ButtonRole.ApplyRole)
        export.clicked.connect(self._export)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(script)
        layout.addWidget(self.info)
        layout.addWidget(buttons)
        self.project.textChanged.connect(self._update)
        self.folder.textChanged.connect(self._update)
        self.exported: Path | None = None
        self._update()

    def settings(self) -> UnitySettings:
        return UnitySettings(project=self.project.text().strip(), folder=self.folder.text().strip(),
                             version=self.version.currentText(), premultiply_alpha=self.pma.isChecked(),
                             on_save=self.on_save.isChecked(), scale=self.scale.value())

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Unity project folder", self.project.text())
        if path:
            self.project.setText(path)
            runtime = unity_bridge.detect_runtime(Path(path))
            if runtime in ("3.8", "4.0"):
                self.version.setCurrentText(runtime)

    def _update(self) -> None:
        s = self.settings()
        if not s.project:
            self.info.setText("Choose the Unity project (the folder with Assets/ and ProjectSettings/).")
            return
        try:
            target = unity_bridge.target_folder(self.doc, s)
        except EditorError as e:
            self.info.setText(str(e))
            return
        runtime = unity_bridge.detect_runtime(Path(s.project))
        rel = target.relative_to(Path(s.project)).as_posix()
        note = f"spine-unity {runtime} found." if runtime else "spine-unity isn't installed in this project yet."
        if runtime and runtime not in ("3.8", "4.0"):
            note += f" Anim2D exports 3.8/4.0 data; spine-unity {runtime} may not read it."
        self.info.setText(f"Writes {rel}/{target.name}.json, .atlas.txt and PNG pages. {note}")

    def _save(self) -> None:
        self.settings().store(self.doc)
        self.accept()

    def _export(self) -> None:
        s = self.settings()
        s.store(self.doc)
        try:
            self.exported, warnings = unity_bridge.export_to_unity(self.doc, s)
        except EditorError as e:
            QMessageBox.warning(self, "Unity bridge", str(e))
            return
        text = f"Exported to {self.exported}"
        if warnings:
            text += "\n\n" + "\n".join(warnings[:10])
        QMessageBox.information(self, "Unity bridge", text)
        self.accept()

    def _install(self) -> None:
        try:
            script = unity_bridge.install_refresh_script(Path(self.project.text().strip()))
        except EditorError as e:
            QMessageBox.warning(self, "Unity bridge", str(e))
            return
        QMessageBox.information(self, "Unity bridge", f"Installed {script}")
