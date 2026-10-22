"""Physics constraints: springy bones for hair, cloth and tails.

Each constraint makes a bone's tip a weight on a spring: it lags behind when the bone moves,
overshoots and settles, and is pulled by gravity and wind. The simulation runs from the start of
the animation at a fixed rate and its result is a rotation added to the bone.

Spine 3.8 and 4.0 runtimes have no physics, so the result is baked to rotate keys (Edit >
Bake physics), which every runtime plays. The Preview can show the simulation without baking.
Constraints are stored in data["physics"] with field names like Spine 4.2's, and are left out of
exports.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from . import affine
from .document import Document, EditorError

FIELDS = {"bone": None, "inertia": 1.0, "strength": 100.0, "damping": 1.0, "mass": 1.0, "wind": 0.0,
          "gravity": 0.0, "mix": 1.0, "limit": 90.0}
STEP = 1 / 60  # simulation rate, like Spine's default physics step


# --- data -----------------------------------------------------------------------------------------

def constraints(doc: Document) -> list[dict[str, Any]]:
    return doc.data.get("physics", [])


def constraint(doc: Document, name: str) -> dict[str, Any]:
    for c in constraints(doc):
        if c["name"] == name:
            return c
    raise EditorError(f"No physics constraint named '{name}'")


def add_physics(doc: Document, bone: str, name: str | None = None, record: bool = True, **fields: Any) -> str:
    """Add a physics constraint on the bone (one per bone)."""
    doc.bone(bone)
    if bone == doc.root_name:
        raise EditorError("The root bone can't have physics")
    if any(c["bone"] == bone for c in constraints(doc)):
        raise EditorError(f"'{bone}' already has physics")
    existing = {c["name"] for c in constraints(doc)}
    name = name or doc.unique_name(f"{bone}_physics", existing)
    if name in existing:
        raise EditorError(f"A physics constraint named '{name}' already exists")
    if record:
        doc.checkpoint(f"Add physics {name}")
    c = {"name": name, **{k: v for k, v in FIELDS.items() if v is not None}, "bone": bone}
    c.update(_checked(fields))
    doc.data.setdefault("physics", []).append(c)
    doc.dirty = True
    doc.notify()
    return name


def set_physics(doc: Document, name: str, record: bool = True, **fields: Any) -> None:
    c = constraint(doc, name)
    fields = _checked(fields)
    if record:
        doc.checkpoint(f"Edit physics {name}")
    c.update(fields)
    doc.dirty = True
    doc.notify()


def delete_physics(doc: Document, name: str, record: bool = True) -> None:
    c = constraint(doc, name)
    if record:
        doc.checkpoint(f"Delete physics {name}")
    doc.data["physics"].remove(c)
    if not doc.data["physics"]:
        del doc.data["physics"]
    doc.dirty = True
    doc.notify()


def _checked(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - (set(FIELDS) - {"bone"})
    if unknown:
        raise EditorError(f"Unknown physics field(s): {', '.join(sorted(unknown))}")
    out = {k: float(v) for k, v in fields.items()}
    for k in ("strength", "damping", "mass", "inertia"):
        if k in out and out[k] < 0:
            raise EditorError(f"Physics {k} can't be negative")
    if out.get("mass") == 0:
        raise EditorError("Physics mass must be above 0")
    if "mix" in out:
        out["mix"] = min(1.0, max(0.0, out["mix"]))
    return out


# --- simulation ------------------------------------------------------------------------------------

def _angle(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


def simulate(doc: Document, animation: str, fps: float | None = None, duration: float | None = None,
             data: dict[str, Any] | None = None) -> tuple[list[float], dict[str, list[float]]]:
    """Run the physics over the animation. Returns (frame times, {bone: rotation offset per frame})."""
    data = data or doc.data
    phys = [c for c in data.get("physics", []) if c.get("mix", 1) > 0]
    if not phys:
        raise EditorError("There are no physics constraints")
    from ..preview.evaluator import PoseEvaluator
    ev = PoseEvaluator(data, animation or None)
    fps = float(fps or doc.fps)
    duration = ev.duration if duration is None else duration
    order = {b["name"]: i for i, b in enumerate(data["bones"])}
    phys.sort(key=lambda c: order[c["bone"]])
    lengths = {c["bone"]: next(b for b in data["bones"] if b["name"] == c["bone"]).get("length", 0) or 10
               for c in phys}
    offsets = {c["bone"]: 0.0 for c in phys}
    state: dict[str, list[float]] = {}  # bone -> [tip x, tip y, velocity x, velocity y]
    frames = max(1, int(round(duration * fps)))
    times = [min(duration, i / fps) for i in range(frames + 1)]
    out = {c["bone"]: [] for c in phys}
    t, next_frame = 0.0, 0
    while next_frame < len(times):
        world = _solve(ev, t, offsets)
        for c in phys:
            bone = c["bone"]
            m = world[bone]
            ox, oy = m[4], m[5]
            # where the tip would be without this bone's own physics
            angle = math.radians(affine.world_rotation(m) - offsets[bone])
            reach = lengths[bone] * math.hypot(m[0], m[1])
            tx, ty = ox + reach * math.cos(angle), oy + reach * math.sin(angle)
            s = state.get(bone)
            if s is None:
                s = state[bone] = [tx, ty, 0.0, 0.0]
            # inertia < 1 lets the tip follow some of the bone's movement straight away
            follow = 1.0 - min(1.0, c.get("inertia", 1.0))
            s[0] += (tx - s[0]) * follow
            s[1] += (ty - s[1]) * follow
            ax = (c.get("strength", 100) * (tx - s[0]) + c.get("wind", 0) * 10) / c.get("mass", 1)
            ay = (c.get("strength", 100) * (ty - s[1]) - c.get("gravity", 0) * 10) / c.get("mass", 1)
            s[2] = (s[2] + ax * STEP) * math.exp(-c.get("damping", 1) * STEP * 4)
            s[3] = (s[3] + ay * STEP) * math.exp(-c.get("damping", 1) * STEP * 4)
            s[0] += s[2] * STEP
            s[1] += s[3] * STEP
            # keep the tip at the bone's length, so it swings rather than stretches
            dx, dy = s[0] - ox, s[1] - oy
            dist = math.hypot(dx, dy) or 1e-9
            s[0], s[1] = ox + dx / dist * reach, oy + dy / dist * reach
            delta = (_angle(dx, dy) - math.degrees(angle) + 180) % 360 - 180
            limit = c.get("limit", 90)
            offsets[bone] = max(-limit, min(limit, delta)) * c.get("mix", 1)
        while next_frame < len(times) and times[next_frame] <= t + 1e-9:
            for bone in out:
                out[bone].append(offsets[bone])
            next_frame += 1
        t += STEP
    return times, out


def _solve(ev, t: float, offsets: dict[str, float]) -> dict[str, affine.Affine]:
    locals_ = ev.bone_locals(t)
    for bone, off in offsets.items():
        values = list(locals_[bone])
        values[2] += off
        locals_[bone] = tuple(values)
    world = ev._solver.solve(locals_, ev.ik_state(t), transform_state=ev.transform_state(t),
                             path_state=ev.path_state(t), path_targets=ev.path_targets(t))
    return {n: (m[0], m[2], m[1], m[3], m[4], m[5]) for n, m in world.items()}


# --- baking ----------------------------------------------------------------------------------------

def baked_animation(doc: Document, animation: str, data: dict[str, Any] | None = None,
                    fps: float | None = None) -> dict[str, Any]:
    """A copy of the animation with the physics added to each bone's rotate keys, one key per frame."""
    data = data or doc.data
    from ..preview.evaluator import PoseEvaluator
    ev = PoseEvaluator(data, animation or None)
    times, offsets = simulate(doc, animation, fps, data=data)
    anim = copy.deepcopy(data["animations"].get(animation, {})) if animation else {}
    for bone, values in offsets.items():
        keys = []
        for t, off in zip(times, values):
            base = ev.bone_locals(t)[bone][2] - next(b for b in data["bones"] if b["name"] == bone).get("rotation", 0)
            keys.append({**({"time": round(t, 6)} if t else {}), "value": round(base + off, 4)})
        anim.setdefault("bones", {}).setdefault(bone, {})["rotate"] = keys
    return anim


def bake_physics(doc: Document, animation: str, record: bool = True) -> int:
    """Write the physics into the animation as rotate keys (then it plays on any runtime).
    Returns the number of bones baked; the constraints stay, set them to mix 0 to compare."""
    doc.animation(animation)
    anim = baked_animation(doc, animation)
    if record:
        doc.checkpoint(f"Bake physics in {animation}")
    doc.data["animations"][animation] = anim
    doc.dirty = True
    doc.notify()
    return len(constraints(doc))
