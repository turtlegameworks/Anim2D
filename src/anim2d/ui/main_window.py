"""Main window: browser (left), preview (centre), settings (right), status bar (bottom).

Widgets only display state and forward edits; validation and generation live in
models/, generation/ and packaging/.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import __version__
from ..models.session import SettingsSession, suggest_instance_name
from ..packaging.package import GenerationResult, build_document
from ..presets.library import PresetLibrary, default_user_library_dir
from ..preview.evaluator import PoseEvaluator
from ..resources import FX_APP_NAME
from ..resources import settings as app_settings
from ..spine_adapters import spine38
from .preset_browser import PresetBrowser
from .preview_widget import PreviewPanel
from .settings_panel import SettingsPanel
from .template_dialog import TemplateImportDialog
from .worker import GenerateJob

ERROR_COLOR = QColor("#ff6b6b")
WARNING_COLOR = QColor("#f0b44c")


class MainWindow(QMainWindow):
    def __init__(self, library: PresetLibrary, settings_store: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(f"{FX_APP_NAME} {__version__} - {spine38.PROFILE_ID}")
        self.library = library
        self.user_root = library.user_root or default_user_library_dir()
        self.store = settings_store or app_settings("fx")
        self.sessions: dict[str, SettingsSession] = {}
        self.session: SettingsSession | None = None
        self.busy = False
        self.last_output: Path | None = None
        self._job: GenerateJob | None = None
        self._ready = False

        default_out = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "Anim2D FX Output"
        self.output_dir = Path(str(self.store.value("output_dir", str(default_out))))

        favorites = self.store.value("favorites", [], type=list) or []
        self.browser = PresetBrowser(library, [str(f) for f in favorites])
        self.preview = PreviewPanel()
        self.settings_panel = SettingsPanel(spine38.PROFILE_LABEL)
        self.settings_panel.output_edit.setText(str(self.output_dir))

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.browser)
        self.splitter.addWidget(self.preview)
        self.splitter.addWidget(self.settings_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 700, 400])  # room for three columns of preset thumbnails

        # Bottom bar
        self.messages = QListWidget()
        self.messages.setMaximumHeight(86)
        self.status = QLabel("Ready")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(120)
        self.progress.hide()
        self.copy_button = QPushButton("Copy output path")
        self.open_button = QPushButton("Open folder")
        self.guide_button = QPushButton("View import guide")
        self.engine_button = QPushButton("Export for game engine…")
        self.engine_button.setToolTip("Skeleton data (JSON or binary) plus a packed texture atlas, for Unity, Godot, "
                                      "Cocos, Phaser, PixiJS and other Spine runtimes")
        self.generate_button = QPushButton("Generate package")
        self.generate_button.setDefault(True)
        self.generate_button.setMinimumWidth(160)
        self.generate_button.setStyleSheet("QPushButton:enabled { background: #2f7de1; color: white; font-weight: bold; }")
        for b in (self.copy_button, self.open_button, self.guide_button):
            b.setEnabled(False)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(6, 0, 6, 6)
        bottom_layout.addWidget(self.messages)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.progress)
        row.addWidget(self.copy_button)
        row.addWidget(self.open_button)
        row.addWidget(self.guide_button)
        row.addWidget(self.engine_button)
        row.addWidget(self.generate_button)
        bottom_layout.addLayout(row)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(bottom)
        self.setCentralWidget(central)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(25)
        self._refresh_timer.timeout.connect(self._refresh_preview)

        # Wiring
        self.browser.preset_selected.connect(self.select_preset)
        self.browser.favorites_changed.connect(lambda favs: self.store.setValue("favorites", favs))
        self.browser.import_requested.connect(self.import_template)
        self.browser.open_templates_requested.connect(self._open_templates_folder)
        sp = self.settings_panel
        sp.value_changed.connect(self._value_changed)
        sp.drag_finished.connect(lambda: self.session and self.session.end_merge())
        sp.instance_name_changed.connect(self._name_changed)
        sp.next_name_button.clicked.connect(self._next_free_name)
        sp.browse_output.connect(self._browse_output)
        sp.undo_button.clicked.connect(self.undo)
        sp.redo_button.clicked.connect(self.redo)
        sp.reset_button.clicked.connect(self.reset_all)
        sp.save_variant_button.clicked.connect(self._save_variant)
        sp.load_variant_button.clicked.connect(self._load_variant)
        self.generate_button.clicked.connect(self.generate)
        self.engine_button.clicked.connect(self.export_for_engine)
        self.copy_button.clicked.connect(self._copy_path)
        self.open_button.clicked.connect(lambda: self._open(self.last_output))
        self.guide_button.clicked.connect(
            lambda: self._open(self.last_output / "IMPORT_IN_SPINE.md" if self.last_output else None))
        self.preview.background_changed.connect(lambda name: self.store.setValue("background", name))
        self.preview.grid_changed.connect(lambda style, color: (self.store.setValue("grid", style),
                                                                color and self.store.setValue("grid_color", color)))
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)

        if self.store.value("geometry"):
            self.restoreGeometry(self.store.value("geometry"))
        else:
            self.resize(1360, 820)
        if self.store.value("splitter2"):  # "splitter" held the old, narrower preset list
            self.splitter.restoreState(self.store.value("splitter2"))
        self.preview.set_background(str(self.store.value("background", "Dark")))
        self.preview.set_grid(str(self.store.value("grid", "Off")), self.store.value("grid_color") or None)

        self._ready = True
        first = self.browser.current_id()
        if first:
            self.select_preset(first)

    # --- preset / session ------------------------------------------------------------------

    def select_preset(self, preset_id: str) -> None:
        if not self._ready:
            return
        preset = self.library.get(preset_id)
        if preset_id not in self.sessions:
            self.sessions[preset_id] = SettingsSession(preset, suggest_instance_name(preset, self.output_dir))
        self.session = self.sessions[preset_id]
        self.settings_panel.build(preset)
        self._sync_panel()
        self._refresh_preview(keep_framing=False)
        self.preview.restart()

    def _sync_panel(self) -> None:
        if not self.session:
            return
        self.settings_panel.set_values(self.session.values)
        if self.settings_panel.name_edit.text() != self.session.instance_name:
            self.settings_panel.name_edit.setText(self.session.instance_name)
        self.settings_panel.set_undo_state(self.session.can_undo, self.session.can_redo)

    def _value_changed(self, name: str, value, merge: bool) -> None:
        if self.session and self.session.set(name, value, merge=merge):
            self._sync_panel()
            self._refresh_timer.start()

    def _name_changed(self, text: str) -> None:
        if self.session:
            self.session.instance_name = text
            self._refresh_timer.start()

    def _next_free_name(self) -> None:
        if self.session:
            self.session.instance_name = suggest_instance_name(self.session.preset, self.output_dir)
            self._sync_panel()
            self._refresh_preview()

    def undo(self) -> None:
        if self.session and self.session.undo():
            self._sync_panel()
            self._refresh_preview()

    def redo(self) -> None:
        if self.session and self.session.redo():
            self._sync_panel()
            self._refresh_preview()

    def reset_all(self) -> None:
        if self.session:
            self.session.reset()
            self._sync_panel()
            self._refresh_preview()

    # --- validation + preview --------------------------------------------------------------

    def _refresh_preview(self, keep_framing: bool = True) -> None:
        if not self.session:
            return
        settings, report = self.session.validate()
        errors, warnings = list(report.errors), list(report.warnings)
        if settings is not None and (self.output_dir / settings.instance_name).exists():
            errors.append(f"'{settings.instance_name}' already exists in the output folder. "
                          f"Pick another name (try 'Next free').")
        evaluator, message = None, ""
        if settings is not None:
            try:
                doc, data = build_document(self.session.preset, settings)
                warnings.extend(doc.warnings)
                self.preview.canvas.cache.set_images({f"{doc.prefix}/{k}": v for k, v in doc.images.items()})
                evaluator = PoseEvaluator(data)
            except (ValueError, KeyError) as exc:
                errors.append(f"Generation failed: {exc}")
        if evaluator is None:
            message = "Fix the highlighted settings to see a preview"
        self.preview.fps = 30.0
        self.preview.set_evaluator(evaluator, keep_framing=keep_framing, message=message)
        self._show_messages(errors, warnings)
        self.generate_button.setEnabled(not errors and not self.busy)
        self.engine_button.setEnabled(not errors and not self.busy)

    def _show_messages(self, errors: list[str], warnings: list[str]) -> None:
        self.messages.clear()
        for text in errors:
            item = QListWidgetItem(f"✖ {text}")
            item.setForeground(ERROR_COLOR)
            self.messages.addItem(item)
        for text in warnings:
            item = QListWidgetItem(f"⚠ {text}")
            item.setForeground(WARNING_COLOR)
            self.messages.addItem(item)
        if not errors and not warnings:
            self.messages.addItem(QListWidgetItem("✔ Settings are valid"))

    # --- generation ------------------------------------------------------------------------

    def generate(self) -> None:
        if self.busy or not self.session:
            return
        settings, report = self.session.validate()
        if settings is None:
            self._refresh_preview()
            return
        self.busy = True
        self.generate_button.setEnabled(False)
        self.progress.show()
        self.status.setText(f"Generating {settings.instance_name}…")
        self._job = GenerateJob(self.session.preset, settings, self.output_dir)
        self._job.signals.finished.connect(self._generation_finished)
        self._job.signals.failed.connect(self._generation_failed)
        QThreadPool.globalInstance().start(self._job)

    def _generation_finished(self, result: GenerationResult) -> None:
        self.busy = False
        self.progress.hide()
        self._job = None
        if not result.ok:
            self.status.setText("Generation failed - see messages above")
            self._show_messages(result.report.errors, result.report.warnings)
            self.generate_button.setEnabled(True)
            return
        self.last_output = result.output_dir
        for b in (self.copy_button, self.open_button, self.guide_button):
            b.setEnabled(True)
        stats = ", ".join(f"{v} {k}" for k, v in result.stats.items())
        generated = self.session.instance_name
        self.session.instance_name = suggest_instance_name(self.session.preset, self.output_dir)
        self._sync_panel()
        self._refresh_preview()
        self.status.setText(f"Generated {result.output_dir} ({stats}). Next name set to "
                            f"'{self.session.instance_name}'. Spine import still needs a manual check.")
        if result.report.warnings:
            self._show_messages([], [f"{generated}: {w}" for w in result.report.warnings])

    def export_for_engine(self, options=None) -> None:
        """Skeleton data plus a packed atlas for game engines (the package is for the Spine editor)."""
        from ..packaging.engine_export import export_for_engine
        from .engine_export_dialog import EngineExportDialog
        if self.busy or not self.session:
            return
        settings, _ = self.session.validate()
        if settings is None:
            self._refresh_preview()
            return
        if options is None:
            dialog = EngineExportDialog(settings.instance_name, self)
            if dialog.exec() != EngineExportDialog.DialogCode.Accepted:
                return
            options = dialog.options()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = export_for_engine(self.session.preset, settings, self.output_dir, options)
        finally:
            QApplication.restoreOverrideCursor()
        if not result.ok:
            self.status.setText("Export failed - see messages above")
            self._show_messages(result.report.errors, result.report.warnings)
            return
        self.last_output = result.output_dir
        self.copy_button.setEnabled(True)
        self.open_button.setEnabled(True)
        files = ", ".join(sorted(p.name for p in result.output_dir.iterdir()))
        self.status.setText(f"Exported for game engines: {result.output_dir} ({files})")

    def _generation_failed(self, message: str) -> None:
        self.busy = False
        self.progress.hide()
        self._job = None
        self.status.setText("Generation failed")
        self._show_messages([f"Unexpected error: {message}"], [])
        self.generate_button.setEnabled(True)

    # --- templates -------------------------------------------------------------------------

    def import_template(self, dialog: TemplateImportDialog | None = None) -> str | None:
        """Show the import dialog; on success reload the library and select the new template."""
        dialog = dialog or TemplateImportDialog(self.library, self.user_root,
                                                start_dir=self.last_output or self.output_dir, parent=self)
        if dialog.exec() != TemplateImportDialog.DialogCode.Accepted or not dialog.registered_id:
            return None
        return self.reload_library(dialog.registered_id)

    def reload_library(self, select_id: str | None = None) -> str | None:
        self.library = PresetLibrary.load(self.library.root, self.user_root)
        if select_id:
            self.sessions.pop(select_id, None)  # a replaced template must start from its new defaults
        self.browser.set_library(self.library, changed_id=select_id)
        if select_id and select_id in self.library.presets:
            self.browser.select(select_id, emit=False)
            self.select_preset(select_id)  # explicit: the row may already have been current
            self.status.setText(f"Registered template '{select_id}' from Spine")
        return select_id

    def _open_templates_folder(self) -> None:
        self.user_root.mkdir(parents=True, exist_ok=True)
        self._open(self.user_root)

    # --- files -----------------------------------------------------------------------------

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", str(self.output_dir))
        if folder:
            self.output_dir = Path(folder)
            self.store.setValue("output_dir", folder)
            self.settings_panel.output_edit.setText(folder)
            self._refresh_preview()

    def _save_variant(self) -> None:
        if not self.session:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save variant", str(self.output_dir / f"{self.session.instance_name}.fxvariant.json"),
                                              "FX variant (*.json)")
        if not path:
            return
        report = self.session.save_variant(Path(path), spine38.PROFILE_ID)
        if report.ok:
            self.status.setText(f"Saved variant {path}")
        else:
            QMessageBox.warning(self, "Cannot save variant", "\n".join(report.errors))

    def _load_variant(self) -> None:
        if not self.session:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load variant", str(self.output_dir), "FX variant (*.json)")
        if not path:
            return
        try:
            self.session.load_variant(Path(path))
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot load variant", str(exc))
            return
        self._sync_panel()
        self._refresh_preview(keep_framing=False)
        self.status.setText(f"Loaded variant {path} (Undo restores the previous settings)")

    def _copy_path(self) -> None:
        if self.last_output:
            QGuiApplication.clipboard().setText(str(self.last_output))
            self.status.setText(f"Copied {self.last_output}")

    def _open(self, path: Path | None) -> None:
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event) -> None:
        self.store.setValue("geometry", self.saveGeometry())
        self.store.setValue("splitter2", self.splitter.saveState())
        QThreadPool.globalInstance().waitForDone(10000)
        super().closeEvent(event)
