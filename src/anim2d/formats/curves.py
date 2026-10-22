"""Spine 4.0 curve evaluation, ported from the official runtime (Animation.java, branch 4.0):
CurveTimeline.setBezier builds 9 sample points by forward differencing in absolute
(time, value) space; getBezierValue interpolates linearly between them.

A key's "curve" is "stepped" or a flat list with 4 numbers per value channel:
[cx1, cy1, cx2, cy2, ...] where cx are absolute times and cy absolute values.
"""

from __future__ import annotations

from typing import Any

BEZIER_POINTS = 9


def bezier_points(time1: float, value1: float, cx1: float, cy1: float, cx2: float, cy2: float,
                  time2: float, value2: float) -> list[tuple[float, float]]:
    tmpx, tmpy = (time1 - cx1 * 2 + cx2) * 0.03, (value1 - cy1 * 2 + cy2) * 0.03
    dddx = ((cx1 - cx2) * 3 - time1 + time2) * 0.006
    dddy = ((cy1 - cy2) * 3 - value1 + value2) * 0.006
    ddx, ddy = tmpx * 2 + dddx, tmpy * 2 + dddy
    dx = (cx1 - time1) * 0.3 + tmpx + dddx * 0.16666667
    dy = (cy1 - value1) * 0.3 + tmpy + dddy * 0.16666667
    x, y = time1 + dx, value1 + dy
    points = []
    for _ in range(BEZIER_POINTS):
        points.append((x, y))
        dx += ddx
        dy += ddy
        ddx += dddx
        ddy += dddy
        x += dx
        y += dy
    return points


def bezier_value(time: float, time1: float, value1: float, handles: tuple[float, float, float, float],
                 time2: float, value2: float) -> float:
    """CurveTimeline.getBezierValue for one channel of one segment."""
    points = bezier_points(time1, value1, *handles, time2, value2)
    if points[0][0] > time:
        x, y = time1, value1
        return y + (time - x) / (points[0][0] - x) * (points[0][1] - y)
    for i in range(1, BEZIER_POINTS):
        if points[i][0] >= time:
            x, y = points[i - 1]
            return y + (time - x) / (points[i][0] - x) * (points[i][1] - y)
    x, y = points[-1]
    return y + (time - x) / (time2 - x) * (value2 - y)


def channel_handles(curve: Any, channel: int) -> tuple[float, float, float, float] | None:
    """Handles of one channel, None for linear; "stepped" is handled by the caller."""
    if not isinstance(curve, list) or len(curve) < channel * 4 + 4:
        return None
    return tuple(curve[channel * 4: channel * 4 + 4])  # type: ignore[return-value]


def segment_value(key: dict[str, Any], next_key: dict[str, Any] | None, time: float, channel: int,
                  v0: float, v1: float) -> float:
    """Value of one channel at time inside the segment that starts at key."""
    t0 = key.get("time", 0)
    if next_key is None:
        return v0
    curve = key.get("curve")
    if curve == "stepped":
        return v0
    t1 = next_key.get("time", 0)
    if t1 <= t0:
        return v1
    handles = channel_handles(curve, channel)
    if handles is None:
        return v0 + (time - t0) / (t1 - t0) * (v1 - v0)
    return bezier_value(time, t0, v0, handles, t1, v1)


def find_segment(keys: list[dict[str, Any]], time: float) -> int | None:
    """Index of the key whose segment contains time (runtime rule: the last key with time <= t),
    or None before the first key."""
    if not keys or time < keys[0].get("time", 0):
        return None
    index = 0
    for i in range(1, len(keys)):
        if keys[i].get("time", 0) > time:
            break
        index = i
    return index


# --- normalized (3.8-style) <-> absolute (4.0-style) handles ------------------------------

def to_absolute(normalized: tuple[float, float, float, float], t0: float, v0: float, t1: float,
                v1: float) -> list[float]:
    c1, c2, c3, c4 = normalized
    return [t0 + c1 * (t1 - t0), v0 + c2 * (v1 - v0), t0 + c3 * (t1 - t0), v0 + c4 * (v1 - v0)]


def to_normalized(handles: tuple[float, float, float, float], t0: float, v0: float, t1: float,
                  v1: float) -> tuple[float, float, float, float] | None:
    """None when the curve cannot be expressed relative to the segment (a flat segment with a bump)."""
    cx1, cy1, cx2, cy2 = handles
    dt, dv = t1 - t0, v1 - v0
    if dt <= 0:
        return None
    if abs(dv) < 1e-9:
        if abs(cy1 - v0) > 1e-6 or abs(cy2 - v0) > 1e-6:
            return None
        return ((cx1 - t0) / dt, 0.0, (cx2 - t0) / dt, 1.0)  # flat: y handles do not matter
    return ((cx1 - t0) / dt, (cy1 - v0) / dv, (cx2 - t0) / dt, (cy2 - v0) / dv)


# --- splitting a segment (graph editor: add a key without changing the motion) -------------

def _cubic(p0: float, p1: float, p2: float, p3: float, s: float) -> float:
    u = 1 - s
    return u * u * u * p0 + 3 * u * u * s * p1 + 3 * u * s * s * p2 + s * s * s * p3


def split_bezier(t0: float, v0: float, handles: tuple[float, float, float, float], t1: float, v1: float,
                 time: float) -> tuple[list[float], float, list[float]]:
    """de Casteljau split of one channel's segment at time (t0 < time < t1).
    Returns (left handles, value at time, right handles), all absolute."""
    cx1, cy1, cx2, cy2 = handles
    lo, hi = 0.0, 1.0
    for _ in range(60):  # x(s) = time; x(0) = t0 < time < t1 = x(1)
        mid = (lo + hi) / 2
        if _cubic(t0, cx1, cx2, t1, mid) < time:
            lo = mid
        else:
            hi = mid
    s = (lo + hi) / 2

    def lerp(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        return a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s

    p0, p1, p2, p3 = (t0, v0), (cx1, cy1), (cx2, cy2), (t1, v1)
    p01, p12, p23 = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
    p012, p123 = lerp(p01, p12), lerp(p12, p23)
    mid_point = lerp(p012, p123)
    return [*p01, *p012], mid_point[1], [*p123, *p23]
