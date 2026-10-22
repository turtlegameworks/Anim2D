"""Anim2D's interface themes: colour sets turned into a Qt palette and stylesheet (our own colours
and drawing; no third-party artwork). "Studio Grey" is the original look, mid greys with a blue
accent in the style of 2D animation tools. The choice is kept in Anim2D's settings, and the FX
Preset Builder uses the same one.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..resources import settings as app_settings


@dataclass(frozen=True)
class Theme:
    window: str
    panel: str  # trees and lists
    base: str  # text fields
    button: str
    hover: str
    border: str
    title: str  # dock title bars and headers
    text: str
    muted: str  # disabled text, placeholders
    accent: str
    accent_text: str  # selection highlight and group titles
    alternate: str


THEMES: dict[str, Theme] = {
    "Studio Grey": Theme("#3c3c3c", "#444444", "#353535", "#4d4d4d", "#5a5a5a", "#2c2c2c", "#2f2f2f", "#dedede",
                         "#7a7a7a", "#2f8bd8", "#7fc4ff", "#474747"),
    "Midnight": Theme("#2c2f37", "#262931", "#24262d", "#353944", "#414654", "#1d1f25", "#22252c", "#e6e8ee",
                      "#6d717c", "#2f7de1", "#8cc2ff", "#2f333c"),
    "Charcoal": Theme("#1f2023", "#18191b", "#141517", "#2a2b2f", "#36383d", "#0e0f10", "#141517", "#d7d9de",
                      "#64666d", "#e0782f", "#ffb07a", "#1d1e21"),
    "Light": Theme("#eceef2", "#ffffff", "#ffffff", "#dfe2e8", "#d0d4dc", "#b9bec8", "#d7dbe2", "#1d2026",
                   "#8a8f99", "#2f7de1", "#1d5fb8", "#f3f5f8"),
    "Ocean": Theme("#1f3340", "#1a2c37", "#162530", "#28414f", "#325061", "#10202a", "#16262f", "#e2eef4",
                   "#6c8794", "#22b3a6", "#7fe8dc", "#203743"),
}
DEFAULT_THEME = "Studio Grey"


def stylesheet(t: Theme) -> str:
    return f"""
QMainWindow, QDialog {{ background: {t.window}; }}
QDockWidget {{ color: {t.text}; }}
QDockWidget::title {{ background: {t.title}; padding: 4px 6px; }}
QToolBar {{ background: {t.window}; border: none; spacing: 2px; }}
QToolButton {{ background: {t.button}; border: 1px solid {t.border}; border-radius: 3px; padding: 3px 8px; color: {t.text}; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton:checked {{ background: {t.accent}; color: white; }}
QPushButton {{ background: {t.button}; border: 1px solid {t.border}; border-radius: 3px; padding: 3px 10px; color: {t.text}; }}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:checked {{ background: {t.accent}; color: white; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.muted}; }}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{ background: {t.base}; border: 1px solid {t.border}; border-radius: 2px;
    padding: 2px 4px; color: {t.text}; selection-background-color: {t.accent}; }}
QTreeWidget, QListWidget, QTableWidget {{ background: {t.panel}; alternate-background-color: {t.alternate};
    border: none; color: {t.text}; }}
QTreeWidget::item:selected {{ background: {t.accent}; color: white; }}
QHeaderView::section {{ background: {t.title}; color: {t.text}; border: none; padding: 3px; }}
QMenuBar {{ background: {t.window}; color: {t.text}; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.accent}; color: white; }}
QGroupBox {{ color: {t.text}; border: 1px solid {t.border}; border-radius: 3px; margin-top: 8px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; }}
QFrame#ToolGroup {{ background: {t.title}; border: 1px solid {t.border}; border-radius: 4px; }}
QLabel#GroupTitle {{ color: {t.accent_text}; font-size: 11px; }}
QLabel#SelectionTitle {{ color: {t.accent_text}; font-size: 13px; }}
QStatusBar {{ color: {t.text}; }}
QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; }}
"""


def palette(t: Theme) -> QPalette:
    p = QPalette()
    for role, color in ((QPalette.ColorRole.Window, t.window), (QPalette.ColorRole.WindowText, t.text),
                        (QPalette.ColorRole.Base, t.base), (QPalette.ColorRole.AlternateBase, t.alternate),
                        (QPalette.ColorRole.Text, t.text), (QPalette.ColorRole.Button, t.button),
                        (QPalette.ColorRole.ButtonText, t.text), (QPalette.ColorRole.ToolTipBase, t.panel),
                        (QPalette.ColorRole.ToolTipText, t.text), (QPalette.ColorRole.Highlight, t.accent),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.Mid, t.border),
                        (QPalette.ColorRole.PlaceholderText, t.muted)):
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted))
    return p


def saved_theme(default: str = DEFAULT_THEME) -> str:
    name = str(app_settings("editor").value("theme", default))
    return name if name in THEMES else default


def apply_theme(app: QApplication, name: str | None = None) -> str:
    """Apply a theme (default: the saved one). Returns its name."""
    name = name if name in THEMES else saved_theme()
    t = THEMES[name]
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t))
    return name


def is_light(name: str) -> bool:
    return QColor(THEMES.get(name, THEMES[DEFAULT_THEME]).window).lightness() > 128
