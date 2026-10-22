"""Create a transform or path constraint: pick the constrained bones and the target."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout)

from .document import Document, EditorError

HELP = {
    "transform": "The bones copy the target bone's rotation, position, scale and shear, mixed by the mix "
                 "values. Offsets and local/relative modes are set in Properties afterwards.",
    "path": "The bones are placed along the path attachment of the target slot, in order. Create a path on a "
            "slot first (right-click the slot → Add path).",
}


class ConstraintDialog(QDialog):
    def __init__(self, doc: Document, kind: str, bone: str | None = None, parent=None):
        super().__init__(parent)
        self.doc, self.kind = doc, kind
        self.created: str | None = None
        self.setWindowTitle("New transform constraint" if kind == "transform" else "New path constraint")
        info = QLabel(HELP[kind])
        info.setWordWrap(True)
        self.bones = QListWidget()
        chain = {bone, *doc.descendants(bone)} if (bone and kind == "path") else {bone}
        for b in doc.bones:
            if b["name"] == doc.root_name:
                continue
            item = QListWidgetItem(b["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if b["name"] in chain else Qt.CheckState.Unchecked)
            self.bones.addItem(item)
        self.target = QComboBox()
        if kind == "transform":
            self.target.addItems([b["name"] for b in doc.bones if b["name"] != bone])
        else:
            with_path = [s["name"] for s in doc.slots
                         if any(a.get("type") == "path" for a in doc.attachments(s["name"]).values())]
            self.target.addItems(with_path or [s["name"] for s in doc.slots])
        self.name = QLineEdit()
        self.name.setPlaceholderText("automatic")
        form = QFormLayout()
        form.addRow("Constrained bones", self.bones)
        form.addRow("Target bone" if kind == "transform" else "Target slot (path)", self.target)
        form.addRow("Name", self.name)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def chosen_bones(self) -> list[str]:
        """Checked bones in skeleton order (a path places them in this order)."""
        return [self.bones.item(i).text() for i in range(self.bones.count())
                if self.bones.item(i).checkState() == Qt.CheckState.Checked]

    def accept(self) -> None:
        bones, target = self.chosen_bones(), self.target.currentText()
        name = self.name.text().strip() or None
        try:
            if not target:
                raise EditorError("Choose a target")
            if self.kind == "transform":
                self.created = self.doc.add_transform(bones, target, name)
            else:
                self.created = self.doc.add_path(bones, target, name)
        except EditorError as exc:
            QMessageBox.warning(self, "Anim2D", str(exc))
            return
        super().accept()
