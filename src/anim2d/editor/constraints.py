"""Transform and path constraints and path attachments for the editor Document (a mixin).

Field names and defaults follow the Spine 4.0 JSON format. Only non-default values are written,
like Spine's exporter; keys always write every mix so the JSON defaults (mixY = mixX, …) cannot
change a value the user did not touch. Constraint orders stay contiguous across IK, transform
and path constraints because the runtime only applies orders 0..n-1.
"""

from __future__ import annotations

import math
from typing import Any

from ..formats import timelines40 as tl40

TRANSFORM_DEFAULTS = {"local": False, "relative": False, "rotation": 0, "x": 0, "y": 0, "scaleX": 0, "scaleY": 0,
                      "shearY": 0, "mixRotate": 1, "mixX": 1, "mixY": 1, "mixScaleX": 1, "mixScaleY": 1,
                      "mixShearY": 1}
PATH_DEFAULTS = {"positionMode": "percent", "spacingMode": "length", "rotateMode": "tangent", "rotation": 0,
                 "position": 0, "spacing": 0, "mixRotate": 1, "mixX": 1, "mixY": 1}
MIXES = ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY")
PATH_MIXES = ("mixRotate", "mixX", "mixY")
CHOICES = {"positionMode": ("fixed", "percent"), "spacingMode": ("length", "fixed", "percent", "proportional"),
           "rotateMode": ("tangent", "chain", "chainScale")}


def _r(value: float) -> float:
    v = round(float(value), 4)
    return 0.0 if v == 0 else v


def _fields(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in values.items():
        if key not in defaults:
            continue
        if isinstance(defaults[key], bool):
            value = bool(value)
        elif isinstance(defaults[key], (int, float)):
            value = _r(value)
        if value != defaults[key]:
            out[key] = value
    return out


# --- path geometry -----------------------------------------------------------------------------

def bezier_length(p0, p1, p2, p3, steps: int = 64) -> float:
    total, prev = 0.0, p0
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
        y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
        total += math.hypot(x - prev[0], y - prev[1])
        prev = (x, y)
    return total


def path_lengths(vertices: list[float], closed: bool) -> list[float]:
    """Cumulative curve lengths of an unweighted path (the "lengths" field), one per point."""
    pts = [(vertices[i], vertices[i + 1]) for i in range(0, len(vertices), 2)]
    count = len(pts) // 3  # points; each has (in handle, point, out handle)
    curves = count if closed else count - 1
    out, total = [], 0.0
    for c in range(curves):
        a = c * 3 + 1
        b = (a + 3) % len(pts)
        total += bezier_length(pts[a], pts[a + 1], pts[b - 1], pts[b])
        out.append(round(total, 4))
    while len(out) < count:
        out.append(round(total, 4))
    return out


def smooth_path(points: list[tuple[float, float]], closed: bool = False) -> list[float]:
    """Path vertices through points with smooth (Catmull-Rom style) handles: [in, point, out] each."""
    n = len(points)
    verts: list[float] = []
    for i, (x, y) in enumerate(points):
        prev = points[i - 1] if (i > 0 or closed) else points[i]
        nxt = points[(i + 1) % n] if (i < n - 1 or closed) else points[i]
        tx, ty = (nxt[0] - prev[0]) / 6, (nxt[1] - prev[1]) / 6
        verts += [x - tx, y - ty, x, y, x + tx, y + ty]
    return [_r(v) for v in verts]


class ConstraintsMixin:
    """Mixed into Document; relies on its data, _begin, notify, bone, slot, animation, attachments,
    unique_name, _check_new_name, _constraint_names and _ordered_constraints."""

    # --- lookup ----------------------------------------------------------------------------
    @property
    def transforms(self) -> list[dict[str, Any]]:
        return self.data.get("transform", [])

    @property
    def paths(self) -> list[dict[str, Any]]:
        return self.data.get("path", [])

    def transform_constraint(self, name: str) -> dict[str, Any]:
        for c in self.transforms:
            if c["name"] == name:
                return c
        raise self._error(f"No transform constraint named '{name}'")

    def path_constraint(self, name: str) -> dict[str, Any]:
        for c in self.paths:
            if c["name"] == name:
                return c
        raise self._error(f"No path constraint named '{name}'")

    def constraint_kind(self, name: str) -> str:
        for kind in ("ik", "transform", "path"):
            if any(c["name"] == name for c in self.data.get(kind, [])):
                return kind
        raise self._error(f"No constraint named '{name}'")

    def _error(self, message: str) -> Exception:
        from .document import EditorError
        return EditorError(message)

    def _renumber_constraints(self) -> None:
        for i, c in enumerate(sorted(self._ordered_constraints(), key=lambda c: c.get("order", 0))):
            c["order"] = i

    def move_constraint(self, name: str, delta: int, record: bool = True) -> None:
        """Change a constraint's place in the update order (earlier constraints are applied first)."""
        ordered = sorted(self._ordered_constraints(), key=lambda c: c.get("order", 0))
        index = next((i for i, c in enumerate(ordered) if c["name"] == name), None)
        if index is None:
            raise self._error(f"No constraint named '{name}'")
        new = max(0, min(len(ordered) - 1, index + delta))
        if new == index:
            return
        self._begin(f"Reorder {name}", record)
        ordered.insert(new, ordered.pop(index))
        for i, c in enumerate(ordered):
            c["order"] = i
        self.notify()

    # --- transform constraints -------------------------------------------------------------
    def _check_constrained_bones(self, bones: list[str], target: str | None) -> None:
        if not bones:
            raise self._error("Choose at least one bone to constrain")
        for b in bones:
            self.bone(b)
        if len(set(bones)) != len(bones):
            raise self._error("A bone is listed twice")
        if target is not None:
            self.bone(target)
            if target in bones:
                raise self._error("The target cannot be one of the constrained bones")

    def add_transform(self, bones: list[str], target: str, name: str | None = None, record: bool = True,
                      **fields: Any) -> str:
        existing = self._constraint_names()
        name = name or self.unique_name(f"{bones[0]}-transform" if bones else "transform", existing)
        self._check_new_name(name, existing, "constraint")
        self._check_constrained_bones(bones, target)
        self._check_mixes(fields)
        self._begin(f"Create transform constraint {name}", record)
        c = {"name": name, "order": len(self._ordered_constraints()), "bones": list(bones), "target": target}
        c.update(_fields(TRANSFORM_DEFAULTS, fields))
        self.data.setdefault("transform", []).append(c)
        self.notify()
        return name

    def _check_mixes(self, fields: dict[str, Any]) -> None:
        for key in MIXES:
            if key in fields and not 0 <= float(fields[key]) <= 1:
                raise self._error(f"{key} must be between 0 and 1")

    def set_transform(self, name: str, record: bool = True, **fields: Any) -> None:
        c = self.transform_constraint(name)
        unknown = set(fields) - set(TRANSFORM_DEFAULTS) - {"bones", "target"}
        if unknown:
            raise self._error(f"Unknown transform constraint field(s): {', '.join(sorted(unknown))}")
        self._check_constrained_bones(fields.get("bones", c["bones"]), fields.get("target", c["target"]))
        self._check_mixes(fields)
        self._begin(f"Edit transform constraint {name}", record)
        for key in ("bones", "target"):
            if key in fields:
                c[key] = list(fields.pop(key)) if key == "bones" else fields.pop(key)
        for key in fields:
            c.pop(key, None)
        c.update(_fields(TRANSFORM_DEFAULTS, fields))
        self.notify()

    # --- path constraints ------------------------------------------------------------------
    def add_path(self, bones: list[str], target: str, name: str | None = None, record: bool = True,
                 **fields: Any) -> str:
        """target is the slot whose path attachment the bones follow."""
        existing = self._constraint_names()
        name = name or self.unique_name(f"{bones[0]}-path" if bones else "path", existing)
        self._check_new_name(name, existing, "constraint")
        self._check_constrained_bones(bones, None)
        self.slot(target)
        self._check_path_fields(fields)
        self._begin(f"Create path constraint {name}", record)
        c = {"name": name, "order": len(self._ordered_constraints()), "bones": list(bones), "target": target}
        c.update(_fields(PATH_DEFAULTS, fields))
        self.data.setdefault("path", []).append(c)
        self.notify()
        return name

    def _check_path_fields(self, fields: dict[str, Any]) -> None:
        for key, values in CHOICES.items():
            if key in fields and fields[key] not in values:
                raise self._error(f"{key} must be one of {', '.join(values)}")
        for key in PATH_MIXES:
            if key in fields and not 0 <= float(fields[key]) <= 1:
                raise self._error(f"{key} must be between 0 and 1")

    def set_path(self, name: str, record: bool = True, **fields: Any) -> None:
        c = self.path_constraint(name)
        unknown = set(fields) - set(PATH_DEFAULTS) - {"bones", "target"}
        if unknown:
            raise self._error(f"Unknown path constraint field(s): {', '.join(sorted(unknown))}")
        self._check_constrained_bones(fields.get("bones", c["bones"]), None)
        if "target" in fields:
            self.slot(fields["target"])
        self._check_path_fields(fields)
        self._begin(f"Edit path constraint {name}", record)
        for key in ("bones", "target"):
            if key in fields:
                c[key] = list(fields.pop(key)) if key == "bones" else fields.pop(key)
        for key in fields:
            c.pop(key, None)
        c.update(_fields(PATH_DEFAULTS, fields))
        self.notify()

    # --- shared: delete, rename, cascades --------------------------------------------------
    def delete_constraint(self, name: str, record: bool = True) -> None:
        kind = self.constraint_kind(name)
        self._begin(f"Delete {name}", record)
        self._delete_constraint_data(kind, name)
        self.notify()

    def _delete_constraint_data(self, kind: str, name: str) -> None:
        self.data[kind] = [c for c in self.data.get(kind, []) if c["name"] != name]
        if not self.data[kind]:
            del self.data[kind]
        self._renumber_constraints()
        for anim in self.data["animations"].values():
            anim.get(kind, {}).pop(name, None)
            if kind in anim and not anim[kind]:
                del anim[kind]
        for skin in self.data.get("skins", []):
            if name in skin.get(kind, []):
                skin[kind] = [n for n in skin[kind] if n != name]
                if not skin[kind]:
                    del skin[kind]

    def rename_constraint(self, old: str, new: str, record: bool = True) -> None:
        kind = self.constraint_kind(old)
        if old == new:
            return
        self._check_new_name(new, self._constraint_names(), "constraint")
        self._begin(f"Rename {old}", record)
        next(c for c in self.data[kind] if c["name"] == old)["name"] = new
        for anim in self.data["animations"].values():
            if old in anim.get(kind, {}):
                anim[kind] = {(new if k == old else k): v for k, v in anim[kind].items()}
        for skin in self.data.get("skins", []):
            if old in skin.get(kind, []):
                skin[kind] = [new if n == old else n for n in skin[kind]]
        self.notify()

    def _constraints_bones_deleted(self, doomed: set[str]) -> None:
        for kind in ("transform", "path"):
            for c in list(self.data.get(kind, [])):
                if doomed & {*c["bones"], c["target"] if kind == "transform" else None}:
                    self._delete_constraint_data(kind, c["name"])

    def _constraints_bone_renamed(self, old: str, new: str) -> None:
        for kind in ("transform", "path"):
            for c in self.data.get(kind, []):
                c["bones"] = [new if b == old else b for b in c["bones"]]
                if kind == "transform" and c["target"] == old:
                    c["target"] = new
        for skin in self.data.get("skins", []):
            if old in skin.get("bones", []):
                skin["bones"] = [new if b == old else b for b in skin["bones"]]

    def _constraints_slot_deleted(self, slot: str) -> None:
        for c in list(self.paths):
            if c["target"] == slot:
                self._delete_constraint_data("path", c["name"])

    def _constraints_slot_renamed(self, old: str, new: str) -> None:
        for c in self.paths:
            if c["target"] == old:
                c["target"] = new

    # --- keys ------------------------------------------------------------------------------
    def set_transform_key(self, animation: str, name: str, time: float, record: bool = True,
                          **mixes: float) -> None:
        """Key the mixes; unspecified ones keep their value at that time (posed or setup)."""
        anim = self.animation(animation)
        c = self.transform_constraint(name)
        if set(mixes) - set(MIXES):
            raise self._error(f"Transform keys take {', '.join(MIXES)}")
        self._check_mixes(mixes)
        if time < 0:
            raise self._error("Key time cannot be negative")
        posed = self.evaluator(animation).transform_state(time).get(name)
        current = {k: c.get(k, TRANSFORM_DEFAULTS[k]) for k in MIXES}
        if posed:
            current.update(zip(MIXES, (posed[k] for k in ("mix_rotate", "mix_x", "mix_y", "mix_scale_x",
                                                          "mix_scale_y", "mix_shear_y"))))
        current.update(mixes)
        self._begin(f"Key transform {name}", record)
        keys = anim.setdefault("transform", {}).setdefault(name, [])
        self._put_key(keys, time, {k: _r(v) for k, v in current.items()}, tl40.TRANSFORM_READER)
        self.notify()

    def set_path_key(self, animation: str, name: str, timeline: str, time: float, record: bool = True,
                     value: float | None = None, **mixes: float) -> None:
        """timeline "position" or "spacing" (value=...), or "mix" (mixRotate/mixX/mixY)."""
        anim = self.animation(animation)
        c = self.path_constraint(name)
        if time < 0:
            raise self._error("Key time cannot be negative")
        posed = self.evaluator(animation).path_state(time).get(name, {})
        keys = anim.setdefault("path", {}).setdefault(name, {})
        if timeline in ("position", "spacing"):
            if mixes:
                raise self._error(f"{timeline} keys take a value")
            current = posed.get(timeline, c.get(timeline, 0)) if value is None else value
            self._begin(f"Key path {timeline} {name}", record)
            self._put_key(keys.setdefault(timeline, []), time, {"value": _r(current)},
                          tl40.PATH_TIMELINES[timeline])
        elif timeline == "mix":
            if value is not None or set(mixes) - set(PATH_MIXES):
                raise self._error("Path mix keys take mixRotate, mixX and mixY")
            self._check_path_fields(mixes)
            current = {k: c.get(k, 1) for k in PATH_MIXES}
            current.update({k: posed[s] for k, s in zip(PATH_MIXES, ("mix_rotate", "mix_x", "mix_y")) if s in posed})
            current.update(mixes)
            self._begin(f"Key path mix {name}", record)
            self._put_key(keys.setdefault("mix", []), time, {k: _r(v) for k, v in current.items()},
                          tl40.PATH_MIX_READER)
        else:
            raise self._error(f"Unknown path timeline '{timeline}'")
        self.notify()

    # --- path attachments ------------------------------------------------------------------
    def add_path_attachment(self, slot: str, points: list[tuple[float, float]], name: str | None = None,
                            closed: bool = False, constant_speed: bool = True, record: bool = True) -> str:
        """A path through points (in the slot bone's space) with smooth handles."""
        if len(points) < 2:
            raise self._error("A path needs at least 2 points")
        existing = set(self.attachments(slot))
        name = name or self.unique_name("path", existing)
        self._check_new_name(name, existing, "attachment")
        vertices = smooth_path(points, closed)
        att: dict[str, Any] = {"type": "path", "vertexCount": len(vertices) // 2,
                               "lengths": path_lengths(vertices, closed), "vertices": vertices}
        if closed:
            att["closed"] = True
        if not constant_speed:
            att["constantSpeed"] = False
        self._begin(f"Create path {name}", record)
        self.skin.setdefault(slot, {})[name] = att
        if self.slot(slot).get("attachment") is None:
            self.slot(slot)["attachment"] = name
        self.notify()
        return name

    def set_path_vertices(self, slot: str, name: str, vertices: list[float], record: bool = True) -> None:
        """Replace an unweighted path's vertices ([in, point, out] per point) and recompute its lengths."""
        att = self.attachments(slot).get(name)
        if att is None or att.get("type") != "path":
            raise self._error(f"'{name}' on slot '{slot}' is not a path")
        if len(vertices) != len(att["vertices"]) and len(att["vertices"]) != att["vertexCount"] * 2:
            raise self._error("Weighted paths are edited through their bones")
        if len(vertices) % 6:
            raise self._error("Path vertices come in threes: in handle, point, out handle")
        self._begin(f"Edit path {name}", record)
        att["vertices"] = [_r(v) for v in vertices]
        att["vertexCount"] = len(vertices) // 2
        att["lengths"] = path_lengths(att["vertices"], bool(att.get("closed", False)))
        self.notify()

    def set_path_options(self, slot: str, name: str, record: bool = True, **fields: Any) -> None:
        att = self.attachments(slot).get(name)
        if att is None or att.get("type") != "path":
            raise self._error(f"'{name}' on slot '{slot}' is not a path")
        if set(fields) - {"closed", "constantSpeed", "color"}:
            raise self._error("Paths have closed, constantSpeed and color options")
        self._begin(f"Edit path {name}", record)
        if "closed" in fields and bool(fields["closed"]) != bool(att.get("closed", False)):
            att["closed"] = bool(fields.pop("closed"))
            if len(att["vertices"]) == att["vertexCount"] * 2:
                att["lengths"] = path_lengths(att["vertices"], att["closed"])
        for key, default in (("closed", False), ("constantSpeed", True)):
            if key in fields:
                att[key] = bool(fields[key])
            if att.get(key, default) == default:
                att.pop(key, None)
        if "color" in fields:
            att["color"] = fields["color"]
        self.notify()

    def insert_path_point(self, slot: str, name: str, after: int, point: tuple[float, float],
                          record: bool = True) -> int:
        """Add a point (with smooth handles) after point index `after`. Returns the new point index."""
        att = self.attachments(slot).get(name)
        if att is None or att.get("type") != "path":
            raise self._error(f"'{name}' on slot '{slot}' is not a path")
        verts = list(att["vertices"])
        count = len(verts) // 6
        if len(verts) != att["vertexCount"] * 2:
            raise self._error("Weighted paths are edited through their bones")
        prev = verts[after * 6 + 2:after * 6 + 4]
        nxt_index = (after + 1) % count
        nxt = verts[nxt_index * 6 + 2:nxt_index * 6 + 4] if (after + 1 < count or att.get("closed")) else prev
        tx, ty = (nxt[0] - prev[0]) / 6, (nxt[1] - prev[1]) / 6
        x, y = point
        index = after + 1
        verts[index * 6:index * 6] = [x - tx, y - ty, x, y, x + tx, y + ty]
        self.set_path_vertices(slot, name, verts, record)
        return index

    def remove_path_points(self, slot: str, name: str, points: set[int], record: bool = True) -> None:
        att = self.attachments(slot).get(name)
        if att is None or att.get("type") != "path":
            raise self._error(f"'{name}' on slot '{slot}' is not a path")
        verts = att["vertices"]
        kept = [v for i in range(len(verts) // 6) if i not in points for v in verts[i * 6:i * 6 + 6]]
        if len(kept) < 12:
            raise self._error("A path needs at least 2 points")
        self.set_path_vertices(slot, name, kept, record)
