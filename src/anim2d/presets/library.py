"""Preset discovery: every folder holding a preset.json is a preset.

Two roots are scanned: the built-in library shipped with the tool (read-only) and
the user's template library, where templates registered from Spine exports go.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..generation.registry import GENERATORS
from ..models.preset import TEMPLATE_GENERATOR, PresetDefinition, PresetError, PresetSummary, load_preset_file

KNOWN_GENERATORS = set(GENERATORS) | {TEMPLATE_GENERATOR}


def _env(name: str, legacy: str) -> str | None:
    """An environment override; the name used before the rename still works."""
    return os.environ.get(name) or os.environ.get(legacy)


def default_library_dir() -> Path:
    env = _env("ANIM2D_LIBRARY", "SPINE_FX_LIBRARY")
    if env:
        return Path(env)
    from ..resources import bundle_root
    return bundle_root() / "preset_library"


def default_user_library_dir() -> Path:
    env = _env("ANIM2D_USER_LIBRARY", "SPINE_FX_USER_LIBRARY")
    if env:
        return Path(env)
    documents = Path.home() / "Documents"
    from ..resources import is_dev_build
    if is_dev_build():  # local test builds never touch the real template library
        return documents / "Anim2D Dev Templates"
    current, legacy = documents / "Anim2D Templates", documents / "Spine FX Templates"
    # Templates registered before the rename stay where they are until the new folder exists.
    return legacy if legacy.is_dir() and not current.exists() else current


@dataclass
class PresetLibrary:
    root: Path
    user_root: Path | None = None
    presets: dict[str, PresetDefinition] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # folder name -> problem

    @classmethod
    def load(cls, root: Path | None = None, user_root: Path | None = None) -> "PresetLibrary":
        library = cls(Path(root) if root else default_library_dir(), Path(user_root) if user_root else None)
        if not library.root.is_dir():
            library.errors[str(library.root)] = "preset library folder not found"
        else:
            library._scan(library.root, "built-in")
        if library.user_root is not None and library.user_root.is_dir():
            library._scan(library.user_root, "user")
        return library

    def _scan(self, root: Path, source: str) -> None:
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            preset_file = folder / "preset.json"
            if not preset_file.is_file():
                continue
            label = folder.name if source == "built-in" else f"{folder.name} (user)"
            try:
                preset = load_preset_file(preset_file, KNOWN_GENERATORS)
            except PresetError as exc:
                self.errors[label] = str(exc)
                continue
            if preset.id in self.presets:
                self.errors[label] = f"duplicate preset id '{preset.id}'"
                continue
            self.presets[preset.id] = dataclasses.replace(preset, source=source)

    def list_presets(self, query: str = "", category: str | None = None) -> list[PresetSummary]:
        q = query.strip().lower()
        result = []
        for p in self.presets.values():
            if category and p.category != category:
                continue
            haystack = " ".join((p.id, p.name, p.category, p.description, *p.tags)).lower()
            if q and q not in haystack:
                continue
            result.append(PresetSummary(p.id, p.name, p.category, p.description, p.tags, p.preview))
        return sorted(result, key=lambda s: (s.category, s.name))

    def get(self, preset_id: str) -> PresetDefinition:
        if preset_id not in self.presets:
            raise KeyError(f"no preset named '{preset_id}'")
        return self.presets[preset_id]
