"""Spine 4.0 binary (.skel) export.

Mirrors the official 4.0 SkeletonBinary reader field by field (spine-runtimes branch 4.0), from
Spine 4.0 JSON data as the editor exports it. Defaults for missing JSON fields are the ones the
4.0 SkeletonJson reader uses, so a .skel loads into the same skeleton as the JSON does.

Encoding (libgdx DataInput): big-endian floats/ints/shorts, varints of 7 bits per byte (low
group first; "optimize positive" off means zigzag), strings as varint(byte count + 1) and
UTF-8 per UTF-16 unit (0 = null, 1 = ""), string refs as varint(index + 1) into a table
written after the header (0 = null).
"""

from __future__ import annotations

import struct
from typing import Any, Iterable

from . import timelines40 as tl

BONE_TYPES = {"rotate": 0, "translate": 1, "translatex": 2, "translatey": 3, "scale": 4, "scalex": 5,
              "scaley": 6, "shear": 7, "shearx": 8, "sheary": 9}
SLOT_TYPES = {"attachment": 0, "rgba": 1, "rgb": 2, "rgba2": 3, "rgb2": 4, "alpha": 5}
PATH_TYPES = {"position": 0, "spacing": 1, "mix": 2}
CURVE_LINEAR, CURVE_STEPPED, CURVE_BEZIER = 0, 1, 2

TRANSFORM_MODES = ["normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection"]
BLEND_MODES = ["normal", "additive", "multiply", "screen"]
POSITION_MODES = ["fixed", "percent"]
SPACING_MODES = ["length", "fixed", "percent", "proportional"]
ROTATE_MODES = ["tangent", "chain", "chainScale"]
ATTACHMENT_TYPES = ["region", "boundingbox", "mesh", "linkedmesh", "path", "point", "clipping"]

BONE_COLOR = "9b9b9bff"  # editor defaults for nonessential colors
BOX_COLOR, PATH_COLOR, POINT_COLOR, CLIP_COLOR = "60f000ff", "ff7f00ff", "f1f100ff", "ce3a3aff"


class BinaryExportError(ValueError):
    pass


class _Out:
    def __init__(self, strings: "_Strings") -> None:
        self.buf = bytearray()
        self.strings = strings

    def byte(self, v: int) -> None:
        self.buf.append(v & 0xFF)

    def bool(self, v: Any) -> None:
        self.buf.append(1 if v else 0)

    def varint(self, v: int, optimize_positive: bool = True) -> None:
        v = int(v)
        if not optimize_positive:
            v = (v << 1) ^ (v >> 31)
        v &= 0xFFFFFFFF
        while True:
            if v < 0x80:
                self.buf.append(v)
                return
            self.buf.append((v & 0x7F) | 0x80)
            v >>= 7

    def int32(self, v: int) -> None:
        self.buf += struct.pack(">I", int(v) & 0xFFFFFFFF)

    def short(self, v: int) -> None:
        self.buf += struct.pack(">h", int(v))

    def float(self, v: Any) -> None:
        self.buf += struct.pack(">f", float(v))

    def floats(self, values: Iterable[Any]) -> None:
        for v in values:
            self.float(v)

    def string(self, s: str | None) -> None:
        if s is None:
            self.varint(0)
            return
        data = _encode(s)
        self.varint(len(data) + 1)
        self.buf += data

    def ref(self, s: str | None) -> None:
        self.varint(0 if s is None else self.strings.index(s) + 1)


class _Strings:
    def __init__(self) -> None:
        self.items: list[str] = []
        self._index: dict[str, int] = {}

    def index(self, s: str) -> int:
        if s not in self._index:
            self._index[s] = len(self.items)
            self.items.append(s)
        return self._index[s]


def _encode(s: str) -> bytes:
    """UTF-8 of each UTF-16 unit, the only form the runtime readers decode (1 to 3 bytes)."""
    raw = s.encode("utf-16-be", "surrogatepass")
    out = bytearray()
    for i in range(0, len(raw), 2):
        c = (raw[i] << 8) | raw[i + 1]
        if c < 0x80:
            out.append(c)
        elif c < 0x800:
            out += bytes((0xC0 | c >> 6, 0x80 | c & 0x3F))
        else:
            out += bytes((0xE0 | c >> 12, 0x80 | (c >> 6) & 0x3F, 0x80 | c & 0x3F))
    return bytes(out)


def _rgba_bytes(hex_value: str | None, default: str = "ffffffff") -> bytes:
    h = (hex_value or default).strip().lstrip("#")
    if len(h) == 6:
        h += "ff"
    try:
        return bytes.fromhex(h[:8])
    except ValueError:
        raise BinaryExportError(f"bad color '{hex_value}'") from None


def _rgba_int(hex_value: str | None, default: str = "ffffffff") -> int:
    return int.from_bytes(_rgba_bytes(hex_value, default), "big")


def _enum(names: list[str], value: str | None, default: str, what: str) -> int:
    value = value or default
    if value not in names:
        raise BinaryExportError(f"unknown {what} '{value}'")
    return names.index(value)


def _index(table: dict[str, int], name: str, what: str) -> int:
    if name not in table:
        raise BinaryExportError(f"{what} '{name}' not found")
    return table[name]


def write_skel(data: dict[str, Any], nonessential: bool = True) -> bytes:
    """Spine 4.0 JSON data (dict) -> .skel bytes. Raises BinaryExportError on data it cannot write."""
    version = str(data.get("skeleton", {}).get("spine", ""))
    if not version.startswith("4.0"):
        raise BinaryExportError(f"binary export needs Spine 4.0 data, not '{version or 'unknown'}'")
    return _Writer(data, nonessential).write()


class _Writer:
    def __init__(self, data: dict[str, Any], nonessential: bool) -> None:
        self.data = data
        self.nonessential = nonessential
        self.strings = _Strings()
        self.bones = {b["name"]: i for i, b in enumerate(data.get("bones", []))}
        self.slots = {s["name"]: i for i, s in enumerate(data.get("slots", []))}
        self.ik = {c["name"]: i for i, c in enumerate(data.get("ik", []))}
        self.transform = {c["name"]: i for i, c in enumerate(data.get("transform", []))}
        self.path = {c["name"]: i for i, c in enumerate(data.get("path", []))}
        self.path_data = {c["name"]: c for c in data.get("path", [])}
        self.events = {name: i for i, name in enumerate(data.get("events", {}))}
        skins = data.get("skins", [])
        if isinstance(skins, dict):  # 3.7-style map; not expected in 4.0 data but harmless
            skins = [{"name": n, "attachments": a} for n, a in skins.items()]
        self.default_skin = next((s for s in skins if s.get("name") == "default"), None)
        self.other_skins = [s for s in skins if s.get("name") != "default"]
        ordered = ([self.default_skin] if self.default_skin and self.default_skin.get("attachments") else []) \
            + self.other_skins
        self.skin_index = {s["name"]: i for i, s in enumerate(ordered)}
        self.skin_by_name = {s["name"]: s for s in skins}

    # -- skeleton ---------------------------------------------------------------------------
    def write(self) -> bytes:
        body = _Out(self.strings)
        self._bones(body)
        self._slots(body)
        self._constraints(body)
        self._skins(body)
        self._events(body)
        anims = self.data.get("animations", {})
        body.varint(len(anims))
        for name, anim in anims.items():
            body.string(name)
            self._animation(body, anim)

        head = _Out(self.strings)
        skeleton = self.data.get("skeleton", {})
        head.buf += b"\0" * 8  # hash: none
        head.string(str(skeleton.get("spine", "")))
        head.floats(skeleton.get(k, 0) for k in ("x", "y", "width", "height"))
        head.bool(self.nonessential)
        if self.nonessential:
            head.float(skeleton.get("fps", 30))
            head.string(skeleton.get("images") or "")
            head.string(skeleton.get("audio") or "")
        head.varint(len(self.strings.items))
        for s in self.strings.items:
            head.string(s)
        return bytes(head.buf + body.buf)

    def _bones(self, out: _Out) -> None:
        bones = self.data.get("bones", [])
        out.varint(len(bones))
        for i, b in enumerate(bones):
            out.string(b["name"])
            if i > 0:
                out.varint(_index(self.bones, b.get("parent", ""), "parent bone"))
            elif b.get("parent"):
                raise BinaryExportError("the first bone must be the root")
            out.floats([b.get("rotation", 0), b.get("x", 0), b.get("y", 0), b.get("scaleX", 1), b.get("scaleY", 1),
                        b.get("shearX", 0), b.get("shearY", 0), b.get("length", 0)])
            out.varint(_enum(TRANSFORM_MODES, b.get("transform"), "normal", "bone transform mode"))
            out.bool(b.get("skin", False))
            if self.nonessential:
                out.int32(_rgba_int(b.get("color"), BONE_COLOR))

    def _slots(self, out: _Out) -> None:
        slots = self.data.get("slots", [])
        out.varint(len(slots))
        for s in slots:
            out.string(s["name"])
            out.varint(_index(self.bones, s["bone"], "slot bone"))
            out.int32(_rgba_int(s.get("color")))
            dark = s.get("dark")
            out.int32(-1 if dark is None else int.from_bytes(_rgba_bytes(dark[:6])[:3], "big"))
            out.ref(s.get("attachment"))
            out.varint(_enum(BLEND_MODES, s.get("blend"), "normal", "blend mode"))

    def _constraint_head(self, out: _Out, c: dict[str, Any]) -> None:
        out.string(c["name"])
        out.varint(c.get("order", 0))
        out.bool(c.get("skin", False))
        bones = c.get("bones", [])
        out.varint(len(bones))
        for b in bones:
            out.varint(_index(self.bones, b, "constraint bone"))

    def _constraints(self, out: _Out) -> None:
        ik = self.data.get("ik", [])
        out.varint(len(ik))
        for c in ik:
            self._constraint_head(out, c)
            out.varint(_index(self.bones, c["target"], "IK target"))
            out.floats([c.get("mix", 1), c.get("softness", 0)])
            out.byte(1 if c.get("bendPositive", True) else -1)
            out.bool(c.get("compress", False))
            out.bool(c.get("stretch", False))
            out.bool(c.get("uniform", False))

        transform = self.data.get("transform", [])
        out.varint(len(transform))
        for c in transform:
            self._constraint_head(out, c)
            out.varint(_index(self.bones, c["target"], "transform target"))
            out.bool(c.get("local", False))
            out.bool(c.get("relative", False))
            mix_x = c.get("mixX", 1)
            mix_scale_x = c.get("mixScaleX", 1)
            out.floats([c.get("rotation", 0), c.get("x", 0), c.get("y", 0), c.get("scaleX", 0), c.get("scaleY", 0),
                        c.get("shearY", 0), c.get("mixRotate", 1), mix_x, c.get("mixY", mix_x), mix_scale_x,
                        c.get("mixScaleY", mix_scale_x), c.get("mixShearY", 1)])

        paths = self.data.get("path", [])
        out.varint(len(paths))
        for c in paths:
            self._constraint_head(out, c)
            out.varint(_index(self.slots, c["target"], "path target slot"))
            out.varint(_enum(POSITION_MODES, c.get("positionMode"), "percent", "position mode"))
            out.varint(_enum(SPACING_MODES, c.get("spacingMode"), "length", "spacing mode"))
            out.varint(_enum(ROTATE_MODES, c.get("rotateMode"), "tangent", "rotate mode"))
            out.floats([c.get("rotation", 0), c.get("position", 0), c.get("spacing", 0), c.get("mixRotate", 1),
                        c.get("mixX", 1), c.get("mixY", 1)])

    # -- skins and attachments --------------------------------------------------------------
    def _skins(self, out: _Out) -> None:
        default = self.default_skin
        if default is not None and any(default.get(k) for k in ("bones", "ik", "transform", "path")):
            raise BinaryExportError("the default skin cannot hold skin bones or constraints in binary data")
        self._skin_attachments(out, (default or {}).get("attachments", {}))
        out.varint(len(self.other_skins))
        for skin in self.other_skins:
            out.ref(skin["name"])
            for key, table in (("bones", self.bones), ("ik", self.ik), ("transform", self.transform),
                               ("path", self.path)):
                names = skin.get(key, [])
                out.varint(len(names))
                for n in names:
                    out.varint(_index(table, n, f"skin {key[:-1] if key == 'bones' else key}"))
            self._skin_attachments(out, skin.get("attachments", {}))

    def _skin_attachments(self, out: _Out, attachments: dict[str, dict[str, Any]]) -> None:
        out.varint(len(attachments))
        for slot_name, entries in attachments.items():
            out.varint(_index(self.slots, slot_name, "skin slot"))
            out.varint(len(entries))
            for key, att in entries.items():
                out.ref(key)
                self._attachment(out, key, att)

    def _vertices(self, out: _Out, vertices: list[float], vertex_count: int) -> None:
        if len(vertices) == vertex_count * 2:
            out.bool(False)
            out.floats(vertices)
            return
        out.bool(True)
        i = 0
        for _ in range(vertex_count):
            count = int(vertices[i])
            out.varint(count)
            i += 1
            for _ in range(count):
                out.varint(int(vertices[i]))
                out.floats(vertices[i + 1:i + 4])
                i += 4
        if i != len(vertices):
            raise BinaryExportError("weighted vertices do not match the vertex count")

    def _attachment(self, out: _Out, key: str, att: dict[str, Any]) -> None:
        name = att.get("name")
        out.ref(name if name is not None and name != key else None)
        kind = att.get("type", "region")
        if kind == "mesh" and att.get("parent"):
            kind = "linkedmesh"
        if kind == "skinnedmesh":
            kind = "mesh"
        out.byte(_enum(ATTACHMENT_TYPES, kind, "region", "attachment type"))
        ne = self.nonessential
        if kind == "region":
            out.ref(att.get("path"))
            out.floats([att.get("rotation", 0), att.get("x", 0), att.get("y", 0), att.get("scaleX", 1),
                        att.get("scaleY", 1), att["width"], att["height"]])
            out.int32(_rgba_int(att.get("color")))
        elif kind == "boundingbox":
            count = int(att["vertexCount"])
            out.varint(count)
            self._vertices(out, att["vertices"], count)
            if ne:
                out.int32(_rgba_int(att.get("color"), BOX_COLOR))
        elif kind == "mesh":
            uvs = att["uvs"]
            count = len(uvs) // 2
            out.ref(att.get("path"))
            out.int32(_rgba_int(att.get("color")))
            out.varint(count)
            out.floats(uvs)
            self._shorts(out, att["triangles"])
            self._vertices(out, att["vertices"], count)
            out.varint(att.get("hull", 0))
            if ne:
                self._shorts(out, att.get("edges", []))
                out.floats([att.get("width", 0), att.get("height", 0)])
        elif kind == "linkedmesh":
            out.ref(att.get("path"))
            out.int32(_rgba_int(att.get("color")))
            out.ref(att.get("skin"))
            out.ref(att["parent"])
            out.bool(att.get("deform", True))
            if ne:
                out.floats([att.get("width", 0), att.get("height", 0)])
        elif kind == "path":
            count = int(att["vertexCount"])
            out.bool(att.get("closed", False))
            out.bool(att.get("constantSpeed", True))
            out.varint(count)
            self._vertices(out, att["vertices"], count)
            lengths = att.get("lengths", [])
            if len(lengths) != count // 3:
                raise BinaryExportError(f"path '{key}' needs {count // 3} lengths")
            out.floats(lengths)
            if ne:
                out.int32(_rgba_int(att.get("color"), PATH_COLOR))
        elif kind == "point":
            out.floats([att.get("rotation", 0), att.get("x", 0), att.get("y", 0)])
            if ne:
                out.int32(_rgba_int(att.get("color"), POINT_COLOR))
        elif kind == "clipping":
            end = att.get("end")
            out.varint(_index(self.slots, end, "clipping end slot") if end else len(self.slots) - 1)
            count = int(att["vertexCount"])
            out.varint(count)
            self._vertices(out, att["vertices"], count)
            if ne:
                out.int32(_rgba_int(att.get("color"), CLIP_COLOR))

    @staticmethod
    def _shorts(out: _Out, values: list[int]) -> None:
        out.varint(len(values))
        for v in values:
            out.short(v)

    def _events(self, out: _Out) -> None:
        events = self.data.get("events", {})
        out.varint(len(events))
        for name, e in events.items():
            out.ref(name)
            out.varint(e.get("int", 0), optimize_positive=False)
            out.float(e.get("float", 0))
            out.string(e.get("string", ""))
            audio = e.get("audio") or None
            out.string(audio)
            if audio is not None:
                out.floats([e.get("volume", 1), e.get("balance", 0)])

    # -- animations -------------------------------------------------------------------------
    @staticmethod
    def _bezier_count(keys: list[dict[str, Any]], channels: int) -> int:
        return sum(channels for k in keys[:-1] if isinstance(k.get("curve"), list))

    @staticmethod
    def _curve(out: _Out, key: dict[str, Any], t0: float, t1: float, v0: list[float], v1: list[float]) -> None:
        curve = key.get("curve")
        if curve == "stepped":
            out.byte(CURVE_STEPPED)
        elif isinstance(curve, list):
            out.byte(CURVE_BEZIER)
            for c in range(len(v0)):
                h = curve[c * 4:c * 4 + 4]
                if len(h) < 4:  # a channel without handles: straight line, as linear
                    dt, dv = t1 - t0, v1[c] - v0[c]
                    h = [t0 + dt / 3, v0[c] + dv / 3, t0 + dt * 2 / 3, v0[c] + dv * 2 / 3]
                out.floats(h)
        else:
            out.byte(CURVE_LINEAR)

    def _curve_frames(self, out: _Out, keys: list[dict[str, Any]], read, write_values=None,
                      after_values=None) -> None:
        """time + values per frame; after each next frame's values, the curve of the frame before."""
        values = [read(k) for k in keys]
        write_values = write_values or (lambda o, k, v: o.floats(v))
        for i, key in enumerate(keys):
            out.float(key.get("time", 0))
            write_values(out, key, values[i])
            if i > 0:
                prev = keys[i - 1]
                self._curve(out, prev, prev.get("time", 0), key.get("time", 0), values[i - 1], values[i])
            if after_values:
                after_values(out, key)

    def _animation(self, out: _Out, anim: dict[str, Any]) -> None:
        tl_count = (sum(len(t) for t in anim.get("slots", {}).values())
                    + sum(len(t) for t in anim.get("bones", {}).values())
                    + len(anim.get("ik", {})) + len(anim.get("transform", {}))
                    + sum(len(t) for t in anim.get("path", {}).values())
                    + sum(len(a) for skin in anim.get("deform", {}).values() for a in skin.values())
                    + (1 if anim.get("drawOrder") else 0) + (1 if anim.get("events") else 0))
        out.varint(tl_count)

        slots = {n: t for n, t in anim.get("slots", {}).items() if any(t.values())}
        out.varint(len(slots))
        for slot_name, timelines in slots.items():
            timelines = {n: k for n, k in timelines.items() if k}
            out.varint(_index(self.slots, slot_name, "animated slot"))
            out.varint(len(timelines))
            for name, keys in timelines.items():
                if name not in SLOT_TYPES:
                    raise BinaryExportError(f"slot timeline '{name}' is not Spine 4.0 data")
                out.byte(SLOT_TYPES[name])
                out.varint(len(keys))
                if name == "attachment":
                    for k in keys:
                        out.float(k.get("time", 0))
                        out.ref(k.get("name"))
                    continue
                read = _slot_reader(name)
                out.varint(self._bezier_count(keys, len(read(keys[0]))))
                self._curve_frames(out, keys, read, write_values=_color_bytes(name))

        bones = {n: t for n, t in anim.get("bones", {}).items() if any(t.values())}
        out.varint(len(bones))
        for bone_name, timelines in bones.items():
            timelines = {n: k for n, k in timelines.items() if k}
            out.varint(_index(self.bones, bone_name, "animated bone"))
            out.varint(len(timelines))
            for name, keys in timelines.items():
                if name not in BONE_TYPES:
                    raise BinaryExportError(f"bone timeline '{name}' is not Spine 4.0 data")
                read = tl.BONE_TIMELINES[name]
                out.byte(BONE_TYPES[name])
                out.varint(len(keys))
                out.varint(self._bezier_count(keys, len(read(keys[0]))))
                self._curve_frames(out, keys, read)

        ik = {n: k for n, k in anim.get("ik", {}).items() if k}
        out.varint(len(ik))
        for name, keys in ik.items():
            out.varint(_index(self.ik, name, "animated IK constraint"))
            out.varint(len(keys))
            out.varint(self._bezier_count(keys, 2))

            def ik_flags(o: _Out, k: dict[str, Any]) -> None:
                o.byte(1 if k.get("bendPositive", True) else -1)
                o.bool(k.get("compress", False))
                o.bool(k.get("stretch", False))
            self._curve_frames(out, keys, tl.IK_READER, after_values=ik_flags)

        transform = {n: k for n, k in anim.get("transform", {}).items() if k}
        out.varint(len(transform))
        for name, keys in transform.items():
            out.varint(_index(self.transform, name, "animated transform constraint"))
            out.varint(len(keys))
            out.varint(self._bezier_count(keys, 6))
            self._curve_frames(out, keys, _transform_mix)

        paths = {n: {t: k for t, k in tls.items() if k} for n, tls in anim.get("path", {}).items()}
        paths = {n: t for n, t in paths.items() if t}
        out.varint(len(paths))
        for name, timelines in paths.items():
            out.varint(_index(self.path, name, "animated path constraint"))
            out.varint(len(timelines))
            for tname, keys in timelines.items():
                if tname not in PATH_TYPES:
                    raise BinaryExportError(f"path timeline '{tname}' is not Spine 4.0 data")
                read = _path_mix if tname == "mix" else _value0
                out.byte(PATH_TYPES[tname])
                out.varint(len(keys))
                out.varint(self._bezier_count(keys, len(read(keys[0]))))
                self._curve_frames(out, keys, read)

        self._deform(out, anim.get("deform", {}))
        self._draw_order(out, anim.get("drawOrder", []))
        self._event_keys(out, anim.get("events", []))

    def _deform(self, out: _Out, deform: dict[str, Any]) -> None:
        skins = {n: {s: {a: k for a, k in atts.items() if k} for s, atts in slots.items()}
                 for n, slots in deform.items()}
        skins = {n: {s: a for s, a in slots.items() if a} for n, slots in skins.items()}
        skins = {n: s for n, s in skins.items() if s}
        out.varint(len(skins))
        for skin_name, slots in skins.items():
            out.varint(_index(self.skin_index, skin_name, "deform skin"))
            out.varint(len(slots))
            for slot_name, atts in slots.items():
                out.varint(_index(self.slots, slot_name, "deform slot"))
                out.varint(len(atts))
                for att_name, keys in atts.items():
                    out.ref(att_name)
                    out.varint(len(keys))
                    out.varint(self._bezier_count(keys, 1))
                    out.float(keys[0].get("time", 0))
                    for i, key in enumerate(keys):
                        verts = key.get("vertices")
                        if verts:
                            out.varint(len(verts))
                            out.varint(key.get("offset", 0))
                            out.floats(verts)
                        else:
                            out.varint(0)
                        if i + 1 < len(keys):
                            t1 = keys[i + 1].get("time", 0)
                            out.float(t1)
                            self._curve(out, key, key.get("time", 0), t1, [0.0], [1.0])

    def _draw_order(self, out: _Out, keys: list[dict[str, Any]]) -> None:
        out.varint(len(keys))
        for key in keys:
            out.float(key.get("time", 0))
            offsets = key.get("offsets") or []
            out.varint(len(offsets))
            for o in offsets:
                out.varint(_index(self.slots, o["slot"], "draw order slot"))
                out.varint(o["offset"])  # negative offsets go out as 32-bit two's complement

    def _event_keys(self, out: _Out, keys: list[dict[str, Any]]) -> None:
        events = self.data.get("events", {})
        out.varint(len(keys))
        for key in keys:
            name = key["name"]
            data = events.get(name)
            if data is None:
                raise BinaryExportError(f"event '{name}' not found")
            out.float(key.get("time", 0))
            out.varint(self.events[name])
            out.varint(key.get("int", data.get("int", 0)), optimize_positive=False)
            out.float(key.get("float", data.get("float", 0)))
            out.bool("string" in key)
            if "string" in key:
                out.string(key["string"])
            if data.get("audio"):
                out.floats([key.get("volume", data.get("volume", 1)), key.get("balance", data.get("balance", 0))])


def _value0(k: dict[str, Any]) -> list[float]:
    return [float(k.get("value", 0))]


def _transform_mix(k: dict[str, Any]) -> list[float]:
    mix_x = float(k.get("mixX", 1))
    mix_scale_x = float(k.get("mixScaleX", 1))
    return [float(k.get("mixRotate", 1)), mix_x, float(k.get("mixY", mix_x)), mix_scale_x,
            float(k.get("mixScaleY", mix_scale_x)), float(k.get("mixShearY", 1))]


def _path_mix(k: dict[str, Any]) -> list[float]:
    mix_x = float(k.get("mixX", 1))
    return [float(k.get("mixRotate", 1)), mix_x, float(k.get("mixY", mix_x))]


def _slot_reader(name: str):
    """Channel values as the runtime holds them (bytes / 255), so bezier values line up."""
    if name == "alpha":
        return lambda k: [round(float(k.get("value", 0)) * 255) / 255]
    if name in ("rgba", "rgb"):
        n = 4 if name == "rgba" else 3
        return lambda k: [b / 255 for b in _rgba_bytes(k["color"])[:n]]
    n = 4 if name == "rgba2" else 3
    return lambda k: [b / 255 for b in _rgba_bytes(k["light"])[:n]] + [b / 255 for b in _rgba_bytes(k["dark"][:6])[:3]]


def _color_bytes(name: str):
    def write(out: _Out, key: dict[str, Any], values: list[float]) -> None:
        for v in values:
            out.byte(max(0, min(255, round(v * 255))))
    return write
