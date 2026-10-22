"""Tree: the skeleton hierarchy, like Spine's Tree view.

Top: search and filter buttons (bones, slots, attachments, constraints) and expand/collapse.
Columns: an eye to hide an item in the viewport (editor only, not exported), the hierarchy, and
a skin mark on skin-only bones and constraints. Slots list their attachments and, when other
skins exist, their skin placeholders with one entry per skin.
Bottom: New… (what can be created for the selection), Set Parent, Add to Skin and Split.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLineEdit, QMenu, QMessageBox, QPushButton, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .document import EditorError
from .state import EditorState

ROLE = Qt.ItemDataRole.UserRole
NAME, EYE, SKIN = 0, 1, 2
FILTERS = (("bones", "Bones", "🦴"), ("slots", "Slots", "▣"), ("attachments", "Attachments", "●"),
           ("constraints", "Constraints", "⛓"))
KIND_FILTER = {"bone": "bones", "slot": "slots", "attachment": "attachments", "placeholder": "attachments",
               "placeholder-skin": "attachments", "ik": "constraints", "transform": "constraints",
               "path": "constraints"}


class TreePanel(QWidget):
    insert_fx_requested = Signal()
    events_requested = Signal()

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        # search and filters
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.filter_buttons: dict[str, QToolButton] = {}
        top = QHBoxLayout()
        top.setSpacing(3)
        top.addWidget(self.search, 1)
        for key, label, mark in FILTERS:
            button = QToolButton()
            button.setText(mark)
            button.setCheckable(True)
            button.setChecked(True)
            button.setToolTip(f"Show {label.lower()}")
            button.toggled.connect(self._apply_filter)
            top.addWidget(button)
            self.filter_buttons[key] = button
        for text, tip, func in (("⊞", "Expand all", lambda: self.tree.expandAll()),
                                ("⊟", "Collapse all", self._collapse)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(func)
            top.addWidget(button)
        # the tree
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Hierarchy", "", ""])
        header = self.tree.header()
        header.moveSection(EYE, 0)  # the eye column sits left of the hierarchy, as in Spine
        header.setStretchLastSection(False)
        header.setSectionResizeMode(NAME, header.ResizeMode.Stretch)
        for col in (EYE, SKIN):
            header.setSectionResizeMode(col, header.ResizeMode.Fixed)
            self.tree.setColumnWidth(col, 26)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemClicked.connect(self._clicked)
        # bottom buttons
        self.new_button = QPushButton("+ New…")
        self.new_button.clicked.connect(self._new_menu)
        self.parent_button = QPushButton("Set Parent")
        self.parent_button.clicked.connect(lambda: self._run(self.set_parent))
        self.skin_button = QPushButton("Add to Skin")
        self.skin_button.clicked.connect(lambda: self._run(self.add_to_skin))
        self.split_button = QPushButton("Split")
        self.split_button.clicked.connect(lambda: self._run(self.split))
        bottom = QHBoxLayout()
        for b in (self.new_button, self.parent_button, self.skin_button, self.split_button):
            bottom.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bottom)
        self._building = False
        state.document_changed.connect(self.rebuild)
        state.mode_changed.connect(self.rebuild)
        state.time_changed.connect(self._time_changed)
        state.selection_changed.connect(self._sync_selection)
        state.selection_changed.connect(self._update_buttons)
        state.view_changed.connect(self._refresh_eyes)
        self.rebuild()

    # --- building -----------------------------------------------------------------------
    def _item(self, parent, text: str, kind: str, name: str = "", owner: str = "", color: str | None = None):
        item = QTreeWidgetItem(parent, [text, "", ""])
        item.setData(NAME, ROLE, (kind, name, owner))
        if color:
            item.setForeground(NAME, QColor(color))
        return item

    def _items(self):
        def walk(item):
            yield item
            for i in range(item.childCount()):
                yield from walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            yield from walk(self.tree.topLevelItem(i))

    def _expanded_keys(self) -> set:
        return {item.data(NAME, ROLE) for item in self._items() if item.isExpanded()}

    def rebuild(self) -> None:
        doc = self.state.doc
        expanded = self._expanded_keys()
        first_build = self.tree.topLevelItemCount() == 0
        self._building = True
        self.tree.clear()

        skeleton = self._item(self.tree, "Skeleton", "group", "skeleton")
        items: dict[str, QTreeWidgetItem] = {}

        def skins_listing(key: str, name: str) -> list[str]:
            return [s["name"] for s in doc.data["skins"] if name in s.get(key, [])]
        for bone in doc.bones:
            parent = items.get(bone.get("parent"), skeleton)
            item = self._item(parent, f"🦴 {bone['name']}", "bone", bone["name"])
            self._skin_mark(item, bone.get("skin", False), skins_listing("bones", bone["name"]))
            items[bone["name"]] = item
        others = [s for s in doc.skin_names() if s != "default"]
        for slot in doc.slots:
            slot_item = self._item(items[slot["bone"]], f"▣ {slot['name']}", "slot", slot["name"], color="#9fd3ff")
            for att_name, att in doc.attachments(slot["name"]).items():
                kind = att.get("type", "region")
                mark = "●" if slot.get("attachment") == att_name else "○"
                suffix = "" if kind == "region" else f"  ({kind})"
                self._item(slot_item, f"{mark} {att_name}{suffix}", "attachment", att_name, slot["name"], color="#c8c8c8")
            if doc.edit_skin != "default":  # the default skin's attachments show too, but are edited there
                for att_name in doc.data["skins"][0]["attachments"].get(slot["name"], {}):
                    if att_name not in doc.attachments(slot["name"]):
                        self._item(slot_item, f"  {att_name}  (default skin)", "group", f"default:{att_name}",
                                   color="#7d818a")
            if others:
                for placeholder, skins in doc.skin_placeholders(slot["name"]).items():
                    holder = self._item(slot_item, f"⌂ {placeholder}  (skin placeholder)", "placeholder", placeholder,
                                        slot["name"], color="#ffb36b")
                    for skin in skins:
                        self._item(holder, f"👕 {skin}", "placeholder-skin", f"{skin}\n{placeholder}", slot["name"],
                                   color="#b9a6ff")

        constraints = self._item(self.tree, "Constraints (update order)", "group", "constraints")
        for c in sorted(doc._ordered_constraints(), key=lambda c: c.get("order", 0)):
            kind = doc.constraint_kind(c["name"])
            chain = " → ".join(c["bones"]) if kind == "ik" else ", ".join(c["bones"])
            mark, color = {"ik": ("⤳", "#ff8af0"), "transform": ("⇉", "#ffb347"), "path": ("〰", "#ff7f00")}[kind]
            item = self._item(constraints, f"{mark} {c['name']}  ({chain} ⇢ {c['target']})", kind, c["name"],
                              color=color)
            self._skin_mark(item, c.get("skin", False), skins_listing(kind, c["name"]))

        st = self.state
        keyed = st.animating
        label = "Draw order, animated (back → front)" if keyed else "Draw order (back → front)"
        draw_order = self._item(self.tree, label, "group", "draworder")
        names = doc.draw_order_at(st.animation, st.time) if keyed else [s["name"] for s in doc.slots]
        self._shown_order = (keyed, names)
        for name in names:
            self._item(draw_order, name, "slot", name, color="#9fd3ff")

        skins = self._item(self.tree, "Skins", "group", "skins")
        for name in doc.skin_names():
            current = "  ✎ editing" if name == doc.edit_skin else ""
            self._item(skins, f"👕 {name}{current}", "skin", name, color="#b9a6ff")

        events = self._item(self.tree, "Events", "group", "events")
        for name in doc.event_names():
            self._item(events, f"⚑ {name}", "event", name, color="#ffd27f")
        animations = self._item(self.tree, "Animations", "group", "animations")
        for name in doc.animation_names():
            current = " ◀" if name == self.state.animation else ""
            self._item(animations, f"▶ {name}{current}", "animation", name, color="#ffb3b3")
        images = self._item(self.tree, "Images" + ("" if doc.images_dir else "  (set the images folder)"),
                            "group", "images")
        for path in doc.image_paths():
            self._item(images, f"🖼 {path}", "image", path, color="#b9e6a8")

        for item in self._items():
            key = item.data(NAME, ROLE)
            # First build: open the skeleton, bones and animations. Later: keep what the user had open.
            item.setExpanded(key in expanded or (first_build and (key[0] == "bone" or
                                                                  key[1] in ("skeleton", "animations", "constraints"))))
        self._building = False
        self._refresh_eyes()
        self._apply_filter()
        self._sync_selection()
        self._update_buttons()

    def _skin_mark(self, item: QTreeWidgetItem, required: bool, skins: list[str]) -> None:
        if required or skins:
            item.setText(SKIN, "👕")
            item.setToolTip(SKIN, "Skin-only: active in " + (", ".join(skins) if skins else "no skin yet"))

    def _refresh_eyes(self) -> None:
        """The eye column: ● visible, ○ hidden in the viewport (a hidden bone hides its slots)."""
        st = self.state
        for item in self._items():
            kind, name, _ = item.data(NAME, ROLE)
            parent = item.parent()
            if kind in ("bone", "slot") and parent is not None and parent.data(NAME, ROLE)[1] != "draworder":
                hidden = (kind, name) in st.hidden
                item.setText(EYE, "○" if hidden else "●")
                item.setForeground(EYE, QColor("#6a6a6a" if hidden else "#cfcfcf"))
                item.setToolTip(EYE, "Show in the viewport" if hidden else "Hide in the viewport (editor only)")

    def _apply_filter(self) -> None:
        """Search by name and the kind filters; parents of matches stay visible."""
        text = self.search.text().strip().lower()
        shown = {k for k, b in self.filter_buttons.items() if b.isChecked()}

        def visit(item) -> bool:
            kind, _name, _owner = item.data(NAME, ROLE)
            children = [visit(item.child(i)) for i in range(item.childCount())]
            category = KIND_FILTER.get(kind)
            if kind == "group":
                own = not text
            else:
                own = (category is None or category in shown) and (not text or text in item.text(NAME).lower())
            visible = own or any(children)
            item.setHidden(not visible)
            if text and any(children):
                item.setExpanded(True)
            return visible
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            visit(top)
            top.setHidden(False)

    def _collapse(self) -> None:
        self.tree.collapseAll()
        if self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)

    def _sync_selection(self) -> None:
        sel = self.state.selection
        if sel.empty:
            return
        target = (sel.kind, sel.name, sel.owner)
        for item in self._items():
            if item.data(NAME, ROLE) == target and not item.isHidden():
                self._building = True
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                self._building = False
                return

    def _update_buttons(self) -> None:
        sel, doc = self.state.selection, self.state.doc
        self.parent_button.setEnabled(sel.kind == "slot" or (sel.kind == "bone" and sel.name != doc.root_name))
        self.skin_button.setEnabled(sel.kind in ("bone", "ik", "transform", "path") and sel.name != doc.root_name)
        self.split_button.setEnabled(sel.kind == "bone" and doc.has_bone(sel.name)
                                     and doc.bone(sel.name).get("length", 0) > 0)

    def _clicked(self, item, column) -> None:
        if column == EYE:
            kind, name, _ = item.data(NAME, ROLE)
            if kind in ("bone", "slot"):
                self.state.set_hidden(kind, name, (kind, name) not in self.state.hidden)

    def _current_changed(self, item, _prev) -> None:
        if self._building or item is None:
            return
        kind, name, owner = item.data(NAME, ROLE)
        if kind == "placeholder-skin":  # edit that skin and select its attachment
            skin, placeholder = name.split("\n")
            self.state.doc.set_edit_skin(skin)
            self.state.select("attachment", placeholder, owner)
        elif kind == "placeholder":
            self.state.select("slot", owner)
        elif kind != "group":
            self.state.select(kind, name, owner)

    def _double_clicked(self, item, _col) -> None:
        kind, name, _owner = item.data(NAME, ROLE)
        if kind == "image":
            self.attach_image(name)
        elif kind == "skin":
            self.state.doc.set_edit_skin(name)
        elif kind == "animation":
            self.state.set_animation(name)
            self.state.set_mode("animate")

    # --- actions ------------------------------------------------------------------------
    def _run(self, func, *args) -> None:
        try:
            func(*args)
        except EditorError as exc:
            QMessageBox.warning(self, "Anim2D", str(exc))

    def attach_image(self, image: str) -> None:
        sel = self.state.selection
        bone = sel.name if sel.kind == "bone" else self.state.doc.root_name
        if sel.kind == "slot":
            self._run(lambda: self.state.select("attachment", self.state.doc.add_attachment(sel.name, image), sel.name))
            return
        self._run(lambda: self.state.select("slot", self.state.doc.add_slot(bone, image=image)))

    def add_ik(self, bone: str) -> None:
        from .ik_dialog import IkDialog
        dialog = IkDialog(self.state.doc, bone, self)
        if dialog.exec() == IkDialog.DialogCode.Accepted and dialog.created:
            self.state.select("ik", dialog.created[0])

    def add_constraint(self, kind: str, bone: str) -> None:
        from .constraint_dialog import ConstraintDialog
        dialog = ConstraintDialog(self.state.doc, kind, bone, self)
        if dialog.exec() == ConstraintDialog.DialogCode.Accepted and dialog.created:
            self.state.select(kind, dialog.created)

    def add_shape(self, slot: str, kind: str) -> None:
        """A starting shape around the slot's bone."""
        doc, size = self.state.doc, 60.0
        if kind == "path":
            name = doc.add_path_attachment(slot, [(0, 0), (size, size / 2), (size * 2, 0)])
        elif kind == "boundingbox":
            name = doc.add_bounding_box(slot, [(-size / 2, -size / 2), (size / 2, -size / 2), (size / 2, size / 2),
                                               (-size / 2, size / 2)])
        elif kind == "clipping":
            name = doc.add_clipping(slot, [(-size, -size), (size, -size), (size, size), (-size, size)])
        else:
            name = doc.add_point(slot)
        self.state.select("attachment", name, slot)

    def add_placeholder(self, slot: str) -> None:
        doc = self.state.doc
        name = self._ask("Skin placeholder", "Placeholder name (each skin can fill it with its own image)")
        if not name:
            return
        image = None
        images = doc.image_paths()
        if images:
            choice, ok = QInputDialog.getItem(self, "Skin placeholder", f"Image for skin '{doc.edit_skin}'",
                                              ["(none yet)", *images], 0, False)
            if not ok:
                return
            image = None if choice == "(none yet)" else choice
        doc.add_skin_placeholder(slot, name, image)
        self.state.select("attachment", name, slot)

    def set_parent(self) -> None:
        sel, doc = self.state.selection, self.state.doc
        if sel.kind == "bone":
            options = [b["name"] for b in doc.bones if b["name"] != sel.name and b["name"] not in doc.descendants(sel.name)]
            current = doc.bone(sel.name).get("parent", "")
            what = f"New parent of bone '{sel.name}'"
        elif sel.kind == "slot":
            options = [b["name"] for b in doc.bones]
            current = doc.slot(sel.name)["bone"]
            what = f"Bone of slot '{sel.name}'"
        else:
            raise EditorError("Select a bone or slot to set its parent")
        choice, ok = QInputDialog.getItem(self, "Set Parent", what, options,
                                          options.index(current) if current in options else 0, False)
        if ok and choice != current:
            if sel.kind == "bone":
                doc.reparent_bone(sel.name, choice)
            else:
                doc.set_slot(sel.name, bone=choice)

    def add_to_skin(self) -> None:
        sel, doc = self.state.selection, self.state.doc
        kind = {"bone": "bone", "ik": "ik", "transform": "transform", "path": "path"}.get(sel.kind or "")
        if kind is None:
            raise EditorError("Select a bone or constraint to add it to a skin")
        skin = doc.edit_skin
        if skin == "default":
            others = [s for s in doc.skin_names() if s != "default"]
            if not others:
                raise EditorError("Create a skin first (New… → Skin)")
            skin, ok = QInputDialog.getItem(self, "Add to Skin", f"Skin for '{sel.name}'", others, 0, False)
            if not ok:
                return
        doc.add_to_skin(kind, sel.name, skin)
        self.state.message.emit(f"'{sel.name}' is now part of skin '{skin}'")

    def split(self) -> None:
        sel = self.state.selection
        if sel.kind != "bone":
            raise EditorError("Select a bone to split")
        self.state.select("bone", self.state.doc.split_bone(sel.name))

    def _ask(self, title: str, label: str, text: str = "") -> str | None:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value.strip() if ok and value.strip() else None

    def new_actions(self) -> list[tuple[str, object]]:
        """What New… offers for the selection (like Spine's New menu)."""
        st, doc = self.state, self.state.doc
        sel = st.selection
        slot = sel.name if sel.kind == "slot" else sel.owner if sel.kind == "attachment" else None
        bone = sel.name if sel.kind == "bone" else doc.slot(slot)["bone"] if slot else doc.root_name
        actions: list[tuple[str, object]] = [
            ("Bone", lambda: st.select("bone", doc.add_bone(bone, length=50))),
            ("Slot", lambda: st.select("slot", doc.add_slot(bone))),
        ]
        if slot:
            actions += [("-", None),
                        ("Skin Placeholder", lambda: self.add_placeholder(slot)),
                        ("Bounding Box", lambda: self.add_shape(slot, "boundingbox")),
                        ("Clipping", lambda: self.add_shape(slot, "clipping")),
                        ("Path", lambda: self.add_shape(slot, "path")),
                        ("Point", lambda: self.add_shape(slot, "point"))]
        if bone != doc.root_name:
            actions += [("-", None),
                        ("IK Constraint", lambda: self.add_ik(bone)),
                        ("Path Constraint", lambda: self.add_constraint("path", bone)),
                        ("Transform Constraint", lambda: self.add_constraint("transform", bone))]
        actions += [("-", None),
                    ("Skin", lambda: (n := self._ask("New skin", "Name")) and doc.add_skin(n) and doc.set_edit_skin(n)),
                    ("Animation", lambda: (n := self._ask("New animation", "Name"))
                     and st.set_animation(doc.add_animation(n))),
                    ("Event", lambda: (n := self._ask("New event", "Name")) and doc.add_event(n))]
        return actions

    def _new_menu(self) -> None:
        menu = QMenu(self)
        for label, func in self.new_actions():
            if label == "-":
                menu.addSeparator()
                continue
            action = QAction(label, menu)
            action.triggered.connect(lambda _=False, f=func: self._run(f))
            menu.addAction(action)
        menu.exec(self.new_button.mapToGlobal(self.new_button.rect().topLeft()))

    def add_sequence(self, slot: str) -> None:
        """Numbered images played as a flip-book (attachment keys) in an animation."""
        from .sequence_dialog import SequenceDialog
        from .sequences import add_image_sequence
        st = self.state
        dialog = SequenceDialog(st.doc, slot, st.animation, st.time if st.animating else 0.0, self)
        if dialog.exec() == SequenceDialog.DialogCode.Accepted:
            c = dialog.choice()
            result = add_image_sequence(st.doc, slot, **c)
            st.set_animation(c["animation"])
            st.set_mode("animate")
            st.message.emit(f"Sequence in {slot}: {len(result['attachments'])} images, {result['keys']} keys, "
                            f"until {result['end']:.2f}s")

    def _time_changed(self) -> None:
        """Rebuild only when the animated draw order at the new time differs from the one shown."""
        st = self.state
        if not st.animating or not st.doc.animation(st.animation).get("drawOrder"):
            if getattr(self, "_shown_order", (False, None))[0]:
                self.rebuild()  # the label shows the time
            return
        if getattr(self, "_shown_order", None) != (True, st.doc.draw_order_at(st.animation, st.time)):
            self.rebuild()

    def edit_physics(self, bone: str) -> None:
        from .physics_dialog import PhysicsDialog
        PhysicsDialog(self.state.doc, bone, self).exec()

    def add_chain(self, bone: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        from .autorig import chain_from_bone
        count, ok = QInputDialog.getInt(self, "Bone chain", "Bones", 4, 1, 64)
        if not ok:
            return
        length, ok = QInputDialog.getDouble(self, "Bone chain", "Total length", 200, 1, 100000, 1)
        if not ok:
            return
        curl, ok = QInputDialog.getDouble(self, "Bone chain", "Curl per bone (degrees)", 0, -180, 180, 1)
        if ok:
            names = chain_from_bone(self.state.doc, bone, count, length, curl)
            self.state.select("bone", names[-1])

    def add_chain_on_path(self, slot: str, path: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        from .autorig import bone_chain, path_points
        doc = self.state.doc
        count, ok = QInputDialog.getInt(self, "Bone chain along path", "Bones", 6, 1, 128)
        if ok:
            names = bone_chain(doc, doc.slot(slot)["bone"], path_points(doc, slot, path), count, f"{path}_")
            self.state.select("bone", names[0])

    def rig_mesh(self, slot: str, name: str) -> None:
        from .autorig import rig_mesh
        bones = rig_mesh(self.state.doc, slot, name)
        QMessageBox.information(self, "Anim2D", f"Bound {name} to {len(bones)} bone(s): {', '.join(bones)}")

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind, name, owner = item.data(NAME, ROLE)
        doc = self.state.doc
        menu = QMenu(self)

        def add(text, func):
            action = QAction(text, menu)
            action.triggered.connect(lambda: self._run(func))
            menu.addAction(action)

        if kind == "bone":
            add("New child bone", lambda: self.state.select("bone", doc.add_bone(name, length=50)))
            add("New slot", lambda: self.state.select("slot", doc.add_slot(name)))
            if name != doc.root_name:
                add("Add IK constraint…", lambda: self.add_ik(name))
                add("Add transform constraint…", lambda: self.add_constraint("transform", name))
                add("Add path constraint…", lambda: self.add_constraint("path", name))
                if doc.bone(name).get("length", 0) > 0:
                    add("Split", lambda: self.state.select("bone", doc.split_bone(name)))
            add("Add bone chain…", lambda: self.add_chain(name))
            if name != doc.root_name:
                add("Physics…", lambda: self.edit_physics(name))
            add("Insert FX here…", lambda: (self.state.select("bone", name), self.insert_fx_requested.emit()))
            add("Rename…", lambda: (n := self._ask("Rename bone", "Name", name)) and doc.rename_bone(name, n))
            if name != doc.root_name:
                add("Delete (with children and slots)", lambda: doc.delete_bone(name))
        elif kind == "slot":
            images = doc.image_paths()
            if images:
                sub = menu.addMenu("Add image attachment")
                for path in images[:200]:
                    action = QAction(path, sub)
                    action.triggered.connect(lambda _=False, p=path: self._run(lambda: doc.add_attachment(name, p)))
                    sub.addAction(action)
            shapes = menu.addMenu("Add shape")
            for label, func in (("Path", lambda: self.add_shape(name, "path")),
                                ("Bounding box", lambda: self.add_shape(name, "boundingbox")),
                                ("Clipping polygon", lambda: self.add_shape(name, "clipping")),
                                ("Point", lambda: self.add_shape(name, "point"))):
                action = QAction(label, shapes)
                action.triggered.connect(lambda _=False, f=func: self._run(f))
                shapes.addAction(action)
            add("Add skin placeholder…", lambda: self.add_placeholder(name))
            add("Add image sequence…", lambda: self.add_sequence(name))
            st = self.state
            if st.animating:  # Animate mode: a draw-order key at the current time
                add("Draw earlier (back) — key", lambda: doc.move_slot_keyed(st.animation, st.time, name, -1))
                add("Draw later (front) — key", lambda: doc.move_slot_keyed(st.animation, st.time, name, 1))
                add("Draw at the back — key", lambda: doc.move_slot_keyed(st.animation, st.time, name, -999))
                add("Draw in front — key", lambda: doc.move_slot_keyed(st.animation, st.time, name, 999))
            else:
                add("Draw earlier (back)", lambda: doc.move_slot(name, -1))
                add("Draw later (front)", lambda: doc.move_slot(name, 1))
            add("Rename…", lambda: (n := self._ask("Rename slot", "Name", name)) and doc.rename_slot(name, n))
            add("Delete", lambda: doc.delete_slot(name))
        elif kind == "attachment":
            add("Show in setup pose", lambda: doc.set_slot(owner, attachment=name))
            att_type = doc.attachments(owner).get(name, {}).get("type")
            if att_type == "path":
                add("Bone chain along this path…", lambda: self.add_chain_on_path(owner, name))
            elif att_type == "mesh":
                add("Rig to the bones over it", lambda: self.rig_mesh(owner, name))
            add("Rename…", lambda: (n := self._ask("Rename attachment", "Name", name))
                and doc.rename_attachment(owner, name, n))
            add("Delete", lambda: doc.delete_attachment(owner, name))
        elif kind == "ik":
            if self.state.animating:
                add(f"Key IK at {self.state.time:.3f}s", lambda: doc.set_ik_key(self.state.animation, name, self.state.time))
            add("Select target bone", lambda: self.state.select("bone", doc.ik(name)["target"]))
            add("Rename…", lambda: (n := self._ask("Rename IK constraint", "Name", name)) and doc.rename_ik(name, n))
            add("Delete", lambda: doc.delete_ik(name))
        elif kind in ("transform", "path"):
            if self.state.animating:
                if kind == "transform":
                    add(f"Key mixes at {self.state.time:.3f}s",
                        lambda: doc.set_transform_key(self.state.animation, name, self.state.time))
                else:
                    for tl in ("position", "spacing", "mix"):
                        add(f"Key {tl} at {self.state.time:.3f}s", lambda t=tl: doc.set_path_key(
                            self.state.animation, name, t, self.state.time))
            if kind == "transform":
                add("Select target bone", lambda: self.state.select("bone", doc.transform_constraint(name)["target"]))
            else:
                add("Select target slot", lambda: self.state.select("slot", doc.path_constraint(name)["target"]))
            add("Rename…", lambda: (n := self._ask("Rename constraint", "Name", name)) and doc.rename_constraint(name, n))
            add("Delete", lambda: doc.delete_constraint(name))
        elif kind == "group" and name == "constraints":
            sel = self.state.selection
            if sel.kind == "bone" and sel.name != doc.root_name:
                add(f"Add IK constraint for '{sel.name}'…", lambda: self.add_ik(sel.name))
                add(f"Add transform constraint for '{sel.name}'…", lambda: self.add_constraint("transform", sel.name))
                add(f"Add path constraint for '{sel.name}'…", lambda: self.add_constraint("path", sel.name))
        elif kind == "skin" or (kind == "group" and name == "skins"):
            add("New skin…", lambda: (n := self._ask("New skin", "Name")) and doc.add_skin(n)
                and doc.set_edit_skin(n))
            if kind == "skin":
                add("Edit and show this skin", lambda: doc.set_edit_skin(name))
                add("Duplicate…", lambda: (n := self._ask("Duplicate skin", "Name", f"{name}-copy"))
                    and doc.duplicate_skin(name, n))
                if name != "default":
                    add("Rename…", lambda: (n := self._ask("Rename skin", "Name", name)) and doc.rename_skin(name, n))
                    add("Delete", lambda: doc.delete_skin(name))
        elif kind == "image":
            add("Attach to selected bone (new slot)", lambda: self.attach_image(name))
        elif kind == "animation" or (kind == "group" and name == "animations"):
            add("New animation", lambda: (n := self._ask("New animation", "Name"))
                and self.state.set_animation(doc.add_animation(n)))
            if kind == "animation":
                add("Edit (Animate mode)", lambda: (self.state.set_animation(name), self.state.set_mode("animate")))
                add("Rename…", lambda: (n := self._ask("Rename animation", "Name", name)) and doc.rename_animation(name, n))
                add("Duplicate", lambda: doc.duplicate_animation(name))
                add("Delete", lambda: doc.delete_animation(name))
        elif kind == "event" or (kind == "group" and name == "events"):
            add("New event", lambda: (n := self._ask("New event", "Name")) and doc.add_event(n))
            if kind == "event" and self.state.animating:
                add(f"Key at {self.state.time:.3f}s", lambda: doc.key_event(self.state.animation, name, self.state.time))
            if kind == "event":
                add("Rename…", lambda: (n := self._ask("Rename event", "Name", name)) and doc.rename_event(name, n))
                add("Delete (with its keys)", lambda: doc.delete_event(name))
            add("Open the Events window", self.events_requested.emit)
        if kind in ("ik", "transform", "path"):
            add("Update earlier", lambda: doc.move_constraint(name, -1))
            add("Update later", lambda: doc.move_constraint(name, 1))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))
