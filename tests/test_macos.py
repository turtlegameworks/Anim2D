"""The macOS app's pieces that can be checked on any system: opening files from Finder, the bundle's
Info.plist and icon, the disk image names, the release notes, and the self-test's disk image checks.
Building and running the real app is CI's macOS build job (tools/build_mac.py, tools/self_test.py)."""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from anim2d import __version__ as VERSION
from anim2d.editor import app as editor_app
from anim2d.editor.document import Document
from anim2d.editor.main_window import EditorWindow

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_mac  # noqa: E402
import release_notes  # noqa: E402
import self_test  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    yield w
    w.doc.dirty = False
    w.close()


def _bones(window) -> set[str]:
    return {bone["name"] for bone in window.doc.bones}


def _project(tmp_path) -> Path:
    doc = Document()
    doc.add_bone("root", "from_finder", length=40)
    path = tmp_path / "rig.anim2d"
    doc.save(path)
    return path


# --- opening from Finder

def test_a_project_opened_from_finder_opens_in_the_window(window, tmp_path):
    path = _project(tmp_path)
    handled = editor_app.FileOpenFilter(window).eventFilter(None, QFileOpenEvent(str(path)))
    assert handled and "from_finder" in _bones(window)


def test_opening_from_finder_asks_to_save_changes_first(window, tmp_path, monkeypatch):
    path = _project(tmp_path)
    window.doc.add_bone("root", "unsaved", length=10)
    monkeypatch.setattr(window, "_confirm_discard", lambda: False)  # the user chose Cancel
    editor_app.FileOpenFilter(window).eventFilter(None, QFileOpenEvent(str(path)))
    assert "unsaved" in _bones(window) and "from_finder" not in _bones(window)


def test_a_file_that_cannot_be_opened_says_why(window, tmp_path, monkeypatch):
    bad = tmp_path / "broken.anim2d"
    bad.write_text("not a project")
    shown = []
    monkeypatch.setattr(editor_app.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    editor_app.open_path(window, bad)
    assert shown and _bones(window) == {"root"}, "the open project stays"


# --- the app bundle

def _bundle(tmp_path) -> Path:
    app = tmp_path / "Anim2D.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleExecutable": "Anim2D", "CFBundleIdentifier": build_mac.BUNDLE_ID}, f)
    return app


def test_info_plist_names_version_minimum_macos_and_project_type(tmp_path):
    app = _bundle(tmp_path)
    build_mac.info_plist(app)
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == VERSION
    assert info["CFBundleExecutable"] == "Anim2D", "PyInstaller's own keys are kept"
    assert info["LSMinimumSystemVersion"] == "14.0" and info["NSHighResolutionCapable"] is True
    declared = info["UTExportedTypeDeclarations"][0]
    assert declared["UTTypeTagSpecification"]["public.filename-extension"] == ["anim2d", "rig2d"]
    assert info["CFBundleDocumentTypes"][0]["LSItemContentTypes"] == [declared["UTTypeIdentifier"]]
    assert self_test.executable(app) == app / "Contents" / "MacOS" / "Anim2D"


def test_the_icon_is_a_1024_px_icns(tmp_path):
    from PIL import Image
    icns = build_mac.make_icns(tmp_path / "Anim2D.icns")
    assert icns.read_bytes()[:4] == b"icns"
    with Image.open(icns) as im:
        assert im.size == (1024, 1024)


@pytest.mark.parametrize("machine, arch", [("arm64", "AppleSilicon"), ("x86_64", "Intel")])
def test_disk_image_names_match_what_the_updater_looks_for(monkeypatch, machine, arch):
    from anim2d import updates
    monkeypatch.setattr(build_mac.platform, "machine", lambda: machine)
    name = build_mac.disk_image_name()
    assert name == f"Anim2D-{VERSION}-macOS-{arch}.dmg"
    match = updates.MAC_DISK_IMAGE.match(name)
    assert match and match["version"] == VERSION and match["arch"] == arch


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need extra rights on Windows")
def test_disk_image_check(tmp_path):
    volume = tmp_path / "volume"
    (volume / "Anim2D.app").mkdir(parents=True)
    assert self_test.check_dmg(volume) == ["the disk image has no link to Applications to drag the app onto"]
    (volume / "Applications").symlink_to("/Applications")
    assert self_test.check_dmg(volume) == []


# --- the release's description

CHANGELOG = """# Changelog

## 0.18.0 (2026-11-02)

Anim2D runs on Macs.

* macOS app.

## 0.17.1 (2026-10-24)

* Older things.
"""


def test_release_notes_are_the_changelog_entry_and_the_downloads():
    text = release_notes.notes("0.18.0", CHANGELOG)
    assert text.startswith("Anim2D runs on Macs.\n\n* macOS app.") and "Older things" not in text
    for name in ("Anim2D-Setup-0.18.0.exe", "Anim2D-Portable-0.18.0.exe", "Anim2D-0.18.0-macOS-AppleSilicon.dmg",
                 "Anim2D-0.18.0-macOS-Intel.dmg"):
        assert f"releases/download/v0.18.0/{name})" in text


def test_no_release_without_a_changelog_entry():
    with pytest.raises(SystemExit, match="no entry for 0.19.0"):
        release_notes.notes("0.19.0", CHANGELOG)
    with pytest.raises(SystemExit, match="no entry"):
        release_notes.notes("0.18.0", "## 0.18.0 (2026-11-02)\n\n## 0.17.1\n* x\n")


def test_this_version_has_its_changelog_entry():
    assert release_notes.changelog_entry(VERSION, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
