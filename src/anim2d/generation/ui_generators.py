"""Generators for casual-game UI feedback and small gameplay effects: notification pings, loading
spinners, chest rays, focus lines, alerts, dust, dashes, stun stars, sleep and flames.

Same contract as generators.py: key times are normalized (0 = activation, 1 = end), every slot
starts hidden and stays attached until u = 1 (faded out by then).
"""

from __future__ import annotations

import math
import random

from ..models.effect import EASE_IN, EASE_OUT
from .builder import EffectBuilder
from .casual_generators import BOUNCE, _glow
from .more_generators import _size, _span
from .slot_generators import _turn


# --- UI -------------------------------------------------------------------------------------------

def badge_ping(b: EffectBuilder, rng: random.Random) -> None:
    """Notification: a badge dot bounces in, rings ripple out from it, then it fades."""
    s = b.settings
    size = float(s["badge_size"])
    hold = float(s["hold"])
    bone = b.bone("badge")
    sc = _size(b, "dot", size)
    slot = b.slot("badge", bone, "dot", scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, 0.0, curve=BOUNCE)
    b.scale(bone, 0.2, 1.0)
    b.scale(bone, hold, 1.0, curve=EASE_IN)
    b.scale(bone, 1.0, 0.0)
    b.alpha(slot, [(0.0, 1.0), (1.0, 1.0)])
    r = _size(b, "ring", 1.0)
    rings = int(s["rings"])
    for i in range(rings):
        lu = _span(0.1 + i * 0.18, min(1.0, 0.1 + i * 0.18 + 0.45))
        bone = b.bone(f"ring_{i + 1:02d}")
        slot = b.slot(f"ring_{i + 1:02d}", bone, "ring", tint="secondary", blend="additive", scale_x=r, scale_y=r)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), size * 1.1, curve=EASE_OUT)
        b.scale(bone, lu(1), size * float(s["ripple_size"]))
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint="secondary")


def orbit_spinner(b: EffectBuilder, rng: random.Random) -> None:
    """Loading spinner: dots on a circle, each smaller and fainter than the one before, turning round."""
    s = b.settings
    count = int(s["dots"])
    radius = float(s["radius"])
    size = float(s["dot_size"])
    spinner = b.bone("spinner")
    _turn(b, spinner, 0.0, 1.0, -360.0 * float(s["turns"]))
    b.scale(spinner, 0.0, 0.6, curve=EASE_OUT)
    b.scale(spinner, 0.15, 1.0)
    b.scale(spinner, 0.85, 1.0, curve=EASE_IN)
    b.scale(spinner, 1.0, 0.6)
    for i in range(count):
        a = math.tau * i / count
        f = 1 - i / count  # the leading dot is the biggest and brightest
        tint = "primary" if i == 0 else "secondary"
        sc = _size(b, "dot", size * (0.45 + 0.55 * f))
        bone = b.bone(f"dot_{i + 1:02d}", parent=spinner, x=radius * math.cos(a), y=radius * math.sin(a))
        slot = b.slot(f"dot_{i + 1:02d}", bone, "dot", tint=tint, scale_x=sc, scale_y=sc)
        b.show(slot, 0.0, 1.0)
        peak = 0.25 + 0.75 * f
        b.alpha(slot, [(0.0, 0.0), (0.15, peak), (0.85, peak), (1.0, 0.0)], tint=tint)


def fan_burst(b: EffectBuilder, rng: random.Random) -> None:
    """Chest open: rays fan up out of a box or chest over a glow, sparkles rise out of it."""
    s = b.settings
    count = int(s["ray_count"])
    spread = float(s["spread"])
    length = float(s["ray_length"])
    hold = float(s["hold"])
    _glow(b, "glow", "glow", float(s["glow_size"]), [(0.0, 0.0), (0.12, 1.0), (hold, 0.9, EASE_IN), (1.0, 0.0)],
          squash=0.6)
    rw, rh = b.image_size("ray")
    for i in range(count):
        angle = 90 - spread / 2 + spread * i / max(1, count - 1) + rng.uniform(-3, 3)
        lu = _span(0.03 * rng.uniform(0, 1), 1.0)
        tint = "primary" if i % 2 == 0 else "secondary"
        ray_len = length * rng.uniform(0.75, 1.0)
        bone = b.bone(f"ray_{i + 1:02d}", rotation=angle)
        slot = b.slot(f"ray_{i + 1:02d}", bone, "ray", tint=tint, blend="additive", x=ray_len / 2,
                      scale_x=ray_len / rw, scale_y=float(s["ray_width"]) / rh)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), 0.1, 1.0, curve=EASE_OUT)
        b.scale(bone, lu(0.25), 1.0, 1.0)
        b.rotate(bone, lu(0), 0.0)
        b.rotate(bone, 1.0, rng.uniform(-6, 6))  # a slight sway
        b.alpha(slot, [(lu(0), 0.0), (lu(0.15), 1.0), (hold, 0.9, EASE_IN), (1.0, 0.0)], tint=tint)
    for i in range(int(s["sparkles"])):
        start = rng.uniform(0.05, 0.6)
        lu = _span(start, min(1.0, start + 0.4))
        sc = _size(b, "sparkle", rng.uniform(18, 36))
        bone = b.bone(f"sparkle_{i + 1:02d}", x=length * 0.3 * rng.uniform(-1, 1), y=length * 0.1)
        slot = b.slot(f"sparkle_{i + 1:02d}", bone, "sparkle", tint="white", blend="additive", scale_x=sc,
                      scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(1), length * 0.15 * rng.uniform(-1, 1), length * rng.uniform(0.5, 0.9))
        b.scale(bone, lu(0), 0.0, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0)
        b.scale(bone, lu(1), 0.2)
        b.alpha(slot, [(lu(0), 1.0), (lu(1), 0.0)], tint="white")


def focus_lines(b: EffectBuilder, rng: random.Random) -> None:
    """Focus lines (manga speed lines): streaks around the edge rush in toward the centre and fade."""
    s = b.settings
    count = int(s["lines"])
    outer = float(s["outer_radius"])
    inner = float(s["inner_radius"])
    lw, lh = b.image_size("line")
    for i in range(count):
        a = 360.0 * i / count + rng.uniform(-4, 4)
        length = (outer - inner) * rng.uniform(0.35, 0.7)
        start = rng.uniform(0.0, 0.35)
        lu = _span(start, min(1.0, start + rng.uniform(0.35, 0.6)))
        rad = math.radians(a)
        # the line points at the centre: its bright head (+X of the image) faces inward
        bone = b.bone(f"line_{i + 1:02d}", x=outer * math.cos(rad), y=outer * math.sin(rad), rotation=a + 180)
        slot = b.slot(f"line_{i + 1:02d}", bone, "line", tint="primary" if i % 3 else "secondary",
                      x=length / 2, scale_x=length / lw, scale_y=float(s["thickness"]) * rng.uniform(0.6, 1.3) / lh)
        b.show(slot, lu(0), 1.0)
        travel = (outer - inner - length) * rng.uniform(0.3, 0.8)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(1), -travel * math.cos(rad), -travel * math.sin(rad))
        b.alpha(slot, [(lu(0), 0.0), (lu(0.2), 1.0), (lu(1), 0.0)], tint="primary" if i % 3 else "secondary")


def alert_pop(b: EffectBuilder, rng: random.Random) -> None:
    """Alert: an exclamation mark pops up with a bounce and a wobble, with emphasis marks around it."""
    s = b.settings
    size = float(s["size"])
    hold = float(s["hold"])
    ew, eh = b.image_size("exclaim")
    bone = b.bone("mark")
    sc = size / eh
    slot = b.slot("mark", bone, "exclaim", y=size / 2, scale_x=sc, scale_y=sc)
    b.show(slot, 0.0, 1.0)
    b.translate(bone, 0.0, 0.0, -size * 0.3, curve=EASE_OUT)
    b.translate(bone, 0.15, 0.0, 0.0)
    b.scale(bone, 0.0, 0.2, 1.6, curve=BOUNCE)
    b.scale(bone, 0.18, 1.0, 1.0)
    b.scale(bone, hold, 1.0, 1.0, curve=EASE_IN)
    b.scale(bone, 1.0, 0.0, 0.0)
    wobble = float(s["wobble"])
    for k, angle in enumerate((wobble, -wobble * 0.7, wobble * 0.4, 0.0)):
        b.rotate(bone, 0.15 + 0.07 * k, angle)
    b.alpha(slot, [(0.0, 1.0), (1.0, 1.0)])
    lw, lh = b.image_size("line")
    marks = int(s["marks"])
    for i in range(marks):
        a = 90 + (i - (marks - 1) / 2) * 32
        rad = math.radians(a)
        lu = _span(0.12, 0.45)
        bone = b.bone(f"mark_{i + 1:02d}", x=size * 0.45 * math.cos(rad), y=size * 0.55 + size * 0.45 * math.sin(rad),
                      rotation=a)
        slot = b.slot(f"mark_{i + 1:02d}", bone, "line", tint="secondary", x=size * 0.15,
                      scale_x=size * 0.3 / lw, scale_y=size * 0.08 / lh)
        b.show(slot, lu(0), 1.0)
        b.scale(bone, lu(0), 0.0, 1.0, curve=EASE_OUT)
        b.scale(bone, lu(0.4), 1.0, 1.0)
        b.translate(bone, lu(0), 0.0, 0.0)
        b.translate(bone, lu(1), size * 0.15 * math.cos(rad), size * 0.15 * math.sin(rad))
        b.alpha(slot, [(lu(0), 1.0), (lu(0.6), 1.0, EASE_IN), (lu(1), 0.0)], tint="secondary")


# --- gameplay -------------------------------------------------------------------------------------

def dust_puff(b: EffectBuilder, rng: random.Random) -> None:
    """Dust: puffs kick out to both sides along the ground and rise a little as they fade (landing, run)."""
    s = b.settings
    count = int(s["puffs"])
    spread = float(s["spread"])
    size = float(s["puff_size"])
    for i in range(count):
        side = -1 if i % 2 == 0 else 1
        if s["one_side"]:
            side = 1
        d = spread * rng.uniform(0.4, 1.0)
        lu = _span(0.04 * rng.uniform(0, 1), rng.uniform(0.75, 1.0))
        sc = _size(b, "puff", size * rng.uniform(0.6, 1.1))
        bone = b.bone(f"puff_{i + 1:02d}", x=side * size * 0.1, rotation=rng.uniform(0, 360))
        slot = b.slot(f"puff_{i + 1:02d}", bone, "puff", scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, lu(1), side * d, size * rng.uniform(0.15, 0.45))
        b.scale(bone, lu(0), 0.3, curve=EASE_OUT)
        b.scale(bone, lu(1), 1.2)
        b.rotate(bone, lu(0), 0.0)
        b.rotate(bone, lu(1), side * -rng.uniform(30, 90))
        b.alpha(slot, [(lu(0), 0.9), (lu(0.3), 0.8, EASE_IN), (lu(1), 0.0)])


def dash_lines(b: EffectBuilder, rng: random.Random) -> None:
    """Dash: speed streaks trail behind a moving object (pointing back along -X), streaming and fading."""
    s = b.settings
    count = int(s["lines"])
    height = float(s["band_height"])
    length = float(s["line_length"])
    lw, lh = b.image_size("line")
    for i in range(count):
        y = height * ((i + 0.5) / count - 0.5) + rng.uniform(-6, 6)
        start = rng.uniform(0.0, 0.4)
        lu = _span(start, min(1.0, start + rng.uniform(0.3, 0.55)))
        ln = length * rng.uniform(0.5, 1.0)
        tint = "primary" if i % 3 else "secondary"
        # the bright head of the streak faces the direction of travel (+X); it drifts back
        bone = b.bone(f"line_{i + 1:02d}", x=-ln * rng.uniform(0.2, 0.6), y=y)
        slot = b.slot(f"line_{i + 1:02d}", bone, "line", tint=tint, blend="additive", x=-ln / 2,
                      scale_x=ln / lw, scale_y=float(s["thickness"]) * rng.uniform(0.6, 1.3) / lh)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0)
        b.translate(bone, lu(1), -length * float(s["drift"]), 0.0)
        b.scale(bone, lu(0), 0.3, 1.0, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0, 1.0)
        b.alpha(slot, [(lu(0), 0.0), (lu(0.2), 1.0), (lu(1), 0.0)], tint=tint)


def orbit_stars(b: EffectBuilder, rng: random.Random) -> None:
    """Stunned: stars circle above the head on a flat ellipse, bigger at the front, smaller at the back."""
    s = b.settings
    count = int(s["stars"])
    rx, ry = float(s["width"]) / 2, float(s["width"]) / 2 * float(s["tilt"])
    turns = float(s["turns"])
    size = float(s["star_size"])
    steps = max(8, int(abs(turns) * 12))
    for i in range(count):
        phase = math.tau * i / count
        sc = _size(b, "star", size)
        bone = b.bone(f"star_{i + 1:02d}")
        slot = b.slot(f"star_{i + 1:02d}", bone, "star", tint="primary" if i % 2 == 0 else "secondary", scale_x=sc,
                      scale_y=sc)
        b.show(slot, 0.0, 1.0)
        for k in range(steps + 1):
            f = k / steps
            a = phase + turns * math.tau * f
            depth = 0.5 - 0.5 * math.sin(a)  # 1 at the front (bottom of the ellipse), 0 at the back
            b.translate(bone, f, rx * math.cos(a), ry * math.sin(a))
            b.scale(bone, f, 0.6 + 0.5 * depth)
            b.rotate(bone, f, -90 * turns * f)  # a slow tumble as it circles
        tint = "primary" if i % 2 == 0 else "secondary"
        b.alpha(slot, [(0.0, 0.0), (0.1, 1.0), (0.85, 1.0, EASE_IN), (1.0, 0.0)], tint=tint)


def float_letters(b: EffectBuilder, rng: random.Random) -> None:
    """Sleep: Z letters float up and to the side one after another, growing, swaying and fading."""
    s = b.settings
    count = int(s["letters"])
    rise = float(s["rise"])
    drift = float(s["drift"])
    size = float(s["letter_size"])
    for i in range(count):
        start = i / count * 0.6
        lu = _span(start, min(1.0, start + 0.55))
        sc = _size(b, "z", size)
        bone = b.bone(f"z_{i + 1:02d}", rotation=15)
        slot = b.slot(f"z_{i + 1:02d}", bone, "z", tint="primary" if i % 2 == 0 else "secondary", scale_x=sc,
                      scale_y=sc)
        b.show(slot, lu(0), 1.0)
        for k in range(5):
            f = k / 4
            b.translate(bone, lu(f), drift * f + size * 0.25 * math.sin(f * math.pi * 2), rise * f)
        b.scale(bone, lu(0), 0.3, curve=EASE_OUT)
        b.scale(bone, lu(1), 1.0 + 0.3 * i / max(1, count - 1))
        b.rotate(bone, lu(0), -10.0)
        b.rotate(bone, lu(1), 10.0)
        tint = "primary" if i % 2 == 0 else "secondary"
        b.alpha(slot, [(lu(0), 0.0), (lu(0.2), 1.0), (lu(0.6), 1.0, EASE_IN), (lu(1), 0.0)], tint=tint)


def flame_rise(b: EffectBuilder, rng: random.Random) -> None:
    """Flames: tongues of fire lick up from a base, shrinking and flickering, over a warm glow."""
    s = b.settings
    count = int(s["flames"])
    width = float(s["base_width"])
    height = float(s["height"])
    size = float(s["flame_size"])
    hold = float(s["hold"])
    _glow(b, "glow", "glow", width * 2.2, [(0.0, 0.0), (0.1, 1.0), (hold, 0.9, EASE_IN), (1.0, 0.0)],
          y=height * 0.15, squash=0.7)
    fw, fh = b.image_size("flame")
    for i in range(count):
        start = hold * i / count * rng.uniform(0.8, 1.0)
        lu = _span(start, min(1.0, start + rng.uniform(0.25, 0.4)))
        x = width * rng.uniform(-0.5, 0.5)
        outer = i % 3 != 0
        tint = "primary" if outer else "secondary"
        sc = size * rng.uniform(0.7, 1.15) * (1.0 if outer else 0.65) / fw
        bone = b.bone(f"flame_{i + 1:02d}", x=x)
        slot = b.slot(f"flame_{i + 1:02d}", bone, "flame", tint=tint, blend="additive", y=fh * sc * 0.4,
                      scale_x=sc, scale_y=sc)
        b.show(slot, lu(0), 1.0)
        b.translate(bone, lu(0), 0.0, 0.0)
        b.translate(bone, lu(1), x * -0.3, height * rng.uniform(0.5, 1.0))
        b.scale(bone, lu(0), 0.5, 0.6, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0, 1.15)
        b.scale(bone, lu(1), 0.2, 0.6)
        b.alpha(slot, [(lu(0), 0.0), (lu(0.15), 1.0), (lu(0.6), 0.8, EASE_IN), (lu(1), 0.0)], tint=tint)
