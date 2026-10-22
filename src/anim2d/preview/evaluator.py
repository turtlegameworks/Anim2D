"""Pose evaluator for Spine JSON data (4.0 rules; 3.8 data is converted on load).

It evaluates the serialized JSON (the same data that is packaged or exported), so the
preview always reflects the current settings. Bone keys (including 4.0 split X/Y timelines),
IK, transform and path constraints (solver.py), shear, slot colour/attachment keys, draw-order
keys, per-channel bezier curves (formats/curves.py), meshes with weights and deform keys
(mesh.py) and skins follow the official 4.0 runtime and are compared with the official runtimes
in the tests. Drawing is approximate: blend modes and clipping are not reproduced.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Any

from ..formats import curves as cv
from ..formats import spine40
from ..formats import timelines40 as tl40
from ..models.effect import Color
from ..templates.spine38_ops import animation_duration
from .mesh import key_offsets, parse_vertices, world_vertices
from .solver import SkeletonSolver

Affine = tuple[float, float, float, float, float, float]  # a, b, c, d, tx, ty: x' = a*x + c*y + tx


@dataclass(frozen=True)
class MeshGeometry:
    vertices: tuple[tuple[float, float], ...]  # world space
    uvs: tuple[tuple[float, float], ...]  # 0..1 over the image, v down
    triangles: tuple[tuple[int, int, int], ...]
    hull: int


@dataclass(frozen=True)
class DrawItem:
    slot: str
    image_path: str  # attachment path relative to the images folder, no extension
    width: float
    height: float
    transform: Affine  # region: maps image pixels (y down, origin top-left) to world (y up)
    color: tuple[float, float, float, float]
    blend: str
    mesh: MeshGeometry | None = None
    attachment: str = ""
    clip: tuple[tuple[float, float], ...] | None = None  # world polygon of an active clipping attachment

    def points(self) -> list[tuple[float, float]]:
        """World-space outline points (mesh vertices, or the region's corners)."""
        if self.mesh is not None:
            return list(self.mesh.vertices)
        a, b, c, d, tx, ty = self.transform
        return [(a * x + c * y + tx, b * x + d * y + ty)
                for x, y in ((0, 0), (self.width, 0), (self.width, self.height), (0, self.height))]


@dataclass(frozen=True)
class ShapeItem:
    slot: str
    attachment: str
    kind: str  # "boundingbox", "clipping", "path" or "point"
    points: tuple[tuple[float, float], ...]  # world vertices (path: in, point, out per point; point: pos, tip)
    closed: bool = False  # paths
    end: str | None = None  # clipping end slot


def _mul(p: Affine, q: Affine) -> Affine:
    a, b, c, d, tx, ty = p
    a2, b2, c2, d2, tx2, ty2 = q
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
            a * tx2 + c * ty2 + tx, b * tx2 + d * ty2 + ty)


def _local(x: float, y: float, rotation: float, sx: float, sy: float) -> Affine:
    r = math.radians(rotation)
    cos, sin = math.cos(r), math.sin(r)
    return (cos * sx, sin * sx, -sin * sy, cos * sy, x, y)


def _bezier_points(cx1: float, cy1: float, cx2: float, cy2: float) -> list[tuple[float, float]]:
    """Same 9-point forward-differencing table the Spine 3.8 runtimes build in CurveTimeline.setCurve."""
    tmpx, tmpy = (-cx1 * 2 + cx2) * 0.03, (-cy1 * 2 + cy2) * 0.03
    dddfx, dddfy = ((cx1 - cx2) * 3 + 1) * 0.006, ((cy1 - cy2) * 3 + 1) * 0.006
    ddfx, ddfy = tmpx * 2 + dddfx, tmpy * 2 + dddfy
    dfx, dfy = cx1 * 0.3 + tmpx + dddfx * 0.16666667, cy1 * 0.3 + tmpy + dddfy * 0.16666667
    x, y = dfx, dfy
    points = []
    for _ in range(9):
        points.append((x, y))
        dfx += ddfx
        dfy += ddfy
        ddfx += dddfx
        ddfy += dddfy
        x += dfx
        y += dfy
    return points


def _bezier(percent: float, cx1: float, cy1: float, cx2: float, cy2: float) -> float:
    """Piecewise-linear lookup matching the 3.8 runtimes' CurveTimeline.getCurvePercent
    (kept for checking that converted 4.0 curves sample identically)."""
    percent = min(1.0, max(0.0, percent))
    points = _bezier_points(cx1, cy1, cx2, cy2)
    prev_x, prev_y = 0.0, 0.0
    for x, y in points:
        if x >= percent:
            return prev_y + (y - prev_y) * (percent - prev_x) / (x - prev_x)
        prev_x, prev_y = x, y
    return prev_y + (1 - prev_y) * (percent - prev_x) / (1 - prev_x)


def _read_xy(default: float):
    return lambda k: [float(k.get("x", default)), float(k.get("y", default))]


def _read_value(default: float):
    return lambda k: [float(k.get("value", default))]


def _rgba_of(hex_value: str) -> list[float]:
    c = Color.from_hex(hex_value if len(hex_value) == 8 else hex_value + "ff")
    return [c.r, c.g, c.b, c.a]


def channels_at(keys: list[dict], time: float, read, count: int) -> list[float] | None:
    """4.0 timeline values at time (runtime rules), or None before the first key."""
    i = cv.find_segment(keys, time)
    if i is None:
        return None
    nxt = keys[i + 1] if i + 1 < len(keys) else None
    v0 = read(keys[i])
    v1 = read(nxt) if nxt is not None else v0
    return [cv.segment_value(keys[i], nxt, time, c, v0[c], v1[c]) for c in range(count)]


def key_at_or_before(keys: list[dict], time: float) -> dict | None:
    i = cv.find_segment(keys, time)
    return keys[i] if i is not None else None


class PoseEvaluator:
    def __init__(self, data: dict[str, Any], animation: str | None = None, skin: str | None = None):
        # Everything is evaluated with the 4.0 rules; 3.8 data (FX packages, older files) is converted first.
        if not spine40.is_40(data):
            data = spine40.upgrade(data)
        self.data = data
        self.overrides: dict[str, dict[int, float]] = {}  # bone -> {local index: value}, not keyed
        animations = data.get("animations", {})
        # animation="" evaluates the setup pose only; None picks the first animation.
        self.animation_name = animation if animation is not None else next(iter(animations), None)
        self.animation = animations.get(self.animation_name, {}) if self.animation_name else {}
        self.duration = animation_duration(self.animation)
        # Attachments are looked up in the active skin first, then in the default skin (Skeleton.getAttachment).
        skins = {s.get("name"): s.get("attachments", {}) for s in data.get("skins", [])}
        self.skin = skin if skin in skins and skin != "default" else None
        self._skins = skins
        merged: dict[str, dict[str, Any]] = {k: dict(v) for k, v in skins.get("default", {}).items()}
        origin: dict[tuple[str, str], str] = {(slot, key): "default" for slot, entries in merged.items()
                                              for key in entries}
        for slot, entries in (skins.get(self.skin, {}) if self.skin else {}).items():
            merged.setdefault(slot, {}).update(entries)
            origin.update({(slot, key): self.skin for key in entries})
        self._attachments = merged
        self._origin = origin  # (slot, key) -> the skin the shown attachment comes from
        self._solver = SkeletonSolver(data, self.skin)
        self.active_bones = self._solver.active_bones()
        self._path_slots = {c.target_slot for c in self._solver.paths}
        self.limitations = self._find_limitations()

    def _find_limitations(self) -> list[str]:
        """Things in the data that this preview does not draw exactly."""
        found = []
        if any("rgba2" in tl or "rgb2" in tl for tl in self.animation.get("slots", {}).values()):
            found.append("dark tint ignored")
        if any(s.get("blend", "normal") != "normal" for s in self.data.get("slots", [])):
            found.append("blend modes approximate")
        return found

    def bone_locals(self, time: float) -> dict[str, tuple[float, ...]]:
        """Posed local (x, y, rotation, scaleX, scaleY, shearX, shearY) per bone: setup pose plus
        keyed values (4.0 rules: translate/rotate/shear add, scale multiplies)."""
        anim_bones = self.animation.get("bones", {})
        result = {}
        for bone in self.data["bones"]:
            x, y = bone.get("x", 0), bone.get("y", 0)
            rot = bone.get("rotation", 0)
            sx, sy = bone.get("scaleX", 1), bone.get("scaleY", 1)
            shx, shy = bone.get("shearX", 0), bone.get("shearY", 0)
            tl = anim_bones.get(bone["name"])
            if tl:
                if "rotate" in tl and (v := channels_at(tl["rotate"], time, _read_value(0), 1)):
                    rot += v[0]
                if "translate" in tl and (v := channels_at(tl["translate"], time, _read_xy(0), 2)):
                    x, y = x + v[0], y + v[1]
                if "translatex" in tl and (v := channels_at(tl["translatex"], time, _read_value(0), 1)):
                    x += v[0]
                if "translatey" in tl and (v := channels_at(tl["translatey"], time, _read_value(0), 1)):
                    y += v[0]
                if "scale" in tl and (v := channels_at(tl["scale"], time, _read_xy(1), 2)):
                    sx, sy = sx * v[0], sy * v[1]
                if "scalex" in tl and (v := channels_at(tl["scalex"], time, _read_value(1), 1)):
                    sx *= v[0]
                if "scaley" in tl and (v := channels_at(tl["scaley"], time, _read_value(1), 1)):
                    sy *= v[0]
                if "shear" in tl and (v := channels_at(tl["shear"], time, _read_xy(0), 2)):
                    shx, shy = shx + v[0], shy + v[1]
                if "shearx" in tl and (v := channels_at(tl["shearx"], time, _read_value(0), 1)):
                    shx += v[0]
                if "sheary" in tl and (v := channels_at(tl["sheary"], time, _read_value(0), 1)):
                    shy += v[0]
            pose = [x, y, rot, sx, sy, shx, shy]
            for index, value in self.overrides.get(bone["name"], {}).items():  # unkeyed edits (Auto Key off)
                pose[index] = value
            result[bone["name"]] = tuple(pose)
        return result

    def ik_state(self, time: float) -> dict[str, dict[str, Any]]:
        """Keyed IK values at time (constraints without keys use their setup values)."""
        result = {}
        for name, keys in self.animation.get("ik", {}).items():
            values = channels_at(keys, time, lambda k: [float(k.get("mix", 1)), float(k.get("softness", 0))], 2)
            if values is None:
                continue
            k0 = key_at_or_before(keys, time)
            result[name] = {"mix": values[0], "softness": values[1], "bend": 1 if k0.get("bendPositive", True) else -1,
                            "compress": bool(k0.get("compress", False)), "stretch": bool(k0.get("stretch", False))}
        return result

    def transform_state(self, time: float) -> dict[str, dict[str, float]]:
        """Keyed transform constraint mixes at time (solver field names)."""
        out = {}
        for name, keys in self.animation.get("transform", {}).items():
            values = channels_at(keys, time, tl40.TRANSFORM_READER, 6)
            if values is not None:
                out[name] = dict(zip(("mix_rotate", "mix_x", "mix_y", "mix_scale_x", "mix_scale_y", "mix_shear_y"),
                                     values))
        return out

    def path_state(self, time: float) -> dict[str, dict[str, float]]:
        """Keyed path constraint position, spacing and mixes at time."""
        out: dict[str, dict[str, float]] = {}
        for name, timelines in self.animation.get("path", {}).items():
            state: dict[str, float] = {}
            for field in ("position", "spacing"):
                if timelines.get(field) and (v := channels_at(timelines[field], time, _read_value(0), 1)):
                    state[field] = v[0]
            if timelines.get("mix") and (v := channels_at(timelines["mix"], time, tl40.PATH_MIX_READER, 3)):
                state.update(zip(("mix_rotate", "mix_x", "mix_y"), v))
            if state:
                out[name] = state
        return out

    def path_targets(self, time: float) -> dict[str, tuple[dict[str, Any] | None, list[float] | None]]:
        """For each path constraint's target slot: the attachment it shows and its deform offsets."""
        out = {}
        for slot in self.data.get("slots", []):
            if slot["name"] not in self._path_slots:
                continue
            name = self.slot_attachment(slot, time)
            att = self._attachments.get(slot["name"], {}).get(name) if name else None
            deform = None
            if att is not None and att.get("type") == "path":
                deform = self.deform_offsets(slot["name"], name, time, parse_vertices(att).deform_length)
            out[slot["name"]] = (att, deform)
        return out

    def slot_color(self, slot: dict[str, Any], time: float) -> tuple[float, float, float, float]:
        """4.0 colour timelines: rgba (all), or rgb and alpha separately; rgba2/rgb2 give the light colour."""
        color = _rgba_of(slot.get("color", "ffffffff"))
        tl = self.animation.get("slots", {}).get(slot["name"], {})
        if "rgba" in tl and (v := channels_at(tl["rgba"], time, lambda k: _rgba_of(k["color"]), 4)):
            color = v
        if "rgb" in tl and (v := channels_at(tl["rgb"], time, lambda k: _rgba_of(k["color"])[:3], 3)):
            color = v + color[3:]
        if "alpha" in tl and (v := channels_at(tl["alpha"], time, _read_value(0), 1)):
            color = color[:3] + v
        if "rgba2" in tl and (v := channels_at(tl["rgba2"], time, lambda k: _rgba_of(k["light"]), 4)):
            color = v
        if "rgb2" in tl and (v := channels_at(tl["rgb2"], time, lambda k: _rgba_of(k["light"])[:3], 3)):
            color = v + color[3:]
        return tuple(color)  # type: ignore[return-value]

    def slot_attachment(self, slot: dict[str, Any], time: float) -> str | None:
        keys = self.animation.get("slots", {}).get(slot["name"], {}).get("attachment")
        if keys and (k := key_at_or_before(keys, time)) is not None:
            return k.get("name")
        return slot.get("attachment")

    def bone_world(self, time: float) -> dict[str, Affine]:
        world = self._solver.solve(self.bone_locals(time), self.ik_state(time),
                                   transform_state=self.transform_state(time), path_state=self.path_state(time),
                                   path_targets=self.path_targets(time))
        # runtime layout (a, b, c, d) -> this module's (a, c, b, d)
        return {n: (m[0], m[2], m[1], m[3], m[4], m[5]) for n, m in world.items()}

    def draw_order(self, time: float) -> list[dict[str, Any]]:
        """Slots back to front, with draw-order keys applied (DrawOrderTimeline)."""
        slots = self.data["slots"]
        keys = self.animation.get("drawOrder")
        k = key_at_or_before(keys, time) if keys else None
        offsets = k.get("offsets") if k else None
        if not offsets:
            return list(slots)
        index = {s["name"]: i for i, s in enumerate(slots)}
        count = len(slots)
        order: list[int] = [-1] * count
        unchanged: list[int] = []
        original = 0
        for entry in offsets:
            slot_index = index.get(entry.get("slot"))
            if slot_index is None:
                return list(slots)
            while original != slot_index:
                unchanged.append(original)
                original += 1
            target = original + int(entry.get("offset", 0))
            if 0 <= target < count:
                order[target] = original
            original += 1
        while original < count:
            unchanged.append(original)
            original += 1
        for i in range(count - 1, -1, -1):
            if order[i] == -1:
                order[i] = unchanged.pop()
        return [slots[i] for i in order]

    def draw_items(self, time: float) -> list[DrawItem]:
        """Drawable attachments back to front. A clipping attachment clips the slots after it up to
        and including its end slot (SkeletonClipping: clipStart, clipEndWithSlot)."""
        world = self.bone_world(time)
        items: list[DrawItem] = []
        clip: tuple[tuple[float, float], ...] | None = None
        clip_end: str | None = None
        for slot in self.draw_order(time):
            item = self._draw_item(slot, time, world)
            if isinstance(item, tuple):  # a clipping attachment starts clipping
                if clip is None:
                    clip, clip_end = item
            elif item is not None:
                items.append(dataclasses.replace(item, clip=clip) if clip is not None else item)
            if clip is not None and clip_end == slot["name"]:
                clip, clip_end = None, None
        return items

    def _draw_item(self, slot: dict[str, Any], time: float, world: dict[str, Affine]):
        """A DrawItem, (clip polygon, end slot) for a clipping attachment, or None."""
        name = slot["name"]
        if slot["bone"] not in self.active_bones:  # a skin bone the active skin does not use
            return None
        attachment = self.slot_attachment(slot, time)
        if attachment is None:
            return None
        att = self._attachments.get(name, {}).get(attachment)
        if att is None:
            return None
        if att.get("type") == "clipping":
            mesh = parse_vertices(att)
            deform = self.deform_offsets(name, attachment, time, mesh.deform_length)
            bones = [world[b["name"]] for b in self.data["bones"]]
            polygon = tuple(world_vertices(mesh, world[slot["bone"]], bones, deform))
            return (polygon, att.get("end")) if len(polygon) >= 3 else None
        color = self.slot_color(slot, time)
        kind = att.get("type", "region")
        if kind in ("mesh", "linkedmesh"):
            source = self.mesh_source(name, attachment)
            if source is None:
                return None
            geo = source[0]
            geometry = MeshGeometry(
                tuple(self.mesh_world_vertices(name, attachment, time, world)),
                tuple((geo["uvs"][i], geo["uvs"][i + 1]) for i in range(0, len(geo["uvs"]), 2)),
                tuple(tuple(geo["triangles"][i:i + 3]) for i in range(0, len(geo["triangles"]) - 2, 3)),
                int(geo.get("hull", 0)))
            return DrawItem(name, att.get("path", att.get("name", attachment)), att.get("width", 0), att.get("height", 0),
                            (1, 0, 0, 1, 0, 0), color, slot.get("blend", "normal"), geometry, attachment)
        if kind != "region":
            return None
        w, h = att["width"], att["height"]
        att_local = _local(att.get("x", 0), att.get("y", 0), att.get("rotation", 0),
                           att.get("scaleX", 1), att.get("scaleY", 1))
        # Image pixels (y down, origin top-left) -> attachment space (y up, centred).
        pixel_to_att: Affine = (1, 0, 0, -1, -w / 2, h / 2)
        transform = _mul(world[slot["bone"]], _mul(att_local, pixel_to_att))
        return DrawItem(name, att.get("path", att.get("name", attachment)), w, h, transform, color,
                        slot.get("blend", "normal"), attachment=attachment)

    def shape_items(self, time: float) -> list[ShapeItem]:
        """Bounding boxes, clipping polygons, paths and points currently shown, in world space
        (for drawing and editing; the game does not render them)."""
        world = self.bone_world(time)
        bone_list = [world[b["name"]] for b in self.data["bones"]]
        out = []
        for slot in self.data["slots"]:
            if slot["bone"] not in self.active_bones:
                continue
            key = self.slot_attachment(slot, time)
            att = self._attachments.get(slot["name"], {}).get(key) if key else None
            kind = att.get("type") if att else None
            if kind not in ("boundingbox", "clipping", "path", "point"):
                continue
            bone = world[slot["bone"]]
            if kind == "point":
                x, y, r = att.get("x", 0), att.get("y", 0), math.radians(att.get("rotation", 0))
                a, b, c, d, tx, ty = bone
                pos = (a * x + c * y + tx, b * x + d * y + ty)
                ux, uy = x + math.cos(r) * 20, y + math.sin(r) * 20
                tip = (a * ux + c * uy + tx, b * ux + d * uy + ty)
                out.append(ShapeItem(slot["name"], key, kind, (pos, tip)))
                continue
            mesh = parse_vertices(att)
            deform = self.deform_offsets(slot["name"], key, time, mesh.deform_length)
            points = tuple(world_vertices(mesh, bone, bone_list, deform))
            out.append(ShapeItem(slot["name"], key, kind, points, bool(att.get("closed", False)), att.get("end")))
        return out

    def mesh_source(self, slot: str, key: str) -> tuple[dict[str, Any], tuple[str, str]] | None:
        """(the attachment holding the vertices, (skin, key) of the attachment whose deform keys apply).
        A linked mesh uses its parent's geometry; with deform on (the default) it also plays the
        parent's deform keys (MeshAttachment.deformAttachment)."""
        att = self._attachments.get(slot, {}).get(key)
        if att is None:
            return None
        own = (self._origin.get((slot, key), "default"), key)
        parent_key = att.get("parent")
        if att.get("type") not in ("mesh", "linkedmesh") or not parent_key:
            return att, own
        parent_skin = att.get("skin") or "default"
        parent = self._skins.get(parent_skin, {}).get(slot, {}).get(parent_key)
        if parent is None:
            return None
        return parent, ((parent_skin, parent_key) if att.get("deform", True) else own)

    def deform_offsets(self, slot: str, attachment: str, time: float, length: int) -> list[float] | None:
        """4.0 DeformTimeline as offsets: None before the first key (setup shape). The key's curve
        maps time to a 0..1 blend between the two keys' vertex arrays. Deform keys belong to an
        attachment of a skin; a linked mesh plays its parent's keys."""
        source = self.mesh_source(slot, attachment)
        skin, key = source[1] if source else ("default", attachment)
        keys = self.animation.get("deform", {}).get(skin, {}).get(slot, {}).get(key)
        if not keys:
            return None
        i = cv.find_segment(keys, time)
        if i is None:
            return None
        prev = key_offsets(keys[i], length)
        if i + 1 >= len(keys):
            return prev
        p = cv.segment_value(keys[i], keys[i + 1], time, 0, 0.0, 1.0)
        nxt = key_offsets(keys[i + 1], length)
        return [a + (b - a) * p for a, b in zip(prev, nxt)]

    def mesh_world_vertices(self, slot: str, attachment: str, time: float,
                            world: dict[str, Affine] | None = None) -> list[tuple[float, float]]:
        world = world if world is not None else self.bone_world(time)
        source = self.mesh_source(slot, attachment)
        if source is None:
            raise KeyError(f"{slot}/{attachment}")
        mesh = parse_vertices(source[0])
        bone_names = [b["name"] for b in self.data["bones"]]
        slot_bone = next(s["bone"] for s in self.data["slots"] if s["name"] == slot)
        deform = self.deform_offsets(slot, attachment, time, mesh.deform_length)
        return world_vertices(mesh, world[slot_bone], [world[n] for n in bone_names], deform)


def _wrap180(degrees: float) -> float:
    """AnimationState.applyRotateTimeline's shortest difference."""
    return degrees - (16384 - int(16384.499999999996 - degrees / 360)) * 360


class MixEvaluator(PoseEvaluator):
    """Crossfade preview like the official 4.0 AnimationState on one track: animation A plays
    from time 0, B is set at `switch` with a mix of `mix_duration` seconds.

    While mixing, every property goes from A's pose to B's by mix = elapsed / mix_duration, with
    unkeyed properties at the setup pose. Rotation keyed by both takes the shorter way round.
    Attachments come from B (setup when B does not key the slot), and IK bend, compress and
    stretch come from B's keys or the setup values."""

    def __init__(self, data: dict[str, Any], from_animation: str, to_animation: str, switch: float,
                 mix_duration: float, loop_from: bool = False, loop_to: bool = False, skin: str | None = None):
        super().__init__(data, from_animation, skin)
        self.a = PoseEvaluator(self.data, from_animation, skin)
        self.b = PoseEvaluator(self.data, to_animation, skin)
        self.switch = max(0.0, float(switch))
        self.mix_duration = max(0.0, float(mix_duration))
        self.loop_from, self.loop_to = loop_from, loop_to
        self.limitations = sorted(set(self.a.limitations) | set(self.b.limitations))
        self._setup = PoseEvaluator(self.data, "", skin)

    @staticmethod
    def _anim_time(ev: PoseEvaluator, t: float, loop: bool) -> float:
        if ev.duration <= 0:
            return 0.0
        return t % ev.duration if loop and t >= ev.duration else min(t, ev.duration)

    def phase(self, time: float) -> tuple[float, float | None, float]:
        """(time in A, time in B or None before the switch, mix 0..1)."""
        ta = self._anim_time(self.a, time, self.loop_from)
        if time < self.switch:
            return ta, None, 0.0
        elapsed = time - self.switch
        mix = 1.0 if self.mix_duration <= 0 else min(1.0, elapsed / self.mix_duration)
        return ta, self._anim_time(self.b, elapsed, self.loop_to), mix

    def _rotation_keyed(self, ev: PoseEvaluator, bone: str) -> bool:
        return "rotate" in ev.animation.get("bones", {}).get(bone, {})

    def bone_locals(self, time: float) -> dict[str, tuple[float, ...]]:
        ta, tb, mix = self.phase(time)
        la = self.a.bone_locals(ta)
        if tb is None or mix <= 0:
            return la
        lb = self.b.bone_locals(tb)
        out = {}
        for name, va in la.items():
            vb = lb[name]
            mixed = [x + (y - x) * mix for x, y in zip(va, vb)]
            if self._rotation_keyed(self.a, name) and self._rotation_keyed(self.b, name):
                mixed[2] = va[2] + _wrap180(vb[2] - va[2]) * mix
            out[name] = tuple(mixed)
        return out

    def ik_state(self, time: float) -> dict[str, dict[str, Any]]:
        ta, tb, mix = self.phase(time)
        if tb is None or mix <= 0:
            return self.a.ik_state(ta)
        sa, sb = self.a.ik_state(ta), self.b.ik_state(tb)
        out = {}
        for c in self.data.get("ik", []):
            name = c["name"]
            if name not in sa and name not in sb:
                continue
            setup = {"mix": float(c.get("mix", 1)), "softness": float(c.get("softness", 0)),
                     "bend": 1 if c.get("bendPositive", True) else -1,
                     "compress": bool(c.get("compress", False)), "stretch": bool(c.get("stretch", False))}
            a, b = sa.get(name, setup), sb.get(name, setup)
            out[name] = {"mix": a["mix"] + (b["mix"] - a["mix"]) * mix,
                         "softness": a["softness"] + (b["softness"] - a["softness"]) * mix,
                         "bend": b["bend"], "compress": b["compress"], "stretch": b["stretch"]}
        return out

    def _mix_constraints(self, kind: str, sa: dict, sb: dict, mix: float) -> dict[str, dict[str, float]]:
        """Lerp keyed constraint values; a constraint only one side keys uses setup values on the other."""
        setup = self._setup._solver
        defaults = {"transform": setup._transform_setup, "path": setup._path_setup}[kind]
        out = {}
        for name in set(sa) | set(sb):
            base = defaults.get(name, {})
            a, b = {**base, **sa.get(name, {})}, {**base, **sb.get(name, {})}
            out[name] = {k: a[k] + (b[k] - a[k]) * mix for k in a}
        return out

    def transform_state(self, time: float) -> dict[str, dict[str, float]]:
        ta, tb, mix = self.phase(time)
        if tb is None or mix <= 0:
            return self.a.transform_state(ta)
        return self._mix_constraints("transform", self.a.transform_state(ta), self.b.transform_state(tb), mix)

    def path_state(self, time: float) -> dict[str, dict[str, float]]:
        ta, tb, mix = self.phase(time)
        if tb is None or mix <= 0:
            return self.a.path_state(ta)
        return self._mix_constraints("path", self.a.path_state(ta), self.b.path_state(tb), mix)

    def draw_order(self, time: float) -> list[dict[str, Any]]:
        ta, tb, _ = self.phase(time)  # like attachments: the second animation's (or setup) once set
        return self.a.draw_order(ta) if tb is None else self.b.draw_order(tb)

    def slot_color(self, slot: dict[str, Any], time: float) -> tuple[float, float, float, float]:
        ta, tb, mix = self.phase(time)
        ca = self.a.slot_color(slot, ta)
        if tb is None or mix <= 0:
            return ca
        cb = self.b.slot_color(slot, tb)
        return tuple(x + (y - x) * mix for x, y in zip(ca, cb))  # type: ignore[return-value]

    def slot_attachment(self, slot: dict[str, Any], time: float) -> str | None:
        ta, tb, mix = self.phase(time)
        if tb is None:
            return self.a.slot_attachment(slot, ta)
        return self.b.slot_attachment(slot, tb)

    def deform_offsets(self, slot: str, attachment: str, time: float, length: int) -> list[float] | None:
        ta, tb, mix = self.phase(time)
        da = self.a.deform_offsets(slot, attachment, ta, length)
        if tb is None or mix <= 0:
            return da
        db = self.b.deform_offsets(slot, attachment, tb, length)
        if da is None and db is None:
            return None
        da, db = da or [0.0] * length, db or [0.0] * length
        return [x + (y - x) * mix for x, y in zip(da, db)]



def _retimed(keys: Any, offset: float) -> Any:
    """A copy of a key list (or nested timeline dict) with every key's time moved by offset."""
    if isinstance(keys, list):
        return [dict(k, time=round(k.get("time", 0) + offset, 6)) if isinstance(k, dict) else k for k in keys]
    if isinstance(keys, dict):
        return {name: _retimed(v, offset) for name, v in keys.items()}
    return keys


def _unrolled(timeline: Any, duration: float, total: float) -> Any:
    """Repeat a timeline's keys every `duration` seconds until `total` (a looping animation)."""
    if duration <= 0 or total <= duration:
        return timeline
    if isinstance(timeline, dict):
        return {name: _unrolled(v, duration, total) for name, v in timeline.items()}
    out, n = [], 0
    while n * duration < total - 1e-9:
        out += _retimed(timeline, n * duration)
        n += 1
    return out


def layered_animation(base: dict[str, Any], layer: dict[str, Any], loop: bool = True) -> dict[str, Any]:
    """Animation `layer` played on a track above `base` (alpha 1, like two AnimationState tracks):
    every timeline the layer keys replaces the base's, the rest of the base plays as it is.
    With loop, the shorter animation repeats to the longer one's length."""
    da, db = animation_duration(base), animation_duration(layer)
    total = max(da, db)
    if loop:
        base = {g: _unrolled(v, da, total) for g, v in base.items()}
        layer = {g: _unrolled(v, db, total) for g, v in layer.items()}
    merged: dict[str, Any] = {g: (dict(v) if isinstance(v, dict) else list(v)) for g, v in base.items()}
    for group in ("bones", "slots", "path"):  # owner -> timeline -> keys
        for owner, timelines in layer.get(group, {}).items():
            merged.setdefault(group, {})[owner] = {**merged.get(group, {}).get(owner, {}), **timelines}
    for group in ("ik", "transform"):  # constraint -> keys
        for owner, keys in layer.get(group, {}).items():
            merged.setdefault(group, {})[owner] = keys
    for skin, slots in layer.get("deform", {}).items():
        for slot, atts in slots.items():
            merged.setdefault("deform", {}).setdefault(skin, {}).setdefault(slot, {}).update(atts)
    for group in ("drawOrder", "events"):
        if layer.get(group):
            merged[group] = layer[group]
    return merged
