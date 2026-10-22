"""History: every undo step in a list; click one to go back (or forward) to it."""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .state import EditorState

START = "Opened / created"


class HistoryPanel(QWidget):
    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self._filling = False
        self.list = QListWidget()
        self.list.setToolTip("Click a step to undo back to it; steps below it can be redone until you change something")
        self.list.currentRowChanged.connect(self._picked)
        self.info = QLabel()
        self.info.setStyleSheet("color: #a9a9a9;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.list)
        layout.addWidget(self.info)
        state.document_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        done, undone = self.state.doc.history()
        self._filling = True
        self.list.clear()
        for i, label in enumerate([START, *done, *undone]):
            item = QListWidgetItem(label)
            if i > len(done):
                item.setForeground(QBrush(QColor("#7a7a7a")))
            self.list.addItem(item)
        self.list.setCurrentRow(len(done))
        self.list.scrollToItem(self.list.currentItem())
        self._filling = False
        self.info.setText(f"{len(done)} step(s) done · {len(undone)} to redo")

    def _picked(self, row: int) -> None:
        if self._filling or row < 0:
            return
        self.state.doc.go_to_history(row)
