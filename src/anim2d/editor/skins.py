"""Skins for the editor Document (a mixin).

Spine 4.0 skins: the "default" skin always exists and comes first; other skins hold their own
attachments (often under the same names, so one slot shows a different image per skin) and may
list skin bones and constraints, which are only active while that skin is. Bones and constraints
marked "skin" (skin required) are inactive in skins that do not list them.

The document edits one skin at a time (edit_skin): attachments(slot) are that skin's, new
attachments go into it, and the viewport shows it on top of the default skin, like activating a
skin in Spine. Deform keys belong to an attachment of a skin, so they follow the edit skin too.
"""

from __future__ import annotations

import copy
from typing import Any

SKIN_LISTS = ("bones", "ik", "transform", "path")


class SkinsMixin:
    """Mixed into Document; relies on its data, _begin, notify, bone, slot, _check_new_name and
    constraint_kind."""

    edit_skin: str = "default"

    def evaluator(self, animation: str | None = "") -> Any:
        """A PoseEvaluator that sees the edit skin (on top of the default skin)."""
        from ..preview.evaluator import PoseEvaluator
        return PoseEvaluator(self.data, animation, self.edit_skin if self.edit_skin != "default" else None)

    def skin_names(self) -> list[str]:
        return [s["name"] for s in self.data["skins"]]

    def animation_skin(self, animation: str | None) -> str | None:
        """The skin an animation belongs to, by name: 'L1_intro' -> skin 'L1' (also 'L1 intro',
        'L1-intro', 'L1/intro'; a skin in a folder matches by its last part). None if no skin matches."""
        if not animation:
            return None
        best = None
        for skin in self.skin_names():
            if skin == "default":
                continue
            for prefix in {skin, skin.rsplit("/", 1)[-1]}:
                if animation.lower().startswith(prefix.lower()) and                         animation[len(prefix):len(prefix) + 1] in ("_", " ", "-", "/", ".") and                         (best is None or len(prefix) > best[1]):
                    best = (skin, len(prefix))
        return best[0] if best else None

    def skin_animations(self, skin: str | None) -> list[str]:
        """Animations shown with a skin: its own and those that belong to no skin."""
        return [a for a in self.animation_names() if self.animation_skin(a) in (None, skin)]

    def skin_data(self, name: str) -> dict[str, Any]:
        for s in self.data["skins"]:
            if s["name"] == name:
                return s
        raise self._error(f"No skin named '{name}'")

    def set_edit_skin(self, name: str) -> None:
        """Choose the skin to edit and show (not an undo step: it is a view setting)."""
        self.skin_data(name)
        if name != self.edit_skin:
            self.edit_skin = name
            self.notify()

    def add_skin(self, name: str, record: bool = True) -> str:
        self._check_new_name(name, set(self.skin_names()), "skin")
        self._begin(f"Create skin {name}", record)
        self.data["skins"].append({"name": name, "attachments": {}})
        self.notify()
        return name

    def duplicate_skin(self, name: str, new: str, record: bool = True) -> str:
        source = self.skin_data(name)
        self._check_new_name(new, set(self.skin_names()), "skin")
        self._begin(f"Duplicate skin {name}", record)
        skin = copy.deepcopy(source)
        skin["name"] = new
        self.data["skins"].append(skin)
        for anim in self.data["animations"].values():  # the copy's attachments get their own deform keys
            if name in anim.get("deform", {}):
                anim["deform"][new] = copy.deepcopy(anim["deform"][name])
        self.notify()
        return new

    def rename_skin(self, old: str, new: str, record: bool = True) -> None:
        skin = self.skin_data(old)
        if old == "default":
            raise self._error("The default skin keeps its name")
        if old == new:
            return
        self._check_new_name(new, set(self.skin_names()), "skin")
        self._begin(f"Rename skin {old}", record)
        skin["name"] = new
        for anim in self.data["animations"].values():
            if old in anim.get("deform", {}):
                anim["deform"] = {(new if k == old else k): v for k, v in anim["deform"].items()}
        for s in self.data["skins"]:  # linked meshes name the skin of their parent
            for entries in s.get("attachments", {}).values():
                for att in entries.values():
                    if att.get("skin") == old:
                        att["skin"] = new
        if self.edit_skin == old:
            self.edit_skin = new
        self.notify()

    def delete_skin(self, name: str, record: bool = True) -> None:
        if name == "default":
            raise self._error("The default skin cannot be deleted")
        self.skin_data(name)
        for s in self.data["skins"]:
            if s["name"] == name:
                continue
            for slot, entries in s.get("attachments", {}).items():
                for key, att in entries.items():
                    if att.get("skin") == name:
                        raise self._error(f"Linked mesh '{key}' in skin '{s['name']}' uses a mesh of skin "
                                          f"'{name}'; delete or relink it first")
        self._begin(f"Delete skin {name}", record)
        self.data["skins"] = [s for s in self.data["skins"] if s["name"] != name]
        for anim in self.data["animations"].values():
            anim.get("deform", {}).pop(name, None)
            if "deform" in anim and not anim["deform"]:
                del anim["deform"]
        if self.edit_skin == name:
            self.edit_skin = "default"
        self.notify()

    def set_skin_members(self, name: str, record: bool = True, **lists: list[str]) -> None:
        """Skin bones and constraints (bones=, ik=, transform=, path=): active only with this skin.
        Listing a bone or constraint marks it skin-required, as Spine does."""
        skin = self.skin_data(name)
        if name == "default" and any(lists.values()):
            raise self._error("The default skin cannot hold skin bones or constraints")
        if set(lists) - set(SKIN_LISTS):
            raise self._error(f"Skins list {', '.join(SKIN_LISTS)}")
        for key, names in lists.items():
            known = {b["name"] for b in self.bones} if key == "bones" else \
                {c["name"] for c in self.data.get(key, [])}
            missing = [n for n in names if n not in known]
            if missing:
                raise self._error(f"No {key[:-1] if key == 'bones' else key} named '{missing[0]}'")
        self._begin(f"Edit skin {name}", record)
        for key, names in lists.items():
            if names:
                skin[key] = list(dict.fromkeys(names))
            else:
                skin.pop(key, None)
            items = self.bones if key == "bones" else self.data.get(key, [])
            for item in items:
                if item["name"] in names:
                    item["skin"] = True
        self.notify()

    def set_skin_required(self, kind: str, name: str, required: bool, record: bool = True) -> None:
        """kind "bone", "ik", "transform" or "path": active only in skins that list it."""
        items = self.bones if kind == "bone" else self.data.get(kind, [])
        item = next((i for i in items if i["name"] == name), None)
        if item is None:
            raise self._error(f"No {kind} named '{name}'")
        if kind == "bone" and name == self.root_name:
            raise self._error("The root bone cannot be skin-only")
        self._begin(f"Edit {name}", record)
        if required:
            item["skin"] = True
        else:
            item.pop("skin", None)
        self.notify()

    def move_attachment_to_skin(self, slot: str, name: str, to_skin: str, record: bool = True) -> None:
        """Move an attachment of the edit skin (with its deform keys) to another skin."""
        source = self.skin_data(self.edit_skin)
        target = self.skin_data(to_skin)
        entries = source.get("attachments", {}).get(slot, {})
        if name not in entries:
            raise self._error(f"Skin '{self.edit_skin}' has no attachment '{name}' on slot '{slot}'")
        if name in target.get("attachments", {}).get(slot, {}):
            raise self._error(f"Skin '{to_skin}' already has '{name}' on slot '{slot}'")
        if to_skin == self.edit_skin:
            return
        self._begin(f"Move {name} to skin {to_skin}", record)
        target.setdefault("attachments", {}).setdefault(slot, {})[name] = entries.pop(name)
        if not entries:
            source["attachments"].pop(slot, None)
        for anim in self.data["animations"].values():
            keys = anim.get("deform", {}).get(self.edit_skin, {}).get(slot, {}).pop(name, None)
            if keys:
                anim["deform"].setdefault(to_skin, {}).setdefault(slot, {})[name] = keys
            self._prune(anim)
        self.notify()
