"""The Spine-style layout: the viewport tool bar (transform values, axes, compensate, options), the
tree (search, filters, eye column, New…, Set Parent, Add to Skin, Split) and bone inherit flags."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402
from anim2d.editor.tree_panel import EYE, NAME, ROLE  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    doc = Document()
    doc.add_bone("root", "hip", x=10, y=20, rotation=90, length=40)
    doc.add_bone("hip", "arm", x=40, rotation=-90, length=60)
    doc.add_bone("arm", "hand", x=60, length=20)
    doc.add_slot("arm", "sleeve")
    doc.skin.setdefault("sleeve", {})["cloth"] = {"width": 30, "height": 20, "x": 10}
    doc.set_slot("sleeve", attachment="cloth")
    doc.add_animation("anim")
    doc.dirty = False
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1400, 900)
    w.show()
    w.state.select("bone", "arm")
    w.viewport.frame_all()
    qapp.processEvents()
    yield w
    w.doc.dirty = False
    w.close()


def world(win, bone):
    return win.doc.evaluator("").bone_world(0)[bone]


def drag(widget, a: QPointF, b: QPointF):
    events = [(QMouseEvent.Type.MouseButtonPress, a, Qt.MouseButton.LeftButton)]
    events += [(QMouseEvent.Type.MouseMove, a + (b - a) * (i / 4), Qt.MouseButton.LeftButton) for i in range(1, 5)]
    events.append((QMouseEvent.Type.MouseButtonRelease, b, Qt.MouseButton.NoButton))
    for kind, pos, buttons in events:
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                   buttons, Qt.KeyboardModifier.NoModifier))


# --- the tool bar ------------------------------------------------------------------------------

def test_transform_fields_show_and_edit_values(win):
    bar = win.tool_panel
    assert bar.title.text() == "arm"
    assert [b.value() for b in bar.value_boxes["translate"]] == [40, 0]
    assert bar.value_boxes["rotate"][0].value() == -90
    field = bar.value_boxes["scale"][1]
    field.setText("1.5")
    field.editingFinished.emit()
    assert win.doc.bone("arm")["scaleY"] == 1.5
    field = bar.value_boxes["shear"][0]
    field.setText("12")
    field.editingFinished.emit()
    assert win.doc.bone("arm")["shearX"] == 12
    # World axes show and take world values
    win.state.set_axes("world")
    m = world(win, "arm")
    assert [b.value() for b in bar.value_boxes["translate"]] == pytest.approx([m[4], m[5]], abs=0.01)
    bar.value_boxes["translate"][0].setText(str(round(m[4] + 15, 4)))
    bar.value_boxes["translate"][0].editingFinished.emit()
    assert world(win, "arm")[4] == pytest.approx(m[4] + 15, abs=0.01)
    assert world(win, "arm")[5] == pytest.approx(m[5], abs=0.01)


def test_translate_gizmo_follows_the_axes(win, qapp):
    st, vp = win.state, win.viewport
    st.set_tool("translate")
    for axes in ("world", "local"):
        st.set_axes(axes)
        vp.repaint()
        before = world(win, "arm")
        handle = next(geo for op, axis, kind, geo in vp._gizmo if (op, axis) == ("translate", "x"))
        start = (handle[0] + handle[1]) / 2
        drag(vp, start, start + QPointF(30, 30))
        after = world(win, "arm")
        moved = (after[4] - before[4], after[5] - before[5])
        if axes == "world":  # only along world X
            assert abs(moved[0]) > 1 and moved[1] == pytest.approx(0, abs=0.01)
        else:  # along the bone's own X axis
            own = (before[0], before[1])
            cross = moved[0] * own[1] - moved[1] * own[0]
            assert abs(cross) < 0.05 and abs(moved[0]) + abs(moved[1]) > 1


def test_compensate_keeps_children_and_images(win):
    st = win.state
    hand_before = world(win, "hand")
    cloth = win.doc.evaluator("").draw_items(0)[0].transform
    st.set_compensate("bones", True)
    st.set_compensate("images", True)
    st.apply_bone_pose("arm", rotation=-45, x=55)
    assert world(win, "hand") == pytest.approx(hand_before, abs=0.01), "the child bone stays put"
    assert win.doc.evaluator("").draw_items(0)[0].transform == pytest.approx(cloth, abs=0.01), "and the image"
    win.doc.undo()
    assert win.doc.bone("arm")["rotation"] == -90, "one undo step"
    st.set_compensate("bones", False)
    st.apply_bone_pose("arm", rotation=-45)
    assert world(win, "hand") != pytest.approx(hand_before, abs=0.01)
    st.set_mode("animate")
    assert not win.tool_panel.compensate_buttons["bones"].isEnabled(), "Setup mode only"


def test_options_hide_and_lock(win, qapp):
    st, vp = win.state, win.viewport
    m = world(win, "hand")
    tip = vp.to_screen(m[4] + 10, m[5])
    assert vp.pick(tip) == ("bone", "hand")
    st.set_view_option("bones", "select", False)
    assert vp.pick(tip) != ("bone", "hand"), "bones cannot be picked"
    st.set_view_option("bones", "select", True)
    st.set_selectable("hand", False)
    assert vp.pick(tip) != ("bone", "hand")
    st.set_selectable("hand", True)
    st.set_hidden("bone", "arm", True)  # hides the bone, its children and their slots
    assert st.bone_hidden("hand") and st.slot_hidden("sleeve")
    assert vp.pick(tip) is None
    st.set_view_option("bones", "names", True)
    vp.repaint()
    assert win.tool_panel.option_buttons[("bones", "names")].isChecked()


# --- the tree ------------------------------------------------------------------------------------

def tree_items(win):
    def walk(item):
        yield item
        for i in range(item.childCount()):
            yield from walk(item.child(i))
    t = win.tree.tree
    for i in range(t.topLevelItemCount()):
        yield from walk(t.topLevelItem(i))


def test_tree_search_filters_and_eye(win, qapp):
    tree = win.tree
    tree.search.setText("han")
    visible = {item.data(NAME, ROLE)[1] for item in tree_items(win)
               if not item.isHidden() and item.data(NAME, ROLE)[0] == "bone"}
    assert visible == {"root", "hip", "arm", "hand"}, "the match and its parents"
    assert next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("slot", "sleeve")).isHidden()
    tree.search.clear()
    tree.filter_buttons["slots"].setChecked(False)  # a slot stays as the parent of shown attachments
    assert not next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("slot", "sleeve")).isHidden()
    tree.filter_buttons["attachments"].setChecked(False)
    assert next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("slot", "sleeve")).isHidden()
    tree.filter_buttons["slots"].setChecked(True)
    tree.filter_buttons["attachments"].setChecked(True)
    item = next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("bone", "hand"))
    assert item.text(EYE) == "●"
    tree._clicked(item, EYE)
    assert ("bone", "hand") in win.state.hidden
    item = next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("bone", "hand"))
    assert item.text(EYE) == "○"


def test_tree_buttons(win, monkeypatch):
    tree, doc, st = win.tree, win.doc, win.state
    labels = [label for label, _ in tree.new_actions()]
    assert {"Bone", "Slot", "IK Constraint", "Path Constraint", "Transform Constraint", "Skin"} <= set(labels)
    st.select("slot", "sleeve")
    assert {"Skin Placeholder", "Bounding Box", "Clipping", "Path", "Point"} <= {l for l, _ in tree.new_actions()}
    st.select("bone", "hand")
    assert tree.split_button.isEnabled()
    tree.split()
    assert st.selection.name == "hand-end" and doc.bone("hand")["length"] == 10
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("hip", True)))
    st.select("bone", "hand")
    tree.set_parent()
    assert doc.bone("hand")["parent"] == "hip"
    doc.add_skin("armor")
    doc.set_edit_skin("armor")
    tree.add_to_skin()
    assert doc.skin_data("armor")["bones"] == ["hand"] and doc.bone("hand")["skin"] is True
    assert "hip" not in doc.skin_data("armor")["bones"], "parents are activated by the runtime, not listed"
    item = next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("bone", "hand"))
    assert item.text(2) == "👕"
    assert doc.validate()[0] == []


def test_skin_placeholders_in_the_tree(win):
    doc, st = win.doc, win.state
    doc.add_skin("blue")
    doc.add_skin("red")
    for skin in ("blue", "red"):
        doc.set_edit_skin(skin)
        doc.add_skin_placeholder("sleeve", "shirt")
    holder = next(i for i in tree_items(win) if i.data(NAME, ROLE)[:2] == ("placeholder", "shirt"))
    assert [holder.child(i).text(0) for i in range(holder.childCount())] == ["👕 blue", "👕 red"]
    win.tree._current_changed(holder.child(0), None)
    assert doc.edit_skin == "blue" and st.selection.name == "shirt"
    doc.set_edit_skin("default")
    with pytest.raises(Exception, match="edit a skin"):
        doc.add_skin_placeholder("sleeve", "hat")


# --- bone properties ----------------------------------------------------------------------------

def test_inherit_flags_map_to_transform_modes(win):
    doc = win.doc
    assert doc.inherit_flags("arm") == {"rotation": True, "scale": True, "reflection": True}
    assert doc.set_inherit("arm", scale=False) == "noScale"
    assert doc.set_inherit("arm", reflection=False) == "noScaleOrReflection"
    assert doc.set_inherit("arm", rotation=False, scale=True) == "noRotationOrReflection"
    assert doc.set_inherit("arm", scale=False) == "onlyTranslation"
    assert doc.set_inherit("arm", rotation=True, scale=True, reflection=True) == "normal"
    assert "transform" not in doc.bone("arm")
    win.state.select("bone", "arm")
    win.properties.rebuild()
    assert doc.validate()[0] == []
