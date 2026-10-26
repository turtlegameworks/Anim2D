"""Settings dialog pages and the editor's update flow (check, notify, skip, install hand-off), with the
network, modal dialogs and the installer launch replaced."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtWidgets import QApplication

from anim2d import updates
from anim2d.editor import update_ui
from anim2d.editor.document import Document
from anim2d.editor.main_window import EditorWindow
from anim2d.editor.settings_dialog import PAGES, EditorSettings, SettingsDialog
from anim2d.resources import VERSION
from anim2d.updates import Asset, Release


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    w = EditorWindow(Document(), settings_store=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    yield w
    w.doc.dirty = False
    w.close()


def _release(version="99.0.0") -> Release:
    return Release(version=version, tag=f"v{version}", title=f"Anim2D {version}", notes="* new things",
                   page="https://example.test/release", published="2026-11-01T10:00:00Z",
                   assets={f"Anim2D-Setup-{version}.exe": Asset(f"Anim2D-Setup-{version}.exe",
                                                               "https://example.test/s.exe", 10),
                           f"Anim2D-Portable-{version}.exe": Asset(f"Anim2D-Portable-{version}.exe",
                                                                  "https://example.test/p.exe", 10),
                           "SHA256SUMS.txt": Asset("SHA256SUMS.txt", "https://example.test/sums", 10)})


def _wait(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        QThreadPool.globalInstance().waitForDone(20)
        QApplication.processEvents()
    assert condition()


@pytest.fixture
def dialogs(monkeypatch):
    """Record modal dialogs instead of showing them."""
    shown = []
    monkeypatch.setattr(update_ui.QMessageBox, "information", lambda *a, **k: shown.append(("information", a[2])))
    monkeypatch.setattr(update_ui.QMessageBox, "warning", lambda *a, **k: shown.append(("warning", a[2])))
    monkeypatch.setattr(update_ui.UpdateDialog, "exec", lambda self: shown.append(("update", self.release.version)))
    return shown


# --- the dialog

def test_settings_without_a_window_has_general_and_about(qapp):
    dialog = SettingsDialog(EditorSettings(undo_levels=77))
    assert dialog.page_names() == ["General", "About"]
    assert dialog.settings().undo_levels == 77 and VERSION in dialog.system_info


def test_every_page_with_a_window(window):
    dialog = SettingsDialog(window.settings, window, window.clear_recent, window=window, page="Plugins")
    assert dialog.page_names() == list(PAGES) and dialog.nav.currentItem().text() == "Plugins"
    dialog.show_page("nonsense")
    assert dialog.nav.currentItem().text() == "General"
    dialog.reject()


def test_view_appearance_and_help_about_open_their_pages(window, monkeypatch):
    opened = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened.append(self.nav.currentItem().text()) or 0)
    window.appearance()
    window.open_settings("About")
    assert opened == ["Appearance", "About"]
    help_menu = window.menuBar().actions()[-1].menu()
    names = [a.text().replace("&", "") for a in help_menu.actions() if a.text()]
    assert {"Check for Updates…", "Report a Bug…", "About Anim2D"} <= set(names)


def test_auto_check_setting_is_saved_on_ok(window):
    dialog = SettingsDialog(window.settings, window, window=window, page="Updates")
    assert dialog.auto_check.isChecked()
    dialog.auto_check.setChecked(False)
    dialog.accept()
    assert window.updates.auto_check is False


# --- checking

def test_no_automatic_check_from_source(window, monkeypatch):
    scheduled = []
    monkeypatch.setattr(update_ui.QTimer, "singleShot", lambda *a: scheduled.append(a))
    window.updates.schedule_startup_check()
    assert not scheduled, "running from source never goes to the network by itself"
    monkeypatch.setattr(updates, "install_kind", lambda: "installed")
    window.updates.schedule_startup_check()
    assert len(scheduled) == 1
    window.store.setValue("updates/last_check", update_ui.datetime.now(update_ui.timezone.utc).isoformat())
    window.updates.schedule_startup_check()
    assert len(scheduled) == 1, "at most once a day"


def test_newer_release_is_offered_and_shown_in_the_status_bar(window, dialogs, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest", lambda: _release())
    window.updates.check(manual=False)
    _wait(lambda: dialogs)
    assert dialogs == [("update", "99.0.0")]
    assert not window.update_button.isHidden() and "99.0.0" in window.update_button.text()
    assert "99.0.0 is available" in window.updates.status()


def test_skipped_version_is_not_offered_again_automatically(window, dialogs, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest", lambda: _release())
    window.updates.skipped = "99.0.0"
    window.updates.check(manual=False)
    _wait(lambda: window.updates.release is not None and not window.updates.checking)
    assert dialogs == [] and not window.update_button.isHidden(), "still shown in the status bar"
    window.updates.check(manual=True)  # asking explicitly shows it anyway
    _wait(lambda: dialogs)
    assert dialogs == [("update", "99.0.0")]


def test_up_to_date_only_says_so_when_asked(window, dialogs, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest", lambda: _release(VERSION))
    window.updates.check(manual=False)
    _wait(lambda: not window.updates.checking)
    assert dialogs == [] and "latest version" in window.updates.status()
    window.updates.check(manual=True)
    _wait(lambda: dialogs)
    assert dialogs[0][0] == "information" and "latest version" in dialogs[0][1]


def test_failed_check_is_quiet_unless_asked(window, dialogs, monkeypatch):
    def offline():
        raise updates.UpdateError("Can't reach GitHub (offline).")
    monkeypatch.setattr(updates, "fetch_latest", offline)
    window.updates.check(manual=False)
    _wait(lambda: not window.updates.checking)
    assert dialogs == [] and "offline" in window.updates.status()
    window.updates.check(manual=True)
    _wait(lambda: dialogs)
    assert dialogs[0][0] == "warning" and "offline" in dialogs[0][1]


def test_a_bug_in_the_check_does_not_escape(window, dialogs, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest", lambda: 1 / 0)
    window.updates.check(manual=True)
    _wait(lambda: dialogs)
    assert dialogs[0][0] == "warning" and "ZeroDivisionError" in dialogs[0][1]


# --- the dialog's buttons and installing

@pytest.mark.parametrize("kind, main", [("installed", "Install update"), ("portable", "Download"),
                                         ("source", "Open release page")])
def test_update_dialog_fits_this_copy(window, monkeypatch, kind, main):
    monkeypatch.setattr(updates, "install_kind", lambda: kind)
    dialog = update_ui.UpdateDialog(window.updates, _release(), window)
    texts = [b.text() for b in dialog.findChildren(update_ui.QPushButton)]
    assert main in texts and "Skip this version" in texts and "Remind me later" in texts


def test_skip_this_version(window):
    dialog = update_ui.UpdateDialog(window.updates, _release(), window)
    dialog._skip()
    assert window.updates.skipped == "99.0.0"


def test_install_downloads_then_hands_over_and_closes(window, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "install_kind", lambda: "installed")
    monkeypatch.setattr(updates, "install_dir", lambda: tmp_path / "Anim2D")
    monkeypatch.setattr(updates, "fetch_checksums", lambda r: {"Anim2D-Setup-99.0.0.exe": "ab" * 32})
    downloaded = tmp_path / "Anim2D-Setup-99.0.0.exe"
    monkeypatch.setattr(updates, "download", lambda asset, sha, folder, report: (report(10, 10), downloaded)[1])
    monkeypatch.setattr(updates, "clean_cache", lambda keep=None: None)
    started, closed = [], []
    monkeypatch.setattr(updates, "start_installer", lambda path, target, pid: started.append((path, target)))
    monkeypatch.setattr(QApplication, "closeAllWindows", lambda *a: closed.append(True))
    window.doc.dirty = True
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window.updates.install(_release())
    _wait(lambda: started)
    assert started == [(downloaded, tmp_path / "Anim2D")] and closed and not window.doc.dirty


def test_install_stops_if_the_user_cancels_saving(window, monkeypatch):
    monkeypatch.setattr(updates, "install_kind", lambda: "installed")
    monkeypatch.setattr(window, "_confirm_discard", lambda: False)
    monkeypatch.setattr(updates, "fetch_checksums", lambda r: pytest.fail("nothing is downloaded"))
    window.updates.install(_release())
    QThreadPool.globalInstance().waitForDone(500)


def test_install_refuses_an_unlisted_download(window, dialogs, monkeypatch):
    monkeypatch.setattr(updates, "install_kind", lambda: "installed")
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    monkeypatch.setattr(updates, "fetch_checksums", lambda r: {})
    monkeypatch.setattr(updates, "start_installer", lambda *a: pytest.fail("must not install"))
    window.updates.install(_release())
    _wait(lambda: dialogs)
    assert dialogs[0][0] == "warning" and "can't be verified" in dialogs[0][1]


def test_portable_copy_downloads_next_to_itself(window, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "install_kind", lambda: "portable")
    monkeypatch.setattr(updates, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(updates, "fetch_checksums", lambda r: {"Anim2D-Portable-99.0.0.exe": "ab" * 32})
    folders = []
    monkeypatch.setattr(updates, "download", lambda asset, sha, folder, report: folders.append((asset.name, folder))
                        or Path(folder) / asset.name)
    monkeypatch.setattr(updates, "start_installer", lambda *a: pytest.fail("portable copies are not installed"))
    shown = []
    monkeypatch.setattr(update_ui.QMessageBox, "exec", lambda self: shown.append(self.text()))
    window.updates.install(_release())
    _wait(lambda: shown)
    assert folders == [("Anim2D-Portable-99.0.0.exe", tmp_path)] and "Anim2D-Portable-99.0.0.exe" in shown[0]


# --- menus follow the Windows conventions

def test_every_menu_command_has_its_own_access_key(window):
    for top in window.menuBar().actions():
        labels = [a.text() for a in top.menu().actions() if not a.isSeparator() and a.text()]
        keys = [t[t.index("&") + 1].lower() for t in labels if "&" in t]
        assert len(keys) == len(labels) and len(set(keys)) == len(keys), top.text()
    file_menu = [a.text() for a in window.menuBar().actions()[0].menu().actions() if a.text()]
    assert "E&xit" in file_menu and "Save Project &As…" in file_menu, "the usual Windows letters"
    assert all(t.replace("&", "")[:1].isascii() for t in file_menu), "no emoji in menu labels"


def test_renamed_commands_keep_a_custom_shortcut(qapp, tmp_path):
    store = QSettings(str(tmp_path / "keys.ini"), QSettings.Format.IniFormat)
    store.setValue("shortcuts/Quit", "Ctrl+Q")  # set before 'Quit' became 'Exit'
    w = EditorWindow(Document(), settings_store=store)
    assert w.shortcuts.key("Exit") == "Ctrl+Q" and store.value("shortcuts/Quit") is None
    w.close()
