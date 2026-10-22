"""Auto-rig helpers: bone chains along a path and one-click mesh rigging.

A chain is laid along a polyline in world space, split into equal-length bones, each child
continuing from its parent's tip. The polyline can come from a path attachment (its bezier curve),
or be a straight line out of a bone's tip. "Rig mesh" picks the bones that reach the mesh and binds
it to them with the same distance weights as Bind.
"""

from __future__ import annotations

import math

from . import affine, mesh_ops
from .document import Document, EditorError

Point = tuple[float, float]


def resample(points: list[Point], count: int) -> list[Point]:
    """count + 1 points spaced evenly along the polyline (its ends kept)."""
    if count < 1:
        raise EditorError("A chain needs at least one bone")
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 1e-9:
        raise EditorError("The path has no length")
    out = [points[0]]
    seg, walked = 0, 0.0
    for k in range(1, count):
        target = total * k / count
        while walked + lengths[seg] < target:
            walked += lengths[seg]
            seg += 1
        f = (target - walked) / lengths[seg] if lengths[seg] else 0
        (ax, ay), (bx, by) = points[seg], points[seg + 1]
        out.append((ax + (bx - ax) * f, ay + (by - ay) * f))
    out.append(points[-1])
    return out


def path_points(doc: Document, slot: str, name: str, samples: int = 24) -> list[Point]:
    """The path attachment's curve in world space (setup pose), as a polyline."""
    att = doc.attachments(slot).get(name)
    if not att or att.get("type") != "path":
        raise EditorError(f"'{name}' is not a path attachment")
    verts = att.get("vertices", [])
    if len(verts) != 2 * att.get("vertexCount", len(verts) // 2):
        raise EditorError("Weighted paths are not supported here; unbind the path first")
    world = doc.evaluator("").bone_world(0)[doc.slot(slot)["bone"]]
    pts = [affine.apply(world, verts[i], verts[i + 1]) for i in range(0, len(verts), 2)]
    # groups of (in handle, point, out handle); a curve runs point i -> point i+1
    groups = [pts[i:i + 3] for i in range(0, len(pts) - 2, 3)]
    if att.get("closed"):
        groups.append(groups[0])
    out = [groups[0][1]]
    for g0, g1 in zip(groups, groups[1:]):
        p0, c0, c1, p1 = g0[1], g0[2], g1[0], g1[1]
        for s in range(1, samples + 1):
            t = s / samples
            u = 1 - t
            out.append(tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                             for a, b, c, d in zip(p0, c0, c1, p1)))
    return out


def bone_chain(doc: Document, parent: str, points: list[Point], count: int, name: str = "chain",
               record: bool = True) -> list[str]:
    """Add `count` bones under `parent` along the world polyline. Returns their names, top first."""
    doc.bone(parent)
    joints = resample(points, count)
    if record:
        doc.checkpoint(f"Bone chain {name}")
    world = doc.evaluator("").bone_world(0)
    parent_world = world[parent]
    names = []
    existing = {b["name"] for b in doc.bones}
    for i in range(count):
        (ax, ay), (bx, by) = joints[i], joints[i + 1]
        inv = affine.invert(parent_world)
        lx, ly = affine.apply(inv, ax, ay)
        dx, dy = affine.apply_vector(inv, bx - ax, by - ay)
        bone = doc.unique_name(f"{name}{i + 1}", existing)
        existing.add(bone)
        doc.add_bone(parent, bone, x=round(lx, 3), y=round(ly, 3), rotation=round(math.degrees(math.atan2(dy, dx)), 3),
                     length=round(math.hypot(dx, dy), 3), record=False)
        names.append(bone)
        parent = bone
        parent_world = doc.evaluator("").bone_world(0)[bone]
    doc.notify()
    return names


def chain_from_bone(doc: Document, bone: str, count: int, length: float, curl: float = 0.0,
                    record: bool = True) -> list[str]:
    """A chain continuing out of the bone's tip, `length` long in all; curl bends it (degrees per bone)."""
    world = doc.evaluator("").bone_world(0)[bone]
    x, y = affine.apply(world, doc.bone(bone).get("length", 0), 0)
    angle = math.radians(affine.world_rotation(world))
    step = length / count
    points = [(x, y)]
    for _ in range(count):
        x, y = x + step * math.cos(angle), y + step * math.sin(angle)
        points.append((x, y))
        angle += math.radians(curl)
    return bone_chain(doc, bone, points, count, f"{bone}_", record)


def bones_reaching(doc: Document, slot: str, name: str, margin: float = 0.1) -> list[str]:
    """Bones whose segment passes through the mesh's bounds (grown by `margin` of their size)."""
    model = mesh_ops.load_model(doc, slot, name)
    xs, ys = [p[0] for p in model.positions], [p[1] for p in model.positions]
    pad = margin * max(max(xs) - min(xs), max(ys) - min(ys))
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    world = doc.evaluator("").bone_world(0)
    found = []
    for b in doc.bones:
        if b["name"] == doc.root_name:
            continue
        m = world[b["name"]]
        ax, ay = affine.apply(m, 0, 0)
        bx, by = affine.apply(m, b.get("length", 0), 0)
        if any(x0 <= ax + (bx - ax) * f <= x1 and y0 <= ay + (by - ay) * f <= y1 for f in (0, .25, .5, .75, 1)):
            found.append(b["name"])
    return found or [doc.slot(slot)["bone"]]


def rig_mesh(doc: Document, slot: str, name: str, record: bool = True) -> list[str]:
    """Bind the mesh to the bones that reach it, weighted by distance. Returns those bones."""
    bones = bones_reaching(doc, slot, name)
    mesh_ops.bind(doc, slot, name, bones, record=record)
    return bones
