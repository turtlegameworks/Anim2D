"""Built-in generators. Each takes an EffectBuilder plus a seeded RNG and adds parts and keys.

All key times are normalized: 0 = activation, 1 = end of effect. One-shot effects
start hidden and every slot is hidden again at u = 1.
"""

from __future__ import annotations

import math
import random

from ..models.effect import EASE_IN, EASE_OUT
from .builder import EffectBuilder


def single_pop(b: EffectBuilder, rng: random.Random) -> None:
    """Smallest useful effect: one image that pops in, grows and fades. Used as the format fixture."""
    s = b.settings
    end_scale = float(s["end_scale"])
    bone = b.bone("pop")
    slot = b.slot("pop", bone, "dot", tint="primary")
    b.show(slot, 0.0, 1.0)
    b.scale(bone, 0.0, 0.2, curve=EASE_OUT)
    b.scale(bone, 1.0, end_scale)
    b.alpha(slot, [(0.0, 1.0), (0.5, 1.0, EASE_IN), (1.0, 0.0)])


def radial_burst(b: EffectBuilder, rng: random.Random) -> None:
    """Impact: a bright flash with rays that shoot outward, stretch, thin and fade."""
    s = b.settings
    count = int(s["ray_count"])
    spread = float(s["spread"])
    ray_length = float(s["ray_length"])
    thickness = float(s["ray_thickness"])
    travel = float(s["travel_distance"])
    start_radius = float(s["start_radius"])
    jitter = float(s["angle_jitter"])
    length_variance = float(s["length_variance"])
    stagger = float(s["stagger"])
    flash_size = float(s["flash_size"])
    flash_portion = float(s["flash_portion"])

    ray_w, ray_h = b.image_size("ray")

    # Rays are created first so the flash draws on top of them.
    for i in range(count):
        if spread >= 360:
            angle = i * 360.0 / count
        elif count == 1:
            angle = 0.0
        else:
            angle = -spread / 2 + spread * i / (count - 1)
        angle += jitter * rng.uniform(-1, 1)
        length = ray_length * (1 + length_variance * rng.uniform(-1, 1))
        start = stagger * rng.uniform(0, 1)

        def lu(f: float, start: float = start) -> float:  # ray-local time -> effect time
            return start + f * (1 - start)

        rad = math.radians(angle)
        cos, sin = math.cos(rad), math.sin(rad)
        bone = b.bone(f"ray_{i + 1:02d}", x=start_radius * cos, y=start_radius * sin, rotation=angle,
                      length=length)
        slot = b.slot(f"ray_{i + 1:02d}", bone, "ray", tint="primary",
                      x=length / 2, scale_x=length / ray_w, scale_y=thickness / ray_h)
        b.show(slot, lu(0.0), 1.0)
        b.translate(bone, lu(0.0), 0.0, 0.0, curve=EASE_OUT)
        b.translate(bone, 1.0, travel * cos, travel * sin)
        b.scale(bone, lu(0.0), 0.15, 1.0, curve=EASE_OUT)
        b.scale(bone, lu(0.3), 1.0, 1.0)
        b.scale(bone, 1.0, 0.25, 0.5)
        b.alpha(slot, [(lu(0.0), 1.0), (lu(0.45), 1.0, EASE_IN), (1.0, 0.0)])

    if flash_size > 0:
        flash_w, _ = b.image_size("flash")
        size = flash_size / flash_w
        bone = b.bone("flash")
        slot = b.slot("flash", bone, "flash", tint="secondary", scale_x=size, scale_y=size)
        b.show(slot, 0.0, flash_portion)
        b.scale(bone, 0.0, 0.4, curve=EASE_OUT)
        b.scale(bone, flash_portion, 1.15)
        b.alpha(slot, [(0.0, 1.0), (flash_portion * 0.3, 1.0, EASE_IN), (flash_portion, 0.0)], tint="secondary")
