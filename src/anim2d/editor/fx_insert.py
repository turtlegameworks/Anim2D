"""Insert a generated FX (built-in preset or Spine template) into the open skeleton.

The FX keeps its own uniquely prefixed bones/slots/attachments/events. Its root bone is
parented to the chosen bone, its slots draw on top, its images are copied into the
project's images folder under <prefix>/, and its keys are merged into the chosen
animation starting at the chosen time. Existing data is never renamed or overwritten.
"""

from __future__ import annotations

import copy
import filecmp
import shutil
from dataclasses import dataclass, field

from ..formats import spine40
from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings
from ..packaging.package import build_document
from ..templates import spine38_ops as ops
from .data_export import remap_weights
from .document import Document, EditorError


@dataclass
class InsertResult:
    prefix: str
    animation: str
    start: float
    end: float
    bones: int
    slots: int
    images: int
    warnings: list[str] = field(default_factory=list)


def insert_fx(doc: Document, preset: PresetDefinition, settings: EffectSettings, parent_bone: str,
              animation: str, start_time: float, record: bool = True) -> InsertResult:
    if doc.images_dir is None:
        raise EditorError("Set the project's images folder first; FX images are copied into it.")
    doc.bone(parent_bone)
    if start_time < 0:
        raise EditorError("Start time cannot be negative")
    try:
        fx_doc, fx = build_document(preset, settings)
    except (ValueError, KeyError) as exc:
        raise EditorError(f"Could not generate the FX: {exc}") from None
    fx_anim = next(iter(fx["animations"].values()), {})
    if fx_anim.get("drawOrder"):
        raise EditorError("This FX template animates draw order, which cannot be merged into another "
                          "skeleton yet. Remove the draw order keys in Spine and re-register it.")

    clashes = []
    for kind, existing, new in (("bone", {b["name"] for b in doc.bones}, [b["name"] for b in fx["bones"]]),
                                ("slot", {s["name"] for s in doc.slots}, [s["name"] for s in fx["slots"]]),
                                ("event", set(doc.data["events"]), list(fx.get("events", {})))):
        clashes += [f"{kind} '{n}'" for n in new if n in existing]
    if clashes:
        raise EditorError(f"Instance name '{settings.instance_name}' is already used in this skeleton "
                          f"({', '.join(clashes[:3])}). Choose another instance name.")

    copies = []
    for image_id, source in sorted(fx_doc.images.items()):
        target = doc.images_dir / fx_doc.prefix / f"{image_id}.png"
        if target.exists() and not filecmp.cmp(source, target, shallow=False):
            raise EditorError(f"{target} already exists with different content; choose another instance name.")
        copies.append((source, target))

    if record:
        doc.checkpoint(f"Insert FX {fx_doc.prefix}")
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)

    bones = copy.deepcopy(fx["bones"])
    bones[0]["parent"] = parent_bone
    doc.data["bones"].extend(bones)
    doc.data["slots"].extend(copy.deepcopy(fx["slots"]))
    attachments = copy.deepcopy(fx["skins"][0]["attachments"])  # FX: default skin
    fx_bones = [b["name"] for b in fx["bones"]]
    target_index = {b["name"]: i for i, b in enumerate(doc.data["bones"])}
    for entries in attachments.values():
        for att in entries.values():
            remap_weights(att, fx_bones, target_index)  # every FX bone was just added, so this cannot fail
    doc.data["skins"][0]["attachments"].update(attachments)
    doc.data["events"].update(copy.deepcopy(fx.get("events", {})))

    anims = doc.data["animations"]
    target_anim = anims.setdefault(animation, {})
    shifted = copy.deepcopy(fx_anim)
    for keys in ops._iter_key_lists(shifted):  # 3.8 curves are normalized, so shifting times is exact
        for k in keys:
            k["time"] = round(k.get("time", 0) + start_time, 5)
    # The editor works in the 4.0 layout: convert the shifted FX animation before merging it.
    shifted = spine40.upgrade({**fx, "animations": {"fx": shifted}})["animations"]["fx"]
    for group in ("bones", "slots"):
        if shifted.get(group):
            target_anim.setdefault(group, {}).update(shifted[group])
    for skin, slots in shifted.get("deform", {}).items():
        target_anim.setdefault("deform", {}).setdefault(skin, {}).update(slots)
    if shifted.get("events"):
        events = target_anim.setdefault("events", [])
        events.extend(shifted["events"])
        events.sort(key=lambda k: k.get("time", 0))
    doc.notify()

    warnings = list(fx_doc.warnings)
    if any(s.get("attachment") for s in fx["slots"]):
        warnings.append("Some FX slots are visible in the setup pose, so they also show in other animations.")
    end = start_time + fx_doc.timing.delay + fx_doc.timing.duration
    return InsertResult(fx_doc.prefix, animation, start_time, end, len(bones), len(fx["slots"]), len(copies), warnings)
