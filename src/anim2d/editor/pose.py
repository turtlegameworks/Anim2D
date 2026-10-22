"""Pose tool: drag a bone's tip and the bone and its parents rotate to follow (like a temporary
IK chain), as Spine's Pose tool does. Solved with cyclic coordinate descent on the posed skeleton.
Only rotations change: in Setup mode the setup pose, in Animate mode rotate keys."""

from __future__ import annotations

import math
from typing import Any

from . import affine


def _rotate(point: tuple[float, float], pivot: tuple[float, float], angle: float) -> tuple[float, float]:
    cos, sin = math.cos(angle), math.sin(angle)
    x, y = point[0] - pivot[0], point[1] - pivot[1]
    return pivot[0] + x * cos - y * sin, pivot[1] + x * sin + y * cos


def pose_chain(doc: Any, evaluator: Any, time: float, bone: str, target: tuple[float, float],
               chain_length: int = 2, iterations: int = 60) -> dict[str, float]:
    """New local rotations (degrees) for `bone` and up to chain_length - 1 of its parents (never the
    root) so the bone's tip moves to `target` (world). Bones of length 0 aim their origin instead."""
    world = evaluator.bone_world(time)
    locals_ = evaluator.bone_locals(time)
    chain = [bone]
    while len(chain) < chain_length:
        parent = doc.bone(chain[-1]).get("parent")
        if not parent or parent == doc.root_name:
            break
        chain.append(parent)
    end = doc.bone(bone)
    length = end.get("length", 0)
    if length > 0:
        tip = affine.apply(world[bone], length, 0)
        joints = {name: (world[name][4], world[name][5]) for name in chain}
    else:  # rotate the parents so this bone's origin follows
        chain = chain[1:]
        if not chain:
            return {}
        tip = (world[bone][4], world[bone][5])
        joints = {name: (world[name][4], world[name][5]) for name in chain}
    deltas = {name: 0.0 for name in chain}
    for _ in range(iterations):
        for j, name in enumerate(chain):
            pivot = joints[name]
            angle = math.atan2(target[1] - pivot[1], target[0] - pivot[0]) - \
                math.atan2(tip[1] - pivot[1], tip[0] - pivot[0])
            angle = (angle + math.pi) % (2 * math.pi) - math.pi
            deltas[name] += angle
            tip = _rotate(tip, pivot, angle)
            for below in chain[:j]:  # joints between this bone and the tip move with it
                joints[below] = _rotate(joints[below], pivot, angle)
        if math.hypot(tip[0] - target[0], tip[1] - target[1]) < 1e-3:
            break
    out = {}
    for name in chain:
        parent = doc.bone(name).get("parent")
        flipped = parent is not None and affine.is_flipped(world[parent])
        sign = -1.0 if flipped else 1.0  # a mirrored parent turns world rotation the other way
        out[name] = locals_[name][2] + sign * math.degrees(deltas[name])
    return out
