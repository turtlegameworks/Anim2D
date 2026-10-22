"""Editing session for one preset: current raw values, undo/redo, reset and naming.

UI-independent so it can be tested without Qt. Values are stored raw (as typed);
validate() turns them into EffectSettings or a report of problems.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .preset import PresetDefinition
from .settings import EffectSettings, ValidationReport, load_variant, save_variant, validate_settings

UNDO_LIMIT = 200


def suggest_instance_name(preset: PresetDefinition, output_dir: Path | None) -> str:
    """fx_<short id>_NN with the first number not already used in output_dir."""
    short = re.sub(r"_(basic|test)$", "", preset.id)
    base = f"fx_{short}"
    taken = set()
    if output_dir and Path(output_dir).is_dir():
        taken = {p.name for p in Path(output_dir).iterdir()}
    for n in range(1, 1000):
        candidate = f"{base}_{n:02d}"
        if candidate not in taken:
            return candidate
    return f"{base}_new"


class SettingsSession:
    def __init__(self, preset: PresetDefinition, instance_name: str):
        self.preset = preset
        self.instance_name = instance_name
        self._values: dict[str, Any] = preset.defaults()
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._last_merge: str | None = None

    @property
    def values(self) -> dict[str, Any]:
        return dict(self._values)

    def get(self, name: str) -> Any:
        return self._values[name]

    def _push(self) -> None:
        self._undo.append(dict(self._values))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def set(self, name: str, value: Any, merge: bool = False) -> bool:
        """Set one value. merge=True folds rapid changes (e.g. slider drags) into one undo step."""
        if name not in self._values:
            raise KeyError(name)
        if self._values[name] == value:
            return False
        if not (merge and self._undo and self._last_merge == name):
            self._push()
        self._last_merge = name if merge else None
        self._values[name] = value
        return True

    def end_merge(self) -> None:
        self._last_merge = None

    def apply(self, values: dict[str, Any]) -> None:
        """Replace several values as one undo step (used for loading variants)."""
        unknown = set(values) - set(self._values)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        self._push()
        self._last_merge = None
        self._values.update(values)

    def reset(self, name: str | None = None) -> None:
        defaults = self.preset.defaults()
        if name is None:
            if self._values != defaults:
                self.apply(defaults)
        else:
            self.set(name, defaults[name])

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(dict(self._values))
        self._values = self._undo.pop()
        self._last_merge = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(dict(self._values))
        self._values = self._redo.pop()
        self._last_merge = None
        return True

    def validate(self) -> tuple[EffectSettings | None, ValidationReport]:
        return validate_settings(self.preset, self.instance_name, self._values)

    def save_variant(self, path: Path, profile: str) -> ValidationReport:
        settings, report = self.validate()
        if settings is not None:
            save_variant(path, self.preset, settings, profile)
        return report

    def load_variant(self, path: Path) -> None:
        """Raises ValueError for incompatible files; leaves the session unchanged on failure."""
        name, raw = load_variant(path, self.preset)
        candidate = {**self.preset.defaults(), **raw}
        _, report = validate_settings(self.preset, name or self.instance_name, candidate)
        if not report.ok:
            raise ValueError("; ".join(report.errors))
        self.apply(candidate)
        if name:
            self.instance_name = name
