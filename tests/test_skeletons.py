"""Several skeletons in one project."""

from __future__ import annotations

import json

import pytest

from anim2d.editor.document import Document, EditorError


def test_add_switch_rename_delete():
    doc = Document()
    doc.add_bone("root", "hero_arm")
    doc.add_skeleton("enemy")
    assert doc.skeleton_names() == ["skeleton", "enemy"] and doc.skeleton_name == "enemy"
    assert not doc.has_bone("hero_arm")
    doc.add_bone("root", "claw")
    doc.switch_skeleton("skeleton")
    assert doc.has_bone("hero_arm") and not doc.has_bone("claw")
    assert doc.undo_label() == "Create bone hero_arm", "each skeleton keeps its own undo"
    doc.rename_skeleton("skeleton", "hero")
    assert doc.skeleton_names() == ["hero", "enemy"]
    with pytest.raises(EditorError):
        doc.add_skeleton("enemy")
    doc.delete_skeleton("hero")
    assert doc.skeleton_names() == ["enemy"] and doc.has_bone("claw")
    with pytest.raises(EditorError):
        doc.delete_skeleton("enemy")


def test_save_and_load(tmp_path):
    doc = Document()
    doc.add_bone("root", "a")
    doc.add_skeleton("b")
    doc.add_bone("root", "b_bone")
    path = tmp_path / "p.rigworks"
    doc.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["active"] == "b" and set(raw["skeletons"]) == {"skeleton", "b"}
    assert any(b["name"] == "b_bone" for b in raw["skeleton"]["bones"]), "old versions open the active one"
    again = Document.load(path)
    assert again.skeleton_name == "b" and again.skeleton_names() == ["skeleton", "b"]
    again.switch_skeleton("skeleton")
    assert again.has_bone("a")


def test_main_window_menu(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from anim2d.editor.main_window import EditorWindow
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    w.doc.add_skeleton("second", switch=False)
    w.switch_skeleton("second")
    assert w.state.doc.skeleton_name == "second" and "[second]" in w.windowTitle()
    w._fill_skeletons_menu()
    assert [a.text() for a in w.skeletons_menu.actions()][:2] == ["skeleton", "second"]
    w.doc.dirty = False
    w.close()
