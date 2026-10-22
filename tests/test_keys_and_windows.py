"""Auto Key, the separate dockable Timeline / Dopesheet / Graph / Preview windows, the W/E/R/Q tool
keys, shear with an image selected, and customisable keyboard shortcuts."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402
from anim2d.editor.shortcuts_dialog import ShortcutsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def win(qapp, tmp_path, store):
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (40, 20), (200, 80, 80, 255)).save(folder / "box.png")
    d = Document(images_dir=folder)
    d.add_bone("root", "arm", length=60)
    d.add_slot("arm", "s", image="box")
    d.add_animation("wave")
    d.dirty = False
    w = EditorWindow(d, settings_store=store)
    w.resize(1400, 900)
    w.show()
    yield w
    w.doc.dirty = False
    w.close()


def test_auto_key(win, qapp):
    st = win.state
    st.set_mode("animate")
    st.select("bone", "arm")
    assert st.auto_key and win.timeline.auto_key_button.isChecked()
    st.apply_bone_pose("arm", rotation=30)
    assert "rotate" in win.doc.animation("wave")["bones"]["arm"], "Auto Key on: the change is a key"
    win.doc.undo()
    win.timeline.auto_key_button.setChecked(False)
    assert not st.auto_key
    st.set_time(0.5)
    st.apply_bone_pose("arm", rotation=45)
    assert "arm" not in win.doc.animation("wave").get("bones", {}), "Auto Key off: no key yet"
    assert st.evaluator().bone_locals(0.5)["arm"][2] == pytest.approx(45), "but the pose shows"
    st.key_selected_bone()
    assert win.doc.animation("wave")["bones"]["arm"]["rotate"][0]["value"] == pytest.approx(45), "K keeps it"
    assert not st.pose_overrides
    st.apply_bone_pose("arm", rotation=90)
    st.set_time(0.2)
    assert not st.pose_overrides, "changing the time drops unkeyed changes"


def test_windows_are_separate_and_dockable(win, qapp):
    win.state.set_mode("animate")
    qapp.processEvents()
    docks = {d.objectName(): d for d in win.findChildren(QDockWidget)}
    for name in ("Timeline", "Dopesheet", "Graph", "Preview"):
        dock = docks[name]
        assert dock.isVisible() or name == "Graph"
        assert dock.allowedAreas() == Qt.DockWidgetArea.AllDockWidgetAreas
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable
    assert win.timeline.sheet.parent() is not win.timeline, "the dopesheet is its own window"
    win.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, win.graph_dock)
    win.graph_dock.setFloating(True)
    win.reset_layout()
    assert not win.graph_dock.isFloating()
    assert win.dockWidgetArea(win.graph_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
    win.timeline.graph_requested.emit()
    assert win.graph_dock.isVisible()


def test_tool_keys_and_custom_shortcuts(win, qapp, store, monkeypatch):
    from anim2d.editor import shortcuts_dialog
    warnings = []
    monkeypatch.setattr(shortcuts_dialog.QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    reg = win.shortcuts
    assert [reg.key(f"Tool: {t}") for t in ("Translate", "Rotate", "Scale", "Shear")] == ["W", "E", "R", "Q"]
    assert reg.key("Export") == "Ctrl+E" and reg.key("Keyboard Shortcuts") == "Ctrl+Alt+K"
    action = reg.entries["Tool: Translate"][0]
    action.trigger()
    assert win.state.tool == "translate"
    dialog = ShortcutsDialog(reg, win)
    dialog.editors["Tool: Translate"].setKeySequence(QKeySequence("E"))  # clashes with Rotate
    dialog.accept()
    assert reg.key("Tool: Translate") == "W" and "E: " in warnings[0], "a clash is refused"
    dialog.editors["Tool: Translate"].setKeySequence(QKeySequence("G"))
    dialog.accept()
    assert reg.key("Tool: Translate") == "G" and store.value("shortcuts/Tool: Translate") == "G"
    again = EditorWindow(win.doc, settings_store=store)
    assert again.shortcuts.key("Tool: Translate") == "G", "kept between sessions"
    again.close()
    dialog = ShortcutsDialog(reg, win)
    dialog.reset_all()
    dialog.accept()
    assert reg.key("Tool: Translate") == "W" and store.value("shortcuts/Tool: Translate") is None


def test_shear_with_an_image_selected(win, qapp):
    st = win.state
    st.select("attachment", "box", "s")
    st.set_tool("shear")
    qapp.processEvents()
    win.viewport.repaint()
    assert {g[:2] for g in win.viewport._gizmo} == {("shear", "x"), ("shear", "y")}, "the image's bone shears"
    origin = win.viewport.gizmo_frame()[0]
    w = win.viewport.to_world(origin)
    assert win.viewport.begin_drag(*w, op="shear", axis="x")
    assert win.viewport._drag["name"] == "arm"
