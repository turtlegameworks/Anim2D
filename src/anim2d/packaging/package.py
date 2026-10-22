"""Generates an effect and writes a portable, validated output package.

Output layout:
    <name>/
      <name>.json                 Spine 3.8 skeleton data
      images/<name>/<image>.png   neutral source art (tinted by slot colour)
      <name>.settings.json        saved variant that reproduces this output
      generation_report.json
      IMPORT_IN_SPINE.md

Files are written to a hidden temporary folder next to the destination, validated,
and only then renamed into place. An existing folder is never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..generation.registry import generate_effect
from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings, ValidationReport, save_variant
from ..spine_adapters import spine38
from ..templates.generate import generate_from_template
from ..validation.spine38_validator import validate_output
from .import_guide import render_import_guide


@dataclass
class GenerationResult:
    ok: bool
    output_dir: Path | None = None
    report: ValidationReport = field(default_factory=ValidationReport)
    stats: dict[str, int] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_document(preset: PresetDefinition, settings: EffectSettings) -> tuple[Any, dict[str, Any]]:
    """Return (IR document, serialized Spine 3.8 dict) without touching the filesystem."""
    missing = set(preset.required_features) - spine38.SUPPORTED_FEATURES
    if missing:
        raise ValueError(f"preset '{preset.id}' needs features not in {spine38.PROFILE_ID}: {', '.join(sorted(missing))}")
    if preset.is_template:
        return generate_from_template(preset, settings)
    doc = generate_effect(preset, settings)
    return doc, spine38.serialize(doc)


def write_package(preset: PresetDefinition, settings: EffectSettings, destination: Path) -> GenerationResult:
    destination = Path(destination)
    name = settings.instance_name
    final_dir = destination / name
    result = GenerationResult(ok=False)

    if final_dir.exists():
        result.report.errors.append(
            f"'{final_dir}' already exists. Choose a different instance name or remove the old folder yourself.")
        return result
    destination.mkdir(parents=True, exist_ok=True)

    try:
        doc, data = build_document(preset, settings)
    except (ValueError, KeyError) as exc:
        result.report.errors.append(f"Generation failed: {exc}")
        return result
    result.report.warnings.extend(doc.warnings)

    temp_dir = destination / f".{name}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        image_dir = temp_dir / "images" / name
        image_dir.mkdir(parents=True)
        hashes = {}
        for image_id, source in sorted(doc.images.items()):
            target = image_dir / f"{image_id}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            hashes[f"images/{name}/{image_id}.png"] = _sha256(target)

        json_path = temp_dir / f"{name}.json"
        json_path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")

        check = validate_output(data, temp_dir, name, end_time=doc.timing.delay + doc.timing.duration,
                                strict_one_shot=not preset.is_template)
        result.report.extend(check)
        result.stats = spine38.count_stats(data)
        if not check.ok:
            return result

        save_variant(temp_dir / f"{name}.settings.json", preset, settings, spine38.PROFILE_ID)
        report = {
            "tool": "anim2d",
            "tool_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "compatibility_profile": spine38.PROFILE_ID,
            "target": spine38.PROFILE_LABEL,
            "verification": {
                "automated_structure_check": "passed",
                "spine_editor_import": "NOT VERIFIED by this tool - follow IMPORT_IN_SPINE.md",
            },
            "preset": {"id": preset.id, "version": preset.version, "generator": preset.generator,
                       "generator_version": preset.generator_version, "source": preset.source},
            "required_features": list(preset.required_features),
            "instance_name": name,
            "animation": doc.animation_name,
            "seed": settings["seed"],
            "duration_seconds": doc.timing.duration,
            "delay_seconds": doc.timing.delay,
            "settings": settings.as_dict(),
            "counts": result.stats,
            "warnings": result.report.warnings,
            "files": {f"{name}.json": _sha256(json_path), **hashes},
        }
        (temp_dir / "generation_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (temp_dir / "IMPORT_IN_SPINE.md").write_text(render_import_guide(name, doc.animation_name, preset),
                                                     encoding="utf-8")
        temp_dir.rename(final_dir)
        result.ok = True
        result.output_dir = final_dir
        return result
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
