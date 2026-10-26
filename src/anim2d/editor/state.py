"""Shared editor state: document, mode, current animation/time, selection and tool."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ..preview.evaluator import PoseEvaluator
from .document import Document, EditorError

TOOLS = ("select", "translate", "rotate", "scale", "shear", "pose", "weights", "create")


@dataclass(frozen=True)
class Selection:
    kind: str | None = None  # "bone", "slot", "attachment", "animation", "event", "image", "ik", "transform",
    # "path" (constraints), "skin"
    name: str = ""
    owner: str = ""  # slot for attachments

    @property
    def empty(self) -> bool:
        return self.kind is None


class EditorState(QObject):
    document_changed = Signal()
    selection_changed = Signal()
    time_changed = Signal()
    mode_changed = Signal()
    tool_changed = Signal()
    vertex_selection_changed = Signal()
    view_changed = Signal()  # axes, compensation, view options, hidden items
    message = Signal(str)

    def __init__(self, doc: Document | None = None):
        super().__init__()
        self.doc = doc or Document()
        self.mode = "setup"
        self.animation: str | None = next(iter(self.doc.animation_names()), None)
        self.time = 0.0
        self.auto_key = True  # Animate mode: changes become keys at once
        # step in frames; front: ghosts over the current frame, so they show where they overlap it
        self.grid = {"style": "Subtle", "custom": None}  # viewport grid (preview.grid)
        self.motion_paths = False  # draw the selected bone's path over the animation (Animate mode)
        self.background = {"name": "Default", "custom": None}  # viewport background (preview.grid.BACKGROUNDS)
        self.onion = {"on": False, "before": 2, "after": 2, "step": 3, "opacity": 0.5, "front": True,
                      "selected": True}  # selected: ghost only the selection (all when nothing is selected)
        self.pose_overrides: dict[str, dict[int, float]] = {}  # unkeyed changes when Auto Key is off
        self.pose_clipboard: dict[str, tuple[float, ...]] = {}  # Edit > Copy pose
        self.selection = Selection()
        self.tool = "select"
        self.vertex_selection: set[int] = set()  # selected vertices of the active mesh
        self.mesh_drag_mode = "reshape"  # setup-mode vertex drags: "reshape", "mesh" or "uv"
        self.key_clipboard: dict | None = None  # copied keys (Document.copy_keys)
        # Weights tool (paints the active mesh in Setup mode)
        self.weight_bone: str | None = None  # None: the slot's bone
        self.paint_mode = "add"
        self.brush_size = 40.0  # screen pixels (radius)
        self.brush_strength = 0.5
        self.brush_feather = 0.6  # 0 hard edge .. 1 soft from the centre
        self.weight_pies = True  # pie charts of each vertex's bone weights
        self.weight_overlay = True  # heat map of the painted bone's weights
        self.weights_selected_only = False  # paint / smooth / auto only the selected vertices
        self.locked_bones: set[str] = set()  # their weights do not change
        self._weights_mesh: tuple[str, str] | None = None  # the mesh the Weights tool works on
        # Viewport options (editor-only, not saved in the skeleton)
        self.axes = "parent"  # translate gizmo and values: "local", "parent" or "world"
        self.compensate = {"bones": False, "images": False}  # Setup mode: keep children / images in place
        self.view_options = {g: {"select": True, "visible": True, "names": False}
                             for g in ("bones", "images", "others")}
        self.hidden: set[tuple[str, str]] = set()  # ("bone" | "slot", name) hidden in the viewport
        self.unselectable: set[str] = set()  # bones that cannot be picked in the viewport
        self._attach(self.doc)

    def _attach(self, doc: Document) -> None:
        doc.listeners.append(self._doc_changed)

    def set_document(self, doc: Document) -> None:
        if self._doc_changed in self.doc.listeners:  # other states (the mix preview) keep theirs
            self.doc.listeners.remove(self._doc_changed)
        self.doc = doc
        self._attach(doc)
        self.animation = next(iter(doc.animation_names()), None)
        self.time = 0.0
        self.pose_overrides = {}
        self.selection = Selection()
        self.mode = "setup"
        if doc.edit_skin == "default":
            def _has_attachments(skin_name: str) -> bool:
                return any(doc.skin_data(skin_name).get("attachments", {}).values())
            if not _has_attachments("default"):
                for skin in doc.skin_names():
                    if skin != "default" and _has_attachments(skin):
                        doc.edit_skin = skin
                        break
        self.document_changed.emit()
        self.selection_changed.emit()
        self.mode_changed.emit()
        self.time_changed.emit()

    def _doc_changed(self) -> None:
        # Drop references to things that no longer exist (after delete, rename or undo).
        if self.animation is not None and self.animation not in self.doc.data["animations"]:
            self.animation = next(iter(self.doc.animation_names()), None)
            if self.animation is None and self.mode == "animate":
                self.mode = "setup"
                self.mode_changed.emit()
        if not self._selection_exists(self.selection):
            self.selection = Selection()
            self.vertex_selection = set()
            self.selection_changed.emit()
        if self.vertex_selection and max(self.vertex_selection) >= self._active_vertex_count():
            self.vertex_selection = set()
            self.vertex_selection_changed.emit()
        self.document_changed.emit()

    def _selection_exists(self, sel: Selection) -> bool:
        d = self.doc
        if sel.kind == "bone":
            return d.has_bone(sel.name)
        if sel.kind == "slot":
            return d.has_slot(sel.name)
        if sel.kind == "attachment":
            return d.has_slot(sel.owner) and sel.name in d.attachments(sel.owner)
        if sel.kind == "animation":
            return sel.name in d.data["animations"]
        if sel.kind == "event":
            return sel.name in d.data["events"]
        if sel.kind in ("ik", "transform", "path"):
            return any(c["name"] == sel.name for c in d.data.get(sel.kind, []))
        if sel.kind == "skin":
            return any(s.get("name") == sel.name for s in d.data.get("skins", []))
        return True

    def _active_vertex_count(self) -> int:
        """Vertices of the mesh or shape being edited (0 when none is)."""
        mesh = self.active_mesh()
        if mesh is not None:
            return len(self.doc.attachments(mesh[0])[mesh[1]].get("uvs", [])) // 2
        shape = self.active_shape()
        if shape is not None and shape[2] != "point":
            return int(self.doc.attachments(shape[0])[shape[1]].get("vertexCount", 0))
        return 0

    # --- setters ---------------------------------------------------------------------------

    def select(self, kind: str | None, name: str = "", owner: str = "") -> None:
        sel = Selection(kind, name, owner)
        if sel != self.selection:
            self.selection = sel
            self.vertex_selection = set()
            if kind == "animation":
                self.set_animation(name)
            self.selection_changed.emit()

    def set_vertex_selection(self, vertices: set[int]) -> None:
        if vertices != self.vertex_selection:
            self.vertex_selection = set(vertices)
            self.vertex_selection_changed.emit()

    def active_mesh(self) -> tuple[str, str] | None:
        """(slot, attachment) of the mesh being edited: the selected mesh attachment, or the mesh
        a selected slot currently shows."""
        sel = self.selection
        if sel.kind == "attachment":
            slot, name = sel.owner, sel.name
        elif sel.kind == "slot":
            slot = sel.name
            shown = [i for i in self.evaluator().draw_items(self.time) if i.slot == slot]
            name = shown[0].attachment if shown else self.doc.slot(slot).get("attachment")
        else:
            return None
        att = self.doc.attachments(slot).get(name) if name else None
        if att and att.get("type") == "mesh" and "parent" not in att:
            return slot, name
        return None

    def active_shape(self) -> tuple[str, str, str] | None:
        """(slot, attachment, type) of the bounding box, clipping polygon, path or point being edited:
        the selected one, or the one a selected slot shows."""
        sel = self.selection
        if sel.kind == "attachment":
            slot, name = sel.owner, sel.name
        elif sel.kind == "slot":
            slot = sel.name
            name = self.evaluator().slot_attachment(self.doc.slot(slot), self.time)
        else:
            return None
        att = self.doc.attachments(slot).get(name) if name else None
        if att and att.get("type") in ("boundingbox", "clipping", "path", "point"):
            return slot, name, att["type"]
        return None

    def weights_mesh(self) -> tuple[str, str] | None:
        """The mesh the Weights view works on: the selected mesh, or (with the Weights tool) the last
        one, so picking a bone to paint keeps the mesh."""
        mesh = self.active_mesh()
        if mesh is not None:
            self._weights_mesh = mesh
            return mesh
        last = self._weights_mesh
        if self.tool == "weights" and last and self.doc.has_slot(last[0]) and \
                self.doc.attachments(last[0]).get(last[1], {}).get("type") == "mesh":
            return last
        return None

    def paint_bone(self) -> str | None:
        """The bone the Weights tool paints: the chosen one, else the active mesh's slot bone."""
        mesh = self.weights_mesh()
        if self.weight_bone and self.doc.has_bone(self.weight_bone):
            return self.weight_bone
        return self.doc.slot(mesh[0])["bone"] if mesh else None

    def set_axes(self, axes: str) -> None:
        if axes in ("local", "parent", "world") and axes != self.axes:
            self.axes = axes
            self.view_changed.emit()

    def set_compensate(self, what: str, on: bool) -> None:
        if self.compensate.get(what) != on:
            self.compensate[what] = on
            self.view_changed.emit()

    def set_view_option(self, group: str, what: str, on: bool) -> None:
        if self.view_options[group][what] != on:
            self.view_options[group][what] = on
            self.view_changed.emit()

    def set_hidden(self, kind: str, name: str, hidden: bool) -> None:
        """Hide a bone (and its slots) or a slot in the viewport, like the tree's eye column."""
        key = (kind, name)
        if (key in self.hidden) != hidden:
            (self.hidden.add if hidden else self.hidden.discard)(key)
            self.view_changed.emit()

    def set_selectable(self, bone: str, selectable: bool) -> None:
        if (bone not in self.unselectable) != selectable:
            (self.unselectable.discard if selectable else self.unselectable.add)(bone)
            self.view_changed.emit()

    def slot_hidden(self, slot: str) -> bool:
        """Hidden itself, or one of its bone's ancestors is."""
        if ("slot", slot) in self.hidden:
            return True
        bone = self.doc.slot(slot)["bone"] if self.doc.has_slot(slot) else None
        return bone is not None and self.bone_hidden(bone)

    def bone_hidden(self, bone: str) -> bool:
        while bone:
            if ("bone", bone) in self.hidden:
                return True
            bone = self.doc.bone(bone).get("parent") if self.doc.has_bone(bone) else None
        return False

    def set_tool(self, tool: str) -> None:
        if tool in TOOLS and tool != self.tool:
            self.tool = tool
            self.tool_changed.emit()

    def set_mode(self, mode: str) -> None:
        if mode == "animate" and self.animation is None:
            self.animation = self.doc.add_animation("animation")
        if mode != self.mode:
            self.pose_overrides = {}
            self.mode = mode
            self.mode_changed.emit()
            self.time_changed.emit()

    POSE_INDEX = {"x": 0, "y": 1, "rotation": 2, "scaleX": 3, "scaleY": 4, "shearX": 5, "shearY": 6}

    def set_background(self, name: str, custom: str | None = None) -> None:
        """Viewport background: a name from preview.grid.BACKGROUND_CHOICES; custom is the colour for "Custom"."""
        self.background = {"name": name, "custom": custom if custom is not None else self.background.get("custom")}
        self.view_changed.emit()

    def set_grid(self, style: str, custom: str | None = None) -> None:
        """Viewport grid: a style from preview.grid.CHOICES; custom is the colour for "Custom"."""
        self.grid = {"style": style, "custom": custom if custom is not None else self.grid.get("custom")}
        self.view_changed.emit()

    def set_onion(self, **settings) -> None:
        self.onion.update(settings)
        self.view_changed.emit()

    def set_auto_key(self, on: bool) -> None:
        if on != self.auto_key:
            self.auto_key = on
            self.clear_pose()
            self.view_changed.emit()

    def clear_pose(self) -> None:
        """Drop unkeyed pose changes (made with Auto Key off)."""
        if self.pose_overrides:
            self.pose_overrides = {}
            self.document_changed.emit()

    def set_animation(self, name: str | None) -> None:
        self.clear_pose()
        skin = self.doc.animation_skin(name)
        if skin and skin != self.doc.edit_skin:  # 'L1_intro' plays with skin L1
            self.doc.set_edit_skin(skin)
        if name != self.animation:
            self.animation = name
            self.time = 0.0
            self.time_changed.emit()
            self.document_changed.emit()

    def set_time(self, time: float) -> None:
        time = max(0.0, time)
        if abs(time - self.time) > 1e-9:
            if self.pose_overrides:
                self.pose_overrides = {}  # unkeyed changes stay at their time only, like Spine
            self.time = time
            self.time_changed.emit()

    @property
    def animating(self) -> bool:
        return self.mode == "animate" and self.animation is not None

    def evaluator(self) -> PoseEvaluator:
        ev = self.doc.evaluator(self.animation if self.animating else "")
        if self.animating and self.pose_overrides:
            ev.overrides = self.pose_overrides
        return ev

    # --- posing ----------------------------------------------------------------------------

    def apply_bone_pose(self, bone: str, record: bool = True, **values: float) -> None:
        """Set posed local values. Setup mode edits the setup pose; animate mode writes keys
        relative to the setup pose (translate/rotate add, scale multiplies), like Spine."""
        if not self.animating:
            if not any(self.compensate.values()):
                self.doc.set_bone(bone, record=record, **values)
                return
            # Compensate: the children and/or images keep their world transform.
            from . import affine
            before = self.doc.evaluator("").bone_world(0)
            children = list(self.doc.children(bone)) if self.compensate["bones"] else []
            images = [(s, n, a) for s in self.doc.slots_of_bone(bone) for n, a in self.doc.attachments(s).items()
                      if a.get("type", "region") == "region"] if self.compensate["images"] else []
            kept = {c: before[c] for c in children}
            placed = {(s, n): affine.mul(before[bone], affine.local(a.get("x", 0), a.get("y", 0), a.get("rotation", 0),
                                                                     a.get("scaleX", 1), a.get("scaleY", 1)))
                      for s, n, a in images}
            if record:
                self.doc.checkpoint(f"Edit bone {bone}")
            self.doc.set_bone(bone, record=False, **values)
            inverse = affine.invert(self.doc.evaluator("").bone_world(0)[bone])
            for child, world in kept.items():
                x, y, rot, sx, sy = affine.decompose(affine.mul(inverse, world))
                self.doc.set_bone(child, record=False, x=x, y=y, rotation=rot, scaleX=sx, scaleY=sy)
            for (slot, name), world in placed.items():
                x, y, rot, sx, sy = affine.decompose(affine.mul(inverse, world))
                self.doc.set_attachment(slot, name, record=False, x=x, y=y, rotation=rot, scaleX=sx, scaleY=sy)
            return
        if not self.auto_key:  # Auto Key off: show the change, key it with Key (K)
            pose = self.pose_overrides.setdefault(bone, {})
            for field, value in values.items():
                if field in self.POSE_INDEX:
                    pose[self.POSE_INDEX[field]] = value
            self.document_changed.emit()
            return
        setup = self.doc.bone(bone)
        if record:
            self.doc.checkpoint(f"Pose {bone}")
        t = self.time
        if "x" in values or "y" in values:
            current = self.evaluator().bone_locals(t)[bone]
            x = values.get("x", current[0]) - setup.get("x", 0)
            y = values.get("y", current[1]) - setup.get("y", 0)
            self.doc.set_bone_key(self.animation, bone, "translate", t, record=False, x=x, y=y)
        if "rotation" in values:
            self.doc.set_bone_key(self.animation, bone, "rotate", t, record=False,
                                  value=values["rotation"] - setup.get("rotation", 0))
        if "scaleX" in values or "scaleY" in values:
            current = self.evaluator().bone_locals(t)[bone]
            sx0, sy0 = setup.get("scaleX", 1) or 1e-6, setup.get("scaleY", 1) or 1e-6
            self.doc.set_bone_key(self.animation, bone, "scale", t, record=False,
                                  x=values.get("scaleX", current[3]) / sx0, y=values.get("scaleY", current[4]) / sy0)
        if "shearX" in values or "shearY" in values:  # shear keys add to the setup shear, like rotation
            current = self.evaluator().bone_locals(t)[bone]
            self.doc.set_bone_key(self.animation, bone, "shear", t, record=False,
                                  x=values.get("shearX", current[5]) - setup.get("shearX", 0),
                                  y=values.get("shearY", current[6]) - setup.get("shearY", 0))

    def key_selected_bone(self, timelines: tuple[str, ...] = ("rotate", "translate", "scale", "shear")) -> None:
        """Key the selected bone's current pose (K)."""
        if not self.animating or self.selection.kind != "bone":
            raise EditorError("Select a bone in Animate mode to set a key")
        bone = self.selection.name
        current = self.evaluator().bone_locals(self.time)[bone]
        setup = self.doc.bone(bone)
        self.doc.checkpoint(f"Key {bone}")
        if "translate" in timelines:
            self.doc.set_bone_key(self.animation, bone, "translate", self.time, record=False,
                                  x=current[0] - setup.get("x", 0), y=current[1] - setup.get("y", 0))
        if "rotate" in timelines:
            self.doc.set_bone_key(self.animation, bone, "rotate", self.time, record=False,
                                  value=current[2] - setup.get("rotation", 0))
        if "scale" in timelines:
            self.doc.set_bone_key(self.animation, bone, "scale", self.time, record=False,
                                  x=current[3] / (setup.get("scaleX", 1) or 1e-6),
                                  y=current[4] / (setup.get("scaleY", 1) or 1e-6))
        if "shear" in timelines:
            self.doc.set_bone_key(self.animation, bone, "shear", self.time, record=False,
                                  x=current[5] - setup.get("shearX", 0), y=current[6] - setup.get("shearY", 0))
        self.pose_overrides.pop(bone, None)
