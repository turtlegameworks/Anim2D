"""World-transform solver matching the Spine 4.0 runtimes: Skeleton.updateCache and
updateWorldTransform, Bone.updateWorldTransformWith/updateAppliedTransform, IkConstraint,
TransformConstraint and PathConstraint.

Ported from the official spine-ts 4.0 runtime (spine-core 4.0.31) so the editor shows the same
pose as the game, including the runtime's quirks (for example the scale formula of local,
absolute transform constraints). Skeleton x/y/scale are the identity here.
Matrices use the runtime's layout: worldX' = a*x + b*y + worldX, worldY' = c*x + d*y + worldY.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

RAD = 180 / math.pi
DEG = math.pi / 180
PI2 = math.pi * 2
EPSILON = 1e-5


def _cos(deg: float) -> float:
    return math.cos(deg * DEG)


def _sin(deg: float) -> float:
    return math.sin(deg * DEG)


def _wrap(r: float) -> float:
    """Radians into [-pi, pi] the way the runtime does it (one step)."""
    if r > math.pi:
        return r - PI2
    if r < -math.pi:
        return r + PI2
    return r


def _wrap_deg(r: float) -> float:
    """r -= (16384 - (16384.499999999996 - r / 360 | 0)) * 360"""
    return r - (16384 - int(16384.499999999996 - r / 360)) * 360


@dataclass(eq=False)
class SolverBone:
    name: str
    index: int
    parent: "SolverBone | None"
    length: float
    transform: str
    skin_required: bool = False
    # posed local values
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    shear_x: float = 0.0
    shear_y: float = 0.0
    # applied values
    ax: float = 0.0
    ay: float = 0.0
    arotation: float = 0.0
    ascale_x: float = 1.0
    ascale_y: float = 1.0
    ashear_x: float = 0.0
    ashear_y: float = 0.0
    # world
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    world_x: float = 0.0
    world_y: float = 0.0
    children: list["SolverBone"] = field(default_factory=list)
    sorted: bool = False
    active: bool = True

    def update(self) -> None:
        self.update_with(self.ax, self.ay, self.arotation, self.ascale_x, self.ascale_y, self.ashear_x, self.ashear_y)

    def update_with(self, x, y, rotation, scale_x, scale_y, shear_x, shear_y) -> None:
        self.ax, self.ay, self.arotation = x, y, rotation
        self.ascale_x, self.ascale_y, self.ashear_x, self.ashear_y = scale_x, scale_y, shear_x, shear_y
        parent = self.parent
        if parent is None:
            rotation_y = rotation + 90 + shear_y
            self.a = _cos(rotation + shear_x) * scale_x
            self.b = _cos(rotation_y) * scale_y
            self.c = _sin(rotation + shear_x) * scale_x
            self.d = _sin(rotation_y) * scale_y
            self.world_x, self.world_y = x, y
            return
        pa, pb, pc, pd = parent.a, parent.b, parent.c, parent.d
        self.world_x = pa * x + pb * y + parent.world_x
        self.world_y = pc * x + pd * y + parent.world_y
        mode = self.transform
        if mode == "normal":
            rotation_y = rotation + 90 + shear_y
            la = _cos(rotation + shear_x) * scale_x
            lb = _cos(rotation_y) * scale_y
            lc = _sin(rotation + shear_x) * scale_x
            ld = _sin(rotation_y) * scale_y
            self.a = pa * la + pb * lc
            self.b = pa * lb + pb * ld
            self.c = pc * la + pd * lc
            self.d = pc * lb + pd * ld
            return
        if mode == "onlyTranslation":
            rotation_y = rotation + 90 + shear_y
            self.a = _cos(rotation + shear_x) * scale_x
            self.b = _cos(rotation_y) * scale_y
            self.c = _sin(rotation + shear_x) * scale_x
            self.d = _sin(rotation_y) * scale_y
        elif mode == "noRotationOrReflection":
            s = pa * pa + pc * pc
            if s > 1e-4:
                s = abs(pa * pd - pb * pc) / s
                pb = pc * s
                pd = pa * s
                prx = math.atan2(pc, pa) * RAD
            else:
                pa = pc = 0.0
                prx = 90 - math.atan2(pd, pb) * RAD
            rx = rotation + shear_x - prx
            ry = rotation + shear_y - prx + 90
            la, lb = _cos(rx) * scale_x, _cos(ry) * scale_y
            lc, ld = _sin(rx) * scale_x, _sin(ry) * scale_y
            self.a = pa * la - pb * lc
            self.b = pa * lb - pb * ld
            self.c = pc * la + pd * lc
            self.d = pc * lb + pd * ld
        elif mode in ("noScale", "noScaleOrReflection"):
            cos, sin = _cos(rotation), _sin(rotation)
            za = pa * cos + pb * sin
            zc = pc * cos + pd * sin
            s = math.sqrt(za * za + zc * zc)
            if s > 1e-5:
                s = 1 / s
            za *= s
            zc *= s
            s = math.sqrt(za * za + zc * zc)
            if mode == "noScale" and (pa * pd - pb * pc < 0):
                s = -s
            r = math.pi / 2 + math.atan2(zc, za)
            zb, zd = math.cos(r) * s, math.sin(r) * s
            la, lb = _cos(shear_x) * scale_x, _cos(90 + shear_y) * scale_y
            lc, ld = _sin(shear_x) * scale_x, _sin(90 + shear_y) * scale_y
            self.a = za * la + zb * lc
            self.b = za * lb + zb * ld
            self.c = zc * la + zd * lc
            self.d = zc * lb + zd * ld

    def update_applied(self) -> None:
        parent = self.parent
        if parent is None:
            self.ax, self.ay = self.world_x, self.world_y
            self.arotation = math.atan2(self.c, self.a) * RAD
            self.ascale_x = math.sqrt(self.a * self.a + self.c * self.c)
            self.ascale_y = math.sqrt(self.b * self.b + self.d * self.d)
            self.ashear_x = 0.0
            self.ashear_y = math.atan2(self.a * self.b + self.c * self.d, self.a * self.d - self.b * self.c) * RAD
            return
        pa, pb, pc, pd = parent.a, parent.b, parent.c, parent.d
        det = pa * pd - pb * pc
        pid = 1 / det if det else math.inf  # a collapsed parent: NaN like the runtime, not an exception
        dx, dy = self.world_x - parent.world_x, self.world_y - parent.world_y
        self.ax = dx * pd * pid - dy * pb * pid
        self.ay = dy * pa * pid - dx * pc * pid
        ia, id_, ib, ic = pid * pd, pid * pa, pid * pb, pid * pc
        ra = ia * self.a - ib * self.c
        rb = ia * self.b - ib * self.d
        rc = id_ * self.c - ic * self.a
        rd = id_ * self.d - ic * self.b
        self.ashear_x = 0.0
        self.ascale_x = math.sqrt(ra * ra + rc * rc)
        if self.ascale_x > 1e-4:
            det = ra * rd - rb * rc
            self.ascale_y = det / self.ascale_x
            self.ashear_y = math.atan2(ra * rb + rc * rd, det) * RAD
            self.arotation = math.atan2(rc, ra) * RAD
        else:
            self.ascale_x = 0.0
            self.ascale_y = math.sqrt(rb * rb + rd * rd)
            self.ashear_y = 0.0
            self.arotation = 90 - math.atan2(rd, rb) * RAD

    def local_to_world(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.b * y + self.world_x, self.c * x + self.d * y + self.world_y


# --- IK ------------------------------------------------------------------------------------

@dataclass(eq=False)
class SolverIk:
    name: str
    order: int
    skin_required: bool
    bones: list[SolverBone]
    target: SolverBone
    mix: float = 1.0
    softness: float = 0.0
    bend: int = 1
    compress: bool = False
    stretch: bool = False
    uniform: bool = False
    active: bool = True

    def update(self) -> None:
        if self.mix == 0:
            return
        t = self.target
        if len(self.bones) == 1:
            apply1(self.bones[0], t.world_x, t.world_y, self.compress, self.stretch, self.uniform, self.mix)
        elif len(self.bones) == 2:
            apply2(self.bones[0], self.bones[1], t.world_x, t.world_y, self.bend, self.stretch, self.uniform,
                   self.softness, self.mix)


def apply1(bone: SolverBone, target_x, target_y, compress, stretch, uniform, alpha) -> None:
    p = bone.parent
    pa, pb, pc, pd = p.a, p.b, p.c, p.d
    rotation_ik = -bone.ashear_x - bone.arotation
    mode = bone.transform
    if mode == "onlyTranslation":
        tx, ty = target_x - bone.world_x, target_y - bone.world_y
    else:
        if mode == "noRotationOrReflection":
            s = abs(pa * pd - pb * pc) / (pa * pa + pc * pc)
            sa, sc = pa, pc
            pb, pd = -sc * s, sa * s
            rotation_ik += math.atan2(sc, sa) * RAD
        x, y = target_x - p.world_x, target_y - p.world_y
        d = pa * pd - pb * pc
        tx = (x * pd - y * pb) / d - bone.ax
        ty = (y * pa - x * pc) / d - bone.ay
    rotation_ik += math.atan2(ty, tx) * RAD
    if bone.ascale_x < 0:
        rotation_ik += 180
    if rotation_ik > 180:
        rotation_ik -= 360
    elif rotation_ik < -180:
        rotation_ik += 360
    sx, sy = bone.ascale_x, bone.ascale_y
    if compress or stretch:
        if mode in ("noScale", "noScaleOrReflection"):
            tx, ty = target_x - bone.world_x, target_y - bone.world_y
        b = bone.length * sx
        dd = math.sqrt(tx * tx + ty * ty)
        if (compress and dd < b) or (stretch and dd > b and b > 1e-4):
            s = (dd / b - 1) * alpha + 1
            sx *= s
            if uniform:
                sy *= s
    bone.update_with(bone.ax, bone.ay, bone.arotation + rotation_ik * alpha, sx, sy, bone.ashear_x, bone.ashear_y)


def apply2(parent: SolverBone, child: SolverBone, target_x, target_y, bend_dir, stretch, uniform, softness,
           alpha) -> None:
    px, py, psx, psy, csx = parent.ax, parent.ay, parent.ascale_x, parent.ascale_y, child.ascale_x
    sx, sy = psx, psy
    if psx < 0:
        psx, os1, s2 = -psx, 180, -1
    else:
        os1, s2 = 0, 1
    if psy < 0:
        psy, s2 = -psy, -s2
    if csx < 0:
        csx, os2 = -csx, 180
    else:
        os2 = 0
    cx = child.ax
    a, b, c, d = parent.a, parent.b, parent.c, parent.d
    u = abs(psx - psy) <= 1e-4
    if not u or stretch:
        cy = 0.0
        cwx = a * cx + parent.world_x
        cwy = c * cx + parent.world_y
    else:
        cy = child.ay
        cwx = a * cx + b * cy + parent.world_x
        cwy = c * cx + d * cy + parent.world_y
    pp = parent.parent
    a, b, c, d = pp.a, pp.b, pp.c, pp.d
    id_ = 1 / (a * d - b * c)
    x, y = cwx - pp.world_x, cwy - pp.world_y
    dx = (x * d - y * b) * id_ - px
    dy = (y * a - x * c) * id_ - py
    l1 = math.sqrt(dx * dx + dy * dy)
    l2 = child.length * csx
    if l1 < 1e-4:
        apply1(parent, target_x, target_y, False, stretch, False, alpha)
        child.update_with(cx, cy, 0, child.ascale_x, child.ascale_y, child.ashear_x, child.ashear_y)
        return
    x, y = target_x - pp.world_x, target_y - pp.world_y
    tx = (x * d - y * b) * id_ - px
    ty = (y * a - x * c) * id_ - py
    dd = tx * tx + ty * ty
    if softness != 0:
        softness *= psx * (csx + 1) * 0.5
        td = math.sqrt(dd)
        sd = td - l1 - l2 * psx + softness
        if sd > 0:
            p = min(1, sd / (softness * 2)) - 1
            p = (sd - softness * (1 - p * p)) / td
            tx -= p * tx
            ty -= p * ty
            dd = tx * tx + ty * ty
    if u:
        l2 *= psx
        cos = (dd - l1 * l1 - l2 * l2) / (2 * l1 * l2)
        if cos < -1:
            cos = -1
            a2 = math.pi * bend_dir
        elif cos > 1:
            cos = 1
            a2 = 0.0
            if stretch:
                a = (math.sqrt(dd) / (l1 + l2) - 1) * alpha + 1
                sx *= a
                if uniform:
                    sy *= a
        else:
            a2 = math.acos(cos) * bend_dir
        a = l1 + l2 * cos
        b = l2 * math.sin(a2)
        a1 = math.atan2(ty * a - tx * b, tx * a + ty * b)
    else:
        a1 = a2 = None
        a = psx * l2
        b = psy * l2
        aa, bb, ta = a * a, b * b, math.atan2(ty, tx)
        c = bb * l1 * l1 + aa * dd - aa * bb
        c1, c2 = -2 * bb * l1, bb - aa
        d = c1 * c1 - 4 * c2 * c
        if d >= 0:
            q = math.sqrt(d)
            if c1 < 0:
                q = -q
            q = -(c1 + q) * 0.5
            r0, r1 = q / c2, c / q
            r = r0 if abs(r0) < abs(r1) else r1
            if r * r <= dd:
                y = math.sqrt(dd - r * r) * bend_dir
                a1 = ta - math.atan2(y, r)
                a2 = math.atan2(y / psy, (r - l1) / psx)
        if a1 is None:
            min_angle, min_x, min_y = math.pi, l1 - a, 0.0
            min_dist = min_x * min_x
            max_angle, max_x, max_y = 0.0, l1 + a, 0.0
            max_dist = max_x * max_x
            c = -a * l1 / (aa - bb)
            if -1 <= c <= 1:
                c = math.acos(c)
                x = a * math.cos(c) + l1
                y = b * math.sin(c)
                d = x * x + y * y
                if d < min_dist:
                    min_angle, min_dist, min_x, min_y = c, d, x, y
                if d > max_dist:
                    max_angle, max_dist, max_x, max_y = c, d, x, y
            if dd <= (min_dist + max_dist) * 0.5:
                a1 = ta - math.atan2(min_y * bend_dir, min_x)
                a2 = min_angle * bend_dir
            else:
                a1 = ta - math.atan2(max_y * bend_dir, max_x)
                a2 = max_angle * bend_dir
    os = math.atan2(cy, cx) * s2
    rotation = parent.arotation
    a1 = (a1 - os) * RAD + os1 - rotation
    if a1 > 180:
        a1 -= 360
    elif a1 < -180:
        a1 += 360
    parent.update_with(px, py, rotation + a1 * alpha, sx, sy, 0, 0)
    rotation = child.arotation
    a2 = ((a2 + os) * RAD - child.ashear_x) * s2 + os2 - rotation
    if a2 > 180:
        a2 -= 360
    elif a2 < -180:
        a2 += 360
    child.update_with(cx, cy, rotation + a2 * alpha, child.ascale_x, child.ascale_y, child.ashear_x, child.ashear_y)


# --- transform constraints -------------------------------------------------------------------

@dataclass(eq=False)
class SolverTransform:
    name: str
    order: int
    skin_required: bool
    bones: list[SolverBone]
    target: SolverBone
    local: bool = False
    relative: bool = False
    offset_rotation: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_scale_x: float = 0.0
    offset_scale_y: float = 0.0
    offset_shear_y: float = 0.0
    mix_rotate: float = 1.0
    mix_x: float = 1.0
    mix_y: float = 1.0
    mix_scale_x: float = 1.0
    mix_scale_y: float = 1.0
    mix_shear_y: float = 1.0
    active: bool = True

    def update(self) -> None:
        if (self.mix_rotate == 0 and self.mix_x == 0 and self.mix_y == 0 and self.mix_scale_x == 0
                and self.mix_shear_y == 0):  # the runtime tests mixScaleX twice and never mixScaleY
            return
        if self.local:
            self._relative_local() if self.relative else self._absolute_local()
        else:
            self._relative_world() if self.relative else self._absolute_world()

    def _absolute_world(self) -> None:
        t = self.target
        ta, tb, tc, td = t.a, t.b, t.c, t.d
        reflect = DEG if ta * td - tb * tc > 0 else -DEG
        offset_rotation = self.offset_rotation * reflect
        offset_shear_y = self.offset_shear_y * reflect
        translate = self.mix_x != 0 or self.mix_y != 0
        for bone in self.bones:
            if self.mix_rotate != 0:
                a, b, c, d = bone.a, bone.b, bone.c, bone.d
                r = _wrap(math.atan2(tc, ta) - math.atan2(c, a) + offset_rotation) * self.mix_rotate
                cos, sin = math.cos(r), math.sin(r)
                bone.a, bone.b = cos * a - sin * c, cos * b - sin * d
                bone.c, bone.d = sin * a + cos * c, sin * b + cos * d
            if translate:
                x, y = t.local_to_world(self.offset_x, self.offset_y)
                bone.world_x += (x - bone.world_x) * self.mix_x
                bone.world_y += (y - bone.world_y) * self.mix_y
            if self.mix_scale_x != 0:
                s = math.sqrt(bone.a * bone.a + bone.c * bone.c)
                if s != 0:
                    s = (s + (math.sqrt(ta * ta + tc * tc) - s + self.offset_scale_x) * self.mix_scale_x) / s
                bone.a *= s
                bone.c *= s
            if self.mix_scale_y != 0:
                s = math.sqrt(bone.b * bone.b + bone.d * bone.d)
                if s != 0:
                    s = (s + (math.sqrt(tb * tb + td * td) - s + self.offset_scale_y) * self.mix_scale_y) / s
                bone.b *= s
                bone.d *= s
            if self.mix_shear_y > 0:
                b, d = bone.b, bone.d
                by = math.atan2(d, b)
                r = _wrap(math.atan2(td, tb) - math.atan2(tc, ta) - (by - math.atan2(bone.c, bone.a)))
                r = by + (r + offset_shear_y) * self.mix_shear_y
                s = math.sqrt(b * b + d * d)
                bone.b, bone.d = math.cos(r) * s, math.sin(r) * s
            bone.update_applied()

    def _relative_world(self) -> None:
        t = self.target
        ta, tb, tc, td = t.a, t.b, t.c, t.d
        reflect = DEG if ta * td - tb * tc > 0 else -DEG
        offset_rotation = self.offset_rotation * reflect
        offset_shear_y = self.offset_shear_y * reflect
        translate = self.mix_x != 0 or self.mix_y != 0
        for bone in self.bones:
            if self.mix_rotate != 0:
                a, b, c, d = bone.a, bone.b, bone.c, bone.d
                r = _wrap(math.atan2(tc, ta) + offset_rotation) * self.mix_rotate
                cos, sin = math.cos(r), math.sin(r)
                bone.a, bone.b = cos * a - sin * c, cos * b - sin * d
                bone.c, bone.d = sin * a + cos * c, sin * b + cos * d
            if translate:
                x, y = t.local_to_world(self.offset_x, self.offset_y)
                bone.world_x += x * self.mix_x
                bone.world_y += y * self.mix_y
            if self.mix_scale_x != 0:
                s = (math.sqrt(ta * ta + tc * tc) - 1 + self.offset_scale_x) * self.mix_scale_x + 1
                bone.a *= s
                bone.c *= s
            if self.mix_scale_y != 0:
                s = (math.sqrt(tb * tb + td * td) - 1 + self.offset_scale_y) * self.mix_scale_y + 1
                bone.b *= s
                bone.d *= s
            if self.mix_shear_y > 0:
                r = _wrap(math.atan2(td, tb) - math.atan2(tc, ta))
                b, d = bone.b, bone.d
                r = math.atan2(d, b) + (r - math.pi / 2 + offset_shear_y) * self.mix_shear_y
                s = math.sqrt(b * b + d * d)
                bone.b, bone.d = math.cos(r) * s, math.sin(r) * s
            bone.update_applied()

    def _absolute_local(self) -> None:
        t = self.target
        for bone in self.bones:
            rotation = bone.arotation
            if self.mix_rotate != 0:
                rotation += _wrap_deg(t.arotation - rotation + self.offset_rotation) * self.mix_rotate
            x = bone.ax + (t.ax - bone.ax + self.offset_x) * self.mix_x
            y = bone.ay + (t.ay - bone.ay + self.offset_y) * self.mix_y
            scale_x, scale_y = bone.ascale_x, bone.ascale_y
            if self.mix_scale_x != 0 and scale_x != 0:  # as in the 4.0 runtime (divides by the scale)
                scale_x = (scale_x + (t.ascale_x - scale_x + self.offset_scale_x) * self.mix_scale_x) / scale_x
            if self.mix_scale_y != 0 and scale_y != 0:
                scale_y = (scale_y + (t.ascale_y - scale_y + self.offset_scale_y) * self.mix_scale_y) / scale_y
            shear_y = bone.ashear_y
            if self.mix_shear_y != 0:
                shear_y += _wrap_deg(t.ashear_y - shear_y + self.offset_shear_y) * self.mix_shear_y
            bone.update_with(x, y, rotation, scale_x, scale_y, bone.ashear_x, shear_y)

    def _relative_local(self) -> None:
        t = self.target
        for bone in self.bones:
            rotation = bone.arotation + (t.arotation + self.offset_rotation) * self.mix_rotate
            x = bone.ax + (t.ax + self.offset_x) * self.mix_x
            y = bone.ay + (t.ay + self.offset_y) * self.mix_y
            scale_x = bone.ascale_x * ((t.ascale_x - 1 + self.offset_scale_x) * self.mix_scale_x + 1)
            scale_y = bone.ascale_y * ((t.ascale_y - 1 + self.offset_scale_y) * self.mix_scale_y + 1)
            shear_y = bone.ashear_y + (t.ashear_y + self.offset_shear_y) * self.mix_shear_y
            bone.update_with(x, y, rotation, scale_x, scale_y, bone.ashear_x, shear_y)


# --- path constraints ------------------------------------------------------------------------

def path_world_vertices(att: dict[str, Any], slot_bone: SolverBone, bones: list[SolverBone],
                        deform: list[float] | None) -> list[float]:
    """All world vertices of a path attachment, flat [x, y, ...] (VertexAttachment.computeWorldVertices)."""
    raw = att.get("vertices", [])
    count = int(att.get("vertexCount", 0))
    out: list[float] = []
    if len(raw) == count * 2:
        a, b, c, d, wx, wy = slot_bone.a, slot_bone.b, slot_bone.c, slot_bone.d, slot_bone.world_x, slot_bone.world_y
        for i in range(0, len(raw), 2):
            vx, vy = raw[i], raw[i + 1]
            if deform:
                vx, vy = vx + deform[i], vy + deform[i + 1]  # unweighted deform holds offsets here
            out += [vx * a + vy * b + wx, vx * c + vy * d + wy]
        return out
    i = f = 0
    while i < len(raw):
        n = int(raw[i])
        i += 1
        x = y = 0.0
        for _ in range(n):
            bone = bones[int(raw[i])]
            vx, vy, weight = raw[i + 1], raw[i + 2], raw[i + 3]
            if deform:
                vx, vy = vx + deform[f], vy + deform[f + 1]
            f += 2
            x += (vx * bone.a + vy * bone.b + bone.world_x) * weight
            y += (vx * bone.c + vy * bone.d + bone.world_y) * weight
            i += 4
        out += [x, y]
    return out


@dataclass(eq=False)
class SolverPath:
    name: str
    order: int
    skin_required: bool
    bones: list[SolverBone]
    target_slot: str
    target_bone: SolverBone
    position_mode: str = "percent"
    spacing_mode: str = "length"
    rotate_mode: str = "tangent"
    offset_rotation: float = 0.0
    position: float = 0.0
    spacing: float = 0.0
    mix_rotate: float = 1.0
    mix_x: float = 1.0
    mix_y: float = 1.0
    active: bool = True
    # set by the solver before each update: the target slot's attachment and its deform offsets
    attachment: dict[str, Any] | None = None
    deform: list[float] | None = None
    all_bones: list[SolverBone] = field(default_factory=list)

    def update(self) -> None:
        att = self.attachment
        if att is None or att.get("type") != "path":
            return
        mix_rotate, mix_x, mix_y = self.mix_rotate, self.mix_x, self.mix_y
        if mix_rotate == 0 and mix_x == 0 and mix_y == 0:
            return
        tangents = self.rotate_mode == "tangent"
        scale = self.rotate_mode == "chainScale"
        bones = self.bones
        bone_count = len(bones)
        spaces_count = bone_count if tangents else bone_count + 1
        spaces = [0.0] * spaces_count
        lengths = [0.0] * bone_count if scale else None
        spacing = self.spacing
        if self.spacing_mode == "percent":
            if scale:
                for i in range(spaces_count - 1):
                    bone = bones[i]
                    if bone.length < EPSILON:
                        lengths[i] = 0.0
                    else:
                        x, y = bone.length * bone.a, bone.length * bone.c
                        lengths[i] = math.sqrt(x * x + y * y)
            for i in range(1, spaces_count):
                spaces[i] = spacing
        elif self.spacing_mode == "proportional":
            total = 0.0
            i = 0
            while i < spaces_count - 1:
                bone = bones[i]
                if bone.length < EPSILON:
                    if scale:
                        lengths[i] = 0.0
                    i += 1
                    spaces[i] = spacing
                else:
                    x, y = bone.length * bone.a, bone.length * bone.c
                    length = math.sqrt(x * x + y * y)
                    if scale:
                        lengths[i] = length
                    i += 1
                    spaces[i] = length
                    total += length
            if total > 0:
                total = spaces_count / total * spacing
                for i in range(1, spaces_count):
                    spaces[i] *= total
        else:
            length_spacing = self.spacing_mode == "length"
            i = 0
            while i < spaces_count - 1:
                bone = bones[i]
                if bone.length < EPSILON:
                    if scale:
                        lengths[i] = 0.0
                    i += 1
                    spaces[i] = spacing
                else:
                    x, y = bone.length * bone.a, bone.length * bone.c
                    length = math.sqrt(x * x + y * y)
                    if scale:
                        lengths[i] = length
                    i += 1
                    spaces[i] = (bone.length + spacing if length_spacing else spacing) * length / bone.length
        positions = self._world_positions(att, spaces, spaces_count, tangents)
        bone_x, bone_y = positions[0], positions[1]
        offset_rotation = self.offset_rotation
        if offset_rotation == 0:
            tip = self.rotate_mode == "chain"
        else:
            tip = False
            p = self.target_bone
            offset_rotation *= DEG if p.a * p.d - p.b * p.c > 0 else -DEG
        p = 3
        for i in range(bone_count):
            bone = bones[i]
            bone.world_x += (bone_x - bone.world_x) * mix_x
            bone.world_y += (bone_y - bone.world_y) * mix_y
            x, y = positions[p], positions[p + 1]
            dx, dy = x - bone_x, y - bone_y
            if scale:
                length = lengths[i]
                if length != 0:
                    s = (math.sqrt(dx * dx + dy * dy) / length - 1) * mix_rotate + 1
                    bone.a *= s
                    bone.c *= s
            bone_x, bone_y = x, y
            if mix_rotate > 0:
                a, b, c, d = bone.a, bone.b, bone.c, bone.d
                if tangents:
                    r = positions[p - 1]
                elif spaces[i + 1] == 0:
                    r = positions[p + 2]
                else:
                    r = math.atan2(dy, dx)
                r -= math.atan2(c, a)
                if tip:
                    cos, sin = math.cos(r), math.sin(r)
                    length = bone.length
                    bone_x += (length * (cos * a - sin * c) - dx) * mix_rotate
                    bone_y += (length * (sin * a + cos * c) - dy) * mix_rotate
                else:
                    r += offset_rotation
                r = _wrap(r) * mix_rotate
                cos, sin = math.cos(r), math.sin(r)
                bone.a, bone.b = cos * a - sin * c, cos * b - sin * d
                bone.c, bone.d = sin * a + cos * c, sin * b + cos * d
            bone.update_applied()
            p += 3

    def _world_positions(self, att: dict[str, Any], spaces: list[float], spaces_count: int,
                         tangents: bool) -> list[float]:
        all_world = path_world_vertices(att, self.target_bone, self.all_bones, self.deform)

        def world(start: int, count: int) -> list[float]:  # computeWorldVertices(slot, start, count, ...)
            return all_world[start:start + count]

        position = self.position
        out = [0.0] * (spaces_count * 3 + 2)
        closed = bool(att.get("closed", False))
        vertices_length = int(att.get("vertexCount", 0)) * 2
        curve_count = vertices_length // 6
        prev_curve = -1
        if not att.get("constantSpeed", True):
            lengths = att.get("lengths", [])
            curve_count -= 1 if closed else 2
            path_length = lengths[curve_count]
            if self.position_mode == "percent":
                position *= path_length
            multiplier = path_length if self.spacing_mode == "percent" else \
                path_length / spaces_count if self.spacing_mode == "proportional" else 1
            w = [0.0] * 8
            curve = 0
            o = 0
            for i in range(spaces_count):
                space = spaces[i] * multiplier
                position += space
                p = position
                if closed:
                    p %= path_length
                    if p < 0:
                        p += path_length
                    curve = 0
                elif p < 0:
                    if prev_curve != -2:
                        prev_curve = -2
                        w[0:4] = world(2, 4)
                    _before(p, w, 0, out, o)
                    o += 3
                    continue
                elif p > path_length:
                    if prev_curve != -3:
                        prev_curve = -3
                        w[0:4] = world(vertices_length - 6, 4)
                    _after(p - path_length, w, 0, out, o)
                    o += 3
                    continue
                while True:
                    length = lengths[curve]
                    if p > length:
                        curve += 1
                        continue
                    if curve == 0:
                        p /= length
                    else:
                        prev = lengths[curve - 1]
                        p = (p - prev) / (length - prev)
                    break
                if curve != prev_curve:
                    prev_curve = curve
                    if closed and curve == curve_count:
                        w[0:4] = world(vertices_length - 4, 4)
                        w[4:8] = world(0, 4)
                    else:
                        w[0:8] = world(curve * 6 + 2, 8)
                _curve_position(p, *w, out, o, tangents or (i > 0 and space == 0))
                o += 3
            return out

        if closed:
            vertices_length += 2
            w = world(2, vertices_length - 4) + world(0, 2)
            w += [w[0], w[1]]
        else:
            curve_count -= 1
            vertices_length -= 4
            w = world(2, vertices_length)
        curves = [0.0] * curve_count
        path_length = 0.0
        x1, y1 = w[0], w[1]
        ww = 2
        for i in range(curve_count):
            cx1, cy1, cx2, cy2, x2, y2 = w[ww:ww + 6]
            tmpx = (x1 - cx1 * 2 + cx2) * 0.1875
            tmpy = (y1 - cy1 * 2 + cy2) * 0.1875
            dddfx = ((cx1 - cx2) * 3 - x1 + x2) * 0.09375
            dddfy = ((cy1 - cy2) * 3 - y1 + y2) * 0.09375
            ddfx, ddfy = tmpx * 2 + dddfx, tmpy * 2 + dddfy
            dfx = (cx1 - x1) * 0.75 + tmpx + dddfx * 0.16666667
            dfy = (cy1 - y1) * 0.75 + tmpy + dddfy * 0.16666667
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx
            dfy += ddfy
            ddfx += dddfx
            ddfy += dddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx
            dfy += ddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx + dddfx
            dfy += ddfy + dddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            curves[i] = path_length
            x1, y1 = x2, y2
            ww += 6
        if self.position_mode == "percent":
            position *= path_length
        multiplier = path_length if self.spacing_mode == "percent" else \
            path_length / spaces_count if self.spacing_mode == "proportional" else 1
        segments = [0.0] * 10
        curve_length = 0.0
        curve = segment = 0
        x1 = y1 = cx1 = cy1 = cx2 = cy2 = x2 = y2 = 0.0
        o = 0
        for i in range(spaces_count):
            space = spaces[i] * multiplier
            position += space
            p = position
            if closed:
                p %= path_length
                if p < 0:
                    p += path_length
                curve = 0
            elif p < 0:
                _before(p, w, 0, out, o)
                o += 3
                continue
            elif p > path_length:
                _after(p - path_length, w, vertices_length - 4, out, o)
                o += 3
                continue
            while True:
                length = curves[curve]
                if p > length:
                    curve += 1
                    continue
                if curve == 0:
                    p /= length
                else:
                    prev = curves[curve - 1]
                    p = (p - prev) / (length - prev)
                break
            if curve != prev_curve:
                prev_curve = curve
                ii = curve * 6
                x1, y1, cx1, cy1, cx2, cy2, x2, y2 = w[ii:ii + 8]
                tmpx = (x1 - cx1 * 2 + cx2) * 0.03
                tmpy = (y1 - cy1 * 2 + cy2) * 0.03
                dddfx = ((cx1 - cx2) * 3 - x1 + x2) * 6e-3
                dddfy = ((cy1 - cy2) * 3 - y1 + y2) * 6e-3
                ddfx, ddfy = tmpx * 2 + dddfx, tmpy * 2 + dddfy
                dfx = (cx1 - x1) * 0.3 + tmpx + dddfx * 0.16666667
                dfy = (cy1 - y1) * 0.3 + tmpy + dddfy * 0.16666667
                curve_length = math.sqrt(dfx * dfx + dfy * dfy)
                segments[0] = curve_length
                for ii in range(1, 8):
                    dfx += ddfx
                    dfy += ddfy
                    ddfx += dddfx
                    ddfy += dddfy
                    curve_length += math.sqrt(dfx * dfx + dfy * dfy)
                    segments[ii] = curve_length
                dfx += ddfx
                dfy += ddfy
                curve_length += math.sqrt(dfx * dfx + dfy * dfy)
                segments[8] = curve_length
                dfx += ddfx + dddfx
                dfy += ddfy + dddfy
                curve_length += math.sqrt(dfx * dfx + dfy * dfy)
                segments[9] = curve_length
                segment = 0
            p *= curve_length
            while True:
                length = segments[segment]
                if p > length:
                    segment += 1
                    continue
                if segment == 0:
                    p /= length
                else:
                    prev = segments[segment - 1]
                    p = segment + (p - prev) / (length - prev)
                break
            _curve_position(p * 0.1, x1, y1, cx1, cy1, cx2, cy2, x2, y2, out, o, tangents or (i > 0 and space == 0))
            o += 3
        return out


def _before(p: float, temp: list[float], i: int, out: list[float], o: int) -> None:
    x1, y1 = temp[i], temp[i + 1]
    r = math.atan2(temp[i + 3] - y1, temp[i + 2] - x1)
    out[o], out[o + 1], out[o + 2] = x1 + p * math.cos(r), y1 + p * math.sin(r), r


def _after(p: float, temp: list[float], i: int, out: list[float], o: int) -> None:
    x1, y1 = temp[i + 2], temp[i + 3]
    r = math.atan2(y1 - temp[i + 1], x1 - temp[i])
    out[o], out[o + 1], out[o + 2] = x1 + p * math.cos(r), y1 + p * math.sin(r), r


def _curve_position(p, x1, y1, cx1, cy1, cx2, cy2, x2, y2, out, o, tangents) -> None:
    if p == 0 or math.isnan(p):
        out[o], out[o + 1], out[o + 2] = x1, y1, math.atan2(cy1 - y1, cx1 - x1)
        return
    tt = p * p
    ttt = tt * p
    u = 1 - p
    uu = u * u
    uuu = uu * u
    ut = u * p
    ut3 = ut * 3
    uut3 = u * ut3
    utt3 = ut3 * p
    x = x1 * uuu + cx1 * uut3 + cx2 * utt3 + x2 * ttt
    y = y1 * uuu + cy1 * uut3 + cy2 * utt3 + y2 * ttt
    out[o], out[o + 1] = x, y
    if tangents:
        if p < 1e-3:
            out[o + 2] = math.atan2(cy1 - y1, cx1 - x1)
        else:
            out[o + 2] = math.atan2(y - (y1 * uu + cy1 * ut * 2 + cy2 * tt), x - (x1 * uu + cx1 * ut * 2 + cx2 * tt))


# --- the skeleton ------------------------------------------------------------------------------

TRANSFORM_KEYS = ("mix_rotate", "mix_x", "mix_y", "mix_scale_x", "mix_scale_y", "mix_shear_y")
PATH_KEYS = ("position", "spacing", "mix_rotate", "mix_x", "mix_y")


class SkeletonSolver:
    """Builds the runtime's update cache for a skin; solve() poses and returns world matrices."""

    def __init__(self, data: dict[str, Any], skin: str | None = None):
        self.bones: dict[str, SolverBone] = {}
        ordered: list[SolverBone] = []
        for i, b in enumerate(data.get("bones", [])):
            parent = self.bones.get(b.get("parent")) if b.get("parent") else None
            bone = SolverBone(b["name"], i, parent, float(b.get("length", 0)), b.get("transform", "normal"),
                              bool(b.get("skin", False)))
            if parent:
                parent.children.append(bone)
            self.bones[bone.name] = bone
            ordered.append(bone)
        self.ordered = ordered
        slots = {s["name"]: s for s in data.get("slots", [])}
        self.iks: list[SolverIk] = []
        for c in data.get("ik", []):
            bones = [self.bones[n] for n in c.get("bones", []) if n in self.bones]
            target = self.bones.get(c.get("target"))
            if not bones or target is None:
                continue
            self.iks.append(SolverIk(c["name"], int(c.get("order", 0)), bool(c.get("skin", False)), bones, target,
                                     float(c.get("mix", 1)), float(c.get("softness", 0)),
                                     1 if c.get("bendPositive", True) else -1, bool(c.get("compress", False)),
                                     bool(c.get("stretch", False)), bool(c.get("uniform", False))))
        self.transforms: list[SolverTransform] = []
        for c in data.get("transform", []):
            bones = [self.bones[n] for n in c.get("bones", []) if n in self.bones]
            target = self.bones.get(c.get("target"))
            if not bones or target is None:
                continue
            mix_x = float(c.get("mixX", 1))
            mix_scale_x = float(c.get("mixScaleX", 1))
            self.transforms.append(SolverTransform(
                c["name"], int(c.get("order", 0)), bool(c.get("skin", False)), bones, target,
                bool(c.get("local", False)), bool(c.get("relative", False)), float(c.get("rotation", 0)),
                float(c.get("x", 0)), float(c.get("y", 0)), float(c.get("scaleX", 0)), float(c.get("scaleY", 0)),
                float(c.get("shearY", 0)), float(c.get("mixRotate", 1)), mix_x, float(c.get("mixY", mix_x)),
                mix_scale_x, float(c.get("mixScaleY", mix_scale_x)), float(c.get("mixShearY", 1))))
        self.paths: list[SolverPath] = []
        for c in data.get("path", []):
            bones = [self.bones[n] for n in c.get("bones", []) if n in self.bones]
            slot = slots.get(c.get("target"))
            if not bones or slot is None or slot["bone"] not in self.bones:
                continue
            self.paths.append(SolverPath(
                c["name"], int(c.get("order", 0)), bool(c.get("skin", False)), bones, slot["name"],
                self.bones[slot["bone"]], c.get("positionMode", "percent"), c.get("spacingMode", "length"),
                c.get("rotateMode", "tangent"), float(c.get("rotation", 0)), float(c.get("position", 0)),
                float(c.get("spacing", 0)), float(c.get("mixRotate", 1)), float(c.get("mixX", 1)),
                float(c.get("mixY", 1)), all_bones=ordered))
        self.ik_by_name = {c.name: c for c in self.iks}
        self.transform_by_name = {c.name: c for c in self.transforms}
        self.path_by_name = {c.name: c for c in self.paths}
        self._ik_setup = {c.name: {"mix": c.mix, "softness": c.softness, "bend": c.bend, "compress": c.compress,
                                   "stretch": c.stretch} for c in self.iks}
        self._transform_setup = {c.name: {k: getattr(c, k) for k in TRANSFORM_KEYS} for c in self.transforms}
        self._path_setup = {c.name: {k: getattr(c, k) for k in PATH_KEYS} for c in self.paths}
        self._skins = {s.get("name"): s for s in data.get("skins", [])}
        self._path_attachment_bones = self._path_bones(data)
        self.skin = skin
        self._build_cache(skin)

    def _path_bones(self, data: dict[str, Any]) -> dict[str, list[list[int]]]:
        """Per path-constraint target slot: the bone index lists of every path attachment any skin
        has for that slot (sortPathConstraintAttachment)."""
        out: dict[str, list[list[int]]] = {}
        for skin in data.get("skins", []):
            for slot, entries in skin.get("attachments", {}).items():
                for att in entries.values():
                    if att.get("type") != "path":
                        continue
                    raw, count = att.get("vertices", []), int(att.get("vertexCount", 0))
                    if len(raw) == count * 2:
                        out.setdefault(slot, []).append([])
                        continue
                    indices, i = [], 0
                    while i < len(raw):
                        n = int(raw[i])
                        i += 1
                        for _ in range(n):
                            indices.append(int(raw[i]))
                            i += 4
                    out.setdefault(slot, []).append(indices)
        return out

    def _skin_has(self, kind: str, name: str) -> bool:
        skin = self._skins.get(self.skin) if self.skin else None
        return bool(skin) and name in skin.get(kind, [])

    def _build_cache(self, skin: str | None) -> None:
        self._cache: list[Any] = []
        for bone in self.ordered:
            bone.sorted = bone.skin_required
            bone.active = not bone.sorted
        skin_data = self._skins.get(skin) if skin else None
        if skin_data:
            for name in skin_data.get("bones", []):
                bone = self.bones.get(name)
                while bone is not None:
                    bone.sorted = False
                    bone.active = True
                    bone = bone.parent
        constraints = [*self.iks, *self.transforms, *self.paths]
        for order in range(len(constraints)):  # the runtime applies orders 0..n-1 only
            for group in (self.iks, self.transforms, self.paths):
                found = next((c for c in group if c.order == order), None)
                if found is not None:
                    break
            if found is None:
                continue
            if isinstance(found, SolverIk):
                self._sort_ik(found)
            elif isinstance(found, SolverTransform):
                self._sort_transform(found)
            else:
                self._sort_path(found)
        for bone in self.ordered:
            self._sort_bone(bone)

    def _constraint_active(self, c, kind: str, target_active: bool) -> bool:
        c.active = target_active and (not c.skin_required or self._skin_has(kind, c.name))
        return c.active

    def _sort_ik(self, c: SolverIk) -> None:
        if not self._constraint_active(c, "ik", c.target.active):
            return
        self._sort_bone(c.target)
        parent = c.bones[0]
        self._sort_bone(parent)
        if len(c.bones) == 1:
            self._cache.append(c)
            self._sort_reset(parent.children)
        else:
            child = c.bones[-1]
            self._sort_bone(child)
            self._cache.append(c)
            self._sort_reset(parent.children)
            child.sorted = True

    def _sort_transform(self, c: SolverTransform) -> None:
        if not self._constraint_active(c, "transform", c.target.active):
            return
        self._sort_bone(c.target)
        if c.local:
            for child in c.bones:
                self._sort_bone(child.parent)
                self._sort_bone(child)
        else:
            for bone in c.bones:
                self._sort_bone(bone)
        self._cache.append(c)
        for bone in c.bones:
            self._sort_reset(bone.children)
        for bone in c.bones:
            bone.sorted = True

    def _sort_path(self, c: SolverPath) -> None:
        if not self._constraint_active(c, "path", c.target_bone.active):
            return
        for indices in self._path_attachment_bones.get(c.target_slot, []):
            if not indices:
                self._sort_bone(c.target_bone)
            for i in indices:
                self._sort_bone(self.ordered[i])
        for bone in c.bones:
            self._sort_bone(bone)
        self._cache.append(c)
        for bone in c.bones:
            self._sort_reset(bone.children)
        for bone in c.bones:
            bone.sorted = True

    def _sort_bone(self, bone: SolverBone) -> None:
        if bone.sorted:
            return
        if bone.parent is not None:
            self._sort_bone(bone.parent)
        bone.sorted = True
        self._cache.append(bone)

    def _sort_reset(self, bones: list[SolverBone]) -> None:
        for bone in bones:
            if not bone.active:
                continue
            if bone.sorted:
                self._sort_reset(bone.children)
            bone.sorted = False

    def active_bones(self) -> set[str]:
        return {b.name for b in self.ordered if b.active}

    def solve(self, locals_: dict[str, tuple], ik_state: dict[str, dict[str, Any]] | None = None, *,
              transform_state: dict[str, dict[str, float]] | None = None,
              path_state: dict[str, dict[str, float]] | None = None,
              path_targets: dict[str, tuple[dict[str, Any] | None, list[float] | None]] | None = None
              ) -> dict[str, tuple]:
        """locals_: name -> (x, y, rotation, scaleX, scaleY, shearX, shearY).
        ik_state: name -> {mix, softness, bend, compress, stretch}; transform_state: name -> mix_* values;
        path_state: name -> {position, spacing, mix_*} (posed values; missing ones use setup values).
        path_targets: path constraint target slot -> (shown attachment, its deform offsets).
        Returns name -> (a, b, c, d, worldX, worldY) in the runtime layout."""
        for name, bone in self.bones.items():
            (bone.x, bone.y, bone.rotation, bone.scale_x, bone.scale_y,
             bone.shear_x, bone.shear_y) = locals_[name]
            bone.ax, bone.ay, bone.arotation = bone.x, bone.y, bone.rotation
            bone.ascale_x, bone.ascale_y = bone.scale_x, bone.scale_y
            bone.ashear_x, bone.ashear_y = bone.shear_x, bone.shear_y
        for c in self.iks:
            for key, value in {**self._ik_setup[c.name], **(ik_state or {}).get(c.name, {})}.items():
                setattr(c, key, value)
        for c in self.transforms:
            for key, value in {**self._transform_setup[c.name], **(transform_state or {}).get(c.name, {})}.items():
                setattr(c, key, value)
        for c in self.paths:
            for key, value in {**self._path_setup[c.name], **(path_state or {}).get(c.name, {})}.items():
                setattr(c, key, value)
            c.attachment, c.deform = (path_targets or {}).get(c.target_slot, (None, None))
        for item in self._cache:
            item.update()
        return {n: (b.a, b.b, b.c, b.d, b.world_x, b.world_y) for n, b in self.bones.items()}
