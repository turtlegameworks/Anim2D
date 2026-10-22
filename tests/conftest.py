from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from anim2d.models.settings import validate_settings
from anim2d.presets.library import PresetLibrary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_user_library(tmp_path, monkeypatch):
    """Keep tests away from the real ~/Documents/Anim2D Templates."""
    path = tmp_path / "user_templates"
    monkeypatch.setenv("ANIM2D_USER_LIBRARY", str(path))
    return path


@pytest.fixture(scope="session")
def library() -> PresetLibrary:
    lib = PresetLibrary.load(ROOT / "src" / "anim2d" / "preset_library")
    assert not lib.errors, lib.errors
    return lib


@pytest.fixture
def make_settings(library):
    def _make(preset_id: str, name: str = "fx_test_01", **values):
        settings, report = validate_settings(library.get(preset_id), name, values)
        assert settings is not None, report.errors
        return settings
    return _make


@pytest.fixture
def preset_dir(tmp_path, library):
    """A writable copy of impact_basic for malformed-preset tests."""
    target = tmp_path / "lib" / "impact_basic"
    shutil.copytree(library.get("impact_basic").directory, target)

    def _edit(**changes):
        path = target / "preset.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return target, _edit
