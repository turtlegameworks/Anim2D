"""Parsed .spine project -> Spine JSON skeleton (3.8 layout; 4.2 animations in the 4.0 layout)."""
import struct

from .reader import Ref

TRANSFORM = ["normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection"]
BLEND = ["normal", "additive", "multiply", "screen"]
TL = {23: "scale", 24: "translate", 25: "rotate", 67: "shear", 26: "attachment", 80: "color", 50: "deform",
      35: "event", 39: "drawOrder", 56: "ik", 66: "transform", 72: "spacing", 73: "position", 74: "mix"}


def T(v):
    return v.target if isinstance(v, Ref) else v


def nm(v):
    v = T(v)
    return T(v.get(1)) if isinstance(v, dict) else v


def anim_name(a):
    """Full path of an animation, following its folder hierarchy (mirrors skin_name)."""
    path = [nm(a)]
    folder = T(a.get(7)) if isinstance(a, dict) else None
    while isinstance(folder, dict) and T(folder.get(1)):
        path.insert(0, T(folder[1]))
        folder = T(folder.get(3))
    return "/".join(str(p) for p in path if p is not None)


def lst(v):
    return [T(x) for x in (T(v) or [])]


def enum(v, names, default=0):
    v = T(v)
    i = v[1] if isinstance(v, tuple) else default
    return names[i] if 0 <= i < len(names) else names[default]


def hexcolor(c, alpha=True):
    c = T(c)
    if not isinstance(c, str):
        return None
    return c if alpha else c[:6]


def packed(f):
    """libGDX Color.toFloatBits (ABGR, alpha 0xfe) -> rrggbbaa."""
    bits = struct.unpack(">I", struct.pack(">f", f))[0]
    r, g, b, a = bits & 0xFF, (bits >> 8) & 0xFF, (bits >> 16) & 0xFF, (bits >> 24) & 0xFF
    a = 255 if a == 0xFE else a
    return f"{r:02x}{g:02x}{b:02x}{a:02x}"


def r4(x):
    return round(x, 6) if isinstance(x, float) else x


def put(d, key, value, default):
    if value is not None and r4(value) != default:
        d[key] = r4(value)


def convert(project, version):
    sk = T(project[0][0])
    fps = project.get(2) or 30.0
    bones = lst(sk[4])
    bone_index = {id(b): i for i, b in enumerate(bones)}
    skins = lst(sk.get(8))
    skin_bones = {id(T(b)) for s in skins for b in lst(s.get(6))}
    out = {"skeleton": {"spine": version, "images": T(sk.get(3)) or "./images/", "audio": T(sk.get(0x22)) or ""}}
    rect = T(sk.get(0x18))
    if isinstance(rect, dict):
        x, y, w, h = rect["v"]
        out["skeleton"].update(x=r4(x), y=r4(y), width=r4(w), height=r4(h))
    jb = []
    for b in bones:
        e = {"name": nm(b)}
        if T(b.get(3)):
            e["parent"] = nm(b[3])
        put(e, "length", b.get(9), 0)
        put(e, "rotation", b.get(0xc), 0)
        put(e, "x", b.get(0xa), 0)
        put(e, "y", b.get(0xb), 0)
        put(e, "scaleX", b.get(0xd), 1)
        put(e, "scaleY", b.get(0xe), 1)
        put(e, "shearX", b.get(0x17), 0)
        put(e, "shearY", b.get(0x19), 0)
        mode = enum(b.get(0x1b), TRANSFORM)
        if mode != "normal":
            e["transform"] = mode
        if id(b) in skin_bones and tuple(int(x) for x in version.split(".")[:3]) >= (3, 8, 75):
            e["skin"] = True
        color = hexcolor(b.get(0x11))
        if color and color != "989898ff" and color != "9b9b9bff":
            e["color"] = color
        jb.append(e)
    out["bones"] = jb

    slots = lst(sk[0xd])
    js = []
    for s in slots:
        e = {"name": nm(s), "bone": nm(s[2])}
        c = hexcolor(s.get(5))
        if c and c != "ffffffff":
            e["color"] = c
        if s.get(0xc):
            e["dark"] = hexcolor(s.get(0xb), False)
        att = T(s.get(9))
        if att and att.get(5, 1):
            e["attachment"] = nm(att)
        blend = enum(s.get(7), BLEND)
        if blend != "normal":
            e["blend"] = blend
        js.append(e)
    out["slots"] = js

    def skin_name(s):
        path = [nm(s)]
        folder = T(s.get(5))
        while isinstance(folder, dict) and T(folder.get(1)):
            path.insert(0, T(folder[1]))
            folder = T(folder.get(3))
        return "/".join(path)

    def verts(a, slot):
        v = T(a.get(0x1c))
        if not isinstance(v, dict):
            return None, 0
        vbones = lst(v.get(1))
        points = lst(v.get(2))
        slot_bone = T(slot[2]) if slot else None
        weighted = not (len(vbones) == 1 and vbones[0] is slot_bone and
                        all(abs(T(p[0])[0] - 1) < 1e-6 for p in points))
        flat = []
        for p in points:
            w, xy = T(p[0]), T(p[1])
            if not weighted:
                flat += [r4(xy[0]), r4(xy[1])]
                continue
            used = [(i, wi) for i, wi in enumerate(w) if wi != 0]
            flat.append(len(used))
            for i, wi in used:
                flat += [bone_index[id(vbones[i])], r4(xy[i * 2]), r4(xy[i * 2 + 1]), r4(wi)]
        return flat, len(points)

    def attachment(a):
        cls = a["_class"]
        slot = T(a.get(0))
        e = {}
        if cls == "RegionAttachment":
            path = T(a.get(0xd))
            if path:
                e["path"] = path
            for key, tag, default in (("x", 6, 0), ("y", 7, 0), ("scaleX", 8, 1), ("scaleY", 9, 1),
                                      ("rotation", 0xa, 0), ("width", 0xb, None), ("height", 0xc, None)):
                put(e, key, a.get(tag), default)
            c = hexcolor(a.get(0xe))
            if c and c != "ffffffff":
                e["color"] = c
            return e
        if cls == "MeshAttachment":
            parent = T(a.get(0x1a))
            path = T(a.get(0xe))
            if parent:
                e["type"] = "linkedmesh"
                holder = T(parent.get(4))
                e["parent"] = nm(holder) if holder else nm(parent)
                if T(parent.get(0x20)):
                    e["skin"] = skin_name(T(parent[0x20]))
                if not a.get(0x1b):
                    e["deform"] = False
            else:
                e["type"] = "mesh"
            if path:
                e["path"] = path
            c = hexcolor(a.get(0x1f))
            if c and c != "ffffffff":
                e["color"] = c
            if not parent:
                e["uvs"] = [r4(x) for x in T(a[0xc])]
                e["triangles"] = list(T(a[0xd]))
                v, n = verts(a, slot)
                e["vertices"] = v
                e["hull"] = (a.get(7) or 0) // 4
                edges = T(a.get(8))
                if edges:
                    e["edges"] = list(edges)
            put(e, "width", a.get(0x10), None)
            put(e, "height", a.get(0x11), None)
            return e
        v, n = verts(a, slot)
        if cls == "ClippingAttachment":
            e.update(type="clipping", end=nm(a.get(0xc)), vertexCount=n, vertices=v)
        elif cls == "BoundingBoxAttachment":
            e.update(type="boundingbox", vertexCount=n, vertices=v)
        elif cls == "PathAttachment":
            e["type"] = "path"
            if a.get(0xc):
                e["closed"] = True
            if not a.get(0xd):
                e["constantSpeed"] = False
            e.update(lengths=[r4(x) for x in T(a[0xf]) or []], vertexCount=n, vertices=v)
        else:
            return None
        c = hexcolor(a.get(0x1f))
        if c and c != {"clipping": None, "boundingbox": "60f000ff", "path": "ff7f00ff"}[e["type"]]:
            e["color"] = c
        return e

    def add(skin, a):
        slot = T(a.get(0))
        holder = T(a.get(4))
        key = nm(holder) if holder else nm(a)
        if not a.get(5, 1):  # the attachment's Export box is off
            return
        e = attachment(a)
        if e is None:
            return
        if key != nm(a):
            e["name"] = nm(a)
        skin.setdefault(nm(slot), {})[key] = e

    default = {}
    for a in lst(sk.get(6)):
        if a["_class"] != "SkinPlaceholder" and not T(a.get(0x20)):
            add(default, a)
    jskins = [{"name": "default", "attachments": default}]  # the runtimes expect it first, even if empty
    for s in skins:
        atts = {}
        for a in lst(s.get(2)):
            if a["_class"] != "SkinPlaceholder":
                add(atts, a)
        entry = {"name": skin_name(s), "attachments": atts}
        sb = [nm(b) for b in lst(s.get(6))]
        if sb:
            entry["bones"] = sb
        for c in lst(s.get(7)):
            kind = {"IkConstraint": "ik", "TransformConstraint": "transform", "PathConstraint": "path"}[c["_class"]]
            entry.setdefault(kind, []).append(nm(c))
        jskins.append(entry)
    out["skins"] = jskins

    order = {id(c): i for i, c in enumerate(lst(sk.get(0x19)))}

    def head(c, bones_tag):
        e = {"name": nm(c)}
        if order.get(id(c), 0):
            e["order"] = order[id(c)]
        if c.get(0x16) and lst(c.get(0x16)):
            e["skin"] = True
        e["bones"] = [nm(b) for b in lst(c[bones_tag])]
        e["target"] = nm(c[3])
        return e

    iks = []
    for c in lst(sk.get(0x10)):
        e = head(c, 2)
        put(e, "mix", c.get(5), 1)
        put(e, "softness", c.get(0xe), 0)
        if (c.get(4) or 2) == 1:  # zigzag varint: 2 = +1, 1 = -1
            e["bendPositive"] = False
        for key, tag in (("compress", 9), ("stretch", 0xb), ("uniform", 0xd)):
            if c.get(tag):
                e[key] = True
        iks.append(e)
    if iks:
        out["ik"] = iks
    tcs = []
    for c in lst(sk.get(0x16)):
        e = head(c, 0x13)
        if c.get(0x14):
            e["local"] = True
        if c.get(0x15):
            e["relative"] = True
        for key, tag, default in (("rotation", 0xe, 0), ("x", 6, 0), ("y", 7, 0), ("scaleX", 0x10, 0),
                                  ("scaleY", 0x11, 0), ("shearY", 0x12, 0), ("rotateMix", 8, 1),
                                  ("translateMix", 4, 1), ("scaleMix", 0xa, 1), ("shearMix", 0xc, 1)):
            put(e, key, c.get(tag), default)
        tcs.append(e)
    if tcs:
        out["transform"] = tcs
    pcs = []
    for c in lst(sk.get(0x17)):
        e = head(c, 2)
        position_mode = enum(c.get(0x10), ["fixed", "percent"], 1)
        spacing_mode = enum(c.get(0xe), ["length", "fixed", "percent"])
        rotate_mode = enum(c.get(0xf), ["tangent", "chain", "chainScale"])
        if position_mode != "percent":
            e["positionMode"] = position_mode
        if spacing_mode != "length":
            e["spacingMode"] = spacing_mode
        if rotate_mode != "tangent":
            e["rotateMode"] = rotate_mode
        for key, tag, default in (("rotation", 0xa, 0), ("position", 4, 0), ("spacing", 0xc, 0),
                                  ("rotateMix", 6, 1), ("translateMix", 8, 1)):
            put(e, key, c.get(tag), default)
        pcs.append(e)
    if pcs:
        out["path"] = pcs

    events = {}
    for ev in lst(sk.get(0xa)):
        e = {}
        put(e, "int", ev.get(3), 0)
        put(e, "float", ev.get(4), 0)
        if T(ev.get(5)):
            e["string"] = T(ev[5])
        if T(ev.get(6)):
            e["audio"] = T(ev[6])
            put(e, "volume", ev.get(8), 1)
            put(e, "balance", ev.get(9), 0)
        events[T(ev.get(2))] = e
    if events:
        out["events"] = events

    def zigzag(n):
        return (n >> 1) ^ -(n & 1)

    def skin_of(att):
        skin = T(att.get(0x20)) if isinstance(att, dict) else None
        return skin_name(skin) if skin else "default"

    def deform_layout(att, values):
        """Editor deform arrays hold (x, y) for every vertex and every bone in the mesh's bone list;
        the runtime format keeps only bones with a non-zero weight."""
        mesh = att
        while isinstance(mesh, dict) and T(mesh.get(0x1a)):
            mesh = T(mesh[0x1a])  # linked mesh: the parent's vertices
        v = T(mesh.get(0x1c)) if isinstance(mesh, dict) else None
        if not isinstance(v, dict):
            return values
        vbones = lst(v.get(1))
        points = lst(v.get(2))
        slot = T(mesh.get(0))
        weighted = not (len(vbones) == 1 and slot is not None and vbones[0] is T(slot[2]) and
                        all(abs(T(p[0])[0] - 1) < 1e-6 for p in points))
        if not weighted or len(values) != 2 * len(vbones) * len(points):
            return values
        out = []
        for vi, p in enumerate(points):
            for bi, w in enumerate(T(p[0])):
                if w != 0:
                    base = 2 * (vi * len(vbones) + bi)
                    out += values[base:base + 2]
        return out

    anims = {}
    for a in (lst(sk.get(7)) if not version.startswith("4.") else []):
        if isinstance(a, dict) and a.get("_class") in ("Obj6c", "Folder"):
            continue
        ja = {}
        for g in lst(a.get(2)):
            target = T(g.get(1))
            for tl in lst(g.get(2)):
                kind = TL.get(T(tl[1])[1])
                if kind is None:
                    continue
                slot = T(tl.get(3))
                jk = []
                for k in lst(tl.get(2)):
                    e = {}
                    t = r4(k[2] / fps)
                    if t:
                        e["time"] = t
                    v = [k.get(i) for i in (8, 9, 0xa, 0xb)]
                    if kind == "rotate":
                        put(e, "angle", v[0], 0)
                    elif kind in ("translate", "shear"):
                        put(e, "x", v[0], 0)
                        put(e, "y", v[1], 0)
                    elif kind == "scale":
                        put(e, "x", v[0], 1)
                        put(e, "y", v[1], 1)
                    elif kind == "color":
                        if slot is not None and slot.get(0xc):
                            e["light"] = packed(v[0])
                            e["dark"] = packed(v[1])[:6]
                        else:
                            e["color"] = packed(v[0])
                    elif kind == "attachment":
                        att = T(k.get(0xd))
                        e["name"] = nm(att) if att else None
                    elif kind == "ik":
                        put(e, "mix", v[0], 1)
                        put(e, "softness", v[2], 0)
                        if v[1] is not None and v[1] < 0:
                            e["bendPositive"] = False
                    elif kind == "transform":
                        for key, x in zip(("rotateMix", "translateMix", "scaleMix", "shearMix"), v):
                            put(e, key, x, 1)
                    elif kind == "position":
                        put(e, "position", v[0], 0)
                    elif kind == "spacing":
                        put(e, "spacing", v[0], 0)
                    elif kind == "mix":
                        put(e, "rotateMix", v[0], 1)
                        put(e, "translateMix", v[1], 1)
                    elif kind == "deform":
                        values = T(k.get(0x10))
                        values = deform_layout(T(tl.get(5)), values["v"] if isinstance(values, dict) else [])
                        nz = [i for i, x in enumerate(values) if x != 0]
                        if nz:
                            if nz[0]:
                                e["offset"] = nz[0]
                            e["vertices"] = [r4(x) for x in values[nz[0]:nz[-1] + 1]]
                    elif kind == "drawOrder":
                        offs_map = {}
                        for o in lst(k.get(0xf)):
                            offs_map[nm(T(o)[0])] = zigzag(T(o)[1])
                        offs = [{"slot": s, "offset": v} for s, v in offs_map.items()]
                        if offs:
                            e["offsets"] = offs
                    elif kind == "event":
                        ek = T(k.get(0xe))
                        ev = T(ek.get(1))
                        e["name"] = T(ev.get(2))
                        put(e, "int", zigzag(ek.get(2) or 0), zigzag(ev.get(3) or 0))
                        put(e, "float", ek.get(3), ev.get(4) or 0)
                        if T(ek.get(4)) and T(ek.get(4)) != T(ev.get(5)):
                            e["string"] = T(ek[4])
                        if T(ev.get(6)):
                            put(e, "volume", ek.get(5), 1)
                            put(e, "balance", ek.get(6), 0)
                    curve = T(k.get(3))
                    ci = curve[1] if isinstance(curve, tuple) else 0
                    if ci == 2 and kind not in ("attachment", "drawOrder", "event"):
                        e["curve"] = "stepped"
                    elif ci == 1 and kind not in ("attachment", "drawOrder", "event"):
                        e["curve"] = r4(k.get(4))
                        e["c2"], e["c3"], e["c4"] = r4(k.get(5)), r4(k.get(6)), r4(k.get(7))
                    jk.append(e)
                if not jk:
                    continue
                if kind != "event":  # two keys at one time: the later one is the one that plays
                    jk = [e for i, e in enumerate(jk) if i + 1 == len(jk) or jk[i + 1].get("time", 0) != e.get("time", 0)]
                if kind in ("rotate", "translate", "scale", "shear"):
                    ja.setdefault("bones", {}).setdefault(nm(target), {})[kind] = jk
                elif kind in ("attachment", "color"):
                    name = "twoColor" if kind == "color" and slot.get(0xc) else kind
                    ja.setdefault("slots", {}).setdefault(nm(slot), {})[name] = jk
                elif kind == "deform":
                    att = T(tl.get(5))
                    holder = T(att.get(4))
                    key = nm(holder) if holder else nm(att)
                    ja.setdefault("deform", {}).setdefault(skin_of(att), {}).setdefault(nm(slot), {})[key] = jk
                elif kind == "ik":
                    ja.setdefault("ik", {})[nm(tl[6])] = jk
                elif kind == "transform":
                    ja.setdefault("transform", {})[nm(tl[7])] = jk
                elif kind in ("position", "spacing", "mix"):
                    ja.setdefault("path", {}).setdefault(nm(tl[8]), {})[kind] = jk
                elif kind == "drawOrder":
                    ja["drawOrder"] = jk
                elif kind == "event":
                    ja.setdefault("events", []).extend(jk)
        if "events" in ja:
            ja["events"].sort(key=lambda e: e.get("time", 0))
        anims[anim_name(a)] = ja
    out["animations"] = anims
    if version.startswith("4."):
        out["_animations40"] = anims42(sk, fps, nm, lst, T, deform_layout, skin_of)
    return out


NONE42 = 2147483648.0
BONE_PROPS = {0: ("rotate", ("value",)), 1: ("translate", ("x", "y")), 14: ("translatex", ("value",)),
              15: ("translatey", ("value",)), 2: ("scale", ("x", "y")), 3: ("shear", ("x", "y"))}


def _curve42(a, b, channels, fps):
    """4.0 curve for the segment a -> b: absolute handles (seconds, value) per channel."""
    if a.get("flag") == 2:
        return "stepped"
    curve, linear = [], True
    t0, t1 = a["time"], b["time"]
    for ch in range(channels):
        ha, hb = a["handles"][ch], b["handles"][ch]
        v0, v1 = a["values"][ch], b["values"][ch]
        if ha[2] != NONE42 or hb[0] != NONE42:
            linear = False
        ox, oy = (ha[2], ha[3]) if ha[2] != NONE42 else (t0 + (t1 - t0) / 3, v0 + (v1 - v0) / 3)
        ix, iy = (hb[0], hb[1]) if hb[0] != NONE42 else (t1 - (t1 - t0) / 3, v1 - (v1 - v0) / 3)
        curve += [round(ox / fps, 6), round(oy, 6), round(ix / fps, 6), round(iy, 6)]
    return None if linear else curve


def _packed42(values):
    return "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in values)


def anims42(sk, fps, nm, lst, T, deform_layout, skin_of):
    """Spine 4.2 animations -> Spine 4.0 JSON animations (per-channel bezier curves kept exactly)."""
    out, notes = {}, []
    for a in lst(sk.get(7)):
        if isinstance(a, dict) and a.get("_class") in ("Obj6c", "Folder"):
            continue
        name = anim_name(a)
        n = 2
        while name in out:
            name = f"{anim_name(a)} {n}"
            n += 1
        ja = {}
        for g in lst(a.get(2)):
            target = T(g.get(1))
            for tl in lst(g.get(2)):
                prop, keys = tl["prop"], [T(k) for k in tl["keys"]]
                if not keys:
                    continue
                if prop in BONE_PROPS and target is not None:
                    kind, fields = BONE_PROPS[prop]
                    jk = []
                    for i, k in enumerate(keys):
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        for f, v in zip(fields, k["values"]):
                            e[f] = round(v, 6)
                        if i + 1 < len(keys):
                            c = _curve42(k, keys[i + 1], len(fields), fps)
                            if c:
                                e["curve"] = c
                        jk.append(e)
                    ja.setdefault("bones", {}).setdefault(nm(target), {})[kind] = jk
                elif prop == 4:  # slot colour: light rgba + dark rgb
                    slot = T(tl.get("ref0"))
                    two = bool(slot and slot.get(0xc))
                    jk = []
                    for i, k in enumerate(keys):
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        if two:
                            e["light"], e["dark"] = _packed42(k["values"][:4]), _packed42(k["values"][4:7])
                        else:
                            e["color"] = _packed42(k["values"][:4])
                        if i + 1 < len(keys):
                            c = _curve42(k, keys[i + 1], 7 if two else 4, fps)
                            if c:
                                e["curve"] = c
                        jk.append(e)
                    ja.setdefault("slots", {}).setdefault(nm(slot), {})["rgba2" if two else "rgba"] = jk
                elif prop == 5:  # attachment
                    slot = T(tl.get("ref0"))
                    jk = []
                    for k in keys:
                        att = T(k.get("attachment"))
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        e["name"] = nm(att) if att else None
                        jk.append(e)
                    ja.setdefault("slots", {}).setdefault(nm(slot), {})["attachment"] = jk
                elif prop == 6:  # deform
                    att = T(tl.get("ref1"))
                    if not isinstance(att, dict):
                        continue
                    slot = T(att.get(0))
                    holder = T(att.get(4))
                    jk = []
                    for i, k in enumerate(keys):
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        dv = T(k.get("deform"))
                        values = deform_layout(att, dv["v"] if isinstance(dv, dict) else [])
                        nz = [j for j, x in enumerate(values) if x != 0]
                        if nz:
                            if nz[0]:
                                e["offset"] = nz[0]
                            e["vertices"] = [round(x, 6) for x in values[nz[0]:nz[-1] + 1]]
                        if i + 1 < len(keys):
                            nxt = keys[i + 1]
                            k2 = dict(k, values=[0.0]); n2 = dict(nxt, values=[1.0])
                            c = _curve42(k2, n2, 1, fps)
                            if c:
                                e["curve"] = c
                        jk.append(e)
                    key = nm(holder) if holder else nm(att)
                    ja.setdefault("deform", {}).setdefault(skin_of(att), {}).setdefault(nm(slot), {})[key] = jk
                elif prop == 7:  # IK: mix, softness, ..., bend direction
                    ik = T(tl.get("ref0"))
                    jk = []
                    for i, k in enumerate(keys):
                        v = k["values"]
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        e["mix"], e["softness"] = round(v[0], 6), round(v[1], 6)
                        if v[4] < 0:
                            e["bendPositive"] = False
                        if i + 1 < len(keys):
                            k2 = dict(k, values=v[:2]); n2 = dict(keys[i + 1], values=keys[i + 1]["values"][:2])
                            c = _curve42(k2, n2, 2, fps)
                            if c:
                                e["curve"] = c
                        jk.append(e)
                    ja.setdefault("ik", {})[nm(ik)] = jk
                elif prop == 13:  # draw order
                    jk = []
                    for k in keys:
                        e = {"time": round(k["time"] / fps, 6)} if k["time"] else {}
                        offs_map = {}
                        for o in (T(k.get("offsets")) or []):
                            offs_map[nm(T(o)[0])] = (T(o)[1] >> 1) ^ -(T(o)[1] & 1)
                        offs = [{"slot": s, "offset": v} for s, v in offs_map.items()]
                        if offs:
                            e["offsets"] = offs
                        jk.append(e)
                    ja["drawOrder"] = jk
                else:
                    notes.append(f"animation {name}: a timeline of type {prop} was not imported")
        out[name] = ja
    return {"animations": out, "notes": sorted(set(notes))}
