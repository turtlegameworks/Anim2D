"""Command search (Ctrl+Space), like Blender's F3 menu: type a few letters, pick a command, Enter runs it.

Every menu command is listed with its menu path and shortcut. Words may come in any order and match
anywhere ("exp unity" finds File > Export to Unity); names that start with what you type come first.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMenuBar, QVBoxLayout


def _clean(text: str) -> str:
    """Menu text without & accelerators, emoji/icons and the trailing ellipsis."""
    text = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
    text = "".join(ch for ch in text if ch.isascii() or ch.isalpha() or ch in "→—–·")
    return text.replace("…", "").strip()


def collect_commands(menu_bar: QMenuBar) -> list[tuple[str, str, QAction]]:
    """[(name, menu path, action)] for every enabled-or-not command in the menus (submenus included)."""
    found: list[tuple[str, str, QAction]] = []

    def walk(menu: QMenu, path: str) -> None:
        for action in menu.actions():
            if action.isSeparator() or not action.text():
                continue
            sub = action.menu()
            if sub is not None:
                walk(sub, f"{path} › {_clean(action.text())}")
            else:
                found.append((_clean(action.text()), path, action))

    for top in menu_bar.actions():
        if top.menu() is not None:
            walk(top.menu(), _clean(top.text()))
    return found


def score(query: str, name: str, path: str) -> int | None:
    """Higher is better; None when the query doesn't match."""
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    if not words:
        return 0
    hay = f"{name} {path}".lower()
    if not all(w in hay for w in words):
        return None
    lowered = name.lower()
    s = 0
    if lowered.startswith(words[0]):
        s += 100
    s += sum(30 for w in words if re.search(rf"\b{re.escape(w)}", lowered))
    s += sum(10 for w in words if w in lowered)
    return s - len(name) // 10


class CommandSearch(QDialog):
    def __init__(self, menu_bar: QMenuBar, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search commands")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.resize(560, 420)
        self.commands = collect_commands(menu_bar)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Type to search every command…  (Enter runs it, Esc closes)")
        self.results = QListWidget()
        self.results.itemActivated.connect(self._run_item)
        self.hint = QLabel()
        self.hint.setStyleSheet("color: #8f8f8f;")
        layout = QVBoxLayout(self)
        layout.addWidget(self.query)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.hint)
        self.query.textChanged.connect(self.filter)
        self.query.installEventFilter(self)
        self.ran: str | None = None
        self.filter("")

    def filter(self, text: str) -> None:
        self.results.clear()
        ranked = []
        for name, path, action in self.commands:
            s = score(text, name, path)
            if s is not None:
                ranked.append((-s, name, path, action))
        ranked.sort(key=lambda r: (r[0], r[1]))
        for _, name, path, action in ranked[:200]:
            key = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            item = QListWidgetItem(f"{name}    —  {path}" + (f"    [{key}]" if key else ""))
            item.setData(Qt.ItemDataRole.UserRole, (name, action))
            if not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(action.statusTip() or action.toolTip())
            self.results.addItem(item)
        first = next((i for i in range(self.results.count())
                      if self.results.item(i).flags() & Qt.ItemFlag.ItemIsEnabled), -1)
        self.results.setCurrentRow(first)
        self.hint.setText(f"{len(ranked)} of {len(self.commands)} commands")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - arrow keys move the list while typing
        from PySide6.QtCore import QEvent
        if obj is self.query and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self.results.keyPressEvent(event)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem()
                if item is not None:
                    self._run_item(item)
                return True
        return super().eventFilter(obj, event)

    def _run_item(self, item: QListWidgetItem) -> None:
        if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        name, action = item.data(Qt.ItemDataRole.UserRole)
        self.ran = name
        self.accept()
        action.trigger()  # checkable commands toggle, like clicking them in the menu
