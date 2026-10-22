"""Weights view, laid out like Spine's: the brush (mode, strength, size, feather), Smooth / Auto /
Prune, display options (Pies, Overlay, Selected), the mesh's bones (pick the one to paint, lock
bones whose weights must not change) and Bind / Remove / Update Bindings."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import mesh_ops
from .document import EditorError
from .state import EditorState
from .value_bar import ValueBar
from .weight_icons import icon

MODES = (("Add", "add"), ("Subtract", "subtract"), ("Replace", "replace"), ("Smooth", "smooth"))
PALETTE = ["#ff5a5a", "#5ad15a", "#5aa8ff", "#ffcf40", "#d987ff", "#4fe0d0", "#ff9a4a", "#b0e05a"]


def bone_color(doc, bone: str) -> QColor:
    """The bone's own colour when it has one, else a stable colour from the palette."""
    data = doc.bone(bone) if doc.has_bone(bone) else {}
    if data.get("color"):
        return QColor(f"#{data['color'][:6]}")
    names = [b["name"] for b in doc.bones]
    return QColor(PALETTE[names.index(bone) % len(PALETTE)] if bone in names else "#cccccc")


class WeightsPanel(QWidget):
    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        st = state
        self.mesh_label = QLabel()
        self.mesh_label.setObjectName("SelectionTitle")
        self.mode = QComboBox()
        self.mode.setIconSize(QSize(16, 16))
        for label, key in MODES:
            self.mode.addItem(icon(key), label, key)
        self.mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mode.activated.connect(lambda _i: setattr(st, "paint_mode", self.mode.currentData()))
        # value bars like Spine's: the number on the left, the bar on the right
        self.strength = ValueBar(0, 100, round(st.brush_strength * 100))
        self.strength.valueChanged.connect(lambda v: setattr(st, "brush_strength", v / 100))
        self.size = ValueBar(1, 400, round(st.brush_size))
        self.size.valueChanged.connect(lambda v: (setattr(st, "brush_size", float(v)), st.view_changed.emit()))
        self.feather = ValueBar(0, 100, round(st.brush_feather * 100))
        self.feather.valueChanged.connect(lambda v: setattr(st, "brush_feather", v / 100))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("Mode:", self.mode)
        form.addRow("Strength:", self.strength)
        form.addRow("Size:", self.size)
        form.addRow("Feather:", self.feather)

        self.smooth_button = QPushButton("Smooth")
        self.smooth_button.setToolTip("Even the weights out with their neighbours (selected vertices with Selected)")
        self.smooth_button.clicked.connect(lambda: self._run(self.smooth))
        self.auto_button = QPushButton("Auto")
        self.auto_button.setToolTip("Weights from the distance to the bound bones (selected vertices with Selected)")
        self.auto_button.clicked.connect(lambda: self._run(self.auto))
        self.prune_button = QPushButton("Prune")
        self.prune_button.setToolTip("Remove small weights")
        self.prune_button.clicked.connect(lambda: self._run(self.prune))
        actions = QHBoxLayout()
        for b, name in ((self.smooth_button, "smooth"), (self.auto_button, "auto"), (self.prune_button, "prune")):
            b.setIcon(icon(name))
            actions.addWidget(b)
        self.pies = QCheckBox("Pies")
        self.pies.setToolTip("Show each vertex's bone weights as a pie")
        self.overlay = QCheckBox("Overlay")
        self.overlay.setToolTip("Colour the mesh by the selected bone's weights")
        self.selected = QCheckBox("Selected")
        self.selected.setToolTip("Paint, smooth and auto only the selected vertices")
        for box, attr in ((self.pies, "weight_pies"), (self.overlay, "weight_overlay"),
                          (self.selected, "weights_selected_only")):
            box.toggled.connect(lambda on, a=attr: (setattr(st, a, on), st.view_changed.emit()))
        checks = QHBoxLayout()
        checks.addStretch(1)
        for box in (self.pies, self.overlay, self.selected):
            checks.addWidget(box)
        checks.addStretch(1)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        self.bones = QTreeWidget()
        self.bones.setColumnCount(3)
        self.bones.setHeaderLabels(["", "Bone", "🔒"])
        self.bones.setRootIsDecorated(False)
        self.bones.setColumnWidth(0, 22)
        self.bones.setColumnWidth(2, 28)
        self.bones.header().setStretchLastSection(False)
        self.bones.header().setSectionResizeMode(1, self.bones.header().ResizeMode.Stretch)
        self.bones.itemClicked.connect(self._bone_clicked)
        self.bind_button = QPushButton("Bind")
        self.bind_button.setToolTip("Bind the mesh to bones (unweighted meshes get automatic weights)")
        self.bind_button.clicked.connect(lambda: self._run(self.bind))
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Take the selected bone out of the mesh; its weight goes to the other bones")
        self.remove_button.clicked.connect(lambda: self._run(self.remove))
        self.update_button = QPushButton("Update Bindings")
        self.update_button.setToolTip("After moving bones in Setup mode: put the mesh back where it was when "
                                      "bound, and bind it to the bones where they are now")
        self.update_button.clicked.connect(lambda: self._run(self.update_bindings))
        for b, name in ((self.bind_button, "bind"), (self.remove_button, "remove"), (self.update_button, "update")):
            b.setIcon(icon(name))
        # Spine's pick button: while it is on, clicking a bone in the viewport or tree binds it to the mesh
        self.pick_button = QToolButton()
        self.pick_button.setIcon(icon("pick"))
        self.pick_button.setIconSize(QSize(16, 16))
        self.pick_button.setCheckable(True)
        self.pick_button.setToolTip("Pick bones: while on, click bones in the viewport or tree to bind them to the mesh")
        bones_header = QHBoxLayout()
        bones_header.addWidget(QLabel("Bones:"))
        bones_header.addStretch(1)
        bones_header.addWidget(self.pick_button)
        bind_row = QHBoxLayout()
        bind_row.addWidget(self.bind_button)
        bind_row.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.mesh_label)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addLayout(checks)
        layout.addWidget(line)
        layout.addLayout(bones_header)
        layout.addWidget(self.bones, 1)
        layout.addLayout(bind_row)
        layout.addWidget(self.update_button)
        for sig in (st.document_changed, st.selection_changed, st.tool_changed, st.view_changed,
                    st.vertex_selection_changed):
            sig.connect(self.refresh)
        st.selection_changed.connect(self._selection_changed)
        self.refresh()

    # --- helpers ---------------------------------------------------------------------------
    def _run(self, func) -> None:
        try:
            func()
        except EditorError as exc:
            QMessageBox.warning(self, "Anim2D", str(exc))

    def mesh(self) -> tuple[str, str]:
        mesh = self.state.weights_mesh()
        if mesh is None:
            raise EditorError("Select a mesh attachment first")
        return mesh

    def _only(self) -> set[int] | None:
        st = self.state
        if st.weights_selected_only:
            if not st.vertex_selection:
                raise EditorError("Selected is on: select vertices first")
            return set(st.vertex_selection)
        return None

    # --- actions ---------------------------------------------------------------------------
    def smooth(self) -> int:
        st = self.state
        return mesh_ops.smooth_weights(st.doc, *self.mesh(), st.brush_strength, self._only(), st.locked_bones)

    def auto(self) -> int:
        st = self.state
        slot, name = self.mesh()
        bones = mesh_ops.bound_bones(st.doc, slot, name)
        return mesh_ops.auto_weights(st.doc, slot, name, bones, self._only(), st.locked_bones)

    def prune(self, threshold: float | None = None) -> int:
        if threshold is None:
            threshold, ok = QInputDialog.getDouble(self, "Prune", "Remove weights below", 0.05, 0.0, 0.5, 3)
            if not ok:
                return 0
        return mesh_ops.prune_weights(self.state.doc, *self.mesh(), threshold)

    def bind(self, bones: list[str] | None = None) -> None:
        st = self.state
        slot, name = self.mesh()
        if bones is None:
            bones = self._choose_bones(slot, name)
            if not bones:
                return
        mesh_ops.bind_more(st.doc, slot, name, bones)
        st.weight_bone = bones[0]
        self.refresh()

    def remove(self) -> None:
        st = self.state
        bone = st.paint_bone()
        if bone is None:
            raise EditorError("Choose a bone in the list to remove")
        mesh_ops.remove_bone_weights(st.doc, *self.mesh(), bone)
        st.locked_bones.discard(bone)
        st.weight_bone = None

    def update_bindings(self) -> None:
        mesh_ops.update_bindings(self.state.doc, *self.mesh())

    def _choose_bones(self, slot: str, name: str) -> list[str]:
        doc, st = self.state.doc, self.state
        bound = set(mesh_ops.bound_bones(doc, slot, name))
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Bind {name} to bones")
        listing = QListWidget()
        suggested = {st.selection.name} if st.selection.kind == "bone" else {doc.slot(slot)["bone"]}
        for b in doc.bones:
            item = QListWidgetItem(b["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if b["name"] in suggested and b["name"] not in bound
                               else Qt.CheckState.Unchecked)
            if b["name"] in bound:
                item.setText(f"{b['name']}  (bound)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            listing.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(listing)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        return [listing.item(i).text() for i in range(listing.count())
                if listing.item(i).checkState() == Qt.CheckState.Checked]

    def _selection_changed(self) -> None:
        """With the Weights tool, selecting a bound bone (tree or Ctrl+click) makes it the paint bone.
        With the pick button on, selecting a bone that isn't bound binds it first."""
        st = self.state
        mesh = st.weights_mesh()
        if mesh is None or st.tool != "weights" or st.selection.kind != "bone":
            return
        bone = st.selection.name
        if self.pick_button.isChecked() and bone not in mesh_ops.bound_bones(st.doc, *mesh) and not st.animating:
            try:
                mesh_ops.bind_more(st.doc, *mesh, [bone])
            except EditorError as exc:
                QMessageBox.warning(self, "Anim2D", str(exc))
                return
        if bone in mesh_ops.bound_bones(st.doc, *mesh):
            st.weight_bone = bone
            self.refresh()

    def _bone_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        bone = item.data(1, Qt.ItemDataRole.UserRole)
        st = self.state
        if column == 2:
            (st.locked_bones.discard if bone in st.locked_bones else st.locked_bones.add)(bone)
        st.weight_bone = bone
        st.view_changed.emit()

    # --- refresh ---------------------------------------------------------------------------
    def refresh(self) -> None:
        st = self.state
        mesh = st.weights_mesh()
        for w in (self.smooth_button, self.auto_button, self.prune_button, self.bind_button, self.remove_button,
                  self.update_button, self.bones, self.pick_button):
            w.setEnabled(mesh is not None and not st.animating)
        self.mesh_label.setText(f"Mesh: {mesh[0]} / {mesh[1]}" if mesh else "Select a mesh (Weights tool: W)")
        self.mode.setCurrentIndex([k for _, k in MODES].index(st.paint_mode))
        self.strength.set(round(st.brush_strength * 100))
        self.size.set(round(st.brush_size))
        self.feather.set(round(st.brush_feather * 100))
        for box, attr in ((self.pies, "weight_pies"), (self.overlay, "weight_overlay"),
                          (self.selected, "weights_selected_only")):
            box.blockSignals(True)
            box.setChecked(getattr(st, attr))
            box.blockSignals(False)
        self.bones.clear()
        if mesh is None:
            return
        paint = st.paint_bone()
        weights_of = {b: sum(mesh_ops.bone_weights(st.doc, *mesh, b)) for b in mesh_ops.bound_bones(st.doc, *mesh)}
        for bone, total in weights_of.items():
            item = QTreeWidgetItem(["■", bone, "🔒" if bone in st.locked_bones else "·"])
            item.setForeground(0, bone_color(st.doc, bone))
            item.setData(1, Qt.ItemDataRole.UserRole, bone)
            item.setToolTip(1, f"total weight {total:.2f}" if total else "bound, no weight yet")
            self.bones.addTopLevelItem(item)
            if bone == paint:
                self.bones.setCurrentItem(item)
