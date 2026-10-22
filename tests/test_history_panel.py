"""History panel: the undo steps as a list, click to jump."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.history_panel import HistoryPanel  # noqa: E402
from anim2d.editor.state import EditorState  # noqa: E402


def test_history_jump():
    QApplication.instance() or QApplication([])
    doc = Document()
    for n in ("a", "b", "c"):
        doc.add_bone("root", n)
    state = EditorState(doc)
    panel = HistoryPanel(state)
    assert panel.list.count() == 4 and panel.list.currentRow() == 3
    panel.list.setCurrentRow(1)  # back to after the first step
    assert doc.has_bone("a") and not doc.has_bone("b")
    assert doc.history() == (["Create bone a"], ["Create bone b", "Create bone c"])
    assert panel.list.count() == 4 and panel.list.currentRow() == 1, "undone steps stay listed"
    panel.list.setCurrentRow(3)
    assert doc.has_bone("c")
    panel.list.setCurrentRow(0)
    doc.add_bone("root", "d")
    assert panel.list.count() == 2, "a new change drops the steps that could be redone"
