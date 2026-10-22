"""Mesh vertex math matching the Spine 3.8 runtimes (SkeletonJson.readVertices,
VertexAttachment.computeWorldVertices, DeformTimeline).

Vertex layouts in the JSON:
  * unweighted: [x, y, x, y, ...] in the slot bone's local space (len == len(uvs))
  * weighted:   per vertex [boneCount, (boneIndex, x, y, weight) * boneCount] where x, y are in
                that bone's local space and boneIndex indexes the skeleton's bone list

Deform keys are stored here as *offsets*: per vertex (unweighted) or per influence (weighted),
two floats each. A key's "vertices" are placed at "offset" and the rest are zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Affine = tuple[float, float, float, float, float, float]  # x' = a*x + c*y + tx, y' = b*x + d*y + ty


@dataclass(frozen=True)
class Influence:
    bone: int  # index into the skeleton bone list
    x: float
    y: float
    weight: float


@dataclass(frozen=True)
class MeshVertices:
    count: int  # number of vertices
    weighted: bool
    local: tuple[tuple[float, float], ...] = ()  # unweighted
    influences: tuple[tuple[Influence, ...], ...] = ()  # weighted, per vertex

    @property
    def deform_length(self) -> int:
        """Length of a deform array: 2 per vertex, or 2 per influence when weighted."""
        if self.weighted:
            return 2 * sum(len(v) for v in self.influences)
        return 2 * self.count


def parse_vertices(att: dict[str, Any]) -> MeshVertices:
    raw = att.get("vertices", [])
    count = len(att["uvs"]) // 2 if "uvs" in att else int(att.get("vertexCount", 0))  # paths, boxes, clips
    if len(raw) == count * 2:
        return MeshVertices(count, False, local=tuple((raw[i], raw[i + 1]) for i in range(0, len(raw), 2)))
    influences = []
    i = 0
    while i < len(raw):
        n = int(raw[i])
        i += 1
        vertex = []
        for _ in range(n):
            vertex.append(Influence(int(raw[i]), raw[i + 1], raw[i + 2], raw[i + 3]))
            i += 4
        influences.append(tuple(vertex))
    return MeshVertices(len(influences), True, influences=tuple(influences))


def encode_weighted(influences: list[list[Influence]]) -> list[float]:
    out: list[float] = []
    for vertex in influences:
        out.append(len(vertex))
        for inf in vertex:
            out.extend((inf.bone, round(inf.x, 4), round(inf.y, 4), round(inf.weight, 4)))
    return out


def _apply(m: Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, tx, ty = m
    return a * x + c * y + tx, b * x + d * y + ty


def world_vertices(mesh: MeshVertices, slot_bone_world: Affine, bone_worlds: list[Affine],
                   deform: list[float] | None = None) -> list[tuple[float, float]]:
    """deform: offsets (see module doc) or None."""
    out = []
    if not mesh.weighted:
        for i, (x, y) in enumerate(mesh.local):
            if deform:
                x, y = x + deform[2 * i], y + deform[2 * i + 1]
            out.append(_apply(slot_bone_world, x, y))
        return out
    f = 0
    for vertex in mesh.influences:
        wx = wy = 0.0
        for inf in vertex:
            x, y = inf.x, inf.y
            if deform:
                x, y = x + deform[f], y + deform[f + 1]
            f += 2
            px, py = _apply(bone_worlds[inf.bone], x, y)
            wx += px * inf.weight
            wy += py * inf.weight
        out.append((wx, wy))
    return out


def key_offsets(key: dict[str, Any], length: int) -> list[float]:
    values = [0.0] * length
    start = int(key.get("offset", 0))
    for i, v in enumerate(key.get("vertices", [])):
        if 0 <= start + i < length:
            values[start + i] = v
    return values


def encode_key(offsets: list[float]) -> dict[str, Any]:
    """Trim leading/trailing zeros into "offset" + "vertices", like Spine's exporter."""
    nonzero = [i for i, v in enumerate(offsets) if abs(v) > 1e-6]
    if not nonzero:
        return {}
    first, last = nonzero[0], nonzero[-1]
    out: dict[str, Any] = {}
    if first:
        out["offset"] = first
    out["vertices"] = [round(v, 4) + 0.0 for v in offsets[first:last + 1]]  # + 0.0 turns -0.0 into 0.0
    return out
