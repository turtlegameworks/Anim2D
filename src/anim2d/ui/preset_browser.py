"""Left panel: search, category filter, favourites and a grid of animated thumbnails."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ..models.settings import validate_settings
from ..packaging.package import build_document
from ..presets.library import PresetLibrary
from ..preview.evaluator import PoseEvaluator
from ..preview.qt_renderer import ImageCache, render_image, world_bounds

THUMB = 96
THUMB_FRAMES = 12
FAVORITES = "★ Favourites"
ALL = "All categories"


def render_thumbnails(library: PresetLibrary, preset_id: str) -> list[QPixmap]:
    """Animated thumbnail frames rendered from the preset's default settings."""
    preset = library.get(preset_id)
    settings, _ = validate_settings(preset, "fx_thumb_01", {"delay": 0})
    if settings is None:
        return []
    doc, data = build_document(preset, settings)
    evaluator = PoseEvaluator(data)
    cache = ImageCache()
    cache.set_images({f"{doc.prefix}/{k}": v for k, v in doc.images.items()})
    bounds = world_bounds(evaluator)
    frames = []
    for i in range(THUMB_FRAMES):
        t = evaluator.duration * (i + 0.5) / THUMB_FRAMES
        frames.append(QPixmap.fromImage(render_image(evaluator, cache, t, THUMB, QColor("#1e1f26"), bounds)))
    return frames


class PresetBrowser(QWidget):
    preset_selected = Signal(str)
    favorites_changed = Signal(list)
    import_requested = Signal()
    open_templates_requested = Signal()

    def __init__(self, library: PresetLibrary, favorites: list[str], parent=None):
        super().__init__(parent)
        self.library = library
        self.favorites = set(favorites)
        self._frames: dict[str, list[QPixmap]] = {}
        self._frame_index = 0

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search presets…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.category = QComboBox()
        self._fill_categories()
        self.category.currentTextChanged.connect(self.refresh)

        # every preset as a thumbnail with its name under it, wrapping into as many columns as fit
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setGridSize(QSize(THUMB + 18, THUMB + 38))
        self.list.setSpacing(4)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet("QListWidget::item { border-radius: 6px; padding: 2px; }"
                                "QListWidget::item:selected { background: #2f7de1; color: white; }")
        self.list.currentItemChanged.connect(self._current_changed)

        self.fav_button = QPushButton("☆ Add to favourites")
        self.fav_button.clicked.connect(self._toggle_favorite)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: #b8bcc6")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.search)
        layout.addWidget(self.category)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(self.fav_button)
        layout.addLayout(row)
        self.import_button = QPushButton("Import template from Spine…")
        self.import_button.setToolTip("Register a JSON export from Spine 3.8 as your own template")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.open_templates_button = QPushButton("Open templates folder")
        self.open_templates_button.clicked.connect(self.open_templates_requested.emit)
        layout.addWidget(self.import_button)  # stacked, so the panel can be as narrow as two thumbnails
        layout.addWidget(self.open_templates_button)
        layout.addWidget(self.description)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #e0a040")
        layout.addWidget(self.problems)

        self.set_library(library)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._advance)
        self._anim.start(83)

    def _fill_categories(self) -> None:
        current = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem(ALL)
        self.category.addItem(FAVORITES)
        for cat in sorted({p.category for p in self.library.presets.values()}):
            self.category.addItem(cat)
        if current and self.category.findText(current) >= 0:
            self.category.setCurrentText(current)
        self.category.blockSignals(False)

    def set_library(self, library: PresetLibrary, changed_id: str | None = None) -> None:
        """Use a freshly loaded library; re-render thumbnails for new or changed presets."""
        self.library = library
        for preset_id in list(self._frames):
            if preset_id not in library.presets or preset_id == changed_id:
                del self._frames[preset_id]
        for preset_id in library.presets:
            if preset_id not in self._frames:
                try:
                    self._frames[preset_id] = render_thumbnails(library, preset_id)
                except (ValueError, KeyError, OSError):
                    self._frames[preset_id] = []
        self._fill_categories()
        self.problems.setText("Some presets could not be loaded:\n" +
                              "\n".join(f"• {k}: {v}" for k, v in library.errors.items()) if library.errors else "")
        self.problems.setVisible(bool(library.errors))
        self.refresh()

    def refresh(self) -> None:
        current = self.current_id()
        category = self.category.currentText()
        summaries = self.library.list_presets(self.search.text(),
                                              None if category in (ALL, FAVORITES) else category)
        if category == FAVORITES:
            summaries = [s for s in summaries if s.id in self.favorites]
        self.list.blockSignals(True)
        self.list.clear()
        for s in summaries:
            star = "★ " if s.id in self.favorites else ""
            badge = " · template" if self.library.get(s.id).source == "user" else ""
            item = QListWidgetItem(f"{star}{s.name}")
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setToolTip(f"<b>{s.name}</b> · {s.category}{badge}<br>{s.description}")
            frames = self._frames.get(s.id)
            if frames:
                item.setIcon(QIcon(frames[self._frame_index % len(frames)]))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count() == 0:
            self.description.setText("No presets match.")
            return
        self.select(current or self.list.item(0).data(Qt.ItemDataRole.UserRole), emit=current is None)

    def select(self, preset_id: str, emit: bool = True) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == preset_id:
                self.list.blockSignals(not emit)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                self._update_details(preset_id)
                return

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _current_changed(self, item, _previous) -> None:
        if item is None:
            return
        preset_id = item.data(Qt.ItemDataRole.UserRole)
        self._update_details(preset_id)
        self.preset_selected.emit(preset_id)

    def _update_details(self, preset_id: str) -> None:
        p = self.library.get(preset_id)
        kind = "Your template (made in Spine)" if p.source == "user" else "Built-in preset"
        self.description.setText(f"{p.description}\n\nTags: {', '.join(p.tags)}\n{kind}, version {p.version}")
        self.fav_button.setText("★ Remove from favourites" if preset_id in self.favorites else "☆ Add to favourites")

    def _toggle_favorite(self) -> None:
        preset_id = self.current_id()
        if not preset_id:
            return
        self.favorites.symmetric_difference_update({preset_id})
        self.favorites_changed.emit(sorted(self.favorites))
        self.refresh()

    def _advance(self) -> None:
        self._frame_index += 1
        for i in range(self.list.count()):
            item = self.list.item(i)
            frames = self._frames.get(item.data(Qt.ItemDataRole.UserRole))
            if frames:
                item.setIcon(QIcon(frames[self._frame_index % len(frames)]))
