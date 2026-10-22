"""Edit > Keyboard Shortcuts: change any command's key; the choices are kept between sessions."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

PREFIX = "shortcuts/"


class ShortcutRegistry:
    """Every command with a keyboard shortcut: id -> (action, default key)."""

    def __init__(self, store: QSettings):
        self.store = store
        self.entries: dict[str, tuple[QAction, str]] = {}

    def add(self, command: str, action: QAction, default: str) -> None:
        self.entries[command] = (action, default)
        saved = self.store.value(PREFIX + command)
        action.setShortcut(QKeySequence(str(saved) if saved is not None else default))

    def key(self, command: str) -> str:
        return self.entries[command][0].shortcut().toString(QKeySequence.SequenceFormat.PortableText)

    def set(self, command: str, key: str) -> None:
        action, default = self.entries[command]
        action.setShortcut(QKeySequence(key))
        if key == default:
            self.store.remove(PREFIX + command)
        else:
            self.store.setValue(PREFIX + command, key)

    def conflicts(self, keys: dict[str, str]) -> dict[str, list[str]]:
        """Keys used by more than one command."""
        used: dict[str, list[str]] = {}
        for command, key in keys.items():
            if key:
                used.setdefault(key, []).append(command)
        return {k: v for k, v in used.items() if len(v) > 1}


class ShortcutsDialog(QDialog):
    def __init__(self, registry: ShortcutRegistry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(640, 620)
        self.registry = registry
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands")
        self.search.textChanged.connect(self._filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Command", "Shortcut", "Default"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 120)
        self.editors: dict[str, QKeySequenceEdit] = {}
        for command in sorted(registry.entries, key=str.lower):
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(command)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name)
            editor = QKeySequenceEdit(QKeySequence(registry.key(command)))
            editor.setMaximumSequenceLength(1)
            editor.setClearButtonEnabled(True)
            self.table.setCellWidget(row, 1, editor)
            default = QTableWidgetItem(registry.entries[command][1] or "—")
            default.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 2, default)
            self.editors[command] = editor
        note = QLabel("Click a shortcut and press the new key. Clear it to leave the command without a key.")
        note.setWordWrap(True)
        reset, ok, cancel = QPushButton("↺ Reset all"), QPushButton("✔ OK"), QPushButton("⊘ Cancel")
        reset.clicked.connect(self.reset_all)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        ok.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        layout.addLayout(buttons)

    def keys(self) -> dict[str, str]:
        return {c: e.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for c, e in self.editors.items()}

    def _filter(self, text: str) -> None:
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, text.lower() not in self.table.item(row, 0).text().lower())

    def reset_all(self) -> None:
        for command, editor in self.editors.items():
            editor.setKeySequence(QKeySequence(self.registry.entries[command][1]))

    def accept(self) -> None:
        keys = self.keys()
        clashes = self.registry.conflicts(keys)
        if clashes:
            text = "\n".join(f"{k}: {', '.join(v)}" for k, v in clashes.items())
            QMessageBox.warning(self, "Keyboard Shortcuts", f"These keys are used more than once:\n\n{text}")
            return
        for command, key in keys.items():
            self.registry.set(command, key)
        super().accept()
