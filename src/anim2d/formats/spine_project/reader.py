"""Reader for the object stream inside Spine editor project files (.spine).

Grammar (as observed):
  object body   := count:varint, count x (tag:varint, value)
  ref value     := varint: 0 null, 1 new (body follows), n>=2 back-reference to object id n-2
  poly value    := class:varint (0 null), then ref value of that class
  string        := ref value; new strings are Kryo strings
Every new referenced object (strings, enums, lists, colours, objects) takes the next id.
"""
from __future__ import annotations

import struct

# Schemas: class name -> {tag: type}. Types:
#   "f" float, "v" varint, "z" bool byte, "s" string, "e" enum (ordinal+1),
#   ("r", Class) final object ref, "p" polymorphic object (class id first), "i" int32 BE
SCHEMA: dict[str, dict[int, object]] = {}
CLASSES: dict[int, str] = {}  # class id -> class name


class Unknown(Exception):
    pass


class Reader:
    def __init__(self, data: bytes, pos: int = 0, schema=None, classes=None):
        self.d, self.p = data, pos
        self.schema = SCHEMA if schema is None else schema
        self.classes = CLASSES if classes is None else classes
        self.objects: list = []
        self.stack = []
        self.values = []  # 4.2: the value reference space
        self.value_space = set()
        self.track_strings = self.track_enums = True  # 4.2 leaves these (and UNTRACKED classes) untracked
        self.untracked = set(UNTRACKED)

    def byte(self) -> int:
        b = self.d[self.p]
        self.p += 1
        return b

    def varint(self) -> int:
        shift = result = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7

    def float(self) -> float:
        v = struct.unpack(">f", self.d[self.p:self.p + 4])[0]
        self.p += 4
        return v

    def int32(self) -> int:
        v = struct.unpack(">i", self.d[self.p:self.p + 4])[0]
        self.p += 4
        return v

    def kstring(self) -> str:
        """Kryo 5 Output.writeString body (after the reference marker)."""
        b = self.d[self.p]
        if b & 0x80 == 0:  # ascii, last char has the high bit
            start = self.p
            while not self.d[self.p] & 0x80:
                self.p += 1
            self.p += 1
            raw = bytearray(self.d[start:self.p])
            raw[-1] &= 0x7F
            return raw.decode("ascii")
        # utf8: varint with flag bit 0x80 in the first byte, 6 bits + continuation 0x40
        first = self.byte()
        length = first & 0x3F
        if first & 0x40:
            shift = 6
            while True:
                b = self.byte()
                length |= (b & 0x7F) << shift
                if not b & 0x80:
                    break
                shift += 7
        if length == 0:
            return None
        if length == 1:
            return ""
        count = length - 1
        out = []
        for _ in range(count):
            b = self.byte()
            if b < 0x80:
                out.append(chr(b))
            elif b >> 5 == 6:
                out.append(chr(((b & 0x1F) << 6) | (self.byte() & 0x3F)))
            else:
                b2, b3 = self.byte(), self.byte()
                out.append(chr(((b & 0x0F) << 12) | ((b2 & 0x3F) << 6) | (b3 & 0x3F)))
        return "".join(out)

    # --- references -------------------------------------------------------------------
    def space(self, cat):
        """4.2 numbers values (strings, enums, colours, lists, arrays) apart from entities."""
        return self.values if self.value_space and cat in self.value_space else self.objects

    def new_id(self, value, cat="?") -> int:
        sp = self.space(cat)
        sp.append(value)
        return len(sp) - 1

    def ref(self, reader, cat="?"):
        """Reference-tracked value: reader(self, id) builds it after the new marker."""
        m = self.varint()
        if m == 0:
            return None
        sp = self.space(cat)
        if m == 1:
            slot = self.new_id(None, cat)
            value = reader(slot)
            sp[slot] = value
            return value
        return Ref(m - 2, sp[m - 2] if m - 2 < len(sp) else None)

    def string(self, tracked=None):
        if not (self.track_strings if tracked is None else tracked):
            return self.kstring() if self.varint() else None
        return self.ref(lambda _id: self.kstring(), "str")

    def enum(self, tracked=None):
        if not (self.track_enums if tracked is None else tracked):
            return ("enum", self.varint() - 1) if self.varint() else None
        return self.ref(lambda _id: ("enum", self.varint() - 1), "enum")

    def obj(self, cls: str):
        if cls in self.untracked:  # no reference id: 0 null, 1 present
            return SPECIAL[cls](self) if self.varint() else None
        return self.ref(lambda _id: self.body(cls, _id), cls)

    def poly(self):
        cid = self.varint()
        if cid == 0:
            return None
        cls = self.classes.get(cid)
        if cls is None:
            raise Unknown(f"class id 0x{cid:x} at {self.p - 1}")
        return self.obj(cls)

    def body(self, cls: str, oid: int):
        if cls in SPECIAL:
            return SPECIAL[cls](self)
        schema = self.schema.get(cls)
        if schema is None:
            raise Unknown(f"no schema for {cls} at {self.p}")
        count = self.varint()
        out = {"_class": cls, "_id": oid, "_pos": self.p}
        self.space(cls)[oid] = out  # children may refer back to it while it is read
        self.stack.append(out)
        for _ in range(count):
            at = self.p
            tag = self.varint()
            kind = schema.get(tag)
            if kind is None:
                raise Unknown(f"{cls} tag 0x{tag:x} at {at} (fields so far {list(out)[2:]})")
            try:
                out[tag] = self.value(kind)
            except Unknown as e:
                if not str(e).startswith("["):
                    raise Unknown(f"[{cls}.{tag:#x}] {e}") from None
                raise
        self.stack.pop()
        return out

    def value(self, kind):
        if kind == "f":
            return self.float()
        if kind == "v":
            return self.varint()
        if kind == "z":
            return self.byte()
        if kind == "i":
            return self.int32()
        if kind == "s":
            return self.string()
        if kind == "e":
            return self.enum()
        if kind == "ts":
            return self.string(True)
        if kind == "te":
            return self.enum(True)
        if kind == "p":
            return self.poly()
        if kind == "ra":  # a reference to an object written earlier (never new here)
            m = self.varint()
            if m == 1:
                raise Unknown(f"new object in a back-reference-only field at {self.p}")
            return None if m == 0 else Ref(m - 2, self.objects[m - 2] if m - 2 < len(self.objects) else None)
        if kind == "deform":  # Vertices.3: clipping attachments write an empty array without a length
            owner = self.stack[-1].get(0)
            owner = owner.target if isinstance(owner, Ref) else owner
            parent = self.stack[-2]["_class"] if len(self.stack) > 1 else None
            if self.d[self.p:self.p + 2] == bytes((0x11, 1)) and parent != "MeshAttachment":
                self.p += 2
                self.new_id([], "FloatArray")  # still a new (empty) array object
                return []
            return self.poly()
        if isinstance(kind, tuple) and kind[0] == "r":
            return self.obj(kind[1])
        if isinstance(kind, tuple) and kind[0] == "a":  # fixed array of kind[1]: varint length+1
            n = self.varint()
            if n == 0:
                return None
            return [self.value(kind[1]) for _ in range(n - 1)]
        raise Unknown(f"bad kind {kind}")


UNTRACKED = {"Vec3", "Rect"}


class Ref:
    def __init__(self, oid, target):
        self.id, self.target = oid, target

    def __repr__(self):
        t = self.target
        name = t.get(1) if isinstance(t, dict) else t
        return f"<ref {self.id} {name!r}>" if not isinstance(t, dict) else f"<ref {self.id} {t['_class']} {name!r}>"


def _list(r: Reader):
    n = r.varint()
    return [r.poly() for _ in range(n)]


def _color(r: Reader):
    v = "%02x%02x%02x%02x" % tuple(r.d[r.p:r.p + 4])
    r.p += 4
    return v


SPECIAL = {"List": _list, "Color": _color}
CLASSES.update({0x0F: "List", 0x1E: "Color"})


def _ints(r: Reader):
    return [r.varint() for _ in range(r.varint())]


def _floats(r: Reader):
    return [r.float() for _ in range(r.varint())]


SPECIAL.update({"IntArray": _ints, "FloatArray": _floats})
CLASSES.update({0x10: "IntArray", 0x11: "FloatArray"})


def _shorts(r: Reader):
    n = r.varint()
    v = list(struct.unpack(f">{n}h", r.d[r.p:r.p + 2 * n]))
    r.p += 2 * n
    return v


SPECIAL["ShortArray"] = _shorts
CLASSES[0x2F] = "ShortArray"


def _rect(r: Reader):
    flag = r.varint()
    return {"_class": "Rect", "flag": flag, "v": [r.float() for _ in range(4)]}


SPECIAL["Rect"] = _rect


def _vec3(r: Reader):
    return [r.float() for _ in range(3)]


SPECIAL["Vec3"] = _vec3
CLASSES[0x29] = "Vec3"


def read_project(r: Reader) -> dict:
    """The project's skeletons (tag 0) and fps (tag 2); editor settings after them are not read."""
    m = r.varint()
    assert m == 1, m
    oid = r.new_id(None)
    out = {"_class": "Project", "_id": oid, "_pos": r.p}
    r.objects[oid] = out
    r.stack.append(out)
    count = r.varint()
    for _ in range(count):
        save = r.p
        tag = r.varint()
        if tag == 0:
            out[0] = r.poly()
        elif tag == 2:
            out[2] = r.float()
        else:
            r.p = save
            break
    r.stack.pop()
    return out


def _deform_values(r: Reader):
    """n values in chunks of 128: chunk count, then per chunk its non-zero count (0: omitted) and floats."""
    n = r.varint()
    values = [0.0] * n
    for c in range(r.byte()):
        if r.byte():
            for i in range(c * 128, min(n, c * 128 + 128)):
                values[i] = r.float()
    return {"_class": "DeformValues", "v": values}


SPECIAL["DeformValues"] = _deform_values
CLASSES[0x6B] = "DeformValues"


def _java_floats(r: Reader):
    n = r.varint()
    return None if n == 0 else [r.float() for _ in range(n - 1)]


SPECIAL["float[]"] = _java_floats


def read_project42(r: Reader) -> dict:
    """4.2 projects write their fields in another order: read until the skeletons (tag 0) are in."""
    assert r.varint() == 1
    oid = r.new_id(None)
    out = {"_class": "Project", "_id": oid, "_pos": r.p}
    r.objects[oid] = out
    r.stack.append(out)
    count = r.varint()
    for _ in range(count):
        tag = r.varint()
        if tag == 0:
            out[0] = r.poly()
            break
        kind = {0xE: "p", 0x11: "f"}.get(tag)
        if kind is None:
            break
        out[tag] = r.value(kind)
    r.stack.pop()
    return out
