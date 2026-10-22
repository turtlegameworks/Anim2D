"""Generators for casual-game effects: match-3 breaks, bubbles, hearts, star ratings, fireworks,
portals and splashes.

Same contract as generators.py: key times are normalized (0 = activation, 1 = end), every slot
starts hidden and stays attached until u = 1 (faded out by then).
"""

from __future__ import annotations

import math
import random

from ..models.effect import EASE_IN, EASE_OUT
from .builder import EffectBuilder
from .more_generators import _flight, _size, _span
from .slot_generators import _turn, _twinkles

BOUNCE = (0.3, 1.6, 0.6, 1.0)  # overshoots then settles


def _glow(b: EffectBuilder, name: str, image: str, size: float, keys, tint: str = "secondary", x: float = 0.0,
          y: float = 0.0, squash: float = 1.0) -> str:
    """A soft additive glow with an alpha envelope; returns its bone."""
    bone = b.bone(name, x=x, y=y)
    g = _size(b, image, size)
    slot = b.slot(name, bone, image, tint=tint, blend="additive", scale_x=g, scale_y=g * squash)
    b.show(slot, keys[0][0], 1.0)
    b.alpha(slot, keys, tint=tint)
    return bone


def shatter(b: EffectBuilder, rng: random.Random) -> None:
    """Gem shatter: a tile breaks into shards that burst out, spin and drop, with a flash and sparkles."""
    s = b.settings
    count = int(s["shards"])
    speed = float(s["burst"])
    gravity = float(s["gravity"])
    size = float(s["shard_size"])
    if float(s["flash_size"]) > 0:
        bone = _glow(b, "flash", "flash", float(s["flash_size"]), [(0.0, 1.0), (0.08, 1.0, EASE_IN), (0.3, 0.0)],
                     tint="white")
        b.scale(bone, 0.0, 0.4, curve=EASE_OUT)
        b.scale(bone, 0.3, 1.2)
    for i in range(count):
        angle = math.tau * i / count + rng.uniform(-0.35, 0.35)
        v = speed * rng.uniform(0.6, 1.0)
        lu = _span(0.0, rng.uniform(0.7, 1.0))
        tint = "primary" if i % 3 else "secondary"
        sc = _size(b, "shard", size * rng.uniform(0.6, 1.2))
        bone = b.bone(f"shard_{i + 1:02d}", rotation=rng.uniform(0, 360))
        slot = b.slot(f"shard_{i + 1:02d}", bone, "shard", tint=tint, scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        _flight(b, bone, lu, v * math.cos(angle), v * math.sin(angle) + speed * 0.3, gravity, 6)
        _turn(b, bone, lu(0), lu(1), rng.choice((-1, 1)) * rng.uniform(200, 500))
        b.scale(bone, lu(0), 1.0)
        b.scale(bone, lu(1), 0.4)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.6), 1.0, EASE_IN), (lu(1), 0.0)], tint=tint)
    _twinkles(b, rng, "sparkle", int(s["sparkles"]),
              lambda r: (speed * 0.5 * r.uniform(-1, 1), speed * 0.5 * r.uniform(-1, 1)), (16, 34), (0.05, 0.5))


def bubble_pop(b: EffectBuilder, rng: random.Random) -> None:
    """Bubble pop: a bubble wobbles, swells and bursts into a ring and droplets."""
    s = b.settings
    size = float(s["bubble_size"])
    pop = float(s["pop_time"])
    drops = int(s["droplets"])
    bone = b.bone("bubble")
    sc = _size(b, "bubble", size)
    slot = b.slot("bubble", bone, "bubble", scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    wobbles = 3
    for k in range(wobbles * 2):
        f = pop * 0.85 * k / (wobbles * 2)
        w = 0.06 * (1 + k / wobbles)
        b.scale(bone, f, 1 + (w if k % 2 else -w), 1 - (w if k % 2 else -w))
    b.scale(bone, pop * 0.85, 1.0, 1.0, curve=EASE_IN)
    b.scale(bone, pop, 1.25, 1.25)
    b.alpha(slot, [(0.0, 1.0), (pop * 0.97, 1.0, "stepped"), (pop, 0.0)])
    bone = b.bone("ring")
    r = _size(b, "ring", 1.0)
    slot = b.slot("ring", bone, "ring", tint="secondary", blend="additive", scale_x=r, scale_y=r)
    lu = _span(pop, min(1.0, pop + 0.35))
    b.show(slot, lu(0), 1.0)
    b.scale(bone, lu(0), size * 1.1, curve=EASE_OUT)
    b.scale(bone, lu(1), size * 1.7)
    b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint="secondary")
    for i in range(drops):
        a = math.tau * i / max(1, drops) + rng.uniform(-0.2, 0.2)
        d = size * rng.uniform(0.7, 1.1)
        lu = _span(pop, min(1.0, pop + rng.uniform(0.3, 0.5)))
        sc = _size(b, "droplet", rng.uniform(8, 16))
        bone = b.bone(f"droplet_{i + 1:02d}", x=size * 0.45 * math.cos(a), y=size * 0.45 * math.sin(a))
        slot = b.slot(f"droplet_{i + 1:02d}", bone, "droplet", tint="primary", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(1), d * 0.5 * math.cos(a), d * 0.5 * math.sin(a) - size * 0.15)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)])


def heart_burst(b: EffectBuilder, rng: random.Random) -> None:
    """Hearts: a soft glow, hearts pop out around the centre, then float up swaying and fade."""
    s = b.settings
    count = int(s["hearts"])
    radius = float(s["burst_radius"])
    rise = float(s["rise"])
    size = float(s["heart_size"])
    if float(s["glow_size"]) > 0:
        bone = _glow(b, "glow", "glow", float(s["glow_size"]), [(0.0, 1.0), (0.3, 0.8, EASE_IN), (0.7, 0.0)])
        b.scale(bone, 0.0, 0.3, curve=EASE_OUT)
        b.scale(bone, 0.3, 1.1)
    for i in range(count):
        a = math.tau * i / count + rng.uniform(-0.3, 0.3)
        d = radius * rng.uniform(0.5, 1.0)
        lu = _span(0.04 * i / max(1, count - 1), 1.0)
        tint = "primary" if i % 4 else "secondary"
        sc = _size(b, "heart", size * rng.uniform(0.6, 1.15))
        bone = b.bone(f"heart_{i + 1:02d}", rotation=rng.uniform(-20, 20))
        slot = b.slot(f"heart_{i + 1:02d}", bone, "heart", tint=tint, scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        x1, y1 = d * math.cos(a), d * math.sin(a)
        sway = rng.choice((-1, 1)) * size * 0.4
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(0.25), x1, y1)
        b.translate(bone, lu(0.5), x1 + sway, y1 + rise * 0.35)
        b.translate(bone, lu(0.75), x1 - sway, y1 + rise * 0.7)
        b.translate(bone, lu(1), x1, y1 + rise)
        b.scale(bone, lu(0), 0.0, curve=BOUNCE)
        b.scale(bone, lu(0.25), 1.0)
        b.scale(bone, lu(1), 0.7)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.65), 1.0, EASE_IN), (lu(1), 0.0)], tint=tint)


def star_rating(b: EffectBuilder, rng: random.Random) -> None:
    """Star rating: stars pop in one after another along an arc with a bounce, a glow and sparkles each."""
    s = b.settings
    count = int(s["stars"])
    spacing = float(s["spacing"])
    arc = float(s["arc"])
    size = float(s["star_size"])
    gap = float(s["gap"])
    hold = float(s["hold"])
    for i in range(count):
        offset = i - (count - 1) / 2
        x = spacing * offset
        y = -arc * (offset / max(1, (count - 1) / 2)) ** 2 + arc if count > 1 else 0.0
        big = 1.25 if count % 2 == 1 and offset == 0 else 1.0  # the middle star a bit bigger, as usual
        start = gap * i
        lu = _span(start, min(1.0, start + 0.25))
        _glow(b, f"glow_{i + 1:02d}", "glow", size * 2.2 * big, [(lu(0), 0.0), (lu(0.4), 1.0), (lu(1), 0.0)],
              x=x, y=y)
        bone = b.bone(f"star_{i + 1:02d}", x=x, y=y, rotation=offset * -8)
        sc = _size(b, "star", size * big)
        slot = b.slot(f"star_{i + 1:02d}", bone, "star", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), 2.2, curve=EASE_OUT)
        b.scale(bone, lu(0.5), 0.85, curve=EASE_OUT)
        b.scale(bone, lu(1), 1.0)
        b.rotate(bone, lu(0), -40.0, curve=EASE_OUT)
        b.rotate(bone, lu(0.6), 0.0)
        b.alpha(slot, [(lu(0), 0.0), (lu(0.3), 1.0), (hold, 1.0, EASE_IN), (1.0, 0.0)])
        _twinkles(b, rng, f"spark{i + 1}", int(s["sparkles"]),
                  lambda r, x=x, y=y: (x + size * 0.8 * r.uniform(-1, 1), y + size * 0.8 * r.uniform(-1, 1)),
                  (14, 28), (lu(0.3), lu(0.6)), tint="white")


def firework(b: EffectBuilder, rng: random.Random) -> None:
    """Firework: a rocket streaks up, bursts into a ring of sparks that droop and fade, with a flash."""
    s = b.settings
    height = float(s["height"])
    launch = float(s["launch_portion"])
    count = int(s["sparks"])
    radius = float(s["burst_radius"])
    sw, sh = b.image_size("streak")
    if launch > 0:
        bone = b.bone("rocket", rotation=90)
        slot = b.slot("rocket", bone, "streak", tint="secondary", blend="additive", x=-sw * 0.3,
                      scale_x=0.6, scale_y=0.6)
        b.show(slot, 0.0, 1.0)
        b.translate(bone, 0.0, 0.0, 0.0, curve=(0.2, 0.6, 0.5, 1.0))
        b.translate(bone, launch, 0.0, height)
        b.alpha(slot, [(0.0, 1.0), (launch, 1.0, "stepped"), (launch + 0.01, 0.0)], tint="secondary")
    burst = _span(launch, 1.0)
    bone = _glow(b, "flash", "glow", radius * 1.4, [(burst(0), 1.0), (burst(0.25), 0.0)], tint="white", y=height)
    b.scale(bone, burst(0), 0.3, curve=EASE_OUT)
    b.scale(bone, burst(0.25), 1.0)
    for i in range(count):
        a = math.tau * i / count + rng.uniform(-0.08, 0.08)
        v = radius * rng.uniform(0.85, 1.1)
        tint = "primary" if i % 2 == 0 else "secondary"
        lu = _span(burst(0), rng.uniform(burst(0.8), 1.0))
        sc = _size(b, "spark", rng.uniform(10, 16))
        bone = b.bone(f"spark_{i + 1:02d}", y=height)
        slot = b.slot(f"spark_{i + 1:02d}", bone, "spark", tint=tint, blend="additive", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        _flight(b, bone, lu, v * math.cos(a), v * math.sin(a), radius * 0.6, 6)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.5), 1.0), (lu(0.7), 0.4), (lu(0.8), 0.9), (lu(1), 0.0)], tint=tint)


def vortex(b: EffectBuilder, rng: random.Random) -> None:
    """Portal: a swirl spins up and grows, sparkles spiral into the centre, then it collapses with a pop."""
    s = b.settings
    size = float(s["size"])
    count = int(s["particles"])
    turns = float(s["turns"])
    close = float(s["close_time"])
    spinner = b.bone("swirl")
    sc = _size(b, "swirl", size)
    slot = b.slot("swirl", spinner, "swirl", blend="additive", scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    _turn(b, spinner, 0.0, 1.0, -360 * float(s["spin_turns"]))
    b.scale(spinner, 0.0, 0.0, curve=EASE_OUT)
    b.scale(spinner, 0.25, 1.0)
    b.scale(spinner, close, 1.0, curve=EASE_IN)
    b.scale(spinner, 1.0, 0.0)
    b.alpha(slot, [(0.0, 0.0), (0.2, 1.0), (close, 1.0), (1.0, 0.0)])
    rise = min(0.3, close * 0.75)  # before close, also at the shortest close_time (0.3)
    bone = _glow(b, "core", "glow", size * 0.8, [(0.0, 0.0), (rise, 0.7), (close, 0.9, EASE_IN), (1.0, 0.0)])
    b.scale(bone, close, 1.0, curve=EASE_OUT)
    b.scale(bone, 1.0, 1.6)
    for i in range(count):
        a0 = rng.uniform(0, math.tau)
        r0 = size * rng.uniform(0.55, 0.8)
        start = rng.uniform(0.0, close * 0.6)
        lu = _span(start, min(close, start + rng.uniform(0.35, 0.55)))
        sc = _size(b, "sparkle", rng.uniform(16, 30))
        bone = b.bone(f"mote_{i + 1:02d}")
        slot = b.slot(f"mote_{i + 1:02d}", bone, "sparkle", tint="secondary", blend="additive", scale_x=sc,
                      scale_y=sc)
        b.show(slot, lu(0), 1.0)
        steps = 8
        for k in range(steps + 1):
            f = k / steps
            r = r0 * (1 - f) ** 1.3
            ang = a0 - turns * math.tau * f  # clockwise, like the swirl
            b.translate(bone, lu(f), r * math.cos(ang), r * math.sin(ang))
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.2), 1.0)
        b.scale(bone, lu(1), 0.2)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint="secondary")


def splash(b: EffectBuilder, rng: random.Random) -> None:
    """Splash: drops leap up in a crown, arc over and fall, each pointing along its path; a ripple spreads."""
    s = b.settings
    count = int(s["drops"])
    spread = float(s["spread"])
    speed = float(s["speed"])
    gravity = float(s["gravity"])
    size = float(s["drop_size"])
    bone = b.bone("ripple")
    r = _size(b, "ring", 1.0)
    slot = b.slot("ripple", bone, "ring", tint="secondary", blend="additive", scale_x=r, scale_y=r * 0.3)
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, speed * 0.06, curve=EASE_OUT)  # the ripple stays about the size of the crown
    b.scale(bone, 0.8, speed * 0.35)
    b.alpha(slot, [(0.0, 1.0), (0.8, 0.0)], tint="secondary")
    dw, dh = b.image_size("drop")
    for i in range(count):
        a = math.radians(90 + spread * ((i + 0.5) / count - 0.5) + rng.uniform(-6, 6))
        v = speed * rng.uniform(0.6, 1.0)
        vx, vy = v * math.cos(a), v * math.sin(a)
        lu = _span(0.03 * rng.uniform(0, 1), rng.uniform(0.75, 1.0))
        sc = size * rng.uniform(0.6, 1.1) / dw
        bone = b.bone(f"drop_{i + 1:02d}")
        slot = b.slot(f"drop_{i + 1:02d}", bone, "drop", tint="primary", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        steps = 6
        _flight(b, bone, lu, vx, vy, gravity, steps)
        previous = None
        for k in range(steps + 1):  # the drop's tip points along its path (the image points up = 90°)
            f = k / steps
            angle = math.degrees(math.atan2(vy - 2 * gravity * f, vx)) - 90
            if previous is not None:
                angle += 360 * round((previous - angle) / 360)
            previous = angle
            b.rotate(bone, lu(f), angle)
        b.alpha(slot, [(lu(0), 1.0), (lu(0.7), 1.0, EASE_IN), (lu(1), 0.0)])
