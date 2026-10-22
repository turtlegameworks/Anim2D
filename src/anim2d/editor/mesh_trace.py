"""Trace: build a mesh from an image's alpha, like Spine's mesh Trace.

1. The image's alpha is thresholded (and padded outwards a few pixels) on a grid of at most
   TRACE_GRID cells per side.
2. The outline of the largest opaque area is followed along pixel edges.
3. Douglas-Peucker simplifies it to the requested detail (in image pixels), without letting the
   outline cross itself.
4. Optional interior points on a grid, then the existing constrained Delaunay triangulation.

The mesh replaces a region attachment (or an unweighted mesh without deform keys) and maps the
image exactly as the region did, so nothing moves.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PIL import Image

from ..preview.triangulate import TriangulationError, triangulate
from . import affine
from .document import EditorError, _r

if TYPE_CHECKING:
    from .document import Document

TRACE_GRID = 256
Point = tuple[float, float]


def alpha_mask(image: Image.Image, threshold: int = 8, padding: int = 2) -> tuple[list[list[bool]], float]:
    """Opaque cells on a grid of at most TRACE_GRID per side, grown by `padding` image pixels.
    Returns (mask[y][x], image pixels per cell)."""
    alpha = image.convert("RGBA").getchannel("A")
    scale = max(1.0, max(alpha.size) / TRACE_GRID)
    w, h = max(1, round(alpha.width / scale)), max(1, round(alpha.height / scale))
    small = alpha.resize((w, h), Image.Resampling.BOX) if scale > 1 else alpha
    data = small.load()
    mask = [[data[x, y] > threshold for x in range(w)] for y in range(h)]
    grow = max(0, round(padding / scale))
    for _ in range(grow):
        mask = [[mask[y][x] or any(0 <= y + dy < h and 0 <= x + dx < w and mask[y + dy][x + dx]
                                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                 for x in range(w)] for y in range(h)]
    return mask, scale


def outline(mask: list[list[bool]]) -> list[Point]:
    """The largest boundary loop around opaque cells, as cell-corner points (y down)."""
    h = len(mask)
    w = len(mask[0]) if h else 0

    def inside(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and mask[y][x]

    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for y in range(h):
        for x in range(w):
            if not mask[y][x]:
                continue
            # clockwise around the cell (y down), only where the neighbour is outside
            for (nx, ny), a, b in (((x, y - 1), (x, y), (x + 1, y)), ((x + 1, y), (x + 1, y), (x + 1, y + 1)),
                                   ((x, y + 1), (x + 1, y + 1), (x, y + 1)), ((x - 1, y), (x, y + 1), (x, y))):
                if not inside(nx, ny):
                    edges.setdefault(a, []).append(b)
    loops: list[list[Point]] = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        current = start
        while True:
            outs = edges.get(current)
            if not outs:
                break
            nxt = outs.pop()
            if not outs:
                del edges[current]
            if nxt == start:
                break
            loop.append(nxt)
            current = nxt
        if len(loop) >= 3:
            loops.append([(float(px), float(py)) for px, py in loop])
    if not loops:
        return []
    return max(loops, key=lambda pts: abs(_area(pts)))


def _area(pts: list[Point]) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _douglas_peucker(pts: list[Point], tolerance: float) -> list[Point]:
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, -1
        for i in range(first + 1, last):
            d = _segment_distance(pts[i], pts[first], pts[last])
            if d > best:
                best, index = d, i
        if index >= 0 and best > tolerance:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(pts, keep) if k]


def _crosses(pts: list[Point]) -> bool:
    n = len(pts)

    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            if ccw(a, c, d) * ccw(b, c, d) < 0 and ccw(a, b, c) * ccw(a, b, d) < 0:
                return True
    return False


def simplify_loop(pts: list[Point], tolerance: float) -> list[Point]:
    """Douglas-Peucker on a closed loop (split at its two farthest points). The tolerance halves
    until the result does not cross itself."""
    if len(pts) < 4:
        return pts
    far = max(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]))
    while True:
        a = _douglas_peucker(pts[:far + 1], tolerance)
        b = _douglas_peucker(pts[far:] + [pts[0]], tolerance)
        result = a[:-1] + b[:-1]
        if len(result) >= 3 and not _crosses(result):
            return result
        if tolerance < 0.05:
            return pts
        tolerance /= 2


def _inside(p: Point, poly: list[Point]) -> bool:
    x, y = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def trace_outline(image: Image.Image, detail: float = 4.0, threshold: int = 8, padding: int = 2) -> list[Point]:
    """Outline points in image pixels (x right, y down), clockwise on screen."""
    mask, scale = alpha_mask(image, threshold, padding)
    loop = outline(mask)
    if len(loop) < 3:
        raise EditorError("The image has no opaque pixels to trace")
    loop = [(x * scale, y * scale) for x, y in loop]
    pts = simplify_loop(loop, max(0.25, detail))
    w, h = image.size
    return [(min(max(x, 0.0), w), min(max(y, 0.0), h)) for x, y in pts]


def interior_points(poly: list[Point], spacing: float) -> list[Point]:
    """A grid of points inside the outline, at least spacing / 3 from its edges."""
    if spacing <= 0:
        return []
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    out = []
    y = min(ys) + spacing / 2
    while y < max(ys):
        x = min(xs) + spacing / 2
        while x < max(xs):
            p = (x, y)
            if _inside(p, poly) and min(_segment_distance(p, poly[i], poly[(i + 1) % len(poly)])
                                        for i in range(len(poly))) > spacing / 3:
                out.append(p)
            x += spacing
        y += spacing
    return out


def trace_mesh(doc: "Document", slot: str, name: str, detail: float = 4.0, threshold: int = 8, padding: int = 2,
               interior: float = 0.0, record: bool = True) -> int:
    """Replace a region (or an unweighted, unkeyed mesh) with a mesh traced from its image.
    detail: how far (image pixels) the outline may stray from the alpha edge; interior: spacing of
    extra points inside (0 for none). Returns the vertex count."""
    att = doc.attachments(slot).get(name)
    if att is None or att.get("type", "region") not in ("region", "mesh") or att.get("parent"):
        raise EditorError(f"'{name}' is not a region or mesh attachment")
    from . import mesh_ops
    if att.get("type") == "mesh":
        from ..preview.mesh import parse_vertices
        if parse_vertices(att).weighted or mesh_ops._deform_keys(doc, slot, name):
            raise EditorError("Trace replaces the mesh: unbind it and delete its deform keys first")
    path = att.get("path", name)
    file = doc.image_file(path)
    if file is None:
        raise EditorError(f"Image {path}.png not found: tracing needs the image")
    with Image.open(file) as img:
        img_w, img_h = img.size
        outline_px = trace_outline(img, detail, threshold, padding)
    if att.get("type") == "mesh":  # the mesh maps the image like its own UVs do: keep that mapping
        world = mesh_ops._setup_world(doc)
        inv = _uv_to_local(doc, slot, name, affine.invert(world[doc.slot(slot)["bone"]]))
        w, h = att.get("width", img_w), att.get("height", img_h)
        place = None
    else:
        w, h = att["width"], att["height"]
        place = affine.local(att.get("x", 0), att.get("y", 0), att.get("rotation", 0),
                             att.get("scaleX", 1), att.get("scaleY", 1))
        inv = None
    points = outline_px + interior_points(outline_px, interior)
    try:
        triangles = [v for t in triangulate(points, len(outline_px)) for v in t]
    except TriangulationError as exc:
        raise EditorError(f"Cannot triangulate the traced outline: {exc}") from None
    uvs, vertices = [], []
    for x, y in points:
        u, v = x / img_w, y / img_h
        uvs += [_r(u), _r(v)]
        if place is not None:
            lx, ly = affine.apply(place, u * w - w / 2, h / 2 - v * h)
        else:
            lx, ly = inv(u, v)
        vertices += [_r(lx), _r(ly)]
    hull = len(outline_px)
    mesh = {"type": "mesh", "path": path, "uvs": uvs, "triangles": triangles, "vertices": vertices, "hull": hull,
            "edges": [v for i in range(hull) for v in (i * 2, ((i + 1) % hull) * 2)],
            "width": img_w, "height": img_h}
    if "color" in att:
        mesh["color"] = att["color"]
    if record:
        doc.checkpoint(f"Trace {name}")
    doc.skin.setdefault(slot, {})[name] = mesh
    doc.notify()
    return len(points)


def _uv_to_local(doc, slot, name, to_local):
    """For an existing mesh: UV -> slot bone local position, from its current triangles (affine
    per triangle, extended outside the mesh by the nearest triangle)."""
    from . import mesh_ops
    model = mesh_ops.load_model(doc, slot, name)
    tris = [model.triangles[i:i + 3] for i in range(0, len(model.triangles) - 2, 3)]

    def convert(u: float, v: float) -> Point:
        best = None
        for a, b, c in tris:
            (ua, va), (ub, vb), (uc, vc) = model.uvs[a], model.uvs[b], model.uvs[c]
            det = (vb - vc) * (ua - uc) + (uc - ub) * (va - vc)
            if abs(det) < 1e-12:
                continue
            l1 = ((vb - vc) * (u - uc) + (uc - ub) * (v - vc)) / det
            l2 = ((vc - va) * (u - uc) + (ua - uc) * (v - vc)) / det
            l3 = 1 - l1 - l2
            score = min(l1, l2, l3)
            if best is None or score > best[0]:
                best = (score, l1, l2, l3, a, b, c)
        _, l1, l2, l3, a, b, c = best
        x = l1 * model.positions[a][0] + l2 * model.positions[b][0] + l3 * model.positions[c][0]
        y = l1 * model.positions[a][1] + l2 * model.positions[b][1] + l3 * model.positions[c][1]
        return affine.apply(to_local, x, y)
    return convert
