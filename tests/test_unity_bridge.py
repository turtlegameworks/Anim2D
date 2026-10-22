"""Unity bridge: export into a Unity project's Assets for spine-unity."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from anim2d.editor import unity_bridge
from anim2d.editor.document import Document, EditorError
from anim2d.editor.unity_bridge import UnitySettings, export_to_unity


def _unity(tmp_path, runtime="4.0.64"):
    project = tmp_path / "Game"
    (project / "Assets" / "Spine").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.7f1\n")
    if runtime:
        (project / "Assets" / "Spine" / "version.txt").write_text(
            f"This Spine-Unity runtime works with data exported from Spine Editor version: {runtime}")
    return project


def _doc(tmp_path) -> Document:
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(images / "box.png")
    doc = Document(images_dir=images, path=tmp_path / "hero.rig2d")
    doc.add_slot("root", "box", image="box")
    return doc


def test_export_into_assets(tmp_path):
    project = _unity(tmp_path)
    doc = _doc(tmp_path)
    s = UnitySettings(project=str(project), folder="Assets/Spine")
    s.store(doc)
    out, warnings = export_to_unity(doc)
    folder = project / "Assets" / "Spine" / "hero"
    assert out == folder / "hero.json"
    assert (folder / "hero.atlas.txt").is_file() and list(folder.glob("*.png"))
    assert json.loads(out.read_text())["skeleton"]["spine"].startswith("4.0")
    assert (project / unity_bridge.STAMP_FILE).is_file()
    assert not warnings
    doc.save()
    assert Document.load(doc.path).project_settings["unity"]["project"] == str(project)


def test_detect_and_checks(tmp_path):
    project = _unity(tmp_path, "3.8.99")
    assert unity_bridge.detect_runtime(project) == "3.8"
    doc = _doc(tmp_path)
    _, warnings = export_to_unity(doc, UnitySettings(project=str(project)))
    assert any("spine-unity 3.8" in w for w in warnings)
    with pytest.raises(EditorError, match="inside Assets"):
        export_to_unity(doc, UnitySettings(project=str(project), folder="../outside"))
    with pytest.raises(EditorError, match="not a Unity project"):
        export_to_unity(doc, UnitySettings(project=str(tmp_path)))
    script = unity_bridge.install_refresh_script(project)
    assert "AssetDatabase.Refresh" in script.read_text()


def test_dialog(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from anim2d.editor.unity_dialog import UnityDialog
    project = _unity(tmp_path)
    doc = _doc(tmp_path)
    dlg = UnityDialog(doc)
    dlg.project.setText(str(project))
    assert "Assets/Spine/hero" in dlg.info.text() and "4.0 found" in dlg.info.text()
    dlg._save()
    assert UnitySettings.of(doc).project == str(project)
