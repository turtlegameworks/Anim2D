"""Graph editor and dopesheet interaction, driven with synthetic mouse and key events offscreen."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document, KeyRef  # noqa: E402
from anim2d.editor.state import EditorState  # noqa: E402
from anim2d.editor.timeline import RULER_H, ROW_H, TimelinePanel  # noqa: E402

ROTATE = KeyRef("bones", "arm", "rotate", 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    doc = Document()
    doc.add_bone("root", "arm", length=50)
    doc.add_bone("root", "leg", length=50)
    doc.add_animation("wave")
    for t, v in ((0, 0), (0.5, 60), (1.0, -20)):
        doc.set_bone_key("wave", "arm", "rotate", t, value=v)
    doc.set_bone_key("wave", "arm", "translate", 0.5, x=10, y=5)
    state = EditorState(doc)
    state.set_mode("animate")
    p = TimelinePanel(state)
    p.resize(900, 300)
    p.show()
    p.sheet.resize(900, 300)
    p.sheet.show()
    qapp.processEvents()
    return p


def mouse(widget, kind, pos: QPointF, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    types = {"press": QMouseEvent.Type.MouseButtonPress, "move": QMouseEvent.Type.MouseMove,
             "release": QMouseEvent.Type.MouseButtonRelease, "double": QMouseEvent.Type.MouseButtonDblClick}
    buttons = Qt.MouseButton.NoButton if kind == "release" else button
    event = QMouseEvent(types[kind], pos, widget.mapToGlobal(pos), button, buttons, modifiers)
    QApplication.sendEvent(widget, event)


def drag(widget, a: QPointF, b: QPointF, steps: int = 4):
    mouse(widget, "press", a)
    for i in range(1, steps + 1):
        mouse(widget, "move", a + (b - a) * (i / steps))
    mouse(widget, "release", b)


def row_y(sheet, index: int) -> float:
    return RULER_H + index * ROW_H + ROW_H / 2 - sheet.scroll_y


def test_box_select_copy_paste(panel):
    sheet, st = panel.sheet, panel.state
    rows = [r for r, _ in sheet.rows()]
    assert [(r.owner, r.timeline) for r in rows] == [("arm", "rotate"), ("arm", "translate")]
    # a box over the first row's first two keys
    a = QPointF(sheet.time_to_x(0.5) + 10, row_y(sheet, 0) - 8)  # empty space, dragged leftwards
    b = QPointF(sheet.time_to_x(0) - 6, row_y(sheet, 0) + 8)
    drag(sheet, a, b)
    assert sheet.selected == {KeyRef("bones", "arm", "rotate", 0), KeyRef("bones", "arm", "rotate", 0.5)}
    sheet.setFocus()
    QTest.keyClick(sheet, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert st.key_clipboard is not None
    st.set_time(2.0)
    st.select("bone", "leg")  # keys copied from one bone paste onto the selected bone
    QTest.keyClick(sheet, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    leg = st.doc.timeline_keys("wave", KeyRef("bones", "leg", "rotate", 0))
    assert [(k["time"], k["value"]) for k in leg] == [(2.0, 0), (2.5, 60)]
    QTest.keyClick(sheet, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(sheet.selected) == 3 + 1 + 2


def test_plain_click_in_empty_space_moves_playhead(panel):
    sheet, st = panel.sheet, panel.state
    sheet.selected = {KeyRef("bones", "arm", "rotate", 0)}
    x = sheet.time_to_x(0.3)
    mouse(sheet, "press", QPointF(x, row_y(sheet, 1)))
    mouse(sheet, "release", QPointF(x, row_y(sheet, 1)))
    assert st.time == pytest.approx(0.3, abs=1 / 30) and not sheet.selected


def test_graph_drag_value_handle_and_insert(panel, qapp):
    sheet, graph, st = panel.sheet, panel.graph, panel.state
    graph.resize(900, 400)
    graph.show()
    sheet.selected = {KeyRef("bones", "arm", "rotate", 0.5)}
    graph.repaint()
    qapp.processEvents()
    assert [(r.owner, r.timeline) for r in graph.rows()] == [("arm", "rotate")]

    # drag the key at 0.5 (value 60) up to 80
    start = QPointF(sheet.time_to_x(0.5), graph.value_to_y(60))
    drag(graph, start, QPointF(start.x(), graph.value_to_y(80)))
    assert st.doc.timeline_keys("wave", ROTATE)[1]["value"] == pytest.approx(80, abs=0.5)
    undo_depth = len(st.doc._undo)

    # make the segment a bezier, then drag its first handle
    panel._apply_curve(panel.curve_combo.findText("Bezier (edit handles in the graph)"))
    curve = st.doc.timeline_keys("wave", ROTATE)[1]["curve"]
    assert isinstance(curve, list) and len(curve) == 4
    graph.repaint()
    qapp.processEvents()
    handle = QPointF(sheet.time_to_x(curve[0]), graph.value_to_y(curve[1]))
    drag(graph, handle, QPointF(sheet.time_to_x(0.9), graph.value_to_y(80)))
    moved = st.doc.timeline_keys("wave", ROTATE)[1]["curve"]
    assert moved[0] == pytest.approx(0.9, abs=0.02) and moved[1] == pytest.approx(80, abs=1)
    assert len(st.doc._undo) == undo_depth + 2, "one undo step per drag (and one for Make bezier)"

    # double-click the curve in the first segment to add a key
    t = 0.2
    y = graph.value_to_y(graph._segment_value(ROTATE, st.doc.timeline_keys("wave", ROTATE),
                                              [k["value"] for k in st.doc.timeline_keys("wave", ROTATE)], 0, t))
    mouse(graph, "double", QPointF(sheet.time_to_x(t), y))
    times = [k["time"] for k in st.doc.timeline_keys("wave", ROTATE)]
    assert any(abs(x - t) < 1 / 30 for x in times) and len(times) == 4


def test_graph_shows_selected_bone_rows_and_legend_toggles(panel, qapp):
    graph, st = panel.graph, panel.state
    graph.resize(900, 400)
    graph.show()
    panel.sheet.selected = set()
    st.select("bone", "arm")
    graph.repaint()
    qapp.processEvents()
    assert {r.timeline for r in graph.rows()} == {"rotate", "translate"}
    names = [name for _, _, name, _, _ in graph._channels()]
    assert names == ["rotate", "x", "y"]
    rect, item = graph._legend[1]
    mouse(graph, "press", rect.center())
    mouse(graph, "release", rect.center())
    assert item in graph.hidden
    graph.fit()
    assert graph.v_min < graph.v_max


def test_mix_preview_panel(qapp):
    from anim2d.editor.main_window import EditorWindow
    from anim2d.preview.evaluator import MixEvaluator
    from PySide6.QtCore import QSettings
    doc = Document()
    doc.add_bone("root", "arm", length=50)
    for name, value in (("idle", 0), ("wave", 90)):
        doc.add_animation(name)
        doc.set_bone_key(name, "arm", "rotate", 0, value=value)
        doc.set_bone_key(name, "arm", "rotate", 1, value=value)
    import tempfile
    store = QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat)
    win = EditorWindow(doc, settings_store=store)
    panel = win.preview
    # by default it plays the animation being edited, and follows the editor's choice
    assert panel.from_combo.currentText() == "(animation being edited)"
    assert panel.state.from_animation == "idle" and panel.state.to_animation is None
    win.state.set_animation("wave")
    assert panel.state.from_animation == "wave"
    win.state.set_animation("idle")
    assert not isinstance(panel.state.evaluator(), MixEvaluator)
    panel.mix_group.setChecked(True)  # crossfade into the second animation
    assert panel.to_combo.currentText() == "wave"
    assert panel.switch_spin.value() == pytest.approx(1.0), "switch defaults to the first animation's end"
    panel.mix_spin.setValue(0.5)
    ev = panel.state.evaluator()
    assert isinstance(ev, MixEvaluator)
    panel.set_time(1.25)
    assert ev.bone_locals(1.25)["arm"][2] == pytest.approx(45)
    assert "mixing 50% into wave" in panel.time_label.text()
    panel.viewport.repaint()
    # the preview never edits: a left click does nothing to the selection or document
    undo_depth = len(doc._undo)
    mouse(panel.viewport, "press", QPointF(50, 50))
    mouse(panel.viewport, "release", QPointF(50, 50))
    assert len(doc._undo) == undo_depth and panel.state.selection.empty
    # a new animation shows up in the combos; switching documents keeps both states listening
    doc.add_animation("run")
    assert panel.to_combo.findText("run") >= 0
    other = Document()
    other.add_animation("jump")
    win.set_document(other)
    assert panel.playing_animation() == "jump"
    other.add_animation("land")
    assert panel.to_combo.findText("land") >= 0 and win.timeline.animation_combo.findText("land") >= 0
    # it plays on its own at the chosen speed
    panel.speed_spin.setValue(2.0)
    panel.set_time(0)
    panel.toggle_play() if not panel._timer.isActive() else None
    assert panel._timer.isActive()

    class Clock:  # a fixed 100 ms on the play clock, so a slow runner can't wrap the 0.5 s loop
        def elapsed(self):
            return 100

        def restart(self):
            pass

    panel._clock = Clock()
    panel._tick()
    assert panel.state.time == pytest.approx(0.2), "two times speed"
    panel.stop()
    other.dirty = False  # closing would otherwise ask to save (a modal dialog)
    win.close()
