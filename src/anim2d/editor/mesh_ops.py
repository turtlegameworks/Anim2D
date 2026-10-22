"""Mesh editing: convert regions to meshes, move vertices, weights (bind/unbind/edit), deform keys.

All weighting changes go through one path (_rebuild): every vertex's setup-pose world
position, and every deform key's per-vertex world delta, are computed first; then the
mesh is re-encoded for the new weighting. So a mesh never jumps when it is bound,
unbound or reweighted, and existing deform keys keep their shape.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from PIL import Image

from ..preview.mesh import Influence, encode_key, encode_weighted, key_offsets, parse_vertices
from ..preview.triangulate import TriangulationError, barycentric, triangulate
from . import affine
from .document import EditorError, KEY_EPS, _r

if TYPE_CHECKING:
    from .document import Document

MAX_INFLUENCES = 4


def _mesh(doc: "Document", slot: str, name: str) -> dict[str, Any]:
    att = doc.attachments(slot).get(name)
    if att is None:
        raise EditorError(f"Slot '{slot}' has no attachment '{name}'")
    if att.get("type") != "mesh" or "parent" in att:
        raise EditorError(f"'{name}' is not an editable mesh")
    return att


def _setup_world(doc: "Document") -> dict[str, affine.Affine]:
    return doc.evaluator("").bone_world(0)


def _deform_keys(doc: "Document", slot: str, name: str) -> list[tuple[str, list[dict[str, Any]]]]:
    found = []
    for anim_name, anim in doc.data["animations"].items():
        keys = anim.get("deform", {}).get(doc.edit_skin, {}).get(slot, {}).get(name)
        if keys:
            found.append((anim_name, keys))
    return found


# --- creating meshes ---------------------------------------------------------------------

def convert_to_mesh(doc: "Document", slot: str, name: str, columns: int = 2, rows: int = 2,
                    record: bool = True) -> None:
    """Replace a region attachment with a grid mesh that looks identical (same name, so keys still apply)."""
    att = doc.attachments(slot).get(name)
    if att is None or att.get("type", "region") != "region":
        raise EditorError(f"'{name}' is not a region attachment")
    if not (1 <= columns <= 30 and 1 <= rows <= 30):
        raise EditorError("Columns and rows must be between 1 and 30")
    path = att.get("path", att.get("name", name))
    file = doc.image_file(path)
    if file is not None:
        with Image.open(file) as img:
            img_w, img_h = img.size
    else:
        img_w, img_h = att["width"], att["height"]
    w, h = att["width"], att["height"]
    place = affine.local(att.get("x", 0), att.get("y", 0), att.get("rotation", 0),
                         att.get("scaleX", 1), att.get("scaleY", 1))
    # Grid points, ordered hull first (clockwise ring), then interior points.
    grid = [(c, r) for r in range(rows + 1) for c in range(columns + 1)]
    ring = [(c, 0) for c in range(columns + 1)] + [(columns, r) for r in range(1, rows + 1)] + \
           [(c, rows) for c in range(columns - 1, -1, -1)] + [(0, r) for r in range(rows - 1, 0, -1)]
    order = ring + [p for p in grid if p not in ring]
    index = {p: i for i, p in enumerate(order)}
    uvs, vertices = [], []
    for c, r in order:
        u, v = c / columns, r / rows
        uvs += [_r(u), _r(v)]
        x, y = affine.apply(place, u * w - w / 2, h / 2 - v * h)
        vertices += [_r(x), _r(y)]
    triangles = []
    for r in range(rows):
        for c in range(columns):
            a, b, cc, d = index[(c, r)], index[(c + 1, r)], index[(c + 1, r + 1)], index[(c, r + 1)]
            triangles += [a, b, cc, a, cc, d]
    hull = len(ring)
    mesh = {"type": "mesh", "path": path, "uvs": uvs, "triangles": triangles, "vertices": vertices,
            "hull": hull, "edges": [v for i in range(hull) for v in (i * 2, ((i + 1) % hull) * 2)],
            "width": img_w, "height": img_h}
    if "color" in att:
        mesh["color"] = att["color"]
    if record:
        doc.checkpoint(f"Convert {name} to mesh")
    doc.skin[slot][name] = mesh
    doc.notify()


# --- geometry helpers --------------------------------------------------------------------

def vertex_world(doc: "Document", slot: str, name: str, animation: str | None = None,
                 time: float = 0.0) -> list[tuple[float, float]]:
    """World positions of the mesh vertices (setup pose, or posed with deform at time)."""
    return doc.evaluator(animation or "").mesh_world_vertices(slot, name, time)


def _vec_inverse(m: affine.Affine, dx: float, dy: float) -> tuple[float, float]:
    return affine.apply_vector(affine.invert(m), dx, dy)


@dataclass
class MeshModel:
    """A mesh in editable form: everything is per vertex and in world space (setup pose), so vertices
    can be inserted, removed or reweighted and then encoded back without anything moving."""
    positions: list[tuple[float, float]]
    uvs: list[tuple[float, float]]
    weights: list[dict[int, float]] | None  # bone index -> weight; None = unweighted
    hull: int
    triangles: list[int]
    keys: list[tuple[dict[str, Any], list[tuple[float, float]]]] = field(default_factory=list)


def load_model(doc: "Document", slot: str, name: str) -> MeshModel:
    att = _mesh(doc, slot, name)
    mesh = parse_vertices(att)
    world = _setup_world(doc)
    bone_worlds = [world[b["name"]] for b in doc.bones]
    slot_world = world[doc.slot(slot)["bone"]]

    def world_deltas(offsets: list[float]) -> list[tuple[float, float]]:
        """Per-vertex world delta a deform key produces, at the setup pose."""
        out, f = [], 0
        for i in range(mesh.count):
            if not mesh.weighted:
                out.append(affine.apply_vector(slot_world, offsets[2 * i], offsets[2 * i + 1]))
                continue
            dx = dy = 0.0
            for inf in mesh.influences[i]:
                vx, vy = affine.apply_vector(bone_worlds[inf.bone], offsets[f], offsets[f + 1])
                dx += vx * inf.weight
                dy += vy * inf.weight
                f += 2
            out.append((dx, dy))
        return out

    uvs = att["uvs"]
    return MeshModel(
        positions=vertex_world(doc, slot, name),
        uvs=[(uvs[i], uvs[i + 1]) for i in range(0, len(uvs), 2)],
        weights=[{inf.bone: inf.weight for inf in v} for v in mesh.influences] if mesh.weighted else None,
        hull=int(att.get("hull", mesh.count)),
        triangles=list(att["triangles"]),
        keys=[(k, world_deltas(key_offsets(k, mesh.deform_length)))
              for _, keys in _deform_keys(doc, slot, name) for k in keys],
    )


def _image_size(doc: "Document", att: dict[str, Any]) -> tuple[float, float]:
    file = doc.image_file(att.get("path", ""))
    if file is not None:
        with Image.open(file) as img:
            return img.size
    return float(att.get("width") or 1), float(att.get("height") or 1)


def store_model(doc: "Document", slot: str, name: str, model: MeshModel, retriangulate: bool) -> None:
    """Encode the model into the attachment and its deform keys. Computes everything first,
    so an error (e.g. an outline that crosses itself) leaves the attachment untouched."""
    att = _mesh(doc, slot, name)
    world = _setup_world(doc)
    bone_worlds = [world[b["name"]] for b in doc.bones]
    slot_world = world[doc.slot(slot)["bone"]]
    triangles = model.triangles
    if retriangulate:
        w, h = _image_size(doc, att)
        try:
            triangles = [v for t in triangulate([(u * w, v * h) for u, v in model.uvs], model.hull) for v in t]
        except TriangulationError as exc:
            raise EditorError(f"Cannot triangulate the mesh: {exc}") from None
    if model.weights is None:
        vertices = [_r(v) for x, y in model.positions
                    for v in affine.apply(affine.invert(slot_world), x, y)]
    else:
        influences = []
        for (x, y), vertex in zip(model.positions, model.weights):
            total = sum(w for w in vertex.values() if w > 0)
            if total <= 0:
                raise EditorError("Every vertex needs at least one bone with weight above zero")
            influences.append([Influence(bone, *affine.apply(affine.invert(bone_worlds[bone]), x, y), w / total)
                               for bone, w in vertex.items() if w > 0])
        vertices = encode_weighted(influences)
    encoded_keys = []
    for key, deltas in model.keys:
        offsets: list[float] = []
        for i, (dx, dy) in enumerate(deltas):
            if model.weights is None:
                offsets += list(_vec_inverse(slot_world, dx, dy))
            else:
                for bone, w in model.weights[i].items():
                    if w > 0:
                        offsets += list(_vec_inverse(bone_worlds[bone], dx, dy))
        encoded_keys.append((key, encode_key(offsets)))
    att["vertices"] = vertices
    att["uvs"] = [_r(v) for uv in model.uvs for v in uv]
    att["triangles"] = triangles
    att["hull"] = model.hull
    att["edges"] = [v for i in range(model.hull) for v in (i * 2, ((i + 1) % model.hull) * 2)]
    for key, encoded in encoded_keys:
        for f in ("offset", "vertices"):
            key.pop(f, None)
        key.update(encoded)


def _rebuild(doc: "Document", slot: str, name: str, spec: list[list[tuple[int, float]]] | None) -> None:
    """Re-encode the mesh for a new weighting. spec: per vertex [(bone index, weight), ...],
    or None for unweighted (vertices in the slot bone's space). Deform keys are converted."""
    model = load_model(doc, slot, name)
    model.weights = None if spec is None else [{bone: w for bone, w in vertex} for vertex in spec]
    store_model(doc, slot, name, model, retriangulate=False)


# --- weights -----------------------------------------------------------------------------

def _distance_to_bone(world: affine.Affine, length: float, x: float, y: float) -> float:
    ox, oy = world[4], world[5]
    tx, ty = affine.apply(world, length, 0) if length > 0 else (ox, oy)
    abx, aby = tx - ox, ty - oy
    l2 = abx * abx + aby * aby
    t = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, ((x - ox) * abx + (y - oy) * aby) / l2))
    return math.hypot(x - (ox + t * abx), y - (oy + t * aby))


def bind(doc: "Document", slot: str, name: str, bone_names: list[str], record: bool = True) -> None:
    """Weight the mesh to bones automatically (inverse-square distance to each bone, up to 4 per vertex)."""
    _mesh(doc, slot, name)
    if not bone_names:
        raise EditorError("Choose at least one bone to bind to")
    world = _setup_world(doc)
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    for b in bone_names:
        doc.bone(b)
    positions = vertex_world(doc, slot, name)
    spec = []
    for x, y in positions:
        scored = []
        for b in bone_names:
            d = _distance_to_bone(world[b], doc.bone(b).get("length", 0), x, y)
            scored.append((1.0 / (d * d + 1.0), index[b]))
        scored.sort(reverse=True)
        top = scored[:MAX_INFLUENCES]
        total = sum(s for s, _ in top)
        vertex = [(bone, s / total) for s, bone in top if s / total >= 0.01]
        spec.append(vertex or [(top[0][1], 1.0)])
    if record:
        doc.checkpoint(f"Bind {name}")
    _rebuild(doc, slot, name, spec)
    remember_bind_pose(doc, slot, name)
    doc.notify()


def unbind(doc: "Document", slot: str, name: str, record: bool = True) -> None:
    att = _mesh(doc, slot, name)
    if not parse_vertices(att).weighted:
        raise EditorError(f"'{name}' has no weights")
    if record:
        doc.checkpoint(f"Unbind {name}")
    _rebuild(doc, slot, name, None)
    doc.notify()


def vertex_weights(doc: "Document", slot: str, name: str, vertex: int) -> dict[str, float]:
    mesh = parse_vertices(_mesh(doc, slot, name))
    if not mesh.weighted:
        return {doc.slot(slot)["bone"]: 1.0}
    bones = [b["name"] for b in doc.bones]
    return {bones[inf.bone]: inf.weight for inf in mesh.influences[vertex]}


def adjust_weight(weights: dict[str, float], bone: str, value: float) -> dict[str, float]:
    """Set one bone's weight exactly; the other bones share the remainder in their current ratio."""
    value = max(0.0, min(1.0, value))
    others = {b: w for b, w in weights.items() if b != bone}
    total = sum(others.values())
    if total <= 1e-9:
        return {bone: 1.0} if value > 0 or not others else {**{b: 1 / len(others) for b in others}}
    return {bone: value, **{b: w / total * (1 - value) for b, w in others.items()}}


def set_vertex_weights(doc: "Document", slot: str, name: str, vertex: int, weights: dict[str, float],
                       record: bool = True) -> None:
    """Set one vertex's bone weights (normalized). Binds the mesh first if it has no weights."""
    att = _mesh(doc, slot, name)
    mesh = parse_vertices(att)
    if not 0 <= vertex < mesh.count:
        raise EditorError(f"Vertex {vertex} does not exist")
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    weights = {b: w for b, w in weights.items() if w > 0}
    for b in weights:
        doc.bone(b)
    if not weights:
        raise EditorError("A vertex needs at least one bone with weight above zero")
    if len(weights) > 8:
        raise EditorError("Use at most 8 bones per vertex")
    slot_bone = index[doc.slot(slot)["bone"]]
    spec = [[(inf.bone, inf.weight) for inf in v] for v in mesh.influences] if mesh.weighted else \
        [[(slot_bone, 1.0)] for _ in range(mesh.count)]
    spec[vertex] = [(index[b], w) for b, w in weights.items()]
    if record:
        doc.checkpoint(f"Weights {name}[{vertex}]")
    _rebuild(doc, slot, name, spec)
    doc.notify()


# --- moving vertices ---------------------------------------------------------------------

def move_vertices_setup(doc: "Document", slot: str, name: str, targets: dict[int, tuple[float, float]],
                        record: bool = True) -> None:
    """Move vertices (setup pose) to world positions. UVs stay, so the image stretches."""
    att = _mesh(doc, slot, name)
    mesh = parse_vertices(att)
    world = _setup_world(doc)
    if record:
        doc.checkpoint(f"Move vertices {name}")
    if not mesh.weighted:
        slot_world = world[doc.slot(slot)["bone"]]
        verts = list(att["vertices"])
        for i, (x, y) in targets.items():
            lx, ly = affine.apply(affine.invert(slot_world), x, y)
            verts[2 * i], verts[2 * i + 1] = _r(lx), _r(ly)
        att["vertices"] = verts
    else:
        bones = [b["name"] for b in doc.bones]
        influences = [list(v) for v in mesh.influences]
        for i, (x, y) in targets.items():
            influences[i] = [Influence(inf.bone, *affine.apply(affine.invert(world[bones[inf.bone]]), x, y),
                                       inf.weight) for inf in influences[i]]
        att["vertices"] = encode_weighted(influences)
    doc.notify()


def set_deform_key(doc: "Document", animation: str, slot: str, name: str, time: float,
                   targets: dict[int, tuple[float, float]] | None = None, reset: bool = False,
                   record: bool = True) -> None:
    """Key the mesh shape at time. Unmoved vertices keep their current (interpolated) deform;
    targets move vertices to world positions in the current pose. reset=True keys the setup shape."""
    att = _mesh(doc, slot, name)
    anim = doc.animation(animation)
    if time < 0:
        raise EditorError("Key time cannot be negative")
    mesh = parse_vertices(att)
    ev = doc.evaluator(animation)
    world = ev.bone_world(time)
    offsets = [0.0] * mesh.deform_length if reset else \
        (ev.deform_offsets(slot, name, time, mesh.deform_length) or [0.0] * mesh.deform_length)
    current = ev.mesh_world_vertices(slot, name, time, world)
    if targets:
        bones = [b["name"] for b in doc.bones]
        slot_world = world[doc.slot(slot)["bone"]]
        f = 0
        for i in range(mesh.count):
            n = len(mesh.influences[i]) if mesh.weighted else 1
            if i in targets:
                dx, dy = targets[i][0] - current[i][0], targets[i][1] - current[i][1]
                for j in range(n):
                    m = world[bones[mesh.influences[i][j].bone]] if mesh.weighted else slot_world
                    ox, oy = _vec_inverse(m, dx, dy)
                    offsets[f + 2 * j] += ox
                    offsets[f + 2 * j + 1] += oy
            f += 2 * n
    if record:
        doc.checkpoint(f"Key deform {name}")
    keys = anim.setdefault("deform", {}).setdefault(doc.edit_skin, {}).setdefault(slot, {}).setdefault(name, [])
    existing = [k for k in keys if abs(k.get("time", 0) - time) < KEY_EPS]
    curve = {f: existing[0][f] for f in ("curve", "c2", "c3", "c4") if existing and f in existing[0]}
    keys[:] = [k for k in keys if abs(k.get("time", 0) - time) >= KEY_EPS]
    keys.append({"time": _r(time), **encode_key(offsets), **curve})
    keys.sort(key=lambda k: k.get("time", 0))
    doc.notify()


def remap_weight_indices(doc: "Document", old_order: list[str]) -> None:
    """After the bone list changed (insert/reorder), rewrite weighted bone indices by name."""
    new_index = {b["name"]: i for i, b in enumerate(doc.bones)}
    for skin in doc.data["skins"]:
        for entries in skin.get("attachments", {}).values():
            for att in entries.values():
                if att.get("type") != "mesh" or "parent" in att:
                    continue
                mesh = parse_vertices(att)
                if not mesh.weighted:
                    continue
                att["vertices"] = encode_weighted([[Influence(new_index[old_order[inf.bone]], inf.x, inf.y, inf.weight)
                                                    for inf in v] for v in mesh.influences])


# --- topology: adding and removing vertices ---------------------------------------------

def _blend_weights(parts: list[tuple[float, dict[int, float]]]) -> dict[int, float]:
    mixed: dict[int, float] = {}
    for factor, weights in parts:
        for bone, w in weights.items():
            mixed[bone] = mixed.get(bone, 0.0) + factor * w
    top = sorted(mixed.items(), key=lambda kv: -kv[1])[:MAX_INFLUENCES]
    total = sum(w for _, w in top if w > 0) or 1.0
    return {bone: w / total for bone, w in top if w / total >= 0.001}


def _interpolate(model: MeshModel, parts: list[tuple[float, int]], at: int) -> None:
    """Insert a vertex at index `at` whose attributes blend the given (factor, vertex) pairs."""
    def mix(values):
        return (sum(f * values[i][0] for f, i in parts), sum(f * values[i][1] for f, i in parts))
    position = mix(model.positions)
    uv = mix(model.uvs)
    model.positions.insert(at, position)
    model.uvs.insert(at, (min(1.0, max(0.0, uv[0])), min(1.0, max(0.0, uv[1]))))
    if model.weights is not None:
        model.weights.insert(at, _blend_weights([(f, model.weights[i]) for f, i in parts]))
    for _, deltas in model.keys:
        deltas.insert(at, mix(deltas))


def _segment_distance(p, a, b) -> tuple[float, float]:
    """(distance from p to segment ab, position t along it)."""
    abx, aby = b[0] - a[0], b[1] - a[1]
    l2 = abx * abx + aby * aby
    t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / l2))
    return math.hypot(p[0] - (a[0] + t * abx), p[1] - (a[1] + t * aby)), t


def add_vertex(doc: "Document", slot: str, name: str, point: tuple[float, float], edge_tolerance: float = 4.0,
               record: bool = True) -> int:
    """Add a vertex at a world point (setup pose). On or near the outline it joins the hull;
    inside it becomes an interior vertex. Its UV, weights and deform offsets are interpolated
    from its neighbours, so nothing visibly changes. Returns the new vertex index."""
    model = load_model(doc, slot, name)
    best = None
    for i in range(model.hull):
        j = (i + 1) % model.hull
        d, t = _segment_distance(point, model.positions[i], model.positions[j])
        if d <= edge_tolerance and (best is None or d < best[0]):
            best = (d, i, j, t)
    if best is not None:
        _, i, j, t = best
        if t < 1e-3 or t > 1 - 1e-3:
            raise EditorError("A vertex already exists there")
        at = i + 1  # between hull vertices i and j in the ring (j may be 0: append at the ring's end)
        _interpolate(model, [(1 - t, i), (t, j)], at)
        model.hull += 1
    else:
        tris = [model.triangles[k:k + 3] for k in range(0, len(model.triangles), 3)]
        for a, b, c in tris:
            weights = barycentric(point, model.positions[a], model.positions[b], model.positions[c])
            if weights and min(weights) >= -1e-9:
                at = len(model.positions)
                _interpolate(model, [(weights[0], a), (weights[1], b), (weights[2], c)], at)
                break
        else:
            raise EditorError("Click inside the mesh or on its outline to add a vertex")
    if record:
        doc.checkpoint(f"Add vertex to {name}")
    try:
        store_model(doc, slot, name, model, retriangulate=True)
    except EditorError:
        if record:
            doc.undo()
            doc._redo.clear()
        raise
    doc.notify()
    return at


def remove_vertices(doc: "Document", slot: str, name: str, indices: set[int], record: bool = True) -> None:
    model = load_model(doc, slot, name)
    if not indices:
        return
    if any(not 0 <= i < len(model.positions) for i in indices):
        raise EditorError("Some selected vertices do not exist")
    hull_left = model.hull - sum(1 for i in indices if i < model.hull)
    if hull_left < 3:
        raise EditorError("A mesh outline needs at least 3 vertices")
    keep = [i for i in range(len(model.positions)) if i not in indices]
    model.positions = [model.positions[i] for i in keep]
    model.uvs = [model.uvs[i] for i in keep]
    if model.weights is not None:
        model.weights = [model.weights[i] for i in keep]
    model.keys = [(k, [d[i] for i in keep]) for k, d in model.keys]
    model.hull = hull_left
    if record:
        doc.checkpoint(f"Delete vertices of {name}")
    try:
        store_model(doc, slot, name, model, retriangulate=True)
    except EditorError:
        if record:
            doc.undo()
            doc._redo.clear()
        raise
    doc.notify()


def retriangulate(doc: "Document", slot: str, name: str, record: bool = True) -> None:
    model = load_model(doc, slot, name)
    if record:
        doc.checkpoint(f"Re-triangulate {name}")
    try:
        store_model(doc, slot, name, model, retriangulate=True)
    except EditorError:
        if record:
            doc.undo()
            doc._redo.clear()
        raise
    doc.notify()


# --- UV editing --------------------------------------------------------------------------

def uv_mapper(doc: "Document", slot: str, name: str) -> Callable[[float, float], tuple[float, float]]:
    """World point (setup pose) -> UV, piecewise-linear over the mesh as it is now. Points outside
    the mesh use the nearest triangle's mapping."""
    model = load_model(doc, slot, name)
    tris = [tuple(model.triangles[k:k + 3]) for k in range(0, len(model.triangles), 3)]
    pos, uvs = model.positions, model.uvs

    def mapper(x: float, y: float) -> tuple[float, float]:
        best, best_score = None, -math.inf
        for a, b, c in tris:
            w = barycentric((x, y), pos[a], pos[b], pos[c])
            if w is None:
                continue
            score = min(w)  # >= 0 inside; the least negative is the nearest
            if score > best_score:
                best, best_score = (w, (a, b, c)), score
        if best is None:
            return 0.5, 0.5
        (wa, wb, wc), (a, b, c) = best
        u = wa * uvs[a][0] + wb * uvs[b][0] + wc * uvs[c][0]
        v = wa * uvs[a][1] + wb * uvs[b][1] + wc * uvs[c][1]
        return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
    return mapper


def drag_vertices(doc: "Document", slot: str, name: str, targets: dict[int, tuple[float, float]], mode: str,
                  mapper: Callable[[float, float], tuple[float, float]] | None = None, record: bool = True) -> None:
    """Setup-mode vertex drag. mode:
      "reshape": move vertices, keep UVs (the image stretches)
      "mesh":    move vertices and their UVs together (the image stays put; like editing the mesh)
      "uv":      keep vertices, move UVs (the image slides under the vertices)
    mapper must come from uv_mapper() taken before the drag started."""
    if mode not in ("reshape", "mesh", "uv"):
        raise EditorError(f"Unknown drag mode '{mode}'")
    if mode == "reshape":
        move_vertices_setup(doc, slot, name, targets, record=record)
        return
    if mapper is None:
        raise EditorError("UV editing needs a mapper from before the drag")
    att = _mesh(doc, slot, name)
    if record:
        doc.checkpoint(f"Edit UVs of {name}")
    uvs = list(att["uvs"])
    for i, (x, y) in targets.items():
        u, v = mapper(x, y)
        uvs[2 * i], uvs[2 * i + 1] = _r(u), _r(v)
    att["uvs"] = uvs
    if mode == "mesh":
        move_vertices_setup(doc, slot, name, targets, record=False)
    doc.notify()


def bones_used_by_weights(doc: "Document") -> dict[str, list[str]]:
    """bone name -> mesh attachments ("slot/attachment") weighted to it."""
    names = [b["name"] for b in doc.bones]
    used: dict[str, list[str]] = {}
    for skin in doc.data["skins"]:
        for slot, entries in skin.get("attachments", {}).items():
            for att_name, att in entries.items():
                if att.get("type") == "mesh" and "parent" not in att:
                    mesh = parse_vertices(att)
                    for v in mesh.influences if mesh.weighted else ():
                        for inf in v:
                            used.setdefault(names[inf.bone], [])
                            if f"{slot}/{att_name}" not in used[names[inf.bone]]:
                                used[names[inf.bone]].append(f"{slot}/{att_name}")
    return used


# --- weight painting ---------------------------------------------------------------------

PAINT_MODES = ("add", "subtract", "replace", "smooth")
MAX_PAINTED_INFLUENCES = 8


def _neighbours(model: MeshModel) -> list[set[int]]:
    out: list[set[int]] = [set() for _ in model.positions]
    for i in range(0, len(model.triangles) - 2, 3):
        a, b, c = model.triangles[i:i + 3]
        out[a] |= {b, c}
        out[b] |= {a, c}
        out[c] |= {a, b}
    return out


def _set_bone_weight(weights: dict[int, float], bone: int, value: float,
                     locked: frozenset[int] = frozenset()) -> dict[int, float]:
    """Give `bone` this weight; the other unlocked bones share the rest in their current ratio and
    locked bones keep theirs. When no unlocked bone can take or give weight, the vertex keeps what
    it has (a vertex's only bone cannot lose its weight)."""
    kept = {b: w for b, w in weights.items() if b in locked and b != bone}
    free = 1.0 - sum(kept.values())
    value = max(0.0, min(free, value))
    others = {b: w for b, w in weights.items() if b != bone and b not in locked and w > 0}
    total = sum(others.values())
    if total <= 1e-9:
        return {**kept, bone: free} if free > 1e-9 else dict(weights)
    out = {**kept, **{b: w / total * (free - value) for b, w in others.items()}}
    if value > 0:
        out[bone] = value
    return out


def _tidy(weights: dict[int, float]) -> dict[int, float]:
    kept = sorted(((w, b) for b, w in weights.items() if w >= 0.001), reverse=True)[:MAX_PAINTED_INFLUENCES]
    total = sum(w for w, _ in kept)
    return {b: w / total for w, b in kept}


def _weights_of(doc: "Document", slot: str, model: MeshModel) -> list[dict[int, float]]:
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    return [dict(w) for w in model.weights] if model.weights else \
        [{index[doc.slot(slot)["bone"]]: 1.0} for _ in model.positions]


def _store_weights(doc: "Document", slot: str, name: str, model: MeshModel, weights: list[dict[int, float]],
                   label: str, record: bool) -> None:
    model.weights = weights
    if record:
        doc.checkpoint(label)
    store_model(doc, slot, name, model, retriangulate=False)
    doc.notify()


def _bone_indices(doc: "Document", bones) -> frozenset[int]:
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    return frozenset(index[b] for b in (bones or ()) if b in index)


def falloff(distance: float, radius: float, feather: float) -> float:
    """Brush strength at a distance: full inside the hard core (1 - feather of the radius), then a
    smooth fade to 0 at the edge. feather 0 = hard brush, 1 = soft all the way from the centre."""
    if radius <= 0:
        return 1.0
    t = distance / radius
    if t >= 1:
        return 0.0
    core = 1.0 - max(0.0, min(1.0, feather))
    if t <= core:
        return 1.0
    u = (t - core) / (1 - core)
    return (1 - u * u) ** 2


def paint_weights(doc: "Document", slot: str, name: str, bone: str, center: tuple[float, float], radius: float,
                  strength: float = 0.5, mode: str = "add", record: bool = True, feather: float = 1.0,
                  only: set[int] | None = None, locked=()) -> int:
    """Brush the mesh's weights for `bone` around a world point (setup pose).

    add/subtract move the weight towards 1/0 by strength, replace towards strength itself, smooth
    blends each vertex's weights with its neighbours'. Feather softens the brush edge (see falloff).
    only: change just these vertices. locked: bones whose weights stay as they are.
    An unweighted mesh is weighted to its slot bone first. Returns how many vertices changed."""
    if mode not in PAINT_MODES:
        raise EditorError(f"Paint mode must be one of {', '.join(PAINT_MODES)}")
    doc.bone(bone)
    model = load_model(doc, slot, name)
    target = _bone_indices(doc, [bone])
    target = next(iter(target))
    locks = _bone_indices(doc, locked) - {target}
    weights = _weights_of(doc, slot, model)
    neighbours = _neighbours(model) if mode == "smooth" else None
    strength = max(0.0, min(1.0, strength))
    changed: dict[int, dict[int, float]] = {}
    for i, (x, y) in enumerate(model.positions):
        if only is not None and i not in only:
            continue
        f = falloff(math.hypot(x - center[0], y - center[1]), radius, feather)
        if f <= 0:
            continue
        current = weights[i]
        if mode == "smooth":
            new = _smoothed(current, [weights[n] for n in neighbours[i]], strength * f, locks)
        elif target in locks:
            continue
        else:
            w = current.get(target, 0.0)
            goal = {"add": 1.0, "subtract": 0.0, "replace": strength}[mode]
            amount = f if mode == "replace" else strength * f
            new = _set_bone_weight(current, target, w + (goal - w) * amount, locks)
        new = _tidy(new)
        if any(abs(new.get(b, 0) - current.get(b, 0)) > 1e-6 for b in set(new) | set(current)):
            changed[i] = new
    if not changed:
        return 0
    for i, new in changed.items():
        weights[i] = new
    _store_weights(doc, slot, name, model, weights, f"Paint weights {name}", record)
    return len(changed)


def _smoothed(current: dict[int, float], around: list[dict[int, float]], amount: float,
              locks: frozenset[int]) -> dict[int, float]:
    around = around or [current]
    bones = set(current).union(*around) - locks
    average = {b: sum(w.get(b, 0) for w in around) / len(around) for b in bones}
    new = {b: current.get(b, 0) + (average[b] - current.get(b, 0)) * amount for b in bones}
    free = 1.0 - sum(w for b, w in current.items() if b in locks)
    total = sum(new.values())
    out = {b: w for b, w in current.items() if b in locks}
    if total > 1e-9:
        out.update({b: w / total * free for b, w in new.items()})
    return out


def smooth_weights(doc: "Document", slot: str, name: str, strength: float = 1.0, only: set[int] | None = None,
                   locked=(), record: bool = True) -> int:
    """One smoothing pass over the whole mesh (or the given vertices)."""
    model = load_model(doc, slot, name)
    weights = _weights_of(doc, slot, model)
    neighbours = _neighbours(model)
    locks = _bone_indices(doc, locked)
    new = [_tidy(_smoothed(w, [weights[n] for n in neighbours[i]], strength, locks))
           if only is None or i in only else w for i, w in enumerate(weights)]
    changed = sum(1 for a, b in zip(weights, new) if any(abs(a.get(k, 0) - b.get(k, 0)) > 1e-6 for k in set(a) | set(b)))
    if changed:
        _store_weights(doc, slot, name, model, new, f"Smooth weights {name}", record)
    return changed


def auto_weights(doc: "Document", slot: str, name: str, bones: list[str], only: set[int] | None = None,
                 locked=(), record: bool = True) -> int:
    """Weights from the distance to each bone (as Bind does), for all or the given vertices."""
    if not bones:
        raise EditorError("Bind the mesh to bones first")
    model = load_model(doc, slot, name)
    weights = _weights_of(doc, slot, model)
    world = _setup_world(doc)
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    locks = _bone_indices(doc, locked)
    count = 0
    for i, (x, y) in enumerate(model.positions):
        if only is not None and i not in only:
            continue
        scored = sorted(((1.0 / (_distance_to_bone(world[b], doc.bone(b).get("length", 0), x, y) ** 2 + 1.0), index[b])
                         for b in bones if index[b] not in locks), reverse=True)[:MAX_INFLUENCES]
        free = 1.0 - sum(w for b, w in weights[i].items() if b in locks)
        total = sum(s for s, _ in scored)
        if not scored or total <= 0:
            continue
        new = {b: w for b, w in weights[i].items() if b in locks}
        new.update({b: s / total * free for s, b in scored if s / total >= 0.01})  # as Bind does
        weights[i] = _tidy(new)
        count += 1
    if count:
        _store_weights(doc, slot, name, model, weights, f"Auto weights {name}", record)
    return count


def prune_weights(doc: "Document", slot: str, name: str, threshold: float = 0.05, max_bones: int | None = None,
                  record: bool = True) -> int:
    """Drop weights below the threshold (and beyond max_bones per vertex); the rest are renormalised."""
    model = load_model(doc, slot, name)
    weights = _weights_of(doc, slot, model)
    count = 0
    for i, w in enumerate(weights):
        ranked = sorted(w.items(), key=lambda kv: -kv[1])
        kept = [(b, v) for b, v in ranked if v >= threshold][:max_bones or None] or ranked[:1]
        total = sum(v for _, v in kept)
        new = {b: v / total for b, v in kept}
        if new.keys() != w.keys():
            weights[i] = new
            count += 1
    if count:
        _store_weights(doc, slot, name, model, weights, f"Prune weights {name}", record)
    return count


def remove_bone_weights(doc: "Document", slot: str, name: str, bone: str, record: bool = True) -> None:
    """Take a bone out of the mesh: its weight goes to the vertex's other bones, or to the nearest
    remaining bound bone for vertices that only had this one."""
    model = load_model(doc, slot, name)
    weights = _weights_of(doc, slot, model)
    gone = next(iter(_bone_indices(doc, [bone])))
    remaining = [b for b in bound_bones(doc, slot, name) if b != bone]
    if not remaining:
        raise EditorError("A weighted mesh needs at least one bone; use Unbind to remove all weights")
    world = _setup_world(doc)
    index = {b["name"]: i for i, b in enumerate(doc.bones)}
    for i, (x, y) in enumerate(model.positions):
        w = {b: v for b, v in weights[i].items() if b != gone}
        if not w:
            nearest = min(remaining, key=lambda b: _distance_to_bone(world[b], doc.bone(b).get("length", 0), x, y))
            w = {index[nearest]: 1.0}
        total = sum(w.values())
        weights[i] = {b: v / total for b, v in w.items()}
    doc.extra_bound_bones.get((doc.edit_skin, slot, name), set()).discard(bone)
    _store_weights(doc, slot, name, model, weights, f"Remove {bone} from {name}", record)


def bound_bones(doc: "Document", slot: str, name: str) -> list[str]:
    """Bones the mesh is bound to (with weight, or added by Bind with none yet), in skeleton order."""
    mesh = parse_vertices(_mesh(doc, slot, name))
    names = [b["name"] for b in doc.bones]
    used = {names[inf.bone] for v in mesh.influences for inf in v} if mesh.weighted else set()
    used |= {b for b in doc.extra_bound_bones.get((doc.edit_skin, slot, name), set()) if b in names}
    return [b for b in names if b in used]


def bind_more(doc: "Document", slot: str, name: str, bones: list[str], record: bool = True) -> None:
    """Spine's Bind: an unweighted mesh is weighted to the bones automatically; a weighted one gets
    them added to its bone list with no weight yet, ready to paint."""
    for b in bones:
        doc.bone(b)
    if not parse_vertices(_mesh(doc, slot, name)).weighted:
        bind(doc, slot, name, bones, record=record)
    else:
        doc.extra_bound_bones.setdefault((doc.edit_skin, slot, name), set()).update(bones)
        doc.notify()
    remember_bind_pose(doc, slot, name)


def remember_bind_pose(doc: "Document", slot: str, name: str) -> None:
    """The bones' setup transforms when the mesh was bound (editor memory, not saved in the file)."""
    doc.bind_poses[(doc.edit_skin, slot, name)] = _setup_world(doc)


def update_bindings(doc: "Document", slot: str, name: str, record: bool = True) -> None:
    """After bones were moved in Setup mode, put the mesh back where it was when it was bound and
    bind it to the bones where they are now (Spine's Update Bindings)."""
    pose = doc.bind_poses.get((doc.edit_skin, slot, name))
    if pose is None:
        raise EditorError("Bind the mesh first: Anim2D remembers the bone positions from the last Bind "
                          "or Update Bindings in this session")
    att = _mesh(doc, slot, name)
    mesh = parse_vertices(att)
    model = load_model(doc, slot, name)
    names = [b["name"] for b in doc.bones]
    positions = []
    for i in range(mesh.count):
        if mesh.weighted:
            x = y = 0.0
            for inf in mesh.influences[i]:
                m = pose.get(names[inf.bone])
                if m is None:
                    raise EditorError(f"Bone '{names[inf.bone]}' was added after the bind; bind again")
                px, py = affine.apply(m, inf.x, inf.y)
                x += px * inf.weight
                y += py * inf.weight
            positions.append((x, y))
        else:
            positions.append(affine.apply(pose[doc.slot(slot)["bone"]], *mesh.local[i]))
    model.positions = positions
    if record:
        doc.checkpoint(f"Update bindings {name}")
    store_model(doc, slot, name, model, retriangulate=False)
    remember_bind_pose(doc, slot, name)
    doc.notify()


def bone_weights(doc: "Document", slot: str, name: str, bone: str) -> list[float]:
    """Each vertex's weight for one bone (0..1): what the weight view colours."""
    mesh = parse_vertices(_mesh(doc, slot, name))
    names = [b["name"] for b in doc.bones]
    if not mesh.weighted:
        return [1.0 if doc.slot(slot)["bone"] == bone else 0.0] * mesh.count
    return [sum(inf.weight for inf in v if names[inf.bone] == bone) for v in mesh.influences]


def vertex_bone_weights(doc: "Document", slot: str, name: str) -> list[list[tuple[str, float]]]:
    """Per vertex: (bone, weight) pairs, heaviest first (for the weight pies)."""
    mesh = parse_vertices(_mesh(doc, slot, name))
    names = [b["name"] for b in doc.bones]
    if not mesh.weighted:
        return [[(doc.slot(slot)["bone"], 1.0)]] * mesh.count
    return [sorted(((names[inf.bone], inf.weight) for inf in v), key=lambda bw: -bw[1]) for v in mesh.influences]
