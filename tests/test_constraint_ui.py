"""Constraint and shape editing in the editor window, driven offscreen."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.constraint_dialog import ConstraintDialog  # noqa: E402
from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    doc = Document()
    doc.add_bone("root", "arm", x=10, length=40)
    doc.add_bone("root", "aim", x=-50, y=40)
    doc.add_bone("root", "tail1", length=30)
    doc.add_bone("tail1", "tail2", x=30, length=30)
    doc.add_slot("root", "rail")
    doc.add_animation("anim")
    doc.dirty = False
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    yield w
    w.doc.dirty = False
    w.close()


def click(widget, pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier):
    for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        buttons = Qt.MouseButton.LeftButton if kind == QMouseEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                   buttons, modifiers))


def drag(widget, a: QPointF, b: QPointF):
    QApplication.sendEvent(widget, QMouseEvent(QMouseEvent.Type.MouseButtonPress, a, widget.mapToGlobal(a),
                                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                               Qt.KeyboardModifier.NoModifier))
    for i in range(1, 5):
        pos = a + (b - a) * (i / 4)
        QApplication.sendEvent(widget, QMouseEvent(QMouseEvent.Type.MouseMove, pos, widget.mapToGlobal(pos),
                                                   Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                                   Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(widget, QMouseEvent(QMouseEvent.Type.MouseButtonRelease, b, widget.mapToGlobal(b),
                                               Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier))


def test_constraint_dialog_creates_transform_and_path(win):
    doc = win.doc
    dialog = ConstraintDialog(doc, "transform", "arm")
    dialog.target.setCurrentText("aim")
    dialog.accept()
    assert dialog.created == "arm-transform" and doc.transform_constraint("arm-transform")["bones"] == ["arm"]
    win.tree.add_shape("rail", "path")
    dialog = ConstraintDialog(doc, "path", "tail1")
    assert dialog.chosen_bones() == ["tail1", "tail2"], "a path takes the whole chain"
    assert dialog.target.currentText() == "rail"
    dialog.accept()
    assert doc.path_constraint(dialog.created)["target"] == "rail"
    # the hierarchy lists every constraint in update order
    texts = []
    root = win.tree.tree.topLevelItem(1)
    for i in range(root.childCount()):
        texts.append(root.child(i).text(0))
    assert texts[0].startswith("⇉ arm-transform") and texts[1].startswith("〰 tail1-path")


def test_properties_edit_and_key_constraints(win, qapp):
    doc, st = win.doc, win.state
    name = doc.add_transform(["arm"], "aim")
    st.select("transform", name)
    qapp.processEvents()
    assert win.properties.title.text() == f"Transform constraint  {name}"
    st.set_mode("animate")
    st.set_animation("anim")
    st.select("transform", name)
    st.set_time(0.5)
    win.timeline._key()
    keys = doc.data["animations"]["anim"]["transform"][name]
    assert keys[0]["time"] == 0.5 and keys[0]["mixRotate"] == 1
    win.viewport.repaint()


def _viewport_point(win, slot: str, index: int) -> QPointF:
    item = next(i for i in win.state.evaluator().shape_items(win.state.time) if i.slot == slot)
    return win.viewport.to_screen(*item.points[index])


def test_drag_and_edit_shapes_in_viewport(win, qapp):
    doc, st, vp = win.doc, win.state, win.viewport
    win.tree.add_shape("rail", "boundingbox")
    vp.frame_all()
    qapp.processEvents()
    before = list(doc.attachments("rail")["bounds"]["vertices"])
    a = _viewport_point(win, "rail", 0)
    drag(vp, a, a + QPointF(20, 0))
    after = doc.attachments("rail")["bounds"]["vertices"]
    assert after[0] == pytest.approx(before[0] + 20 / vp.zoom, abs=0.5) and after[2:] == before[2:]
    # Alt+click on an edge adds a vertex; Delete removes the selected one
    p0, p1 = _viewport_point(win, "rail", 1), _viewport_point(win, "rail", 2)
    click(vp, (p0 + p1) / 2, Qt.KeyboardModifier.AltModifier)
    assert doc.attachments("rail")["bounds"]["vertexCount"] == 5 and st.vertex_selection == {2}
    win.delete_selected()
    assert doc.attachments("rail")["bounds"]["vertexCount"] == 4
    # a path point drags its handles along
    st.select("slot", "rail")
    path = doc.add_path_attachment("rail", [(0, 0), (60, 30), (120, 0)])
    doc.set_slot("rail", attachment=path)
    st.select("attachment", path, "rail")
    vp.repaint()
    start = list(doc.attachments("rail")[path]["vertices"])
    pt = _viewport_point(win, "rail", 4)  # the middle point
    drag(vp, pt, pt + QPointF(0, -30))
    moved = doc.attachments("rail")[path]["vertices"]
    dy = 30 / vp.zoom
    for i in (3, 4, 5):  # in handle, point, out handle
        assert moved[i * 2 + 1] == pytest.approx(start[i * 2 + 1] + dy, abs=0.5)
    assert moved[1] == start[1]
    assert doc.attachments("rail")[path]["lengths"][0] > 0
    # point attachment: drag moves it
    point = doc.add_point("rail", 10, 0)
    doc.set_slot("rail", attachment=point)
    st.select("attachment", point, "rail")
    pt = _viewport_point(win, "rail", 0)
    drag(vp, pt, pt + QPointF(10, 0))
    assert doc.attachments("rail")[point]["x"] == pytest.approx(10 + 10 / vp.zoom, abs=0.5)
    assert doc.validate()[0] == []


def _tip(doc, bone, anim="", t=0.0):
    from anim2d.editor import affine
    from anim2d.preview.evaluator import PoseEvaluator
    m = PoseEvaluator(doc.data, anim).bone_world(t)[bone]
    return affine.apply(m, doc.bone(bone).get("length", 0), 0)


@pytest.mark.parametrize("mirrored", [False, True])
def test_pose_chain_reaches_the_target(mirrored):
    from anim2d.editor.pose import pose_chain
    from anim2d.preview.evaluator import PoseEvaluator
    doc = Document()
    doc.add_bone("root", "hip", y=10)
    if mirrored:
        doc.set_bone("hip", scaleX=-1)
    doc.add_bone("hip", "thigh", rotation=-80, length=50)
    doc.add_bone("thigh", "shin", x=50, rotation=10, length=45)
    target = (30.0, -60.0)
    rotations = pose_chain(doc, PoseEvaluator(doc.data, ""), 0, "shin", target)
    assert set(rotations) == {"shin", "thigh"}
    for bone, rotation in rotations.items():
        doc.set_bone(bone, rotation=rotation)
    assert _tip(doc, "shin") == pytest.approx(target, abs=0.05)
    # one bone only: it aims at the target
    only = pose_chain(doc, PoseEvaluator(doc.data, ""), 0, "shin", (200, 200), chain_length=1)
    assert set(only) == {"shin"}


def test_pose_tool_drag_keys_rotations(win, qapp):
    doc, st, vp = win.doc, win.state, win.viewport
    st.set_mode("animate")
    st.set_animation("anim")
    st.set_time(0.25)
    st.set_tool("pose")
    st.select("bone", "tail2")
    vp.frame_all()
    qapp.processEvents()
    start = vp.to_screen(*_tip(doc, "tail2", "anim", 0.25))
    goal = vp.to_screen(40, 25)  # within reach of the 30 + 30 chain from the origin
    vp.begin_drag(*vp.to_world(start))
    vp.drag_to(*vp.to_world(goal))
    vp.end_drag()
    reached = vp.to_screen(*_tip(doc, "tail2", "anim", 0.25))
    assert abs(reached.x() - goal.x()) + abs(reached.y() - goal.y()) < 1.5
    bones = doc.data["animations"]["anim"]["bones"]
    assert bones["tail1"]["rotate"][0]["time"] == 0.25 and bones["tail2"]["rotate"][0]["time"] == 0.25
    assert "translate" not in bones.get("tail2", {}), "the Pose tool only rotates"
    doc.undo()
    assert "tail1" not in doc.data["animations"]["anim"].get("bones", {}), "one undo step per drag"
