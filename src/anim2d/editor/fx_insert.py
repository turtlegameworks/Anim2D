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


def event_times(doc: Document, animation: str, event: str) -> list[float]:
    """When the event fires in the animation, in order."""
    keys = doc.data["animations"].get(animation, {}).get("events", [])
    return sorted({round(float(k.get("time", 0)), 5) for k in keys if k.get("name") == event})


def _repeat(fx_anim: dict, starts: list[float]) -> dict:
    """The FX animation (3.8 layout) played from each start time. Each play is cut where the next begins:
    its slots are hidden there and the next play takes over from its own first keys."""
    out: dict = {}
    for i, start in enumerate(starts):
        cut = starts[i + 1] if i + 1 < len(starts) else None
        for group in ("bones", "slots"):
            for name, timelines in fx_anim.get(group, {}).items():
                for tl_name, keys in timelines.items():
                    target = out.setdefault(group, {}).setdefault(name, {}).setdefault(tl_name, [])
                    kept = [copy.deepcopy(k) for k in keys if cut is None or k.get("time", 0) + start < cut - 1e-6]
                    for k in kept:
                        k["time"] = round(k.get("time", 0) + start, 5)
                    if cut is not None and kept:
                        for key in ("curve", "c2", "c3", "c4"):
                            kept[-1].pop(key, None)  # no easing across the cut
                    if cut is not None and tl_name == "attachment" and kept and kept[-1].get("name") is not None:
                        kept.append({"time": round(cut, 5), "name": None})
                    target.extend(kept)
    for group in out.values():
        for timelines in group.values():
            for tl_name, keys in timelines.items():  # where a play's cut meets the next play's first key,
                timelines[tl_name] = list({k["time"]: k for k in keys}.values())  # the new play wins
    return out


def insert_fx(doc: Document, preset: PresetDefinition, settings: EffectSettings, parent_bone: str,
              animation: str, start_time: float, record: bool = True, trigger_event: str | None = None) -> InsertResult:
    """trigger_event: play the FX each time this event fires in the animation, instead of at start_time.
    An animation set (intro, loop, outro) goes into <animation>_intro, <animation>_loop and <animation>_outro."""
    if doc.images_dir is None:
        raise EditorError("Set the project's images folder first; FX images are copied into it.")
    doc.bone(parent_bone)
    if start_time < 0:
        raise EditorError("Start time cannot be negative")
    starts = [start_time]
    if trigger_event:
        starts = event_times(doc, animation, trigger_event)
        if not starts:
            raise EditorError(f"The event '{trigger_event}' doesn't fire in the animation '{animation}'.")
        if any(k.get("time", 0) < 0 for k in doc.data["animations"].get(animation, {}).get("events", [])
               if k.get("name") == trigger_event):  # an event edited to a negative time; checked before rounding
            raise EditorError(f"The event '{trigger_event}' fires before the start of '{animation}'.")
    try:
        fx_doc, fx = build_document(preset, settings)
    except (ValueError, KeyError) as exc:
        raise EditorError(f"Could not generate the FX: {exc}") from None
    fx_anim = next(iter(fx["animations"].values()), {})
    if len(starts) > 1 and any(a.get("deform") for a in fx["animations"].values()):
        raise EditorError("This FX template deforms meshes, which can't be repeated on events yet. "
                          "Insert it at a start time instead.")
    if any(a.get("drawOrder") for a in fx["animations"].values()):
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
    fx_anims = fx["animations"]
    if len(fx_anims) > 1:  # an animation set: each part into an animation of its own
        targets = {f"{animation}_{name.removeprefix(fx_doc.prefix + '_')}": a for name, a in fx_anims.items()}
    else:
        targets = {animation: fx_anim}
    for target_name, source in targets.items():
        _merge(anims.setdefault(target_name, {}), source, fx, starts)
    doc.notify()

    warnings = list(fx_doc.warnings)
    if any(s.get("attachment") for s in fx["slots"]):
        warnings.append("Some FX slots are visible in the setup pose, so they also show in other animations.")
    end = starts[-1] + fx_doc.timing.delay + fx_doc.timing.duration
    if len(targets) > 1:
        warnings.append(f"The animation set went into {', '.join(targets)}.")
    if trigger_event:
        warnings.append(f"Plays {len(starts)} time(s), on each '{trigger_event}' event.")
    return InsertResult(fx_doc.prefix, animation, starts[0], end, len(bones), len(fx["slots"]), len(copies), warnings)


def _merge(target_anim: dict, fx_anim: dict, fx: dict, starts: list[float]) -> None:
    """Add the FX animation's keys, played from each start time, to an animation of the skeleton."""
    if len(starts) == 1:
        shifted = copy.deepcopy(fx_anim)
        for keys in ops._iter_key_lists(shifted):  # 3.8 curves are normalized, so shifting times is exact
            for k in keys:
                k["time"] = round(k.get("time", 0) + starts[0], 5)
    else:
        shifted = {**_repeat(fx_anim, starts), **({"events": [{**k, "time": round(k.get("time", 0) + s, 5)}
                                                               for s in starts for k in fx_anim["events"]]}
                                                    if fx_anim.get("events") else {})}
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
