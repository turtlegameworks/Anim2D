"""Serializer for the Spine 3.8 JSON format (target editor: Spine 3.8.75 Pro).

Field names follow the official 3.8 runtime reader, spine-runtimes branch 3.8,
spine-libgdx/.../SkeletonJson.java. Notable 3.8 specifics:
  * "skins" is an array of {"name", "attachments"} (not an object keyed by skin).
  * rotate keys use "angle"; translate/scale keys use "x"/"y".
  * slot colour keys are absolute "rrggbbaa" strings.
  * bezier curves are written as "curve": cx1, "c2": cy1, "c3": cx2, "c4": cy2, in
    0..1 normalized segment space, so retiming keys does not invalidate them.
  * an attachment key with "name": null hides the slot.
"""

from __future__ import annotations

from typing import Any

from ..models.effect import Curve, EffectDocument

PROFILE_ID = "spine-3.8.75"
SPINE_VERSION = "3.8.75"
PROFILE_LABEL = "Spine 3.8.75 (Pro or Essential; regions, bones, slot colour, attachment keys)"
SUPPORTED_FEATURES = frozenset({"bones", "regions", "slot_color", "attachment_keys", "bezier_curves"})
FX_BONE_COLOR = "ff9f1aff"  # editor display colour only, never a tint


def _n(value: float) -> float | int:
    rounded = round(float(value), 4)
    if rounded == 0:
        return 0
    return int(rounded) if rounded == int(rounded) else rounded


def _curve(out: dict[str, Any], curve: Curve) -> None:
    if curve is None:
        return
    if curve == "stepped":
        out["curve"] = "stepped"
        return
    cx1, cy1, cx2, cy2 = curve
    out["curve"] = _n(cx1)
    out["c2"] = _n(cy1)
    out["c3"] = _n(cx2)
    out["c4"] = _n(cy2)


def serialize(doc: EffectDocument) -> dict[str, Any]:
    t = doc.timing.seconds

    bones = []
    for bone in doc.bones:
        entry: dict[str, Any] = {"name": bone.name}
        if bone.parent:
            entry["parent"] = bone.parent
        for key, value, default in (("length", bone.length, 0), ("rotation", bone.rotation, 0),
                                    ("x", bone.x, 0), ("y", bone.y, 0),
                                    ("scaleX", bone.scale_x, 1), ("scaleY", bone.scale_y, 1)):
            if _n(value) != default:
                entry[key] = _n(value)
        entry["color"] = FX_BONE_COLOR
        bones.append(entry)

    slots = []
    skin_attachments: dict[str, dict[str, Any]] = {}
    for slot in doc.slots:
        entry = {"name": slot.name, "bone": slot.bone, "color": slot.color.to_hex()}
        if slot.setup_attachment:
            entry["attachment"] = slot.setup_attachment
        if slot.blend != "normal":
            entry["blend"] = slot.blend
        slots.append(entry)
        per_slot: dict[str, Any] = {}
        for att in slot.attachments:
            data: dict[str, Any] = {"path": att.path}
            for key, value, default in (("x", att.x, 0), ("y", att.y, 0), ("rotation", att.rotation, 0),
                                        ("scaleX", att.scale_x, 1), ("scaleY", att.scale_y, 1)):
                if _n(value) != default:
                    data[key] = _n(value)
            data["width"] = _n(att.width)
            data["height"] = _n(att.height)
            per_slot[att.name] = data
        skin_attachments[slot.name] = per_slot

    anim_bones: dict[str, Any] = {}
    for bone_name, tl in doc.bone_timelines.items():
        out: dict[str, Any] = {}
        if tl.rotate:
            keys = []
            for k in tl.rotate:
                key = {"time": _n(t(k.u)), "angle": _n(k.angle)}
                _curve(key, k.curve)
                keys.append(key)
            out["rotate"] = keys
        for name, vector_keys in (("translate", tl.translate), ("scale", tl.scale)):
            if vector_keys:
                keys = []
                for k in vector_keys:
                    key = {"time": _n(t(k.u)), "x": _n(k.x), "y": _n(k.y)}
                    _curve(key, k.curve)
                    keys.append(key)
                out[name] = keys
        if out:
            anim_bones[bone_name] = out

    anim_slots: dict[str, Any] = {}
    for slot_name, tl in doc.slot_timelines.items():
        out = {}
        if tl.attachment:
            keys = [{"time": _n(t(k.u)), "name": k.name} for k in tl.attachment]
            # Make the hidden pre-roll explicit when the effect is delayed.
            if keys[0]["time"] > 0 and keys[0]["name"] is not None:
                keys.insert(0, {"time": 0, "name": None})
            out["attachment"] = keys
        if tl.color:
            keys = []
            for k in tl.color:
                key = {"time": _n(t(k.u)), "color": k.color.to_hex()}
                _curve(key, k.curve)
                keys.append(key)
            out["color"] = keys
        if out:
            anim_slots[slot_name] = out

    animation: dict[str, Any] = {}
    if anim_slots:
        animation["slots"] = anim_slots
    if anim_bones:
        animation["bones"] = anim_bones

    return {
        "skeleton": {
            "spine": SPINE_VERSION,
            "fps": _n(doc.timing.fps),
            "images": "./images/",
            "audio": "",
        },
        "bones": bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": skin_attachments}],
        "animations": {doc.animation_name: animation},
    }


def count_stats(data: dict[str, Any]) -> dict[str, int]:
    keys = 0
    for anim in data.get("animations", {}).values():
        for group in ("bones", "slots"):
            for timelines in anim.get(group, {}).values():
                keys += sum(len(v) for v in timelines.values())
    attachments = sum(len(a) for skin in data.get("skins", []) for a in skin.get("attachments", {}).values())
    return {
        "bones": len(data.get("bones", [])),
        "slots": len(data.get("slots", [])),
        "attachments": attachments,
        "animations": len(data.get("animations", {})),
        "keys": keys,
    }
