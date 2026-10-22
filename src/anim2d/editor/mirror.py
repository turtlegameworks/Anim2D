"""Pose clipboard (copy / paste / paste mirrored) and mirroring bone chains.

Mirroring a chain across a vertical line: its top bone gets the reflected transform (a negative
scale), and every bone below keeps its local transform, so the copy is an exact mirror image and
the left and right sides share the same local values. That is also why "paste mirrored" can copy
a pose straight across to the bones with the opposite side's names.
"""

from __future__ import annotations

import copy
import re

from . import affine
from .document import Document, EditorError

POSE_FIELDS = ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY")
_SIDES = [("L", "R"), ("l", "r"), ("Left", "Right"), ("left", "right"), ("LEFT", "RIGHT")]


def mirror_name(name: str) -> str | None:
    """The opposite side's name: L_Arm <-> R_Arm, arm_l <-> arm_r, LeftHand <-> RightHand, arm.L <-> arm.R."""
    for a, b in _SIDES:
        for one, other in ((a, b), (b, a)):
            if len(one) == 1:  # a single letter only counts as a separate word: L_x, x_L, x.L, x L, x-L
                pattern = rf"(?<![A-Za-z]){one}(?![A-Za-z])"
            else:
                pattern = rf"{one}"
            m = re.search(pattern, name)
            if m:
                return name[:m.start()] + other + name[m.end():]
    return None


# --- pose clipboard -----------------------------------------------------------------------------

def copy_pose(state, bones: list[str]) -> dict[str, tuple[float, ...]]:
    """The bones' current local pose (with unkeyed changes)."""
    locals_ = state.evaluator().bone_locals(state.time)
    return {b: tuple(locals_[b][:7]) for b in bones if b in locals_}


def paste_pose(state, pose: dict[str, tuple[float, ...]], mirrored: bool = False) -> list[str]:
    """Pose the bones (keys in Animate mode, the setup pose in Setup mode). mirrored: onto the
    opposite side's bones. Returns the bones changed; one undo step."""
    doc = state.doc
    targets = []
    for bone, values in pose.items():
        target = mirror_name(bone) if mirrored else bone
        if target and doc.has_bone(target):
            targets.append((target, values))
    if not targets:
        raise EditorError("No matching bones to paste onto" + (" (mirrored names like L_/R_)" if mirrored else ""))
    doc.checkpoint("Paste pose" + (" mirrored" if mirrored else ""))
    for target, values in targets:
        state.apply_bone_pose(target, record=False, **dict(zip(POSE_FIELDS, values)))
    doc.notify()
    return [t for t, _ in targets]


# --- mirroring a chain ------------------------------------------------------------------------------

def _subtree(doc: Document, bone: str) -> list[str]:
    out = [bone]
    for b in doc.bones:
        if b.get("parent") in out and b["name"] not in out:
            out.append(b["name"])
    return out


def _remap_weighted(vertices: list[float], index_map: dict[int, int]) -> list[float]:
    out, i = [], 0
    while i < len(vertices):
        count = int(vertices[i])
        out.append(count)
        i += 1
        for _ in range(count):
            out += [index_map.get(int(vertices[i]), int(vertices[i])), vertices[i + 1], vertices[i + 2], vertices[i + 3]]
            i += 4
    return out


def mirror_bones(doc: Document, bone: str, axis_x: float = 0.0, record: bool = True) -> dict[str, str]:
    """Copy a bone and everything below it (with their slots and attachments) reflected across the
    vertical line x = axis_x (world). Returns {original bone: mirrored bone}."""
    if bone == doc.root_name:
        raise EditorError("Mirror a bone below the root")
    chain = _subtree(doc, bone)
    taken = {b["name"] for b in doc.bones}
    names: dict[str, str] = {}
    for b in chain:
        wanted = mirror_name(b) or f"{b}_mirror"
        new = wanted if wanted not in taken else doc.unique_name(wanted, taken)
        names[b] = new
        taken.add(new)
    if record:
        doc.checkpoint(f"Mirror {bone}")
    world = doc.evaluator("").bone_world(0)
    top = doc.bone(bone)
    parent = top.get("parent")
    parent_world = world[parent] if parent else affine.IDENTITY
    reflect = (-1.0, 0.0, 0.0, 1.0, 2 * axis_x, 0.0)
    x, y, rotation, sx, sy = affine.decompose(affine.mul(affine.invert(parent_world), affine.mul(reflect, world[bone])))
    new_bones = []
    for b in chain:
        data = copy.deepcopy(doc.bone(b))
        data["name"] = names[b]
        if b == bone:
            data.update(x=round(x, 4), y=round(y, 4), rotation=round(rotation, 4), scaleX=round(sx, 6),
                        scaleY=round(sy, 6))
            for key in ("shearX", "shearY"):
                data.pop(key, None)
        else:
            data["parent"] = names[data["parent"]]
        new_bones.append(data)
    position = max(i for i, b in enumerate(doc.bones) if b["name"] in chain) + 1
    doc.data["bones"][position:position] = new_bones
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    index_map = {index[b]: index[names[b]] for b in chain}
    # slots of the chain, drawn right after the originals, with their attachments in every skin
    slot_names = {s["name"] for s in doc.slots}
    for slot in [s for s in doc.slots if s["bone"] in names]:
        new_slot = copy.deepcopy(slot)
        new_slot["name"] = mirror_name(slot["name"]) or f"{slot['name']}_mirror"
        if new_slot["name"] in slot_names:
            new_slot["name"] = doc.unique_name(new_slot["name"], slot_names)
        slot_names.add(new_slot["name"])
        new_slot["bone"] = names[slot["bone"]]
        doc.data["slots"].insert(doc.data["slots"].index(slot) + 1, new_slot)
        for skin in doc.data["skins"]:
            atts = skin.get("attachments", {}).get(slot["name"])
            if not atts:
                continue
            copied = copy.deepcopy(atts)
            for att in copied.values():
                if att.get("type") == "mesh" and len(att.get("vertices", [])) != len(att.get("uvs", [])):
                    att["vertices"] = _remap_weighted(att["vertices"], index_map)
            skin["attachments"][new_slot["name"]] = copied
    doc.dirty = True
    doc.notify()
    return names
