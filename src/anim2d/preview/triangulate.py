"""Constrained Delaunay triangulation of a mesh: the hull ring is kept as boundary edges.

Points 0..hull-1 form the hull ring (any orientation, may be concave, may contain collinear
points); the remaining points are interior. Steps:
  1. ear-clip the polygon made of the hull's corner (non-collinear) points;
  2. split hull edges at the collinear hull points;
  3. insert interior points (splitting the triangle or edge that contains them);
  4. Lawson edge flips to Delaunay, never flipping a hull edge.
"""

from __future__ import annotations

Point = tuple[float, float]
Tri = tuple[int, int, int]
EPS = 1e-9


class TriangulationError(ValueError):
    pass


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _scale(points: list[Point]) -> float:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)


def polygon_area(points: list[Point]) -> float:
    return sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))


def hull_is_simple(points: list[Point], hull: int) -> bool:
    ring = points[:hull]
    tol = _scale(ring) * 1e-7
    for i in range(hull):
        # An outline that doubles back on itself along a line (a "spike") also overlaps itself.
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % hull]
        ab, bc = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        length = max(abs(ab[0]) + abs(ab[1]), 1e-12)
        if abs(_orient(a, b, c)) <= tol * length and ab[0] * bc[0] + ab[1] * bc[1] < 0:
            return False
    for i in range(hull):
        a, b = ring[i], ring[(i + 1) % hull]
        for j in range(i + 2, hull):
            if i == 0 and j == hull - 1:
                continue
            if _segments_cross(a, b, ring[j], ring[(j + 1) % hull]):
                return False
    return abs(polygon_area(ring)) > EPS


def point_in_polygon(p: Point, ring: list[Point]) -> bool:
    inside = False
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


def _in_circle(a: Point, b: Point, c: Point, d: Point) -> bool:
    """True if d is strictly inside the circumcircle of CCW triangle abc."""
    ax, ay = a[0] - d[0], a[1] - d[1]
    bx, by = b[0] - d[0], b[1] - d[1]
    cx, cy = c[0] - d[0], c[1] - d[1]
    det = (ax * ax + ay * ay) * (bx * cy - cx * by) - (bx * bx + by * by) * (ax * cy - cx * ay) \
        + (cx * cx + cy * cy) * (ax * by - bx * ay)
    return det > 1e-9


class _Mesh:
    def __init__(self, points: list[Point]):
        self.p = points
        self.tris: list[Tri] = []
        self.fixed: set[frozenset[int]] = set()

    def add(self, a: int, b: int, c: int) -> None:
        if _orient(self.p[a], self.p[b], self.p[c]) < 0:
            b, c = c, b
        self.tris.append((a, b, c))

    def with_edge(self, a: int, b: int) -> list[Tri]:
        e = {a, b}
        return [t for t in self.tris if e <= set(t)]

    def split_edge(self, a: int, b: int, p: int) -> list[tuple[int, int]]:
        """Split every triangle on edge a-b at point p. Returns edges to legalize."""
        touched = []
        for t in self.with_edge(a, b):
            c = next(v for v in t if v not in (a, b))
            self.tris.remove(t)
            self.add(a, p, c)
            self.add(p, b, c)
            touched += [(a, c), (b, c)]
        if frozenset((a, b)) in self.fixed:
            self.fixed.discard(frozenset((a, b)))
            self.fixed |= {frozenset((a, p)), frozenset((p, b))}
        return touched

    def legalize(self, edges: list[tuple[int, int]]) -> None:
        stack = list(edges)
        guard = 0
        while stack:
            guard += 1
            if guard > 100000:
                raise TriangulationError("triangulation did not converge")
            a, b = stack.pop()
            if frozenset((a, b)) in self.fixed:
                continue
            pair = self.with_edge(a, b)
            if len(pair) != 2:
                continue
            t1, t2 = pair
            c = next(v for v in t1 if v not in (a, b))
            d = next(v for v in t2 if v not in (a, b))
            x, y, z = t1  # stored triangles are always counter-clockwise
            if not _in_circle(self.p[x], self.p[y], self.p[z], self.p[d]):
                continue
            # Only flip when the quad is convex (the new edge c-d lies inside it).
            if _orient(self.p[c], self.p[d], self.p[a]) * _orient(self.p[c], self.p[d], self.p[b]) >= 0:
                continue
            self.tris.remove(t1)
            self.tris.remove(t2)
            self.add(c, d, a)
            self.add(c, d, b)
            stack += [(a, c), (a, d), (b, c), (b, d)]


def triangulate(points: list[Point], hull: int) -> list[Tri]:
    n = len(points)
    if hull < 3 or hull > n:
        raise TriangulationError("a mesh needs at least 3 hull vertices")
    if not hull_is_simple(points, hull):
        raise TriangulationError("the mesh outline crosses itself")
    tol = _scale(points) * 1e-7
    ring = list(range(hull))
    if polygon_area([points[i] for i in ring]) < 0:
        ring.reverse()  # work counter-clockwise
    m = _Mesh(points)

    # 1. ear-clip the corner polygon (collinear hull points are added in step 2)
    def collinear(i: int, poly: list[int]) -> bool:
        k = poly.index(i)
        a, b, c = points[poly[k - 1]], points[i], points[poly[(k + 1) % len(poly)]]
        return abs(_orient(a, b, c)) <= tol * max(abs(b[0] - a[0]) + abs(b[1] - a[1]), 1)
    corners = list(ring)
    changed = True
    while changed and len(corners) > 3:
        changed = False
        for i in list(corners):
            if len(corners) > 3 and collinear(i, corners):
                corners.remove(i)
                changed = True
    on_edges = [i for i in ring if i not in corners]
    poly = list(corners)
    guard = 0
    while len(poly) > 3:
        guard += 1
        if guard > 10 * hull * hull + 100:
            raise TriangulationError("could not triangulate the mesh outline")
        for k in range(len(poly)):
            a, b, c = poly[k - 1], poly[k], poly[(k + 1) % len(poly)]
            if _orient(points[a], points[b], points[c]) <= tol:
                continue
            if any(_inside_tri(points[q], points[a], points[b], points[c], strict=False)
                   for q in poly if q not in (a, b, c)):
                continue
            m.add(a, b, c)
            poly.pop(k)
            break
        else:
            raise TriangulationError("could not triangulate the mesh outline")
    m.add(*poly)
    m.fixed = {frozenset((corners[i], corners[(i + 1) % len(corners)])) for i in range(len(corners))}

    # 2. collinear hull points split their hull edge
    for i in on_edges:
        edge = _find_edge_containing(m, points, i, tol)
        if edge is None:
            raise TriangulationError("a hull vertex is not on the mesh outline")
        m.split_edge(*edge, i)
    m.fixed = {frozenset((ring[i], ring[(i + 1) % hull])) for i in range(hull)}

    # 3. interior points
    ring_points = [points[i] for i in ring]
    for i in range(hull, n):
        p = points[i]
        if not point_in_polygon(p, ring_points) and _find_edge_containing(m, points, i, tol) is None:
            raise TriangulationError(f"vertex {i} is outside the mesh outline")
        edge = _find_edge_containing(m, points, i, tol)
        if edge is not None:
            if frozenset(edge) in m.fixed:
                raise TriangulationError(f"vertex {i} lies on the mesh outline; add it to the hull instead")
            m.legalize(m.split_edge(*edge, i))
            continue
        tri = next((t for t in m.tris if _inside_tri(p, *(points[v] for v in t), strict=True)), None)
        if tri is None:
            raise TriangulationError(f"vertex {i} coincides with another vertex")
        a, b, c = tri
        m.tris.remove(tri)
        m.add(a, b, i)
        m.add(b, c, i)
        m.add(c, a, i)
        m.legalize([(a, b), (b, c), (c, a)])

    # 4. make the whole triangulation Delaunay
    m.legalize([(t[k], t[(k + 1) % 3]) for t in list(m.tris) for k in range(3)])
    used = {v for t in m.tris for v in t}
    if len(used) != n:
        raise TriangulationError("some vertices could not be connected (duplicates?)")
    return m.tris


def _inside_tri(p: Point, a: Point, b: Point, c: Point, strict: bool) -> bool:
    d1, d2, d3 = _orient(a, b, p), _orient(b, c, p), _orient(c, a, p)
    if strict:
        return d1 > EPS and d2 > EPS and d3 > EPS
    return d1 >= -EPS and d2 >= -EPS and d3 >= -EPS


def _find_edge_containing(m: _Mesh, points: list[Point], i: int, tol: float) -> tuple[int, int] | None:
    p = points[i]
    for t in m.tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            if i in (a, b):
                continue
            pa, pb = points[a], points[b]
            length = max(abs(pb[0] - pa[0]) + abs(pb[1] - pa[1]), 1e-12)
            if abs(_orient(pa, pb, p)) <= tol * length:
                lo_x, hi_x = sorted((pa[0], pb[0]))
                lo_y, hi_y = sorted((pa[1], pb[1]))
                if lo_x - tol <= p[0] <= hi_x + tol and lo_y - tol <= p[1] <= hi_y + tol \
                        and p != pa and p != pb:
                    return a, b
    return None


def barycentric(p: Point, a: Point, b: Point, c: Point) -> tuple[float, float, float] | None:
    det = _orient(a, b, c)
    if abs(det) < 1e-12:
        return None
    wa = _orient(b, c, p) / det
    wb = _orient(c, a, p) / det
    return wa, wb, 1 - wa - wb
