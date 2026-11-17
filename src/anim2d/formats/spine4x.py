"""Conversion from the Spine 4.0 JSON layout to the 4.1, 4.2 and 4.3 layouts.

Field names and semantics follow the official readers (SkeletonJson in spine-core 4.1, 4.2 and
4.3). The editor works in the 4.0 layout; everything 4.0 can express plays the same on the newer
runtimes, so the conversion is exact. What changed:

  4.1  * deform timelines move from animations[a]["deform"][skin][slot][attachment] to
         animations[a]["attachments"][skin][slot][attachment]["deform"]
       * a linked mesh's "deform" flag is called "timelines"
  4.2  * a bone's "transform" mode is called "inherit" (same values)
  4.3  * IK, transform and path constraints are one "constraints" list with a "type", in update
         order (4.0's "order" field is gone)
       * IK "uniform" is "scaleY": "uniform"
       * a transform constraint's "target" bone is its "source"; "local" is "localSource" plus
         "localTarget" and "relative" is "additive"; which properties it moves is an explicit
         "properties" map (rotate, x, y, scaleX, scaleY and shearY each driving itself)
       * transform constraint keys: a missing mixScaleY is 1, no longer mixScaleX
       * a path constraint's "target" slot is its "slot"
       * a linked mesh's "parent" is its "source"
"""

from __future__ import annotations

import copy
from typing import Any

from .spine40 import STAMP_38, STAMP_40

# Stamps of Esoteric's own example exports for each version (spine-runtimes branches 4.1 to 4.3).
STAMP_41 = "4.1.17"
STAMP_42 = "4.2.22"
STAMP_43 = "4.3.75"
STAMPS = {"4.3": STAMP_43, "4.2": STAMP_42, "4.1": STAMP_41, "4.0": STAMP_40, "3.8": STAMP_38}
RUNTIMES = list(STAMPS)  # the spine-runtimes versions Anim2D exports for, newest first

# 4.3 transform constraint property -> its mix
_MIXES = {"rotate": "mixRotate", "x": "mixX", "y": "mixY", "scaleX": "mixScaleX", "scaleY": "mixScaleY",
          "shearY": "mixShearY"}
_TYPES = ("ik", "transform", "path")  # 4.0 update order for constraints with the same "order"


def stamp_for(runtime: str) -> str:
    """The data stamp for a spine-runtimes version ("4.3" -> "4.3.75")."""
    if runtime not in STAMPS:
        raise ValueError(f"Anim2D exports for Spine {', '.join(RUNTIMES)}, not {runtime}")
    return STAMPS[runtime]


def minor(stamp: str) -> int:
    """The minor version of a 4.x stamp ("4.3.75" -> 3)."""
    parts = str(stamp).split(".")
    if len(parts) < 2 or parts[0] != "4" or not parts[1].isdigit():
        raise ValueError(f"not a Spine 4.x version: {stamp!r}")
    return int(parts[1])


def check(stamp: str) -> int:
    """The minor version of a 4.x stamp Anim2D can export; ValueError for any other."""
    target = minor(stamp)
    if target > 3:
        raise ValueError(f"Spine {stamp} is newer than Anim2D can export (4.0 to 4.3)")
    return target


def upgrade(data: dict[str, Any], stamp: str) -> dict[str, Any]:
    """A copy of 4.0 layout data in the layout of the stamp's version (4.0 to 4.3), stamped with it."""
    target = check(stamp)
    out = copy.deepcopy(data)
    out.setdefault("skeleton", {})["spine"] = stamp
    if target >= 1:
        _to_41(out)
    if target >= 2:
        _to_42(out)
    if target >= 3:
        _to_43(out)
    return out


def _attachments(data: dict[str, Any]):
    for skin in data.get("skins", []):
        for slot in skin.get("attachments", {}).values():
            yield from slot.values()


def _to_41(data: dict[str, Any]) -> None:
    for anim in data.get("animations", {}).values():
        deform = anim.pop("deform", None)
        if not deform:
            continue
        attachments = anim.setdefault("attachments", {})
        for skin, slots in deform.items():
            for slot, atts in slots.items():
                for att, keys in atts.items():
                    attachments.setdefault(skin, {}).setdefault(slot, {}).setdefault(att, {})["deform"] = keys
    for att in _attachments(data):
        if att.get("type") == "linkedmesh" and "deform" in att:
            att["timelines"] = att.pop("deform")


def _to_42(data: dict[str, Any]) -> None:
    for bone in data.get("bones", []):
        if "transform" in bone:
            bone["inherit"] = bone.pop("transform")


def _transform_properties(data: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    """The 4.3 properties map for a 4.0 transform constraint: each property whose mix is ever
    other than 0 (in the setup pose, at a key or along a key's Bezier curve) drives itself one to one."""
    return {prop: {"to": {prop: {}}} for prop, mix in _MIXES.items() if _drives(data, c, mix)}


def _to_43(data: dict[str, Any]) -> None:
    properties = {c["name"]: _transform_properties(data, c) for c in data.get("transform") or []}
    for anim in data.get("animations", {}).values():
        for timeline in anim.get("transform", {}).values():
            for key in timeline:  # 4.3 defaults a missing mixScaleY to 1, not to mixScaleX
                if "mixScaleY" not in key and "mixScaleX" in key:
                    key["mixScaleY"] = key["mixScaleX"]

    ranked = []
    for rank, kind in enumerate(_TYPES):
        for index, c in enumerate(data.pop(kind, None) or []):
            ranked.append((c.get("order", 0), rank, index, kind, c))
    constraints = []
    for _, _, _, kind, c in sorted(ranked, key=lambda r: r[:3]):
        c = dict(c)
        c.pop("order", None)
        new: dict[str, Any] = {"type": kind, "name": c.pop("name")}
        if kind == "ik":
            if c.pop("uniform", False):
                c["scaleY"] = "uniform"
        elif kind == "transform":
            new["source"] = c.pop("target")
            if c.pop("local", False):
                c["localSource"] = c["localTarget"] = True
            if c.pop("relative", False):
                c["additive"] = True
            if properties[new["name"]]:
                c["properties"] = properties[new["name"]]
        elif kind == "path":
            new["slot"] = c.pop("target")
        new.update(c)
        constraints.append(new)
    if constraints:
        data["constraints"] = constraints

    for att in _attachments(data):
        if att.get("type") == "linkedmesh" and "parent" in att:
            att["source"] = att.pop("parent")


def _mix(values: dict[str, Any], mix: str) -> float:
    """A mix in a transform constraint or key, with 4.0's defaults: 1, but mixY takes mixX's value
    and mixScaleY mixScaleX's."""
    fallback = {"mixY": "mixX", "mixScaleY": "mixScaleX"}.get(mix)
    return float(values.get(mix, values.get(fallback, 1) if fallback else 1))


def _mixes(data: dict[str, Any], c: dict[str, Any], mix: str) -> list[float]:
    """A transform constraint's setup mix and every keyed value of it."""
    return [_mix(c, mix)] + [_mix(k, mix) for anim in data.get("animations", {}).values()
                             for k in anim.get("transform", {}).get(c["name"], [])]


_CHANNELS = list(_MIXES.values())  # the order of a 4.0 transform key's curve channels


def _keys(data: dict[str, Any], c: dict[str, Any]):
    for anim in data.get("animations", {}).values():
        yield anim.get("transform", {}).get(c["name"], [])


def _handles(data: dict[str, Any], c: dict[str, Any], mix: str) -> list[float]:
    """The mix's Bezier handle values (cy1, cy2) in every key's curve: the curve can leave the
    values at its keys."""
    i = _CHANNELS.index(mix)
    out = []
    for keys in _keys(data, c):
        for key in keys:
            curve = key.get("curve")
            if isinstance(curve, list) and len(curve) >= 4 * i + 4:
                out += [float(curve[4 * i + 1]), float(curve[4 * i + 3])]
    return out


def _ever_fractional(data: dict[str, Any], c: dict[str, Any], mix: str) -> bool:
    """Whether the mix is ever other than a whole number: in the setup pose, at a key, or between
    keys (a curve between different values passes through the fractions, and a Bezier curve whose
    handles leave its keys' value does too). Whole mixes turn by whole turns, which looks the same."""
    if any(m != int(m) for m in _mixes(data, c, mix)):
        return True
    i = _CHANNELS.index(mix)
    for keys in _keys(data, c):
        values = [_mix(k, mix) for k in keys]
        for key, start, end in zip(keys, values, values[1:]):
            curve = key.get("curve")
            if curve == "stepped":
                continue
            if start != end:
                return True
            if isinstance(curve, list) and len(curve) >= 4 * i + 4 and \
                    (float(curve[4 * i + 1]) != start or float(curve[4 * i + 3]) != start):
                return True
    return False


def runtime_notes(data: dict[str, Any], stamp: str) -> list[str]:
    """Notes for 4.0 layout data exported for the stamp's version, about transform constraints that
    the 4.2 and newer runtimes apply with different math from 4.0 and 4.1 (the data is the same;
    Spine changed the runtimes):

      * When they rebuild a bone's local pose from its world transform, they read its shear with
        the opposite sign (4.0 and 4.1 had it reversed). A world (not local) transform constraint
        that shears bones puts shear in their world transform and their descendants'; it shows when
        a later constraint moves one of those bones.
      * A local, absolute (not relative) transform constraint mixes rotation and shear straight
        from one angle to the other instead of the shortest way round. That shows when its rotate
        or shear mix is ever fractional (set, keyed or between keys) and the angles are over 180
        degrees apart.
    And from 4.3:
      * A local, absolute transform constraint sets the scale it mixes; 4.0 to 4.2 divided it by
        the bone's own scale. That shows when it mixes scale (set, keyed or along a curve) into
        bones whose own scale is not 1: set up, animated, or changed by an earlier constraint.
    """
    if minor(stamp) < 2:
        return []
    version = stamp[:3]
    scaled = _scaled_bones(data)  # grows, in update order, with the bones earlier constraints scale
    transforms = {c["name"] for c in data.get("transform") or []}
    notes = []
    for c in _in_update_order(data):
        absolute_local = c["name"] in transforms and c.get("local") and not c.get("relative")
        scales = set(c.get("bones", [])) if _can_scale(data, c, c["name"] in transforms) else set()
        if not absolute_local:
            scaled |= scales
            continue
        partway = sorted({name for mix, name in (("mixRotate", "rotate"), ("mixShearY", "shear"))
                          if _ever_fractional(data, c, mix)})
        if partway:
            notes.append(f"Transform constraint '{c['name']}' mixes {' and '.join(partway)} partway: Spine {version} "
                         "runtimes mix those angles without taking the shortest way round, so when they are more "
                         "than 180 degrees apart the pose there can differ from Anim2D's.")
        if minor(stamp) >= 3 and scales and scaled & scales:
            notes.append(f"Transform constraint '{c['name']}' is local and mixes scale into bones with their own "
                         f"scale: Spine {version} runtimes set that scale, where 4.0 to 4.2 runtimes and Anim2D "
                         "divide it by the bone's own scale, so those bones are scaled differently there.")
        scaled |= scales
    return notes + _shear_notes(data, version)


def _in_update_order(data: dict[str, Any]) -> list[dict[str, Any]]:
    """IK, transform and path constraints in the order the 4.0 runtime applies them."""
    ranked = []
    for rank, kind in enumerate(_TYPES):
        for index, c in enumerate(data.get(kind) or []):
            ranked.append((c.get("order", 0), rank, index, c))
    return [c for *_, c in sorted(ranked, key=lambda r: r[:3])]


def _drives(data: dict[str, Any], c: dict[str, Any], mix: str) -> bool:
    """Whether a transform constraint's mix is ever other than 0: set up, keyed or along a curve."""
    return any(_mixes(data, c, mix)) or any(h != 0 for h in _handles(data, c, mix))


def _can_scale(data: dict[str, Any], c: dict[str, Any], transform: bool) -> bool:
    """Whether a constraint can change its bones' own scale: a transform constraint that mixes
    scale, IK that stretches or compresses, a path constraint that scales its chain."""
    if transform:
        return _drives(data, c, "mixScaleX") or _drives(data, c, "mixScaleY")
    return bool(c.get("stretch") or c.get("compress")) or c.get("rotateMode") == "chainScale"


def _scaled_bones(data: dict[str, Any]) -> set[str]:
    """Bones whose own scale is not always 1: set up, keyed or curved otherwise."""
    out = {b["name"] for b in data.get("bones", [])
           if float(b.get("scaleX", 1)) != 1 or float(b.get("scaleY", 1)) != 1}
    for anim in data.get("animations", {}).values():
        for name, timelines in anim.get("bones", {}).items():
            for kind, keys in timelines.items():
                if kind == "scale":
                    values = [float(v) for k in keys for v in (k.get("x", 1), k.get("y", 1))]
                elif kind in ("scalex", "scaley"):
                    values = [float(k.get("value", 1)) for k in keys]
                else:
                    continue
                handles = [float(h) for k in keys if isinstance(k.get("curve"), list) for h in k["curve"][1::2]]
                if any(v != 1 for v in values + handles):  # a key, or a curve between keys, leaves 1
                    out.add(name)
    return out


def _shear_notes(data: dict[str, Any], version: str) -> list[str]:
    children: dict[str, list[str]] = {}
    for bone in data.get("bones", []):
        if bone.get("parent"):
            children.setdefault(bone["parent"], []).append(bone["name"])

    def with_descendants(names: set[str]) -> set[str]:
        out, todo = set(), list(names)
        while todo:
            name = todo.pop()
            if name not in out:
                out.add(name)
                todo.extend(children.get(name, []))
        return out

    ranked = []
    for rank, kind in enumerate(_TYPES):
        for index, c in enumerate(data.get(kind) or []):
            ranked.append((c.get("order", 0), rank, index, c))
    ranked.sort(key=lambda r: r[:3])
    transforms = {c["name"] for c in data.get("transform") or []}
    notes = []
    for i, (_, _, _, c) in enumerate(ranked):
        if c["name"] not in transforms or c.get("local") or not any(_mixes(data, c, "mixShearY")):
            continue
        sheared = with_descendants(set(c.get("bones", [])))
        later = [other["name"] for _, _, _, other in ranked[i + 1:] if sheared & set(other.get("bones", []))]
        if later:
            notes.append(f"Transform constraint '{c['name']}' shears bones that {', '.join(later)} then moves: "
                         f"Spine {version} runtimes read that shear back with the opposite sign to Spine 4.0, "
                         "so the pose there can differ from Anim2D's. Set its shear mix to 0, or make it local, "
                         "to avoid this.")
    return notes
