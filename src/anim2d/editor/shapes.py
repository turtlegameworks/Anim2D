"""Bounding boxes, clipping polygons and points for the editor Document (a mixin).

Like Spine 4.0: a bounding box or clipping attachment is a polygon (vertexCount, vertices) in the
slot bone's space; clipping clips the slots from its own slot up to its end slot; a point is a
position and rotation. Path attachments live in constraints.py. Vertices are edited unweighted.
"""

from __future__ import annotations

from typing import Any

SHAPE_TYPES = ("boundingbox", "clipping", "path", "point")
COLORS = {"boundingbox": "60f000ff", "clipping": "ce3a3aff", "path": "ff7f00ff", "point": "f1f100ff"}


def _r(value: float) -> float:
    v = round(float(value), 4)
    return 0.0 if v == 0 else v


class ShapesMixin:
    """Mixed into Document; relies on its data, _begin, notify, slot, attachments, unique_name and
    _check_new_name."""

    def _new_shape(self, slot: str, base: str, name: str | None, att: dict[str, Any], record: bool) -> str:
        self.slot(slot)
        existing = set(self.attachments(slot))
        name = name or self.unique_name(base, existing)
        self._check_new_name(name, existing, "attachment")
        self._begin(f"Create {att['type']} {name}", record)
        self.skin.setdefault(slot, {})[name] = att
        if self.slot(slot).get("attachment") is None:
            self.slot(slot)["attachment"] = name
        self.notify()
        return name

    @staticmethod
    def _polygon(points: list[tuple[float, float]], kind: str) -> list[float]:
        if len(points) < 3:
            from .document import EditorError
            raise EditorError(f"A {'bounding box' if kind == 'boundingbox' else 'clipping polygon'} "
                              f"needs at least 3 points")
        return [_r(v) for x, y in points for v in (x, y)]

    def add_bounding_box(self, slot: str, points: list[tuple[float, float]], name: str | None = None,
                         record: bool = True) -> str:
        verts = self._polygon(points, "boundingbox")
        return self._new_shape(slot, "bounds", name,
                               {"type": "boundingbox", "vertexCount": len(points), "vertices": verts}, record)

    def add_clipping(self, slot: str, points: list[tuple[float, float]], end: str | None = None,
                     name: str | None = None, record: bool = True) -> str:
        verts = self._polygon(points, "clipping")
        att: dict[str, Any] = {"type": "clipping", "vertexCount": len(points), "vertices": verts}
        if end is not None:
            self.slot(end)
            att["end"] = end
        return self._new_shape(slot, "clip", name, att, record)

    def add_point(self, slot: str, x: float = 0, y: float = 0, rotation: float = 0, name: str | None = None,
                  record: bool = True) -> str:
        att: dict[str, Any] = {"type": "point"}
        for key, value in (("x", x), ("y", y), ("rotation", rotation)):
            if value:
                att[key] = _r(value)
        return self._new_shape(slot, "point", name, att, record)

    def _shape(self, slot: str, name: str, kinds: tuple[str, ...]) -> dict[str, Any]:
        att = self.attachments(slot).get(name)
        if att is None or att.get("type") not in kinds:
            from .document import EditorError
            raise EditorError(f"'{name}' on slot '{slot}' is not a {' or '.join(kinds)} attachment")
        return att

    def set_polygon_vertices(self, slot: str, name: str, vertices: list[float], record: bool = True) -> None:
        """Replace a bounding box's or clipping polygon's vertices (x, y pairs, slot bone space)."""
        att = self._shape(slot, name, ("boundingbox", "clipping"))
        from .document import EditorError
        if len(vertices) % 2 or len(vertices) < 6:
            raise EditorError("A polygon needs at least 3 x, y points")
        if len(att["vertices"]) != att["vertexCount"] * 2:
            raise EditorError("Weighted polygons follow their bones; unweighted ones can be edited")
        self._begin(f"Edit {name}", record)
        att["vertices"] = [_r(v) for v in vertices]
        att["vertexCount"] = len(vertices) // 2
        self.notify()

    def insert_polygon_vertex(self, slot: str, name: str, after: int, point: tuple[float, float],
                              record: bool = True) -> int:
        att = self._shape(slot, name, ("boundingbox", "clipping"))
        verts = list(att["vertices"])
        index = after + 1
        verts[index * 2:index * 2] = [point[0], point[1]]
        self.set_polygon_vertices(slot, name, verts, record)
        return index

    def remove_polygon_vertices(self, slot: str, name: str, indices: set[int], record: bool = True) -> None:
        att = self._shape(slot, name, ("boundingbox", "clipping"))
        verts = [v for i in range(att["vertexCount"]) if i not in indices
                 for v in att["vertices"][i * 2:i * 2 + 2]]
        self.set_polygon_vertices(slot, name, verts, record)

    def set_shape(self, slot: str, name: str, record: bool = True, **fields: Any) -> None:
        """Point: x, y, rotation, color. Bounding box: color. Clipping: end, color."""
        att = self._shape(slot, name, ("point", "boundingbox", "clipping"))
        kind = att["type"]
        allowed = {"point": {"x", "y", "rotation", "color"}, "boundingbox": {"color"},
                   "clipping": {"end", "color"}}[kind]
        from .document import EditorError
        if set(fields) - allowed:
            raise EditorError(f"{kind} attachments have {', '.join(sorted(allowed))}")
        if fields.get("end") is not None:
            self.slot(fields["end"])
        self._begin(f"Edit {name}", record)
        for key, value in fields.items():
            if value is None or (key in ("x", "y", "rotation") and _r(value) == 0) or \
                    (key == "color" and value == COLORS[kind]):
                att.pop(key, None)
            else:
                att[key] = _r(value) if key in ("x", "y", "rotation") else value
        self.notify()
