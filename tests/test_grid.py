"""Viewport grid styles: five styles, a custom colour or off, in Anim2D and the FX builder."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.preview.grid import CHOICES, CUSTOM, GRID_STYLES, OFF, draw_grid, style_for  # noqa: E402


def _pixels(style) -> set[int]:
    img = QImage(200, 200, QImage.Format.Format_ARGB32)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    draw_grid(p, QTransform(1, 0, 0, -1, 100, 100), 200, 200, style)
    p.end()
    return {img.pixel(x, y) for x in range(0, 200, 3) for y in range(0, 200, 3)}


def test_styles_draw_and_differ():
    QApplication.instance() or QApplication([])
    assert len(GRID_STYLES) == 5 and CHOICES[-2:] == [CUSTOM, OFF]
    assert style_for(OFF) is None and len(_pixels(None)) == 1, "off draws nothing"
    drawn = {name: frozenset(_pixels(style)) for name, style in GRID_STYLES.items()}
    assert all(len(px) > 1 for px in drawn.values())
    assert len(set(drawn.values())) == 5, "each style looks different"
    pink = style_for(CUSTOM, "#ff3fb0")
    assert pink.minor.startswith("#ff3fb0") and _pixels(pink) != drawn["Subtle"]


def test_rigworks_view_menu_sets_and_remembers(tmp_path):
    QApplication.instance() or QApplication([])
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = EditorWindow(Document(), settings_store=store)
    w.set_grid("Blueprint")
    assert w.state.grid["style"] == "Blueprint" and w.preview.state.grid["style"] == "Blueprint"
    assert w.grid_actions["Blueprint"].isChecked()
    w.set_grid(CUSTOM, "#00ff88")
    w.close()
    again = EditorWindow(Document(), settings_store=store)
    assert again.state.grid == {"style": CUSTOM, "custom": "#00ff88"}
    again.close()


def test_fx_builder_preview_grid():
    QApplication.instance() or QApplication([])
    from anim2d.ui.preview_widget import PreviewPanel
    panel = PreviewPanel()
    seen = []
    panel.grid_changed.connect(lambda style, color: seen.append((style, color)))
    panel.set_grid("Dots")
    assert panel.canvas.grid == "Dots" and panel.grid_combo.currentText() == "Dots"
    panel.set_grid(CUSTOM, "#123456")
    assert seen[-1] == (CUSTOM, "#123456") and panel.grid_combo.currentText().startswith("Custom")
    panel.canvas.resize(200, 200)
    panel.canvas.grab()  # paints without error


def test_themes_backgrounds_and_appearance_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    from anim2d.editor.settings_dialog import SettingsDialog
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    from anim2d.editor.theme import THEMES, palette, stylesheet
    from anim2d.preview.grid import BACKGROUND_CHOICES, background_for
    assert len(THEMES) == 5 and all(t.accent in stylesheet(t) for t in THEMES.values())
    assert palette(THEMES["Light"]).window().color().lightness() > 200
    assert background_for("Charcoal") == "#17181c" and background_for("Default") is None
    assert background_for(CUSTOM, "#112233") == "#112233" and "Checker" in BACKGROUND_CHOICES
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = EditorWindow(Document(), settings_store=store)
    w.set_theme("Ocean")
    assert app.palette().window().color().name() == THEMES["Ocean"].window
    w.set_background(CUSTOM, "#204060")
    assert w.preview.state.background == {"name": CUSTOM, "custom": "#204060"}
    dialog = SettingsDialog(w.settings, w, window=w, page="Appearance")
    assert dialog.nav.currentItem().text() == "Appearance"
    panel = dialog.appearance
    panel.theme.setCurrentText("Light")
    panel.background.setCurrentText("Light")
    panel.grid.setCurrentText("Dots")
    assert w.theme_name == "Light" and w.state.background["name"] == "Light" and w.state.grid["style"] == "Dots"
    dialog.reject()  # Cancel puts everything back
    assert w.theme_name == "Ocean" and w.state.background["name"] == CUSTOM
    w.viewport.resize(200, 150)
    w.viewport.grab()
    w.close()
    again = EditorWindow(Document(), settings_store=store)
    assert again.theme_name == "Ocean" and again.state.background == {"name": CUSTOM, "custom": "#204060"}
    again.set_theme("Studio Grey")
    again.close()
