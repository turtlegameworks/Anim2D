"""Space deselects; windows can be made small or big."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402


def _window():
    QApplication.instance() or QApplication([])
    doc = Document()
    doc.add_bone("root", "arm")
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    return w


def test_space_deselects():
    w = _window()
    w.state.select("bone", "arm")
    w.viewport.setFocus()
    QTest.keyClick(w.viewport, Qt.Key.Key_Space)
    assert w.state.selection.kind is None
    w.doc.dirty = False
    w.close()


def test_windows_resize_freely():
    w = _window()
    w.state.set_mode("animate")
    QApplication.processEvents()
    for dock in (w.timeline_dock, w.dopesheet_dock, w.graph_dock, w.preview_dock, w.tree_dock):
        assert dock.minimumSizeHint().width() < 200, dock.windowTitle()
    assert w.minimumSizeHint().width() < 1200
    w.timeline_dock.setFloating(True)
    w.timeline_dock.resize(420, 260)
    QApplication.processEvents()
    assert w.timeline_dock.size().toTuple() == (420, 260)
    w.doc.dirty = False
    w.close()


def test_space_still_types_in_text_fields():
    from PySide6.QtWidgets import QLineEdit
    w = _window()
    w.state.select("bone", "arm")
    field = w.tree.findChild(QLineEdit)
    field.setFocus()
    QTest.keyClicks(field, "a b")
    assert field.text() == "a b" and w.state.selection.name == "arm"
    w.doc.dirty = False
    w.close()
