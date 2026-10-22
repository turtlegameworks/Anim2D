"""Image sequences: numbered images (fire_01 … fire_12) played as a flip-book.

Spine 4.1+ has a 'sequence' attachment, but the 3.8 and 4.0 runtimes Anim2D exports for do not,
so a sequence is made of plain parts every runtime plays: one region attachment per frame in the
slot, and attachment keys at the chosen frame rate.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .document import Document, EditorError

_NUMBERED = re.compile(r"^(.*?)(\d+)$")


def find_sequences(image_paths: list[str], minimum: int = 2) -> dict[str, list[str]]:
    """Numbered images grouped by their name without the number, in number order:
    {'fx/fire_': ['fx/fire_01', 'fx/fire_02', ...]}."""
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for path in image_paths:
        m = _NUMBERED.match(path)
        if m:
            groups[m.group(1)].append((int(m.group(2)), path))
    return {prefix: [p for _, p in sorted(items)] for prefix, items in sorted(groups.items()) if len(items) >= minimum}


def frame_order(count: int, mode: str) -> list[int]:
    """Frame indexes for one cycle: forward, reverse or ping-pong (there and back, ends shown once)."""
    if count < 1:
        return []
    forward = list(range(count))
    if mode == "reverse":
        return forward[::-1]
    if mode == "pingpong":
        return forward + forward[-2:0:-1]
    return forward


def add_image_sequence(doc: Document, slot: str, images: list[str], animation: str, start: float = 0.0,
                       fps: float = 24.0, mode: str = "forward", cycles: int = 1, hold_last: bool = True,
                       record: bool = True) -> dict[str, Any]:
    """Add the images to the slot (reusing attachments already there) and key them as a flip-book in
    the animation from `start`. Returns {'attachments': [...], 'keys': n, 'end': time}."""
    if not images:
        raise EditorError("Choose at least one image for the sequence")
    if not 1 <= fps <= 120:
        raise EditorError("The sequence frame rate must be between 1 and 120")
    if cycles < 1:
        raise EditorError("Play the sequence at least once")
    doc.slot(slot)
    doc.animation(animation)
    if record:
        doc.checkpoint(f"Image sequence in {slot}")
    existing = {att.get("path", name): name for name, att in doc.attachments(slot).items()}
    names = []
    for image in images:
        name = existing.get(image) or doc.add_attachment(slot, image, record=False)
        names.append(name)
    order = frame_order(len(names), mode) * cycles
    if mode == "pingpong" and len(names) > 1:
        order.append(0)  # finish where it started
    time = start
    for index in order:
        doc.set_slot_key(animation, slot, "attachment", time, names[index], record=False)
        time += 1 / fps
    if not hold_last:
        doc.set_slot_key(animation, slot, "attachment", time, None, record=False)
    doc.notify()
    return {"attachments": names, "keys": len(order) + (0 if hold_last else 1), "end": round(time, 6)}
