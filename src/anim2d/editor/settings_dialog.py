"""File > Settings (F12): every preference in one window, one page per topic.

General · Appearance · Shortcuts · Updates · Plugins · About. Pages that act on the editor (Appearance,
Shortcuts, Updates, Plugins) need the EditorWindow; without one (tests, other hosts) only General and
About are shown. Appearance changes show at once and Cancel puts them back.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..resources import APP_NAME, HOMEPAGE, VERSION, icon_path, system_description
from . import document
from .form_widgets import SliderField, section

PAGES = ("General", "Appearance", "Shortcuts", "Updates", "Plugins", "About")
MUTED = "color: #8f8f8f;"


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


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(MUTED)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    label.setOpenExternalLinks(True)
    return label


def _page(*widgets) -> QWidget:
    """A page: its widgets top to bottom, scrolling if the window is small."""
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(4, 0, 4, 0)
    for w in widgets:
        if isinstance(w, QFormLayout):
            layout.addLayout(w)
        else:
            layout.addWidget(w)
    layout.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidget(inner)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    return scroll


def _link_button(text: str, url: str) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(url)
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    return button


def _buttons_row(*buttons) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: EditorSettings, parent=None, on_clear_recent=None, window=None,
                 page: str = "General"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 500)
        self.window_ = window
        self.appearance = None
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.nav.setStyleSheet("QListWidget::item { padding: 6px 10px; }")
        self.stack = QStackedWidget()
        pages = {"General": self._general(settings, on_clear_recent)}
        if window is not None:
            from .appearance_panel import AppearancePanel
            self.appearance = AppearancePanel(window)
            pages["Appearance"] = _page(section("Appearance"), self.appearance)
            pages["Shortcuts"] = self._shortcuts()
            pages["Updates"] = self._updates()
            pages["Plugins"] = self._plugins()
        pages["About"] = self._about()
        for name in PAGES:
            if name in pages:
                self.nav.addItem(QListWidgetItem(name))
                self.stack.addWidget(pages[name])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.show_page(page)

        ok, cancel = QPushButton("✔ OK"), QPushButton("⊘ Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        body = QHBoxLayout()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)

    def show_page(self, name: str) -> None:
        names = [self.nav.item(i).text() for i in range(self.nav.count())]
        self.nav.setCurrentRow(names.index(name) if name in names else 0)

    def page_names(self) -> list[str]:
        return [self.nav.item(i).text() for i in range(self.nav.count())]

    # pages

    def _general(self, settings: EditorSettings, on_clear_recent) -> QWidget:
        self.recent = SliderField(1, 30, settings.recent_count)
        self.undo = SliderField(10, 2000, settings.undo_levels)
        self.fps = SliderField(1, 120, settings.default_fps)
        self.autosave = SliderField(0, 60, settings.autosave_minutes, note="minutes (0 = off)")
        form = QFormLayout()
        form.addRow("Recent projects:", self.recent)
        form.addRow("Undo levels:", self.undo)
        form.addRow("New skeleton FPS:", self.fps)
        form.addRow("Autosave every:", self.autosave)
        clear = QPushButton("Clear recent projects")
        clear.setEnabled(on_clear_recent is not None)
        if on_clear_recent is not None:
            clear.clicked.connect(on_clear_recent)
            clear.clicked.connect(lambda: clear.setText("Recent projects cleared"))
        return _page(section("General"), form,
                     _note("Autosave writes <project>.anim2d.autosave next to a saved project that has changes; "
                           "the project file itself is only written when you save."),
                     _buttons_row(clear),
                     section("Folders"), self._folders(),
                     _note("Your work is kept in Documents\\Anim2D, and Anim2D's own files (downloaded updates, "
                           "the log) in your AppData folder. Uninstalling never removes your projects."))

    def _folders(self) -> QWidget:
        """Where things are, each with a button that opens it."""
        from .. import paths
        from ..presets.library import default_user_library_dir
        rows = (("Projects", paths.projects_dir(), "Where Save Project and Open Project start"),
                ("FX templates", default_user_library_dir(), "Templates you registered from Spine exports"),
                ("FX output", paths.current(paths.fx_output_dir()), "Where the FX Builder writes packages at first"),
                ("Logs", paths.logs_dir(), "anim2d.log: attach it to a bug report"))
        form = QFormLayout()
        for name, folder, tip in rows:
            path = QLabel(str(folder))
            path.setStyleSheet(MUTED)
            path.setToolTip(tip)
            path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            button = QPushButton("Open")
            button.setToolTip(f"Open {folder} in File Explorer")
            button.clicked.connect(lambda _=False, f=folder: self._open_folder(f))
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(path, 1)
            row.addWidget(button)
            form.addRow(f"{name}:", line)
        holder = QWidget()
        holder.setLayout(form)
        return holder

    def _shortcuts(self) -> QWidget:
        window = self.window_
        edit = QPushButton("Edit keyboard shortcuts…")
        edit.clicked.connect(window.edit_shortcuts)
        show = QPushButton("Show all shortcuts")
        show.clicked.connect(window.show_shortcuts)
        return _page(section("Shortcuts"),
                     _note("Every command can have its own key. Changes are kept between sessions; Edit > "
                           "Keyboard Shortcuts (Ctrl+Alt+K) opens the same editor."),
                     _buttons_row(edit, show),
                     _note("In the viewport: right or middle drag pans, the wheel zooms, F frames everything."))

    def _updates(self) -> QWidget:
        from .. import updates
        controller = self.window_.updates
        kind = updates.install_kind()
        self.auto_check = QCheckBox("Check for updates automatically (once a day)")
        self.auto_check.setChecked(controller.auto_check)
        if kind in ("source", "dev"):
            self.auto_check.setEnabled(False)
            self.auto_check.setToolTip("Automatic checks only run in the installed or portable program")
        self.update_status = QLabel(controller.status())
        self.update_status.setWordWrap(True)
        controller.status_changed.connect(self._update_status_changed)
        check = QPushButton("Check now")
        check.clicked.connect(lambda: controller.check(manual=True))
        install = QPushButton("Install update…" if kind == "installed" else "Download update…")
        install.setVisible(controller.release is not None and kind not in ("source", "dev"))
        install.clicked.connect(self._install_update)
        self.update_install = install
        self.update_kind = kind
        controller.available.connect(self._update_available)
        skipped = controller.skipped
        unskip = QPushButton(f"Stop skipping {skipped}")
        unskip.setVisible(bool(skipped))
        unskip.clicked.connect(lambda: (setattr(controller, "skipped", ""), unskip.hide()))
        how = {
            "installed": "Updates download from the project's GitHub releases, are checked against the "
                         "release's SHA-256 checksums, and install with one click. Anim2D asks to save your "
                         "work before it restarts.",
            "portable": "This is the portable program: a new version is downloaded next to it for you to start "
                        "instead. Install Anim2D to get one-click updates.",
            "mac": "Updates download from the project's GitHub releases: the disk image for this Mac (Apple "
                   "silicon or Intel) is saved to Downloads and checked against the release's SHA-256 checksums. "
                   f"Open it and drag the new {APP_NAME} into Applications.",
            "dev": "This is a local test build: it never checks or updates by itself, and never touches the "
                   "installed Anim2D. Check now still tells you about new releases.",
            "source": "You're running from source, so there are no automatic checks. Check now still tells you "
                      "about new releases; update with git pull.",
        }[kind]
        return _page(section("Updates"), QLabel(f"{APP_NAME} {VERSION}"), self.update_status,
                     _buttons_row(check, install, unskip), self.auto_check, _note(how),
                     _note(f'<a href="{HOMEPAGE}/releases">All releases and their notes</a>'))

    def _update_status_changed(self, text: str) -> None:
        self.update_status.setText(text)

    def _update_available(self, _release) -> None:
        self.update_install.setVisible(self.update_kind not in ("source", "dev"))

    def _install_update(self) -> None:
        controller = self.window_.updates
        if controller.release is not None:
            controller.install(controller.release, self)

    def _plugins(self) -> QWidget:
        from ..presets.library import default_user_library_dir
        window = self.window_
        unity = QPushButton("Unity Bridge…")
        unity.clicked.connect(window.unity_bridge)
        templates = default_user_library_dir()
        folder = QPushButton("Open templates folder")
        folder.clicked.connect(lambda: self._open_folder(templates))
        importer = QPushButton("Import FX template…")
        importer.clicked.connect(window.import_template)
        return _page(section("Plugins"),
                     _note("Plugin support is planned: add-ons that bring new tools, exporters and FX. Until then, "
                           "these built-in integrations live here."),
                     QLabel("<b>Unity bridge</b>"),
                     _note("Exports the skeleton straight into a spine-unity project, optionally after every save. "
                           "Its settings are kept in each project."),
                     _buttons_row(unity),
                     QLabel("<b>FX templates</b>"),
                     _note(f"Your own FX, registered from Spine exports, are kept in {templates}."),
                     _buttons_row(importer, folder))

    def _open_folder(self, folder) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Settings", f"Can't open {folder}:\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _about(self) -> QWidget:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion
        icon = QLabel()
        if icon_path().is_file():
            icon.setPixmap(QIcon(str(icon_path())).pixmap(64, 64))
        title = QLabel(f"<b style='font-size:18px'>{APP_NAME}</b><br>Version {VERSION}")
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(icon)
        row.addWidget(title, 1)
        from .. import logs
        self.system_info = (f"{APP_NAME} {VERSION}\n{system_description()}\n"
                            f"Python {platform.python_version()} · Qt {qVersion()} · PySide6 {pyside_version}\n"
                            f"{'Built program' if getattr(sys, 'frozen', False) else 'From source'}\n"
                            f"Log: {logs.log_file()}")
        log_folder = QPushButton("Open log folder")
        log_folder.setToolTip("anim2d.log records what happened, to attach to a bug report")
        log_folder.clicked.connect(lambda: self._open_folder(logs.log_file().parent))
        copy = QPushButton("Copy system info")
        copy.setToolTip("Versions to paste into a bug report")
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.system_info),
                                      copy.setText("Copied")))
        return _page(head,
                     QLabel("A free, open-source 2D skeletal animation editor and FX builder that reads and "
                            "writes Spine data."),
                     _buttons_row(_link_button("Website", HOMEPAGE),
                                  _link_button("Report a bug", f"{HOMEPAGE}/issues/new/choose"),
                                  _link_button("Release notes", f"{HOMEPAGE}/releases")),
                     _buttons_row(copy, log_folder),
                     section("Licence"),
                     _note(f'Released under the <a href="{HOMEPAGE}/blob/main/LICENSE">MIT License</a>. '
                           "Built with Qt for Python (PySide6, LGPL) and Pillow."),
                     _note("Spine is a trademark of Esoteric Software LLC. Anim2D is an independent project, not "
                           "affiliated with or endorsed by Esoteric Software."),
                     section("System"),
                     _note(self.system_info.replace("\n", "<br>")))

    # results

    def settings(self) -> EditorSettings:
        return EditorSettings(self.recent.value(), self.undo.value(), self.fps.value(), self.autosave.value())

    def accept(self) -> None:
        if self.window_ is not None:
            self.window_.updates.auto_check = self.auto_check.isChecked()
        super().accept()

    def reject(self) -> None:
        if self.appearance is not None:
            self.appearance.revert()
        super().reject()
