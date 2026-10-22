"""Bone and tree operations for the editor Document (a mixin): split bones, inherit flags (the
Spine 4.0 transform modes), adding bones and constraints to skins, and skin placeholders.

Transform modes as inherit flags (Spine 4.0 data stores the mode; the editor shows checkboxes):
  normal                 inherit rotation, scale and reflection
  noScale                inherit rotation and reflection
  noScaleOrReflection    inherit rotation
  noRotationOrReflection inherit scale
  onlyTranslation        inherit nothing but position
"""

from __future__ import annotations


MODES = {  # (rotation, scale, reflection) -> transform mode
    (True, True, True): "normal", (True, False, True): "noScale", (True, False, False): "noScaleOrReflection",
    (False, True, False): "noRotationOrReflection", (False, False, False): "onlyTranslation",
}
FLAGS = {mode: flags for flags, mode in MODES.items()}


def mode_for(rotation: bool, scale: bool, reflection: bool) -> str:
    """The transform mode for the requested inherit flags. Spine 4.0 has no mode that inherits both
    rotation and scale but not reflection, nor one that inherits reflection without rotation, so
    reflection only counts when rotation is inherited and scale is not."""
    if not rotation:
        return "noRotationOrReflection" if scale else "onlyTranslation"
    if scale:
        return "normal"
    return "noScale" if reflection else "noScaleOrReflection"


class BoneOpsMixin:
    """Mixed into Document."""

    # --- inherit flags ------------------------------------------------------------------------
    def inherit_flags(self, bone: str) -> dict[str, bool]:
        rotation, scale, reflection = FLAGS[self.bone(bone).get("transform", "normal")]
        return {"rotation": rotation, "scale": scale, "reflection": reflection}

    def set_inherit(self, bone: str, record: bool = True, **flags: bool) -> str:
        """Change inherit rotation/scale/reflection; returns the transform mode stored."""
        current = self.inherit_flags(bone)
        if set(flags) - set(current):
            raise self._error("Inherit flags are rotation, scale and reflection")
        current.update({k: bool(v) for k, v in flags.items()})
        mode = mode_for(current["rotation"], current["scale"], current["reflection"])
        self._begin(f"Inherit {bone}", record)
        if mode == "normal":
            self.bone(bone).pop("transform", None)
        else:
            self.bone(bone)["transform"] = mode
        self.notify()
        return mode

    # --- split ----------------------------------------------------------------------------------
    def split_bone(self, name: str, at: float = 0.5, record: bool = True) -> str:
        """Split a bone in two along its length: it keeps the first part, a new child bone takes the
        rest, and the bone's child bones move to the new bone (nothing moves in the viewport)."""
        bone = self.bone(name)
        length = float(bone.get("length", 0))
        if length <= 0:
            raise self._error("Only a bone with a length can be split")
        if not 0 < at < 1:
            raise self._error("Split between the start and the end of the bone")
        children = list(self.children(name))
        self._begin(f"Split {name}", record)
        new = self.add_bone(name, self.unique_name(f"{name}-end", {b["name"] for b in self.bones}),
                            x=length * at, length=length * (1 - at), record=False)
        self.set_bone(name, record=False, length=length * at)
        for child in children:
            self.reparent_bone(child, new, record=False)
        self.notify()
        return new

    # --- skins ----------------------------------------------------------------------------------
    def add_to_skin(self, kind: str, name: str, skin: str | None = None, record: bool = True) -> str:
        """Add a bone ("bone") or constraint ("ik", "transform", "path") to a skin (the edit skin by
        default); it becomes skin-only, active while that skin is."""
        skin = skin or self.edit_skin
        if skin == "default":
            raise self._error("Choose a skin other than the default one (edit it from the Skins list)")
        key = "bones" if kind == "bone" else kind
        current = list(self.skin_data(skin).get(key, []))
        if name in current:
            return skin
        current.append(name)  # the runtime activates a skin bone's parents itself
        self.set_skin_members(skin, record=record, **{key: current})
        return skin

    def skin_placeholders(self, slot: str) -> dict[str, list[str]]:
        """Placeholder name -> the non-default skins that have an attachment under it for this slot."""
        out: dict[str, list[str]] = {}
        for skin in self.data["skins"][1:]:
            for key in skin.get("attachments", {}).get(slot, {}):
                out.setdefault(key, []).append(skin["name"])
        return out

    def add_skin_placeholder(self, slot: str, name: str, image: str | None = None, record: bool = True) -> str:
        """A placeholder is an attachment name skins fill in: this adds `name` to the edit skin's
        slot (from an image, or as an empty point until one is chosen)."""
        if self.edit_skin == "default":
            raise self._error("Skin placeholders belong to skins: edit a skin first (double-click it in Skins)")
        self.slot(slot)
        existing = set(self.attachments(slot))
        self._check_new_name(name, existing, "attachment")
        self._begin(f"Skin placeholder {name}", record)
        if image:
            self.add_attachment(slot, image, name=name, record=False)
        else:
            self.skin.setdefault(slot, {})[name] = {"type": "point"}
        if self.slot(slot).get("attachment") is None:
            self.slot(slot)["attachment"] = name
        self.notify()
        return name

    def all_attachment_names(self, slot: str) -> set[str]:
        return {key for skin in self.data["skins"] for key in skin.get("attachments", {}).get(slot, {})}
