"""More built-in generators: slash, shockwave, sparkles, fountain, heal, explosion, level up, magic
circle and lightning. Same contract as generators.py: key times are normalized (0 = activation,
1 = end), every slot starts hidden and is hidden again by u = 1.
"""

from __future__ import annotations

import math
import random

from ..models.effect import EASE_IN, EASE_OUT
from .builder import EffectBuilder


# --- helpers ---------------------------------------------------------------------------------------

# Every slot stays attached until u = 1 (faded out by then), so the effect always ends at its duration
# whichever parts are turned off.

def _span(start: float, end: float):
    """Map a part's own 0..1 time into the effect's u between start and end."""
    return lambda f: start + f * (end - start)


def _size(b: EffectBuilder, image_id: str, px: float) -> float:
    """Scale that makes the image px pixels wide."""
    return px / b.image_size(image_id)[0]


def _flight(b: EffectBuilder, bone: str, lu, vx: float, vy: float, gravity: float, steps: int = 6) -> None:
    """Translate keys along a thrown path: x = vx f, y = vy f - gravity f², with f the part's time 0..1."""
    for i in range(steps + 1):
        f = i / steps
        b.translate(bone, lu(f), vx * f, vy * f - gravity * f * f)


# --- slash, rings, sparkles --------------------------------------------------------------------------

def slash_arc(b: EffectBuilder, rng: random.Random) -> None:
    """Sword slash: a crescent sweeps through an arc and fades, with fainter trails behind it."""
    s = b.settings
    sweep = float(s["sweep"])
    thickness = float(s["thickness"])
    trails = int(s["trails"])
    sweep_portion = float(s["sweep_portion"])
    scale = _size(b, "slash", float(s["size"]))
    for i in reversed(range(trails + 1)):  # the main arc (i = 0) is created last so it draws on top
        tint = "primary" if i == 0 else "secondary"
        lu = _span(0.07 * i, 1.0)
        bone = b.bone(f"arc_{i + 1:02d}", rotation=-sweep / 2)
        slot = b.slot(f"arc_{i + 1:02d}", bone, "slash", tint=tint, blend="additive",
                      scale_x=scale * thickness, scale_y=scale)
        b.show(slot, lu(0), 1.0)
        b.rotate(bone, lu(0), 0.0, curve=EASE_OUT)
        b.rotate(bone, lu(sweep_portion), sweep)
        b.scale(bone, lu(0), 0.6, 0.85, curve=EASE_OUT)
        b.scale(bone, lu(sweep_portion), 1.0, 1.0)
        b.scale(bone, 1.0, 1.15, 1.05)
        peak = 1.0 if i == 0 else 0.55 / i
        b.alpha(slot, [(lu(0), peak), (lu(sweep_portion), peak, EASE_IN), (1.0, 0.0)], tint=tint)


def ring_wave(b: EffectBuilder, rng: random.Random) -> None:
    """Shockwave: rings expand from the centre and fade, after a quick glow."""
    s = b.settings
    rings = int(s["rings"])
    start, end = float(s["start_size"]), float(s["end_size"])
    squash = float(s["squash"])
    gap = float(s["ring_gap"])
    if float(s["glow_size"]) > 0:
        bone = b.bone("glow")
        g = _size(b, "glow", float(s["glow_size"]))
        slot = b.slot("glow", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g * squash)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.5, curve=EASE_OUT)
        b.scale(bone, 0.5, 1.2)
        b.alpha(slot, [(0.0, 1.0), (0.15, 1.0, EASE_IN), (0.5, 0.0)], tint="secondary")
    r = _size(b, "ring", 1.0)  # ring scale keys are then the ring's width in pixels
    for i in range(rings):
        lu = _span(gap * i, 1.0)
        bone = b.bone(f"ring_{i + 1:02d}")
        slot = b.slot(f"ring_{i + 1:02d}", bone, "ring", blend="additive", scale_x=r, scale_y=r * squash)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), start, curve=EASE_OUT)
        b.scale(bone, 1.0, end)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.35), 0.9, EASE_IN), (1.0, 0.0)])


def scatter_pop(b: EffectBuilder, rng: random.Random) -> None:
    """Sparkles: stars pop up at random spots in a circle, turn a little and twinkle out."""
    s = b.settings
    count = int(s["count"])
    radius = float(s["radius"])
    size = float(s["star_size"])
    variance = float(s["size_variance"])
    spread_time = float(s["spread_time"])
    for i in range(count):
        a = rng.uniform(0, math.tau)
        d = radius * math.sqrt(rng.uniform(0, 1))
        start = spread_time * rng.uniform(0, 1)
        lu = _span(start, start + rng.uniform(0.45, 0.75) * (1 - start))
        sc = _size(b, "sparkle", size * (1 + variance * rng.uniform(-1, 1)))
        tint = "primary" if i % 3 else "secondary"
        bone = b.bone(f"star_{i + 1:02d}", x=d * math.cos(a), y=d * math.sin(a), rotation=rng.uniform(0, 45))
        slot = b.slot(f"star_{i + 1:02d}", bone, "sparkle", tint=tint, blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)  # faded out by lu(1); hidden at the end of the effect like every slot
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.35), 1.0)
        b.scale(bone, lu(1), 0.0)
        b.rotate(bone, lu(0), 0.0)
        b.rotate(bone, lu(1), rng.choice((-1, 1)) * 90)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.5), 0.6), (lu(0.7), 1.0), (lu(1), 0.0)], tint=tint)


# --- thrown and rising pieces ------------------------------------------------------------------------

def particle_spray(b: EffectBuilder, rng: random.Random) -> None:
    """Coin fountain: coins are thrown up in a fan, flip, fall back under gravity and fade."""
    s = b.settings
    count = int(s["count"])
    spread = float(s["spread"])
    speed = float(s["speed"])
    gravity = float(s["gravity"])
    size = float(s["piece_size"])
    for i in range(count):
        angle = math.radians(90 + rng.uniform(-spread / 2, spread / 2))
        v = speed * rng.uniform(0.6, 1.0)
        lu = _span(0.15 * rng.uniform(0, 1), 1.0)
        tint = "primary" if rng.random() < 0.75 else "secondary"
        sc = _size(b, "dot", size * rng.uniform(0.7, 1.2))
        bone = b.bone(f"piece_{i + 1:02d}")
        slot = b.slot(f"piece_{i + 1:02d}", bone, "dot", tint=tint, scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        _flight(b, bone, lu, v * math.cos(angle), v * math.sin(angle), gravity)
        flips = rng.randint(2, 4)  # the coin turns over: its width swings between full and thin
        for k in range(flips * 2 + 1):
            b.scale(bone, lu(k / (flips * 2)), 1.0 if k % 2 == 0 else 0.2, 1.0)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.7), 1.0, EASE_IN), (1.0, 0.0)], tint=tint)
    if float(s["sparkle_size"]) > 0:
        bone = b.bone("sparkle")
        sc = _size(b, "sparkle", float(s["sparkle_size"]))
        slot = b.slot("sparkle", bone, "sparkle", tint="secondary", blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.2, curve=EASE_OUT)
        b.scale(bone, 0.35, 1.3)
        b.alpha(slot, [(0.0, 1.0), (0.12, 1.0, EASE_IN), (0.35, 0.0)], tint="secondary")


def rising_particles(b: EffectBuilder, rng: random.Random) -> None:
    """Heal: a soft glow at the feet while plus signs float up, sway and fade."""
    s = b.settings
    count = int(s["count"])
    width = float(s["area_width"])
    rise = float(s["rise"])
    size = float(s["symbol_size"])
    sway = float(s["sway"])
    if float(s["glow_size"]) > 0:
        bone = b.bone("glow")
        g = _size(b, "glow", float(s["glow_size"]))
        slot = b.slot("glow", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g * 0.45)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.3, curve=EASE_OUT)
        b.scale(bone, 0.25, 1.0)
        b.scale(bone, 1.0, 1.1)
        b.alpha(slot, [(0.0, 0.0), (0.2, 1.0), (0.7, 0.8, EASE_IN), (1.0, 0.0)], tint="secondary")
    for i in range(count):
        start = 0.5 * rng.uniform(0, 1)
        lu = _span(start, min(1.0, start + rng.uniform(0.45, 0.6)))
        sc = _size(b, "plus", size * rng.uniform(0.6, 1.1))
        bone = b.bone(f"symbol_{i + 1:02d}", x=width * rng.uniform(-0.5, 0.5))
        slot = b.slot(f"symbol_{i + 1:02d}", bone, "plus", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        side = rng.choice((-1, 1)) * sway
        height = rise * rng.uniform(0.7, 1.0)
        for k in range(5):
            f = k / 4
            b.translate(bone, lu(f), side * math.sin(f * math.pi * 1.5), height * f)
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.25), 1.0)
        b.scale(bone, lu(1), 0.6)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.6), 1.0, EASE_IN), (lu(1), 0.0)])


# --- bigger compositions -----------------------------------------------------------------------------

def explosion(b: EffectBuilder, rng: random.Random) -> None:
    """Explosion: smoke puffs billow out, debris flies, and a bright flash on top."""
    s = b.settings
    puffs = int(s["smoke_puffs"])
    radius = float(s["blast_radius"])
    smoke_size = float(s["smoke_size"])
    for i in range(puffs):
        a = math.tau * i / max(1, puffs) + rng.uniform(-0.3, 0.3)
        d = radius * rng.uniform(0.1, 1.0)  # some stay near the centre, so the cloud isn't a hollow ring
        lu = _span(0.05 * rng.uniform(0, 1), 1.0)
        sc = _size(b, "smoke", smoke_size * rng.uniform(0.8, 1.3) * (1.3 - 0.5 * d / radius))
        bone = b.bone(f"smoke_{i + 1:02d}", rotation=rng.uniform(0, 360))
        slot = b.slot(f"smoke_{i + 1:02d}", bone, "smoke", tint="secondary", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, 1.0, d * math.cos(a), d * math.sin(a) + radius * 0.25)
        b.scale(bone, lu(0), 0.3, curve=EASE_OUT)
        b.scale(bone, 1.0, 1.4)
        b.alpha(slot, [(lu(0), 0.9), (lu(0.4), 0.7, EASE_IN), (1.0, 0.0)], tint="secondary")
    for i in range(int(s["debris"])):
        angle = rng.uniform(0, math.tau)
        v = radius * rng.uniform(1.0, 1.8)
        lu = _span(0.0, rng.uniform(0.6, 0.9))
        sc = _size(b, "debris", rng.uniform(8, 18))
        bone = b.bone(f"debris_{i + 1:02d}")
        slot = b.slot(f"debris_{i + 1:02d}", bone, "debris", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        _flight(b, bone, lu, v * math.cos(angle), v * abs(math.sin(angle)) + radius * 0.4, radius * 1.2, 5)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.6), 1.0, EASE_IN), (lu(1), 0.0)])
    if float(s["flash_size"]) > 0:
        bone = b.bone("flash")
        sc = _size(b, "flash", float(s["flash_size"]))
        slot = b.slot("flash", bone, "flash", blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.3, curve=EASE_OUT)
        b.scale(bone, 0.3, 1.2)
        b.alpha(slot, [(0.0, 1.0), (0.1, 1.0, EASE_IN), (0.3, 0.0)])


def pillar_burst(b: EffectBuilder, rng: random.Random) -> None:
    """Level up: a pillar of light shoots up, a ring spreads at its base, sparkles rise and twinkle."""
    s = b.settings
    height = float(s["pillar_height"])
    width = float(s["pillar_width"])
    bw, bh = b.image_size("beam")
    bone = b.bone("pillar")
    slot = b.slot("pillar", bone, "beam", tint="secondary", blend="additive", y=height / 2,
                  scale_x=width / bw, scale_y=height / bh)
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, 0.2, 0.2, curve=EASE_OUT)
    b.scale(bone, 0.2, 1.0, 1.0)
    b.scale(bone, 0.7, 0.8, 1.05, curve=EASE_IN)
    b.scale(bone, 1.0, 0.0, 1.1)
    b.alpha(slot, [(0.0, 1.0), (0.6, 1.0, EASE_IN), (1.0, 0.0)], tint="secondary")
    bone = b.bone("ring")
    r = _size(b, "ring", 1.0)
    slot = b.slot("ring", bone, "ring", blend="additive", scale_x=r, scale_y=r * 0.35)
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, width, curve=EASE_OUT)
    b.scale(bone, 0.7, width * 3.2)
    b.alpha(slot, [(0.0, 1.0), (0.3, 1.0, EASE_IN), (0.7, 0.0)])
    for i in range(int(s["sparkles"])):
        start = 0.1 + 0.5 * rng.uniform(0, 1)
        lu = _span(start, min(1.0, start + 0.4))
        sc = _size(b, "sparkle", rng.uniform(20, 38))
        bone = b.bone(f"sparkle_{i + 1:02d}", x=width * 0.9 * rng.uniform(-1, 1), y=height * rng.uniform(0.05, 0.3))
        slot = b.slot(f"sparkle_{i + 1:02d}", bone, "sparkle", blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0)
        b.translate(bone, lu(1), 0.0, height * rng.uniform(0.35, 0.6))
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0)
        b.scale(bone, lu(1), 0.0)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.3)])


def spin_glow(b: EffectBuilder, rng: random.Random) -> None:
    """Magic circle: a rune circle grows in and turns, an inner copy turns the other way, then both fade."""
    s = b.settings
    spin = float(s["spin"])
    squash = float(s["squash"])
    hold = float(s["hold"])
    sc = _size(b, "rune", float(s["circle_size"]))
    if float(s["glow_size"]) > 0:
        bone = b.bone("glow")
        g = _size(b, "glow", float(s["glow_size"]))
        slot = b.slot("glow", bone, "glow", tint="secondary", blend="additive", scale_x=g, scale_y=g * squash)
        b.show(slot, 0.0, 1.0)
        b.alpha(slot, [(0.0, 0.0), (0.25, 0.9), (0.5, 0.6), (hold, 0.9, EASE_IN), (1.0, 0.0)], tint="secondary")
    tilt = b.bone("tilt", scale_y=squash)  # squashing a parent keeps the circles turning on the ground plane
    for name, factor, direction, tint in (("outer", 1.0, 1, "primary"), ("inner", 0.55, -1, "secondary")):
        bone = b.bone(name, parent=tilt)
        slot = b.slot(name, bone, "rune", tint=tint, blend="additive", scale_x=sc * factor, scale_y=sc * factor)
        b.show(slot, 0.0, 1.0)
        b.scale(bone, 0.0, 0.0, curve=EASE_OUT)
        b.scale(bone, 0.25, 1.0)
        b.scale(bone, hold, 1.0, curve=EASE_IN)
        b.scale(bone, 1.0, 1.15)
        steps = max(1, math.ceil(abs(spin) / 120))  # keys under 180° apart so the turn goes the intended way
        for k in range(steps + 1):
            b.rotate(bone, k / steps, direction * spin * k / steps)
        b.alpha(slot, [(0.0, 0.0), (0.2, 1.0), (hold, 1.0, EASE_IN), (1.0, 0.0)], tint=tint)


def flicker_bolt(b: EffectBuilder, rng: random.Random) -> None:
    """Lightning: a bolt flickers between two shapes from above, then flashes where it hits."""
    s = b.settings
    height = float(s["bolt_height"])
    width = float(s["bolt_width"])
    flickers = int(s["flickers"])
    strike = float(s["strike_portion"])
    bw, bh = b.image_size("bolt")
    for i in range(2):  # two bolts, the second mirrored, take turns on each flicker
        mirror = -1.0 if i else 1.0
        bone = b.bone(f"bolt_{i + 1:02d}", scale_x=mirror)
        slot = b.slot(f"bolt_{i + 1:02d}", bone, "bolt", blend="additive", y=height / 2,
                      scale_x=width / bw * (1 + 0.25 * i), scale_y=height / bh)
        keys = [(strike * k / flickers, 1.0 if (k + i) % 2 == 0 else 0.0, "stepped") for k in range(flickers)]
        keys += [(strike, 1.0 if i == 0 else 0.0), (1.0, 0.0)]
        b.show(slot, 0.0, 1.0)
        b.alpha(slot, keys)
        b.scale(bone, 0.0, 1.0, 0.9)  # scale keys multiply the setup scale, so the mirror stays
        b.scale(bone, strike, 1.0, 1.0)
    bone = b.bone("impact")
    sc = _size(b, "flash", float(s["flash_size"]))
    slot = b.slot("impact", bone, "flash", tint="secondary", blend="additive", scale_x=sc, scale_y=sc * 0.6)
    b.show(slot, strike * 0.5, 1.0)
    b.scale(bone, strike * 0.5, 0.3, curve=EASE_OUT)
    b.scale(bone, 1.0, 1.3)
    b.alpha(slot, [(strike * 0.5, 1.0), (strike, 1.0, EASE_IN), (1.0, 0.0)], tint="secondary")
