"""Preset definitions: data-only packages under preset_library/<id>/preset.json.

Presets never contain code. The "generator" field names a built-in generator
from anim2d.generation.registry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .params import COMMON_PARAMETERS, ParameterSpec, spec_from_json

PRESET_SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# Image ids may be nested paths for templates imported from Spine (e.g. "sparks/spark_a").
IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}(/[A-Za-z0-9][A-Za-z0-9_\-]{0,63}){0,7}$")
MAX_PNG_BYTES = 8 * 1024 * 1024
TEMPLATE_GENERATOR = "spine_template"


class PresetError(ValueError):
    """A preset package is malformed. The message is meant for artists."""


@dataclass(frozen=True)
class PresetDefinition:
    id: str
    version: int
    name: str
    category: str
    description: str
    tags: tuple[str, ...]
    generator: str
    generator_version: int
    source_duration_seconds: float
    supports_loop: bool
    required_features: tuple[str, ...]
    images: tuple[tuple[str, Path], ...]  # (image_id, absolute png path)
    parameters: tuple[ParameterSpec, ...]  # common + effect-specific, in display order
    directory: Path
    preview: Path | None = None
    source: str = "built-in"  # "built-in" or "user"
    template_file: Path | None = None  # Spine data with local names (template presets only)
    template_meta_file: Path | None = None

    @property
    def is_template(self) -> bool:
        return self.generator == TEMPLATE_GENERATOR

    def parameter(self, name: str) -> ParameterSpec:
        for spec in self.parameters:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def defaults(self) -> dict[str, Any]:
        return {spec.name: spec.default for spec in self.parameters}

    def image_map(self) -> dict[str, Path]:
        return dict(self.images)


@dataclass(frozen=True)
class PresetSummary:
    id: str
    name: str
    category: str
    description: str
    tags: tuple[str, ...]
    preview: Path | None


def _require(data: dict, key: str, kind: type | tuple[type, ...]):
    if key not in data:
        raise PresetError(f"missing required field '{key}'")
    value = data[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise PresetError(f"field '{key}' has the wrong type")
    return value


def _safe_relative(directory: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel:
        raise PresetError("image paths must be non-empty strings")
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or ":" in rel:
        raise PresetError(f"image path '{rel}' must be relative and stay inside the preset folder")
    resolved = (directory / pure).resolve()
    if directory.resolve() not in resolved.parents:
        raise PresetError(f"image path '{rel}' escapes the preset folder")
    return resolved


def load_preset_file(path: Path, known_generators: set[str]) -> PresetDefinition:
    path = Path(path)
    directory = path.parent
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresetError(f"{path.name}: cannot read preset JSON ({exc})") from None
    if not isinstance(data, dict):
        raise PresetError("preset file must contain a JSON object")

    schema = _require(data, "schema_version", int)
    if schema != PRESET_SCHEMA_VERSION:
        raise PresetError(f"unsupported preset schema_version {schema}; this tool reads version {PRESET_SCHEMA_VERSION}")
    preset_id = _require(data, "id", str)
    if not ID_PATTERN.match(preset_id):
        raise PresetError(f"preset id '{preset_id}' must be lowercase letters, digits and underscores")
    generator = _require(data, "generator", str)
    if generator not in known_generators:
        raise PresetError(f"preset '{preset_id}' uses unknown generator '{generator}'")
    source_duration = _require(data, "source_duration_seconds", (int, float))
    if not 0 < source_duration <= 60:
        raise PresetError("source_duration_seconds must be between 0 and 60")

    images_raw = _require(data, "images", dict)
    images: list[tuple[str, Path]] = []
    for image_id, rel in images_raw.items():
        if not IMAGE_ID_PATTERN.match(image_id):
            raise PresetError(f"image id '{image_id}' may only use letters, digits, '_', '-' and '/' folders")
        image_path = _safe_relative(directory, rel)
        if image_path.suffix.lower() != ".png":
            raise PresetError(f"image '{rel}' must be a PNG file")
        if not image_path.is_file():
            raise PresetError(f"image '{rel}' is missing from the preset folder")
        if image_path.stat().st_size > MAX_PNG_BYTES:
            raise PresetError(f"image '{rel}' is larger than {MAX_PNG_BYTES // (1024 * 1024)} MB")
        images.append((image_id, image_path))

    overrides = data.get("common_overrides", {})
    if not isinstance(overrides, dict):
        raise PresetError("common_overrides must be an object")
    common_names = {spec.name for spec in COMMON_PARAMETERS}
    unknown = set(overrides) - common_names
    if unknown:
        raise PresetError(f"common_overrides names unknown parameters: {', '.join(sorted(unknown))}")
    params: list[ParameterSpec] = []
    try:
        for spec in COMMON_PARAMETERS:
            if spec.name in overrides:
                spec = spec.with_overrides(overrides[spec.name])
                spec.coerce(spec.default)
            params.append(spec)
        specific = data.get("parameters", {})
        if not isinstance(specific, dict):
            raise PresetError("parameters must be an object")
        for name, raw in specific.items():
            if name in common_names:
                raise PresetError(f"parameter '{name}' clashes with a common parameter")
            if not ID_PATTERN.match(name):
                raise PresetError(f"parameter name '{name}' must be lowercase letters, digits and underscores")
            params.append(spec_from_json(name, raw))
    except PresetError:
        raise
    except ValueError as exc:
        raise PresetError(str(exc)) from None

    preview = None
    if data.get("preview"):
        preview = _safe_relative(directory, data["preview"])
        if not preview.is_file():
            preview = None

    template_file = template_meta_file = None
    if generator == TEMPLATE_GENERATOR:
        for key in ("template", "template_meta"):
            if not isinstance(data.get(key), str):
                raise PresetError(f"template preset needs a '{key}' file path")
        template_file = _safe_relative(directory, data["template"])
        template_meta_file = _safe_relative(directory, data["template_meta"])
        for file in (template_file, template_meta_file):
            if not file.is_file():
                raise PresetError(f"template file '{file.name}' is missing from the preset folder")

    return PresetDefinition(
        id=preset_id,
        version=_require(data, "version", int),
        name=_require(data, "name", str),
        category=_require(data, "category", str),
        description=str(data.get("description", "")),
        tags=tuple(str(t) for t in data.get("tags", ())),
        generator=generator,
        generator_version=_require(data, "generator_version", int),
        source_duration_seconds=float(source_duration),
        supports_loop=bool(data.get("supports_loop", False)),
        required_features=tuple(str(f) for f in data.get("required_features", ())),
        images=tuple(images),
        parameters=tuple(params),
        directory=directory.resolve(),
        preview=preview,
        template_file=template_file,
        template_meta_file=template_meta_file,
    )
