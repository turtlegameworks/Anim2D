from __future__ import annotations

import json
import os
import sys
import shutil
from pathlib import Path

import pytest

from anim2d.models.settings import validate_settings
from anim2d.presets.library import PresetLibrary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_user_library(tmp_path, monkeypatch):
    """Keep tests away from the real Documents (templates, projects, FX output), AppData and ~/Library
    (updates, logs) and the home folder."""
    path = tmp_path / "user_templates"
    monkeypatch.setenv("ANIM2D_USER_LIBRARY", str(path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    from anim2d import paths
    monkeypatch.setattr(paths, "documents", lambda: tmp_path / "Documents")
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


# On GitHub's Windows Server runners Qt crashes while the interpreter shuts down, after every test has
# finished, which turns a green run red. On CI, exit with pytest's own status before that teardown.
def pytest_sessionfinish(session, exitstatus):
    session.config.stash[_EXIT_STATUS] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if os.environ.get("CI") and _EXIT_STATUS in config.stash:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(config.stash[_EXIT_STATUS])


_EXIT_STATUS = pytest.StashKey[int]()
