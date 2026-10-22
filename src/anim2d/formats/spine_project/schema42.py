"""Field types of Spine 4.2 project objects, and the readers for 4.2 animation data.

The 4.2 setup objects mostly keep the 3.8 tags. Animations changed: a timeline (class 132)
holds its owner, a property id and a key list; each key (class 133) stores its time in frames,
the values, and per value channel the in/out bezier handles (absolute frame/value, 2**31 when
there is none) and a handle mode, then a flag (2 = stepped). 4.2 also numbers references in
two spaces: values (strings, enums, colours, lists, arrays) apart from entities.
"""

from . import reader as kp
from .reader import CLASSES as BASE_CLASSES

F, V, Z, S, E, P = "f", "v", "z", "s", "e", "p"


def R(name: str) -> tuple[str, str]:  # noqa: N802 - reads like a kind
    return ("r", name)


TRAILING = {4: 1, 5: 1, 6: 2, 7: 1}

CHANNELS = {0: 1, 1: 2, 2: 2, 3: 2, 4: 7, 14: 1, 15: 1}

VALUE_SPACE = {"Vec3", "List", "str", "enum", "Color", "FloatArray", "IntArray", "ShortArray", "KeyList", "List0",
               "Map74", "float[]"}


def _timeline42(r):
    owner = r.value("ra")
    prop = r.varint()
    out = {"_class": "Timeline42", "owner": owner, "prop": prop}
    r.stack.append(out)
    m = r.varint()  # the key list: 1 = new, then its size
    keys = []
    if m == 1:
        r.new_id(keys, "KeyList")
        n = r.varint()
        keys = [r.poly() for _ in range(n)]
    out["keys"] = keys
    for i in range(TRAILING.get(prop, 0)):  # slot / constraint / attachment the timeline animates
        out[f"ref{i}"] = r.value("ra")
    r.stack.pop()
    return out


def _handles(r, n):
    return [[r.float() for _ in range(4)] + [r.float()] for _ in range(n)]


def _key42(r):
    r.value("ra")  # the timeline's owner
    r.value("ra")  # the timeline (the one being read)
    timeline = r.stack[-1]
    prop = timeline["prop"]
    k = {"_class": "Key42", "time": r.float(), "prop": prop}
    c = CHANNELS.get(prop)
    if c is not None:
        k["values"] = [r.float() for _ in range(c)]
        k["handles"] = _handles(r, c)
        k["flag"] = r.byte()
    elif prop == 5:  # attachment
        k["flag"] = r.byte()
        k["attachment"] = r.poly()
    elif prop == 6:  # deform
        k["handles"] = _handles(r, 1)
        k["flag"] = r.byte()
        k["deform"] = r.obj("DeformValues")
    elif prop == 7:  # IK
        k["values"] = [r.float() for _ in range(5)]
        k["handles"] = _handles(r, 2)
        k["flag"] = r.byte()
    elif prop == 13:  # draw order
        k["flag"] = r.byte()
        k["offsets"] = r.value(("r", "List0"))
    else:
        raise kp.Unknown(f"key for timeline property {prop} at {r.p}")
    return k


def _list0(r):
    return [r.poly() for _ in range(r.varint())]


def _map74(r):
    return {"_class": "Map74", "items": [(r.poly(), r.byte()) for _ in range(r.varint())]}


def configure(r):
    """4.2 keeps two reference spaces: values and entities."""
    r.value_space = set(VALUE_SPACE)
    r.untracked.discard("Vec3")

kp.SPECIAL.update({"Timeline42": _timeline42, "Key42": _key42, "List0": _list0, "Map74": _map74,
                   "Marker70": lambda r: {"_class": "Marker70", "v": r.varint()}})

CLASSES: dict[int, str] = dict(BASE_CLASSES)
CLASSES.update({
    0x0a: 'Event',
    0x0b: 'Skeleton',
    0x0c: 'Bone',
    0x0d: 'Slot',
    0x12: 'Animation',
    0x13: 'BoneTimelines',
    0x14: 'Timeline',
    0x22: 'Skin',
    0x23: 'EventKey',
    0x26: 'Obj26',
    0x27: 'DrawOrderOffset',
    0x2b: 'RegionAttachment',
    0x2c: 'SkinPlaceholder',
    0x2e: 'MeshAttachment',
    0x34: 'BoundingBoxAttachment',
    0x35: 'Vertices',
    0x36: 'Vertex',
    0x38: 'IkConstraint',
    0x41: 'TransformConstraint',
    0x45: 'PathAttachment',
    0x47: 'Vertices',
    0x48: 'PathConstraint',
    0x50: 'Obj50',
    0x52: 'Key',
    0x54: 'ClippingAttachment',
    0x6c: 'Folder',
    0x70: 'Marker70',
    0x74: 'Map74',
    0x84: 'Timeline42',
    0x85: 'Key42',
})

SCHEMA: dict[str, dict[int, object]] = {
    'Animation': {0x0: 'ra', 0x1: S, 0x2: P, 0x3: Z, 0x4: Z, 0x5: Z, 0x6: Z, 0x7: P, 0x8: P, 0x9: P},
    'Bone': {0x0: 'ra', 0x1: S, 0x2: Z, 0x3: R('Bone'), 0x4: F, 0x5: F, 0x6: F, 0x7: F, 0x8: F, 0x9: F, 0xa: F, 0xb: F, 0xc: F, 0xd: F, 0xe: F, 0x11: P, 0x17: F, 0x18: F, 0x19: F, 0x1a: F, 0x1b: 'te', 0x1c: 'ts', 0x1d: Z, 0x1e: Z, 0x1f: P, 0x20: 'te', 0x7e: Z},
    'BoneTimelines': {0x0: 'ra', 0x1: 'ra', 0x2: P, 0x3: 'ra', 0x4: Z, 0x5: 'ra', 0x6: P},
    'BoundingBoxAttachment': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x7: V, 0x8: P, 0x9: F, 0xa: F, 0xb: F, 0x12: P, 0x13: F, 0x14: F, 0x15: F, 0x1c: P, 0x1e: Z, 0x1f: P, 0x20: R('Skin'), 0x21: Z, 0x23: Z},
    'ClippingAttachment': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x7: V, 0x8: P, 0x9: F, 0xa: F, 0xb: F, 0xc: R('Slot'), 0x12: P, 0x13: F, 0x14: F, 0x15: F, 0x1c: P, 0x1e: Z, 0x1f: P, 0x20: R('Skin'), 0x21: Z, 0x23: Z},
    'DrawOrderOffset': {0x0: 'ra', 0x1: V},
    'Event': {0x0: 'ra', 0x2: S, 0x3: V, 0x4: F, 0x5: S, 0x6: S, 0x7: Z, 0x8: F, 0x9: F, 0xa: P},
    'EventKey': {0x1: R('Event'), 0x2: V, 0x3: F, 0x4: S, 0x5: F, 0x6: F},
    'Folder': {0x0: Z, 0x1: S, 0x2: 'ra', 0x3: P, 0x4: P, 0x5: P},
    'IkConstraint': {0x0: 'ra', 0x1: S, 0x2: P, 0x3: R('Bone'), 0x5: F, 0x6: F, 0x9: V, 0xa: Z, 0xb: Z, 0xc: Z, 0xd: Z, 0xe: F, 0xf: F, 0x10: Z, 0x11: Z, 0x16: P, 0x20: P, 0x22: Z},
    'Key': {0x0: R('Timeline'), 0x1: E, 0x2: F, 0x3: E, 0x4: F, 0x5: F, 0x6: F, 0x7: F, 0x8: F, 0x9: F, 0xa: F, 0xb: F, 0xd: P, 0xe: R('EventKey'), 0xf: P, 0x10: P, 0x11: F},
    'MeshAttachment': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x7: V, 0x8: P, 0x9: F, 0xa: F, 0xb: F, 0xc: P, 0xd: P, 0xe: S, 0x10: F, 0x11: F, 0x12: P, 0x13: F, 0x14: F, 0x15: F, 0x1a: P, 0x1b: Z, 0x1c: P, 0x1e: Z, 0x1f: P, 0x20: R('Skin'), 0x21: Z, 0x22: Z, 0x23: Z, 0x24: Z},
    'Obj26': {},
    'Obj50': {0x0: Z},
    'Obj6c': {0x0: Z, 0x1: S, 0x2: 'ra', 0x3: P, 0x4: P, 0x5: P},
    'PathAttachment': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x7: V, 0x8: P, 0x9: F, 0xa: F, 0xb: F, 0xc: Z, 0xd: Z, 0xf: R('float[]'), 0x12: P, 0x13: F, 0x14: F, 0x15: F, 0x1c: P, 0x1e: Z, 0x1f: P, 0x20: R('Skin'), 0x21: Z, 0x23: Z},
    'PathConstraint': {0x0: 'ra', 0x1: S, 0x2: P, 0x3: R('Slot'), 0x4: F, 0x5: F, 0x6: F, 0x7: F, 0x8: F, 0x9: F, 0xa: F, 0xb: Z, 0xc: F, 0xd: F, 0xe: E, 0xf: E, 0x10: E, 0x16: P},
    'Project': {0x0: P, 0xe: P, 0x11: F},
    'RegionAttachment': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x6: F, 0x7: F, 0x8: F, 0x9: F, 0xa: F, 0xb: F, 0xc: F, 0xd: S, 0xe: P, 0xf: P, 0x20: R('Skin'), 0x21: Z, 0x23: Z},
    'Skeleton': {0x0: 'ra', 0x1: S, 0x2: Z, 0x3: S, 0x4: P, 0x6: P, 0x7: P, 0x8: P, 0xa: P, 0xd: P, 0xe: P, 0x10: P, 0x11: P, 0x12: P, 0x13: P, 0x14: P, 0x15: P, 0x16: P, 0x17: P, 0x18: R('Rect'), 0x19: P, 0x1a: P, 0x1b: P, 0x1c: P, 0x1d: P, 0x1e: P, 0x1f: P, 0x20: P, 0x21: 'ra', 0x22: S, 0x23: Z, 0x24: P, 0x25: P, 0x26: P, 0x27: P, 0x28: P, 0x29: P, 0x2a: Z, 0x2b: Z, 0x2c: P, 0x2d: P, 0x2e: F, 0x2f: Z, 0x30: Z, 0x31: P, 0x32: P, 0x7e: Z},
    'Skin': {0x0: 'ra', 0x1: S, 0x2: P, 0x3: Z, 0x4: Z, 0x5: P, 0x6: P, 0x7: P, 0x8: P, 0x7e: Z},
    'SkinPlaceholder': {0x0: R('Slot'), 0x1: S, 0x4: R('SkinPlaceholder'), 0x5: Z, 0x20: R('Skin'), 0x21: Z, 0x23: Z, 0x7d: Z},
    'Slot': {0x0: 'ra', 0x1: S, 0x2: R('Bone'), 0x3: Z, 0x4: P, 0x5: P, 0x7: E, 0x8: P, 0x9: P, 0xa: P, 0xb: P, 0xc: Z, 0xd: Z, 0xe: E, 0xf: F, 0x10: P, 0x7e: Z},
    'Timeline': {0x0: 'ra', 0x1: E, 0x2: P, 0x3: 'ra', 0x4: 'ra', 0x5: P, 0x6: P, 0x7: P, 0x8: P},
    'TransformConstraint': {0x0: 'ra', 0x1: S, 0x3: R('Bone'), 0x4: F, 0x5: F, 0x6: F, 0x7: F, 0x8: F, 0x9: F, 0xa: F, 0xb: F, 0xc: F, 0xd: F, 0xe: F, 0xf: Z, 0x10: F, 0x11: F, 0x12: F, 0x13: P, 0x14: Z, 0x15: Z, 0x16: P},
    'Vertex': {0x0: P, 0x1: P},
    'Vertices': {0x0: P, 0x1: P, 0x2: P, 0x3: 'deform'},
}
