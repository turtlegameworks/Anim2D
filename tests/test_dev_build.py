"""Local test builds: the dev channel keeps away from the installed app, shows DEV BUILD, never updates
itself; and tools/dev_build.py's housekeeping (folders, pruning, the post-commit hook, the icon)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from anim2d import resources, updates

ROOT = Path(__file__).resolve().parents[1]
DEV_INFO = {"channel": "dev", "commit": "abc1234def5678", "branch": "local-dev-builds", "dirty": True,
            "built": "2026-10-24T10:00:00"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dev(monkeypatch):
    """Pretend to be a frozen test build."""
    monkeypatch.setattr(resources, "build_info", lambda: dict(DEV_INFO))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_source_runs_are_not_dev_builds():
    assert resources.build_info() == {"channel": "source"}
    assert not resources.is_dev_build() and resources.dev_label() == ""


def test_dev_build_keeps_its_own_settings_and_templates(dev, monkeypatch, tmp_path):
    from anim2d.presets.library import default_user_library_dir
    assert resources.dev_label() == "DEV BUILD · abc1234 · uncommitted changes"
    store = resources.settings("editor")
    assert store.organizationName() == "Anim2D-Dev" and store.applicationName() == "Anim2D"
    monkeypatch.delenv("ANIM2D_USER_LIBRARY", raising=False)
    monkeypatch.delenv("SPINE_FX_USER_LIBRARY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_user_library_dir() == tmp_path / "Documents" / "Anim2D Dev Templates"


def test_dev_build_never_updates_itself(dev, qapp, monkeypatch, tmp_path):
    assert updates.install_kind() == "dev"
    from anim2d.editor import update_ui
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    scheduled = []
    monkeypatch.setattr(update_ui.QTimer, "singleShot", lambda *a: scheduled.append(a))
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert not scheduled, "no automatic check"
    dialog = update_ui.UpdateDialog(w.updates, updates.Release("99.0.0", "v99.0.0", "t", "", "", "", {}), w)
    texts = [b.text() for b in dialog.findChildren(update_ui.QPushButton)]
    assert "Open release page" in texts and "Install update" not in texts and "Download" not in texts
    w.close()


def test_dev_build_is_labelled(dev, qapp, tmp_path):
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    from anim2d.presets.library import PresetLibrary
    from anim2d.ui.main_window import MainWindow
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat))
    assert w.dev_badge is not None and w.dev_badge.text().startswith("DEV BUILD")
    assert "[DEV]" in w.windowTitle() and "local-dev-builds" in w.dev_badge.toolTip()
    w.close()
    fx = MainWindow(PresetLibrary.load(ROOT / "src" / "anim2d" / "preset_library"),
                    QSettings(str(tmp_path / "f.ini"), QSettings.Format.IniFormat))
    assert fx.dev_badge is not None and "[DEV]" in fx.windowTitle()
    fx.close()


def test_official_builds_show_no_label(qapp, tmp_path):
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat))
    assert w.dev_badge is None and "[DEV]" not in w.windowTitle()
    w.close()


# --- tools/dev_build.py

@pytest.fixture
def tool(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        spec = importlib.util.spec_from_file_location("dev_build_t", ROOT / "tools" / "dev_build.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "tools"))
    monkeypatch.setattr(module, "BUILDS", tmp_path / "dev-builds")
    monkeypatch.setattr(module, "WORK", tmp_path / "build" / "dev")
    return module


def _fake_build(tool, name: str) -> Path:
    folder = tool.BUILDS / name
    folder.mkdir(parents=True)
    (folder / "build_info.json").write_text(json.dumps({"channel": "dev"}))
    (folder / "Anim2D-Dev.exe").write_bytes(b"x")
    return folder


def test_builds_are_listed_newest_first_and_pruned(tool):
    names = ["20261024-090000-aaaaaaa", "20261024-110000-bbbbbbb-dirty", "20261023-235959-ccccccc"]
    for n in names:
        _fake_build(tool, n)
    (tool.BUILDS / "not-a-build").mkdir()
    assert [p.name for p in tool.builds()] == sorted(names, reverse=True)
    assert tool.remove(tool.builds()[1:]) == 2
    assert [p.name for p in tool.builds()] == ["20261024-110000-bbbbbbb-dirty"]
    assert (tool.BUILDS / "not-a-build").is_dir(), "only test build folders are touched"


def test_clean_removes_every_build(tool, capsys):
    _fake_build(tool, "20261024-090000-aaaaaaa")
    (tool.BUILDS / "20261024-080000-bbbbbbb-failed").mkdir()
    (tool.BUILDS / "20261024-080000-bbbbbbb-failed" / "build.log").write_text("error")
    assert tool.main(["--clean"]) == 0
    assert tool.folders() == [] and "Removed 2" in capsys.readouterr().out


def test_a_running_build_is_left_whole_and_found_again(tool, monkeypatch):
    folder = _fake_build(tool, "20261024-090000-aaaaaaa")
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self.name == "Anim2D-Dev.exe":
            raise PermissionError("in use")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", locked)
    assert tool.remove([folder], quiet=True) == 0
    assert (folder / "build_info.json").is_file() and tool.builds() == [folder], "nothing half-deleted"
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert tool.remove(tool.folders()) == 1 and not folder.exists()


def test_failed_builds_do_not_count_as_builds(tool):
    good = _fake_build(tool, "20261024-090000-aaaaaaa")
    failed = tool.BUILDS / "20261024-100000-bbbbbbb-failed"
    failed.mkdir()
    (failed / "build.log").write_text("error")
    assert tool.builds() == [good], "only finished builds count towards --keep"
    assert tool.folders() == [failed, good]


def test_hook_is_installed_once_and_keeps_an_existing_hook(tool, tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    monkeypatch.setattr(tool, "git", lambda *a: str(hooks) if a[:2] == ("rev-parse", "--git-path") else "")
    tool.install_hook(quiet=True)
    text = (hooks / "post-commit").read_text()
    assert text.startswith("#!/bin/sh") and "dev_build.py --clean --quiet" in text
    tool.install_hook(quiet=True)
    assert (hooks / "post-commit").read_text() == text, "not added twice"
    mine = "#!/usr/bin/env python\nimport sys\nsys.exit(0)\n"  # any language, any exit
    (hooks / "post-commit").write_text(mine)
    tool.install_hook(quiet=True)
    text = (hooks / "post-commit").read_text()
    assert (hooks / tool.PREVIOUS_HOOK).read_text() == mine, "the existing hook is kept exactly as it was"
    assert text.startswith("#!/bin/sh\n") and text.count(tool.HOOK_MARKER) == 1
    assert text.index("dev_build.py --clean") < text.index(f'exec "$(dirname "$0")/{tool.PREVIOUS_HOOK}" "$@"'), \
        "cleans up first, then runs the existing hook"
    tool.install_hook(quiet=True)
    assert (hooks / "post-commit").read_text() == text and (hooks / tool.PREVIOUS_HOOK).read_text() == mine


@pytest.mark.parametrize("name, ours", [
    ("20261024-090000-abc1234", True), ("20261024-090000-abc1234-dirty", True),
    ("20261024-090000-abc1234-dirty-failed", True), ("20261024-090000-nogit-failed", True),
    ("20261024-080000-backup", False), ("20261024-080000-abc1234-old", False), ("my stuff", False),
])
def test_only_folders_it_made_are_cleaned(tool, name, ours):
    (tool.BUILDS / name).mkdir(parents=True)
    assert (tool.BUILDS / name in tool.folders()) is ours


def test_dev_icon_has_every_size(tool, tmp_path):
    from PIL import Image
    path = tool.dev_icon(tmp_path / "dev.ico")
    with Image.open(path) as ico:
        assert (16, 16) in ico.ico.sizes() and (256, 256) in ico.ico.sizes()
