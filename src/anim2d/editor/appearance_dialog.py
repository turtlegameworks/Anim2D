"""View > Appearance: interface theme, viewport background and grid, applied live while choosing."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ..preview.grid import BACKGROUND_CHOICES, CHOICES as GRID_CHOICES, CUSTOM
from .theme import THEMES


class AppearanceDialog(QDialog):
    """window: the EditorWindow (set_theme, set_background, set_grid and its state)."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.setWindowTitle("Appearance")
        self.window_ = window
        st = window.state
        self.start = (window.theme_name, dict(st.background), dict(st.grid))
        self.theme = QComboBox()
        self.theme.addItems(list(THEMES))
        self.theme.setCurrentText(window.theme_name)
        self.background = QComboBox()
        self.background.addItems(BACKGROUND_CHOICES)
        self.background.setCurrentText(st.background["name"])
        self.bg_color = QPushButton()
        self.grid = QComboBox()
        self.grid.addItems(GRID_CHOICES)
        self.grid.setCurrentText(st.grid["style"])
        self.grid_color = QPushButton()
        self.bg_custom = st.background.get("custom") or "#3a3f4b"
        self.grid_custom = st.grid.get("custom") or "#4f8fe0"
        for button, which in ((self.bg_color, "background"), (self.grid_color, "grid")):
            button.setToolTip("Choose the colour for Custom")
            button.setFixedWidth(90)
            button.clicked.connect(lambda _=False, w=which: self._pick(w))
        form = QFormLayout()
        form.addRow("Interface theme", self.theme)
        form.addRow("Viewport background", self._row(self.background, self.bg_color))
        form.addRow("Grid", self._row(self.grid, self.grid_color))
        note = QLabel("Changes show at once. The theme also applies to the FX Preset Builder the next time it opens.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f8f8f;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset = buttons.addButton("Defaults", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self._defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.theme.currentTextChanged.connect(lambda name: self.window_.set_theme(name))
        self.background.currentTextChanged.connect(lambda _: self._apply_background())
        self.grid.currentTextChanged.connect(lambda _: self._apply_grid())
        self._paint_buttons()

    @staticmethod
    def _row(combo: QComboBox, button: QPushButton) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(combo, 1)
        row.addWidget(button)
        return w

    def _paint_buttons(self) -> None:
        for button, color, combo in ((self.bg_color, self.bg_custom, self.background),
                                     (self.grid_color, self.grid_custom, self.grid)):
            text = "black" if QColor(color).lightness() > 140 else "white"
            button.setText(color)
            button.setStyleSheet(f"QPushButton {{ background: {color}; color: {text}; }}")
            button.setEnabled(combo.currentText() == CUSTOM)

    def _pick(self, which: str) -> None:
        start = QColor(self.bg_custom if which == "background" else self.grid_custom)
        color = QColorDialog.getColor(start, self, "Background colour" if which == "background" else "Grid colour")
        if not color.isValid():
            return
        if which == "background":
            self.bg_custom = color.name()
            self._apply_background()
        else:
            self.grid_custom = color.name()
            self._apply_grid()

    def _apply_background(self) -> None:
        self.window_.set_background(self.background.currentText(), self.bg_custom)
        self._paint_buttons()

    def _apply_grid(self) -> None:
        self.window_.set_grid(self.grid.currentText(), self.grid_custom)
        self._paint_buttons()

    def _defaults(self) -> None:
        from .theme import DEFAULT_THEME
        self.theme.setCurrentText(DEFAULT_THEME)
        self.background.setCurrentText("Default")
        self.grid.setCurrentText("Subtle")

    def reject(self) -> None:
        theme, background, grid = self.start
        self.window_.set_theme(theme)
        self.window_.set_background(background["name"], background.get("custom"))
        self.window_.set_grid(grid["style"], grid.get("custom"))
        super().reject()
