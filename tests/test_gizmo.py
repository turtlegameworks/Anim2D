"""Viewport gizmos: dragging a handle changes only what it controls (move/rotate/scale/shear)."""

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


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    doc = Document()
    doc.add_bone("root", "arm", x=20, y=10, rotation=30, length=80)
    doc.add_animation("anim")
    doc.dirty = False
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1300, 900)
    w.show()
    w.state.select("bone", "arm")
    w.viewport.frame_all()
    qapp.processEvents()
    yield w
    w.doc.dirty = False
    w.close()


def drag(widget, a: QPointF, b: QPointF):
    events = [(QMouseEvent.Type.MouseButtonPress, a, Qt.MouseButton.LeftButton)]
    events += [(QMouseEvent.Type.MouseMove, a + (b - a) * (i / 4), Qt.MouseButton.LeftButton) for i in range(1, 5)]
    events.append((QMouseEvent.Type.MouseButtonRelease, b, Qt.MouseButton.NoButton))
    for kind, pos, buttons in events:
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                   buttons, Qt.KeyboardModifier.NoModifier))


def handle(win, op, axis):
    win.viewport.repaint()
    for o, a, kind, geo in win.viewport._gizmo:
        if (o, a) == (op, axis):
            if kind == "box":
                return geo[0]
            if kind == "segment":
                return (geo[0] + geo[1]) / 2
            centre, radius = geo
            return centre + QPointF(radius, 0)
    raise AssertionError(f"no {op}/{axis} handle in {[(o, a) for o, a, *_ in win.viewport._gizmo]}")


def bone(win):
    return win.doc.bone("arm")


def test_select_tool_shows_move_rotate_scale(win):
    win.viewport.repaint()
    ops = {(o, a) for o, a, *_ in win.viewport._gizmo}
    assert {("translate", "x"), ("translate", "y"), ("translate", "xy"), ("rotate", None),
            ("scale", "x"), ("scale", "y")} <= ops
    win.state.set_tool("shear")
    win.viewport.repaint()
    assert {(o, a) for o, a, *_ in win.viewport._gizmo} == {("shear", "x"), ("shear", "y")}
    win.state.select(None)
    win.viewport.repaint()
    assert win.viewport._gizmo == [], "no gizmo without a selection"


def test_translate_axis_handles(win):
    start = dict(bone(win))
    h = handle(win, "translate", "x")
    zoom = win.viewport.zoom
    drag(win.viewport, h, h + QPointF(40, 25))  # only the parent (root) X axis moves
    assert bone(win)["x"] == pytest.approx(start["x"] + 40 / zoom, abs=0.05) and bone(win)["y"] == start["y"]
    h = handle(win, "translate", "y")
    drag(win.viewport, h, h + QPointF(30, -40))  # screen up is world +y
    assert bone(win)["y"] == pytest.approx(start["y"] + 40 / zoom, abs=0.05)
    assert bone(win)["x"] == pytest.approx(start["x"] + 40 / zoom, abs=0.05)


def test_rotate_ring_and_scale_handles(win):
    h = handle(win, "rotate", None)
    drag(win.viewport, h, h + QPointF(0, -60))
    assert bone(win)["rotation"] != 30 and bone(win).get("scaleX", 1) == 1
    win.state.set_tool("scale")
    h = handle(win, "scale", "y")
    origin = win.viewport.gizmo_frame()[0]
    drag(win.viewport, h, origin + (h - origin) * 2)
    assert bone(win).get("scaleY") == pytest.approx(2, abs=0.05) and bone(win).get("scaleX", 1) == 1
    h = handle(win, "scale", "xy")
    drag(win.viewport, h + QPointF(3, 3), h + QPointF(40, 40))
    assert bone(win).get("scaleX", 1) != 1


def test_shear_handles_set_setup_and_keys(win):
    win.state.set_tool("shear")
    h = handle(win, "shear", "y")
    origin = win.viewport.gizmo_frame()[0]
    drag(win.viewport, h, origin + QPointF(h.y() - origin.y(), -(h.x() - origin.x())))  # a quarter turn
    assert abs(bone(win).get("shearY", 0)) == pytest.approx(90, abs=3) and bone(win).get("shearX", 0) == 0
    win.state.set_mode("animate")
    win.state.set_animation("anim")
    win.state.set_time(0.5)
    win.viewport.repaint()
    h = handle(win, "shear", "x")
    drag(win.viewport, h, h + QPointF(0, -30))
    keys = win.doc.data["animations"]["anim"]["bones"]["arm"]["shear"]
    assert keys[0]["time"] == 0.5 and keys[0]["x"] != 0
    assert bone(win).get("shearX", 0) == 0, "Animate mode keys; the setup pose stays"
    locals_ = win.state.evaluator().bone_locals(0.5)["arm"]
    assert locals_[5] == pytest.approx(keys[0]["x"], abs=1e-3)


def test_setup_and_animate_have_their_own_interface(win, qapp):
    assert win.mode_switch.setup_button.isChecked()
    assert not win.timeline_dock.isVisible() and not win.preview_dock.isVisible(), "Setup: no timeline"
    assert not any(k.isVisible() for k in win.tool_panel.key_buttons.values())
    win.mode_switch.animate_button.click()
    qapp.processEvents()
    assert win.state.mode == "animate" and win.mode_switch.animate_button.isChecked()
    assert win.timeline_dock.isVisible() and win.preview_dock.isVisible(), "Animate: timeline and preview"
    assert all(k.isVisible() for k in win.tool_panel.key_buttons.values())
    # the user's layout per mode is remembered
    win.preview_dock.hide()
    win.toggle_mode()
    qapp.processEvents()
    assert win.state.mode == "setup" and not win.timeline_dock.isVisible()
    win.toggle_mode()
    qapp.processEvents()
    assert win.timeline_dock.isVisible() and not win.preview_dock.isVisible()


def test_key_buttons_show_and_set_keys(win, qapp):
    win.state.set_mode("animate")
    win.state.set_animation("anim")
    panel = win.tool_panel
    assert panel.key_state("rotate") == "none"
    win.state.set_time(0.5)
    panel.key_buttons["rotate"].click()
    assert panel.key_state("rotate") == "here"
    assert "#4cd964" in panel.key_buttons["rotate"].styleSheet(), "green: keyed at this frame"
    win.state.set_time(0.8)
    assert panel.key_state("rotate") == "elsewhere" and panel.key_state("shear") == "none"
    panel.key_buttons["shear"].click()
    assert win.doc.data["animations"]["anim"]["bones"]["arm"]["shear"][0]["time"] == 0.8
    win.state.set_tool("rotate")
    assert panel.tool_buttons["rotate"].isChecked()
