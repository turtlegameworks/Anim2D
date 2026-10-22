"""Properties panel for the current selection. In Animate mode, bone/slot edits set keys."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..models.effect import Color
from .document import EditorError
from .state import EditorState


def _spin(lo: float, hi: float, decimals: int = 2, step: float = 1.0, suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setKeyboardTracking(False)
    if suffix:
        s.setSuffix(suffix)
    return s


class PropertiesPanel(QWidget):
    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #9aa0aa")
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(self.body)
        layout.addStretch(1)
        self._updaters: list[Callable[[], None]] = []
        self._updating = False
        state.selection_changed.connect(self.rebuild)
        state.mode_changed.connect(self.rebuild)
        state.vertex_selection_changed.connect(self.rebuild)
        state.document_changed.connect(self.refresh)
        state.time_changed.connect(self.refresh)
        self.rebuild()

    # --- helpers ------------------------------------------------------------------------

    def _run(self, func: Callable[[], Any]) -> None:
        if self._updating:
            return
        try:
            func()
        except EditorError as exc:
            QMessageBox.warning(self, "Anim2D", str(exc))
            self.refresh()

    def _clear(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._updaters.clear()

    def _number(self, label: str, getter: Callable[[], float], setter: Callable[[float], None],
                lo=-100000.0, hi=100000.0, decimals=2, step=1.0, suffix="") -> None:
        spin = _spin(lo, hi, decimals, step, suffix)
        spin.valueChanged.connect(lambda v: self._run(lambda: setter(v)))
        self.form.addRow(label, spin)

        def update():
            spin.blockSignals(True)
            spin.setValue(getter())
            spin.blockSignals(False)
        self._updaters.append(update)

    def _text(self, label: str, getter: Callable[[], str], setter: Callable[[str], None]) -> None:
        edit = QLineEdit()
        edit.editingFinished.connect(lambda: edit.text() != getter() and self._run(lambda: setter(edit.text().strip())))
        self.form.addRow(label, edit)

        def update():
            if not edit.hasFocus():
                edit.setText(getter())
        self._updaters.append(update)

    def _combo(self, label: str, items: Callable[[], list[str]], getter: Callable[[], str],
               setter: Callable[[str], None]) -> None:
        combo = QComboBox()
        combo.activated.connect(lambda _i: self._run(lambda: setter(combo.currentText())))
        self.form.addRow(label, combo)

        def update():
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items())
            combo.setCurrentText(getter())
            combo.blockSignals(False)
        self._updaters.append(update)

    def _color(self, label: str, getter: Callable[[], str], setter: Callable[[str], None]) -> None:
        button = QPushButton()

        def pick():
            c = Color.from_hex(getter())
            chosen = QColorDialog.getColor(QColor.fromRgbF(c.r, c.g, c.b, c.a), self, label,
                                           QColorDialog.ColorDialogOption.ShowAlphaChannel)
            if chosen.isValid():
                self._run(lambda: setter(Color(chosen.redF(), chosen.greenF(), chosen.blueF(), chosen.alphaF()).to_hex()))
        button.clicked.connect(pick)
        self.form.addRow(label, button)

        def update():
            value = getter()
            c = Color.from_hex(value)
            fg = "#000" if c.r * 0.3 + c.g * 0.59 + c.b * 0.11 > 0.55 else "#fff"
            button.setText(f"#{value}")
            button.setStyleSheet(f"background: #{value[:6]}; color: {fg};")
        self._updaters.append(update)

    def _check(self, label: str, getter: Callable[[], bool], setter: Callable[[bool], None], tip: str = "") -> None:
        box = QCheckBox()
        box.setToolTip(tip)
        box.toggled.connect(lambda v: self._run(lambda: setter(v)))
        self.form.addRow(label, box)

        def update():
            box.blockSignals(True)
            box.setChecked(bool(getter()))
            box.blockSignals(False)
        self._updaters.append(update)

    def _button_row(self, *buttons: tuple[str, Callable[[], Any]]) -> None:
        row = QHBoxLayout()
        for text, func in buttons:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=func: self._run(f))
            row.addWidget(b)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.form.addRow(holder)

    # --- building -----------------------------------------------------------------------

    def rebuild(self) -> None:
        self._clear()
        sel = self.state.selection
        st, doc = self.state, self.state.doc
        animating = st.animating
        self.hint.setText(f"Animate mode: changes set keys in '{st.animation}' at the current time."
                          if animating and sel.kind in ("bone", "slot", "ik", "transform", "path") else "")
        if sel.kind == "bone":
            self._build_bone(sel.name)
        elif sel.kind == "ik":
            self._build_ik(sel.name)
        elif sel.kind == "skin":
            self._build_skin(sel.name)
        elif sel.kind == "transform":
            self._build_transform(sel.name)
        elif sel.kind == "path":
            self._build_path_constraint(sel.name)
        elif sel.kind == "slot":
            self._build_slot(sel.name)
        elif sel.kind == "attachment":
            self._build_attachment(sel.owner, sel.name)
        elif sel.kind == "animation":
            self.title.setText(f"Animation  {sel.name}")
            self._text("Name", lambda: sel.name, lambda v: (doc.rename_animation(sel.name, v), st.select("animation", v)))
            self.form.addRow("Duration", QLabel(f"{doc.animation_duration(sel.name):.3f} s"))
            self._button_row(("Edit", lambda: (st.set_animation(sel.name), st.set_mode("animate"))),
                             ("Duplicate", lambda: doc.duplicate_animation(sel.name)),
                             ("Delete", lambda: doc.delete_animation(sel.name)))
        elif sel.kind == "event":
            self.title.setText(f"Event  {sel.name}")
            ev = doc.data["events"][sel.name]
            self._number("Int", lambda: ev.get("int", 0), lambda v: (doc.checkpoint("Edit event"), ev.__setitem__("int", int(v)), doc.notify()),
                         -1e9, 1e9, 0)
            self._number("Float", lambda: ev.get("float", 0), lambda v: (doc.checkpoint("Edit event"), ev.__setitem__("float", v), doc.notify()))
            if animating:
                self._button_row((f"Key at current time", lambda: doc.key_event(st.animation, sel.name, st.time)))
        elif sel.kind == "image":
            self.title.setText(f"Image  {sel.name}")
            file = doc.image_file(sel.name)
            self.form.addRow("File", QLabel(str(file) if file else "missing"))
            self.hint.setText("Double-click the image (or use the button) to attach it to the selected bone.")
        else:
            self.title.setText("Skeleton")
            self._number("Frames per second", lambda: doc.fps,
                         lambda v: (doc.checkpoint("Set FPS"), doc.data["skeleton"].__setitem__("fps", v), doc.notify()),
                         1, 240, 0)
            self.form.addRow("Images folder", QLabel(str(doc.images_dir) if doc.images_dir else "not set"))
            self.form.addRow("Bones / slots", QLabel(f"{len(doc.bones)} / {len(doc.slots)}"))
        self.refresh()

    def _build_bone(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"Bone  {name}")
        bone = lambda: doc.bone(name)  # noqa: E731
        posed = lambda i: st.evaluator().bone_locals(st.time)[name][i]  # noqa: E731
        self._text("Name", lambda: name, lambda v: (doc.rename_bone(name, v), st.select("bone", v)))
        if name != doc.root_name:
            self._combo("Parent", lambda: [b["name"] for b in doc.bones if b["name"] != name and
                                           b["name"] not in doc.descendants(name)],
                        lambda: bone().get("parent", ""), lambda v: doc.reparent_bone(name, v))
        self._number("X", lambda: posed(0), lambda v: st.apply_bone_pose(name, x=v))
        self._number("Y", lambda: posed(1), lambda v: st.apply_bone_pose(name, y=v))
        self._number("Rotation", lambda: posed(2), lambda v: st.apply_bone_pose(name, rotation=v), suffix=" °")
        self._number("Scale X", lambda: posed(3), lambda v: st.apply_bone_pose(name, scaleX=v), decimals=3, step=0.05)
        self._number("Scale Y", lambda: posed(4), lambda v: st.apply_bone_pose(name, scaleY=v), decimals=3, step=0.05)
        self._number("Shear X", lambda: posed(5), lambda v: st.apply_bone_pose(name, shearX=v), suffix=" °")
        self._number("Shear Y", lambda: posed(6), lambda v: st.apply_bone_pose(name, shearY=v), suffix=" °")
        if not st.animating:
            self._number("Length", lambda: bone().get("length", 0), lambda v: doc.set_bone(name, length=v), 0, 100000)
            self._color("Bone colour", lambda: bone().get("color", "989898ff"), lambda v: doc.set_bone(name, color=v))
            if name != doc.root_name:
                for label, flag in (("Inherit rotation", "rotation"), ("Inherit scale", "scale"),
                                    ("Inherit reflection", "reflection")):
                    self._check(label, lambda f=flag: doc.inherit_flags(name)[f],
                                lambda v, f=flag: doc.set_inherit(name, **{f: v}),
                                "Spine 4.0 transform modes: reflection counts only without inherited scale")
                self._skin_only("bone", name)
            self._check("Select", lambda: name not in st.unselectable,
                        lambda v: st.set_selectable(name, v), "Can be picked in the viewport (editor only)")
        else:
            self._button_row(("Key rotate", lambda: st.key_selected_bone(("rotate",))),
                             ("Key move", lambda: st.key_selected_bone(("translate",))),
                             ("Key scale", lambda: st.key_selected_bone(("scale",))),
                             ("Key shear", lambda: st.key_selected_bone(("shear",))))

    def _build_ik(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"IK constraint  {name}")
        c = lambda: doc.ik(name)  # noqa: E731

        def posed(field: str, default: Any) -> Any:
            if st.animating:
                values = st.evaluator().ik_state(st.time).get(name)
                if values:
                    return {"bendPositive": values["bend"] > 0}.get(field, values.get(field, default))
            return c().get(field, default)

        def setter(field: str) -> Callable[[Any], None]:
            if st.animating:
                return lambda v: doc.set_ik_key(st.animation, name, st.time, **{field: v})
            return lambda v: doc.set_ik(name, **{field: v})

        self._text("Name", lambda: name, lambda v: (doc.rename_ik(name, v), st.select("ik", v)))
        self.form.addRow("Bones", QLabel(" → ".join(c()["bones"])))
        if not st.animating:
            self._combo("Target", lambda: [b["name"] for b in doc.bones if b["name"] not in c()["bones"]
                                           and b["name"] not in doc.descendants(c()["bones"][0])],
                        lambda: c()["target"], lambda v: doc.set_ik(name, target=v))
        self._number("Mix", lambda: posed("mix", 1), setter("mix"), 0, 1, 3, 0.05)
        if len(c()["bones"]) == 2:
            self._number("Softness", lambda: posed("softness", 0), setter("softness"), 0, 100000, 2, 1)
            self._check("Bend positive", lambda: posed("bendPositive", True), setter("bendPositive"),
                        "Which way the two-bone chain bends")
        self._check("Stretch", lambda: posed("stretch", False), setter("stretch"),
                    "Scale the bones longer when the target is out of reach")
        if len(c()["bones"]) == 1:
            self._check("Compress", lambda: posed("compress", False), setter("compress"),
                        "Scale the bone shorter when the target is closer than its length")
            if not st.animating:
                self._check("Uniform", lambda: c().get("uniform", False), lambda v: doc.set_ik(name, uniform=v),
                            "Stretch/compress scale both axes")
        if not st.animating:
            self._skin_only("ik", name)
        if st.animating:
            self._button_row(("Key IK", lambda: doc.set_ik_key(st.animation, name, st.time)),
                             ("Select target", lambda: st.select("bone", c()["target"])))
        else:
            self._button_row(("Select target", lambda: st.select("bone", c()["target"])),
                             ("Delete", lambda: doc.delete_ik(name)))

    def _build_skin(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"Skin  {name}")
        skin = lambda: doc.skin_data(name)  # noqa: E731
        attachments = sum(len(v) for v in skin().get("attachments", {}).values())
        self.form.addRow("Attachments", QLabel(str(attachments)))
        if name == "default":
            self.hint.setText("Attachments every skin shows unless a skin has its own under the same name.")
        else:
            self._text("Name", lambda: name, lambda v: (doc.rename_skin(name, v), st.select("skin", v)))
            self.hint.setText("Skin bones and constraints are only active while this skin is. List them "
                              "comma-separated; listing one marks it skin-only.")
            for label, key in (("Bones", "bones"), ("IK", "ik"), ("Transform", "transform"), ("Path", "path")):
                self._text(label, lambda k=key: ", ".join(skin().get(k, [])),
                           lambda v, k=key: doc.set_skin_members(name, **{k: [n.strip() for n in v.split(",")
                                                                               if n.strip()]}))
        editing = doc.edit_skin == name
        self.form.addRow("Editing", QLabel("yes: new attachments go into this skin" if editing else "no"))
        buttons = [] if editing else [("Edit and show this skin", lambda: doc.set_edit_skin(name))]
        buttons.append(("Duplicate", lambda: doc.duplicate_skin(name, doc.unique_name(f"{name}-copy",
                                                                                      set(doc.skin_names())))))
        if name != "default":
            buttons.append(("Delete", lambda: doc.delete_skin(name)))
        self._button_row(*buttons)

    def _skin_only(self, kind: str, name: str) -> None:
        """Setup mode: mark a bone or constraint as active only in skins that list it."""
        doc = self.state.doc

        def required() -> bool:  # read fresh: undo replaces the document data
            items = doc.bones if kind == "bone" else doc.data.get(kind, [])
            return next((bool(i.get("skin", False)) for i in items if i["name"] == name), False)
        self._check("Skin only", required, lambda v: doc.set_skin_required(kind, name, v),
                    "Only active while a skin that lists it is active")

    def _bones_row(self, getter: Callable[[], list[str]], setter: Callable[[list[str]], None]) -> None:
        """Constrained bones as text; edited as a comma-separated list (order matters for paths)."""
        self._text("Bones", lambda: ", ".join(getter()),
                   lambda v: setter([b.strip() for b in v.split(",") if b.strip()]))

    def _build_transform(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"Transform constraint  {name}")
        c = lambda: doc.transform_constraint(name)  # noqa: E731
        mixes = (("Mix rotate", "mixRotate", "mix_rotate"), ("Mix X", "mixX", "mix_x"), ("Mix Y", "mixY", "mix_y"),
                 ("Mix scale X", "mixScaleX", "mix_scale_x"), ("Mix scale Y", "mixScaleY", "mix_scale_y"),
                 ("Mix shear Y", "mixShearY", "mix_shear_y"))

        def mix_value(field: str, solver_name: str) -> float:
            if st.animating:
                posed = st.evaluator().transform_state(st.time).get(name)
                if posed:
                    return posed[solver_name]
            defaults = {"mixY": c().get("mixX", 1), "mixScaleY": c().get("mixScaleX", 1)}
            return c().get(field, defaults.get(field, 1))

        self._text("Name", lambda: name, lambda v: (doc.rename_constraint(name, v), st.select("transform", v)))
        if not st.animating:
            self._bones_row(lambda: c()["bones"], lambda v: doc.set_transform(name, bones=v))
            self._combo("Target", lambda: [b["name"] for b in doc.bones if b["name"] not in c()["bones"]],
                        lambda: c()["target"], lambda v: doc.set_transform(name, target=v))
            self._check("Local", lambda: c().get("local", False), lambda v: doc.set_transform(name, local=v),
                        "Copy the target's local values instead of its world transform")
            self._check("Relative", lambda: c().get("relative", False), lambda v: doc.set_transform(name, relative=v),
                        "Add the target's values instead of matching them")
            self._skin_only("transform", name)
            for label, field, kw in (("Offset rotation", "rotation", {"suffix": " °"}), ("Offset X", "x", {}),
                                     ("Offset Y", "y", {}),
                                     ("Offset scale X", "scaleX", {"decimals": 3, "step": 0.05}),
                                     ("Offset scale Y", "scaleY", {"decimals": 3, "step": 0.05}),
                                     ("Offset shear Y", "shearY", {"suffix": " °"})):
                self._number(label, lambda f=field: c().get(f, 0), lambda v, f=field: doc.set_transform(name, **{f: v}),
                             **kw)
        for label, field, solver_name in mixes:
            setter = (lambda v, f=field: doc.set_transform_key(st.animation, name, st.time, **{f: v})) \
                if st.animating else (lambda v, f=field: doc.set_transform(name, **{f: v}))
            self._number(label, lambda f=field, sn=solver_name: mix_value(f, sn), setter, 0, 1, 3, 0.05)
        if st.animating:
            self._button_row(("Key mixes", lambda: doc.set_transform_key(st.animation, name, st.time)),
                             ("Select target", lambda: st.select("bone", c()["target"])))
        else:
            self._button_row(("Select target", lambda: st.select("bone", c()["target"])),
                             ("Delete", lambda: doc.delete_constraint(name)))

    def _build_path_constraint(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"Path constraint  {name}")
        c = lambda: doc.path_constraint(name)  # noqa: E731

        def posed(field: str, solver_name: str, default: float) -> float:
            if st.animating:
                value = st.evaluator().path_state(st.time).get(name, {}).get(solver_name)
                if value is not None:
                    return value
            return c().get(field, default)

        def setter(field: str, timeline: str) -> Callable[[float], None]:
            if not st.animating:
                return lambda v: doc.set_path(name, **{field: v})
            if timeline == "mix":
                return lambda v: doc.set_path_key(st.animation, name, "mix", st.time, **{field: v})
            return lambda v: doc.set_path_key(st.animation, name, timeline, st.time, value=v)

        self._text("Name", lambda: name, lambda v: (doc.rename_constraint(name, v), st.select("path", v)))
        if not st.animating:
            self._bones_row(lambda: c()["bones"], lambda v: doc.set_path(name, bones=v))
            self._combo("Target slot", lambda: [s["name"] for s in doc.slots], lambda: c()["target"],
                        lambda v: doc.set_path(name, target=v))
            self._combo("Position mode", lambda: ["percent", "fixed"], lambda: c().get("positionMode", "percent"),
                        lambda v: doc.set_path(name, positionMode=v))
            self._combo("Spacing mode", lambda: ["length", "fixed", "percent", "proportional"],
                        lambda: c().get("spacingMode", "length"), lambda v: doc.set_path(name, spacingMode=v))
            self._combo("Rotate mode", lambda: ["tangent", "chain", "chainScale"],
                        lambda: c().get("rotateMode", "tangent"), lambda v: doc.set_path(name, rotateMode=v))
            self._number("Offset rotation", lambda: c().get("rotation", 0), lambda v: doc.set_path(name, rotation=v),
                         suffix=" °")
            self._skin_only("path", name)
        percent = c().get("positionMode", "percent") == "percent"
        self._number("Position", lambda: posed("position", "position", 0), setter("position", "position"),
                     decimals=4 if percent else 2, step=0.01 if percent else 1)
        self._number("Spacing", lambda: posed("spacing", "spacing", 0), setter("spacing", "spacing"),
                     decimals=4, step=0.01 if c().get("spacingMode") in ("percent", "proportional") else 1)
        for label, field, solver_name in (("Mix rotate", "mixRotate", "mix_rotate"), ("Mix X", "mixX", "mix_x"),
                                          ("Mix Y", "mixY", "mix_y")):
            self._number(label, lambda f=field, sn=solver_name: posed(f, sn, 1), setter(field, "mix"), 0, 1, 3, 0.05)
        if not st.animating:
            self._button_row(("Select target slot", lambda: st.select("slot", c()["target"])),
                             ("Delete", lambda: doc.delete_constraint(name)))

    def _build_shape(self, slot: str, name: str, kind: str) -> None:
        st, doc = self.state, self.state.doc
        titles = {"path": "Path", "boundingbox": "Bounding box", "clipping": "Clipping", "point": "Point"}
        self.title.setText(f"{titles[kind]}  {name}")
        att = lambda: doc.attachments(slot)[name]  # noqa: E731
        self._text("Name", lambda: name, lambda v: (doc.rename_attachment(slot, name, v),
                                                    st.select("attachment", v, slot)))
        if kind == "point":
            self._number("X", lambda: att().get("x", 0), lambda v: doc.set_shape(slot, name, x=v))
            self._number("Y", lambda: att().get("y", 0), lambda v: doc.set_shape(slot, name, y=v))
            self._number("Rotation", lambda: att().get("rotation", 0), lambda v: doc.set_shape(slot, name, rotation=v),
                         suffix=" °")
            self.hint.setText("Setup mode: drag the point with Translate, aim it with Rotate.")
            self._move_to_skin(slot, name)
            return
        count = att().get("vertexCount", 0)
        self.form.addRow("Vertices", QLabel(f"{count // 3} points" if kind == "path" else f"{count} vertices"))
        if kind == "path":
            self._check("Closed", lambda: att().get("closed", False),
                        lambda v: doc.set_path_options(slot, name, closed=v), "Join the last point to the first")
            self._check("Constant speed", lambda: att().get("constantSpeed", True),
                        lambda v: doc.set_path_options(slot, name, constantSpeed=v),
                        "Bones move along the path at an even speed")
            users = [c["name"] for c in doc.paths if c["target"] == slot]
            self.form.addRow("Used by", QLabel(", ".join(users) if users else "no path constraint yet"))
            self.hint.setText("Setup mode: drag points and handles (squares); a point carries its handles. "
                              "Alt+click adds a point, Delete removes the selected points.")
        else:
            self.hint.setText("Setup mode: drag vertices; Alt+click on an edge adds one, Delete removes the "
                              "selected ones.")
        if kind == "clipping":
            self._combo("Clip until slot", lambda: ["(last slot)", *[s["name"] for s in doc.slots]],
                        lambda: att().get("end") or "(last slot)",
                        lambda v: doc.set_shape(slot, name, end=None if v == "(last slot)" else v))
        self._move_to_skin(slot, name)

    def _slot_color_now(self, name: str) -> str:
        st = self.state
        for item in st.evaluator().draw_items(st.time):
            if item.slot == name:
                return Color(*item.color).to_hex()
        return st.doc.slot(name).get("color", "ffffffff")

    def _slot_attachment_now(self, name: str) -> str:
        st = self.state
        slot = st.doc.slot(name)
        if not st.animating:
            return slot.get("attachment") or "(none)"
        keys = st.doc.animation(st.animation).get("slots", {}).get(name, {}).get("attachment", [])
        current = slot.get("attachment")
        for k in keys:
            if k.get("time", 0) <= st.time + 1e-6:
                current = k.get("name")
        return current or "(none)"

    def _build_slot(self, name: str) -> None:
        st, doc = self.state, self.state.doc
        self.title.setText(f"Slot  {name}")
        slot = lambda: doc.slot(name)  # noqa: E731
        options = lambda: ["(none)", *doc.attachments(name)]  # noqa: E731

        def set_attachment(v):
            value = None if v == "(none)" else v
            if st.animating:
                doc.set_slot_key(st.animation, name, "attachment", st.time, value)
            else:
                doc.set_slot(name, attachment=value)

        def set_color(v):
            if st.animating:
                doc.set_slot_key(st.animation, name, "color", st.time, v)
            else:
                doc.set_slot(name, color=v)

        self._text("Name", lambda: name, lambda v: (doc.rename_slot(name, v), st.select("slot", v)))
        if not st.animating:
            self._combo("Bone", lambda: [b["name"] for b in doc.bones], lambda: slot()["bone"],
                        lambda v: doc.set_slot(name, bone=v))
        self._combo("Attachment", options, lambda: self._slot_attachment_now(name), set_attachment)
        self._color("Colour", (lambda: self._slot_color_now(name)) if st.animating else
                    (lambda: slot().get("color", "ffffffff")), set_color)
        if not st.animating:
            self._combo("Blend", lambda: ["normal", "additive", "multiply", "screen"],
                        lambda: slot().get("blend", "normal"), lambda v: doc.set_slot(name, blend=v))
            self._button_row(("Draw earlier", lambda: doc.move_slot(name, -1)),
                             ("Draw later", lambda: doc.move_slot(name, 1)))

    def _build_attachment(self, slot: str, name: str) -> None:
        doc = self.state.doc
        self.title.setText(f"Attachment  {name}")
        att = lambda: doc.attachments(slot)[name]  # noqa: E731
        if att().get("type") == "mesh" and "parent" not in att():
            self._build_mesh(slot, name)
            return
        kind = att().get("type", "region")
        if kind in ("path", "boundingbox", "clipping", "point"):
            self._build_shape(slot, name, kind)
            return
        if kind != "region":
            self.form.addRow("Type", QLabel(f"{kind} (kept and exported)"))
            return
        self._text("Name", lambda: name, lambda v: (doc.rename_attachment(slot, name, v),
                                                    self.state.select("attachment", v, slot)))
        self._combo("Image", lambda: doc.image_paths() or [att().get("path", name)],
                    lambda: att().get("path", name), lambda v: doc.set_attachment(slot, name, path=v))
        for label, key, default, kw in (("X", "x", 0, {}), ("Y", "y", 0, {}), ("Rotation", "rotation", 0, {"suffix": " °"}),
                                        ("Scale X", "scaleX", 1, {"decimals": 3, "step": 0.05}),
                                        ("Scale Y", "scaleY", 1, {"decimals": 3, "step": 0.05}),
                                        ("Width", "width", 0, {"lo": 0}), ("Height", "height", 0, {"lo": 0})):
            self._number(label, lambda k=key, d=default: att().get(k, d),
                         lambda v, k=key: doc.set_attachment(slot, name, **{k: v}), **kw)
        if not self.state.animating:
            self._button_row(("Show in setup pose", lambda: doc.set_slot(slot, attachment=name)),
                             ("Convert to mesh…", lambda: self._convert_to_mesh(slot, name)),
                             ("Trace…", lambda: self._trace(slot, name)))
        self._move_to_skin(slot, name)

    def _move_to_skin(self, slot: str, name: str) -> None:
        doc = self.state.doc
        others = [s for s in doc.skin_names() if s != doc.edit_skin]
        if others and not self.state.animating:
            self._combo("Move to skin", lambda: ["…", *others], lambda: "…",
                        lambda v: v != "…" and (doc.move_attachment_to_skin(slot, name, v),
                                                self.state.select("slot", slot)))

    def _convert_to_mesh(self, slot: str, name: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        from . import mesh_ops
        cols, ok = QInputDialog.getInt(self, "Convert to mesh", "Columns", 3, 1, 30)
        if not ok:
            return
        rows, ok = QInputDialog.getInt(self, "Convert to mesh", "Rows", 1, 1, 30)
        if ok:
            mesh_ops.convert_to_mesh(self.state.doc, slot, name, cols, rows)
            self.rebuild()

    def _trace(self, slot: str, name: str) -> None:
        """Ask for the trace settings, then build the mesh from the image's alpha."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from . import mesh_trace
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Trace {name}")
        form = QFormLayout(dialog)
        detail = _spin(0.5, 100, 1, 0.5, " px")
        detail.setValue(4)
        detail.setToolTip("How far the outline may stray from the image edge: lower gives more vertices")
        padding = _spin(0, 50, 0, 1, " px")
        padding.setValue(2)
        threshold = _spin(0, 254, 0, 1)
        threshold.setValue(8)
        threshold.setToolTip("Pixels with alpha above this count as opaque")
        interior = _spin(0, 1000, 0, 5, " px")
        interior.setToolTip("Spacing of extra vertices inside the outline (0: none)")
        form.addRow("Detail", detail)
        form.addRow("Padding", padding)
        form.addRow("Alpha threshold", threshold)
        form.addRow("Interior spacing", interior)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._run(lambda: mesh_trace.trace_mesh(self.state.doc, slot, name, detail.value(), int(threshold.value()),
                                                    int(padding.value()), interior.value()))
            self.rebuild()

    def _build_mesh(self, slot: str, name: str) -> None:
        from ..preview.mesh import parse_vertices
        from . import mesh_ops
        st, doc = self.state, self.state.doc
        self.title.setText(f"Mesh  {name}")
        att = lambda: doc.attachments(slot)[name]  # noqa: E731
        mesh = parse_vertices(att())
        bones = [b["name"] for b in doc.bones]
        used = sorted({bones[i.bone] for v in mesh.influences for i in v}, key=bones.index) if mesh.weighted else []
        self._text("Name", lambda: name, lambda v: (doc.rename_attachment(slot, name, v),
                                                    st.select("attachment", v, slot)))
        self._combo("Image", lambda: doc.image_paths() or [att().get("path", name)],
                    lambda: att().get("path", name), lambda v: doc.set_attachment(slot, name, path=v))
        info = f"{mesh.count} vertices · {len(att()['triangles']) // 3} triangles"
        self.form.addRow("Mesh", QLabel(info))
        weights = QLabel(f"weighted to {', '.join(used)}" if used else f"follows slot bone '{doc.slot(slot)['bone']}'")
        weights.setWordWrap(True)
        self.form.addRow("Weights", weights)
        chosen = sorted(st.vertex_selection)
        self.form.addRow("Selected", QLabel(", ".join(map(str, chosen[:12])) + (" …" if len(chosen) > 12 else "")
                                            if chosen else "none (click vertices, Ctrl+click to add)"))
        if st.animating:
            self.hint.setText(f"Animate mode: dragging vertices sets deform keys in '{st.animation}'.")
            self._button_row(("Key deform", lambda: mesh_ops.set_deform_key(doc, st.animation, slot, name, st.time)),
                             ("Key setup shape", lambda: mesh_ops.set_deform_key(doc, st.animation, slot, name,
                                                                                 st.time, reset=True)))
            return
        self.hint.setText("Setup mode: Alt+click adds a vertex (inside or on the outline); Delete removes the "
                          "selected vertices.")
        modes = {"Reshape (image stretches)": "reshape", "Edit mesh (image stays)": "mesh",
                 "UVs only (image slides)": "uv"}
        self._combo("Vertex drag", lambda: list(modes),
                    lambda: next(k for k, v in modes.items() if v == st.mesh_drag_mode),
                    lambda v: setattr(st, "mesh_drag_mode", modes[v]))
        self._button_row(("Delete vertices", lambda: (mesh_ops.remove_vertices(doc, slot, name, set(chosen)),
                                                      st.set_vertex_selection(set())) if chosen else None),
                         ("Re-triangulate", lambda: mesh_ops.retriangulate(doc, slot, name)),
                         ("Trace…", lambda: self._trace(slot, name)))
        self._button_row(("Weights view (Shift+W)…", lambda: st.set_tool("weights")))
        self._move_to_skin(slot, name)
        buttons = [("Bind to bones…", lambda: self._bind(slot, name))]
        if mesh.weighted:
            buttons.append(("Unbind", lambda: mesh_ops.unbind(doc, slot, name)))
        self._button_row(*buttons)
        if len(chosen) == 1:
            vertex = chosen[0]
            current = lambda: mesh_ops.vertex_weights(doc, slot, name, vertex)  # noqa: E731
            self.form.addRow(QLabel(f"<b>Vertex {vertex} weights</b>"))
            for bone in current():
                self._number(f"  {bone}", lambda b=bone: current().get(b, 0),
                             lambda v, b=bone: mesh_ops.set_vertex_weights(
                                 doc, slot, name, vertex, mesh_ops.adjust_weight(current(), b, v)),
                             0, 1, 3, 0.05)
            self._combo("  Add bone", lambda: ["…", *[b for b in bones if b not in current()]], lambda: "…",
                        lambda v: v != "…" and mesh_ops.set_vertex_weights(
                            doc, slot, name, vertex, mesh_ops.adjust_weight(current(), v, 0.25)))

    def _bind(self, slot: str, name: str) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem
        from PySide6.QtCore import Qt
        from . import mesh_ops
        doc = self.state.doc
        slot_bone = doc.slot(slot)["bone"]
        suggested = {slot_bone, *doc.children(slot_bone), doc.bone(slot_bone).get("parent")}
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Bind {name} to bones")
        bone_list = QListWidget()
        for b in doc.bones:
            item = QListWidgetItem(b["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if b["name"] in suggested and b["name"] != doc.root_name
                               else Qt.CheckState.Unchecked)
            bone_list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Weights are computed from each vertex's distance to these bones "
                                "(up to 4 per vertex). Fine-tune per vertex afterwards."))
        layout.addWidget(bone_list)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [bone_list.item(i).text() for i in range(bone_list.count())
                  if bone_list.item(i).checkState() == Qt.CheckState.Checked]
        self._run(lambda: mesh_ops.bind(doc, slot, name, chosen))
        self.rebuild()

    def refresh(self) -> None:
        if not self.state._selection_exists(self.state.selection):
            return
        self._updating = True
        try:
            for update in self._updaters:
                try:
                    update()
                except (EditorError, KeyError):
                    pass
        finally:
            self._updating = False
