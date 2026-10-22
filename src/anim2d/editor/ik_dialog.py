"""Dialog to create an IK constraint for a bone (one-bone or two-bone chain)."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QVBoxLayout)

from .document import Document, EditorError

NEW_TARGET = "＋ New target bone at the chain tip"


class IkDialog(QDialog):
    def __init__(self, doc: Document, end_bone: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add IK constraint")
        self.doc = doc
        self.end_bone = end_bone
        self.created: tuple[str, str] | None = None
        bone = doc.bone(end_bone)
        parent_bone = bone.get("parent")

        self.chain_combo = QComboBox()
        self.chain_combo.addItem(f"1 bone: {end_bone}", [end_bone])
        if parent_bone and parent_bone != doc.root_name:
            self.chain_combo.addItem(f"2 bones: {parent_bone} → {end_bone}", [parent_bone, end_bone])
            self.chain_combo.setCurrentIndex(1)
        self.target_combo = QComboBox()
        self.target_combo.addItem(NEW_TARGET)
        self.name_edit = QLineEdit(doc.unique_name(f"{end_bone}-ik", doc._constraint_names()))
        self.bend_check = QCheckBox("Bend positive")
        self.bend_check.setChecked(True)
        self.bend_check.setToolTip("Which way a two-bone chain bends (like a knee or an elbow)")
        self.error = QLabel()
        self.error.setStyleSheet("color: #ff6b6b")
        self.error.setWordWrap(True)
        self.chain_combo.currentIndexChanged.connect(self._fill_targets)

        form = QFormLayout()
        form.addRow("Bones", self.chain_combo)
        form.addRow("Target", self.target_combo)
        form.addRow("Name", self.name_edit)
        form.addRow("", self.bend_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("The constrained bones rotate so the chain tip reaches the target. "
                                "Move the target bone to pose the chain."))
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._fill_targets()

    def chain(self) -> list[str]:
        return list(self.chain_combo.currentData())

    def _fill_targets(self) -> None:
        chain = self.chain()
        excluded = {*chain, *self.doc.descendants(chain[0])}
        current = self.target_combo.currentText()
        self.target_combo.clear()
        self.target_combo.addItem(NEW_TARGET)
        self.target_combo.addItems([b["name"] for b in self.doc.bones if b["name"] not in excluded])
        if self.target_combo.findText(current) >= 0:
            self.target_combo.setCurrentText(current)

    def _create(self) -> None:
        options = {"bend_positive": self.bend_check.isChecked()}
        name = self.name_edit.text().strip() or None
        try:
            if self.target_combo.currentText() == NEW_TARGET:
                self.created = self.doc.add_ik_with_target(self.chain(), name, **options)
            else:
                target = self.target_combo.currentText()
                self.created = (self.doc.add_ik(self.chain(), target, name, **options), target)
        except EditorError as exc:
            self.error.setText(str(exc))
            return
        self.accept()
