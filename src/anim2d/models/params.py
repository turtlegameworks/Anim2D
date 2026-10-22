"""Parameter specifications shared by presets and the settings model."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from .effect import Color

PARAM_TYPES = ("integer", "number", "boolean", "color", "choice")
GROUPS = ("appearance", "transform", "timing", "effect", "advanced")


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str
    default: Any
    label: str
    group: str = "effect"
    min: float | None = None
    max: float | None = None
    unit: str = ""
    description: str = ""
    choices: tuple[str, ...] = ()

    def coerce(self, value: Any) -> Any:
        """Convert a raw value to this parameter's type or raise ValueError with a readable message."""
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no"):
                return value.lower() in ("true", "1", "yes")
            raise ValueError(f"{self.label}: expected true or false, got {value!r}")
        if self.type == "color":
            if not isinstance(value, str):
                raise ValueError(f"{self.label}: expected a hex colour such as ff8800, got {value!r}")
            return Color.from_hex(value).to_hex()
        if self.type == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.label}: must be one of {', '.join(self.choices)}, got {value!r}")
            return value
        if isinstance(value, bool):
            raise ValueError(f"{self.label}: expected a number, got {value!r}")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label}: expected a number, got {value!r}") from None
        if not math.isfinite(number):
            raise ValueError(f"{self.label}: must be a finite number")
        if self.type == "integer":
            if number != int(number):
                raise ValueError(f"{self.label}: must be a whole number, got {value!r}")
            number = int(number)
        if self.min is not None and number < self.min:
            raise ValueError(f"{self.label}: {number}{self.unit} is below the minimum of {self.min}{self.unit}")
        if self.max is not None and number > self.max:
            raise ValueError(f"{self.label}: {number}{self.unit} is above the maximum of {self.max}{self.unit}")
        return number

    def with_overrides(self, overrides: dict[str, Any]) -> "ParameterSpec":
        if not isinstance(overrides, dict):
            raise ValueError(f"the override for common parameter '{self.name}' must be an object")
        allowed = {"default", "min", "max", "label", "description", "unit"}
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"cannot override {', '.join(sorted(unknown))} on common parameter '{self.name}'")
        spec = replace(self, **overrides)
        _check_bounds(spec)
        return spec


def _check_bounds(spec: "ParameterSpec") -> None:
    """Numeric parameters need finite number bounds with min <= max."""
    if spec.type not in ("integer", "number"):
        return
    if spec.min is None or spec.max is None:
        raise ValueError(f"parameter '{spec.name}': numeric parameters need both min and max")
    for label, bound in (("min", spec.min), ("max", spec.max)):
        if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound):
            raise ValueError(f"parameter '{spec.name}': {label} must be a number")
    if spec.min > spec.max:
        raise ValueError(f"parameter '{spec.name}': min is greater than max")


def spec_from_json(name: str, data: dict[str, Any]) -> ParameterSpec:
    if not isinstance(data, dict):
        raise ValueError(f"parameter '{name}' must be an object")
    ptype = data.get("type")
    if ptype not in PARAM_TYPES:
        raise ValueError(f"parameter '{name}': type must be one of {', '.join(PARAM_TYPES)}")
    group = data.get("group", "effect")
    if group not in GROUPS:
        raise ValueError(f"parameter '{name}': group must be one of {', '.join(GROUPS)}")
    spec = ParameterSpec(
        name=name,
        type=ptype,
        default=data.get("default"),
        label=str(data.get("label", name.replace("_", " ").capitalize())),
        group=group,
        min=data.get("min"),
        max=data.get("max"),
        unit=str(data.get("unit", "")),
        description=str(data.get("description", "")),
        choices=tuple(data.get("choices", ())),
    )
    _check_bounds(spec)
    spec.coerce(spec.default)  # the default itself must be valid
    return spec


# Common controls every preset gets. Presets may override default/min/max/label.
COMMON_PARAMETERS: tuple[ParameterSpec, ...] = (
    ParameterSpec("primary_color", "color", "ffffffff", "Primary colour", "appearance",
                  description="Tint applied to the main parts of the effect"),
    ParameterSpec("secondary_color", "color", "ffffffff", "Secondary colour", "appearance",
                  description="Tint applied to accent parts such as flashes and highlights"),
    ParameterSpec("opacity", "number", 1.0, "Opacity", "appearance", 0.0, 1.0,
                  description="Multiplies every alpha key of the effect"),
    ParameterSpec("scale", "number", 1.0, "Scale", "transform", 0.1, 5.0, "x",
                  description="Uniform scale of the FX root bone"),
    ParameterSpec("rotation", "number", 0.0, "Rotation", "transform", -360.0, 360.0, "°",
                  description="Rotation of the FX root bone; counter-clockwise, 0° points right (+X)"),
    ParameterSpec("position_x", "number", 0.0, "Position X", "transform", -5000.0, 5000.0, "px",
                  description="Root bone X; +X is right"),
    ParameterSpec("position_y", "number", 0.0, "Position Y", "transform", -5000.0, 5000.0, "px",
                  description="Root bone Y; +Y is up (Spine convention)"),
    ParameterSpec("mirror", "boolean", False, "Mirror horizontally", "transform",
                  description="Flips the effect by setting the root bone's scale X negative"),
    ParameterSpec("duration", "number", 0.5, "Duration", "timing", 0.05, 5.0, "s",
                  description="Length of the generated animation, excluding start delay"),
    ParameterSpec("delay", "number", 0.0, "Start delay", "timing", 0.0, 5.0, "s",
                  description="Effect stays hidden until this time"),
    ParameterSpec("seed", "integer", 1, "Random seed", "advanced", 0, 2**31 - 1,
                  description="Same seed and settings always produce the same effect"),
)
