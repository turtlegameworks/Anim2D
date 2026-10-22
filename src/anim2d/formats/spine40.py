"""Conversion between the Spine 3.8 and 4.0 JSON layouts.

Field names and semantics follow the official readers (SkeletonJson.java, branches 3.8 and 4.0).
Differences handled here:
  * curves: 3.8 has one normalized bezier per key ("curve", "c2".."c4"); 4.0 has one absolute
    bezier per value channel ("curve": [cx1, cy1, cx2, cy2, ...]).
  * rotation: 3.8 interpolates along the shortest path; 4.0 interpolates the values directly.
  * timelines: 4.0 adds split timelines (translatex/translatey, scalex/y, shearx/y) and colour
    timelines rgba/rgb/alpha/rgba2/rgb2 (3.8: color/twoColor); rotate keys use "value".
  * constraint mixes: 4.0 mixRotate/mixX/mixY/mixScaleX/mixScaleY/mixShearY.

3.8 -> 4.0 is lossless. 4.0 -> 3.8 is exact where 3.8 can express the data; otherwise the
affected segments are baked into linear keys at the skeleton's frame rate, and a note says so.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ..models.effect import Color
from . import curves as cv

STAMP_40 = "4.0.64"
STAMP_38 = "3.8.99"
EPS = 1e-4


def is_40(data: dict[str, Any]) -> bool:
    return str(data.get("skeleton", {}).get("spine", "")).startswith("4.")


def _wrap(r: float) -> float:
    return r - (16384 - int(16384.499999999996 - r / 360)) * 360


def _rgba(hex_value: str) -> list[float]:
    c = Color.from_hex(hex_value if len(hex_value) == 8 else hex_value + "ff")
    return [c.r, c.g, c.b, c.a]


def _hex(values: list[float], alpha: bool = True) -> str:
    c = Color(*values[:3], values[3] if alpha and len(values) > 3 else 1.0).to_hex()
    return c if alpha else c[:6]


def _normalized_38(key: dict[str, Any]) -> tuple[float, float, float, float] | str | None:
    curve = key.get("curve")
    if curve is None:
        return None
    if curve == "stepped":
        return "stepped"
    if isinstance(curve, list):  # older array form
        return tuple(curve)  # type: ignore[return-value]
    return (float(curve), float(key.get("c2", 0)), float(key.get("c3", 1)), float(key.get("c4", 1)))


def _strip_curve(key: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in key.items() if k not in ("curve", "c2", "c3", "c4")}


# --- timeline descriptions: (read channel values, write a key from values) ----------------

Reader = Callable[[dict[str, Any]], list[float]]
Writer = Callable[[float, list[float], dict[str, Any]], dict[str, Any]]


def _r(v: float) -> float:
    v = round(float(v), 5)
    return 0.0 if v == 0 else v


# --- 3.8 -> 4.0 ---------------------------------------------------------------------------

def _upgrade_keys(keys: list[dict[str, Any]], read: Reader, write: Writer,
                  values: list[list[float]] | None = None) -> list[dict[str, Any]]:
    values = values if values is not None else [read(k) for k in keys]
    out = []
    for i, key in enumerate(keys):
        new = write(key.get("time", 0), values[i], _strip_curve(key))
        norm = _normalized_38(key)
        if norm == "stepped":
            new["curve"] = "stepped"
        elif norm is not None and i + 1 < len(keys):
            t0, t1 = key.get("time", 0), keys[i + 1].get("time", 0)
            new["curve"] = [_r(v) for ch in range(len(values[i]))
                            for v in cv.to_absolute(norm, t0, values[i][ch], t1, values[i + 1][ch])]
        out.append(new)
    return out


def upgrade(data: dict[str, Any], stamp: str = STAMP_40) -> dict[str, Any]:
    """3.8 data -> 4.0 data (a new dict)."""
    out = copy.deepcopy(data)
    out.setdefault("skeleton", {})["spine"] = stamp
    out["skeleton"].pop("hash", None)
    for c in out.get("transform", []):
        for old, new in (("rotateMix", "mixRotate"), ("translateMix", "mixX"), ("scaleMix", "mixScaleX"),
                         ("shearMix", "mixShearY")):
            if old in c:
                c[new] = c.pop(old)
        if "mixX" in c:
            c["mixY"] = c["mixX"]
        if "mixScaleX" in c:
            c["mixScaleY"] = c["mixScaleX"]
    for c in out.get("path", []):
        for old, new in (("rotateMix", "mixRotate"), ("translateMix", "mixX")):
            if old in c:
                c[new] = c.pop(old)
        if "mixX" in c:
            c["mixY"] = c["mixX"]
    for anim in out.get("animations", {}).values():
        if "draworder" in anim:
            anim["drawOrder"] = anim.pop("draworder")
        for bone, tls in anim.get("bones", {}).items():
            new_tls = {}
            for name, keys in tls.items():
                if name == "rotate":
                    unwrapped, prev = [], None
                    for k in keys:  # 3.8 takes the shortest path; make the values say so explicitly
                        a = float(k.get("angle", 0))
                        prev = a if prev is None else prev + _wrap(a - prev)
                        unwrapped.append([prev])
                    new_tls[name] = _upgrade_keys(keys, lambda k: [], lambda t, v, k: {"time": _r(t), "value": _r(v[0])},
                                                  values=unwrapped)
                else:
                    default = 1.0 if name == "scale" else 0.0
                    new_tls[name] = _upgrade_keys(
                        keys, lambda k, d=default: [float(k.get("x", d)), float(k.get("y", d))],
                        lambda t, v, k: {"time": _r(t), "x": _r(v[0]), "y": _r(v[1])})
            anim["bones"][bone] = new_tls
        for slot, tls in anim.get("slots", {}).items():
            new_tls = {}
            for name, keys in tls.items():
                if name == "attachment":
                    new_tls[name] = keys
                elif name == "color":
                    new_tls["rgba"] = _upgrade_keys(keys, lambda k: _rgba(k["color"]),
                                                    lambda t, v, k: {"time": _r(t), "color": _hex(v)})
                elif name == "twoColor":
                    new_tls["rgba2"] = _upgrade_keys(
                        keys, lambda k: _rgba(k["light"]) + _rgba(k["dark"])[:3],
                        lambda t, v, k: {"time": _r(t), "light": _hex(v[:4]), "dark": _hex(v[4:7], False)})
            anim["slots"][slot] = new_tls
        for name, keys in anim.get("ik", {}).items():
            anim["ik"][name] = _upgrade_keys(
                keys, lambda k: [float(k.get("mix", 1)), float(k.get("softness", 0))],
                lambda t, v, k: {**k, "time": _r(t), "mix": _r(v[0]), "softness": _r(v[1])})
            for k in anim["ik"][name]:
                if k.get("softness") == 0:
                    k.pop("softness")
        for name, keys in anim.get("transform", {}).items():
            anim["transform"][name] = _upgrade_keys(
                keys, lambda k: [float(k.get("rotateMix", 1)), float(k.get("translateMix", 1)),
                                 float(k.get("translateMix", 1)), float(k.get("scaleMix", 1)),
                                 float(k.get("scaleMix", 1)), float(k.get("shearMix", 1))],
                lambda t, v, k: {"time": _r(t), "mixRotate": _r(v[0]), "mixX": _r(v[1]), "mixY": _r(v[2]),
                                 "mixScaleX": _r(v[3]), "mixScaleY": _r(v[4]), "mixShearY": _r(v[5])})
        for name, tls in anim.get("path", {}).items():
            new_tls = {}
            for tl, keys in tls.items():
                if tl in ("position", "spacing"):
                    new_tls[tl] = _upgrade_keys(keys, lambda k, tl=tl: [float(k.get(tl, 0))],
                                                lambda t, v, k: {"time": _r(t), "value": _r(v[0])})
                elif tl == "mix":
                    new_tls[tl] = _upgrade_keys(
                        keys, lambda k: [float(k.get("rotateMix", 1)), float(k.get("translateMix", 1)),
                                         float(k.get("translateMix", 1))],
                        lambda t, v, k: {"time": _r(t), "mixRotate": _r(v[0]), "mixX": _r(v[1]), "mixY": _r(v[2])})
            anim["path"][name] = new_tls
        for skin in anim.get("deform", {}).values():
            for slot in skin.values():
                for att, keys in slot.items():
                    new_keys = []
                    for i, k in enumerate(keys):
                        new = _strip_curve(k)
                        norm = _normalized_38(k)
                        if norm == "stepped":
                            new["curve"] = "stepped"
                        elif norm is not None and i + 1 < len(keys):
                            new["curve"] = [_r(v) for v in cv.to_absolute(norm, k.get("time", 0), 0.0,
                                                                           keys[i + 1].get("time", 0), 1.0)]
                        new_keys.append(new)
                    slot[att] = new_keys
    return out


# --- 4.0 -> 3.8 ---------------------------------------------------------------------------

class _Channels:
    """A 4.0 timeline as (time, values, curve, extras) rows with value_at() evaluation."""

    def __init__(self, keys: list[dict[str, Any]], read: Reader, defaults: list[float]):
        self.keys = keys
        self.values = [read(k) for k in keys]
        self.defaults = defaults

    def value_at(self, time: float, channel: int) -> float:
        i = cv.find_segment(self.keys, time)
        if i is None:
            return self.defaults[channel]
        nxt = self.keys[i + 1] if i + 1 < len(self.keys) else None
        v1 = self.values[i + 1][channel] if nxt is not None else 0.0
        return cv.segment_value(self.keys[i], nxt, time, channel, self.values[i][channel], v1)

    def times(self) -> list[float]:
        return [k.get("time", 0) for k in self.keys]


def _segment_curve_38(sources: list[tuple[_Channels, int, int]], t0: float, t1: float,
                      rotation: bool) -> tuple[str, Any]:
    """How a merged segment [t0, t1] can be written in 3.8: ("linear"|"stepped"|"bezier", normalized) or
    ("bake", None). sources: (timeline, key index at t0, channel)."""
    kinds, normals = set(), []
    for tl, i, ch in sources:
        if i is None or i + 1 >= len(tl.keys) or abs(tl.keys[i + 1].get("time", 0) - t1) > 1e-6 \
                or abs(tl.keys[i].get("time", 0) - t0) > 1e-6:
            # The segment does not line up with this timeline's own keys.
            seg_i = cv.find_segment(tl.keys, t0)
            if seg_i is None or seg_i + 1 >= len(tl.keys):
                kinds.add("linear")  # holding a value (before the first or after the last key)
                continue
            if tl.keys[seg_i].get("curve") == "stepped":
                return "bake", None
            if cv.channel_handles(tl.keys[seg_i].get("curve"), ch) is None:
                kinds.add("linear")  # a linear sub-span of a linear segment is linear
                continue
            return "bake", None
        curve = tl.keys[i].get("curve")
        if curve == "stepped":
            kinds.add("stepped")
            continue
        handles = cv.channel_handles(curve, ch)
        v0, v1 = tl.values[i][ch], tl.values[i + 1][ch]
        if rotation and abs(v1 - v0) >= 180 - 1e-6:
            return "bake", None
        if handles is None:
            kinds.add("linear")
            continue
        norm = cv.to_normalized(handles, t0, v0, t1, v1)
        if norm is None:
            return "bake", None
        if abs(v1 - v0) < 1e-9:
            continue  # a flat channel accepts any curve
        kinds.add("bezier")
        normals.append(norm)
    if "stepped" in kinds:
        return ("stepped", None) if kinds == {"stepped"} else ("bake", None)
    if "bezier" in kinds:
        if "linear" in kinds:
            linear = (1 / 3, 1 / 3, 2 / 3, 2 / 3)  # a linear channel equals this bezier
            normals.append(linear)
        first = normals[0]
        if all(all(abs(a - b) < EPS for a, b in zip(first, n)) for n in normals[1:]):
            return "bezier", first
        return "bake", None
    return "linear", None


def _bake_times(values_at: Callable[[float], list[float]], t0: float, t1: float, tolerance: float,
                rotation: bool, min_step: float) -> list[float]:
    """Interior sample times so that straight lines between samples stay within tolerance of the curve
    (checked at the quarter points), and rotation steps stay below 180°."""
    times: list[float] = []

    def split(a: float, b: float, va: list[float], vb: list[float]) -> None:
        mid = (a + b) / 2
        vm = values_at(mid)
        needs = b - a > min_step and (
            any(abs(m - (x + y) / 2) > tolerance for m, x, y in zip(vm, va, vb)) or
            any(abs(values_at(a + (b - a) * f)[c] - (va[c] + (vb[c] - va[c]) * f)) > tolerance
                for f in (0.25, 0.75) for c in range(len(va))) or
            (rotation and abs(vb[0] - va[0]) >= 179))
        if not needs:
            return
        split(a, mid, va, vm)
        times.append(mid)
        split(mid, b, vm, vb)

    split(t0, t1, values_at(t0), values_at(t1))
    return times


def _merge_timeline(sources: list[tuple[_Channels | None, int]], defaults: list[float], fps: float,
                    write: Callable[[float, list[float]], dict[str, Any]], rotation: bool = False,
                    stepped_extras: Callable[[float], dict[str, Any]] | None = None,
                    tolerance: float = 0.01) -> tuple[list[dict], bool]:
    """Build 3.8 keys for channels that may come from several 4.0 timelines. Returns (keys, baked)."""
    timelines = [s for s, _ in sources if s is not None]
    times = sorted({t for tl in timelines for t in tl.times()})
    if not times:
        return [], False

    def values_at(t: float) -> list[float]:
        return [tl.value_at(t, ch) if tl is not None else defaults[n] for n, (tl, ch) in enumerate(sources)]

    keys: list[dict[str, Any]] = []
    baked = False
    for n, t0 in enumerate(times):
        key = write(t0, values_at(t0))
        if stepped_extras:
            key.update(stepped_extras(t0))
        keys.append(key)
        if n + 1 == len(times):
            break
        t1 = times[n + 1]
        refs = []
        for tl, ch in sources:
            if tl is None:
                continue
            idx = next((i for i, k in enumerate(tl.keys) if abs(k.get("time", 0) - t0) < 1e-6), None)
            refs.append((tl, idx, ch))
        kind, norm = _segment_curve_38(refs, t0, t1, rotation)
        if kind == "stepped":
            key["curve"] = "stepped"
        elif kind == "bezier":
            key["curve"], key["c2"], key["c3"], key["c4"] = (_r(v) for v in norm)
        elif kind == "bake":
            baked = True
            # Values just before t1 matter for stepped sub-spans: sample on (t0, t1) only.
            for t in _bake_times(values_at, t0, t1 - 1e-6, tolerance, rotation, 1 / (fps * 32)):
                extra = write(t, values_at(t))
                if stepped_extras:
                    extra.update(stepped_extras(t))
                keys.append(extra)
    keys.sort(key=lambda k: k.get("time", 0))
    return keys, baked


def downgrade(data: dict[str, Any], stamp: str = STAMP_38) -> tuple[dict[str, Any], list[str]]:
    """4.0 data -> 3.8 data (a new dict) and notes about anything that had to be baked."""
    out = copy.deepcopy(data)
    out.setdefault("skeleton", {})["spine"] = stamp
    out["skeleton"].pop("hash", None)
    fps = float(out["skeleton"].get("fps", 30) or 30)
    notes: list[str] = []
    slot_color = {s["name"]: _rgba(s.get("color", "ffffffff")) for s in out.get("slots", [])}
    slot_dark = {s["name"]: _rgba(s["dark"] + "ff")[:3] if s.get("dark") else [0.0, 0.0, 0.0]
                 for s in out.get("slots", [])}
    for c in out.get("transform", []):
        if "mixRotate" in c:
            c["rotateMix"] = c.pop("mixRotate")
        if "mixX" in c or "mixY" in c:
            c["translateMix"] = c.pop("mixX", c.get("mixY", 1))
            c.pop("mixY", None)
        if "mixScaleX" in c or "mixScaleY" in c:
            c["scaleMix"] = c.pop("mixScaleX", c.get("mixScaleY", 1))
            c.pop("mixScaleY", None)
        if "mixShearY" in c:
            c["shearMix"] = c.pop("mixShearY")
    for c in out.get("path", []):
        if "mixRotate" in c:
            c["rotateMix"] = c.pop("mixRotate")
        if "mixX" in c or "mixY" in c:
            c["translateMix"] = c.pop("mixX", c.get("mixY", 1))
            c.pop("mixY", None)

    for anim_name, anim in out.get("animations", {}).items():
        baked_any = False
        for bone, tls in anim.get("bones", {}).items():
            new_tls = {}
            if "rotate" in tls:
                tl = _Channels(tls["rotate"], lambda k: [float(k.get("value", 0))], [0.0])
                new_tls["rotate"], b = _merge_timeline([(tl, 0)], [0.0], fps,
                                                       lambda t, v: {"time": _r(t), "angle": _r(v[0])}, rotation=True)
                baked_any |= b
            for base, default in (("translate", 0.0), ("scale", 1.0), ("shear", 0.0)):
                if base in tls:
                    tl = _Channels(tls[base], lambda k, d=default: [float(k.get("x", d)), float(k.get("y", d))],
                                   [default, default])
                    sources = [(tl, 0), (tl, 1)]
                else:
                    sx = _Channels(tls[base + "x"], lambda k, d=default: [float(k.get("value", d))], [default]) \
                        if base + "x" in tls else None
                    sy = _Channels(tls[base + "y"], lambda k, d=default: [float(k.get("value", d))], [default]) \
                        if base + "y" in tls else None
                    if sx is None and sy is None:
                        continue
                    sources = [(sx, 0), (sy, 0)]
                new_tls[base], b = _merge_timeline(sources, [default, default], fps,
                                                   lambda t, v: {"time": _r(t), "x": _r(v[0]), "y": _r(v[1])},
                                                   tolerance=0.0002 if base == "scale" else 0.01)
                baked_any |= b
            anim["bones"][bone] = new_tls
        for slot, tls in anim.get("slots", {}).items():
            new_tls = {}
            if "attachment" in tls:
                new_tls["attachment"] = tls["attachment"]
            setup = slot_color.get(slot, [1.0, 1.0, 1.0, 1.0])
            if any(n in tls for n in ("rgba", "rgb", "alpha")):
                if "rgba" in tls:
                    tl = _Channels(tls["rgba"], lambda k: _rgba(k["color"]), setup)
                    sources = [(tl, 0), (tl, 1), (tl, 2), (tl, 3)]
                else:
                    rgb = _Channels(tls["rgb"], lambda k: _rgba(k["color"])[:3], setup[:3]) if "rgb" in tls else None
                    alpha = _Channels(tls["alpha"], lambda k: [float(k.get("value", 1))], setup[3:]) \
                        if "alpha" in tls else None
                    sources = [(rgb, 0), (rgb, 1), (rgb, 2), (alpha, 0)]
                new_tls["color"], b = _merge_timeline(sources, setup, fps,
                                                      lambda t, v: {"time": _r(t), "color": _hex(v)}, tolerance=0.002)
                baked_any |= b
            if "rgba2" in tls or "rgb2" in tls:
                dark = slot_dark.get(slot, [0.0, 0.0, 0.0])
                if "rgba2" in tls:
                    tl = _Channels(tls["rgba2"], lambda k: _rgba(k["light"]) + _rgba(k["dark"] + "ff")[:3], setup + dark)
                    sources = [(tl, c) for c in range(7)]
                else:
                    tl = _Channels(tls["rgb2"], lambda k: _rgba(k["light"] + "ff")[:3] + _rgba(k["dark"] + "ff")[:3],
                                   setup[:3] + dark)
                    sources = [(tl, 0), (tl, 1), (tl, 2), (None, 0), (tl, 3), (tl, 4), (tl, 5)]
                new_tls["twoColor"], b = _merge_timeline(
                    sources, setup + dark, fps,
                    lambda t, v: {"time": _r(t), "light": _hex(v[:4]), "dark": _hex(v[4:7], False)}, tolerance=0.002)
                baked_any |= b
            anim["slots"][slot] = new_tls
        for name, keys in anim.get("ik", {}).items():
            tl = _Channels(keys, lambda k: [float(k.get("mix", 1)), float(k.get("softness", 0))], [1.0, 0.0])

            def extras(t: float, keys=keys) -> dict[str, Any]:
                i = cv.find_segment(keys, t)
                k = keys[i] if i is not None else {}
                return {f: k[f] for f in ("bendPositive", "compress", "stretch") if f in k}
            anim["ik"][name], b = _merge_timeline([(tl, 0), (tl, 1)], [1.0, 0.0], fps,
                                                  lambda t, v: {"time": _r(t), "mix": _r(v[0]), "softness": _r(v[1])},
                                                  stepped_extras=extras, tolerance=0.001)
            baked_any |= b
        for name, keys in anim.get("transform", {}).items():
            tl = _Channels(keys, lambda k: [float(k.get("mixRotate", 1)), float(k.get("mixX", 1)),
                                            float(k.get("mixScaleX", 1)), float(k.get("mixShearY", 1))], [1.0] * 4)
            anim["transform"][name], b = _merge_timeline(
                [(tl, c) for c in range(4)], [1.0] * 4, fps,
                lambda t, v: {"time": _r(t), "rotateMix": _r(v[0]), "translateMix": _r(v[1]),
                              "scaleMix": _r(v[2]), "shearMix": _r(v[3])}, tolerance=0.001)
            baked_any |= b
        for name, tls in anim.get("path", {}).items():
            new_tls = {}
            for tl_name, keys in tls.items():
                if tl_name in ("position", "spacing"):
                    tl = _Channels(keys, lambda k: [float(k.get("value", 0))], [0.0])
                    new_tls[tl_name], b = _merge_timeline([(tl, 0)], [0.0], fps,
                                                          lambda t, v, n=tl_name: {"time": _r(t), n: _r(v[0])})
                else:
                    tl = _Channels(keys, lambda k: [float(k.get("mixRotate", 1)), float(k.get("mixX", 1))], [1.0, 1.0])
                    new_tls[tl_name], b = _merge_timeline(
                        [(tl, 0), (tl, 1)], [1.0, 1.0], fps,
                        lambda t, v: {"time": _r(t), "rotateMix": _r(v[0]), "translateMix": _r(v[1])}, tolerance=0.001)
                baked_any |= b
            anim["path"][name] = new_tls
        for skin in anim.get("deform", {}).values():
            for slot in skin.values():
                for att, keys in slot.items():
                    new_keys = []
                    for i, k in enumerate(keys):
                        new = _strip_curve(k)
                        curve = k.get("curve")
                        if curve == "stepped":
                            new["curve"] = "stepped"
                        elif isinstance(curve, list) and i + 1 < len(keys):
                            norm = cv.to_normalized(tuple(curve[:4]), k.get("time", 0), 0.0,
                                                    keys[i + 1].get("time", 0), 1.0)
                            if norm is not None:
                                new["curve"], new["c2"], new["c3"], new["c4"] = (_r(v) for v in norm)
                        new_keys.append(new)
                    slot[att] = new_keys
        if baked_any:
            notes.append(f"Animation '{anim_name}': some curves (separate X/Y or colour channels, per-channel "
                         f"curves, or rotations over 180° between keys) were converted to extra linear keys for 3.8.")
    return out, notes
