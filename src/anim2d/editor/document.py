"""Editor document: a skeleton held in the Spine 3.8 JSON layout, with undo/redo.

Every mutating method records an undo snapshot first (pass record=False when a
caller already did, e.g. during a mouse drag). Listeners are notified after changes.

Supported for editing: bones, slots, region attachments, default skin, animations
with rotate/translate/scale/shear, colour and attachment keys, events, draw order.
Meshes, bounding boxes, points, clipping and their deform keys are kept as-is
(imported data round-trips) but are not edited.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..formats import atlas as atlas_packer
from ..formats import binary40, spine40
from ..formats import curves as cv
from ..formats import timelines40 as tl40
from ..models.effect import Color
from ..preview.mesh import key_offsets, parse_vertices
from ..templates import spine38_ops as ops
from ..validation.spine38_validator import validate_output
from . import affine
from .constraints import ConstraintsMixin
from .shapes import ShapesMixin
from .skins import SkinsMixin
from .bone_ops import BoneOpsMixin

PROJECT_FORMAT = "anim2d-project"
LEGACY_PROJECT_FORMATS = ("rigworks-project",)  # written before the rename; still opened
PROJECT_SUFFIX = ".anim2d"
PROJECT_SUFFIXES = (PROJECT_SUFFIX, ".rig2d")  # .rig2d: projects saved before the rename
PROJECT_VERSION = 2  # 1: Spine 3.8 layout; 2: Spine 4.0 layout (1 is converted on load)
STAMP_40 = spine40.STAMP_40  # the editor works in the Spine 4.0 layout
RUNTIME_STAMP = "3.8.99"  # official 3.8 runtimes reject data stamped exactly 3.8.75
EDITOR_STAMP = "3.8.75"
KEY_EPS = 1e-4
UNDO_LIMIT = 300
NAME_RE = re.compile(r"^[^\s/\\][^/\\]{0,63}$")
BONE_TIMELINE_FIELDS = {"rotate": ("value",), "translate": ("x", "y"), "scale": ("x", "y"), "shear": ("x", "y"),
                        "translatex": ("value",), "translatey": ("value",), "scalex": ("value",),
                        "scaley": ("value",), "shearx": ("value",), "sheary": ("value",)}
BONE_TIMELINE_DEFAULTS = {"rotate": {"value": 0.0}, "translate": {"x": 0.0, "y": 0.0},
                          "scale": {"x": 1.0, "y": 1.0}, "shear": {"x": 0.0, "y": 0.0},
                          "translatex": {"value": 0.0}, "translatey": {"value": 0.0}, "scalex": {"value": 1.0},
                          "scaley": {"value": 1.0}, "shearx": {"value": 0.0}, "sheary": {"value": 0.0}}
BONE_TIMELINE_ORDER = ("rotate", "translate", "translatex", "translatey", "scale", "scalex", "scaley",
                       "shear", "shearx", "sheary")
SLOT_TIMELINE_ORDER = ("attachment", "rgba", "rgb", "alpha", "rgba2", "rgb2")


class EditorError(ValueError):
    """A user-facing editing problem."""


@dataclass(frozen=True)
class KeyRef:
    group: str  # "bones", "slots", "ik", "transform", "path", "deform", "events" or "drawOrder"
    owner: str  # bone, slot or constraint name; "slot\nattachment" for deform ("" for events/drawOrder)
    timeline: str  # "rotate", "translate", "color", "attachment", "events", "drawOrder", ...
    time: float


def new_skeleton(fps: float = 30) -> dict[str, Any]:
    return {
        "skeleton": {"spine": STAMP_40, "fps": fps, "images": "./images/", "audio": ""},
        "bones": [{"name": "root"}],
        "slots": [],
        "skins": [{"name": "default", "attachments": {}}],
        "events": {},
        "animations": {},
    }


def _r(value: float) -> float:
    v = round(float(value), 4)
    return 0.0 if v == 0 else v


class Document(ConstraintsMixin, ShapesMixin, SkinsMixin, BoneOpsMixin):
    def __init__(self, data: dict[str, Any] | None = None, images_dir: Path | None = None,
                 path: Path | None = None):
        self.data = data if data is not None else new_skeleton()
        self.images_dir = Path(images_dir) if images_dir else None
        self.path = Path(path) if path else None
        self.dirty = False
        self._undo: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []
        self.listeners: list[Callable[[], None]] = []
        # Weights view memory (this session only, not saved): bones bound with no weight yet, and
        # the bone setup transforms at the last Bind, for Update Bindings. Keyed (skin, slot, mesh).
        self.extra_bound_bones: dict[tuple[str, str, str], set[str]] = {}
        self.bind_poses: dict[tuple[str, str, str], dict[str, Any]] = {}
        # Other skeletons in the same project (they share the images folder): name -> (data, undo, redo)
        self.skeleton_name = "skeleton"
        self.other_skeletons: dict[str, tuple[dict[str, Any], list, list]] = {}
        self._skeleton_order: list[str] = []  # names in the order they were added
        self.project_settings: dict[str, Any] = {}  # saved with the project (e.g. "unity": the Unity bridge)
        self._normalize()

    # --- infrastructure -------------------------------------------------------------------

    def _normalize(self) -> None:
        if not spine40.is_40(self.data):  # 3.8 data (imports, version 1 projects) -> 4.0 layout
            self.data = spine40.upgrade(self.data)
        d = self.data
        d.setdefault("skeleton", {}).setdefault("fps", 30)
        d.setdefault("bones", [{"name": "root"}])
        d.setdefault("slots", [])
        skins = d.setdefault("skins", [])
        if not skins:
            skins.append({"name": "default", "attachments": {}})
        skins[0].setdefault("attachments", {})
        d.setdefault("events", {})
        d.setdefault("animations", {})
        d["skeleton"].pop("hash", None)
        for anim in d["animations"].values():
            if "draworder" in anim:
                anim["drawOrder"] = anim.pop("draworder")

    def _snapshot(self) -> str:
        return json.dumps(self.data, separators=(",", ":"))

    def checkpoint(self, label: str) -> None:
        self._undo.append((label, self._snapshot()))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self.dirty = True

    def notify(self) -> None:
        for listener in list(self.listeners):
            listener()

    def _begin(self, label: str, record: bool) -> None:
        if record:
            self.checkpoint(label)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> bool:
        if not self._undo:
            return False
        label, snap = self._undo.pop()
        self._redo.append((label, self._snapshot()))
        self.data = json.loads(snap)
        self.dirty = True
        self.notify()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        label, snap = self._redo.pop()
        self._undo.append((label, self._snapshot()))
        self.data = json.loads(snap)
        self.dirty = True
        self.notify()
        return True

    # --- skeletons in the project ------------------------------------------------------------

    def skeleton_names(self) -> list[str]:
        """Every skeleton in the project, the one being edited included, in the order they were added."""
        self._remember_order()
        return list(self._skeleton_order)

    def _remember_order(self) -> None:
        names = [self.skeleton_name, *self.other_skeletons]
        order = [n for n in self._skeleton_order if n in names]
        self._skeleton_order = order + [n for n in names if n not in order]

    def _check_skeleton_name(self, name: str) -> str:
        name = name.strip()
        if not name:
            raise EditorError("A skeleton needs a name")
        if name == self.skeleton_name or name in self.other_skeletons:
            raise EditorError(f"A skeleton named '{name}' already exists")
        return name

    def add_skeleton(self, name: str, data: dict[str, Any] | None = None, switch: bool = True) -> str:
        """Add a skeleton to the project (empty, or the given data). Not undoable: delete it instead."""
        self._remember_order()
        name = self._check_skeleton_name(name)
        other = Document(copy.deepcopy(data) if data is not None else None)
        other.data["skeleton"]["fps"] = self.fps
        self.other_skeletons[name] = (other.data, [], [])
        self._remember_order()
        self.dirty = True
        if switch:
            self.switch_skeleton(name)
        return name

    def switch_skeleton(self, name: str) -> None:
        """Edit another skeleton of the project (each keeps its own undo history)."""
        if name == self.skeleton_name:
            return
        if name not in self.other_skeletons:
            raise EditorError(f"No skeleton named '{name}'")
        self._remember_order()
        data, undo, redo = self.other_skeletons.pop(name)
        self.other_skeletons[self.skeleton_name] = (self.data, self._undo, self._redo)
        self.skeleton_name, self.data, self._undo, self._redo = name, data, undo, redo
        self.extra_bound_bones, self.bind_poses = {}, {}
        self._normalize()
        self.notify()

    def rename_skeleton(self, old: str, new: str) -> None:
        if old == new:
            return
        new = self._check_skeleton_name(new)
        self._remember_order()
        order = [new if n == old else n for n in self._skeleton_order]
        if old == self.skeleton_name:
            self.skeleton_name = new
        elif old in self.other_skeletons:
            self.other_skeletons = {(new if k == old else k): v for k, v in self.other_skeletons.items()}
        else:
            raise EditorError(f"No skeleton named '{old}'")
        self._skeleton_order = order
        self.dirty = True
        self.notify()

    def delete_skeleton(self, name: str) -> None:
        """Remove a skeleton from the project (not undoable). The last one can't be deleted."""
        if not self.other_skeletons:
            raise EditorError("A project needs at least one skeleton")
        self._remember_order()
        if name == self.skeleton_name:
            nxt = next(n for n in self._skeleton_order if n != name)
            self.switch_skeleton(nxt)
        if name not in self.other_skeletons:
            raise EditorError(f"No skeleton named '{name}'")
        del self.other_skeletons[name]
        self._remember_order()
        self.dirty = True
        self.notify()

    def skeleton_data(self, name: str) -> dict[str, Any]:
        """A skeleton's data (the live data for the one being edited)."""
        if name == self.skeleton_name:
            return self.data
        if name not in self.other_skeletons:
            raise EditorError(f"No skeleton named '{name}'")
        return self.other_skeletons[name][0]

    def history(self) -> tuple[list[str], list[str]]:
        """(steps done, oldest first; steps undone that redo would bring back, next first)."""
        return [label for label, _ in self._undo], [label for label, _ in reversed(self._redo)]

    def go_to_history(self, done: int) -> None:
        """Undo or redo until `done` steps are applied (0: before the first step in the history)."""
        done = max(0, min(done, len(self._undo) + len(self._redo)))
        while len(self._undo) > done:
            self.undo()
        while len(self._undo) < done:
            self.redo()

    # --- queries --------------------------------------------------------------------------

    @property
    def fps(self) -> float:
        return float(self.data["skeleton"].get("fps", 30) or 30)

    @property
    def bones(self) -> list[dict[str, Any]]:
        return self.data["bones"]

    @property
    def slots(self) -> list[dict[str, Any]]:
        return self.data["slots"]

    @property
    def skin(self) -> dict[str, dict[str, Any]]:
        """Attachments of the edit skin, per slot (the default skin unless another is being edited)."""
        for s in self.data["skins"]:
            if s["name"] == self.edit_skin:
                return s.setdefault("attachments", {})
        self.edit_skin = "default"
        return self.data["skins"][0]["attachments"]

    @property
    def root_name(self) -> str:
        return self.bones[0]["name"]

    def bone(self, name: str) -> dict[str, Any]:
        for b in self.bones:
            if b["name"] == name:
                return b
        raise EditorError(f"No bone named '{name}'")

    def slot(self, name: str) -> dict[str, Any]:
        for s in self.slots:
            if s["name"] == name:
                return s
        raise EditorError(f"No slot named '{name}'")

    def has_bone(self, name: str) -> bool:
        return any(b["name"] == name for b in self.bones)

    def has_slot(self, name: str) -> bool:
        return any(s["name"] == name for s in self.slots)

    def children(self, name: str) -> list[str]:
        return [b["name"] for b in self.bones if b.get("parent") == name]

    def descendants(self, name: str) -> list[str]:
        result, stack = [], [name]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                result.append(child)
                stack.append(child)
        return result

    def slots_of_bone(self, bone: str) -> list[str]:
        return [s["name"] for s in self.slots if s["bone"] == bone]

    def attachments(self, slot: str) -> dict[str, Any]:
        """The edit skin's attachments of a slot. Writers use self.skin.setdefault(slot, {})."""
        return self.skin.get(slot, {})

    def animation_names(self) -> list[str]:
        return list(self.data["animations"])

    def animation(self, name: str) -> dict[str, Any]:
        if name not in self.data["animations"]:
            raise EditorError(f"No animation named '{name}'")
        return self.data["animations"][name]

    def animation_duration(self, name: str) -> float:
        return ops.animation_duration(self.animation(name)) if name in self.data["animations"] else 0.0

    def event_names(self) -> list[str]:
        return list(self.data.get("events", {}))

    def image_paths(self) -> list[str]:
        """Images available in the images folder, as attachment paths (relative, no extension)."""
        if not self.images_dir or not self.images_dir.is_dir():
            return []
        paths = []
        for file in sorted(self.images_dir.rglob("*.png")):
            rel = file.relative_to(self.images_dir).with_suffix("").as_posix()
            paths.append(rel)
        return paths

    def image_file(self, path: str) -> Path | None:
        if not self.images_dir:
            return None
        file = self.images_dir / f"{path}.png"
        return file if file.is_file() else None

    def unique_name(self, base: str, existing: set[str]) -> str:
        base = base.strip() or "item"
        if base not in existing:
            return base
        n = 2
        while f"{base}{n}" in existing:
            n += 1
        return f"{base}{n}"

    def _check_new_name(self, name: str, existing: set[str], kind: str) -> None:
        if not isinstance(name, str) or not NAME_RE.match(name) or name != name.strip():
            raise EditorError(f"'{name}' is not a valid {kind} name (1-64 characters, no slashes, "
                              f"no leading or trailing spaces)")
        if name in existing:
            raise EditorError(f"A {kind} named '{name}' already exists")

    # --- bones ----------------------------------------------------------------------------

    def bone_world_setup(self) -> dict[str, affine.Affine]:
        world: dict[str, affine.Affine] = {}
        for b in self.bones:
            m = affine.local(b.get("x", 0), b.get("y", 0), b.get("rotation", 0), b.get("scaleX", 1), b.get("scaleY", 1))
            world[b["name"]] = affine.mul(world[b["parent"]], m) if b.get("parent") else m
        return world

    def add_bone(self, parent: str, name: str | None = None, *, x: float = 0, y: float = 0,
                 rotation: float = 0, length: float = 0, record: bool = True) -> str:
        self.bone(parent)
        existing = {b["name"] for b in self.bones}
        name = name or self.unique_name("bone", existing)
        self._check_new_name(name, existing, "bone")
        self._begin(f"Create bone {name}", record)
        bone = {"name": name, "parent": parent, "length": _r(length), "rotation": _r(rotation),
                "x": _r(x), "y": _r(y)}
        # Insert after the parent's last descendant so parents always come before children.
        names = [b["name"] for b in self.bones]
        family = [parent, *self.descendants(parent)]
        index = max(names.index(n) for n in family) + 1
        self.bones.insert(index, bone)
        self._remap_weights(names)
        self.notify()
        return name

    def _remap_weights(self, old_order: list[str]) -> None:
        """Weighted mesh vertices store bone indices; keep them pointing at the same bones."""
        if [b["name"] for b in self.bones] != old_order:
            from .mesh_ops import remap_weight_indices
            remap_weight_indices(self, old_order)

    def set_bone(self, name: str, record: bool = True, **fields: Any) -> None:
        bone = self.bone(name)
        allowed = {"x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length", "color", "transform", "skin"}
        unknown = set(fields) - allowed
        if unknown:
            raise EditorError(f"Unknown bone field(s): {', '.join(sorted(unknown))}")
        self._begin(f"Edit bone {name}", record)
        for key, value in fields.items():
            bone[key] = _r(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
        self.notify()

    def delete_bone(self, name: str, record: bool = True) -> None:
        if name == self.root_name:
            raise EditorError("The root bone cannot be deleted")
        self.bone(name)
        doomed = {name, *self.descendants(name)}
        doomed_slots = [s["name"] for s in self.slots if s["bone"] in doomed]
        from .mesh_ops import bones_used_by_weights
        for bone, meshes in bones_used_by_weights(self).items():
            others = [m for m in meshes if m.split("/")[0] not in doomed_slots]
            if bone in doomed and others:
                raise EditorError(f"Bone '{bone}' weights mesh {others[0]}. Unbind the mesh or remove "
                                  f"that bone from its weights first.")
        self._begin(f"Delete bone {name}", record)
        for slot in doomed_slots:
            self._delete_slot_data(slot)
        old_order = [b["name"] for b in self.bones]
        self.data["bones"] = [b for b in self.bones if b["name"] not in doomed]
        self._remap_weights(old_order)
        for anim in self.data["animations"].values():
            for bone in doomed:
                anim.get("bones", {}).pop(bone, None)
        for ik in [c for c in self.iks if doomed & {*c["bones"], c["target"]}]:
            self._delete_ik_data(ik["name"])
        self._constraints_bones_deleted(doomed)
        if "physics" in self.data:
            self.data["physics"] = [c for c in self.data["physics"] if c["bone"] not in doomed]
            if not self.data["physics"]:
                del self.data["physics"]
        for skin in self.data.get("skins", []):
            if doomed & set(skin.get("bones", [])):
                skin["bones"] = [b for b in skin["bones"] if b not in doomed]
                if not skin["bones"]:
                    del skin["bones"]
        self.notify()

    def reparent_bone(self, name: str, new_parent: str, record: bool = True) -> None:
        """Move a bone under another parent, keeping its setup pose in world space."""
        if name == self.root_name:
            raise EditorError("The root bone cannot be reparented")
        if new_parent == name or new_parent in self.descendants(name):
            raise EditorError("A bone cannot become a child of itself or its descendants")
        self.bone(new_parent)
        for ik in self.iks:
            if len(ik["bones"]) == 2 and ik["bones"][1] == name and new_parent != ik["bones"][0]:
                raise EditorError(f"'{name}' is the child bone of IK constraint '{ik['name']}'; "
                                  f"delete the constraint before reparenting it")
        world = self.bone_world_setup()
        new_local = affine.mul(affine.invert(world[new_parent]), world[name])
        x, y, rot, sx, sy = affine.decompose(new_local)
        self._begin(f"Reparent bone {name}", record)
        bone = self.bone(name)
        old_order = [b["name"] for b in self.bones]
        moving = [b for b in self.bones if b["name"] in {name, *self.descendants(name)}]
        self.data["bones"] = [b for b in self.bones if b not in moving]
        bone.update(parent=new_parent, x=_r(x), y=_r(y), rotation=_r(rot), scaleX=_r(sx), scaleY=_r(sy))
        names = [b["name"] for b in self.bones]
        family = [new_parent, *[n for n in self.descendants(new_parent) if n in names]]
        index = max(names.index(n) for n in family) + 1
        self.data["bones"][index:index] = moving
        self._remap_weights(old_order)
        self.notify()

    def rename_bone(self, old: str, new: str, record: bool = True) -> None:
        self.bone(old)
        if old == new:
            return
        self._check_new_name(new, {b["name"] for b in self.bones}, "bone")
        self._begin(f"Rename bone {old}", record)
        for b in self.bones:
            if b["name"] == old:
                b["name"] = new
            if b.get("parent") == old:
                b["parent"] = new
        for s in self.slots:
            if s["bone"] == old:
                s["bone"] = new
        for ik in self.iks:
            ik["bones"] = [new if b == old else b for b in ik["bones"]]
            if ik["target"] == old:
                ik["target"] = new
        self._constraints_bone_renamed(old, new)
        for c in self.data.get("physics", []):
            if c["bone"] == old:
                c["bone"] = new
        for anim in self.data["animations"].values():
            bones = anim.get("bones", {})
            if old in bones:
                anim["bones"] = {(new if k == old else k): v for k, v in bones.items()}
        self.notify()

    # --- IK constraints -------------------------------------------------------------------

    @property
    def iks(self) -> list[dict[str, Any]]:
        return self.data.get("ik", [])

    def ik(self, name: str) -> dict[str, Any]:
        for c in self.iks:
            if c["name"] == name:
                return c
        raise EditorError(f"No IK constraint named '{name}'")

    def _constraint_names(self) -> set[str]:
        return {c["name"] for group in ("ik", "transform", "path") for c in self.data.get(group, [])}

    def _check_ik_bones(self, bones: list[str], target: str | None, ignore: str | None = None) -> None:
        """target=None checks only the chain (used before a new target bone is created)."""
        if len(bones) not in (1, 2):
            raise EditorError("An IK constraint controls one bone or a parent and child")
        for b in [*bones, *([target] if target else [])]:
            self.bone(b)
        if len(bones) == 2 and self.bone(bones[1]).get("parent") != bones[0]:
            raise EditorError(f"'{bones[1]}' must be a direct child of '{bones[0]}'")
        if bones[0] == self.root_name:
            raise EditorError("The root bone cannot be IK constrained")
        if target and (target in bones or target in self.descendants(bones[0])):
            raise EditorError("The target cannot be a constrained bone or one of their children")
        for c in self.iks:
            if c["name"] != ignore and set(c["bones"]) & set(bones):
                raise EditorError(f"'{(set(c['bones']) & set(bones)).pop()}' is already constrained by '{c['name']}'")

    def add_ik(self, bones: list[str], target: str, name: str | None = None, *, bend_positive: bool = True,
               mix: float = 1.0, softness: float = 0.0, compress: bool = False, stretch: bool = False,
               uniform: bool = False, record: bool = True) -> str:
        existing = self._constraint_names()
        name = name or self.unique_name(f"{bones[-1]}-ik" if bones else "ik", existing)
        self._check_new_name(name, existing, "constraint")
        self._check_ik_bones(bones, target)
        if not 0 <= mix <= 1:
            raise EditorError("Mix must be between 0 and 1")
        self._begin(f"Create IK {name}", record)
        constraint: dict[str, Any] = {"name": name, "order": len(self._ordered_constraints()), "bones": list(bones),
                                      "target": target}
        constraint.update(self._ik_fields(mix=mix, softness=softness, bendPositive=bend_positive,
                                          compress=compress, stretch=stretch, uniform=uniform))
        self.data.setdefault("ik", []).append(constraint)
        self.notify()
        return name

    def add_ik_with_target(self, bones: list[str], name: str | None = None, record: bool = True,
                           **options: Any) -> tuple[str, str]:
        """Create a target bone under the root at the tip of the chain's last bone (as currently
        posed), then the constraint. One undo step. Returns (constraint name, target name)."""
        end = self.bone(bones[-1])
        world = self.evaluator("").bone_world(0)
        tip = affine.apply(world[end["name"]], end.get("length", 0), 0)
        x, y = affine.apply(affine.invert(world[self.root_name]), *tip)
        target_name = self.unique_name(f"{end['name']}-target", {b["name"] for b in self.bones})
        self._check_ik_bones(bones, None)  # fail before creating the target bone
        if name:
            self._check_new_name(name, self._constraint_names(), "constraint")
        self._begin(f"Create IK for {end['name']}", record)
        self.add_bone(self.root_name, target_name, x=x, y=y, record=False)
        name = self.add_ik(bones, target_name, name, record=False, **options)
        return name, target_name

    @staticmethod
    def _ik_fields(**fields: Any) -> dict[str, Any]:
        """Only non-default values are written, like Spine's exporter."""
        defaults = {"mix": 1, "softness": 0, "bendPositive": True, "compress": False, "stretch": False,
                    "uniform": False}
        return {k: (_r(v) if k in ("mix", "softness") else bool(v)) for k, v in fields.items()
                if k in defaults and v != defaults[k]}

    def _ordered_constraints(self) -> list[dict[str, Any]]:
        return [c for group in ("ik", "transform", "path") for c in self.data.get(group, [])]

    def set_ik(self, name: str, record: bool = True, **fields: Any) -> None:
        constraint = self.ik(name)
        allowed = {"mix", "softness", "bendPositive", "compress", "stretch", "uniform", "target"}
        unknown = set(fields) - allowed
        if unknown:
            raise EditorError(f"Unknown IK field(s): {', '.join(sorted(unknown))}")
        if "target" in fields:
            self._check_ik_bones(constraint["bones"], fields["target"], ignore=name)
        if "mix" in fields and not 0 <= fields["mix"] <= 1:
            raise EditorError("Mix must be between 0 and 1")
        if "softness" in fields and fields["softness"] < 0:
            raise EditorError("Softness cannot be negative")
        self._begin(f"Edit IK {name}", record)
        if "target" in fields:
            constraint["target"] = fields.pop("target")
        for key in fields:
            constraint.pop(key, None)
        constraint.update(self._ik_fields(**fields))
        self.notify()

    def _delete_ik_data(self, name: str) -> None:
        for skin in self.data.get("skins", []):
            if name in skin.get("ik", []):
                skin["ik"] = [n for n in skin["ik"] if n != name]
                if not skin["ik"]:
                    del skin["ik"]
        self.data["ik"] = [c for c in self.iks if c["name"] != name]
        for i, c in enumerate(self._ordered_constraints()):
            c["order"] = i  # keep orders contiguous; the runtime only applies orders 0..n-1
        for anim in self.data["animations"].values():
            anim.get("ik", {}).pop(name, None)
            if "ik" in anim and not anim["ik"]:
                del anim["ik"]
        if not self.data["ik"]:
            del self.data["ik"]

    def delete_ik(self, name: str, record: bool = True) -> None:
        self.ik(name)
        self._begin(f"Delete IK {name}", record)
        self._delete_ik_data(name)
        self.notify()

    def rename_ik(self, old: str, new: str, record: bool = True) -> None:
        self.ik(old)
        if old == new:
            return
        self._check_new_name(new, self._constraint_names(), "constraint")
        self._begin(f"Rename IK {old}", record)
        self.ik(old)["name"] = new
        for anim in self.data["animations"].values():
            if old in anim.get("ik", {}):
                anim["ik"] = {(new if k == old else k): v for k, v in anim["ik"].items()}
        self.notify()

    def set_ik_key(self, animation: str, name: str, time: float, record: bool = True, **values: Any) -> None:
        """Key mix/softness/bendPositive/compress/stretch. Unspecified fields keep the value at that
        time (from an existing key, or the constraint's current posed value)."""
        anim = self.animation(animation)
        constraint = self.ik(name)
        allowed = {"mix", "softness", "bendPositive", "compress", "stretch"}
        if set(values) - allowed:
            raise EditorError(f"IK keys take {', '.join(sorted(allowed))}")
        if time < 0:
            raise EditorError("Key time cannot be negative")
        if "mix" in values and not 0 <= values["mix"] <= 1:
            raise EditorError("Mix must be between 0 and 1")
        posed = self.evaluator(animation).ik_state(time).get(name)
        current = {"mix": constraint.get("mix", 1), "softness": constraint.get("softness", 0),
                   "bendPositive": constraint.get("bendPositive", True),
                   "compress": constraint.get("compress", False), "stretch": constraint.get("stretch", False)}
        if posed:
            current.update(mix=posed["mix"], softness=posed["softness"], bendPositive=posed["bend"] > 0,
                           compress=posed["compress"], stretch=posed["stretch"])
        self._begin(f"Key IK {name}", record)
        keys = anim.setdefault("ik", {}).setdefault(name, [])
        existing = next((k for k in keys if abs(k.get("time", 0) - time) < KEY_EPS), None)
        if existing is not None:
            current.update({f: existing.get(f, d) for f, d in (("mix", 1), ("softness", 0), ("bendPositive", True),
                                                               ("compress", False), ("stretch", False))})
        current.update(values)
        # IK keys default to mix 1 (not the constraint's mix), so mix is always written explicitly.
        fields = {"mix": _r(current["mix"]), **{k: v for k, v in self._ik_fields(**current).items() if k != "mix"}}
        snap = tl40.snapshot(keys, tl40.IK_READER)
        if existing is not None:
            for f in ("mix", "softness", "bendPositive", "compress", "stretch"):
                existing.pop(f, None)
            existing.update(fields)
        else:
            keys.append({"time": _r(time), **fields})
            keys.sort(key=lambda k: k.get("time", 0))
        tl40.restore(keys, tl40.IK_READER, snap)
        self.notify()

    # --- slots and attachments ------------------------------------------------------------

    def add_slot(self, bone: str, name: str | None = None, image: str | None = None,
                 record: bool = True) -> str:
        self.bone(bone)
        existing = {s["name"] for s in self.slots}
        name = name or self.unique_name(Path(image).name if image else bone, existing)
        self._check_new_name(name, existing, "slot")
        self._begin(f"Create slot {name}", record)
        self.slots.append({"name": name, "bone": bone, "color": "ffffffff"})
        self.skin[name] = {}
        if image:
            self.add_attachment(name, image, record=False, set_visible=True)
        self.notify()
        return name

    def add_attachment(self, slot: str, image: str, name: str | None = None, record: bool = True,
                       set_visible: bool = False) -> str:
        s = self.slot(slot)
        file = self.image_file(image)
        if file is None:
            raise EditorError(f"Image '{image}.png' is not in the images folder")
        with Image.open(file) as img:
            width, height = img.size
        existing = set(self.attachments(slot))
        name = name or self.unique_name(Path(image).name, existing)
        self._check_new_name(name, existing, "attachment")
        self._begin(f"Add attachment {name}", record)
        self.skin.setdefault(slot, {})[name] = {"path": image, "width": width, "height": height}
        if set_visible or s.get("attachment") is None:
            s["attachment"] = name
        self.notify()
        return name

    def set_attachment(self, slot: str, name: str, record: bool = True, **fields: Any) -> None:
        att = self.attachments(slot).get(name)
        if att is None:
            raise EditorError(f"Slot '{slot}' has no attachment '{name}'")
        allowed = {"x", "y", "rotation", "scaleX", "scaleY", "width", "height", "path", "color"}
        unknown = set(fields) - allowed
        if unknown:
            raise EditorError(f"Unknown attachment field(s): {', '.join(sorted(unknown))}")
        self._begin(f"Edit attachment {name}", record)
        for key, value in fields.items():
            att[key] = _r(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
        self.notify()

    def set_slot(self, name: str, record: bool = True, **fields: Any) -> None:
        slot = self.slot(name)
        if "attachment" in fields and fields["attachment"] is not None \
                and fields["attachment"] not in self.all_attachment_names(name):
            raise EditorError(f"Slot '{name}' has no attachment '{fields['attachment']}'")
        if "color" in fields:
            fields["color"] = Color.from_hex(fields["color"]).to_hex()
        if "blend" in fields and fields["blend"] not in ("normal", "additive", "multiply", "screen"):
            raise EditorError(f"Unknown blend mode '{fields['blend']}'")
        if "bone" in fields:
            self.bone(fields["bone"])
        unknown = set(fields) - {"attachment", "color", "blend", "bone", "dark"}
        if unknown:
            raise EditorError(f"Unknown slot field(s): {', '.join(sorted(unknown))}")
        self._begin(f"Edit slot {name}", record)
        for key, value in fields.items():
            if value is None and key in ("attachment", "dark"):
                slot.pop(key, None)
            else:
                slot[key] = value
        if slot.get("blend") == "normal":
            slot.pop("blend")
        self.notify()

    def _delete_slot_data(self, name: str) -> None:
        self.data["slots"] = [s for s in self.slots if s["name"] != name]
        self._constraints_slot_deleted(name)
        for skin in self.data["skins"]:
            skin.get("attachments", {}).pop(name, None)
            for entries in skin.get("attachments", {}).values():
                for att in entries.values():
                    if att.get("type") == "clipping" and att.get("end") == name:
                        att.pop("end")
        for anim in self.data["animations"].values():
            anim.get("slots", {}).pop(name, None)
            for skin in anim.get("deform", {}).values():
                skin.pop(name, None)
            for key in anim.get("drawOrder", []):
                key["offsets"] = [o for o in key.get("offsets", []) if o["slot"] != name]

    def delete_slot(self, name: str, record: bool = True) -> None:
        self.slot(name)
        self._begin(f"Delete slot {name}", record)
        self._delete_slot_data(name)
        self.notify()

    def delete_attachment(self, slot: str, name: str, record: bool = True) -> None:
        if name not in self.attachments(slot):
            raise EditorError(f"Slot '{slot}' has no attachment '{name}'")
        self._begin(f"Delete attachment {name}", record)
        del self.skin[slot][name]
        s = self.slot(slot)
        if s.get("attachment") == name:
            s.pop("attachment")
        for anim in self.data["animations"].values():
            keys = anim.get("slots", {}).get(slot, {}).get("attachment", [])
            for k in keys:
                if k.get("name") == name:
                    k["name"] = None
            for skin in anim.get("deform", {}).values():
                skin.get(slot, {}).pop(name, None)
        self.notify()

    def rename_slot(self, old: str, new: str, record: bool = True) -> None:
        self.slot(old)
        if old == new:
            return
        self._check_new_name(new, {s["name"] for s in self.slots}, "slot")
        self._begin(f"Rename slot {old}", record)

        def swap(mapping: dict[str, Any]) -> dict[str, Any]:
            return {(new if k == old else k): v for k, v in mapping.items()}

        self.slot(old)["name"] = new
        self._constraints_slot_renamed(old, new)
        for skin in self.data["skins"]:
            skin["attachments"] = swap(skin.get("attachments", {}))
            for entries in skin["attachments"].values():
                for att in entries.values():
                    if att.get("type") == "clipping" and att.get("end") == old:
                        att["end"] = new
        for anim in self.data["animations"].values():
            if "slots" in anim:
                anim["slots"] = swap(anim["slots"])
            for skin_name, slots in anim.get("deform", {}).items():
                anim["deform"][skin_name] = swap(slots)
            for key in anim.get("drawOrder", []):
                for o in key.get("offsets", []):
                    if o["slot"] == old:
                        o["slot"] = new
        self.notify()

    def rename_attachment(self, slot: str, old: str, new: str, record: bool = True) -> None:
        entries = self.attachments(slot)
        if old not in entries:
            raise EditorError(f"Slot '{slot}' has no attachment '{old}'")
        if old == new:
            return
        self._check_new_name(new, set(entries), "attachment")
        self._begin(f"Rename attachment {old}", record)
        att = entries[old]
        if "path" not in att and att.get("type", "region") in ("region", "mesh"):
            att["path"] = old  # keep pointing at the same image
        self.skin[slot] = {(new if k == old else k): v for k, v in entries.items()}
        s = self.slot(slot)
        if s.get("attachment") == old:
            s["attachment"] = new
        for anim in self.data["animations"].values():
            for k in anim.get("slots", {}).get(slot, {}).get("attachment", []):
                if k.get("name") == old:
                    k["name"] = new
            for skin in anim.get("deform", {}).values():
                if old in skin.get(slot, {}):
                    skin[slot] = {(new if k == old else k): v for k, v in skin[slot].items()}
        self.notify()

    def move_slot(self, name: str, delta: int, record: bool = True) -> None:
        """Change draw order: positive delta draws the slot later (on top)."""
        names = [s["name"] for s in self.slots]
        i = names.index(self.slot(name)["name"])
        j = max(0, min(len(names) - 1, i + delta))
        if i == j:
            return
        self._begin(f"Draw order {name}", record)
        slot = self.slots.pop(i)
        self.slots.insert(j, slot)
        self.notify()

    # --- draw-order keys -------------------------------------------------------------------

    def draw_order_at(self, animation: str, time: float) -> list[str]:
        """Slot names back to front at that time (setup order plus the draw-order keys)."""
        return [s["name"] for s in self.evaluator(animation).draw_order(time)]

    def set_draw_order_key(self, animation: str, time: float, order: list[str], record: bool = True) -> None:
        """Key a draw order. Offsets are listed for every slot not at its setup index, in setup
        order: the runtime places those and fills the rest in setup order, giving back this order."""
        setup = [s["name"] for s in self.slots]
        if sorted(order) != sorted(setup):
            raise EditorError("A draw order must list every slot once")
        anim = self.animation(animation)
        self._begin(f"Key draw order at {time:.3f}s", record)
        where = {name: i for i, name in enumerate(order)}
        offsets = [{"slot": name, "offset": where[name] - i} for i, name in enumerate(setup) if where[name] != i]
        key = {"time": round(time, 6)} if time else {}
        if offsets:
            key["offsets"] = offsets
        keys = [k for k in anim.get("drawOrder", []) if abs(k.get("time", 0) - time) > 1e-6]
        keys.append(key)
        keys.sort(key=lambda k: k.get("time", 0))
        anim["drawOrder"] = keys
        self.notify()

    def move_slot_keyed(self, animation: str, time: float, name: str, delta: int, record: bool = True) -> None:
        """Animate mode: draw a slot earlier/later from this time on (a draw-order key)."""
        order = self.draw_order_at(animation, time)
        i = order.index(self.slot(name)["name"])
        j = max(0, min(len(order) - 1, i + delta))
        if i == j:
            return
        order.insert(j, order.pop(i))
        self.set_draw_order_key(animation, time, order, record)

    # --- animations -----------------------------------------------------------------------

    def add_animation(self, name: str | None = None, record: bool = True) -> str:
        existing = set(self.data["animations"])
        name = name or self.unique_name("animation", existing)
        self._check_new_name(name, existing, "animation")
        self._begin(f"Create animation {name}", record)
        self.data["animations"][name] = {}
        self.notify()
        return name

    def rename_animation(self, old: str, new: str, record: bool = True) -> None:
        self.animation(old)
        if old == new:
            return
        self._check_new_name(new, set(self.data["animations"]), "animation")
        self._begin(f"Rename animation {old}", record)
        self.data["animations"] = {(new if k == old else k): v for k, v in self.data["animations"].items()}
        self.notify()

    def duplicate_animation(self, name: str, record: bool = True) -> str:
        source = self.animation(name)
        new = self.unique_name(f"{name}_copy", set(self.data["animations"]))
        self._begin(f"Duplicate animation {name}", record)
        self.data["animations"][new] = copy.deepcopy(source)
        self.notify()
        return new

    def delete_animation(self, name: str, record: bool = True) -> None:
        self.animation(name)
        self._begin(f"Delete animation {name}", record)
        del self.data["animations"][name]
        self.notify()

    # --- keys -----------------------------------------------------------------------------

    @staticmethod
    def _put_key(keys: list[dict[str, Any]], time: float, values: dict[str, Any],
                 reader: Any = False) -> None:
        """Insert or update the key at time. reader (a timelines40 reader, or None for deform)
        keeps 4.0 curve shapes; pass nothing for timelines without curves."""
        time = _r(time)
        snap = tl40.snapshot(keys, reader) if reader is not False else None
        for k in keys:
            if abs(k.get("time", 0) - time) < KEY_EPS:
                k.update(values)
                k["time"] = time
                break
        else:
            keys.append({"time": time, **values})
            keys.sort(key=lambda k: k.get("time", 0))
        if snap is not None:
            tl40.restore(keys, reader, snap)

    def set_bone_key(self, animation: str, bone: str, timeline: str, time: float, record: bool = True,
                     **values: float) -> None:
        anim = self.animation(animation)
        self.bone(bone)
        if "angle" in values:  # 3.8 name for a rotate key's value
            values["value"] = values.pop("angle")
        if timeline not in BONE_TIMELINE_FIELDS:
            raise EditorError(f"Unknown bone timeline '{timeline}'")
        if time < 0:
            raise EditorError("Key time cannot be negative")
        fields = BONE_TIMELINE_FIELDS[timeline]
        if set(values) - set(fields):
            raise EditorError(f"{timeline} keys take {', '.join(fields)}")
        tls = anim.setdefault("bones", {}).setdefault(bone, {})
        if timeline in ("translate", "scale", "shear") and timeline not in tls and \
                (timeline + "x" in tls or timeline + "y" in tls):
            # The bone uses separate X/Y timelines (Spine 4.0): key those instead.
            self._begin(f"Key {timeline} {bone}", record)
            for axis, field in (("x", "x"), ("y", "y")):
                if field in values:
                    self.set_bone_key(animation, bone, timeline + axis, time, record=False, value=values[field])
            self.notify()
            return
        self._begin(f"Key {timeline} {bone}", record)
        keys = tls.setdefault(timeline, [])
        full = {**BONE_TIMELINE_DEFAULTS[timeline], **self._value_at_bone(keys, time, timeline), **values}
        self._put_key(keys, time, {f: _r(full[f]) for f in fields}, tl40.BONE_TIMELINES[timeline])
        self.notify()

    def _value_at_bone(self, keys: list[dict[str, Any]], time: float, timeline: str) -> dict[str, float]:
        """Existing keyed value at time (exact key only) so partial updates keep the other axis."""
        for k in keys:
            if abs(k.get("time", 0) - time) < KEY_EPS:
                return {f: k.get(f, BONE_TIMELINE_DEFAULTS[timeline][f]) for f in BONE_TIMELINE_FIELDS[timeline]}
        return {}

    def set_slot_key(self, animation: str, slot: str, timeline: str, time: float, value: Any,
                     record: bool = True) -> None:
        anim = self.animation(animation)
        self.slot(slot)
        if time < 0:
            raise EditorError("Key time cannot be negative")
        tls = anim.setdefault("slots", {}).setdefault(slot, {})
        if timeline in ("color", "rgba"):
            color = Color.from_hex(value).to_hex()
            self._begin(f"Key colour {slot}", record)
            if "rgba" not in tls and ("rgb" in tls or "alpha" in tls):
                # The slot keys colour and alpha separately (Spine 4.0): key both.
                rgb = tls.setdefault("rgb", [])
                self._put_key(rgb, time, {"color": color[:6]}, tl40.SLOT_TIMELINES["rgb"])
                alpha = tls.setdefault("alpha", [])
                self._put_key(alpha, time, {"value": _r(Color.from_hex(color).a)}, tl40.SLOT_TIMELINES["alpha"])
            else:
                self._put_key(tls.setdefault("rgba", []), time, {"color": color}, tl40.SLOT_TIMELINES["rgba"])
        elif timeline == "attachment":
            if value is not None and value not in self.attachments(slot):
                raise EditorError(f"Slot '{slot}' has no attachment '{value}'")
            self._begin(f"Key attachment {slot}", record)
            self._put_key(tls.setdefault("attachment", []), time, {"name": value})
        else:
            raise EditorError(f"Unknown slot timeline '{timeline}'")
        self.notify()

    def add_event(self, name: str, record: bool = True, **defaults: Any) -> None:
        self._check_new_name(name, set(self.data["events"]), "event")
        self._begin(f"Create event {name}", record)
        self.data["events"][name] = {k: v for k, v in defaults.items() if k in self.EVENT_FIELDS}
        self.notify()

    def key_event(self, animation: str, name: str, time: float, record: bool = True) -> None:
        anim = self.animation(animation)
        if name not in self.data["events"]:
            raise EditorError(f"No event named '{name}'")
        self._begin(f"Key event {name}", record)
        keys = anim.setdefault("events", [])
        keys.append({"time": _r(time), "name": name})
        keys.sort(key=lambda k: k.get("time", 0))
        self.notify()

    EVENT_FIELDS = {"int": int, "float": float, "string": str, "audio": str, "volume": float, "balance": float}
    EVENT_DEFAULTS = {"int": 0, "float": 0.0, "string": "", "audio": "", "volume": 1.0, "balance": 0.0}

    def set_event(self, name: str, record: bool = True, **fields: Any) -> None:
        """Change an event's default values (int, float, string, audio, volume, balance)."""
        event = self.data["events"].get(name)
        if event is None:
            raise EditorError(f"No event named '{name}'")
        self._begin(f"Edit event {name}", record)
        for field, value in fields.items():
            kind = self.EVENT_FIELDS.get(field)
            if kind is None:
                raise EditorError(f"Events have no field '{field}'")
            value = kind(value)
            if value == self.EVENT_DEFAULTS[field]:
                event.pop(field, None)
            else:
                event[field] = value
        if not event.get("audio"):
            event.pop("volume", None)
            event.pop("balance", None)
        self.notify()

    def rename_event(self, old: str, new: str, record: bool = True) -> None:
        if old not in self.data["events"]:
            raise EditorError(f"No event named '{old}'")
        if new == old:
            return
        self._check_new_name(new, set(self.data["events"]), "event")
        self._begin(f"Rename event {old}", record)
        self.data["events"] = {(new if k == old else k): v for k, v in self.data["events"].items()}
        for anim in self.data["animations"].values():
            for key in anim.get("events", []):
                if key.get("name") == old:
                    key["name"] = new
        self.notify()

    def delete_event(self, name: str, record: bool = True) -> None:
        """Delete an event and all its keys."""
        if name not in self.data["events"]:
            raise EditorError(f"No event named '{name}'")
        self._begin(f"Delete event {name}", record)
        del self.data["events"][name]
        for anim in self.data["animations"].values():
            keys = [k for k in anim.get("events", []) if k.get("name") != name]
            if keys:
                anim["events"] = keys
            else:
                anim.pop("events", None)
        self.notify()

    def event_keys(self, animation: str) -> list[dict[str, Any]]:
        return list(self.animation(animation).get("events", []))

    def set_event_key(self, animation: str, index: int, record: bool = True, **fields: Any) -> None:
        """Change one event key: time, name, or a value that overrides the event's default."""
        keys = self.animation(animation).get("events", [])
        if not 0 <= index < len(keys):
            raise EditorError("No such event key")
        key = keys[index]
        self._begin("Edit event key", record)
        for field, value in fields.items():
            if field == "time":
                key["time"] = _r(max(0.0, float(value)))
            elif field == "name":
                if value not in self.data["events"]:
                    raise EditorError(f"No event named '{value}'")
                key["name"] = value
            elif field in self.EVENT_FIELDS:
                value = self.EVENT_FIELDS[field](value)
                default = self.data["events"].get(key["name"], {}).get(field, self.EVENT_DEFAULTS[field])
                if value == default:
                    key.pop(field, None)
                else:
                    key[field] = value
            else:
                raise EditorError(f"Event keys have no field '{field}'")
        keys.sort(key=lambda k: k.get("time", 0))
        self.notify()

    def delete_event_key(self, animation: str, index: int, record: bool = True) -> None:
        anim = self.animation(animation)
        keys = anim.get("events", [])
        if not 0 <= index < len(keys):
            raise EditorError("No such event key")
        self._begin("Delete event key", record)
        del keys[index]
        if not keys:
            anim.pop("events", None)
        self.notify()

    def _timeline_keys(self, anim: dict[str, Any], ref: KeyRef) -> list[dict[str, Any]] | None:
        if ref.group in ("bones", "slots"):
            return anim.get(ref.group, {}).get(ref.owner, {}).get(ref.timeline)
        if ref.group in ("ik", "transform"):
            return anim.get(ref.group, {}).get(ref.owner)
        if ref.group == "path":
            return anim.get("path", {}).get(ref.owner, {}).get(ref.timeline)
        if ref.group == "deform":  # owner is "slot\nattachment"
            slot, att = ref.owner.split("\n")
            return anim.get("deform", {}).get(self.edit_skin, {}).get(slot, {}).get(att)
        return anim.get(ref.group)

    def _match(self, keys: list[dict[str, Any]], ref: KeyRef) -> list[dict[str, Any]]:
        return [k for k in keys if abs(k.get("time", 0) - ref.time) < KEY_EPS]

    def _prune(self, anim: dict[str, Any]) -> None:
        for group in ("bones", "slots"):
            for owner in list(anim.get(group, {})):
                tls = anim[group][owner]
                for tl in [t for t, keys in tls.items() if not keys]:
                    del tls[tl]
                if not tls:
                    del anim[group][owner]
            if group in anim and not anim[group]:
                del anim[group]
        for group in ("ik", "transform"):
            for name in [n for n, keys in anim.get(group, {}).items() if not keys]:
                del anim[group][name]
        for name in list(anim.get("path", {})):
            timelines = anim["path"][name]
            for tl in [t for t, keys in timelines.items() if not keys]:
                del timelines[tl]
            if not timelines:
                del anim["path"][name]
        for skin in list(anim.get("deform", {})):
            for slot in list(anim["deform"][skin]):
                for att in [a for a, keys in anim["deform"][skin][slot].items() if not keys]:
                    del anim["deform"][skin][slot][att]
                if not anim["deform"][skin][slot]:
                    del anim["deform"][skin][slot]
            if not anim["deform"][skin]:
                del anim["deform"][skin]
        for group in ("events", "drawOrder", "ik", "transform", "path", "deform"):
            if group in anim and not anim[group]:
                del anim[group]

    @staticmethod
    def _reader(ref: KeyRef) -> Any:
        """timelines40 reader for curve upkeep; False for timelines without curves."""
        if ref.group in ("bones", "ik", "transform", "path", "deform") or \
                (ref.group == "slots" and ref.timeline != "attachment"):
            return tl40.reader_for(ref.group, ref.timeline)
        return False

    def _snapshots(self, anim: dict[str, Any], refs: list[KeyRef]) -> list[tuple[list, Any, Any]]:
        seen, out = set(), []
        for ref in refs:
            keys = self._timeline_keys(anim, ref)
            reader = self._reader(ref)
            if keys is None or reader is False or id(keys) in seen:
                continue
            seen.add(id(keys))
            out.append((keys, reader, tl40.snapshot(keys, reader)))
        return out

    def delete_keys(self, animation: str, refs: list[KeyRef], record: bool = True) -> int:
        anim = self.animation(animation)
        self._begin("Delete keys", record)
        snaps = self._snapshots(anim, refs)
        removed = 0
        for ref in refs:
            keys = self._timeline_keys(anim, ref)
            if not keys:
                continue
            for k in self._match(keys, ref):
                keys.remove(k)
                removed += 1
        for keys, reader, snap in snaps:
            tl40.restore(keys, reader, snap)  # the key before a deleted one now spans the merged segment
        self._prune(anim)
        self.notify()
        return removed

    def move_keys(self, animation: str, refs: list[KeyRef], dt: float, record: bool = True) -> list[KeyRef]:
        """Shift keys in time; a moved key replaces any key it lands on. Returns the moved refs."""
        anim = self.animation(animation)
        if refs and min(r.time for r in refs) + dt < 0:
            dt = -min(r.time for r in refs)
        self._begin("Move keys", record)
        snaps = self._snapshots(anim, refs)
        moving: list[tuple[KeyRef, list[dict[str, Any]], dict[str, Any]]] = []
        for ref in refs:
            keys = self._timeline_keys(anim, ref)
            if keys:
                for k in self._match(keys, ref):
                    moving.append((ref, keys, k))
        for _, keys, k in moving:
            keys.remove(k)
        moved = []
        for ref, keys, k in moving:
            new_time = _r(k.get("time", 0) + dt)
            if ref.group != "events":
                keys[:] = [o for o in keys if abs(o.get("time", 0) - new_time) >= KEY_EPS]
            k["time"] = new_time
            keys.append(k)
            keys.sort(key=lambda key: key.get("time", 0))
            moved.append(KeyRef(ref.group, ref.owner, ref.timeline, new_time))
        for keys, reader, snap in snaps:
            tl40.restore(keys, reader, snap)
        self.notify()
        return moved

    def set_curve(self, animation: str, refs: list[KeyRef], curve: Any, record: bool = True) -> None:
        """curve: None (linear), "stepped", or a normalized preset (cx1, cy1, cx2, cy2) in 0..1 that is
        applied to every value channel of the segment (stored as absolute 4.0 handles)."""
        anim = self.animation(animation)
        if curve not in (None, "stepped"):
            cx1, cy1, cx2, cy2 = (float(v) for v in curve)
            if not (0 <= cx1 <= 1 and 0 <= cx2 <= 1):
                raise EditorError("Curve handle X values must be between 0 and 1")
        self._begin("Set curve", record)
        for ref in refs:
            reader = self._reader(ref)
            if reader is False:
                continue
            keys = self._timeline_keys(anim, ref) or []
            for k in self._match(keys, ref):
                k.pop("curve", None)
                if curve == "stepped":
                    k["curve"] = "stepped"
                elif curve is not None:
                    absolute = tl40.preset_curve(keys, reader, keys.index(k), tuple(curve))
                    if absolute is not None:
                        k["curve"] = absolute
        self.notify()

    # --- graph editor --------------------------------------------------------------------

    def timeline_keys(self, animation: str, row: KeyRef) -> list[dict[str, Any]]:
        """The keys of a row's timeline (do not modify; use the editing methods)."""
        return self._timeline_keys(self.animation(animation), row) or []

    @staticmethod
    def key_channels(row: KeyRef, key: dict[str, Any]) -> list[float]:
        """A key's value per channel (deform keys: the 0..1 blend start)."""
        reader = Document._reader(row)
        if reader is False:
            return []
        return [0.0] if reader is None else reader(key)

    def _one_key(self, anim: dict[str, Any], ref: KeyRef) -> tuple[list[dict[str, Any]], int]:
        keys = self._timeline_keys(anim, ref) or []
        for i, k in enumerate(keys):
            if abs(k.get("time", 0) - ref.time) < KEY_EPS:
                return keys, i
        raise EditorError("That key no longer exists")

    def set_key_channel(self, animation: str, ref: KeyRef, channel: int, value: float, record: bool = True) -> None:
        """Change one value channel of a key; the curves around it keep their shape."""
        anim = self.animation(animation)
        reader = self._reader(ref)
        if reader is False or ref.group == "deform":
            raise EditorError("These keys have no values to edit in the graph")
        keys, i = self._one_key(anim, ref)
        if not 0 <= channel < len(tl40.channel_names(ref.group, ref.timeline)):
            raise EditorError("No such value channel")
        self._begin("Edit key value", record)
        snap = tl40.snapshot(keys, reader)
        tl40.write_channel(ref.group, ref.timeline, keys[i], channel, value)
        tl40.restore(keys, reader, snap)
        self.notify()

    def set_key_handles(self, animation: str, ref: KeyRef, channel: int, handles: tuple[float, float, float, float],
                        record: bool = True) -> None:
        """Set the absolute bezier handles (cx1, cy1, cx2, cy2) of one channel of the segment that
        starts at ref. A linear or stepped segment becomes a bezier; other channels stay as they were."""
        anim = self.animation(animation)
        reader = self._reader(ref)
        if reader is False:
            raise EditorError("These keys have no curves")
        keys, i = self._one_key(anim, ref)
        if i + 1 >= len(keys):
            raise EditorError("The last key has no curve")
        names = tl40.channel_names(ref.group, ref.timeline)
        if not 0 <= channel < len(names):
            raise EditorError("No such value channel")
        key, nxt = keys[i], keys[i + 1]
        t0, t1 = key.get("time", 0), nxt.get("time", 0)
        v0s, v1s = ([0.0], [1.0]) if reader is None else (reader(key), reader(nxt))
        curve = key.get("curve")
        if not isinstance(curve, list) or len(curve) < 4 * len(names):
            curve = []
            for c in range(len(names)):  # straight line: the same motion as linear
                dv = v1s[c] - v0s[c]
                curve += [t0 + (t1 - t0) / 3, v0s[c] + dv / 3, t0 + (t1 - t0) * 2 / 3, v0s[c] + dv * 2 / 3]
        cx1, cy1, cx2, cy2 = (float(h) for h in handles)
        cx1, cx2 = min(max(cx1, t0), t1), min(max(cx2, t0), t1)
        self._begin("Edit curve", record)
        curve = list(curve)
        curve[channel * 4:channel * 4 + 4] = [cx1, cy1, cx2, cy2]
        key["curve"] = [round(v, 5) for v in curve]
        self.notify()

    def insert_key(self, animation: str, row: KeyRef, time: float, record: bool = True) -> KeyRef:
        """Add a key on a curve timeline at time without changing the motion: bezier segments are
        split (de Casteljau), linear and stepped ones keep their shape."""
        anim = self.animation(animation)
        reader = self._reader(row)
        if reader is False:
            raise EditorError("Keys on this row cannot be added from the graph")
        keys = self._timeline_keys(anim, row)
        if not keys:
            raise EditorError("The timeline has no keys")
        time = _r(time)
        if time < 0:
            raise EditorError("Key time cannot be negative")
        if any(abs(k.get("time", 0) - time) < KEY_EPS for k in keys):
            return KeyRef(row.group, row.owner, row.timeline, time)
        self._begin("Add key", record)
        i = cv.find_segment(keys, time)
        names = tl40.channel_names(row.group, row.timeline)
        if i is None or i + 1 >= len(keys):
            # Before the first or after the last key the value holds; a copy of that key keeps it
            # (the segment between the two equal keys is linear, so flat).
            new = copy.deepcopy(keys[0] if i is None else keys[-1])
            new.pop("curve", None)
            new["time"] = time
            keys.append(new)
            keys.sort(key=lambda k: k.get("time", 0))
            self.notify()
            return KeyRef(row.group, row.owner, row.timeline, time)
        key, nxt = keys[i], keys[i + 1]
        t0, t1 = key.get("time", 0), nxt.get("time", 0)
        curve = key.get("curve")
        new = {k: copy.deepcopy(v) for k, v in key.items() if k != "curve"}  # flags (bend, …) hold
        new["time"] = time
        if row.group == "deform":
            p = cv.segment_value(key, nxt, time, 0, 0.0, 1.0)
            slot, att_name = row.owner.split("\n")
            length = parse_vertices(self.attachments(slot)[att_name]).deform_length
            a, b = key_offsets(key, length), key_offsets(nxt, length)
            new.pop("vertices", None)
            new.pop("offset", None)
            verts = [_r(x + (y - x) * p) for x, y in zip(a, b)]
            if any(verts):
                new["vertices"] = verts
            if isinstance(curve, list) and 1e-6 < p < 1 - 1e-6:
                left, _, right = cv.split_bezier(t0, 0.0, tuple(curve[:4]), t1, 1.0, time)
                key["curve"] = [round(v, 5) for v in (left[0], left[1] / p, left[2], left[3] / p)]
                new["curve"] = [round(v, 5) for v in (right[0], (right[1] - p) / (1 - p),
                                                      right[2], (right[3] - p) / (1 - p))]
            elif curve == "stepped":
                new["curve"] = "stepped"
        else:
            v0s, v1s = reader(key), reader(nxt)
            values, left_curve, right_curve = [], [], []
            for c in range(len(names)):
                handles = cv.channel_handles(curve, c)
                if curve == "stepped":
                    values.append(v0s[c])
                elif handles is None:
                    values.append(v0s[c] + (time - t0) / (t1 - t0) * (v1s[c] - v0s[c]))
                    left_curve.append(None)
                    right_curve.append(None)
                else:
                    left, value, right = cv.split_bezier(t0, v0s[c], handles, t1, v1s[c], time)
                    values.append(value)
                    left_curve.append(left)
                    right_curve.append(right)
            for c, value in enumerate(values):
                tl40.write_channel(row.group, row.timeline, new, c, value)
            if curve == "stepped":
                new["curve"] = "stepped"
            elif isinstance(curve, list):
                v_new = reader(new)  # values as stored (rounded), for straight channels
                lc, rc = [], []
                for c in range(len(names)):
                    if left_curve[c] is None:
                        lc += cv.to_absolute((1 / 3, 1 / 3, 2 / 3, 2 / 3), t0, v0s[c], time, v_new[c])
                        rc += cv.to_absolute((1 / 3, 1 / 3, 2 / 3, 2 / 3), time, v_new[c], t1, v1s[c])
                    else:
                        lc += left_curve[c]
                        rc += right_curve[c]
                key["curve"] = [round(v, 5) for v in lc]
                new["curve"] = [round(v, 5) for v in rc]
        keys.insert(i + 1, new)
        self.notify()
        return KeyRef(row.group, row.owner, row.timeline, time)

    # --- copy and paste -------------------------------------------------------------------

    def copy_keys(self, animation: str, refs: list[KeyRef]) -> dict[str, Any]:
        """A clipboard of keys: times relative to the earliest, curve shapes kept relative to their
        segment so they fit wherever the keys are pasted."""
        anim = self.animation(animation)
        if not refs:
            raise EditorError("Select keys to copy")
        start = min(r.time for r in refs)
        rows: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for ref in sorted(refs, key=lambda r: r.time):
            keys = self._timeline_keys(anim, ref) or []
            reader = self._reader(ref)
            snap = tl40.snapshot(keys, reader) if reader is not False else {}
            for k in self._match(keys, ref):
                entry = {"key": copy.deepcopy(k), "shape": snap.get(id(k))}
                entry["key"]["time"] = _r(k.get("time", 0) - start)
                rows.setdefault((ref.group, ref.owner, ref.timeline), []).append(entry)
        return {"format": "anim2d-keys", "rows": [{"row": list(r), "keys": e} for r, e in rows.items()]}

    def _ensure_keys(self, anim: dict[str, Any], group: str, owner: str, timeline: str) -> list[dict[str, Any]]:
        if group == "bones":
            self.bone(owner)
            return anim.setdefault("bones", {}).setdefault(owner, {}).setdefault(timeline, [])
        if group == "slots":
            self.slot(owner)
            return anim.setdefault("slots", {}).setdefault(owner, {}).setdefault(timeline, [])
        if group == "ik":
            self.ik(owner)
            return anim.setdefault("ik", {}).setdefault(owner, [])
        if group == "transform":
            self.transform_constraint(owner)
            return anim.setdefault("transform", {}).setdefault(owner, [])
        if group == "path":
            self.path_constraint(owner)
            return anim.setdefault("path", {}).setdefault(owner, {}).setdefault(timeline, [])
        if group == "deform":
            slot, att = owner.split("\n")
            if att not in self.attachments(slot):
                raise EditorError(f"Slot '{slot}' has no attachment '{att}'")
            return anim.setdefault("deform", {}).setdefault(self.edit_skin, {}).setdefault(slot, {}).setdefault(att, [])
        if group in ("events", "drawOrder"):
            return anim.setdefault(group, [])
        raise EditorError(f"Cannot paste {group} keys")

    def paste_keys(self, animation: str, clipboard: dict[str, Any], time: float, owner: str | None = None,
                   record: bool = True) -> list[KeyRef]:
        """Paste copied keys starting at time. Keys at the same times are replaced (events are
        added). owner pastes keys copied from one bone or slot onto another of the same kind."""
        anim = self.animation(animation)
        if not isinstance(clipboard, dict) or clipboard.get("format") not in ("anim2d-keys", "rigworks-keys"):
            raise EditorError("The clipboard holds no keys")
        rows = clipboard["rows"]
        if owner is not None:
            owners = {(r["row"][0], r["row"][1]) for r in rows}
            if len(owners) != 1 or next(iter(owners))[0] not in ("bones", "slots"):
                raise EditorError("Keys from several bones or slots paste onto their own bones and slots")
        self._begin("Paste keys", record)
        pasted, skipped = [], []
        for entry in rows:
            group, row_owner, timeline = entry["row"]
            target = owner if owner is not None else row_owner
            try:
                keys = self._ensure_keys(anim, group, target, timeline)
            except EditorError:
                skipped.append(target)
                continue
            ref = KeyRef(group, target, timeline, 0)
            reader = self._reader(ref)
            snap = tl40.snapshot(keys, reader) if reader is not False else {}
            for item in entry["keys"]:
                k = copy.deepcopy(item["key"])
                k["time"] = _r(time + k.get("time", 0))
                if group != "events":
                    keys[:] = [o for o in keys if abs(o.get("time", 0) - k["time"]) >= KEY_EPS]
                keys.append(k)
                if item.get("shape") is not None:
                    snap[id(k)] = [tuple(c) if not isinstance(c, tuple) else c for c in item["shape"]]
                pasted.append(KeyRef(group, target, timeline, k["time"]))
            keys.sort(key=lambda key: key.get("time", 0))
            if reader is not False:
                tl40.restore(keys, reader, snap)
        self._prune(anim)
        if not pasted:  # nothing changed (empty containers were pruned): drop the undo step
            if record:
                self._undo.pop()
            raise EditorError("Nothing to paste here: " + ", ".join(sorted(set(skipped))) + " no longer exist")
        self.notify()
        return pasted

    def timeline_rows(self, animation: str) -> list[tuple[KeyRef, list[float]]]:
        """(row key with time=0, key times) for each timeline in display order."""
        if animation not in self.data["animations"]:
            return []
        anim = self.animation(animation)
        rows = []
        for b in self.bones:
            for tl in BONE_TIMELINE_ORDER:
                keys = anim.get("bones", {}).get(b["name"], {}).get(tl)
                if keys:
                    rows.append((KeyRef("bones", b["name"], tl, 0), [k.get("time", 0) for k in keys]))
        for s in self.slots:
            for tl in SLOT_TIMELINE_ORDER:
                keys = anim.get("slots", {}).get(s["name"], {}).get(tl)
                if keys:
                    rows.append((KeyRef("slots", s["name"], tl, 0), [k.get("time", 0) for k in keys]))
            for att, keys in anim.get("deform", {}).get(self.edit_skin, {}).get(s["name"], {}).items():
                if keys:
                    rows.append((KeyRef("deform", f"{s['name']}\n{att}", "deform", 0),
                                 [k.get("time", 0) for k in keys]))
        for c in self.iks:
            keys = anim.get("ik", {}).get(c["name"])
            if keys:
                rows.append((KeyRef("ik", c["name"], "ik", 0), [k.get("time", 0) for k in keys]))
        for c in self.transforms:
            keys = anim.get("transform", {}).get(c["name"])
            if keys:
                rows.append((KeyRef("transform", c["name"], "transform", 0), [k.get("time", 0) for k in keys]))
        for c in self.paths:
            for tl in ("position", "spacing", "mix"):
                keys = anim.get("path", {}).get(c["name"], {}).get(tl)
                if keys:
                    rows.append((KeyRef("path", c["name"], tl, 0), [k.get("time", 0) for k in keys]))
        for group in ("drawOrder", "events"):
            if anim.get(group):
                rows.append((KeyRef(group, "", group, 0), [k.get("time", 0) for k in anim[group]]))
        return rows

    def key_at(self, animation: str, ref: KeyRef) -> dict[str, Any] | None:
        anim = self.animation(animation)
        keys = self._timeline_keys(anim, ref) or []
        found = self._match(keys, ref)
        return found[0] if found else None

    # --- validation, files ----------------------------------------------------------------

    def validate(self) -> tuple[list[str], list[str]]:
        data = copy.deepcopy(self.data)
        data["skeleton"] = {"spine": STAMP_40, "fps": self.fps, "images": "./", "audio": ""}
        for anim in data["animations"].values():
            self._prune(anim)
        report = validate_output(data, self.images_dir, None, strict_one_shot=False, spine_version=None,
                                 full=True)
        errors = [e for e in report.errors if "no animations" not in e]
        # Characters normally stay visible; that check only matters for one-shot FX.
        warnings = [w for w in report.warnings if "stay visible after" not in w]
        return errors, warnings

    def export_data(self, stamp: str = STAMP_40) -> tuple[dict[str, Any], list[str]]:
        """The skeleton in the layout of the stamp's Spine version (4.x as is, 3.8 converted).
        Returns (data, notes about anything 3.8 could not express exactly)."""
        data = copy.deepcopy(self.data)
        data["skeleton"] = {"spine": STAMP_40, "fps": self.fps, "images": "./images/", "audio": ""}
        notes = []
        if data.pop("physics", None):  # 3.8/4.0 runtimes have no physics
            notes.append("Physics constraints are not exported (Spine 3.8/4.0 runtimes have none): "
                         "use Edit > Bake physics to keys")
        for anim in data["animations"].values():
            self._prune(anim)
        if stamp.startswith("4."):
            data["skeleton"]["spine"] = stamp
            return data, notes
        data, more = spine40.downgrade(data, stamp=stamp)
        return data, notes + more

    def used_images(self) -> set[str]:
        return {ops.image_path_of(key, att) for _, key, att in ops.skin_attachments(self.data)
                if att.get("type", "region") in ops.IMAGE_TYPES}

    def pack_atlas(self, name: str, settings: atlas_packer.PackSettings) -> atlas_packer.PackResult:
        """Pack every image the skins use into atlas pages (regions named by attachment path)."""
        images = {}
        for path in sorted(self.used_images()):
            source = self.image_file(path)
            if source is None:
                raise EditorError(f"Cannot pack the atlas: image {path}.png not found")
            with Image.open(source) as img:
                images[path] = img.convert("RGBA")
        try:
            return atlas_packer.pack(images, name, settings)
        except atlas_packer.PackError as exc:
            raise EditorError(f"Cannot pack the atlas: {exc}") from None

    def pack_atlases(self, name: str, settings: atlas_packer.PackSettings) -> list[tuple[str, Any]]:
        """pack_atlas for each scale in settings.scales: [(atlas name, PackResult)]."""
        images = {}
        for path in sorted(self.used_images()):
            source = self.image_file(path)
            if source is None:
                raise EditorError(f"Cannot pack the atlas: image {path}.png not found")
            with Image.open(source) as img:
                images[path] = img.convert("RGBA")
        try:
            return atlas_packer.pack_all(images, name, settings)
        except atlas_packer.PackError as exc:
            raise EditorError(f"Cannot pack the atlas: {exc}") from None

    def merge_spine_json(self, json_path: Path) -> list[str]:
        """File > Import Data: add another Spine JSON's bones, slots, skins, constraints, events and
        animations to this skeleton (what already exists is kept). Undoable. Returns notes."""
        from . import data_export
        source = Path(json_path)
        load = Document.import_spine_project if source.suffix.lower() == ".spine" else Document.import_spine_json
        other, warnings = load(source, self.images_dir)
        self.checkpoint("Import data")
        notes = data_export.merge_skeleton(self.data, other.data)
        self.dirty = True
        self.notify()
        return warnings + notes

    def export_spine_json(self, out_file: Path, stamp: str = STAMP_40, overwrite: bool = False,
                          copy_images: bool = True, binary: bool = False,
                          atlas: atlas_packer.PackSettings | None = None, nonessential: bool = True,
                          pretty: bool = True, cleanup: bool = False, javascript: bool = False) -> list[str]:
        """Write Spine JSON for the stamp's version (4.0 by default, or 3.8) plus images/ next to it.
        binary=True writes Spine 4.0 binary (.skel) instead. With atlas settings the images are packed
        into <name>.atlas and its page PNGs instead of copied. nonessential=False leaves out what the
        runtimes do not need, cleanup removes keys that change nothing, pretty indents the JSON and
        javascript writes names without quotes. Returns warnings; raises EditorError."""
        from . import data_export
        out_file = Path(out_file)
        errors, warnings = self.validate()
        if errors:
            raise EditorError("Fix these before exporting:\n" + "\n".join(errors[:10]))
        if out_file.exists() and not overwrite:
            raise EditorError(f"{out_file.name} already exists")
        packed = self.pack_atlases(out_file.stem, atlas) if atlas is not None else None
        if packed is not None and not overwrite:  # every page of every scale, before anything is written
            for name, result in packed:
                for file in result.files(out_file.parent / f"{name}{atlas.atlas_extension or '.atlas'}"):
                    if file.exists():
                        raise EditorError(f"{file.name} already exists")
        if binary and not stamp.startswith("4.0"):
            raise EditorError("Binary (.skel) export is Spine 4.0 only; choose JSON for Spine 3.8")
        data, notes = self.export_data(stamp)
        warnings = warnings + notes
        if cleanup:
            removed = data_export.clean_animations(data)
            if removed:
                warnings.append(f"Animation clean up removed {removed} key(s) that changed nothing")
        if not nonessential and not binary:
            data_export.strip_nonessential(data)
        if binary:
            try:
                payload = binary40.write_skel(data, nonessential=nonessential)
            except binary40.BinaryExportError as exc:
                raise EditorError(f"Cannot write binary data: {exc}") from None
        else:
            payload = data_export.dump(data, pretty, javascript).encode("utf-8")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_file.with_name(f".{out_file.name}.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, out_file)
        if packed is not None:
            for name, result in packed:
                result.write(out_file.parent / f"{name}{atlas.atlas_extension or '.atlas'}")
        elif copy_images:
            images_out = out_file.parent / "images"
            if self.images_dir and images_out.resolve() != self.images_dir.resolve():
                for path in sorted(self.used_images()):
                    source = self.image_file(path)
                    if source is None:
                        warnings.append(f"image {path}.png not found; not copied")
                        continue
                    target = images_out / f"{path}.png"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        return warnings

    @classmethod
    def import_spine_json(cls, json_path: Path, images_dir: Path | None = None) -> tuple["Document", list[str]]:
        json_path = Path(json_path)
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EditorError(f"Cannot read {json_path.name}: {exc}") from None
        if not isinstance(data, dict) or "skeleton" not in data or "bones" not in data:
            raise EditorError(f"{json_path.name} is not a Spine skeleton JSON file")
        return cls._from_spine_data(data, json_path, images_dir)

    @classmethod
    def import_spine_project(cls, spine_path: Path, images_dir: Path | None = None) -> tuple["Document", list[str]]:
        """A Spine editor project (.spine, Spine 3.8.x or 4.2.x). Its first skeleton is imported."""
        from ..formats.spine_project import SpineProjectError, load_spine_project
        spine_path = Path(spine_path)
        try:
            data, notes, version = load_spine_project(spine_path)
        except SpineProjectError as exc:
            raise EditorError(f"Cannot open {spine_path.name}: {exc}") from None
        if images_dir is None:
            configured = str(data.get("skeleton", {}).get("images") or "")
            for candidate in ([Path(configured)] if Path(configured).is_absolute() else
                              [spine_path.parent / configured] if configured else []) + [spine_path.parent / "images"]:
                if candidate.is_dir():
                    images_dir = candidate.resolve()
                    break
        doc, warnings = cls._from_spine_data(data, spine_path, images_dir)
        return doc, [f"Imported from a Spine {version} project."] + notes + warnings

    @classmethod
    def _from_spine_data(cls, data: dict[str, Any], source: Path,
                         images_dir: Path | None) -> tuple["Document", list[str]]:
        from ..templates.register import default_images_dir
        json_path = source
        notes = ops.normalize_layout(data)
        errors, warnings = ops.check_supported(data, None, full=True)
        warnings = notes + warnings
        errors = [e for e in errors if "no animations" not in e]
        if errors:
            raise EditorError(f"Cannot open {json_path.name}:\n" + "\n".join(errors))
        if any(e.get("audio") for e in data.get("events", {}).values() if isinstance(e, dict)):
            warnings.append("Audio events are kept and exported; Anim2D does not play their sounds.")
        images = Path(images_dir) if images_dir else default_images_dir(json_path, data)
        if images is None:
            warnings.append("Images folder not found; set it with File > Set Images Folder.")
        doc = cls(data, images)  # 3.8 data is converted to the 4.0 layout here
        return doc, warnings

    def save(self, path: Path | None = None) -> None:
        path = Path(path or self.path or "")
        if not path.name:
            raise EditorError("Choose where to save the project")
        self.save_copy(path)
        self.project_settings = self._settings_for(path)
        self.path = path
        self.dirty = False

    def _settings_for(self, path: Path) -> dict[str, Any]:
        """project_settings as saved at path: a relative audio folder is rebased so it still names the
        same folder from the new project location (Save As into another folder)."""
        settings = self.project_settings
        audio = settings.get("audio_dir")
        if not audio or Path(audio).is_absolute() or self.path is None or path.resolve().parent == self.path.resolve().parent:
            return settings
        folder = (self.path.parent / audio).resolve()
        try:
            audio = os.path.relpath(folder, path.parent).replace("\\", "/")
        except ValueError:  # different drive
            audio = folder.as_posix()
        return {**settings, "audio_dir": audio}

    def save_copy(self, path: Path) -> None:
        """Write the project to path without making it the document's file (autosave)."""
        path = Path(path)
        images = None
        if self.images_dir:
            try:
                images = os.path.relpath(self.images_dir, path.parent).replace("\\", "/")
            except ValueError:  # different drive
                images = str(self.images_dir)
        project = {"format": PROJECT_FORMAT, "format_version": PROJECT_VERSION,
                   "images_dir": images, "skeleton": self.data}
        if self.other_skeletons or self.skeleton_name != "skeleton":
            # "skeleton" stays the one being edited, so older Anim2D versions still open the file
            project["active"] = self.skeleton_name
            project["skeletons"] = {n: self.skeleton_data(n) for n in self.skeleton_names()}
        if self.project_settings:
            project["settings"] = self._settings_for(path)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(project, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Document":
        path = Path(path)
        try:
            project = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EditorError(f"Cannot open {path.name}: {exc}") from None
        if not isinstance(project, dict) or project.get("format") not in (PROJECT_FORMAT, *LEGACY_PROJECT_FORMATS):
            raise EditorError(f"{path.name} is not an Anim2D project")
        if project.get("format_version") not in (1, PROJECT_VERSION):  # 1 (3.8 layout) is upgraded on load
            raise EditorError(f"{path.name} was saved by an unsupported Anim2D version")
        if not isinstance(project.get("skeleton"), dict):
            raise EditorError(f"{path.name} is damaged: it has no skeleton data")
        images = project.get("images_dir")
        images_dir = (path.parent / images).resolve() if images and not Path(images).is_absolute() else \
            (Path(images) if images else None)
        doc = cls(project["skeleton"], images_dir, path)
        if isinstance(project.get("settings"), dict):
            doc.project_settings = project["settings"]
        skeletons = project.get("skeletons")
        if isinstance(skeletons, dict) and skeletons:
            active = project.get("active") if project.get("active") in skeletons else next(iter(skeletons))
            doc.skeleton_name = active
            for name, data in skeletons.items():
                if name != active:
                    doc.other_skeletons[name] = (cls(data).data, [], [])
            doc._skeleton_order = list(skeletons)
        return doc
