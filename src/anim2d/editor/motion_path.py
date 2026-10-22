"""Motion paths: where a bone goes over the animation, one dot per frame, bigger at keys.

Dragging a dot moves the bone there at that frame: the translate key at that time is set (or added)
so the bone's world position follows the mouse, whatever its parents do.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import affine
from .document import KEY_EPS, Document


@dataclass
class PathPoint:
    time: float
    x: float
    y: float
    key: bool  # the bone has a key (any of its timelines) at this time


def key_times(doc: Document, animation: str, bone: str) -> list[float]:
    tls = doc.animation(animation).get("bones", {}).get(bone, {})
    return sorted({k.get("time", 0) for keys in tls.values() for k in keys})


def path_points(doc: Document, animation: str, bone: str, fps: float | None = None) -> list[PathPoint]:
    """The bone's world position at every frame of the animation (and at every key)."""
    if not animation or not doc.has_bone(bone):
        return []
    ev = doc.evaluator(animation)
    fps = float(fps or doc.fps)
    duration = ev.duration
    keys = key_times(doc, animation, bone)
    times = sorted({round(i / fps, 6) for i in range(int(duration * fps + 1e-6) + 1)} | set(keys))
    out = []
    for t in times:
        m = ev.bone_world(t)[bone]
        out.append(PathPoint(t, m[4], m[5], any(abs(t - k) < KEY_EPS for k in keys)))
    return out


def translate_key_at(doc: Document, animation: str, bone: str, t: float) -> tuple[float, float]:
    """The translate key values the bone has at time t (setup-relative, as in the keys)."""
    ev = doc.evaluator(animation)
    x, y = ev.bone_locals(t)[bone][:2]
    setup = doc.bone(bone)
    return x - setup.get("x", 0), y - setup.get("y", 0)


def move_to(doc: Document, animation: str, bone: str, t: float, base: tuple[float, float],
            world_dx: float, world_dy: float, record: bool = False) -> tuple[float, float]:
    """Key the bone at time t so it sits world_dx, world_dy away from where `base` (translate key
    values at the start of the drag) put it. Returns the new key values."""
    parent = doc.bone(bone).get("parent")
    parent_world = doc.evaluator(animation).bone_world(t)[parent] if parent else affine.IDENTITY
    inv = affine.invert(parent_world)
    lx, ly = affine.apply_vector(inv, world_dx, world_dy)
    x, y = round(base[0] + lx, 3), round(base[1] + ly, 3)
    doc.set_bone_key(animation, bone, "translate", t, record=record, x=x, y=y)
    return x, y

