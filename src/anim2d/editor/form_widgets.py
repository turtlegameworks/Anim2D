"""Small form widgets in the style of Spine's dialogs: a number with a bar, a path field with
favourite (★) and browse buttons, and section titles."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QSlider, QSpinBox, QToolButton, QWidget)


class SliderField(QWidget):
    """A number box followed by a bar, like Spine's sliders. decimals > 0 gives a float value."""

    changed = Signal()

    def __init__(self, low: float, high: float, value: float, decimals: int = 0, note: str = "",
                 width: int = 300):
        super().__init__()
        self.decimals = decimals
        self.factor = 10 ** decimals
        if decimals:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
        else:
            self.spin = QSpinBox()
        self.spin.setRange(low, high)
        self.spin.setFixedWidth(90)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(low * self.factor), round(high * self.factor))
        self.slider.setMinimumWidth(120)
        self.note = QLabel(note)
        self.note.setStyleSheet("color: #a9a9a9;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.spin)
        layout.addWidget(self.slider, 1)
        layout.addSpacing(8)
        layout.addWidget(self.note)
        self.slider.setMaximumWidth(width)
        self.spin.valueChanged.connect(lambda v: self._sync_slider(v))
        self.slider.valueChanged.connect(lambda v: self.spin.setValue(v / self.factor))
        self.set(value)

    def _sync_slider(self, v) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(v * self.factor))
        self.slider.blockSignals(False)
        self.changed.emit()

    def value(self) -> float:
        v = self.spin.value()
        return float(v) if self.decimals else int(v)

    def set(self, value: float) -> None:
        self.spin.setValue(value)
        self._sync_slider(self.spin.value())


class PathField(QWidget):
    """A path with ★ (favourite paths, kept in settings) and 📁 (browse) buttons.
    mode: "folder", "save" (a file to write) or "open" (a file to read)."""

    def __init__(self, mode: str = "folder", file_filter: str = "", store: QSettings | None = None,
                 key: str = "favorites"):
        super().__init__()
        self.mode, self.file_filter, self.store, self.key = mode, file_filter, store, key
        self.edit = QLineEdit()
        self.star = QToolButton()
        self.star.setText("★")
        self.star.setToolTip("Favourite paths")
        self.star.clicked.connect(self._favorites)
        self.browse = QToolButton()
        self.browse.setText("📁")
        self.browse.setToolTip("Browse")
        self.browse.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.star)
        layout.addWidget(self.browse)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self.edit.setText(text)

    def favorites(self) -> list[str]:
        if self.store is None:
            return []
        value = self.store.value(self.key, [])
        return [value] if isinstance(value, str) else list(value or [])

    def _favorites(self) -> None:
        menu = QMenu(self)
        current = self.text()
        favs = self.favorites()
        if current and current not in favs:
            menu.addAction("Add to favourites", lambda: self._store([current] + favs))
        if current in favs:
            menu.addAction("Remove from favourites", lambda: self._store([f for f in favs if f != current]))
        if favs:
            menu.addSeparator()
        for f in favs:
            menu.addAction(f, lambda f=f: self.setText(f))
        if menu.isEmpty():
            menu.addAction("Type a path, then add it here").setEnabled(False)
        menu.exec(self.star.mapToGlobal(self.star.rect().bottomLeft()))

    def _store(self, favs: list[str]) -> None:
        if self.store is not None:
            self.store.setValue(self.key, favs[:12])

    def _browse(self) -> None:
        start = self.text()
        if self.mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        elif self.mode == "open":
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.file_filter)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Choose file", start, self.file_filter)
        if path:
            self.setText(path)


def section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #7fe0f0; font-size: 13px;")
    return label


def rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #555555;")
    return line


def row(*widgets, stretch: bool = True) -> QWidget:
    w = QWidget()
    layout = QHBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    for item in widgets:
        if isinstance(item, int):
            layout.addSpacing(item)
        else:
            layout.addWidget(item)
    if stretch:
        layout.addStretch(1)
    return w
