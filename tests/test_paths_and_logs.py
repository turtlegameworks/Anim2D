"""Where things are kept (anim2d.paths), moving the pre-0.17.1 folders, the log and the error box, and the
'Updated to' note. conftest points Documents and LOCALAPPDATA at tmp_path."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from anim2d import logs, paths
from anim2d.resources import VERSION


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def docs(tmp_path):
    return tmp_path / "Documents"


def test_layout(docs, tmp_path):
    assert paths.projects_dir() == docs / "Anim2D" / "Projects"
    assert paths.templates_dir() == docs / "Anim2D" / "FX Templates"
    assert paths.fx_output_dir() == docs / "Anim2D" / "FX Output"
    assert paths.updates_dir() == tmp_path / "LocalAppData" / "Anim2D" / "updates"
    assert paths.logs_dir() == tmp_path / "LocalAppData" / "Anim2D" / "logs"


def test_documents_comes_from_windows():
    found = paths.known_documents()  # the real lookup: conftest only replaces documents()
    assert found is not None and found.is_dir()


def test_old_folders_move_once_without_overwriting(docs):
    old_templates, old_output = docs / "Anim2D Templates", docs / "Anim2D FX Output"
    (old_templates / "my_hit").mkdir(parents=True)
    (old_templates / "my_hit" / "preset.json").write_text("{}")
    (old_output / "fx_01").mkdir(parents=True)
    moved = paths.migrate()
    assert {(o.name, n.name) for o, n in moved} == {("Anim2D Templates", "FX Templates"),
                                                    ("Anim2D FX Output", "FX Output")}
    assert (paths.templates_dir() / "my_hit" / "preset.json").is_file() and not old_templates.exists()
    assert (paths.fx_output_dir() / "fx_01").is_dir()
    assert paths.migrate() == [], "only once"


def test_an_existing_new_folder_is_never_merged_into(docs):
    (docs / "Anim2D Templates" / "old").mkdir(parents=True)
    (paths.templates_dir() / "new").mkdir(parents=True)
    assert paths.migrate() == []
    assert (docs / "Anim2D Templates" / "old").is_dir() and not (paths.templates_dir() / "old").exists()


def test_the_oldest_name_still_moves(docs):
    (docs / "Spine FX Templates" / "t").mkdir(parents=True)
    paths.migrate()
    assert (paths.templates_dir() / "t").is_dir()


def test_a_folder_that_cannot_move_keeps_being_used(docs, monkeypatch):
    (docs / "Anim2D Templates").mkdir(parents=True)
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(PermissionError("in use")))
    assert paths.migrate() == []
    from anim2d.presets.library import default_user_library_dir
    monkeypatch.delenv("ANIM2D_USER_LIBRARY")
    assert default_user_library_dir() == docs / "Anim2D Templates"


def test_fx_builder_writes_to_the_new_output_folder(qapp, docs, tmp_path):
    from anim2d.presets.library import PresetLibrary
    from anim2d.ui.main_window import MainWindow
    library = PresetLibrary.load(Path(__file__).resolve().parents[1] / "src" / "anim2d" / "preset_library")
    store = QSettings(str(tmp_path / "fx.ini"), QSettings.Format.IniFormat)
    store.setValue("output_dir", str(docs / "Anim2D FX Output"))  # remembered, then moved by migrate()
    w = MainWindow(library, store)
    assert w.output_dir == paths.fx_output_dir()
    w.close()
    store.setValue("output_dir", str(tmp_path / "my own folder"))
    w = MainWindow(library, store)
    assert w.output_dir == tmp_path / "my own folder", "a folder the user chose is kept"
    w.close()


def test_file_dialogs_start_in_projects(qapp, tmp_path):
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat))
    assert w._start_dir() == str(paths.projects_dir()) and paths.projects_dir().is_dir()
    w.store.setValue("last_dir", str(tmp_path))
    assert w._start_dir() == str(tmp_path)
    w.store.setValue("last_dir", str(tmp_path / "gone"))
    assert w._start_dir() == str(paths.projects_dir()), "a folder that's gone falls back"
    w.close()


# --- 'Updated to' note

@pytest.mark.parametrize("last, shown", [("0.16.0", True), (VERSION, False), ("", False), ("99.0.0", False)])
def test_updated_note(qapp, tmp_path, last, shown):
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    store = QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat)
    if last:
        store.setValue("last_version", last)
    w = EditorWindow(Document(), settings_store=store)
    assert (w.updated_button is not None) is shown and store.value("last_version") == VERSION
    if shown:
        assert f"Updated to {VERSION}" in w.updated_button.text() and "0.16.0" in w.updated_button.toolTip()
    w.close()


# --- log and error box

@pytest.fixture
def fresh_log(monkeypatch):
    monkeypatch.setattr(logs, "_installed", False)
    before = list(logging.getLogger().handlers)
    yield
    for handler in logging.getLogger().handlers:
        if handler not in before:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_log_file(qapp, fresh_log):
    path = logs.setup("editor")
    logging.getLogger("anim2d.test").warning("hello from the test")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert path == paths.logs_dir() / "anim2d.log" and f"Anim2D {VERSION} (editor" in text and "hello from" in text


def test_unexpected_error_is_logged_and_shown(qapp, fresh_log, monkeypatch):
    logs.setup("editor")
    shown = []
    monkeypatch.setattr(logs, "show_error", lambda summary, details: shown.append((summary, details)))
    try:
        raise ValueError("boom")
    except ValueError as exc:
        logs._unhandled(type(exc), exc, exc.__traceback__)
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert shown and shown[0][0] == "ValueError: boom" and "Traceback" in shown[0][1]
    assert "unexpected error" in logs.log_file().read_text(encoding="utf-8")


def test_settings_lists_the_folders(qapp, tmp_path):
    from anim2d.editor.document import Document
    from anim2d.editor.main_window import EditorWindow
    from anim2d.editor.settings_dialog import SettingsDialog
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat))
    dialog = SettingsDialog(w.settings, w, window=w)
    texts = [label.text() for label in dialog.findChildren(type(dialog.update_status))]
    assert str(paths.projects_dir()) in texts and str(paths.logs_dir()) in texts
    assert str(logs.log_file()) in dialog.system_info
    dialog.reject()
    w.close()
