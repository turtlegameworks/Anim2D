"""What each Spine 4.0 timeline stores, and keeping curve shapes when keys change.

4.0 curves are absolute ([cx1, cy1, cx2, cy2] per value channel, in seconds and value units).
When a key's time or value changes, the stored handles would no longer fit their segment, so
edits take a snapshot of every curve relative to its segment first (normalized), apply the
change, then rebuild the absolute handles for the new segment endpoints.
"""

from __future__ import annotations

from typing import Any, Callable

from ..models.effect import Color
from . import curves as cv

Reader = Callable[[dict[str, Any]], list[float]]


def _rgba(hex_value: str) -> list[float]:
    c = Color.from_hex(hex_value if len(hex_value) == 8 else hex_value + "ff")
    return [c.r, c.g, c.b, c.a]


def _value(default: float) -> Reader:
    return lambda k: [float(k.get("value", default))]


def _xy(default: float) -> Reader:
    return lambda k: [float(k.get("x", default)), float(k.get("y", default))]


BONE_TIMELINES: dict[str, Reader] = {
    "rotate": _value(0),
    "translate": _xy(0), "translatex": _value(0), "translatey": _value(0),
    "scale": _xy(1), "scalex": _value(1), "scaley": _value(1),
    "shear": _xy(0), "shearx": _value(0), "sheary": _value(0),
}
SLOT_TIMELINES: dict[str, Reader] = {
    "rgba": lambda k: _rgba(k["color"]),
    "rgb": lambda k: _rgba(k["color"])[:3],
    "alpha": _value(0),  # the 4.0 JSON reader defaults alpha keys to 0
    "rgba2": lambda k: _rgba(k["light"]) + _rgba(k["dark"])[:3],
    "rgb2": lambda k: _rgba(k["light"])[:3] + _rgba(k["dark"])[:3],
}
IK_READER: Reader = lambda k: [float(k.get("mix", 1)), float(k.get("softness", 0))]  # noqa: E731


def TRANSFORM_READER(k: dict[str, Any]) -> list[float]:  # noqa: N802 - a reader like the others
    mix_x, mix_scale_x = float(k.get("mixX", 1)), float(k.get("mixScaleX", 1))
    return [float(k.get("mixRotate", 1)), mix_x, float(k.get("mixY", mix_x)), mix_scale_x,
            float(k.get("mixScaleY", mix_scale_x)), float(k.get("mixShearY", 1))]


def PATH_MIX_READER(k: dict[str, Any]) -> list[float]:  # noqa: N802
    mix_x = float(k.get("mixX", 1))
    return [float(k.get("mixRotate", 1)), mix_x, float(k.get("mixY", mix_x))]


PATH_TIMELINES: dict[str, Reader] = {"position": _value(0), "spacing": _value(0), "mix": PATH_MIX_READER}
TRANSFORM_FIELDS = ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY")
PATH_MIX_FIELDS = ("mixRotate", "mixX", "mixY")
DEFORM_READER: Reader | None = None  # deform curves blend 0 -> 1 between keys


def reader_for(group: str, timeline: str) -> Reader | None:
    """None means the timeline is the 0..1 deform blend."""
    if group == "bones":
        return BONE_TIMELINES[timeline]
    if group == "slots":
        return SLOT_TIMELINES.get(timeline)
    if group == "ik":
        return IK_READER
    if group == "transform":
        return TRANSFORM_READER
    if group == "path":
        return PATH_TIMELINES.get(timeline)
    if group == "deform":
        return DEFORM_READER
    return None


CHANNELS: dict[str, tuple[str, ...]] = {
    "rotate": ("rotate",), "translate": ("x", "y"), "translatex": ("x",), "translatey": ("y",),
    "scale": ("scale x", "scale y"), "scalex": ("scale x",), "scaley": ("scale y",),
    "shear": ("shear x", "shear y"), "shearx": ("shear x",), "sheary": ("shear y",),
    "rgba": ("red", "green", "blue", "alpha"), "rgb": ("red", "green", "blue"), "alpha": ("alpha",),
    "rgba2": ("red", "green", "blue", "alpha", "dark red", "dark green", "dark blue"),
    "rgb2": ("red", "green", "blue", "dark red", "dark green", "dark blue"),
    "ik": ("mix", "softness"), "deform": ("blend",),
    "transform": ("mix rotate", "mix x", "mix y", "mix scale x", "mix scale y", "mix shear y"),
    "position": ("position",), "spacing": ("spacing",), "mix": ("mix rotate", "mix x", "mix y"),
}


def channel_names(group: str, timeline: str) -> tuple[str, ...]:
    """Value channels a curve timeline interpolates; () for timelines without curves."""
    if group in ("bones", "slots", "ik", "deform", "transform", "path"):
        return CHANNELS.get(timeline, ())
    return ()


def _set_color_channel(key: dict[str, Any], field: str, index: int, value: float, rgb_only: bool) -> None:
    c = Color.from_hex(key[field][:6] if rgb_only else key[field])
    parts = [c.r, c.g, c.b, c.a]
    parts[index] = max(0.0, min(1.0, value))
    text = Color(*parts).to_hex()
    key[field] = text[:6] if rgb_only else text


def _write_mixes(key: dict[str, Any], current: list[float], fields: tuple[str, ...], channel: int,
                 value: float) -> None:
    """Write all mix fields explicitly: the JSON defaults (mixY = mixX, …) would otherwise follow
    the edited channel."""
    current[channel] = max(0.0, min(1.0, value))
    for field, v in zip(fields, current):
        key[field] = round(v, 4)


def write_channel(group: str, timeline: str, key: dict[str, Any], channel: int, value: float) -> None:
    """Set one value channel of a key (the inverse of the timeline's reader)."""
    value = round(float(value), 4)
    if group == "bones":
        if timeline in ("translate", "scale", "shear"):
            key["xy"[channel]] = value
        else:
            key["value"] = value
    elif group == "ik":
        key[("mix", "softness")[channel]] = max(0.0, value) if channel else max(0.0, min(1.0, value))
    elif group == "transform":
        _write_mixes(key, TRANSFORM_READER(key), TRANSFORM_FIELDS, channel, value)
    elif group == "path" and timeline == "mix":
        _write_mixes(key, PATH_MIX_READER(key), PATH_MIX_FIELDS, channel, value)
    elif group == "path":
        key["value"] = value
    elif group == "slots":
        if timeline == "alpha":
            key["value"] = max(0.0, min(1.0, value))
        elif timeline in ("rgba", "rgb"):
            _set_color_channel(key, "color", channel, value, timeline == "rgb")
        elif timeline in ("rgba2", "rgb2"):
            light = 4 if timeline == "rgba2" else 3
            if channel < light:
                _set_color_channel(key, "light", channel, value, timeline == "rgb2")
            else:
                _set_color_channel(key, "dark", channel - light, value, True)
        else:
            raise ValueError(f"slot timeline '{timeline}' has no value channels")
    else:
        raise ValueError(f"{group} keys have no editable value channels")


def _values(keys: list[dict[str, Any]], read: Reader | None) -> list[list[float]]:
    if read is None:  # deform: every segment goes 0 -> 1
        return [[0.0] for _ in keys]
    return [read(k) for k in keys]


def _ends(keys, values, read, i) -> tuple[list[float], list[float]]:
    if read is None:
        return [0.0], [1.0]
    return values[i], values[i + 1]


Snapshot = dict[int, list[tuple[str, tuple[float, float, float, float]]]]


def snapshot(keys: list[dict[str, Any]], read: Reader | None) -> Snapshot:
    """Each bezier key's handles relative to its segment, keyed by id(key)."""
    values = _values(keys, read)
    snap: Snapshot = {}
    for i in range(len(keys) - 1):
        curve = keys[i].get("curve")
        if not isinstance(curve, list):
            continue
        t0, t1 = keys[i].get("time", 0), keys[i + 1].get("time", 0)
        v0s, v1s = _ends(keys, values, read, i)
        channels = []
        for c in range(len(curve) // 4):
            handles = tuple(curve[c * 4: c * 4 + 4])
            norm = cv.to_normalized(handles, t0, v0s[c], t1, v1s[c]) if t1 > t0 else None
            if norm is not None:
                channels.append(("norm", norm))
            else:  # flat segment with a bump: keep the bump's height in value units
                dt = (t1 - t0) or 1.0
                channels.append(("raw", ((handles[0] - t0) / dt, handles[1] - v0s[c],
                                         (handles[2] - t0) / dt, handles[3] - v0s[c])))
        snap[id(keys[i])] = channels
    return snap


def restore(keys: list[dict[str, Any]], read: Reader | None, snap: Snapshot) -> None:
    """Rebuild absolute handles from a snapshot after keys moved, changed or were added/removed.
    The last key never keeps a curve (it has no segment)."""
    values = _values(keys, read)
    for i, key in enumerate(keys):
        if not isinstance(key.get("curve"), list):
            continue
        channels = snap.get(id(key))
        if i == len(keys) - 1 or channels is None:
            if i == len(keys) - 1:
                key.pop("curve", None)
            continue
        t0, t1 = key.get("time", 0), keys[i + 1].get("time", 0)
        v0s, v1s = _ends(keys, values, read, i)
        out: list[float] = []
        for c, (kind, h) in enumerate(channels):
            if c >= len(v0s):
                break
            if kind == "norm":
                out += cv.to_absolute(h, t0, v0s[c], t1, v1s[c])
            else:
                dt = t1 - t0
                out += [t0 + h[0] * dt, v0s[c] + h[1], t0 + h[2] * dt, v0s[c] + h[3]]
        key["curve"] = [round(v, 5) for v in out]


def preset_curve(keys: list[dict[str, Any]], read: Reader | None, index: int,
                 normalized: tuple[float, float, float, float]) -> list[float] | None:
    """Absolute curve for key index from a normalized preset (same shape on every channel)."""
    if index + 1 >= len(keys):
        return None
    values = _values(keys, read)
    t0, t1 = keys[index].get("time", 0), keys[index + 1].get("time", 0)
    v0s, v1s = _ends(keys, values, read, index)
    return [round(v, 5) for c in range(len(v0s)) for v in cv.to_absolute(normalized, t0, v0s[c], t1, v1s[c])]
