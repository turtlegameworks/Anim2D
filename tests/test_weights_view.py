"""Weights view (brush settings, Smooth / Auto / Prune, bones list with locks, Bind / Remove / Update
Bindings) and the viewport zoom bar."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor import mesh_ops  # noqa: E402
from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def arm(tmp_path) -> Document:
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (100, 40), (90, 90, 250, 255)).save(folder / "bar.png")
    d = Document(images_dir=folder)
    d.add_bone("root", "upper", length=50)
    d.add_bone("upper", "lower", x=50, length=50)
    d.add_slot("upper", "arm", image="bar")
    d.set_attachment("arm", "bar", x=50)
    mesh_ops.convert_to_mesh(d, "arm", "bar", 6, 2)
    return d


def flat(points):
    return [c for p in points for c in p]


# --- operations ----------------------------------------------------------------------------------

def test_feather_shapes_the_brush():
    assert mesh_ops.falloff(0, 10, 0.0) == 1 and mesh_ops.falloff(9.9, 10, 0.0) == 1, "hard brush"
    assert mesh_ops.falloff(10, 10, 0.5) == 0
    assert mesh_ops.falloff(4, 10, 0.5) == 1, "full strength inside the core"
    assert 0 < mesh_ops.falloff(8, 10, 0.5) < 1


def test_locked_bones_keep_their_weight(arm):
    mesh_ops.bind(arm, "arm", "bar", ["upper", "lower"])
    before = mesh_ops.bone_weights(arm, "arm", "bar", "upper")
    arm.add_bone("root", "extra", y=30, length=40)
    mesh_ops.bind_more(arm, "arm", "bar", ["extra"])
    assert mesh_ops.bound_bones(arm, "arm", "bar") == ["upper", "lower", "extra"], "bound with no weight yet"
    mesh_ops.paint_weights(arm, "arm", "bar", "extra", (50, 0), 500, 0.5, "add", locked={"upper"})
    assert mesh_ops.bone_weights(arm, "arm", "bar", "upper") == pytest.approx(before, abs=1e-3)
    assert sum(mesh_ops.bone_weights(arm, "arm", "bar", "extra")) > 1
    for v in range(len(before)):
        assert sum(mesh_ops.vertex_weights(arm, "arm", "bar", v).values()) == pytest.approx(1, abs=1e-3)


def test_selected_only_smooth_auto_prune(arm):
    mesh_ops.bind(arm, "arm", "bar", ["upper", "lower"])
    before = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    changed = mesh_ops.paint_weights(arm, "arm", "bar", "lower", (50, 0), 500, 1.0, "replace", only={0, 1})
    after = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert changed <= 2 and after[2:] == pytest.approx(before[2:], abs=1e-6)
    spread = max(after) - min(after)
    mesh_ops.smooth_weights(arm, "arm", "bar", 1.0)
    smoothed = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert max(smoothed) - min(smoothed) <= spread
    assert mesh_ops.auto_weights(arm, "arm", "bar", ["upper", "lower"]) == len(before)
    assert mesh_ops.bone_weights(arm, "arm", "bar", "lower") == pytest.approx(before, abs=1e-3), "auto = bind"
    mesh_ops.prune_weights(arm, "arm", "bar", 0.3)
    for v in range(len(before)):
        weights = mesh_ops.vertex_weights(arm, "arm", "bar", v)
        assert all(w >= 0.3 - 1e-6 for w in weights.values()) or len(weights) == 1
        assert sum(weights.values()) == pytest.approx(1, abs=1e-3)


def test_remove_bone_and_update_bindings(arm):
    mesh_ops.bind(arm, "arm", "bar", ["upper", "lower"])
    shape = mesh_ops.vertex_world(arm, "arm", "bar")
    arm.set_bone("lower", rotation=40)  # moving a bone in Setup mode bends the bound mesh
    bent = mesh_ops.vertex_world(arm, "arm", "bar")
    assert flat(bent) != pytest.approx(flat(shape), abs=0.5)
    mesh_ops.update_bindings(arm, "arm", "bar")  # back where it was bound, bound to the moved bone
    assert flat(mesh_ops.vertex_world(arm, "arm", "bar")) == pytest.approx(flat(shape), abs=0.01)
    arm.set_bone("lower", rotation=60)
    assert flat(mesh_ops.vertex_world(arm, "arm", "bar")) != pytest.approx(flat(shape), abs=0.5)
    arm.undo()
    mesh_ops.remove_bone_weights(arm, "arm", "bar", "lower")
    assert mesh_ops.bound_bones(arm, "arm", "bar") == ["upper"]
    assert all(w == pytest.approx(1) for w in mesh_ops.bone_weights(arm, "arm", "bar", "upper"))
    assert flat(mesh_ops.vertex_world(arm, "arm", "bar")) == pytest.approx(flat(shape), abs=0.01), "nothing moves"
    with pytest.raises(EditorError, match="at least one bone"):
        mesh_ops.remove_bone_weights(arm, "arm", "bar", "upper")
    other = Document()
    other.add_slot("root", "s")
    with pytest.raises(EditorError):
        mesh_ops.update_bindings(other, "s", "none")
    assert arm.validate()[0] == []


# --- the panel and the viewport ----------------------------------------------------------------

@pytest.fixture
def win(qapp, arm):
    arm.dirty = False
    w = EditorWindow(arm, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1400, 900)
    w.show()
    w.state.select("attachment", "bar", "arm")
    w.viewport.frame_all()
    qapp.processEvents()
    yield w
    w.doc.dirty = False
    w.close()


def test_weights_view(win, qapp):
    st, panel = win.state, win.weights
    st.set_tool("weights")
    qapp.processEvents()
    assert win.weights_dock.isVisible(), "the Weights tool opens the Weights view"
    assert panel.mesh_label.text() == "Mesh: arm / bar"
    panel.bind(["upper", "lower"])
    names = [panel.bones.topLevelItem(i).text(1) for i in range(panel.bones.topLevelItemCount())]
    assert names == ["upper", "lower"]
    st.select("bone", "lower")  # picking a bone keeps the mesh being weighted
    assert st.weights_mesh() == ("arm", "bar") and st.paint_bone() == "lower"
    lock = panel.bones.topLevelItem(0)
    panel._bone_clicked(lock, 2)
    assert "upper" in st.locked_bones and st.paint_bone() == "upper"
    panel._bone_clicked(panel.bones.topLevelItem(1), 1)
    assert st.paint_bone() == "lower"
    panel.strength.spin.setValue(30)
    panel.feather.spin.setValue(10)
    panel.size.spin.setValue(55)
    assert (st.brush_strength, st.brush_feather, st.brush_size) == (0.3, 0.1, 55)
    panel.mode.setCurrentIndex(2)
    panel.mode.activated.emit(2)
    assert st.paint_mode == "replace"
    assert panel.prune(0.2) >= 0
    st.weights_selected_only = True
    with pytest.raises(EditorError, match="select vertices"):
        panel.smooth()
    st.weights_selected_only = False
    panel.smooth()
    for box in (panel.pies, panel.overlay):
        box.setChecked(not box.isChecked())
        win.viewport.repaint()  # pies on or off, overlay on or off: draws
    panel.remove()
    assert mesh_ops.bound_bones(win.doc, "arm", "bar") == ["upper"]


def test_zoom_bar(win, qapp):
    vp = win.viewport
    bar = vp.zoom_bar
    assert bar.isVisible() and bar.x() < 20, "on the left edge"
    zoom = vp.zoom
    bar.plus.click()
    assert vp.zoom == pytest.approx(zoom * 1.25)
    bar.minus.click()
    assert vp.zoom == pytest.approx(zoom)
    bar.actual.click()
    assert vp.zoom == 1.0
    bar.slider.setValue(bar.slider.maximum())
    assert vp.zoom == pytest.approx(16.0)
    bar.fit.click()
    assert vp.zoom < 16
    centre = QPointF(vp.width() / 2, vp.height() / 2)
    world = vp.to_world(centre)
    vp.set_zoom(3.0)
    assert vp.to_world(centre) == pytest.approx(world, abs=1e-6), "zooms about the centre"
    assert win.preview.viewport.checker and win.preview.viewport.zoom_bar is not None


def test_value_bar_input(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from anim2d.editor.value_bar import ValueBar
    bar = ValueBar(0, 100, 50)
    bar.resize(200, 28)
    bar.show()
    seen = []
    bar.valueChanged.connect(seen.append)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(199, 14))  # right end of the bar
    assert bar.value() == 100
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(91, 14))  # left end of the bar
    assert bar.value() <= 1, "near the left end of the bar"
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert bar.value() == seen[-1] and seen[-1] in (10, 11)
    bar.set(42)
    assert bar.value() == 42 and seen[-1] in (10, 11), "set() shows a value without emitting"
    bar.spin.setValue(500)
    assert bar.value() == 100, "clamped"
    bar.grab()


def test_pick_bones_button_binds(qapp, tmp_path):
    import tempfile
    from PIL import Image
    from PySide6.QtCore import QSettings
    from anim2d.editor.main_window import EditorWindow
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (100, 40), (255, 255, 255, 255)).save(images / "arm.png")
    doc = Document(images_dir=images)
    doc.add_bone("root", "upper", x=-50, length=50)
    doc.add_bone("upper", "lower", x=50, length=50)
    doc.add_slot("root", "arm", image="arm")
    mesh_ops.convert_to_mesh(doc, "arm", "arm", 4, 2)
    mesh_ops.bind(doc, "arm", "arm", ["upper"])
    win = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    st, panel = win.state, win.weights
    st.select("attachment", "arm", "arm")
    st.set_tool("weights")
    st.select("bone", "lower")
    assert mesh_ops.bound_bones(doc, "arm", "arm") == ["upper"], "picking is off: selecting doesn't bind"
    panel.pick_button.setChecked(True)
    st.select("bone", "upper")
    st.select("bone", "lower")
    assert mesh_ops.bound_bones(doc, "arm", "arm") == ["upper", "lower"] and st.paint_bone() == "lower"
    doc.dirty = False
    win.close()
