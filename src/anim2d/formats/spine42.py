"""Conversion from the Spine 4.0 JSON layout (what the editor keeps) to the 4.2 and 4.3 layouts.

Field names follow the official readers (spine-runtimes SkeletonJson.java, branches 4.0, 4.2 and
4.3); the layouts were checked against Esoteric's own exports of the spineboy example.

4.0 -> 4.2 (4.1 changes included):
  * bones: "transform" (the inheritance mode) is renamed "inherit", same values.
  * linked meshes: "deform" (inherit the parent's deform keys) is renamed "timelines".
  * animations: "deform": {skin: {slot: {attachment: [keys]}}} becomes
    "attachments": {skin: {slot: {attachment: {"deform": [keys]}}}}.
4.2 -> 4.3:
  * the "ik", "transform" and "path" arrays become one "constraints" array, in the order the
    constraints apply (4.2's "order"), each with a "type".
  * transform constraints: "target" is "source"; "local" is "localSource" + "localTarget";
    "relative" is "additive"; the mixes act through "properties" (each source property mapped to
    the same property of the constrained bones, as Spine converts 4.2 data).
  * path constraints: "target" (the path slot) is "slot".
  * IK: "uniform": true is "scaleY": "uniform".
  * linked meshes: "parent" is "source".
  * defaults that changed (path mixY, transform key mixScaleY) are written out.
Data written for 4.3 also loads in 4.4 runtimes (4.4 only adds optional groups).
"""

from __future__ import annotations

import copy
from typing import Any

STAMP_42 = "4.2.22"  # the stamps on Esoteric's example exports; runtimes read only "major.minor"
STAMP_43 = "4.3.75"
TRANSFORM_PROPERTIES = ("rotate", "x", "y", "scaleX", "scaleY", "shearY")
_MAX_100 = {"rotate", "x", "y", "shearY"}  # what Spine writes for a 4.2 constraint (used only when clamped)


def version_of(stamp: str) -> tuple[int, int]:
    parts = str(stamp).split(".")
    try:
        return int(parts[0]), int(parts[1].split("-")[0]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


def _skin_attachments(data: dict[str, Any]):
    for skin in data.get("skins", []):
        for atts in skin.get("attachments", {}).values():
            yield from atts.values()


def upgrade(data: dict[str, Any], stamp: str = STAMP_42) -> tuple[dict[str, Any], list[str]]:
    """A copy of 4.0-layout data in the layout of `stamp` (4.1/4.2, or 4.3 and later). Returns
    (data, notes)."""
    out = copy.deepcopy(data)
    out.setdefault("skeleton", {})["spine"] = stamp
    _to_42(out)
    if version_of(stamp) >= (4, 3):
        _to_43(out)
    return out, []


def _to_42(d: dict[str, Any]) -> None:
    for bone in d.get("bones", []):
        if "transform" in bone:
            mode = bone.pop("transform")
            if mode != "normal":
                bone["inherit"] = mode
    for att in _skin_attachments(d):
        if att.get("type") == "linkedmesh" and "deform" in att:
            inherit = att.pop("deform")
            if not inherit:
                att["timelines"] = False
    for anim in d.get("animations", {}).values():
        deform = anim.pop("deform", None)
        if not deform:
            continue
        attachments = anim.setdefault("attachments", {})
        for skin, slots in deform.items():
            for slot, atts in slots.items():
                for name, keys in atts.items():
                    attachments.setdefault(skin, {}).setdefault(slot, {}).setdefault(name, {})["deform"] = keys


def _to_43(d: dict[str, Any]) -> None:
    ordered: list[tuple[int, int, dict[str, Any]]] = []
    for group_index, group in enumerate(("ik", "transform", "path")):
        for c in d.pop(group, []) or []:
            ordered.append((int(c.get("order", 0)), group_index, {"type": group, **c}))
    ordered.sort(key=lambda item: (item[0], item[1]))  # stable: ties keep their listing order
    constraints = []
    for _, _, c in ordered:
        c.pop("order", None)
        kind = c["type"]
        if kind == "ik":
            if c.pop("uniform", False):
                c["scaleY"] = "uniform"
        elif kind == "transform":
            c = _transform_43(c)
        elif kind == "path":
            c["slot"] = c.pop("target")
            c["mixY"] = c.get("mixY", 1)  # 4.2 default 1; 4.3 defaults to mixX
        constraints.append(c)
    if constraints:
        out = {}
        for key, value in d.items():  # keep the usual key order: constraints after slots
            out[key] = value
            if key == "slots":
                out["constraints"] = constraints
        if "constraints" not in out:
            out["constraints"] = constraints
        d.clear()
        d.update(out)
    for att in _skin_attachments(d):
        if att.get("type") == "linkedmesh" and "parent" in att:
            att["source"] = att.pop("parent")
    for anim in d.get("animations", {}).values():
        for keys in anim.get("transform", {}).values():
            for key in keys:  # 4.2 defaults mixY to mixX and mixScaleY to mixScaleX; 4.3 to 1
                key.setdefault("mixY", key.get("mixX", 1))
                key.setdefault("mixScaleY", key.get("mixScaleX", 1))


def _transform_43(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "transform", "name": c["name"]}
    if c.get("skin"):
        out["skin"] = True
    out["source"] = c["target"]
    out["bones"] = list(c.get("bones", []))
    if c.get("local"):
        out["localSource"] = True
        out["localTarget"] = True
    if c.get("relative"):
        out["additive"] = True
    for key in ("rotation", "x", "y", "scaleX", "scaleY", "shearY"):  # offsets
        if c.get(key):
            out[key] = c[key]
    out["properties"] = {p: {"to": {p: {"max": 100} if p in _MAX_100 else {}}} for p in TRANSFORM_PROPERTIES}
    mix_x = c.get("mixX", 1)
    mix_scale_x = c.get("mixScaleX", 1)
    out["mixRotate"] = c.get("mixRotate", 1)
    out["mixX"] = mix_x
    out["mixY"] = c.get("mixY", mix_x)
    out["mixScaleX"] = mix_scale_x
    out["mixScaleY"] = c.get("mixScaleY", mix_scale_x)
    out["mixShearY"] = c.get("mixShearY", 1)
    return out
