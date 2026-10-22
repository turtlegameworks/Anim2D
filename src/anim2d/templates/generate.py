"""Generates an effect instance from an artist-authored Spine 3.8 template.

Templates get the common controls only: colour (by tint role), opacity, root transform,
mirror, duration (retiming), start delay. The artist's bones, art and keys are kept.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..models.effect import Color, EffectDocument, Timing
from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings
from ..spine_adapters.spine38 import SPINE_VERSION
from . import spine38_ops as ops


@lru_cache(maxsize=32)
def _read_text(path: str, mtime_ns: int) -> str:
    return Path(path).read_text(encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    # Parsing a cached string gives every call its own copy, so instances never share state.
    return json.loads(_read_text(str(path), path.stat().st_mtime_ns))


def generate_from_template(preset: PresetDefinition, settings: EffectSettings) -> tuple[EffectDocument, dict]:
    template = _load_json(preset.template_file)
    meta = _load_json(preset.template_meta_file)
    prefix = settings.instance_name

    data = ops.rename_document(template, lambda n: f"{prefix}_{n}", lambda p: f"{prefix}/{p}")

    root = data["bones"][0]
    scale = float(settings["scale"])
    root["x"] = round(root.get("x", 0) + float(settings["position_x"]), 4)
    root["y"] = round(root.get("y", 0) + float(settings["position_y"]), 4)
    root["rotation"] = round(root.get("rotation", 0) + float(settings["rotation"]), 4)
    root["scaleX"] = round(root.get("scaleX", 1) * scale * (-1 if settings["mirror"] else 1), 4)
    root["scaleY"] = round(root.get("scaleY", 1) * scale, 4)

    roles = {f"{prefix}_{slot}": role for slot, role in meta.get("tint_roles", {}).items()}
    ops.apply_colors(data, roles, Color.from_hex(settings["primary_color"]),
                     Color.from_hex(settings["secondary_color"]), float(settings["opacity"]))

    duration, delay = float(settings["duration"]), float(settings["delay"])
    ops.retime(data, float(meta["source_duration_seconds"]), duration, delay)
    ops.add_hidden_preroll(data, delay)
    data["skeleton"] = {"spine": SPINE_VERSION, "fps": template.get("skeleton", {}).get("fps", 30),
                        "images": "./images/", "audio": ""}

    doc = EffectDocument(
        prefix=prefix,
        animation_name=f"{prefix}_{meta['animation']}",
        timing=Timing(duration=duration, delay=delay, fps=float(data["skeleton"]["fps"])),
        images=preset.image_map(),
    )
    if any(s.get("attachment") is not None for s in data["slots"]):
        doc.warnings.append("Some slots are visible in the setup pose (as authored in the template).")
    return doc, data
