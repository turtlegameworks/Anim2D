"""Texture Packer settings (laid out like Spine's), File > Texture Packer and File > Texture Unpacker."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..formats import atlas as packer
from .form_widgets import PathField, SliderField, row, section

RESAMPLE_LABELS = [("Bicubic", "bicubic"), ("Bilinear", "bilinear"), ("Nearest", "nearest"),
                   ("Lanczos", "lanczos"), ("Box", "box")]


def spine_defaults() -> packer.PackSettings:
    """The settings the Texture Packer dialog starts from (Spine's defaults)."""
    return packer.PackSettings(max_width=2048, max_height=2048, padding=2, padding_y=2, strip_whitespace=True,
                               strip_whitespace_y=True, rotation=True, alias=True, ignore_blank=False,
                               alpha_threshold=3, edge_padding=True, duplicate_padding=False, min_width=16,
                               min_height=16, premultiply_alpha=True, bleed=False, resample="bicubic",
                               limit_memory=True)


def load_settings(store: QSettings | None, key: str = "packer/settings") -> packer.PackSettings:
    if store is not None:
        try:
            return packer.PackSettings.from_dict(json.loads(str(store.value(key, ""))))
        except (TypeError, ValueError):
            pass
    return spine_defaults()


class TexturePackerSettingsDialog(QDialog):
    def __init__(self, settings: packer.PackSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Texture Packer Settings")
        s = settings or spine_defaults()

        def check(text: str, value: bool) -> QCheckBox:
            box = QCheckBox(text)
            box.setChecked(value)
            return box

        def combo(items, value) -> QComboBox:
            c = QComboBox()
            for item in items:
                label, data = item if isinstance(item, tuple) else (item, item)
                c.addItem(label, data)
            i = c.findData(value)
            c.setCurrentIndex(max(0, i))
            return c

        # Regions
        self.strip_x = check("Strip whitespace X", s.strip_x)
        self.strip_y = check("Strip whitespace Y", s.strip_y)
        self.rotation = check("Rotation", s.rotation)
        self.alias = check("Alias", s.alias)
        self.ignore_blank = check("Ignore blank images", s.ignore_blank)
        self.alpha_threshold = SliderField(0, 255, s.alpha_threshold)
        regions = QFormLayout()
        regions.addRow(row(self.strip_x, self.strip_y))
        regions.addRow(row(self.rotation, self.alias, self.ignore_blank))
        regions.addRow("Alpha threshold:", self.alpha_threshold)
        # Region padding
        self.padding_x = SliderField(0, 32, s.pad_x)
        self.padding_y = SliderField(0, 32, s.pad_y)
        self.edge_padding = check("Edge padding", s.edge_padding)
        self.duplicate_padding = check("Duplicate padding", s.duplicate_padding)
        padding = QFormLayout()
        padding.addRow("Padding X:", self.padding_x)
        padding.addRow("Padding Y:", self.padding_y)
        padding.addRow(row(self.edge_padding, self.duplicate_padding))
        # Pages
        self.min_width = SliderField(1, 8192, s.min_width or 16)
        self.min_height = SliderField(1, 8192, s.min_height or 16)
        self.max_width = SliderField(16, 8192, s.max_width)
        self.max_height = SliderField(16, 8192, s.max_height)
        self.pot = check("Power of two", s.power_of_two)
        self.div4 = check("Divisible by 4", s.divisible_by_4)
        self.square = check("Square", s.square)
        pages = QFormLayout()
        pages.addRow("Min width:", self.min_width)
        pages.addRow("Min height:", self.min_height)
        pages.addRow("Max width:", self.max_width)
        pages.addRow("Max height:", self.max_height)
        pages.addRow(row(self.pot, self.div4, self.square))
        # Runtime
        self.filter_min = combo(packer.FILTERS, s.filter_min)
        self.filter_mag = combo(packer.FILTERS, s.filter_mag)
        self.wrap_x = combo(packer.WRAPS, s.wrap_x)
        self.wrap_y = combo(packer.WRAPS, s.wrap_y)
        self.format = combo(packer.FORMATS, s.format)
        runtime = QFormLayout()
        runtime.addRow("Filter min:", self.filter_min)
        runtime.addRow("Filter mag:", self.filter_mag)
        runtime.addRow("Wrap X:", self.wrap_x)
        runtime.addRow("Wrap Y:", self.wrap_y)
        runtime.addRow("Format:", self.format)
        # Output
        self.output_format = combo([("PNG", "png"), ("JPG", "jpg")], s.output_format)
        self.packing = combo([("Rectangles", "rectangles"), ("Grid", "grid")], s.packing)
        self.pma = check("Premultiply alpha", s.premultiply_alpha)
        self.bleed = check("Bleed", s.bleed)
        output = QVBoxLayout()
        output.addWidget(row(QLabel("Format:"), self.output_format, 12, QLabel("Packing:"), self.packing))
        output.addWidget(row(self.pma, self.bleed))
        grid = QGridLayout()
        grid.addWidget(QLabel("Scale:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(QLabel("Suffix:"), 1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(QLabel("Resample:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        scales = s.scales or [(s.scale, "", s.resample)]
        self.scale_fields, self.suffix_fields, self.resample_fields = [], [], []
        for col in range(5):
            scale, suffix, resample = scales[col] if col < len(scales) else ("", "", "bicubic")
            sf = QLineEdit(str(scale) if scale != "" else "")
            sf.setFixedWidth(80)
            xf = QLineEdit(suffix)
            xf.setFixedWidth(80)
            rf = combo(RESAMPLE_LABELS, resample)
            for r, w in enumerate((sf, xf, rf)):
                grid.addWidget(w, r, col + 1)
            self.scale_fields.append(sf)
            self.suffix_fields.append(xf)
            self.resample_fields.append(rf)
        output.addLayout(grid)
        # Options
        self.atlas_extension = QLineEdit(s.atlas_extension)
        self.atlas_extension.setFixedWidth(100)
        self.flatten = check("Flatten paths", s.flatten_paths)
        self.indexes = check("Use indexes", s.use_indexes)
        self.debug = check("Debug", s.debug)
        self.fast = check("Fast", s.fast)
        self.limit_memory = check("Limit memory", s.limit_memory)
        options = QFormLayout()
        options.addRow("Atlas extension:", self.atlas_extension)
        options.addRow(row(self.flatten))
        options.addRow(row(self.indexes, self.debug))
        options.addRow(row(self.fast, self.limit_memory))

        body = QGridLayout()
        for (title, lay), r, c in (((("Regions", regions)), 0, 0), (("Region Padding", padding), 0, 1),
                                   (("Pages", pages), 1, 0), (("Runtime", runtime), 1, 1),
                                   (("Output", output), 2, 0), (("Options", options), 2, 1)):
            box = QWidget()
            v = QVBoxLayout(box)
            v.addWidget(section(title))
            v.addLayout(lay)
            v.addStretch(1)
            body.addWidget(box, r, c)
        save, load, defaults = QPushButton("💾 Save"), QPushButton("📂 Load"), QPushButton("↺ Defaults")
        ok, cancel = QPushButton("✔ OK"), QPushButton("⊘ Cancel")
        save.clicked.connect(self._save)
        load.clicked.connect(self._load)
        defaults.clicked.connect(lambda: self._apply(spine_defaults()))
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        ok.setDefault(True)
        buttons = QHBoxLayout()
        for b in (save, load, defaults):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addLayout(buttons)

    def settings(self) -> packer.PackSettings:
        scales = []
        for sf, xf, rf in zip(self.scale_fields, self.suffix_fields, self.resample_fields):
            try:
                value = float(sf.text())
            except ValueError:
                continue
            if value > 0:
                scales.append((value, xf.text().strip(), rf.currentData()))
        if not scales:
            scales = [(1.0, "", "bicubic")]
        single = len(scales) == 1 and scales[0][1] == ""
        return packer.PackSettings(
            max_width=self.max_width.value(), max_height=self.max_height.value(), padding=self.padding_x.value(),
            padding_y=self.padding_y.value(), strip_whitespace=self.strip_x.isChecked(),
            strip_whitespace_y=self.strip_y.isChecked(), rotation=self.rotation.isChecked(),
            alias=self.alias.isChecked(), ignore_blank=self.ignore_blank.isChecked(),
            alpha_threshold=self.alpha_threshold.value(), edge_padding=self.edge_padding.isChecked(),
            duplicate_padding=self.duplicate_padding.isChecked(), min_width=self.min_width.value(),
            min_height=self.min_height.value(), power_of_two=self.pot.isChecked(),
            divisible_by_4=self.div4.isChecked(), square=self.square.isChecked(),
            filter_min=self.filter_min.currentData(), filter_mag=self.filter_mag.currentData(),
            wrap_x=self.wrap_x.currentData(), wrap_y=self.wrap_y.currentData(), format=self.format.currentData(),
            output_format=self.output_format.currentData(), packing=self.packing.currentData(),
            premultiply_alpha=self.pma.isChecked(), bleed=self.bleed.isChecked(),
            scale=scales[0][0], resample=scales[0][2], scales=[] if single else scales,
            atlas_extension=self.atlas_extension.text().strip() or ".atlas", flatten_paths=self.flatten.isChecked(),
            use_indexes=self.indexes.isChecked(), debug=self.debug.isChecked(), fast=self.fast.isChecked(),
            limit_memory=self.limit_memory.isChecked())

    def _apply(self, s: packer.PackSettings) -> None:
        fresh = TexturePackerSettingsDialog(s)
        for name, value in vars(fresh).items():
            mine = getattr(self, name, None)
            if isinstance(value, QCheckBox):
                mine.setChecked(value.isChecked())
            elif isinstance(value, SliderField):
                mine.set(value.value())
            elif isinstance(value, QComboBox):
                mine.setCurrentIndex(value.currentIndex())
            elif isinstance(value, QLineEdit):
                mine.setText(value.text())
        for mine, theirs in ((self.scale_fields, fresh.scale_fields), (self.suffix_fields, fresh.suffix_fields)):
            for a, b in zip(mine, theirs):
                a.setText(b.text())
        for a, b in zip(self.resample_fields, fresh.resample_fields):
            a.setCurrentIndex(b.currentIndex())
        fresh.deleteLater()

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save packer settings", "", "Pack settings (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.settings().to_dict(), indent=1), encoding="utf-8")

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load packer settings", "", "Pack settings (*.json)")
        if path:
            try:
                self._apply(packer.PackSettings.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Texture Packer", f"Cannot load {Path(path).name}: {exc}")


class TexturePackDialog(QDialog):
    """File > Texture Packer: pack every image in a folder into an atlas."""

    def __init__(self, store: QSettings | None = None, parent=None, input_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Texture Packer")
        self.store = store
        get = (lambda k, d: str(store.value(k, d))) if store is not None else (lambda k, d: d)
        self.input = PathField("folder", store=store, key="favorites/folders")
        self.input.setText(get("packer/input", input_dir))
        self.output = PathField("folder", store=store, key="favorites/folders")
        self.output.setText(get("packer/output", ""))
        self.name = QLineEdit(get("packer/name", "skeleton"))
        self.settings = load_settings(store)
        settings_button = QPushButton("⚙ Settings…")
        settings_button.clicked.connect(self._edit_settings)
        pack, cancel = QPushButton("📦 Pack"), QPushButton("⊘ Cancel")
        pack.clicked.connect(self.pack)
        cancel.clicked.connect(self.reject)
        pack.setDefault(True)
        form = QFormLayout()
        form.addRow("Input folder:", self.input)
        form.addRow("Output folder:", self.output)
        form.addRow("Atlas name:", self.name)
        buttons = QHBoxLayout()
        buttons.addWidget(settings_button)
        buttons.addStretch(1)
        buttons.addWidget(pack)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.resize(640, 160)
        self.written: list[Path] = []

    def _edit_settings(self) -> None:
        dialog = TexturePackerSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings()
            if self.store is not None:
                self.store.setValue("packer/settings", json.dumps(self.settings.to_dict()))

    def pack(self) -> None:
        if not self.input.text() or not self.output.text() or not self.name.text().strip():
            QMessageBox.warning(self, "Texture Packer", "Choose the input folder, output folder and atlas name")
            return
        try:
            self.written = packer.pack_folder(Path(self.input.text()), Path(self.output.text()),
                                              self.name.text().strip(), self.settings)
        except (packer.PackError, OSError) as exc:
            QMessageBox.warning(self, "Texture Packer", str(exc))
            return
        if self.store is not None:
            for key, value in (("input", self.input.text()), ("output", self.output.text()),
                               ("name", self.name.text().strip())):
                self.store.setValue(f"packer/{key}", value)
        self.accept()


class TextureUnpackDialog(QDialog):
    """File > Texture Unpacker: write each region of an atlas back out as its own image."""

    def __init__(self, store: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Texture Unpacker")
        self.store = store
        get = (lambda k: str(store.value(k, ""))) if store is not None else (lambda k: "")
        self.atlas = PathField("open", "Texture atlas (*.atlas *.atlas.txt *.txt);;All files (*)", store,
                               "favorites/files")
        self.atlas.setText(get("unpacker/atlas"))
        self.images = PathField("folder", store=store, key="favorites/folders")
        self.images.edit.setPlaceholderText("Same folder as the atlas")
        self.output = PathField("folder", store=store, key="favorites/folders")
        self.output.setText(get("unpacker/output"))
        unpack, cancel = QPushButton("📤 Unpack"), QPushButton("⊘ Cancel")
        unpack.clicked.connect(self.unpack)
        cancel.clicked.connect(self.reject)
        unpack.setDefault(True)
        form = QFormLayout()
        form.addRow("Atlas file:", self.atlas)
        form.addRow("Images folder:", self.images)
        form.addRow("Output folder:", self.output)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(unpack)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.resize(640, 160)
        self.written: list[Path] = []

    def unpack(self) -> None:
        if not self.atlas.text() or not self.output.text():
            QMessageBox.warning(self, "Texture Unpacker", "Choose the atlas file and the output folder")
            return
        try:
            self.written = packer.unpack(Path(self.atlas.text()), Path(self.output.text()),
                                         Path(self.images.text()) if self.images.text() else None)
        except (packer.PackError, OSError) as exc:
            QMessageBox.warning(self, "Texture Unpacker", str(exc))
            return
        if self.store is not None:
            self.store.setValue("unpacker/atlas", self.atlas.text())
            self.store.setValue("unpacker/output", self.output.text())
        self.accept()
