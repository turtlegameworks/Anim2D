"""Version-neutral intermediate representation (IR) of a generated effect.

Generators build an EffectDocument; a Spine adapter serializes it for one
specific editor version. Key times are stored normalized (0..1) and are
converted to seconds by the adapter with:

    generated_time = start_delay + normalized_time * duration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

# A curve applies to the segment that starts at the key carrying it.
# None = linear, "stepped" = hold, tuple = normalized bezier (cx1, cy1, cx2, cy2).
Curve = Union[None, Literal["stepped"], tuple[float, float, float, float]]

EASE_OUT: Curve = (0.15, 0.7, 0.35, 1.0)
EASE_IN: Curve = (0.6, 0.0, 0.85, 0.3)


@dataclass(frozen=True)
class Color:
    r: float
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def from_hex(cls, value: str) -> "Color":
        text = value.strip().lstrip("#").lower()
        if len(text) == 6:
            text += "ff"
        if len(text) != 8 or any(c not in "0123456789abcdef" for c in text):
            raise ValueError(f"'{value}' is not a colour in RRGGBB or RRGGBBAA hex form")
        parts = [int(text[i : i + 2], 16) / 255 for i in range(0, 8, 2)]
        return cls(*parts)

    def to_hex(self) -> str:
        return "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in (self.r, self.g, self.b, self.a))

    def with_alpha(self, a: float) -> "Color":
        return Color(self.r, self.g, self.b, a)


@dataclass
class Bone:
    name: str
    parent: str | None
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    length: float = 0.0


@dataclass
class RegionAttachment:
    name: str
    image_id: str  # key into EffectDocument.images
    path: str  # image path relative to the package images folder, without extension
    width: float
    height: float
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0


@dataclass
class Slot:
    name: str
    bone: str
    color: Color
    setup_attachment: str | None = None  # None = hidden in setup pose
    blend: str = "normal"
    attachments: list[RegionAttachment] = field(default_factory=list)


@dataclass
class VectorKey:
    u: float
    x: float
    y: float
    curve: Curve = None


@dataclass
class RotateKey:
    u: float
    angle: float
    curve: Curve = None


@dataclass
class ColorKey:
    u: float
    color: Color
    curve: Curve = None


@dataclass
class AttachmentKey:
    u: float
    name: str | None


@dataclass
class BoneTimelines:
    translate: list[VectorKey] = field(default_factory=list)
    scale: list[VectorKey] = field(default_factory=list)
    rotate: list[RotateKey] = field(default_factory=list)


@dataclass
class SlotTimelines:
    color: list[ColorKey] = field(default_factory=list)
    attachment: list[AttachmentKey] = field(default_factory=list)


@dataclass(frozen=True)
class Timing:
    duration: float  # seconds
    delay: float  # seconds
    fps: float = 30.0

    def seconds(self, u: float) -> float:
        return self.delay + u * self.duration


@dataclass
class EffectDocument:
    prefix: str
    animation_name: str
    timing: Timing
    bones: list[Bone] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    bone_timelines: dict[str, BoneTimelines] = field(default_factory=dict)
    slot_timelines: dict[str, SlotTimelines] = field(default_factory=dict)
    images: dict[str, Path] = field(default_factory=dict)  # image_id -> source PNG
    warnings: list[str] = field(default_factory=list)
