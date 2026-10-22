"""Ease presets and curve copy/paste for the selected keys.

Overshoot, anticipate and similar eases are one bezier per segment (set_curve). Bounce and elastic
swing back and forth several times, which a single bezier can't do, so they add keys between each
selected key and the next, sampled from the easing function (bone timelines; linear between them).
"""

from __future__ import annotations

import math
from typing import Any, Callable

from ..formats import timelines40 as tl40
from .bake import DEFAULTS, FIELDS
from .document import KEY_EPS, Document, EditorError, KeyRef, _r

# normalized cubic beziers (cx1, cy1, cx2, cy2); y outside 0..1 overshoots
EASE_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "Ease in": (0.6, 0.0, 0.85, 0.3),
    "Ease out": (0.15, 0.7, 0.35, 1.0),
    "Ease in-out": (0.45, 0.0, 0.55, 1.0),
    "Strong ease in": (0.75, 0.0, 0.95, 0.15),
    "Strong ease out": (0.05, 0.85, 0.25, 1.0),
    "Overshoot (back out)": (0.34, 1.56, 0.64, 1.0),
    "Anticipate (back in)": (0.36, 0.0, 0.66, -0.56),
    "Anticipate + overshoot": (0.68, -0.6, 0.32, 1.6),
}


def _bounce(u: float) -> float:
    """Ease-out bounce: falls to the target and bounces three times."""
    n, d = 7.5625, 2.75
    if u < 1 / d:
        return n * u * u
    if u < 2 / d:
        u -= 1.5 / d
        return n * u * u + 0.75
    if u < 2.5 / d:
        u -= 2.25 / d
        return n * u * u + 0.9375
    u -= 2.625 / d
    return n * u * u + 0.984375


def _elastic(u: float) -> float:
    """Ease-out elastic: shoots past the target and wobbles in."""
    if u in (0.0, 1.0):
        return u
    return 2 ** (-10 * u) * math.sin((u * 10 - 0.75) * (2 * math.pi / 3)) + 1


BAKED_EASES: dict[str, Callable[[float], float]] = {"Bounce (adds keys)": _bounce, "Elastic (adds keys)": _elastic}


def bake_ease(doc: Document, animation: str, refs: list[KeyRef], name: str, steps: int = 12,
              record: bool = True) -> int:
    """Shape each selected bone key's segment with a bounce/elastic ease by adding keys. Returns keys added."""
    ease = BAKED_EASES[name]
    anim = doc.animation(animation)
    targets = []
    for ref in refs:
        if ref.group != "bones" or ref.timeline not in FIELDS:
            continue
        keys = doc._timeline_keys(anim, ref)
        if not keys:
            continue
        for k in doc._match(keys, ref):
            i = keys.index(k)
            if i + 1 < len(keys):
                targets.append((ref, keys, k, keys[i + 1]))
    if not targets:
        raise EditorError("Select bone keys that have a next key (rotate, translate, scale or shear)")
    doc._begin(f"{name.split(' (')[0]} ease", record)
    added = 0
    for ref, keys, k0, k1 in targets:
        fields, default = FIELDS[ref.timeline], DEFAULTS[ref.timeline]
        t0, t1 = k0.get("time", 0), k1.get("time", 0)
        k0.pop("curve", None)
        new = []
        for s in range(1, steps):
            u = s / steps
            f = ease(u)
            key: dict[str, Any] = {"time": _r(t0 + (t1 - t0) * u)}
            for field in fields:
                a, b = k0.get(field, default), k1.get(field, default)
                key[field] = round(a + (b - a) * f, 4)
            if all(abs(o.get("time", 0) - key["time"]) >= KEY_EPS for o in keys):
                new.append(key)
        keys.extend(new)
        keys.sort(key=lambda key: key.get("time", 0))
        added += len(new)
    doc.dirty = True
    doc.notify()
    return added


def copy_curve(doc: Document, animation: str, ref: KeyRef) -> Any:
    """The shape of the segment after the key: None (linear), "stepped" or normalized handles."""
    anim = doc.animation(animation)
    keys = doc._timeline_keys(anim, ref)
    reader = doc._reader(ref)
    if not keys or reader is False:
        raise EditorError("Select a key on a timeline with curves")
    matches = doc._match(keys, ref)
    if not matches:
        raise EditorError("Select a key")
    k = matches[0]
    if keys.index(k) == len(keys) - 1:
        raise EditorError("The last key has no curve after it; select an earlier key")
    curve = k.get("curve")
    if curve in (None, "stepped"):
        return curve
    channels = tl40.snapshot(keys, reader).get(id(k)) or []
    # a channel that doesn't change (x of a vertical move) says nothing about the shape: use the one
    # whose value changes most over the segment
    nxt = keys[keys.index(k) + 1]
    a, b = doc.key_channels(ref, k), doc.key_channels(ref, nxt)
    order = sorted(range(min(len(channels), len(a), len(b))), key=lambda c: -abs(b[c] - a[c]))
    norm = next((channels[c][1] for c in order if channels[c][0] == "norm"), None)
    return tuple(round(v, 5) for v in norm) if norm else None


def paste_curve(doc: Document, animation: str, refs: list[KeyRef], curve: Any, record: bool = True) -> None:
    if not refs:
        raise EditorError("Select the keys to paste the curve onto")
    doc.set_curve(animation, refs, curve, record=record)
