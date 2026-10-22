"""Helper that generators use to assemble an EffectDocument with consistent naming."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..models.effect import (AttachmentKey, Bone, BoneTimelines, Color, ColorKey, Curve, EffectDocument,
                             RegionAttachment, RotateKey, Slot, SlotTimelines, Timing, VectorKey)
from ..models.settings import EffectSettings


class EffectBuilder:
    """Every name it creates starts with the instance prefix, e.g. fx_impact_01_ray_03."""

    def __init__(self, settings: EffectSettings, images: dict[str, Path]):
        self.settings = settings
        self.prefix = settings.instance_name
        self._images = dict(images)
        self._image_sizes: dict[str, tuple[int, int]] = {}
        self._used_images: dict[str, Path] = {}
        self.doc = EffectDocument(
            prefix=self.prefix,
            animation_name=f"{self.prefix}_play",
            timing=Timing(duration=float(settings["duration"]), delay=float(settings["delay"])),
        )
        scale = float(settings["scale"])
        self.root = f"{self.prefix}_root"
        self.doc.bones.append(Bone(
            name=self.root, parent=None,
            x=float(settings["position_x"]), y=float(settings["position_y"]),
            rotation=float(settings["rotation"]),
            scale_x=-scale if settings["mirror"] else scale, scale_y=scale,
        ))
        self.primary = Color.from_hex(settings["primary_color"])
        self.secondary = Color.from_hex(settings["secondary_color"])
        self.opacity = float(settings["opacity"])

    # --- structure -------------------------------------------------------------------------

    def image_size(self, image_id: str) -> tuple[int, int]:
        if image_id not in self._image_sizes:
            with Image.open(self._images[image_id]) as img:
                self._image_sizes[image_id] = img.size
        return self._image_sizes[image_id]

    def bone(self, local_name: str, parent: str | None = None, **kwargs) -> str:
        name = f"{self.prefix}_{local_name}"
        self.doc.bones.append(Bone(name=name, parent=parent or self.root, **kwargs))
        return name

    def slot(self, local_name: str, bone: str, image_id: str, tint: str = "primary",
             visible_in_setup: bool = False, blend: str = "normal", **attachment_kwargs) -> str:
        """Create a slot holding one region attachment showing image_id."""
        if image_id not in self._images:
            raise KeyError(f"preset has no image '{image_id}'")
        width, height = self.image_size(image_id)
        self._used_images[image_id] = self._images[image_id]
        attachment = RegionAttachment(
            name=f"{self.prefix}_{image_id}", image_id=image_id, path=f"{self.prefix}/{image_id}",
            width=width, height=height, **attachment_kwargs,
        )
        name = f"{self.prefix}_{local_name}_slot"
        color = self._tint(tint).with_alpha(self.opacity)
        self.doc.slots.append(Slot(
            name=name, bone=bone, color=color,
            setup_attachment=attachment.name if visible_in_setup else None,
            blend=blend, attachments=[attachment],
        ))
        return name

    def attachment_name(self, slot: str) -> str:
        return next(s for s in self.doc.slots if s.name == slot).attachments[0].name

    # --- timelines -------------------------------------------------------------------------

    def _bone_tl(self, bone: str) -> BoneTimelines:
        return self.doc.bone_timelines.setdefault(bone, BoneTimelines())

    def _slot_tl(self, slot: str) -> SlotTimelines:
        return self.doc.slot_timelines.setdefault(slot, SlotTimelines())

    def translate(self, bone: str, u: float, x: float, y: float, curve: Curve = None) -> None:
        self._bone_tl(bone).translate.append(VectorKey(u, x, y, curve))

    def scale(self, bone: str, u: float, x: float, y: float | None = None, curve: Curve = None) -> None:
        self._bone_tl(bone).scale.append(VectorKey(u, x, x if y is None else y, curve))

    def rotate(self, bone: str, u: float, angle: float, curve: Curve = None) -> None:
        self._bone_tl(bone).rotate.append(RotateKey(u, angle, curve))

    def show(self, slot: str, u_on: float, u_off: float) -> None:
        """Make the slot visible from u_on and hidden again at u_off."""
        timeline = self._slot_tl(slot)
        timeline.attachment.append(AttachmentKey(u_on, self.attachment_name(slot)))
        timeline.attachment.append(AttachmentKey(u_off, None))

    def alpha(self, slot: str, keys: list[tuple[float, float] | tuple[float, float, Curve]],
              tint: str = "primary") -> None:
        """Alpha envelope; each key is (u, alpha[, curve]). Alpha is multiplied by the opacity setting."""
        base = self._tint(tint)
        timeline = self._slot_tl(slot)
        for key in keys:
            u, a = key[0], key[1]
            curve = key[2] if len(key) > 2 else None
            timeline.color.append(ColorKey(u, base.with_alpha(a * self.opacity), curve))

    def _tint(self, which: str) -> Color:
        return {"primary": self.primary, "secondary": self.secondary, "white": Color(1, 1, 1, 1)}[which]

    def build(self) -> EffectDocument:
        self.doc.images = dict(self._used_images)
        for timelines in self.doc.bone_timelines.values():
            for keys in (timelines.translate, timelines.scale, timelines.rotate):
                keys.sort(key=lambda k: k.u)
        for timelines in self.doc.slot_timelines.values():
            timelines.color.sort(key=lambda k: k.u)
            timelines.attachment.sort(key=lambda k: k.u)
        return self.doc
