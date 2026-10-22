"""Retime: scale an animation (or the selected keys) in time, reverse it, or loop a section of it.

Curves keep their shape: the handles are saved relative to their segment before the keys move and
rebuilt afterwards (timelines40.snapshot / restore). Reversing also mirrors each curve and moves it
to the key that now starts the segment.
"""

from __future__ import annotations

import copy
from typing import Any, Iterator

from ..formats import timelines40 as tl40
from .document import KEY_EPS, Document, EditorError, KeyRef, _r


def _lists(doc: Document, anim: dict[str, Any]) -> Iterator[tuple[list[dict[str, Any]], Any]]:
    """Every key list of the animation with its curve reader (False: the timeline has no curves)."""
    for group in ("bones", "slots"):
        for owner, tls in anim.get(group, {}).items():
            for tl, keys in tls.items():
                reader = tl40.reader_for(group, tl) if (group == "bones" or tl != "attachment") else False
                yield keys, reader
    for group in ("ik", "transform"):
        for keys in anim.get(group, {}).values():
            yield keys, tl40.reader_for(group, group)
    for tls in anim.get("path", {}).values():
        for tl, keys in tls.items():
            yield keys, tl40.reader_for("path", tl)
    for skin in anim.get("deform", {}).values():
        for atts in skin.values():
            for keys in atts.values():
                yield keys, tl40.reader_for("deform", "deform")
    for group in ("events", "drawOrder"):
        if anim.get(group):
            yield anim[group], False


def _chosen(doc: Document, anim: dict[str, Any], refs: list[KeyRef] | None) -> list[tuple[list, Any, list]]:
    """[(keys, reader, chosen keys)]: every key, or only those the refs name."""
    out = []
    if refs is None:
        for keys, reader in _lists(doc, anim):
            out.append((keys, reader, list(keys)))
        return out
    by_list: dict[int, tuple[list, Any, list]] = {}
    for ref in refs:
        keys = doc._timeline_keys(anim, ref)
        if not keys:
            continue
        reader = doc._reader(ref)
        entry = by_list.setdefault(id(keys), (keys, reader, []))
        for k in doc._match(keys, ref):
            if all(k is not o for o in entry[2]):
                entry[2].append(k)
    return list(by_list.values())


def _settle(keys: list[dict[str, Any]], moved: list[dict[str, Any]], events: bool) -> None:
    """Sort by time; a moved key replaces another key it now shares a time with."""
    if not events:
        for k in moved:
            keys[:] = [o for o in keys if o is k or o in moved or abs(o.get("time", 0) - k.get("time", 0)) >= KEY_EPS]
    keys.sort(key=lambda key: key.get("time", 0))
    if keys and "time" in keys[0] and keys[0]["time"] == 0:
        keys[0].pop("time")  # Spine leaves time 0 out


def _set_time(key: dict[str, Any], t: float) -> None:
    t = _r(max(0.0, t))
    if t:
        key["time"] = t
    else:
        key.pop("time", None)


def scale_time(doc: Document, animation: str, factor: float, refs: list[KeyRef] | None = None,
               pivot: float | None = None, record: bool = True) -> int:
    """Stretch (factor > 1) or squeeze (< 1) time around pivot (default: 0, or the first selected key).
    Returns the number of keys moved."""
    if factor <= 0:
        raise EditorError("The time scale must be above 0")
    anim = doc.animation(animation)
    chosen = _chosen(doc, anim, refs)
    if not any(c for _, _, c in chosen):
        raise EditorError("There are no keys to retime")
    if pivot is None:
        pivot = 0.0 if refs is None else min(k.get("time", 0) for _, _, c in chosen for k in c)
    doc._begin(f"Scale time ×{factor:g}", record)
    count = 0
    for keys, reader, picked in chosen:
        snap = tl40.snapshot(keys, reader) if reader is not False else None
        for k in picked:
            _set_time(k, pivot + (k.get("time", 0) - pivot) * factor)
            count += 1
        _settle(keys, picked, keys is anim.get("events"))
        if snap is not None:
            tl40.restore(keys, reader, snap)
    doc.dirty = True
    doc.notify()
    return count


def _mirror(channels: list) -> list:
    """A segment's normalized handles played backwards."""
    out = []
    for kind, h in channels:
        if kind != "norm":
            return []  # a flat segment with a bump: it becomes linear
        x1, y1, x2, y2 = h
        out.append(("norm", (1 - x2, 1 - y2, 1 - x1, 1 - y1)))
    return out


def reverse(doc: Document, animation: str, refs: list[KeyRef] | None = None, record: bool = True) -> int:
    """Play the animation (or the span of the selected keys) backwards. Returns the keys moved."""
    anim = doc.animation(animation)
    chosen = _chosen(doc, anim, refs)
    times = [k.get("time", 0) for _, _, c in chosen for k in c]
    if not times:
        raise EditorError("There are no keys to reverse")
    a, b = (0.0, doc.animation_duration(animation)) if refs is None else (min(times), max(times))
    doc._begin("Reverse keys", record)
    count = 0
    for keys, reader, picked in chosen:
        snap = tl40.snapshot(keys, reader) if reader is not False else {}
        order = sorted(picked, key=lambda k: k.get("time", 0))
        # the curve of segment (k -> next) now belongs to next, which starts the reversed segment
        curves = {}
        for k0, k1 in zip(order, order[1:]):
            curves[id(k1)] = (k0.get("curve"), snap.get(id(k0)))
        for k in order:
            k.pop("curve", None)
        for k in order:
            _set_time(k, a + b - k.get("time", 0))
            count += 1
            moved = curves.get(id(k))
            if moved is None:
                continue
            curve, channels = moved
            if curve == "stepped":
                k["curve"] = "stepped"
            elif isinstance(curve, list) and channels and _mirror(channels):
                k["curve"] = list(curve)  # placeholder, rebuilt by restore from the mirrored shape
                snap[id(k)] = _mirror(channels)
        _settle(keys, picked, keys is anim.get("events"))
        if reader is not False:
            tl40.restore(keys, reader, snap)
    doc.dirty = True
    doc.notify()
    return count


def loop_section(doc: Document, animation: str, start: float, end: float, times: int = 1,
                 record: bool = True) -> int:
    """Repeat the keys in [start, end) `times` more times right after `end`; later keys move on.
    Returns the number of keys added."""
    if end - start <= KEY_EPS:
        raise EditorError("The section to loop must have a length")
    if times < 1:
        raise EditorError("Loop the section at least once")
    anim = doc.animation(animation)
    length = end - start
    shift = length * times
    doc._begin(f"Loop {start:g}s–{end:g}s ×{times}", record)
    added = 0
    for keys, reader in list(_lists(doc, anim)):
        snap = tl40.snapshot(keys, reader) if reader is not False else {}
        section = [k for k in keys if start - KEY_EPS <= k.get("time", 0) < end - KEY_EPS]
        later = [k for k in keys if k.get("time", 0) >= end - KEY_EPS]
        for k in later:
            _set_time(k, k.get("time", 0) + shift)
        copies = []
        for n in range(1, times + 1):
            for k in section:
                c = copy.deepcopy(k)
                _set_time(c, k.get("time", 0) + length * n)
                if id(k) in snap:
                    snap[id(c)] = snap[id(k)]
                copies.append(c)
        keys.extend(copies)
        added += len(copies)
        _settle(keys, copies, keys is anim.get("events"))
        if reader is not False:
            tl40.restore(keys, reader, snap)
    doc.dirty = True
    doc.notify()
    return added
