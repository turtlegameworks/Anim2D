"""Checks a serialized Spine 3.8 document against the subset of the format this tool writes.

This is a structural check, not proof that the Spine editor accepts the file;
editor import is a separate manual verification step.

strict_one_shot=True (built-in generators) makes "slot still visible after its last key"
an error. Templates are authored by artists, so there it is only a warning.

full=True is the Anim2D editor profile: every constraint type, skins with their own bones and
constraints, linked meshes and path attachments, checked for consistent references.
"""

from __future__ import annotations

import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from ..models.settings import ValidationReport
from ..preview.mesh import parse_vertices
from ..spine_adapters.spine38 import SPINE_VERSION

HEX8 = re.compile(r"^[0-9a-f]{8}$")
HEX6 = re.compile(r"^[0-9a-f]{6}$")
BONE_TIMELINES = {"rotate": ("angle",), "translate": ("x", "y"), "scale": ("x", "y"), "shear": ("x", "y")}
SLOT_TIMELINES = {"attachment", "color", "twoColor"}
BONE_TIMELINES_40 = {"rotate": ("value",), "translate": ("x", "y"), "scale": ("x", "y"), "shear": ("x", "y"),
                     "translatex": ("value",), "translatey": ("value",), "scalex": ("value",),
                     "scaley": ("value",), "shearx": ("value",), "sheary": ("value",)}
SLOT_TIMELINES_40 = {"attachment", "rgba", "rgb", "alpha", "rgba2", "rgb2"}
# 4.0 colour key fields: name -> (field, pattern)
COLOR_FIELDS_40 = {"rgba": (("color", HEX8),), "rgb": (("color", HEX6),),
                   "rgba2": (("light", HEX8), ("dark", HEX6)), "rgb2": (("light", HEX6), ("dark", HEX6))}
ATTACHMENT_TYPES = {"region", "mesh", "boundingbox", "point", "clipping"}
ATTACHMENT_TYPES_FULL = ATTACHMENT_TYPES | {"linkedmesh", "path"}
TRANSFORM_KEY_FIELDS = ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY")
PATH_KEY_FIELDS = {"position": ("value",), "spacing": ("value",), "mix": ("mixRotate", "mixX", "mixY")}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_output(data: dict[str, Any], package_dir: Path | None, prefix: str | None,
                    end_time: float | None = None, strict_one_shot: bool = True,
                    spine_version: str | None = SPINE_VERSION, allow_ik: bool = False,
                    full: bool = False) -> ValidationReport:
    """prefix=None skips instance-prefix rules (whole skeletons from the editor);
    spine_version=None accepts any 3.8.x or 4.0.x stamp (the stamp picks the layout rules)."""
    report = ValidationReport()
    allow_ik = allow_ik or full
    err = report.errors.append
    warn = report.warnings.append

    skeleton = data.get("skeleton", {})
    stamp = str(skeleton.get("spine", ""))
    if spine_version is not None and stamp != spine_version:
        err(f"skeleton.spine must be '{spine_version}', found {skeleton.get('spine')!r}")
    elif spine_version is None and not stamp.startswith(("3.8.", "4.0.")):
        err(f"skeleton.spine must be a 3.8 or 4.0 version, found {skeleton.get('spine')!r}")
    v40 = stamp.startswith("4.")
    bone_timelines = BONE_TIMELINES_40 if v40 else BONE_TIMELINES
    slot_timelines = SLOT_TIMELINES_40 if v40 else SLOT_TIMELINES
    images_dir = skeleton.get("images", "")
    if not _relative_ok(images_dir):
        err(f"skeleton.images path '{images_dir}' must be relative and portable")
    for group in ("ik", "transform", "path"):
        if data.get(group) and not (group == "ik" and allow_ik) and not full:
            err(f"{group} constraints are not supported by this profile")

    names_seen: set[str] = set()

    def check_name(kind: str, name: Any) -> None:
        if not isinstance(name, str) or not name:
            err(f"{kind} has an empty or non-text name")
            return
        if prefix is not None and not name.startswith(prefix + "_"):
            err(f"{kind} '{name}' does not start with the instance prefix '{prefix}_'")
        key = f"{kind}:{name}"
        if key in names_seen:
            err(f"duplicate {kind} name '{name}'")
        names_seen.add(key)

    bones: set[str] = set()
    for i, bone in enumerate(data.get("bones", [])):
        check_name("bone", bone.get("name"))
        parent = bone.get("parent")
        if i == 0 and parent is not None:
            err("the first bone must be the root and have no parent")
        if i > 0 and parent not in bones:
            err(f"bone '{bone.get('name')}' has parent '{parent}' which is not defined before it")
        for key in ("x", "y", "rotation", "scaleX", "scaleY", "shearX", "shearY", "length"):
            if key in bone and not _finite(bone[key]):
                err(f"bone '{bone.get('name')}' has a non-finite {key}")
        bones.add(bone.get("name"))
    if not bones:
        err("document has no bones")

    slots: dict[str, str | None] = {}
    for slot in data.get("slots", []):
        name = slot.get("name")
        check_name("slot", name)
        if slot.get("bone") not in bones:
            err(f"slot '{name}' references missing bone '{slot.get('bone')}'")
        for key in ("color", "dark"):
            if key in slot and not HEX8.match(str(slot[key])) and not (key == "dark" and len(str(slot[key])) == 6):
                err(f"slot '{name}' {key} must be lowercase hex")
        if slot.get("blend", "normal") not in ("normal", "additive", "multiply", "screen"):
            err(f"slot '{name}' has unknown blend mode '{slot.get('blend')}'")
        slots[name] = slot.get("attachment")

    skins = data.get("skins")
    if not isinstance(skins, list) or not skins or skins[0].get("name") != "default":
        err("skins must be a list whose first entry is the 'default' skin (Spine 3.8 layout)")
        skins = []
    attachments: dict[str, set[str]] = {s: set() for s in slots}
    for skin in skins:
        for slot_name, entries in skin.get("attachments", {}).items():
            if slot_name not in slots:
                err(f"skin '{skin.get('name')}' has attachments for missing slot '{slot_name}'")
                continue
            for att_name, att in entries.items():
                # Attachment names are scoped per slot, so only the prefix is checked here.
                if prefix is not None and not str(att_name).startswith(prefix + "_"):
                    err(f"attachment '{att_name}' does not start with the instance prefix '{prefix}_'")
                attachments[slot_name].add(att_name)
                _check_attachment(report, att_name, att, slots, package_dir, images_dir, len(bones), full)
    skin_names = [s.get("name") for s in skins]
    if len(set(skin_names)) != len(skin_names):
        err("skin names must be unique")
    if full:
        by_skin = {s.get("name"): s.get("attachments", {}) for s in skins}
        for skin in skins:
            for key in ("bones", "ik", "transform", "path"):
                known = bones if key == "bones" else {c.get("name") for c in data.get(key, [])}
                for name in skin.get(key, []):
                    if name not in known:
                        err(f"skin '{skin.get('name')}' lists missing {key[:-1] if key == 'bones' else key} '{name}'")
            for slot_name, entries in skin.get("attachments", {}).items():
                for att_name, att in entries.items():
                    parent = att.get("parent")
                    if att.get("type") in ("mesh", "linkedmesh") and parent is not None:
                        source = by_skin.get(att.get("skin") or "default", {}).get(slot_name, {}).get(parent)
                        if source is None or source.get("type") != "mesh" or source.get("parent"):
                            err(f"linked mesh '{att_name}' on slot '{slot_name}' needs mesh '{parent}' "
                                f"in skin '{att.get('skin') or 'default'}'")
    default_entries = skins[0].get("attachments", {}) if skins else {}
    for slot_name, setup in slots.items():
        where = attachments if full else {k: set(v) for k, v in default_entries.items()}
        if setup is not None and setup not in where.get(slot_name, set()):
            err(f"slot '{slot_name}' setup attachment '{setup}' is not in "
                f"{'any skin' if full else 'the default skin'}")

    events = data.get("events", {})
    for event_name in events:
        check_name("event", event_name)

    iks: set[str] = set()
    if allow_ik:
        parents = {b.get("name"): b.get("parent") for b in data.get("bones", [])}
        constrained: dict[str, str] = {}
        orders = []
        for c in data.get("ik", []):
            name = c.get("name")
            check_name("constraint", name)
            iks.add(name)
            chain, target = c.get("bones", []), c.get("target")
            orders.append(c.get("order", 0))
            if len(chain) not in (1, 2) or any(b not in bones for b in chain):
                err(f"IK '{name}' must constrain 1 or 2 existing bones")
                continue
            if len(chain) == 2 and parents.get(chain[1]) != chain[0]:
                err(f"IK '{name}': '{chain[1]}' is not a direct child of '{chain[0]}'")
            if target not in bones:
                err(f"IK '{name}' targets missing bone '{target}'")
            elif target in chain:
                err(f"IK '{name}' cannot target one of its own bones")
            for b in chain:
                if b in constrained:
                    err(f"bone '{b}' is constrained by both '{constrained[b]}' and '{name}'")
                constrained[b] = name
            mix = c.get("mix", 1)
            if not _finite(mix) or not 0 <= mix <= 1:
                err(f"IK '{name}' mix must be between 0 and 1")
            if not _finite(c.get("softness", 0)) or c.get("softness", 0) < 0:
                err(f"IK '{name}' softness must be a number >= 0")
        for c in data.get("transform", []) if full else ():
            name = c.get("name")
            check_name("constraint", name)
            orders.append(c.get("order", 0))
            if not c.get("bones") or any(b not in bones for b in c.get("bones", [])):
                err(f"transform constraint '{name}' must constrain existing bones")
            if c.get("target") not in bones:
                err(f"transform constraint '{name}' targets missing bone '{c.get('target')}'")
            elif c.get("target") in c.get("bones", []):
                err(f"transform constraint '{name}' cannot target one of its own bones")
            for key in ("rotation", "x", "y", "scaleX", "scaleY", "shearY", *TRANSFORM_KEY_FIELDS):
                if key in c and not _finite(c[key]):
                    err(f"transform constraint '{name}' has a non-finite {key}")
        for c in data.get("path", []) if full else ():
            name = c.get("name")
            check_name("constraint", name)
            orders.append(c.get("order", 0))
            if not c.get("bones") or any(b not in bones for b in c.get("bones", [])):
                err(f"path constraint '{name}' must constrain existing bones")
            if c.get("target") not in slots:
                err(f"path constraint '{name}' targets missing slot '{c.get('target')}'")
            for key, values in (("positionMode", ("fixed", "percent")),
                                ("spacingMode", ("length", "fixed", "percent", "proportional")),
                                ("rotateMode", ("tangent", "chain", "chainScale"))):
                if key in c and c[key] not in values:
                    err(f"path constraint '{name}' has unknown {key} '{c[key]}'")
        if sorted(orders) != list(range(len(orders))):
            err("constraint orders must be 0..n-1 (the runtime skips other orders)")

    animations = data.get("animations", {})
    if not animations:
        err("document has no animations")
    max_time = 0.0
    still_visible: list[str] = []
    for anim_name, anim in animations.items():
        check_name("animation", anim_name)
        for group in anim:
            if group not in ("bones", "slots", "deform", "drawOrder", "events") and not (group == "ik" and allow_ik) \
                    and not (full and group in ("transform", "path")):
                err(f"animation '{anim_name}' has unsupported '{group}' timelines")
        transform_names = {c.get("name") for c in data.get("transform", [])}
        path_names = {c.get("name") for c in data.get("path", [])}
        for name, keys in anim.get("transform", {}).items() if full else ():
            if name not in transform_names:
                err(f"animation '{anim_name}' keys missing transform constraint '{name}'")
            max_time = max(max_time, _check_keys(report, f"{name}.transform", keys, TRANSFORM_KEY_FIELDS))
        for name, timelines in anim.get("path", {}).items() if full else ():
            if name not in path_names:
                err(f"animation '{anim_name}' keys missing path constraint '{name}'")
            for tl_name, keys in timelines.items():
                if tl_name not in PATH_KEY_FIELDS:
                    err(f"unsupported path timeline '{tl_name}' on '{name}'")
                    continue
                max_time = max(max_time, _check_keys(report, f"{name}.{tl_name}", keys, PATH_KEY_FIELDS[tl_name]))
        for ik_name, keys in anim.get("ik", {}).items() if allow_ik else ():
            if ik_name not in iks:
                err(f"animation '{anim_name}' keys missing IK constraint '{ik_name}'")
            max_time = max(max_time, _check_keys(report, f"{ik_name}.ik", keys, ("mix", "softness")))
            if any(not 0 <= k.get("mix", 1) <= 1 for k in keys if _finite(k.get("mix", 1))):
                err(f"{ik_name}: IK key mix must be between 0 and 1")
        for bone_name, timelines in anim.get("bones", {}).items():
            if bone_name not in bones:
                err(f"animation '{anim_name}' keys missing bone '{bone_name}'")
            for tl_name, keys in timelines.items():
                if tl_name not in bone_timelines:
                    err(f"unsupported bone timeline '{tl_name}' on '{bone_name}'")
                    continue
                max_time = max(max_time, _check_keys(report, f"{bone_name}.{tl_name}", keys,
                                                     bone_timelines[tl_name]))
        for slot_name, timelines in anim.get("slots", {}).items():
            if slot_name not in slots:
                err(f"animation '{anim_name}' keys missing slot '{slot_name}'")
                continue
            for tl_name, keys in timelines.items():
                if tl_name not in slot_timelines:
                    err(f"unsupported slot timeline '{tl_name}' on '{slot_name}'")
                    continue
                max_time = max(max_time, _check_keys(report, f"{slot_name}.{tl_name}", keys,
                                                     ("value",) if tl_name == "alpha" else ()))
                for k in keys:
                    if tl_name == "attachment" and k.get("name") is not None \
                            and k["name"] not in attachments.get(slot_name, set()):
                        err(f"{slot_name}: attachment key shows unknown attachment '{k['name']}'")
                    if tl_name == "color" and not HEX8.match(str(k.get("color"))):
                        err(f"{slot_name}: colour key must be 8 lowercase hex digits")
                    if tl_name == "twoColor" and not HEX8.match(str(k.get("light"))):
                        err(f"{slot_name}: two-colour light key must be 8 lowercase hex digits")
                    for field, pattern in COLOR_FIELDS_40.get(tl_name, ()) if v40 else ():
                        if not pattern.match(str(k.get(field))):
                            err(f"{slot_name}: {tl_name} key '{field}' must be "
                                f"{8 if pattern is HEX8 else 6} lowercase hex digits")
            att_keys = timelines.get("attachment", [])
            if att_keys and att_keys[-1].get("name") is not None:
                if strict_one_shot:
                    err(f"slot '{slot_name}' is still visible after its last key")
                else:
                    still_visible.append(slot_name)
        skins_by_name = {s.get("name"): s.get("attachments", {}) for s in skins}
        for skin_name, per_slot in anim.get("deform", {}).items():
            if skin_name not in skins_by_name:
                err(f"deform keys reference missing skin '{skin_name}'")
                continue
            for slot_name, per_att in per_slot.items():
                for att_name, keys in per_att.items():
                    if att_name not in skins_by_name[skin_name].get(slot_name, {}):
                        err(f"deform keys reference unknown attachment '{att_name}' on slot '{slot_name}'")
                        continue
                    max_time = max(max_time, _check_keys(report, f"{slot_name}.{att_name}.deform", keys, ()))
                    att = skins_by_name[skin_name].get(slot_name, {}).get(att_name, {})
                    length = mesh_deform_length(att) if att.get("type") in ("mesh", "path") \
                        and not att.get("parent") else None
                    for k in keys:
                        values = k.get("vertices", [])
                        if not all(_finite(v) for v in values):
                            err(f"{slot_name}.{att_name}: deform key values must be finite")
                        if length is not None and int(k.get("offset", 0)) + len(values) > length:
                            err(f"{slot_name}.{att_name}: deform key at {k.get('time', 0)}s is longer than the "
                                f"mesh ({length} values)")
        if anim.get("drawOrder"):
            max_time = max(max_time, _check_keys(report, "drawOrder", anim["drawOrder"], ()))
            for k in anim["drawOrder"]:
                for off in k.get("offsets", []):
                    if off.get("slot") not in slots:
                        err(f"draw order key references missing slot '{off.get('slot')}'")
        if anim.get("events"):
            max_time = max(max_time, _check_keys(report, "events", anim["events"], (), allow_same_time=True))
            for k in anim["events"]:
                if k.get("name") not in events:
                    err(f"event key references undefined event '{k.get('name')}'")
    if still_visible:
        shown = ", ".join(still_visible[:4]) + (f" and {len(still_visible) - 4} more" if len(still_visible) > 4 else "")
        warn(f"{len(still_visible)} slot(s) stay visible after the animation ends (as authored): {shown}")
    if strict_one_shot:
        for slot_name, setup in slots.items():
            if setup is not None:
                warn(f"slot '{slot_name}' is visible in the setup pose")

    if end_time is not None and abs(max_time - end_time) > 1e-3:
        err(f"last key is at {max_time:.4f}s but the effect should end at {end_time:.4f}s")
    return report


def mesh_deform_length(att: dict) -> int | None:
    try:
        return parse_vertices(att).deform_length
    except (IndexError, TypeError, ValueError):
        return None


def _check_mesh(report: ValidationReport, name: str, att: dict, bone_count: int) -> None:
    err = report.errors.append
    uvs, tris = att["uvs"], att["triangles"]
    if len(uvs) % 2 or not all(_finite(v) for v in uvs):
        err(f"mesh '{name}' uvs must be finite x, y pairs")
        return
    count = len(uvs) // 2
    try:
        mesh = parse_vertices(att)
    except (IndexError, TypeError, ValueError):
        err(f"mesh '{name}' vertices are malformed")
        return
    if mesh.count != count:
        err(f"mesh '{name}' has {mesh.count} vertices but {count} uvs")
    if mesh.weighted:
        for i, vertex in enumerate(mesh.influences):
            if any(not 0 <= inf.bone < bone_count for inf in vertex):
                err(f"mesh '{name}' vertex {i} is weighted to a bone index that does not exist")
                break
            total = sum(inf.weight for inf in vertex)
            if not _finite(total) or abs(total - 1) > 0.01:
                report.warnings.append(f"mesh '{name}' vertex {i} weights sum to {total:.3f}, not 1")
                break
    if len(tris) % 3 or any(not isinstance(t, int) or not 0 <= t < count for t in tris):
        err(f"mesh '{name}' triangles must be index triples below {count}")
    if not 0 <= att.get("hull", 0) <= count:
        err(f"mesh '{name}' hull is larger than its vertex count")


def _check_attachment(report: ValidationReport, name: str, att: dict, slots: dict, package_dir: Path | None,
                      images_dir: str, bone_count: int = 0, full: bool = False) -> None:
    err = report.errors.append
    kind = att.get("type", "region")
    if kind not in (ATTACHMENT_TYPES_FULL if full else ATTACHMENT_TYPES):
        err(f"attachment '{name}' type '{kind}' is not supported by this profile")
        return
    linked = kind == "linkedmesh" or (kind == "mesh" and "parent" in att)
    if linked and not full:
        err(f"attachment '{name}' is a linked mesh, which is not supported")
    if kind == "path":
        count = att.get("vertexCount")
        if not isinstance(count, int) or count < 2 or count % 3:
            err(f"path '{name}' vertexCount must be a multiple of 3 (2 points and 1 more per curve)")
        elif len(att.get("lengths", [])) != count // 3:
            err(f"path '{name}' needs {count // 3} curve lengths")
    if kind == "region":
        for key in ("width", "height"):
            if not _finite(att.get(key)) or att.get(key) <= 0:
                err(f"attachment '{name}' needs a positive {key}")
    for key in ("x", "y", "rotation", "scaleX", "scaleY", "width", "height"):
        if key in att and not _finite(att[key]):
            err(f"attachment '{name}' has a non-finite {key}")
    if kind == "mesh" and not linked:
        if not att.get("uvs") or not att.get("triangles") or not att.get("vertices"):
            err(f"mesh '{name}' needs uvs, triangles and vertices")
        elif "parent" not in att:
            _check_mesh(report, name, att, bone_count)
    if kind in ("boundingbox", "clipping") and not att.get("vertices"):
        err(f"{kind} '{name}' needs vertices")
    if kind == "clipping" and att.get("end") is not None and att["end"] not in slots:
        err(f"clipping '{name}' ends at missing slot '{att['end']}'")
    if kind in ("region", "mesh", "linkedmesh"):
        path = att.get("path", att.get("name", name))  # Spine: path, else name, else the skin key
        if not _relative_ok(path):
            err(f"attachment '{name}' image path '{path}' must be relative and portable")
        elif package_dir is not None:
            _check_image(report, Path(package_dir) / images_dir / f"{path}.png", att, name, kind)


def _relative_ok(path: Any) -> bool:
    if not isinstance(path, str) or not path or ":" in path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def _check_keys(report: ValidationReport, where: str, keys: Any, fields: tuple[str, ...],
                allow_same_time: bool = False) -> float:
    if not isinstance(keys, list) or not keys:
        report.errors.append(f"{where}: timeline has no keys")
        return 0.0
    previous = -1.0
    for k in keys:
        time = k.get("time", 0)
        if not _finite(time) or time < 0:
            report.errors.append(f"{where}: key time {time!r} must be a finite number >= 0")
            continue
        if time < previous:
            report.errors.append(f"{where}: key times are not in order")
        elif time == previous and not allow_same_time:
            report.errors.append(f"{where}: two keys at {time}s")
        previous = time
        for field in fields:
            if field in k and not _finite(k[field]):
                report.errors.append(f"{where}: {field} is not a finite number")
        curve = k.get("curve")
        if isinstance(curve, list):  # 4.0: absolute [cx1, cy1, cx2, cy2] per value channel
            if not curve or len(curve) % 4 or not all(_finite(v) for v in curve):
                report.errors.append(f"{where}: a 4.0 curve needs 4 finite numbers per value channel")
        elif curve is not None and curve != "stepped":
            values = [curve, k.get("c2", 0), k.get("c3", 1), k.get("c4", 1)]
            if not all(_finite(v) for v in values) or not (0 <= values[0] <= 1 and 0 <= values[2] <= 1):
                report.errors.append(f"{where}: bezier curve handles must be finite with x in 0..1")
    return max(previous, 0.0)


def _check_image(report: ValidationReport, file: Path, att: dict, att_name: str, kind: str) -> None:
    if not file.is_file():
        report.errors.append(f"attachment '{att_name}' image is missing: {file.name}")
        return
    try:
        with Image.open(file) as img:
            size, mode = img.size, img.mode
    except OSError:
        report.errors.append(f"attachment '{att_name}' image {file.name} is not a readable PNG")
        return
    if kind == "region" and size != (round(att["width"]), round(att["height"])):
        # Spine allows a region's size to differ from its image, so for artist data this is a warning.
        report.warnings.append(f"attachment '{att_name}' size {att['width']}x{att['height']} differs from "
                               f"image {size[0]}x{size[1]}")
    if mode not in ("RGBA", "LA", "P"):
        report.warnings.append(f"image {file.name} has no alpha channel")
