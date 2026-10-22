"""Operations on Spine 3.8 JSON documents used by artist-authored templates.

Supported subset (everything else is rejected with an actionable message):
  * one skin, "default", without skin-specific bones or constraints
  * attachments: region, mesh (not linked), boundingbox, point, clipping
  * bone timelines: rotate, translate, scale, shear
  * slot timelines: attachment, color, twoColor
  * deform, drawOrder and event timelines; events without audio
  * no IK, transform or path constraints
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Any, Callable

from ..models.effect import Color

ATTACHMENT_TYPES = {"region", "mesh", "boundingbox", "point", "clipping"}
IMAGE_TYPES = {"region", "mesh"}
BONE_TIMELINES = {"rotate", "translate", "scale", "shear",
                  "translatex", "translatey", "scalex", "scaley", "shearx", "sheary"}  # last six: 4.0
SLOT_TIMELINES = {"attachment", "color", "twoColor", "rgba", "rgb", "alpha", "rgba2", "rgb2"}  # rgba..: 4.0
ANIMATION_GROUPS = {"bones", "slots", "deform", "drawOrder", "draworder", "events"}
CONSTRAINT_GROUPS = ("ik", "transform", "path")

Rename = Callable[[str], str]


def skin_attachments(data: dict[str, Any]):
    """Yield (slot_name, attachment_key, attachment_dict) for every skin entry."""
    for skin in data.get("skins", []):
        for slot_name, entries in skin.get("attachments", {}).items():
            for key, att in entries.items():
                yield slot_name, key, att


def image_path_of(key: str, att: dict[str, Any]) -> str:
    """Spine 3.8 rule: path defaults to the attachment's name, which defaults to its skin key."""
    return att.get("path", att.get("name", key))


def normalize_layout(data: dict[str, Any]) -> list[str]:
    """In place: accept JSON that Spine itself imports but that is not a 3.8 export, notably the
    output of Spine's Photoshop-to-Spine script (no version stamp, skins as an object keyed by
    skin name, absolute image folder from the machine that ran the script). Returns notes."""
    notes: list[str] = []
    skeleton = data.setdefault("skeleton", {})
    if not isinstance(skeleton, dict):
        return notes
    skins = data.get("skins")
    if isinstance(skins, dict):  # pre-3.8 / Photoshop script layout
        data["skins"] = [{"name": name, "attachments": attachments}
                         for name, attachments in sorted(skins.items(), key=lambda kv: kv[0] != "default")]
    if not str(skeleton.get("spine", "")).strip():
        for anim in data.get("animations", {}).values():
            for keys in _iter_key_lists(anim) if isinstance(anim, dict) else ():
                for k in keys:
                    curve = k.get("curve")
                    if isinstance(curve, list) and len(curve) == 4:  # older array form of a bezier
                        k["curve"], k["c2"], k["c3"], k["c4"] = curve
        skeleton["spine"] = "3.8.99"
        notes.append("The file has no Spine version (for example a Photoshop-to-Spine export); "
                     "it was read as Spine 3.8 data.")
    images = skeleton.get("images")
    if isinstance(images, str) and (":" in images or images.startswith(("/", "\\"))):
        notes.append(f"The file's image folder '{images}' is an absolute path from another computer; "
                     f"the 'images' folder next to the file is used instead.")
    return notes


def check_supported(data: dict[str, Any], animation: str | None,
                    allow_ik: bool = False, full: bool = False) -> tuple[list[str], list[str]]:
    """allow_ik=True accepts IK constraints. full=True is the Anim2D editor, which reads everything
    Spine 4.0 data holds: only the version and the presence of bones are checked."""
    errors: list[str] = []
    warnings: list[str] = []
    allowed_constraints = ("ik",) if allow_ik else ()
    version = str(data.get("skeleton", {}).get("spine", ""))
    if not version.startswith(("3.8.", "4.0.")):
        errors.append(f"This file is Spine {version} data; Anim2D reads Spine 3.8 and 4.0 JSON. Open it in Spine "
                      f"and export JSON for version 4.0 or 3.8." if version else
                      "This file has no Spine version. Export it from Spine as JSON (version 4.0 or 3.8).")
        return errors, warnings
    if not data.get("bones"):
        errors.append("The skeleton has no bones.")
    if full:
        if isinstance(data.get("skins"), dict):
            errors.append("Skins are stored in the pre-3.8 layout; export from Spine 3.8 or 4.0.")
        return errors, warnings
    for group in CONSTRAINT_GROUPS:
        if data.get(group) and group not in allowed_constraints:
            errors.append(f"{group.upper() if group == 'ik' else group.capitalize()} constraints are not supported "
                          f"in templates yet. Remove them in Spine and export again.")
    skins = data.get("skins", [])
    if isinstance(skins, dict):
        errors.append("Skins are stored in the pre-3.8 layout; export from Spine 3.8.")
        skins = []
    names = [s.get("name") for s in skins]
    if any(n != "default" for n in names):
        errors.append(f"Only the default skin is supported; found skins: {', '.join(map(str, names))}.")
    for skin in skins:
        for key in ("bones", "ik", "transform", "path"):
            if skin.get(key):
                errors.append(f"Skin '{skin.get('name')}' has skin-specific {key}; this is not supported.")
    for c in data.get("ik", []) if allow_ik else ():
        if c.get("skin"):
            errors.append(f"IK constraint '{c.get('name')}' is skin-specific; this is not supported.")
    for slot_name, key, att in skin_attachments(data):
        kind = att.get("type", "region")
        if kind == "linkedmesh":
            errors.append(f"Linked mesh '{key}' on slot '{slot_name}' is not supported; convert it to a normal mesh.")
        elif kind not in ATTACHMENT_TYPES:
            errors.append(f"{kind} attachment '{key}' on slot '{slot_name}' is not supported.")
        elif kind == "mesh" and "parent" in att:
            errors.append(f"Linked mesh '{key}' on slot '{slot_name}' is not supported; convert it to a normal mesh.")
    for name, event in data.get("events", {}).items():
        if isinstance(event, dict) and event.get("audio"):
            errors.append(f"Event '{name}' plays audio; audio events are not supported in FX templates.")
    if data.get("skeleton", {}).get("audio"):
        warnings.append("The skeleton's audio folder setting is ignored.")

    animations = data.get("animations", {})
    if not animations:
        errors.append("The skeleton has no animations. Create the FX animation in Spine and export again.")
    elif animation is not None:
        anim = animations.get(animation)
        if anim is None:
            errors.append(f"Animation '{animation}' does not exist.")
        else:
            for group in anim:
                if group in CONSTRAINT_GROUPS and anim[group] and group not in allowed_constraints:
                    errors.append(f"Animation '{animation}' keys {group} constraints, which are not supported.")
                elif group not in ANIMATION_GROUPS and group not in CONSTRAINT_GROUPS:
                    errors.append(f"Animation '{animation}' has unsupported '{group}' timelines.")
            for bone, tls in anim.get("bones", {}).items():
                for tl in tls:
                    if tl not in BONE_TIMELINES:
                        errors.append(f"Bone '{bone}' has an unsupported '{tl}' timeline.")
            for slot, tls in anim.get("slots", {}).items():
                for tl in tls:
                    if tl not in SLOT_TIMELINES:
                        errors.append(f"Slot '{slot}' has an unsupported '{tl}' timeline.")
            for skin in anim.get("deform", {}):
                if skin != "default":
                    errors.append(f"Deform keys for skin '{skin}' are not supported.")
            if animation_duration(anim) <= 0:
                errors.append(f"Animation '{animation}' has no keys after time 0, so it cannot be retimed.")
    return errors, warnings


def _iter_key_lists(anim: dict[str, Any]):
    for group in ("bones", "slots"):
        for timelines in anim.get(group, {}).values():
            yield from timelines.values()
    for skin in anim.get("deform", {}).values():
        for slot in skin.values():
            yield from slot.values()
    for group in ("ik", "transform"):
        yield from anim.get(group, {}).values()
    for timelines in anim.get("path", {}).values():
        yield from timelines.values()
    for group in ("drawOrder", "draworder", "events"):
        if anim.get(group):
            yield anim[group]


def animation_duration(anim: dict[str, Any]) -> float:
    return max((k.get("time", 0) for keys in _iter_key_lists(anim) for k in keys), default=0.0)


def keep_only_animation(data: dict[str, Any], animation: str) -> dict[str, Any]:
    out = copy.deepcopy(data)
    out["animations"] = {animation: out["animations"][animation]}
    if "draworder" in out["animations"][animation]:
        out["animations"][animation]["drawOrder"] = out["animations"][animation].pop("draworder")
    return out


def rename_document(data: dict[str, Any], rename: Rename, image_path: Callable[[str], str]) -> dict[str, Any]:
    """Return a copy with every bone/slot/attachment/event/animation name and reference renamed,
    and every region/mesh image path rewritten. Attachment "name" fields are folded into "path"."""
    src = data
    out: dict[str, Any] = {k: copy.deepcopy(v) for k, v in src.items()
                           if k not in ("bones", "slots", "skins", "events", "animations")}
    out["bones"] = []
    for bone in src.get("bones", []):
        b = copy.deepcopy(bone)
        b["name"] = rename(bone["name"])
        if "parent" in bone:
            b["parent"] = rename(bone["parent"])
        out["bones"].append(b)
    out["slots"] = []
    for slot in src.get("slots", []):
        s = copy.deepcopy(slot)
        s["name"] = rename(slot["name"])
        s["bone"] = rename(slot["bone"])
        if slot.get("attachment") is not None:
            s["attachment"] = rename(slot["attachment"])
        out["slots"].append(s)
    out["skins"] = []
    for skin in src.get("skins", []):
        new_skin = {k: copy.deepcopy(v) for k, v in skin.items() if k != "attachments"}
        attachments: dict[str, Any] = {}
        for slot_name, entries in skin.get("attachments", {}).items():
            renamed: dict[str, Any] = {}
            for key, att in entries.items():
                a = copy.deepcopy(att)
                kind = a.get("type", "region")
                path = image_path_of(key, att)
                a.pop("name", None)
                if kind in IMAGE_TYPES:
                    a["path"] = image_path(path)
                else:
                    a.pop("path", None)
                if kind == "clipping" and "end" in a:
                    a["end"] = rename(a["end"])
                renamed[rename(key)] = a
            attachments[rename(slot_name)] = renamed
        new_skin["attachments"] = attachments
        out["skins"].append(new_skin)
    if "events" in src:
        out["events"] = {rename(name): copy.deepcopy(ev) for name, ev in src["events"].items()}
    out["animations"] = {}
    for anim_name, anim in src.get("animations", {}).items():
        a: dict[str, Any] = {}
        if "bones" in anim:
            a["bones"] = {rename(b): copy.deepcopy(t) for b, t in anim["bones"].items()}
        if "slots" in anim:
            a["slots"] = {}
            for slot_name, tls in anim["slots"].items():
                tls = copy.deepcopy(tls)
                for k in tls.get("attachment", []):
                    if k.get("name") is not None:
                        k["name"] = rename(k["name"])
                a["slots"][rename(slot_name)] = tls
        if "deform" in anim:
            a["deform"] = {skin: {rename(slot): {rename(att): copy.deepcopy(keys) for att, keys in atts.items()}
                                  for slot, atts in slots.items()}
                           for skin, slots in anim["deform"].items()}
        for group in ("drawOrder", "draworder"):
            if group in anim:
                keys = copy.deepcopy(anim[group])
                for k in keys:
                    for off in k.get("offsets", []):
                        off["slot"] = rename(off["slot"])
                a["drawOrder"] = keys
        if "events" in anim:
            keys = copy.deepcopy(anim["events"])
            for k in keys:
                k["name"] = rename(k["name"])
            a["events"] = keys
        for group, value in anim.items():
            if group not in a and group not in ("draworder",):
                a[group] = copy.deepcopy(value)
        out["animations"][rename(anim_name)] = a
    return out


def all_names(data: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "bone": [b["name"] for b in data.get("bones", [])],
        "slot": [s["name"] for s in data.get("slots", [])],
        "event": list(data.get("events", {})),
        "animation": list(data.get("animations", {})),
    }


def retime(data: dict[str, Any], source_duration: float, duration: float, delay: float) -> None:
    """In place: t -> delay + t / source_duration * duration on every timeline of every animation.
    3.8 bezier handles are normalized per segment, so they stay valid."""
    factor = duration / source_duration
    for anim in data.get("animations", {}).values():
        for keys in _iter_key_lists(anim):
            for k in keys:
                k["time"] = round(delay + k.get("time", 0) * factor, 5)


def add_hidden_preroll(data: dict[str, Any], delay: float) -> None:
    """In place, after retiming: keep every slot hidden from 0 until the delay ends."""
    if delay <= 0:
        return
    for anim in data.get("animations", {}).values():
        slot_tls = anim.setdefault("slots", {})
        for slot in data.get("slots", []):
            keys = slot_tls.get(slot["name"], {}).get("attachment", [])
            visible_in_setup = slot.get("attachment") is not None
            if not keys and not visible_in_setup:
                continue  # never shows anything
            new_keys = [{"time": 0, "name": None}]
            first_time = keys[0].get("time", 0) if keys else None
            if visible_in_setup and (first_time is None or first_time > delay + 1e-6):
                # Before its first key the slot showed its setup attachment; restore that at activation.
                new_keys.append({"time": round(delay, 5), "name": slot["attachment"]})
            slot_tls.setdefault(slot["name"], {})["attachment"] = new_keys + keys


def slot_rgb_sets(data: dict[str, Any], animation: str) -> dict[str, set[str]]:
    """Distinct RGB hex values each slot shows (setup colour plus colour/twoColor keys)."""
    anim = data["animations"][animation]
    result: dict[str, set[str]] = {}
    for slot in data.get("slots", []):
        colors = {str(slot.get("color", "ffffffff"))[:6].lower()}
        tl = anim.get("slots", {}).get(slot["name"], {})
        colors.update(str(k["color"])[:6].lower() for k in tl.get("color", []))
        colors.update(str(k["light"])[:6].lower() for k in tl.get("twoColor", []))
        result[slot["name"]] = colors
    return result


def detect_tint_roles(data: dict[str, Any], animation: str) -> tuple[dict[str, str], str, str]:
    """Guess which slots follow the primary/secondary colour controls.

    Slots that show a single RGB colour are grouped by that colour; the most common colour
    becomes primary and the next secondary (ties broken by slot order). Everything else keeps
    its authored colours. Returns (roles by slot name, primary rgb, secondary rgb)."""
    sets = slot_rgb_sets(data, animation)
    single = [(name, next(iter(c))) for name, c in sets.items() if len(c) == 1]
    counts = Counter(c for _, c in single)
    first_seen = {}
    for i, (_, c) in enumerate(single):
        first_seen.setdefault(c, i)
    ranked = sorted(counts, key=lambda c: (-counts[c], first_seen[c]))
    primary = ranked[0] if ranked else "ffffff"
    secondary = ranked[1] if len(ranked) > 1 else primary
    roles = {name: "keep" for name in sets}
    for name, c in single:
        if c == primary:
            roles[name] = "primary"
        elif c == secondary and secondary != primary:
            roles[name] = "secondary"
    return roles, primary, secondary


def apply_colors(data: dict[str, Any], roles: dict[str, str], primary: Color, secondary: Color,
                 opacity: float) -> None:
    """In place: replace RGB of tinted slots and multiply every alpha by opacity."""
    def recolor(hex8: str, role: str) -> str:
        c = Color.from_hex(hex8)
        if role == "primary":
            c = Color(primary.r, primary.g, primary.b, c.a)
        elif role == "secondary":
            c = Color(secondary.r, secondary.g, secondary.b, c.a)
        return c.with_alpha(c.a * opacity).to_hex()

    anims = list(data.get("animations", {}).values())
    for slot in data.get("slots", []):
        role = roles.get(slot["name"], "keep")
        slot["color"] = recolor(slot.get("color", "ffffffff"), role)
        for anim in anims:
            tl = anim.get("slots", {}).get(slot["name"], {})
            for k in tl.get("color", []):
                k["color"] = recolor(k["color"], role)
            for k in tl.get("twoColor", []):
                k["light"] = recolor(k["light"], role)


SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_\-]+")


def sanitize_image_path(path: str) -> str:
    parts = [SAFE_SEGMENT.sub("_", p).strip("_") or "img" for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    parts = [p if p[0].isalnum() else f"i{p}" for p in parts if p != ".."]
    return "/".join(parts) or "img"
