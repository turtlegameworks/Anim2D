"""Settings validation, instance naming and saved variants."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .preset import PresetDefinition

INSTANCE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
VARIANT_SCHEMA_VERSION = 1


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "ValidationReport") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


@dataclass(frozen=True)
class EffectSettings:
    """Validated, fully-populated settings for one effect instance."""

    instance_name: str
    values: tuple[tuple[str, Any], ...]

    def __getitem__(self, name: str) -> Any:
        for key, value in self.values:
            if key == name:
                return value
        raise KeyError(name)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)


def validate_instance_name(name: str) -> str | None:
    if not isinstance(name, str) or not INSTANCE_NAME_PATTERN.match(name):
        return ("Instance name must be 3-48 characters: lowercase letters, digits and underscores, "
                "starting with a letter (for example fx_impact_01)")
    return None


def validate_settings(preset: PresetDefinition, instance_name: str,
                      raw: dict[str, Any] | None = None) -> tuple[EffectSettings | None, ValidationReport]:
    """Merge raw values over preset defaults and check every value. Returns (settings, report)."""
    report = ValidationReport()
    raw = dict(raw or {})
    name_error = validate_instance_name(instance_name)
    if name_error:
        report.errors.append(name_error)

    known = {spec.name for spec in preset.parameters}
    for unknown in sorted(set(raw) - known):
        report.errors.append(f"'{unknown}' is not a setting of preset '{preset.id}'")

    values: list[tuple[str, Any]] = []
    for spec in preset.parameters:
        try:
            values.append((spec.name, spec.coerce(raw.get(spec.name, spec.default))))
        except ValueError as exc:
            report.errors.append(str(exc))

    if report.ok:
        merged = dict(values)
        if merged.get("opacity", 1) == 0:
            report.warnings.append("Opacity is 0, so the effect will be invisible")
    if not report.ok:
        return None, report
    return EffectSettings(instance_name, tuple(values)), report


def save_variant(path: Path, preset: PresetDefinition, settings: EffectSettings,
                 compatibility_profile: str) -> None:
    data = {
        "variant_schema_version": VARIANT_SCHEMA_VERSION,
        "preset_id": preset.id,
        "preset_version": preset.version,
        "compatibility_profile": compatibility_profile,
        "instance_name": settings.instance_name,
        "settings": settings.as_dict(),
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_variant(path: Path, preset: PresetDefinition) -> tuple[str, dict[str, Any]]:
    """Return (instance_name, raw settings). Raises ValueError on incompatible variants."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read variant file: {exc}") from None
    if not isinstance(data, dict) or data.get("variant_schema_version") != VARIANT_SCHEMA_VERSION:
        raise ValueError("Unsupported variant file version")
    if data.get("preset_id") != preset.id:
        raise ValueError(f"Variant is for preset '{data.get('preset_id')}', not '{preset.id}'")
    if data.get("preset_version") != preset.version:
        raise ValueError(f"Variant was saved with preset version {data.get('preset_version')}; "
                         f"installed version is {preset.version}. Re-create the variant.")
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Variant file has no settings object")
    return str(data.get("instance_name", "")), settings
