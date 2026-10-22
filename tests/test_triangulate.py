"""Constrained Delaunay triangulation used when mesh vertices are added or removed."""

from __future__ import annotations

import random

import pytest

from anim2d.preview.triangulate import (TriangulationError, _in_circle, _orient, polygon_area,
                                                  triangulate)


def _check(points, hull, tris):
    area = sum(abs(_orient(points[a], points[b], points[c])) / 2 for a, b, c in tris)
    assert area == pytest.approx(abs(polygon_area(points[:hull])), rel=1e-9), "triangles must tile the outline"
    assert all(_orient(points[a], points[b], points[c]) > 0 for a, b, c in tris), "all CCW, no slivers of zero area"
    assert {v for t in tris for v in t} == set(range(len(points))), "every vertex is used"
    edges = {}
    for t in tris:
        for k in range(3):
            edges.setdefault(frozenset((t[k], t[(k + 1) % 3])), []).append(t)
    for i in range(hull):
        assert frozenset((i, (i + 1) % hull)) in edges, "hull edges are kept"
    hull_edges = {frozenset((i, (i + 1) % hull)) for i in range(hull)}
    for edge, pair in edges.items():  # Delaunay except across the outline
        if edge in hull_edges or len(pair) != 2:
            continue
        a, b, c = pair[0]
        d = next(v for v in pair[1] if v not in edge)
        assert not _in_circle(points[a], points[b], points[c], points[d]), edge


def test_grid_with_collinear_hull_points_and_interior():
    cols, rows = 6, 2
    ring = [(c, 0) for c in range(cols + 1)] + [(cols, r) for r in range(1, rows + 1)] + \
           [(c, rows) for c in range(cols - 1, -1, -1)] + [(0, r) for r in range(rows - 1, 0, -1)]
    interior = [(c, r) for r in range(1, rows) for c in range(1, cols)]
    points = [(x * 10.0, y * 10.0) for x, y in ring + interior]
    _check(points, len(ring), triangulate(points, len(ring)))


def test_concave_outline_never_covers_the_notch():
    ring = [(0, 0), (100, 0), (100, 100), (60, 100), (60, 40), (40, 40), (40, 100), (0, 100)]  # a "U"
    interior = [(20, 20), (80, 20), (20, 80), (80, 80), (50, 20)]
    points = [(float(x), float(y)) for x, y in ring + interior]
    _check(points, len(ring), triangulate(points, len(ring)))


def test_clockwise_outline_and_random_interior():
    rng = random.Random(4)
    ring = [(0, 0), (0, 80), (120, 80), (120, 0)]  # clockwise in y-up terms
    interior = [(rng.uniform(5, 115), rng.uniform(5, 75)) for _ in range(40)]
    points = [(float(x), float(y)) for x, y in ring] + interior
    _check(points, len(ring), triangulate(points, len(ring)))


@pytest.mark.parametrize("points, hull, message", [
    ([(0, 0), (10, 0)], 2, "at least 3"),
    ([(0, 0), (10, 10), (10, 0), (0, 10)], 4, "crosses itself"),
    ([(0, 0), (10, 0), (10, 10), (0, 10), (20, 20)], 4, "outside"),
    ([(0, 0), (10, 0), (10, 10), (0, 10), (5, 0)], 4, "on the mesh outline"),
    ([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (5, 5)], 4, "coincides"),
])
def test_bad_input_is_rejected(points, hull, message):
    with pytest.raises(TriangulationError, match=message):
        triangulate([(float(x), float(y)) for x, y in points], hull)
