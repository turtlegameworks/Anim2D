"""2D affine helpers shared by the editor (same convention as preview.evaluator.Affine)."""

from __future__ import annotations

import math

Affine = tuple[float, float, float, float, float, float]  # x' = a*x + c*y + tx, y' = b*x + d*y + ty
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(p: Affine, q: Affine) -> Affine:
    a, b, c, d, tx, ty = p
    a2, b2, c2, d2, tx2, ty2 = q
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
            a * tx2 + c * ty2 + tx, b * tx2 + d * ty2 + ty)


def invert(m: Affine) -> Affine:
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        det = 1e-12
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty))


def apply(m: Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, tx, ty = m
    return a * x + c * y + tx, b * x + d * y + ty


def apply_vector(m: Affine, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, _, _ = m
    return a * x + c * y, b * x + d * y


def local(x: float, y: float, rotation: float, sx: float, sy: float) -> Affine:
    r = math.radians(rotation)
    cos, sin = math.cos(r), math.sin(r)
    return (cos * sx, sin * sx, -sin * sy, cos * sy, x, y)


def decompose(m: Affine) -> tuple[float, float, float, float, float]:
    """(x, y, rotation, scaleX, scaleY) for a matrix without shear."""
    a, b, c, d, tx, ty = m
    sx = math.hypot(a, b)
    rotation = math.degrees(math.atan2(b, a))
    det = a * d - b * c
    sy = det / sx if sx > 1e-12 else math.hypot(c, d)
    return tx, ty, rotation, sx, sy


def world_rotation(m: Affine) -> float:
    return math.degrees(math.atan2(m[1], m[0]))


def is_flipped(m: Affine) -> bool:
    return m[0] * m[3] - m[1] * m[2] < 0
