"""Physics settings for one bone: add, edit or remove its physics constraint."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout

from . import physics
from .document import Document

SPINS = [("strength", "Stiffness", 0, 10000, "How hard the spring pulls the tip back"),
         ("damping", "Damping", 0, 100, "How quickly the swinging dies down"),
         ("inertia", "Inertia", 0, 1, "1: the tip lags fully behind movement; 0: it follows at once"),
         ("mass", "Mass", 0.01, 1000, "Heavier tips swing further and slower"),
         ("gravity", "Gravity", -1000, 1000, "Pull down (negative pulls up)"),
         ("wind", "Wind", -1000, 1000, "Push to the right (negative to the left)"),
         ("limit", "Limit (°)", 0, 180, "The most the bone can swing away"),
         ("mix", "Mix", 0, 1, "0 turns the physics off")]


class PhysicsDialog(QDialog):
    def __init__(self, doc: Document, bone: str, parent=None):
        super().__init__(parent)
        self.doc, self.bone = doc, bone
        self.existing = next((c for c in physics.constraints(doc) if c["bone"] == bone), None)
        self.setWindowTitle(f"Physics on {bone}")
        values = self.existing or physics.FIELDS
        form = QFormLayout()
        self.spins: dict[str, QDoubleSpinBox] = {}
        for key, label, low, high, tip in SPINS:
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(2)
            spin.setValue(float(values.get(key, physics.FIELDS[key])))
            spin.setToolTip(tip)
            form.addRow(label, spin)
            self.spins[key] = spin
        info = QLabel("Spine 3.8/4.0 runtimes have no physics: bake it to keys (Edit > Bake physics to keys) "
                      "before exporting. The Preview shows it live.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #a9a9a9;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply" if self.existing else "Add physics")
        self.removed = False
        if self.existing:
            remove = buttons.addButton("Remove physics", QDialogButtonBox.ButtonRole.DestructiveRole)
            remove.clicked.connect(self._remove)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(buttons)

    def values(self) -> dict[str, float]:
        return {k: s.value() for k, s in self.spins.items()}

    def _apply(self) -> None:
        if self.existing:
            physics.set_physics(self.doc, self.existing["name"], **self.values())
        else:
            physics.add_physics(self.doc, self.bone, **self.values())
        self.accept()

    def _remove(self) -> None:
        physics.delete_physics(self.doc, self.existing["name"])
        self.removed = True
        self.accept()
