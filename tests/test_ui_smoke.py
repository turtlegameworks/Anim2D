"""Drives the real main window offscreen: edit, validate, generate on the worker thread."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, library):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("output_dir", str(tmp_path / "out"))
    w = MainWindow(library, store)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def _wait(qapp, window):
    QThreadPool.globalInstance().waitForDone(30000)
    for _ in range(20):
        qapp.processEvents()
        if not window.busy:
            return


def test_generate_impact_without_touching_json(qapp, window, tmp_path):
    window.browser.select("impact_basic")
    qapp.processEvents()
    assert window.session.preset.id == "impact_basic"
    window.settings_panel.editors["ray_count"].changed.emit("ray_count", 12, False)
    window.settings_panel.editors["primary_color"].changed.emit("primary_color", "3aa0ffff", False)
    assert window.generate_button.isEnabled()

    window.generate()
    _wait(qapp, window)
    out = tmp_path / "out" / "fx_impact_01"
    assert (out / "fx_impact_01.json").is_file()
    assert window.open_button.isEnabled() and window.last_output == out
    assert window.session.instance_name == "fx_impact_02", "next free name is suggested after generating"


def test_invalid_values_block_generation(qapp, window):
    window.browser.select("impact_basic")
    window._value_changed("ray_count", 99, False)
    window._refresh_preview()
    assert not window.generate_button.isEnabled()
    assert "above the maximum" in window.messages.item(0).text()
    window.undo()
    assert window.generate_button.isEnabled()


def test_existing_output_name_is_flagged(qapp, window, tmp_path):
    window.browser.select("impact_basic")
    (tmp_path / "out" / window.session.instance_name).mkdir(parents=True)
    window._refresh_preview()
    assert not window.generate_button.isEnabled()
    assert "already exists" in window.messages.item(0).text()
    window._next_free_name()
    assert window.generate_button.isEnabled()


def test_import_template_from_spine_export(qapp, window, tmp_path):
    from anim2d.ui.template_dialog import TemplateImportDialog

    window.browser.select("impact_basic")
    window.generate()
    _wait(qapp, window)
    export_json = window.last_output / "fx_impact_01.json"  # a package is laid out like a Spine export

    dialog = TemplateImportDialog(window.library, window.user_root)
    dialog.set_json(export_json)
    assert not dialog.register_button.isEnabled() or dialog.name_edit.text()
    dialog.name_edit.setText("Blue Hit")
    dialog._name_edited("Blue Hit")
    assert dialog.id_edit.text() == "blue_hit"
    assert dialog.register_button.isEnabled(), [dialog.messages.item(i).text() for i in range(dialog.messages.count())]
    assert dialog.roles()["ray_01_slot"] == "primary"
    dialog._register()
    assert dialog.registered_id == "blue_hit"

    window.reload_library(dialog.registered_id)
    assert window.session.preset.id == "blue_hit" and window.session.preset.source == "user"
    assert "template" in window.browser.list.currentItem().toolTip()
    window._value_changed("primary_color", "3aa0ffff", False)
    assert window.generate_button.isEnabled()
    window.generate()
    _wait(qapp, window)
    assert (window.last_output / f"{window.last_output.name}.json").is_file()

    again = TemplateImportDialog(window.library, window.user_root)
    again.set_json(export_json)
    again.name_edit.setText("Blue Hit")
    again._name_edited("Blue Hit")
    assert not again.register_button.isEnabled(), "existing id needs Replace"
    again.replace_check.setChecked(True)
    assert again.register_button.isEnabled()


def test_switching_presets_keeps_each_session(qapp, window):
    window.browser.select("impact_basic")
    window._value_changed("ray_count", 4, False)
    window.browser.select("minimal_pop")
    assert window.session.preset.id == "minimal_pop"
    window.browser.select("impact_basic")
    assert window.session.get("ray_count") == 4
