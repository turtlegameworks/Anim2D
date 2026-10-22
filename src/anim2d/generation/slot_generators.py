"""Generators for slot-game style effects: wins, reward items and character reveals.

Same contract as generators.py: key times are normalized (0 = activation, 1 = end), every slot
starts hidden and stays attached until u = 1 (faded out by then), so the effect always ends at its
duration whichever parts are turned off.
"""

from __future__ import annotations

import math
import random

from ..models.effect import EASE_IN, EASE_OUT
from .builder import EffectBuilder
from .more_generators import _flight, _size, _span


def _turn(b: EffectBuilder, bone: str, u0: float, u1: float, degrees: float) -> None:
    """Rotate keys from 0 to `degrees` between u0 and u1, under 180° apart so the runtime turns that way."""
    steps = max(1, math.ceil(abs(degrees) / 120))
    for k in range(steps + 1):
        b.rotate(bone, u0 + (u1 - u0) * k / steps, degrees * k / steps)


def _twinkles(b: EffectBuilder, rng: random.Random, name: str, count: int, spot, size: tuple[float, float],
              when: tuple[float, float], image: str = "sparkle", tint: str = "white") -> None:
    """Sparkles that pop, turn and fade at spots from spot(rng) -> (x, y), starting within `when`."""
    for i in range(count):
        x, y = spot(rng)
        start = rng.uniform(*when)
        lu = _span(start, min(1.0, start + rng.uniform(0.2, 0.35)))
        sc = _size(b, image, rng.uniform(*size))
        bone = b.bone(f"{name}_{i + 1:02d}", x=x, y=y, rotation=rng.uniform(0, 45))
        slot = b.slot(f"{name}_{i + 1:02d}", bone, image, tint=tint, blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.4), 1.0)
        b.scale(bone, lu(1), 0.0)
        b.rotate(bone, lu(0), 0.0)
        b.rotate(bone, lu(1), 90)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint=tint)


# --- wins and reward items -----------------------------------------------------------------------

def coin_rain(b: EffectBuilder, rng: random.Random) -> None:
    """Big win: coins pour down from above across an area, flipping and turning, with twinkles."""
    s = b.settings
    count = int(s["count"])
    width = float(s["area_width"])
    drop = float(s["drop_height"])
    shower = float(s["shower_time"])
    size = float(s["coin_size"])
    for i in range(count):
        start = shower * rng.uniform(0, 1)
        lu = _span(start, min(1.0, start + rng.uniform(0.35, 0.55)))
        x = width * rng.uniform(-0.5, 0.5)
        sc = _size(b, "coin", size * rng.uniform(0.75, 1.2))
        bone = b.bone(f"coin_{i + 1:02d}", x=x, y=drop / 2, rotation=rng.uniform(-30, 30))
        slot = b.slot(f"coin_{i + 1:02d}", bone, "coin", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        _flight(b, bone, lu, rng.uniform(-0.1, 0.1) * width, -drop * 0.25, drop * 0.85, 4)  # falls faster and faster
        flips = rng.randint(2, 4)
        for k in range(flips * 2 + 1):
            b.scale(bone, lu(k / (flips * 2)), 1.0 if k % 2 == 0 else 0.15, 1.0)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.8), 1.0, EASE_IN), (lu(1), 0.0)])
    _twinkles(b, rng, "twinkle", int(s["sparkles"]),
              lambda r: (width * r.uniform(-0.5, 0.5), drop * r.uniform(-0.4, 0.5)), (20, 44), (0.05, 0.75))


def rotating_rays(b: EffectBuilder, rng: random.Random) -> None:
    """Win rays: soft light rays fan out behind an item and turn slowly, over a glow."""
    s = b.settings
    count = int(s["ray_count"])
    length = float(s["ray_length"])
    width = float(s["ray_width"])
    hold = float(s["hold"])
    if float(s["glow_size"]) > 0:
        bone = b.bone("glow")
        g = _size(b, "glow", float(s["glow_size"]))
        slot = b.slot("glow", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.3, curve=EASE_OUT)
        b.scale(bone, 0.2, 1.0)
        b.alpha(slot, [(0.0, 0.0), (0.15, 1.0), (hold, 1.0, EASE_IN), (1.0, 0.0)], tint="secondary")
    spinner = b.bone("spinner")
    _turn(b, spinner, 0.0, 1.0, float(s["spin"]))
    b.scale(spinner, 0.0, 0.2, curve=EASE_OUT)
    b.scale(spinner, 0.25, 1.0)
    b.scale(spinner, 1.0, 1.1)
    rw, rh = b.image_size("ray")
    for i in range(count):
        angle = 360.0 * i / count
        bone = b.bone(f"ray_{i + 1:02d}", parent=spinner, rotation=angle)
        tint = "primary" if i % 2 == 0 else "secondary"
        slot = b.slot(f"ray_{i + 1:02d}", bone, "ray", tint=tint, blend="additive", x=length / 2,
                      scale_x=length / rw, scale_y=width / rh)
        b.show(slot, 0.0, 1.0)
        b.alpha(slot, [(0.0, 0.0), (0.2, 1.0), (hold, 1.0, EASE_IN), (1.0, 0.0)], tint=tint)


def pulse_glow(b: EffectBuilder, rng: random.Random) -> None:
    """Symbol highlight: a glow behind an item pulses, each pulse sends out a ring, sparkles circle it."""
    s = b.settings
    pulses = int(s["pulses"])
    glow = float(s["glow_size"])
    ring_size = float(s["ring_size"])
    orbit = float(s["orbit_radius"])
    bone = b.bone("glow")
    g = _size(b, "glow", glow)
    slot = b.slot("glow", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g)
    b.show(slot, 0.0, 1.0)
    keys = [(0.0, 0.0)]
    for p in range(pulses):
        a, m = p / pulses, (p + 0.35) / pulses
        b.scale(bone, a, 0.85, curve=EASE_OUT)
        b.scale(bone, m, 1.1, curve=EASE_IN)
        keys += [(m, 1.0), ((p + 1) / pulses, 0.45)]
    b.scale(bone, 1.0, 0.9)
    keys[-1] = (1.0, 0.0)
    b.alpha(slot, keys, tint="secondary")
    r = _size(b, "ring", 1.0)
    for p in range(pulses):
        lu = _span(p / pulses, min(1.0, (p + 1.4) / pulses))
        bone = b.bone(f"ring_{p + 1:02d}")
        slot = b.slot(f"ring_{p + 1:02d}", bone, "ring", blend="additive", scale_x=r, scale_y=r)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), ring_size * 0.6, curve=EASE_OUT)
        b.scale(bone, lu(1), ring_size * 1.3)
        b.alpha(slot, [(lu(0), 0.9), (lu(1), 0.0)])
    count = int(s["sparkles"])
    if count:
        orbiter = b.bone("orbit")
        _turn(b, orbiter, 0.0, 1.0, -float(s["orbit_spin"]))
        for i in range(count):
            a = math.tau * i / count
            sc = _size(b, "sparkle", rng.uniform(22, 36))
            bone = b.bone(f"sparkle_{i + 1:02d}", parent=orbiter, x=orbit * math.cos(a), y=orbit * math.sin(a))
            slot = b.slot(f"sparkle_{i + 1:02d}", bone, "sparkle", tint="white", blend="additive",
                          scale_x=sc, scale_y=sc)
            b.show(slot, 0.0, 1.0)
            phase = rng.uniform(0, 1)
            b.alpha(slot, [(0.0, 0.0), (0.15, 1.0), (0.3 + 0.3 * phase, 0.4), (0.65 + 0.1 * phase, 1.0),
                           (1.0, 0.0)], tint="white")


def shine_sweep(b: EffectBuilder, rng: random.Random) -> None:
    """Shine: a slanted streak of light sweeps across an item, then a sparkle twinkles on it."""
    s = b.settings
    w, h = float(s["item_width"]), float(s["item_height"])
    sweeps = int(s["sweeps"])
    angle = float(s["angle"])
    sw, sh = b.image_size("shine")
    for k in range(sweeps):
        lu = _span(k / sweeps * 0.8, min(1.0, k / sweeps * 0.8 + 0.55))
        bone = b.bone(f"shine_{k + 1:02d}", rotation=angle)
        slot = b.slot(f"shine_{k + 1:02d}", bone, "shine", tint="white", blend="additive",
                      scale_x=float(s["streak_width"]) / sw, scale_y=math.hypot(w, h) / sh)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), -w * 0.6, 0.0, curve=(0.4, 0.0, 0.6, 1.0))
        b.translate(bone, lu(1), w * 0.6, 0.0)
        b.alpha(slot, [(lu(0), 0.0), (lu(0.2), 1.0), (lu(0.8), 1.0), (lu(1), 0.0)], tint="white")
    if float(s["sparkle_size"]) > 0:
        bone = b.bone("sparkle", x=w * 0.3, y=h * 0.3)
        sc = _size(b, "sparkle", float(s["sparkle_size"]))
        slot = b.slot("sparkle", bone, "sparkle", tint="primary", blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, 0.55, 1.0)
        b.scale(bone, 0.55, 0.0, curve=EASE_OUT)
        b.scale(bone, 0.75, 1.0)
        b.scale(bone, 1.0, 0.0)
        b.rotate(bone, 0.55, 0.0)
        b.rotate(bone, 1.0, 120)
        b.alpha(slot, [(0.55, 1.0), (1.0, 0.2)])


def confetti(b: EffectBuilder, rng: random.Random) -> None:
    """Confetti: paper strips burst up in a fan, flutter, drift and fall, in three colours."""
    s = b.settings
    count = int(s["count"])
    spread = float(s["spread"])
    speed = float(s["speed"])
    gravity = float(s["gravity"])
    size = float(s["piece_size"])
    drift = float(s["drift"])
    tints = ("primary", "secondary", "white")
    for i in range(count):
        angle = math.radians(90 + rng.uniform(-spread / 2, spread / 2))
        v = speed * rng.uniform(0.55, 1.0)
        lu = _span(0.08 * rng.uniform(0, 1), 1.0)
        tint = tints[i % 3]
        sc = _size(b, "confetti", size * rng.uniform(0.7, 1.2))
        bone = b.bone(f"piece_{i + 1:02d}", rotation=rng.uniform(0, 180))
        slot = b.slot(f"piece_{i + 1:02d}", bone, "confetti", tint=tint, scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        vx = v * math.cos(angle) + drift * rng.uniform(-1, 1)
        _flight(b, bone, lu, vx, v * math.sin(angle), gravity, 8)
        _turn(b, bone, lu(0), lu(1), rng.choice((-1, 1)) * rng.uniform(180, 720))
        flutters = rng.randint(3, 6)  # the strip turns over: its height swings between full and thin
        for k in range(flutters * 2 + 1):
            b.scale(bone, lu(k / (flutters * 2)), 1.0, 1.0 if k % 2 == 0 else 0.15)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.75), 1.0, EASE_IN), (1.0, 0.0)], tint=tint)


def collect_trail(b: EffectBuilder, rng: random.Random) -> None:
    """Collect: a star flies along an arc to a target (a counter or meter), leaving sparkles, and pops there."""
    s = b.settings
    tx, ty = float(s["target_x"]), float(s["target_y"])
    arc = float(s["arc_height"])
    travel = float(s["travel_portion"])
    cx, cy = tx / 2, ty / 2 + arc  # the curve's control point

    def at(f: float) -> tuple[float, float]:
        return ((1 - f) ** 2 * 0 + 2 * (1 - f) * f * cx + f * f * tx,
                (1 - f) ** 2 * 0 + 2 * (1 - f) * f * cy + f * f * ty)

    for i in range(int(s["trail"])):  # trail sparkles appear where the star passes, then fade
        f = (i + 1) / (int(s["trail"]) + 1)
        x, y = at(f)
        u0 = travel * f
        lu = _span(u0, min(1.0, u0 + 0.3))
        sc = _size(b, "sparkle", rng.uniform(16, 30))
        bone = b.bone(f"trail_{i + 1:02d}", x=x + rng.uniform(-8, 8), y=y + rng.uniform(-8, 8))
        slot = b.slot(f"trail_{i + 1:02d}", bone, "sparkle", tint="secondary", blend="additive", scale_x=sc,
                      scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), 1.0)
        b.scale(bone, lu(1), 0.0)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint="secondary")
    bone = b.bone("star")
    sc = _size(b, "star", float(s["star_size"]))
    slot = b.slot("star", bone, "star", scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    steps = 8
    for k in range(steps + 1):
        f = k / steps
        x, y = at(f)
        b.translate(bone, travel * f, x, y, curve=None)
    b.scale(bone, 0.0, 0.0, curve=EASE_OUT)
    b.scale(bone, travel * 0.15, 1.2)
    b.scale(bone, travel, 0.7, curve=EASE_OUT)
    b.scale(bone, 1.0, 0.0)
    b.rotate(bone, 0.0, 0.0)
    b.rotate(bone, travel, -100)
    b.alpha(slot, [(0.0, 1.0), (travel, 1.0, EASE_IN), (1.0, 0.0)])
    if float(s["pop_size"]) > 0:
        bone = b.bone("pop", x=tx, y=ty)
        g = _size(b, "glow", float(s["pop_size"]))
        slot = b.slot("pop", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g)
        b.show(slot, travel, 1.0)
        b.scale(bone, travel, 0.3, curve=EASE_OUT)
        b.scale(bone, 1.0, 1.2)
        b.alpha(slot, [(travel, 1.0), (1.0, 0.0)], tint="secondary")


# --- character reveals ----------------------------------------------------------------------------

def poof_reveal(b: EffectBuilder, rng: random.Random) -> None:
    """Poof: smoke bursts out and thins away to reveal what's behind, with a flash and sparkles."""
    s = b.settings
    size = float(s["size"])
    puffs = int(s["puffs"])
    for i in range(puffs):
        a = math.tau * i / max(1, puffs) + rng.uniform(-0.25, 0.25)
        d = size * rng.uniform(0.25, 0.55)
        lu = _span(0.0, rng.uniform(0.7, 1.0))
        sc = _size(b, "smoke", size * rng.uniform(0.45, 0.65))
        bone = b.bone(f"puff_{i + 1:02d}", x=d * 0.3 * math.cos(a), y=d * 0.3 * math.sin(a),
                      rotation=rng.uniform(0, 360))
        slot = b.slot(f"puff_{i + 1:02d}", bone, "smoke", tint="primary", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(1), d * math.cos(a), d * math.sin(a))
        b.scale(bone, lu(0), 0.4, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0)
        b.scale(bone, lu(1), 1.3)
        b.rotate(bone, lu(0), 0.0)
        b.rotate(bone, lu(1), rng.uniform(-40, 40))
        b.alpha(slot, [(lu(0), 1.0), (lu(0.35), 1.0, EASE_IN), (lu(1), 0.0)])
    if float(s["flash_size"]) > 0:
        bone = b.bone("flash")
        sc = _size(b, "flash", float(s["flash_size"]))
        slot = b.slot("flash", bone, "flash", tint="secondary", blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.2, curve=EASE_OUT)
        b.scale(bone, 0.25, 1.0)
        b.alpha(slot, [(0.0, 1.0), (0.1, 1.0, EASE_IN), (0.3, 0.0)], tint="secondary")
    _twinkles(b, rng, "sparkle", int(s["sparkles"]),
              lambda r: (size * 0.6 * r.uniform(-1, 1), size * 0.6 * r.uniform(-1, 1)), (18, 40), (0.15, 0.65),
              tint="secondary")


def light_reveal(b: EffectBuilder, rng: random.Random) -> None:
    """Light reveal: rays burst out and turn, a big flash whites out the centre, a ring spreads, sparkles."""
    s = b.settings
    size = float(s["size"])
    count = int(s["ray_count"])
    spinner = b.bone("spinner")
    _turn(b, spinner, 0.0, 1.0, 60)
    b.scale(spinner, 0.0, 0.1, curve=EASE_OUT)
    b.scale(spinner, 0.3, 1.0)
    b.scale(spinner, 1.0, 1.2)
    rw, rh = b.image_size("ray")
    for i in range(count):
        tint = "primary" if i % 2 == 0 else "secondary"
        bone = b.bone(f"ray_{i + 1:02d}", parent=spinner, rotation=360.0 * i / count + rng.uniform(-6, 6))
        length = size * rng.uniform(0.8, 1.1)
        slot = b.slot(f"ray_{i + 1:02d}", bone, "ray", tint=tint, blend="additive", x=length / 2,
                      scale_x=length / rw, scale_y=size * 0.35 / rh)
        b.show(slot, 0.0, 1.0)
        b.alpha(slot, [(0.0, 0.0), (0.12, 1.0), (0.55, 0.8, EASE_IN), (1.0, 0.0)], tint=tint)
    bone = b.bone("ring")
    r = _size(b, "ring", 1.0)
    slot = b.slot("ring", bone, "ring", tint="secondary", blend="additive", scale_x=r, scale_y=r)
    b.show(slot, 0.05, 1.0)
    b.scale(bone, 0.05, size * 0.3, curve=EASE_OUT)
    b.scale(bone, 0.6, size * 2.0)
    b.alpha(slot, [(0.05, 1.0), (0.6, 0.0)], tint="secondary")
    bone = b.bone("flash")
    sc = _size(b, "flash", size * 1.2)
    slot = b.slot("flash", bone, "flash", tint="white", blend="additive", scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, 0.2, curve=EASE_OUT)
    b.scale(bone, 0.2, 1.0)
    b.scale(bone, 0.5, 1.3)
    b.alpha(slot, [(0.0, 1.0), (0.18, 1.0, EASE_IN), (0.5, 0.0)], tint="white")
    _twinkles(b, rng, "sparkle", int(s["sparkles"]),
              lambda r: (size * 0.8 * r.uniform(-1, 1), size * 0.8 * r.uniform(-1, 1)), (20, 46), (0.15, 0.7))
