"""Turn a JSON export from Spine 3.8 into a user template preset.

Round trip:
  1. Generate a package (or start from any Spine 3.8 project) and import it into Spine.
  2. Edit bones, slots, art and keys in Spine, then Export -> JSON (Nonessential data on).
  3. analyze_export() reads that JSON and its images folder.
  4. register_template() writes preset_library-style folder into the user template library.

The template keeps the artist's data. Names lose the old instance prefix so new
instances can be prefixed again; images are copied next to the template.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..models.preset import ID_PATTERN, PresetDefinition, PresetError, TEMPLATE_GENERATOR, load_preset_file
from ..formats import spine40
from . import spine38_ops as ops

TEMPLATE_META_SCHEMA = 1
MAX_EXPORT_BYTES = 32 * 1024 * 1024


@dataclass
class SlotInfo:
    name: str  # local name (prefix removed)
    colors: list[str]  # distinct RGB hex values
    role: str  # primary / secondary / keep


@dataclass
class ExportAnalysis:
    json_path: Path
    images_dir: Path | None
    animations: list[str] = field(default_factory=list)
    animation: str | None = None
    old_prefix: str = ""
    spine_version: str = ""
    source_duration: float = 0.0
    slots: list[SlotInfo] = field(default_factory=list)
    primary_rgb: str = "ffffff"
    secondary_rgb: str = "ffffff"
    template: dict[str, Any] | None = None  # local-named data, single animation
    image_files: dict[str, Path] = field(default_factory=dict)  # local image path -> source PNG
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.template is not None


def _detect_prefix(data: dict[str, Any]) -> str:
    """A package made by this tool has a root bone '<prefix>_root'."""
    bones = data.get("bones", [])
    root = bones[0]["name"] if bones else ""
    if root.endswith("_root") and len(root) > 5:
        return root[: -len("_root")]
    return ""


def default_images_dir(json_path: Path, data: dict[str, Any]) -> Path | None:
    configured = data.get("skeleton", {}).get("images")
    candidates = []
    if isinstance(configured, str) and configured and ":" not in configured:
        candidates.append(json_path.parent / configured)
    candidates.append(json_path.parent / "images")
    for c in candidates:
        if c.is_dir():
            return c.resolve()
    return None


def analyze_export(json_path: Path, images_dir: Path | None = None, animation: str | None = None) -> ExportAnalysis:
    json_path = Path(json_path)
    result = ExportAnalysis(json_path=json_path, images_dir=Path(images_dir) if images_dir else None)
    try:
        if json_path.stat().st_size > MAX_EXPORT_BYTES:
            result.errors.append("The JSON file is too large for an FX template.")
            return result
        data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append(f"Cannot read the JSON file: {exc}")
        return result
    if not isinstance(data, dict) or "skeleton" not in data:
        result.errors.append("This is not a Spine skeleton JSON export.")
        return result

    result.warnings += ops.normalize_layout(data)
    result.spine_version = str(data["skeleton"].get("spine", ""))
    if result.spine_version.startswith("4.0."):  # templates use the 3.8 layout; 4.0 exports are converted
        data, notes = spine40.downgrade(data)
        result.warnings += notes
    result.animations = list(data.get("animations", {}))
    if animation is None and len(result.animations) == 1:
        animation = result.animations[0]
    result.animation = animation
    errors, warnings = ops.check_supported(data, animation)
    result.errors += errors
    result.warnings += warnings
    if animation is None and len(result.animations) > 1:
        result.errors.append("The export has several animations; choose which one the template uses.")
    if result.errors:
        return result

    if result.images_dir is None:
        result.images_dir = default_images_dir(json_path, data)
    if result.images_dir is None:
        result.errors.append("Cannot find the images folder. Choose the folder that holds this skeleton's PNG images.")
        return result

    data = ops.keep_only_animation(data, animation)
    others = [a for a in result.animations if a != animation]
    if others:
        result.warnings.append(f"Only '{animation}' is kept; other animations are dropped: {', '.join(others)}.")
    result.old_prefix = prefix = _detect_prefix(data)

    def local(name: str) -> str:
        return name[len(prefix) + 1:] if prefix and name.startswith(prefix + "_") else name

    image_map: dict[str, str] = {}  # original path -> local sanitized path
    used: dict[str, str] = {}
    for slot_name, key, att in ops.skin_attachments(data):
        if att.get("type", "region") not in ops.IMAGE_TYPES:
            continue
        original = ops.image_path_of(key, att)
        if original in image_map:
            continue
        stripped = original[len(prefix) + 1:] if prefix and original.startswith(prefix + "/") else original
        local_path = ops.sanitize_image_path(stripped)
        base, n = local_path, 2
        while local_path in used and used[local_path] != original:
            local_path, n = f"{base}_{n}", n + 1
        used[local_path] = original
        image_map[original] = local_path
        source = result.images_dir / f"{original}.png"
        if not source.is_file():
            result.errors.append(f"Image '{original}.png' (used by '{key}' on slot '{slot_name}') is missing "
                                 f"from {result.images_dir}.")
        else:
            result.image_files[local_path] = source
    if result.errors:
        return result

    template = ops.rename_document(data, local, lambda p: image_map[p])
    for kind, names in ops.all_names(template).items():
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            result.errors.append(f"{kind.capitalize()} names collide after removing prefix '{prefix}': "
                                 f"{', '.join(dupes)}. Rename them in Spine.")
    if result.errors:
        return result
    template["skeleton"] = {"spine": result.spine_version,
                            "fps": data["skeleton"].get("fps", 30), "images": "./images/", "audio": ""}
    anim_local = local(animation)
    result.source_duration = ops.animation_duration(template["animations"][anim_local])
    roles, result.primary_rgb, result.secondary_rgb = ops.detect_tint_roles(template, anim_local)
    rgb_sets = ops.slot_rgb_sets(template, anim_local)
    result.slots = [SlotInfo(name, sorted(rgb_sets[name]), roles[name]) for name in roles]
    result.template = template
    result.counts = {
        "bones": len(template.get("bones", [])),
        "slots": len(template.get("slots", [])),
        "attachments": sum(1 for _ in ops.skin_attachments(template)),
        "images": len(result.image_files),
        "events": len(template.get("events", {})),
    }
    if result.source_duration > 10:
        result.warnings.append(f"The animation is {result.source_duration:.2f}s long; FX are usually much shorter.")
    return result


def suggest_template_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"fx_{slug}" if slug else "my_template"
    return slug[:40]


def register_template(analysis: ExportAnalysis, library_dir: Path, *, preset_id: str, name: str,
                      category: str = "custom", description: str = "", tags: tuple[str, ...] = (),
                      roles: dict[str, str] | None = None, replace: bool = False,
                      known_generators: set[str]) -> PresetDefinition:
    """Write the template preset. Raises PresetError with an artist-readable message."""
    if not analysis.ok:
        raise PresetError("The export has problems; fix them before registering.")
    if not ID_PATTERN.match(preset_id):
        raise PresetError("Template id must be 2-41 characters: lowercase letters, digits and underscores, "
                          "starting with a letter.")
    if not name.strip():
        raise PresetError("Give the template a name.")
    roles = dict(roles or {s.name: s.role for s in analysis.slots})
    if set(roles.values()) - {"primary", "secondary", "keep"}:
        raise PresetError("Tint roles must be primary, secondary or keep.")

    library_dir = Path(library_dir)
    library_dir.mkdir(parents=True, exist_ok=True)
    final_dir = library_dir / preset_id
    version = 1
    if final_dir.exists():
        if not replace:
            raise PresetError(f"A template with id '{preset_id}' already exists. Choose another id or tick "
                              f"'Replace existing template'.")
        try:
            old = json.loads((final_dir / "preset.json").read_text(encoding="utf-8"))
            version = int(old.get("version", 0)) + 1
        except (OSError, ValueError):
            raise PresetError(f"The existing folder '{final_dir}' is not a readable template; "
                              f"move it away yourself first.") from None

    temp_dir = library_dir / f".{preset_id}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        images = {}
        for local_path, source in sorted(analysis.image_files.items()):
            target = temp_dir / "images" / f"{local_path}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            images[local_path] = f"images/{local_path}.png"
        (temp_dir / "template.json").write_text(json.dumps(analysis.template, indent=1) + "\n", encoding="utf-8")
        meta = {
            "template_meta_schema": TEMPLATE_META_SCHEMA,
            "animation": next(iter(analysis.template["animations"])),
            "source_duration_seconds": analysis.source_duration,
            "tint_roles": roles,
            "reference_colors": {"primary": analysis.primary_rgb, "secondary": analysis.secondary_rgb},
            "source": {
                "file": analysis.json_path.name,
                "spine_version": analysis.spine_version,
                "removed_prefix": analysis.old_prefix,
                "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tool_version": __version__,
            },
        }
        (temp_dir / "template_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        duration = round(analysis.source_duration, 4)
        preset = {
            "schema_version": 1,
            "id": preset_id,
            "version": version,
            "name": name.strip(),
            "category": category.strip() or "custom",
            "description": description.strip() or f"Template made in Spine from {analysis.json_path.name}.",
            "tags": list(tags) + ["template"],
            "generator": TEMPLATE_GENERATOR,
            "generator_version": 1,
            "source_duration_seconds": duration,
            "supports_loop": False,
            "required_features": ["bones", "slot_color", "attachment_keys"],
            "preview": None,
            "images": images,
            "template": "template.json",
            "template_meta": "template_meta.json",
            "common_overrides": {
                "duration": {"default": duration, "max": max(5.0, round(duration * 2, 4))},
                "primary_color": {"default": analysis.primary_rgb + "ff"},
                "secondary_color": {"default": analysis.secondary_rgb + "ff"},
            },
            "parameters": {},
        }
        (temp_dir / "preset.json").write_text(json.dumps(preset, indent=2) + "\n", encoding="utf-8")
        loaded = load_preset_file(temp_dir / "preset.json", known_generators)  # same checks as the library

        # Prove a default instance generates and validates before publishing the template.
        from ..models.settings import validate_settings
        from ..packaging.package import build_document
        from ..validation.spine38_validator import validate_output
        settings, report = validate_settings(loaded, "fx_check_01", {})
        if settings is None:
            raise PresetError("; ".join(report.errors))
        doc, data = build_document(loaded, settings)
        check = validate_output(data, None, "fx_check_01", end_time=doc.timing.duration, strict_one_shot=False)
        if not check.ok:
            raise PresetError("The template does not generate valid data: " + "; ".join(check.errors[:5]))

        if final_dir.exists():
            backup = library_dir / "_backup" / f"{preset_id}-v{version - 1}-{time.strftime('%Y%m%d-%H%M%S')}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            final_dir.rename(backup)
        temp_dir.rename(final_dir)
        return load_preset_file(final_dir / "preset.json", known_generators)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
