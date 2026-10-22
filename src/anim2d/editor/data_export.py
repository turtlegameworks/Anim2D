"""Options of Spine's data export: nonessential data, animation clean up, JSON or JavaScript text,
and merging another skeleton's data into the open one (File > Import Data)."""

from __future__ import annotations

import json
import re
from typing import Any

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def strip_nonessential(data: dict[str, Any]) -> None:
    """Remove what the runtimes do not need (Spine leaves it out when Nonessential data is off):
    the skeleton's fps/images/audio, bone and shape colours, mesh edges and sizes."""
    skeleton = data.get("skeleton", {})
    for key in ("fps", "images", "audio"):
        skeleton.pop(key, None)
    for bone in data.get("bones", []):
        bone.pop("color", None)
    for skin in data.get("skins", []):
        for entries in skin.get("attachments", {}).values():
            for att in entries.values():
                kind = att.get("type", "region")
                if kind in ("boundingbox", "path", "point", "clipping"):
                    att.pop("color", None)
                if kind in ("mesh", "linkedmesh"):
                    for key in ("edges", "width", "height"):
                        att.pop(key, None)


def _plain(curve: Any) -> bool:
    return curve is None or curve == "stepped"


def _duration(node: Any) -> float:
    """The latest key time anywhere in an animation: the runtimes take its duration from this."""
    if isinstance(node, dict):
        own = node.get("time", 0) if isinstance(node.get("time", 0), (int, float)) else 0
        return max([own, *(_duration(v) for v in node.values() if isinstance(v, (dict, list)))])
    if isinstance(node, list):
        return max((_duration(v) for v in node), default=0)
    return 0


def _clean_keys(keys: list[dict[str, Any]]) -> int:
    """Drop keys that change nothing: a key equal to both neighbours, or a last key equal to the one
    before it, when the curves around it cannot overshoot (linear or stepped). Returns the count."""
    def value(k: dict[str, Any]) -> dict[str, Any]:
        return {f: v for f, v in k.items() if f not in ("time", "curve")}
    removed = 0
    i = 1
    while i < len(keys):
        last = i == len(keys) - 1
        same_before = value(keys[i]) == value(keys[i - 1]) and _plain(keys[i - 1].get("curve"))
        if same_before and (last or value(keys[i]) == value(keys[i + 1]) and _plain(keys[i].get("curve"))):
            del keys[i]
            removed += 1
            continue
        i += 1
    return removed


def clean_animations(data: dict[str, Any]) -> int:
    """Animation clean up: remove keys that change nothing. Returns how many were removed. The
    animation keeps its duration: if only removed keys reached its end, one of them is put back."""
    removed = 0
    for anim in data.get("animations", {}).values():
        duration = _duration(anim)
        lists = [keys for group in ("bones", "slots", "path") for timelines in anim.get(group, {}).values()
                 for keys in timelines.values() if isinstance(keys, list) and keys and isinstance(keys[0], dict)]
        lists += [keys for group in ("ik", "transform") for keys in anim.get(group, {}).values()
                  if isinstance(keys, list) and keys]
        tails = [keys[-1] for keys in lists]
        for keys in lists:
            removed += _clean_keys(keys)
        if _duration(anim) < duration:
            for keys, tail in zip(lists, tails):
                if keys[-1] is not tail and tail.get("time", 0) >= duration:
                    keys.append(tail)
                    removed -= 1
                    break
    return removed


def _js(value: Any, indent: str | None, level: int) -> str:
    """JavaScript object text: like JSON but names that are identifiers are not quoted."""
    nl = "\n" + indent * (level + 1) if indent is not None else ""
    end = "\n" + indent * level if indent is not None else ""
    sep = ": " if indent is not None else ":"
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = [(k if _IDENT.fullmatch(k) else json.dumps(k)) + sep + _js(v, indent, level + 1)
                 for k, v in value.items()]
        return "{" + nl + ("," + nl).join(parts) + end + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(not isinstance(v, (dict, list)) for v in value):
            return "[" + ", ".join(_js(v, indent, level + 1) for v in value) + "]"
        return "[" + nl + ("," + nl).join(_js(v, indent, level + 1) for v in value) + end + "]"
    return json.dumps(value)


def dump(data: dict[str, Any], pretty: bool = True, javascript: bool = False) -> str:
    if javascript:
        return _js(data, "\t" if pretty else None, 0) + "\n"
    if pretty:
        return json.dumps(data, indent="\t") + "\n"
    return json.dumps(data, separators=(",", ":")) + "\n"


def _weighted(att: dict[str, Any]) -> bool:
    vertices = att.get("vertices")
    if not isinstance(vertices, list):
        return False
    if att.get("type", "region") == "mesh":
        return len(vertices) != len(att.get("uvs", []))
    if att.get("type") in ("path", "boundingbox", "clipping"):
        return len(vertices) != 2 * int(att.get("vertexCount", 0))
    return False


def remap_weights(att: dict[str, Any], source_bones: list[str], target_index: dict[str, int]) -> bool:
    """Weighted vertices name bones by their index in the skeleton's bone list. Rewrite those indices
    from the source skeleton's order to the target's, in place. False (and att unchanged) when a bone
    it is weighted to is not in the target."""
    if not _weighted(att):
        return True
    vertices = list(att["vertices"])
    i = 0
    while i < len(vertices):
        count = int(vertices[i])
        for k in range(count):
            at = i + 1 + k * 4
            index = int(vertices[at])
            name = source_bones[index] if 0 <= index < len(source_bones) else None
            if name not in target_index:
                return False
            vertices[at] = target_index[name]
        i += 1 + count * 4
    att["vertices"] = vertices
    return True


def merge_skeleton(target: dict[str, Any], source: dict[str, Any]) -> list[str]:
    """Add the source skeleton's bones, slots, constraints, skins, events and animations that the
    target does not have yet (both in the 4.0 layout). Returns notes about what was skipped."""
    notes: list[str] = []
    bones = {b["name"] for b in target["bones"]}
    for bone in source.get("bones", []):
        if bone["name"] in bones:
            continue
        if bone.get("parent") and bone["parent"] not in bones:
            notes.append(f"bone {bone['name']} skipped: its parent {bone['parent']} is missing")
            continue
        target["bones"].append(json.loads(json.dumps(bone)))
        bones.add(bone["name"])
    slots = {s["name"] for s in target["slots"]}
    for slot in source.get("slots", []):
        if slot["name"] in slots:
            continue
        if slot["bone"] not in bones:
            notes.append(f"slot {slot['name']} skipped: its bone {slot['bone']} is missing")
            continue
        target["slots"].append(json.loads(json.dumps(slot)))
        slots.add(slot["name"])
    for group in ("ik", "transform", "path"):
        have = {c["name"] for c in target.get(group, [])}
        for c in source.get(group, []):
            if c["name"] in have:
                notes.append(f"{group} constraint {c['name']} already exists; kept the open one")
                continue
            needed = list(c.get("bones", [])) + [c.get("target")]
            if any(b and b not in bones and b not in slots for b in needed):
                notes.append(f"{group} constraint {c['name']} skipped: a bone it uses is missing")
                continue
            target.setdefault(group, []).append(json.loads(json.dumps(c)))
    source_bones = [b["name"] for b in source.get("bones", [])]
    target_index = {b["name"]: i for i, b in enumerate(target["bones"])}
    skins = {s["name"]: s for s in target.setdefault("skins", [])}
    for skin in source.get("skins", []):
        mine = skins.get(skin["name"])
        if mine is None:
            mine = {"name": skin["name"], "attachments": {}}
            for key in ("bones", "ik", "transform", "path"):
                if skin.get(key):
                    mine[key] = [n for n in skin[key] if n in bones or key != "bones"]
            target["skins"].append(mine)
            skins[skin["name"]] = mine
        for slot, entries in skin.get("attachments", {}).items():
            if slot not in slots:
                continue
            place = mine.setdefault("attachments", {}).setdefault(slot, {})
            for name, att in entries.items():
                if name in place:
                    continue
                att = json.loads(json.dumps(att))
                if not remap_weights(att, source_bones, target_index):
                    notes.append(f"attachment {name} skipped: a bone it is weighted to is missing")
                    continue
                place[name] = att
    for name, event in source.get("events", {}).items():
        target.setdefault("events", {}).setdefault(name, json.loads(json.dumps(event)))
    animations = target.setdefault("animations", {})
    for name, anim in source.get("animations", {}).items():
        new = name
        n = 2
        while new in animations:
            new = f"{name} {n}"
            n += 1
        if new != name:
            notes.append(f"animation {name} imported as '{new}'")
        copy = json.loads(json.dumps(anim))
        constraints = {g: {c["name"] for c in target.get(g, [])} for g in ("ik", "transform", "path")}
        dropped = 0
        for group, known in (("bones", bones), ("slots", slots), *constraints.items()):
            for owner in [o for o in copy.get(group, {}) if o not in known]:
                del copy[group][owner]
                dropped += 1
        for skin_name in list(copy.get("deform", {})):
            for slot in [s for s in copy["deform"][skin_name] if s not in slots or skin_name not in skins]:
                del copy["deform"][skin_name][slot]
                dropped += 1
        if dropped:
            notes.append(f"animation {new}: {dropped} timeline(s) for missing parts left out")
        animations[new] = copy
    return notes
