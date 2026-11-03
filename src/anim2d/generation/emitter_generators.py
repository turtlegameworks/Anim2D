"""A general particle emitter: particles are emitted over time, moved by physics (gravity, drag, wind,
turbulence and an attractor) and baked to bone and slot keys. Same contract as generators.py: key times
are normalized (0 = activation, 1 = end), every slot starts hidden and is hidden again by u = 1.

The simulation is deterministic: a fixed time step and the effect's seed give the same keys every time.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..models.effect import Color
from .builder import EffectBuilder

STEP = 1 / 120  # simulation time step, seconds
MIN_LIFE = 0.05  # particles with less time than this before the effect ends are not emitted


def _lerp(a: Color, b: Color, f: float) -> Color:
    return Color(a.r + (b.r - a.r) * f, a.g + (b.g - a.g) * f, a.b + (b.b - a.b) * f, a.a + (b.a - a.a) * f)


def _spawn_point(shape: str, width: float, rng: random.Random) -> tuple[float, float]:
    if shape == "line":
        return rng.uniform(-width / 2, width / 2), 0.0
    if shape == "circle":  # anywhere inside the circle, evenly spread
        r, a = width / 2 * math.sqrt(rng.random()), rng.uniform(0, math.tau)
        return r * math.cos(a), r * math.sin(a)
    if shape == "ring":
        a = rng.uniform(0, math.tau)
        return width / 2 * math.cos(a), width / 2 * math.sin(a)
    return 0.0, 0.0


def simulate(start: tuple[float, float], velocity: tuple[float, float], life: float, physics: dict,
             rng: random.Random, samples: int) -> list[tuple[float, float, float]]:
    """(x, y, heading in degrees) at samples + 1 evenly spaced moments of the particle's life."""
    x, y = start
    vx, vy = velocity
    gravity, drag, wind = physics["gravity"], physics["drag"], physics["wind"]
    turbulence, frequency = physics["turbulence"], physics["turbulence_frequency"]
    ax_, ay_, pull = physics["attract_x"], physics["attract_y"], physics["attract_strength"]
    phases = [rng.uniform(0, math.tau) for _ in range(4)]  # this particle's own turbulence field
    steps = max(1, math.ceil(life / STEP))
    dt = life / steps
    every = steps / samples
    out = [(x, y, math.degrees(math.atan2(vy, vx)))]
    for i in range(1, steps + 1):
        t = i * dt
        fx = wind + turbulence * (math.sin(frequency * math.tau * t + phases[0])
                                  + 0.5 * math.sin(2.3 * frequency * math.tau * t + phases[1]))
        fy = -gravity + turbulence * (math.sin(frequency * math.tau * t + phases[2])
                                      + 0.5 * math.sin(1.7 * frequency * math.tau * t + phases[3]))
        if pull:
            dx, dy = ax_ - x, ay_ - y
            distance = max(20.0, math.hypot(dx, dy))  # no sling-shot through the centre
            fx += pull * dx / distance
            fy += pull * dy / distance
        vx = (vx + fx * dt) * math.exp(-drag * dt)
        vy = (vy + fy * dt) * math.exp(-drag * dt)
        x += vx * dt
        y += vy * dt
        if i >= len(out) * every - 1e-9:
            out.append((x, y, math.degrees(math.atan2(vy, vx))))
    while len(out) < samples + 1:
        out.append(out[-1])
    return out


PHASES = ("intro", "loop", "outro")


@dataclass
class Particle:
    index: int
    born: float  # seconds from the start of the effect
    life: float
    path: list[tuple[float, float, float]]  # evenly spaced (x, y, heading) over its life
    tint: str
    spin_dir: int
    start_rot: float

    def at(self, t: float) -> tuple[float, float, float]:
        """(x, y, heading) t seconds into its life, between the samples."""
        pos = max(0.0, min(1.0, t / self.life)) * (len(self.path) - 1)
        k = min(int(pos), len(self.path) - 2)
        f = pos - k
        (x0, y0, h0), (x1, y1, h1) = self.path[k], self.path[k + 1]
        dh = (h1 - h0 + 180) % 360 - 180  # the short way round
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, h0 + dh * f


def _bezier(p0, p1, p2, f: float) -> tuple[float, float]:
    """A point on the curve from p0 to p2 bent towards p1."""
    a, b, c = (1 - f) ** 2, 2 * (1 - f) * f, f * f
    return a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]


def _particles(s, duration: float, cycling: bool, rng: random.Random) -> list[Particle]:
    """Every particle of the effect, from one simulation. When cycling (loops and animation sets) they are
    emitted evenly over the whole duration and may live on past the end, into the next cycle."""
    physics = {name: float(s[name]) for name in ("gravity", "drag", "wind", "turbulence", "turbulence_frequency",
                                                  "attract_x", "attract_y", "attract_strength")}
    count = int(s["count"])
    window = duration if cycling else float(s["emit_time"]) * duration
    life_base, life_var = float(s["lifetime"]), float(s["lifetime_variance"])
    speed, speed_var = float(s["speed"]), float(s["speed_variance"])
    angle, spread = float(s["angle"]), float(s["spread"])
    shape, width = str(s["emitter_shape"]), float(s["emitter_width"])
    curve = ((0.0, 0.0), (float(s["path_bend_x"]), float(s["path_bend_y"])),
             (float(s["path_end_x"]), float(s["path_end_y"])))
    spin, face = float(s["spin"]), bool(s["face_direction"])
    out = []
    for i in range(count):
        born = window * (i + rng.random()) / count
        life = life_base * (1 + rng.uniform(-life_var, life_var))
        life = min(life, duration) if cycling else min(life, duration - born)
        direction = angle + rng.uniform(-spread / 2, spread / 2)
        v = speed * (1 + rng.uniform(-speed_var, speed_var))
        if shape == "path":
            # travel: the spawn point walks from start to end over the emission; distribute: anywhere on it
            f = born / window if str(s["path_mode"]) == "travel" and window else rng.random()
            start = _bezier(*curve, f)
            if bool(s["path_align"]):  # the direction is then measured from the curve's own direction there
                (x0, y0), (x1, y1) = _bezier(*curve, max(0.0, f - 0.01)), _bezier(*curve, min(1.0, f + 0.01))
                direction += math.degrees(math.atan2(y1 - y0, x1 - x0))
        else:
            start = _spawn_point(shape, width, rng)
        a = math.radians(direction)
        samples = max(4, min(16, round(life * 10)))
        path = simulate(start, (v * math.cos(a), v * math.sin(a)), max(life, MIN_LIFE), physics, rng, samples)
        tint = "primary" if bool(s["color_over_life"]) or rng.random() < 0.7 else "secondary"
        spin_dir = rng.choice((-1, 1))
        start_rot = rng.uniform(0, 360) if spin and not face else 0.0
        if life >= MIN_LIFE:
            out.append(Particle(i, born, life, path, tint, spin_dir, start_rot))
    return out


def _segments(p: Particle, phase: str, duration: float) -> list[tuple[float, float, float]]:
    """The parts of a particle's life a phase shows, as (from, to) seconds into its life and the effect
    time its life starts at. A loop shows the part inside the cycle and the tail carried over from the cycle
    before; an intro starts cold; an outro only finishes those tails, with nothing new emitted."""
    inside = (0.0, min(p.life, duration - p.born), p.born)
    tail = (duration - p.born, p.life, p.born - duration) if p.born + p.life > duration + 1e-9 else None
    if phase in ("one_shot", "intro"):
        return [inside]
    if phase == "loop":
        return [inside] + ([tail] if tail else [])
    return [tail] if tail else []


def _keys(b: EffectBuilder, p: Particle, bone: str, slot: str, phase: str, s, duration: float) -> None:
    start_px, end_px = float(s["start_size"]), float(s["end_size"])
    spin, face = float(s["spin"]), bool(s["face_direction"])
    fade_in, fade_out = float(s["fade_in"]), float(s["fade_out"])
    over_life = bool(s["color_over_life"])
    base = b.primary if p.tint == "primary" else b.secondary
    step = p.life / (len(p.path) - 1)
    for a, z, offset in _segments(p, phase, duration):
        def u(t: float, offset=offset) -> float:
            return max(0.0, min(1.0, (offset + t) / duration))

        def distinct(times, u=u) -> list[float]:
            """The times in order, one per exported key time (two keys can't share a time)."""
            return list({round(u(t), 6): t for t in sorted(times)}.values())

        if round(u(z), 6) <= round(u(a), 6):
            continue  # too short to show at all
        b.show(slot, u(a), u(z))
        for t in distinct({a, z, *(k * step for k in range(len(p.path)) if a < k * step < z)}):
            x, y, heading = p.at(t)
            b.translate(bone, u(t), x, y)
            b.scale(bone, u(t), start_px + (end_px - start_px) * t / p.life)
            if face:
                b.rotate(bone, u(t), heading)
            elif spin:
                b.rotate(bone, u(t), p.start_rot + p.spin_dir * spin * t)
        for t in distinct({a, z, *(f * p.life for f in (fade_in, 1.0 - fade_out) if a < f * p.life < z)}):
            f = t / p.life
            alpha = min(1.0, f / fade_in if fade_in else 1.0, (1 - f) / fade_out if fade_out else 1.0)
            color = _lerp(b.primary, b.secondary, f) if over_life else base
            b.color(slot, u(t), color.with_alpha(color.a * max(0.0, alpha)))


def particle_emitter(b: EffectBuilder, rng: random.Random) -> None:
    """Particles emitted over time, each flying its own physics path for its lifetime. The phase makes a
    one-shot, or the intro, loop or outro of a continuous effect; an animation set makes all three from the
    same simulation, so they hand over to each other without a jump."""
    s = b.settings
    duration = float(s["duration"])
    animation_set = bool(s["animation_set"])
    phase = str(s["phase"])
    particles = _particles(s, duration, animation_set or phase != "one_shot", rng)
    image = "particle"  # every emitter preset names its image "particle"
    px = 1.0 / b.image_size(image)[0]  # scale for a 1 px wide image
    parts = []
    for p in particles:
        bone = b.bone(f"particle_{p.index + 1:03d}")
        slot = b.slot(f"particle_{p.index + 1:03d}", bone, image, tint=p.tint, blend=str(s["blend"]),
                      scale_x=px, scale_y=px)
        parts.append((p, bone, slot))
    for name in (PHASES if animation_set else (phase,)):
        if animation_set:
            b.start_animation(name)
        for p, bone, slot in parts:
            _keys(b, p, bone, slot, name, s, duration)


