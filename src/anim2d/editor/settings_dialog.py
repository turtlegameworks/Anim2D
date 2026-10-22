"""File > Settings (F12): editor preferences kept between sessions."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import document
from .form_widgets import SliderField, section


@dataclass
class EditorSettings:
    recent_count: int = 10  # recent projects listed in File > Recent Projects
    undo_levels: int = 300
    default_fps: int = 30  # of new skeletons
    autosave_minutes: int = 0  # 0: off; otherwise a <project>.anim2d.autosave copy is written

    @classmethod
    def load(cls, store: QSettings) -> "EditorSettings":
        s = cls()
        for name in s.__dataclass_fields__:
            try:
                setattr(s, name, int(store.value(f"settings/{name}", getattr(s, name))))
            except (TypeError, ValueError):
                pass
        return s

    def save(self, store: QSettings) -> None:
        for name in self.__dataclass_fields__:
            store.setValue(f"settings/{name}", getattr(self, name))

    def apply(self) -> None:
        document.UNDO_LIMIT = max(10, self.undo_levels)


class SettingsDialog(QDialog):
    def __init__(self, settings: EditorSettings, parent=None, on_clear_recent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.recent = SliderField(1, 30, settings.recent_count)
        self.undo = SliderField(10, 2000, settings.undo_levels)
        self.fps = SliderField(1, 120, settings.default_fps)
        self.autosave = SliderField(0, 60, settings.autosave_minutes, note="minutes (0 = off)")
        form = QFormLayout()
        form.addRow(section("General"))
        form.addRow("Recent projects:", self.recent)
        form.addRow("Undo levels:", self.undo)
        form.addRow("New skeleton FPS:", self.fps)
        form.addRow("Autosave every:", self.autosave)
        note = QLabel("Autosave writes <project>.anim2d.autosave next to a saved project that has changes; "
                      "the project file itself is only written when you save.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #a9a9a9;")
        form.addRow(note)
        clear = QPushButton("Clear recent projects")
        clear.setEnabled(on_clear_recent is not None)
        if on_clear_recent is not None:
            clear.clicked.connect(on_clear_recent)
        ok, cancel = QPushButton("✔ OK"), QPushButton("⊘ Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def settings(self) -> EditorSettings:
        return EditorSettings(self.recent.value(), self.undo.value(), self.fps.value(), self.autosave.value())
