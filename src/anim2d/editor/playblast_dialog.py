"""File > Playblast… (Ctrl+Shift+B): record the animation as the viewport shows it.

Pick the animation and frame range, the size (a percent of the viewport), the format (MP4, MOV, AVI,
GIF, WebP, APNG or a PNG/JPEG sequence) and where to save it, then Playblast. The choices are kept
for next time.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressDialog, QRadioButton, QSpinBox, QVBoxLayout)

from . import playblast as pb
from .document import EditorError
from .form_widgets import PathField

STORE_KEY = "playblast/settings"


class PlayblastDialog(QDialog):
    def __init__(self, viewport, parent=None, store: QSettings | None = None):
        super().__init__(parent)
        self.setWindowTitle("Playblast")
        self.viewport, self.store = viewport, store
        self.written: list[Path] = []
        st = viewport.state
        doc = st.doc
        saved = pb.PlayblastSettings.from_dict(self._stored())

        # --- what ----------------------------------------------------------------------------
        self.animation = QComboBox()
        self.animation.addItems(list(doc.animation_names()))
        current = saved.animation if saved.animation in doc.data["animations"] else st.animation
        if current:
            self.animation.setCurrentText(current)
        self.whole = QRadioButton("Whole animation")
        self.custom = QRadioButton("Frames")
        group = QButtonGroup(self)
        group.addButton(self.whole)
        group.addButton(self.custom)
        self.start = QSpinBox()
        self.end = QSpinBox()
        for box in (self.start, self.end):
            box.setRange(0, 100000)
        self.start.setValue(saved.start)
        self.end.setValue(saved.end)
        (self.custom if saved.range == "custom" else self.whole).setChecked(True)
        range_row = QHBoxLayout()
        range_row.addWidget(self.whole)
        range_row.addSpacing(12)
        range_row.addWidget(self.custom)
        range_row.addWidget(self.start)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.end)
        range_row.addStretch(1)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setDecimals(2)
        self.fps.setValue(saved.fps if saved.fps > 0 else doc.fps)
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 50)
        self.repeat.setValue(saved.repeat)
        self.repeat.setToolTip("How many times the animation plays in the file")

        what = QGroupBox("Animation")
        form = QFormLayout(what)
        form.addRow("Animation", self.animation)
        form.addRow("Range", range_row)
        form.addRow("Frame rate", self.fps)
        form.addRow("Repeat", self.repeat)

        # --- look ----------------------------------------------------------------------------
        self.scale = QComboBox()
        for percent in pb.SCALES:
            self.scale.addItem(f"{percent}%", percent)
        self.scale.setCurrentIndex(max(0, self.scale.findData(saved.scale)))
        self.size_label = QLabel()
        size_row = QHBoxLayout()
        size_row.addWidget(self.scale)
        size_row.addWidget(self.size_label, 1)
        self.bones = QCheckBox("Bones")
        self.bones.setChecked(saved.bones)
        self.grid = QCheckBox("Grid")
        self.grid.setChecked(saved.grid)
        self.hud = QCheckBox("Frame counter")
        self.hud.setToolTip("Write the animation name and frame number in the corner")
        self.hud.setChecked(saved.hud)
        show_row = QHBoxLayout()
        for box in (self.bones, self.grid, self.hud):
            show_row.addWidget(box)
        show_row.addStretch(1)
        look = QGroupBox("Look (framed as in the viewport)")
        form = QFormLayout(look)
        form.addRow("Size", size_row)
        form.addRow("Show", show_row)

        # --- output --------------------------------------------------------------------------
        self.format = QComboBox()
        for key, (label, _, _) in pb.FORMATS.items():
            self.format.addItem(label, key)
        self.format.setCurrentIndex(max(0, self.format.findData(saved.format)))
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(saved.quality)
        self.quality.setToolTip("JPEG, Motion JPEG (AVI) and WebP quality")
        self.folder = PathField("folder", store=store, key="favorites/folders")
        self.folder.setText(saved.folder or self._default_folder())
        self.name = QLineEdit(saved.name)
        self.name.setPlaceholderText("<animation>_playblast")
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setStyleSheet("color: #9aa0aa")
        self.path_label.setWordWrap(True)
        self.overwrite = QCheckBox("Overwrite existing files")
        self.overwrite.setChecked(saved.overwrite)
        self.open_after = QCheckBox("Open when done")
        self.open_after.setChecked(saved.open_after)
        options = QHBoxLayout()
        options.addWidget(self.overwrite)
        options.addWidget(self.open_after)
        options.addStretch(1)
        out = QGroupBox("Output")
        form = QFormLayout(out)
        form.addRow("Format", self.format)
        form.addRow("Quality", self.quality)
        form.addRow("Folder", self.folder)
        form.addRow("File name", self.name)
        form.addRow("", self.path_label)
        form.addRow("", options)
        self._out_form = form

        buttons = QDialogButtonBox()
        self.run_button = buttons.addButton("🎬 Playblast", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(what)
        layout.addWidget(look)
        layout.addWidget(out)
        layout.addWidget(buttons)

        for signal in (self.animation.currentTextChanged, self.format.currentIndexChanged,
                       self.scale.currentIndexChanged, self.name.textChanged, self.folder.edit.textChanged,
                       self.whole.toggled, self.start.valueChanged, self.end.valueChanged, self.fps.valueChanged):
            signal.connect(self._refresh)
        self.animation.currentTextChanged.connect(self._animation_changed)
        if saved.range != "custom" or not saved.end:
            self._animation_changed()
        self._refresh()
        self.resize(520, self.sizeHint().height())

    # --- settings ---------------------------------------------------------------------------
    def _stored(self) -> dict | None:
        if self.store is None:
            return None
        try:
            return json.loads(self.store.value(STORE_KEY, "") or "{}")
        except (TypeError, ValueError):
            return None

    def _default_folder(self) -> str:
        doc = self.viewport.state.doc
        base = Path(doc.path).parent if getattr(doc, "path", None) else Path.home() / "Videos"
        return str(base / "playblasts")

    def settings(self) -> pb.PlayblastSettings:
        return pb.PlayblastSettings(
            format=self.format.currentData(), folder=self.folder.text(), name=self.name.text().strip(),
            animation=self.animation.currentText(), range="custom" if self.custom.isChecked() else "animation",
            start=self.start.value(), end=self.end.value(), fps=self.fps.value(), scale=self.scale.currentData(),
            quality=self.quality.value(), bones=self.bones.isChecked(), grid=self.grid.isChecked(),
            hud=self.hud.isChecked(), repeat=self.repeat.value(), overwrite=self.overwrite.isChecked(),
            open_after=self.open_after.isChecked())

    def _remember(self) -> None:
        if self.store is not None:
            self.store.setValue(STORE_KEY, json.dumps(self.settings().to_dict()))

    # --- view -------------------------------------------------------------------------------
    def _animation_changed(self) -> None:
        """Whole animation: the frame boxes show its length."""
        doc = self.viewport.state.doc
        name = self.animation.currentText()
        if name in doc.data["animations"]:
            last = round(doc.animation_duration(name) * self.fps.value())
            self.start.setValue(0)
            self.end.setValue(last)

    def _refresh(self) -> None:
        s = self.settings()
        self.start.setEnabled(self.custom.isChecked())
        self.end.setEnabled(self.custom.isChecked())
        w, h = pb.capture_size(self.viewport, s)
        self.size_label.setText(f"{w} × {h} px")
        self.quality.setEnabled(s.format in ("jpeg", "avi", "webp"))
        name = s.animation or "animation"
        try:
            path = pb.output_path(s, name)
            if not pb.FORMATS[s.format][2]:
                path = path.with_name(f"{path.stem}_0000{path.suffix}  …")
            self.path_label.setText(f"Saves {path}")
        except EditorError as exc:
            self.path_label.setText(str(exc))
        self.run_button.setEnabled(bool(s.animation))

    # --- run --------------------------------------------------------------------------------
    def run(self) -> None:
        s = self.settings()
        self._remember()
        progress = QProgressDialog("Recording frames…", "Cancel", 0, 100, self)
        progress.setWindowTitle("Playblast")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        def step(done: int, total: int) -> bool:
            progress.setMaximum(max(1, total))
            progress.setValue(done)
            QApplication.processEvents()
            return not progress.wasCanceled()
        try:
            self.written = pb.playblast(self.viewport, s, step)
        except EditorError as exc:
            progress.close()
            if str(exc) != "Playblast cancelled":
                QMessageBox.warning(self, "Playblast", str(exc))
            return
        finally:
            progress.close()
        first = self.written[0]
        message = f"Saved {first}" if len(self.written) == 1 else \
            f"Saved {len(self.written)} frames in {first.parent}"
        if s.open_after:
            try:
                pb.reveal(first if pb.FORMATS[s.format][2] else first.parent)
            except OSError as exc:
                message += f"\n(Could not open it: {exc})"
        if parent := self.parent():
            label = getattr(parent, "status_label", None)
            if label is not None:
                label.setText(message.splitlines()[0])
        self.accept()
