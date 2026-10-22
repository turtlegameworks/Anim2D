"""The SETUP / ANIMATE mode switch and the tool bar at the bottom of the viewport.

The bar is laid out like Spine's, in groups:
  Tools      Pose, Weights, Create
  Transform  Rotate / Translate / Scale / Shear: the tool, its values for the selection (editable)
             and, in Animate mode, a key button coloured by key state (green: keyed at this frame,
             yellow: keyed elsewhere, grey: no keys)
  Axes       Local / Parent / World: the directions the translate gizmo moves along, and the space
             the translate and rotate values are shown in (World shows world values)
  Compensate Bones / Images: in Setup mode, transforming a bone leaves its children / its images
             where they are
  Options    Bones / Images / Others: selectable, visible, names shown
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from . import affine
from .document import KEY_EPS, EditorError
from .state import EditorState

TOOLS = (("select", "Select", "V"), ("translate", "Translate", "W"), ("rotate", "Rotate", "E"),
         ("scale", "Scale", "R"), ("shear", "Shear", "Q"), ("pose", "Pose", "P"), ("weights", "Weights", "Shift+W"),
         ("create", "Create", "C"))  # default keys; Edit > Keyboard Shortcuts changes them
KEYED_TIMELINES = {"translate": ("translate", "translatex", "translatey"), "rotate": ("rotate",),
                   "scale": ("scale", "scalex", "scaley"), "shear": ("shear", "shearx", "sheary")}
KEY_COLORS = {"here": "#4cd964", "elsewhere": "#e0b040", "none": "#8a8a8a"}
MODE_STYLE = {
    "setup": "QPushButton:checked { background: #3d6fb6; color: white; font-weight: bold; }",
    "animate": "QPushButton:checked { background: #c0392b; color: white; font-weight: bold; }",
}
TRANSFORM_ROWS = (("rotate", "Rotate", 1), ("translate", "Translate", 2), ("scale", "Scale", 2), ("shear", "Shear", 2))


class ModeSwitch(QWidget):
    """The big SETUP | ANIMATE switch at the top left."""

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setup_button = QPushButton("SETUP")
        self.animate_button = QPushButton("ANIMATE")
        group = QButtonGroup(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        for button, mode in ((self.setup_button, "setup"), (self.animate_button, "animate")):
            button.setCheckable(True)
            button.setMinimumSize(96, 30)
            button.setStyleSheet(MODE_STYLE[mode] + " QPushButton { padding: 4px 14px; }")
            button.setToolTip(f"{mode.capitalize()} mode (Ctrl+Tab switches)")
            button.clicked.connect(lambda _=False, m=mode: self._switch(m))
            group.addButton(button)
            layout.addWidget(button)
        state.mode_changed.connect(self.refresh)
        self.refresh()

    def _switch(self, mode: str) -> None:
        try:
            self.state.set_mode(mode)
        except EditorError:
            pass
        self.refresh()

    def refresh(self) -> None:
        self.setup_button.setChecked(self.state.mode == "setup")
        self.animate_button.setChecked(self.state.mode == "animate")


class _VerticalTitle(QLabel):
    """A group title written bottom-to-top along the group's left edge."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text
        self.setObjectName("GroupTitle")
        self.setFixedWidth(16)
        self.setMinimumHeight(self.fontMetrics().horizontalAdvance(text) + 8)  # the whole word fits

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setPen(QColor("#7fc4ff"))
        p.translate(0, self.height())
        p.rotate(-90)  # x now runs up the label, y across it
        p.drawText(0, 0, self.height(), self.width(), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


def _group(title: str) -> tuple[QFrame, QGridLayout]:
    frame = QFrame()
    frame.setObjectName("ToolGroup")
    outer = QHBoxLayout(frame)
    outer.setContentsMargins(2, 3, 4, 3)
    outer.setSpacing(3)
    outer.addWidget(_VerticalTitle(title))
    grid = QGridLayout()
    grid.setHorizontalSpacing(3)
    grid.setVerticalSpacing(3)
    outer.addLayout(grid)
    return frame, grid


class ValueField(QLineEdit):
    """A number field: typing a value and pressing Enter (or leaving the field) applies it."""

    edited = Signal()

    def __init__(self):
        super().__init__()
        self.setValidator(QDoubleValidator(-1e7, 1e7, 4))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(78)
        self._value = 0.0
        self.editingFinished.connect(self._finished)

    def value(self) -> float:
        try:
            return float(self.text())
        except ValueError:
            return self._value

    def setValue(self, value: float) -> None:
        self._value = float(value)
        self.setText(f"{value:.2f}")

    def _finished(self) -> None:
        if abs(self.value() - self._value) > 1e-9:
            self._value = self.value()
            self.edited.emit()


def _value_box() -> ValueField:
    return ValueField()


class ToolPanel(QWidget):
    """The tool bar at the bottom of the viewport (see the module doc)."""

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.tool_buttons: dict[str, QToolButton] = {}
        self.key_buttons: dict[str, QToolButton] = {}
        self.value_boxes: dict[str, list[ValueField]] = {}
        self._updating = False
        self.title = QLabel()
        self.title.setObjectName("SelectionTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addStretch(1)  # centred, like Spine's bar
        tools_group = QButtonGroup(self)

        def tool_button(tool: str, label: str, key: str) -> QToolButton:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(f"{label} ({key})")
            button.setMinimumWidth(84)
            button.clicked.connect(lambda _=False, t=tool: state.set_tool(t))
            tools_group.addButton(button)
            self.tool_buttons[tool] = button
            return button

        frame, grid = _group("Tools")
        for row, (tool, label, key) in enumerate((("pose", "Pose", "P"), ("weights", "Weights", "W"),
                                                  ("create", "Create", "C"))):
            grid.addWidget(tool_button(tool, label, key), row, 0)
        bar.addWidget(frame, 0, Qt.AlignmentFlag.AlignBottom)

        frame, grid = _group("Transform")
        for row, (tool, label, count) in enumerate(TRANSFORM_ROWS):
            grid.addWidget(tool_button(tool, label, {"rotate": "R", "translate": "T", "scale": "S", "shear": "H"}[tool]),
                           row, 0)
            boxes = []
            for col in range(count):
                box = _value_box()
                box.edited.connect(lambda t=tool: self._edited(t))
                grid.addWidget(box, row, 1 + col if count == 2 else 1, 1, 1 if count == 2 else 2)
                boxes.append(box)
            self.value_boxes[tool] = boxes
            keyb = QToolButton()
            keyb.setText("◆")
            keyb.setFixedWidth(26)
            keyb.setToolTip(f"Key {label.lower()} of the selected bone at the current time")
            keyb.clicked.connect(lambda _=False, t=tool: self.key(t))
            grid.addWidget(keyb, row, 3)
            self.key_buttons[tool] = keyb
        bar.addWidget(frame, 0, Qt.AlignmentFlag.AlignBottom)

        frame, grid = _group("Axes")
        self.axes_buttons: dict[str, QPushButton] = {}
        axes_group = QButtonGroup(self)
        for row, axes in enumerate(("local", "parent", "world")):
            button = QPushButton(axes.capitalize())
            button.setCheckable(True)
            button.setToolTip({"local": "Translate along the bone's own axes",
                               "parent": "Translate along the parent bone's axes (local values)",
                               "world": "Translate along the world axes; values shown in world space"}[axes])
            button.clicked.connect(lambda _=False, a=axes: self._set_axes(a))
            axes_group.addButton(button)
            grid.addWidget(button, row, 0)
            self.axes_buttons[axes] = button
        bar.addWidget(frame, 0, Qt.AlignmentFlag.AlignBottom)

        frame, grid = _group("Compensate")
        self.compensate_buttons: dict[str, QPushButton] = {}
        for row, what in enumerate(("bones", "images")):
            button = QPushButton(what.capitalize())
            button.setCheckable(True)
            button.setToolTip(f"Setup mode: transforming a bone leaves its {'child bones' if what == 'bones' else 'images'} "
                              f"where they are")
            button.toggled.connect(lambda on, w=what: state.set_compensate(w, on))
            grid.addWidget(button, row, 0)
            self.compensate_buttons[what] = button
        bar.addWidget(frame, 0, Qt.AlignmentFlag.AlignBottom)

        frame, grid = _group("Options")
        self.option_buttons: dict[tuple[str, str], QToolButton] = {}
        for col, (what, mark, tip) in enumerate((("select", "⇱", "can be selected in the viewport"),
                                                 ("visible", "◉", "visible"), ("names", "Aa", "names shown"))):
            head = QLabel(mark)
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.setToolTip(tip)
            grid.addWidget(head, 0, 1 + col)
        for row, group in enumerate(("bones", "images", "others"), start=1):
            grid.addWidget(QLabel(group.capitalize()), row, 0)
            for col, what in enumerate(("select", "visible", "names")):
                button = QToolButton()
                button.setCheckable(True)
                button.setFixedSize(22, 20)
                button.setText("●")
                button.setToolTip(f"{group.capitalize()}: {what}")
                button.toggled.connect(lambda on, g=group, w=what: state.set_view_option(g, w, on))
                grid.addWidget(button, row, 1 + col)
                self.option_buttons[(group, what)] = button
        bar.addWidget(frame, 0, Qt.AlignmentFlag.AlignBottom)
        bar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.title)
        top.addStretch(1)
        layout.addLayout(top)
        layout.addLayout(bar)
        for sig in (state.tool_changed, state.mode_changed, state.selection_changed, state.time_changed,
                    state.document_changed, state.view_changed):
            sig.connect(self.refresh)
        self.refresh()

    # --- values ------------------------------------------------------------------------------
    def _target(self) -> tuple[str, str, str] | None:
        """('bone', name, '') or ('attachment', slot, name) for a region attachment."""
        sel = self.state.selection
        if sel.kind == "bone":
            return "bone", sel.name, ""
        if sel.kind == "attachment" and not self.state.animating:
            att = self.state.doc.attachments(sel.owner).get(sel.name)
            if att is not None and att.get("type", "region") == "region":
                return "attachment", sel.owner, sel.name
        if sel.kind == "slot":
            return "bone", self.state.doc.slot(sel.name)["bone"], ""
        return None

    def values(self) -> dict[str, list[float]] | None:
        """What the transform rows show for the selection, in the current axes."""
        target = self._target()
        if target is None:
            return None
        st = self.state
        if target[0] == "attachment":
            att = st.doc.attachments(target[1])[target[2]]
            return {"rotate": [att.get("rotation", 0)], "translate": [att.get("x", 0), att.get("y", 0)],
                    "scale": [att.get("scaleX", 1), att.get("scaleY", 1)], "shear": [0.0, 0.0]}
        ev = st.evaluator()
        x, y, rot, sx, sy, shx, shy = ev.bone_locals(st.time)[target[1]]
        if st.axes == "world":
            m = ev.bone_world(st.time)[target[1]]
            x, y = m[4], m[5]
            rot = math.degrees(math.atan2(m[1], m[0]))  # world rotation of the bone's x axis
        return {"rotate": [rot], "translate": [x, y], "scale": [sx, sy], "shear": [shx, shy]}

    def _edited(self, tool: str) -> None:
        if self._updating:
            return
        target = self._target()
        if target is None:
            return
        st = self.state
        v = [b.value() for b in self.value_boxes[tool]]
        try:
            if target[0] == "attachment":
                fields = {"rotate": {"rotation": v[0]}, "translate": {"x": v[0], "y": v[1]},
                          "scale": {"scaleX": v[0], "scaleY": v[1]}}.get(tool)
                if fields:
                    st.doc.set_attachment(target[1], target[2], **fields)
                return
            bone = target[1]
            if tool == "translate" and st.axes == "world":  # world values -> the parent's space
                parent = st.doc.bone(bone).get("parent")
                world = st.evaluator().bone_world(st.time)
                m = world[parent] if parent else affine.IDENTITY
                v = list(affine.apply(affine.invert(m), v[0], v[1]))
            if tool == "rotate" and st.axes == "world":
                current = self.values()["rotate"][0]
                v = [st.evaluator().bone_locals(st.time)[bone][2] + (v[0] - current)]
            fields = {"rotate": {"rotation": v[0]}, "translate": {"x": v[0], "y": v[1]},
                      "scale": {"scaleX": v[0], "scaleY": v[1]}, "shear": {"shearX": v[0], "shearY": v[1]}}[tool]
            st.apply_bone_pose(bone, **fields)
        except EditorError as exc:
            st.message.emit(str(exc))
            self.refresh()

    # --- keys --------------------------------------------------------------------------------
    def key_state(self, tool: str) -> str:
        """'here', 'elsewhere' or 'none' for the selected bone's timelines of this tool."""
        st = self.state
        if not st.animating or st.selection.kind != "bone":
            return "none"
        timelines = st.doc.animation(st.animation).get("bones", {}).get(st.selection.name, {})
        keys = [k for name in KEYED_TIMELINES[tool] for k in timelines.get(name, [])]
        if any(abs(k.get("time", 0) - st.time) < KEY_EPS for k in keys):
            return "here"
        return "elsewhere" if keys else "none"

    def key(self, tool: str) -> None:
        try:
            self.state.key_selected_bone((tool,))
        except EditorError as exc:
            self.state.message.emit(str(exc))

    def _set_axes(self, axes: str) -> None:
        self.state.set_axes(axes)

    def refresh(self) -> None:
        st = self.state
        animating = st.animating
        for tool, button in self.tool_buttons.items():
            button.setChecked(st.tool == tool)
        for axes, button in self.axes_buttons.items():
            button.setChecked(st.axes == axes)
        for what, button in self.compensate_buttons.items():
            button.blockSignals(True)
            button.setChecked(st.compensate[what])
            button.setEnabled(not animating)
            button.blockSignals(False)
        for (group, what), button in self.option_buttons.items():
            button.blockSignals(True)
            button.setChecked(st.view_options[group][what])
            button.blockSignals(False)
        target = self._target()
        sel = st.selection
        self.title.setText("" if target is None else
                           (sel.name if sel.kind != "attachment" else f"{sel.owner} / {sel.name}"))
        self.title.setVisible(target is not None)
        values = self.values()
        self._updating = True
        try:
            for tool, boxes in self.value_boxes.items():
                for i, box in enumerate(boxes):
                    box.setEnabled(values is not None and not (tool == "shear" and target[0] == "attachment"))
                    if values is not None and not box.hasFocus():
                        box.setValue(values[tool][i])
        finally:
            self._updating = False
        for tool, keyb in self.key_buttons.items():
            keyb.setVisible(animating)
            keyb.setEnabled(animating and sel.kind == "bone")
            keyb.setStyleSheet(f"QToolButton {{ color: {KEY_COLORS[self.key_state(tool)]}; font-size: 14px; }}")
