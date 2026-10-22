"""Bake (constraints to plain keys) and simplify (remove keys that change nothing visible).

Bake samples an animation at every frame with all constraints applied, writes each chosen bone's
resulting local pose as rotate/translate/scale keys, and keys the constraints acting on those bones
to a mix of 0, so the animation looks the same without depending on IK or other constraints.
Simplify removes keys that straight-line interpolation between their neighbours reproduces within a
tolerance.
"""

from __future__ import annotations

from typing import Any

from . import affine
from .document import Document, EditorError

# Small enough that errors adding up along a long bone chain stay around a pixel.
TOLERANCE = {"rotate": 0.1, "translate": 0.1, "translatex": 0.1, "translatey": 0.1, "scale": 0.001,
             "scalex": 0.001, "scaley": 0.001, "shear": 0.1, "shearx": 0.1, "sheary": 0.1}
FIELDS = {"rotate": ("value",), "translate": ("x", "y"), "scale": ("x", "y"), "shear": ("x", "y"),
          "translatex": ("value",), "translatey": ("value",), "scalex": ("value",), "scaley": ("value",),
          "shearx": ("value",), "sheary": ("value",)}
DEFAULTS = {"rotate": 0, "translate": 0, "translatex": 0, "translatey": 0, "scale": 1, "scalex": 1, "scaley": 1,
            "shear": 0, "shearx": 0, "sheary": 0}


def constrained_bones(doc: Document) -> list[str]:
    """Bones that IK, transform or path constraints move."""
    out: list[str] = []
    for kind in ("ik", "transform", "path"):
        for c in doc.data.get(kind, []):
            for b in c.get("bones", []):
                if b not in out:
                    out.append(b)
    return out


def bake(doc: Document, animation: str, bones: list[str] | None = None, fps: float | None = None,
         record: bool = True) -> dict[str, Any]:
    """Bake the bones' constrained poses to keys at every frame. bones: default the constrained ones."""
    anim = doc.animation(animation)
    bones = bones if bones is not None else constrained_bones(doc)
    if not bones:
        raise EditorError("There is nothing to bake: no bone is moved by a constraint")
    fps = float(fps or doc.fps)
    ev = doc.evaluator(animation)
    duration = ev.duration
    frames = max(1, int(round(duration * fps)))
    times = [min(duration, i / fps) for i in range(frames + 1)]
    if record:
        doc.checkpoint(f"Bake {animation}")
    parents = {b["name"]: b.get("parent") for b in doc.bones}
    samples: dict[str, list[tuple[float, tuple[float, ...]]]] = {b: [] for b in bones}
    for t in times:
        world = ev.bone_world(t)
        for b in bones:
            parent = parents.get(b)
            parent_world = world[parent] if parent else affine.IDENTITY
            samples[b].append((t, affine.decompose(affine.mul(affine.invert(parent_world), world[b]))))
    tls_all = anim.setdefault("bones", {})
    for b, poses in samples.items():
        setup = doc.bone(b)
        tls = tls_all.setdefault(b, {})
        for name in ("rotate", "translate", "translatex", "translatey", "scale", "scalex", "scaley"):
            tls.pop(name, None)
        rot0, x0, y0 = setup.get("rotation", 0), setup.get("x", 0), setup.get("y", 0)
        sx0, sy0 = setup.get("scaleX", 1) or 1e-6, setup.get("scaleY", 1) or 1e-6
        rotate, translate, scale = [], [], []
        previous = None
        for t, (x, y, rotation, sx, sy) in poses:
            value = rotation - rot0
            if previous is not None:  # keep rotation continuous across ±180
                value += 360 * round((previous - value) / 360)
            previous = value
            stamp = {"time": round(t, 6)} if t else {}
            rotate.append({**stamp, "value": round(value, 4)})
            translate.append({**stamp, "x": round(x - x0, 4), "y": round(y - y0, 4)})
            scale.append({**stamp, "x": round(sx / sx0, 5), "y": round(sy / sy0, 5)})
        tls.update(rotate=rotate, translate=translate, scale=scale)
    # constraints acting on baked bones no longer change them
    silenced = []
    for kind, zero in (("ik", {"mix": 0}), ("transform", {"mixRotate": 0, "mixX": 0, "mixY": 0, "mixScaleX": 0,
                                                            "mixScaleY": 0, "mixShearY": 0})):
        for c in doc.data.get(kind, []):
            if set(c.get("bones", [])) & set(bones):
                anim.setdefault(kind, {})[c["name"]] = [dict(zero)]
                silenced.append(c["name"])
    for c in doc.data.get("path", []):
        if set(c.get("bones", [])) & set(bones):
            anim.setdefault("path", {}).setdefault(c["name"], {})["mix"] = [{"mixRotate": 0, "mixX": 0, "mixY": 0}]
            silenced.append(c["name"])
    doc.dirty = True
    doc.notify()
    return {"bones": bones, "frames": len(times), "constraints": silenced}


def _value_at(keys: list[dict[str, Any]], field: str, default: float, t: float, a: int, b: int) -> float:
    ta, tb = keys[a].get("time", 0), keys[b].get("time", 0)
    va, vb = keys[a].get(field, default), keys[b].get(field, default)
    if tb - ta < 1e-12:
        return va
    return va + (vb - va) * (t - ta) / (tb - ta)


def simplify_keys(keys: list[dict[str, Any]], timeline: str, tolerance: float | None = None) -> int:
    """Remove keys of one bone timeline that linear interpolation reproduces within the tolerance.
    Keys with a curve (bezier or stepped), or next to one, are kept. Returns how many were removed."""
    fields, default = FIELDS[timeline], DEFAULTS[timeline]
    tol = TOLERANCE[timeline] if tolerance is None else tolerance
    removed = 0
    changed = True
    while changed:
        changed = False
        best = None
        for i in range(1, len(keys) - 1):
            if any("curve" in keys[j] for j in (i - 1, i)):
                continue
            t = keys[i].get("time", 0)
            err = max(abs(keys[i].get(f, default) - _value_at(keys, f, default, t, i - 1, i + 1)) for f in fields)
            if err <= tol and (best is None or err < best[0]):
                best = (err, i)
        if best is not None:
            del keys[best[1]]
            removed += 1
            changed = True
    if len(keys) == 2 and not any("curve" in k for k in keys) and \
            all(abs(keys[0].get(f, default) - keys[1].get(f, default)) <= tol for f in fields):
        del keys[1]
        removed += 1
    return removed


def simplify(doc: Document, animation: str, bones: list[str] | None = None, tolerance: float | None = None,
             record: bool = True) -> int:
    """Simplify every bone timeline (of the given bones) in the animation. Returns keys removed."""
    anim = doc.animation(animation)
    if record:
        doc.checkpoint(f"Simplify {animation}")
    removed = 0
    for bone, tls in anim.get("bones", {}).items():
        if bones is not None and bone not in bones:
            continue
        for name, keys in tls.items():
            if name in FIELDS and isinstance(keys, list):
                removed += simplify_keys(keys, name, tolerance)
    doc.dirty = True
    doc.notify()
    return removed
