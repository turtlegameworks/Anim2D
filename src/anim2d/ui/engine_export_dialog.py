"""Export for game engine: which Spine runtime version, data format and atlas naming."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QVBoxLayout)

from ..packaging.engine_export import EngineExport

ENGINES = [("Unity (spine-unity)", True), ("Godot, Cocos, Phaser, PixiJS, libGDX, Unreal, other", False)]


class EngineExportDialog(QDialog):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export for game engine")
        self.engine = QComboBox()
        for label, unity in ENGINES:
            self.engine.addItem(label, unity)
        self.version = QComboBox()
        self.version.addItems(["4.0", "3.8"])
        self.format = QComboBox()
        self.format.addItem("JSON (.json)", False)
        self.format.addItem("Binary (.skel, smaller and faster)", True)
        self.pma = QCheckBox("Premultiplied alpha")
        self.pma.setToolTip("Match your material/shader: spine-unity's default materials expect premultiplied alpha")
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.1, 2.0)
        self.scale.setSingleStep(0.25)
        self.scale.setValue(1.0)
        self.scale.setSuffix(" x")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #a9a9a9;")
        form = QFormLayout()
        form.addRow("Engine", self.engine)
        form.addRow("Spine runtime", self.version)
        form.addRow("Data", self.format)
        form.addRow("Atlas scale", self.scale)
        form.addRow(self.pma)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.info)
        layout.addWidget(buttons)
        self.name = name
        for w in (self.engine, self.version, self.format):
            w.currentIndexChanged.connect(self._update)
        self.engine.currentIndexChanged.connect(lambda: self.pma.setChecked(bool(self.engine.currentData())))
        self.pma.setChecked(True)
        self._update()

    def _update(self) -> None:
        binary_ok = self.version.currentText() == "4.0"
        if not binary_ok:
            self.format.setCurrentIndex(0)
        self.format.model().item(1).setEnabled(binary_ok)
        o = self.options()
        data = f"{self.name}{'.skel' if o.binary else '.json'}"
        atlas = f"{self.name}{'.atlas.txt' if o.unity else '.atlas'}"
        self.info.setText(f"Writes {self.name}_runtime/ with {data}, {atlas} and the atlas PNG, ready for the "
                          f"Spine {o.version} runtime of your engine.")

    def options(self) -> EngineExport:
        return EngineExport(version=self.version.currentText(), binary=bool(self.format.currentData()),
                            unity=bool(self.engine.currentData()), premultiply_alpha=self.pma.isChecked(),
                            scale=self.scale.value())
