"""File > Export, laid out like Spine's: Data (JSON, Binary), Image (GIF, PNG, APNG, PSD, JPEG, WebP)
and Video (AVI, MOV, MP4) on the left, that format's options on the right."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QRectF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..formats.atlas import PackSettings
from .document import EDITOR_STAMP, RUNTIME_STAMP, STAMP_40, Document, EditorError
from .form_widgets import PathField, SliderField, row, rule, section
from .media_export import KINDS, SINGLE_FILE, MediaExport, export_media, preview_frames
from .packer_dialogs import TexturePackerSettingsDialog, load_settings

VERSIONS = [
    (f"Spine 4.0 ({STAMP_40})", STAMP_40),
    (f"Spine 3.8 ({RUNTIME_STAMP}) runtimes", RUNTIME_STAMP),
    ("Spine 3.8.75 editor import", EDITOR_STAMP),
]
COMPRESSION = {0: "None", 1: "Fastest", 3: "Fast", 6: "Medium (default)", 9: "Best"}
GIF_QUALITY = {1: "Fastest", 4: "Fast", 8: "High (default)", 10: "Best"}
VISIBLE = {  # media rows shown per kind
    "encoding": {"avi", "mov"},
    "colors": {"gif"}, "color_dither": {"gif"}, "alpha_threshold": {"gif"}, "alpha_dither": {"gif"},
    "quality": {"gif"}, "jpeg_quality": {"jpeg", "webp", "avi", "mov"},
    "compression": {"png", "apng", "mov"}, "pack": {"png", "jpeg"},
    "repeat": SINGLE_FILE, "pause": SINGLE_FILE - {"psd"}, "output_type": SINGLE_FILE,
}


@dataclass
class ExportContext:
    """What the dialog needs from the editor."""
    doc: Document
    animation: str | None = None
    skin: str | None = None
    time: float = 0.0
    crop: Callable[[], QRectF | None] = lambda: None  # the world area the viewport shows
    project_name: str = "skeleton"
    start_dir: str = ""


@dataclass
class DataExport:
    folder: str = ""
    extension: str = ".json"
    javascript: bool = False
    pretty: bool = False
    version: str = STAMP_40
    nonessential: bool = True
    cleanup: bool = True
    warnings: bool = True
    pack: bool = False
    pack_settings: dict[str, Any] = field(default_factory=dict)


class ExportDialog(QDialog):
    def __init__(self, context: ExportContext, parent=None, store: QSettings | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setMinimumWidth(660)
        self.ctx, self.store = context, store
        self.written: list[Path] = []
        self.warnings: list[str] = []
        self._pack: dict[str, dict[str, Any]] = {}  # per kind
        self._color = QColor("#000000")
        self._matte_color = QColor("#ffffff")

        # left: the formats
        nav = QVBoxLayout()
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str, QRadioButton] = {}
        for group in ("Data", "Image", "Video"):
            nav.addWidget(section(group))
            for key, (label, g, _) in KINDS.items():
                if g == group:
                    button = QRadioButton(label)
                    self.kind_group.addButton(button)
                    self.kind_buttons[key] = button
                    nav.addWidget(button)
                    button.toggled.connect(lambda on, k=key: on and self._kind_changed(k))
        nav.addStretch(1)
        nav_widget = QWidget()
        nav_widget.setLayout(nav)
        nav_widget.setFixedWidth(110)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color: #555555;")

        self.title = section("")
        self.pages = QStackedWidget()
        self.pages.addWidget(self._data_page())
        self.pages.addWidget(self._media_page())
        right = QVBoxLayout()
        right.addWidget(self.title)
        right.addWidget(self.pages, 1)
        body = QHBoxLayout()
        body.addWidget(nav_widget)
        body.addWidget(divider)
        body.addLayout(right, 1)

        save, load = QPushButton("💾 Save"), QPushButton("📂 Load")
        save.setToolTip("Save these export settings to a file")
        load.setToolTip("Load export settings from a file")
        save.clicked.connect(self._save_file)
        load.clicked.connect(self._load_file)
        self.open_after = QCheckBox("Open after export")
        self.export_button, cancel = QPushButton("📤 Export"), QPushButton("⊘ Cancel")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self._export_clicked)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for w in (save, load):
            buttons.addWidget(w)
        buttons.addStretch(1)
        buttons.addWidget(self.open_after)
        buttons.addSpacing(16)
        buttons.addWidget(self.export_button)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(rule())
        layout.addLayout(buttons)

        self.kind = ""
        self._restore()

    # --- data page ------------------------------------------------------------------------

    def _data_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.folder = PathField("folder", store=self.store, key="favorites/folders")
        self.extension = QLineEdit(".json")
        self.extension.setFixedWidth(130)
        self.text_format = QComboBox()
        self.text_format.addItems(["JSON", "JavaScript"])
        self.text_format.setToolTip("JavaScript leaves the quotes off names (smaller; not strict JSON)")
        self.pretty = QCheckBox("Pretty print")
        self.version = QComboBox()
        for label, stamp in VERSIONS:
            self.version.addItem(label, stamp)
        self.nonessential = QCheckBox("Nonessential data")
        self.nonessential.setToolTip("Bone colours, mesh edges and sizes, fps and images path: the editor "
                                     "uses them, the runtimes do not")
        self.cleanup = QCheckBox("Animation clean up")
        self.cleanup.setToolTip("Leave out keys that change nothing")
        self.show_warnings = QCheckBox("Warnings")
        self.data_pack = QCheckBox("Pack")
        pack_settings = QPushButton("Pack settings…")
        pack_settings.clicked.connect(lambda: self._edit_pack("data"))
        self.format_row = row(self.text_format, 12, self.pretty)
        form.addRow("Output folder:", self.folder)
        form.addRow("Extension:", row(self.extension))
        form.addRow("Format:", self.format_row)
        form.addRow("Version:", row(self.version))
        form.addRow("Output:", row(self.nonessential, 12, self.cleanup))
        form.addRow("", row(self.show_warnings))
        form.addRow("Texture atlas:", row(self.data_pack, 12, pack_settings))
        self.data_note = QLabel()
        self.data_note.setWordWrap(True)
        self.data_note.setStyleSheet("color: #a9a9a9;")
        form.addRow(self.data_note)
        self.data_form = form
        self.version.currentIndexChanged.connect(self._sync_data)
        return page

    def _sync_data(self) -> None:
        binary = self.kind == "binary"
        if binary:
            self.version.setCurrentIndex(0)
        self.version.setEnabled(not binary)
        self.data_form.setRowVisible(self.format_row, not binary)
        notes = []
        if self.version.currentData() != STAMP_40:
            notes.append("Spine 3.8 has no split or colour channel timelines and other curves; anything "
                         "3.8 cannot express is converted to extra keys.")
        if binary:
            notes.append("Binary (.skel) is Spine 4.0: smaller and faster to load.")
        self.data_note.setText(" ".join(notes))

    def data_settings(self) -> DataExport:
        return DataExport(self.folder.text(), self.extension.text().strip() or KINDS[self.kind or "json"][2],
                          self.text_format.currentIndex() == 1, self.pretty.isChecked(),
                          str(self.version.currentData()), self.nonessential.isChecked(), self.cleanup.isChecked(),
                          self.show_warnings.isChecked(), self.data_pack.isChecked(),
                          dict(self._pack.get("data") or load_settings(self.store).to_dict()))

    def set_data(self, d: DataExport) -> None:
        self.folder.setText(d.folder)
        self.extension.setText(d.extension)
        self.text_format.setCurrentIndex(1 if d.javascript else 0)
        self.pretty.setChecked(d.pretty)
        self.version.setCurrentIndex(max(0, self.version.findData(d.version)))
        self.nonessential.setChecked(d.nonessential)
        self.cleanup.setChecked(d.cleanup)
        self.show_warnings.setChecked(d.warnings)
        self.data_pack.setChecked(d.pack)
        if d.pack_settings:
            self._pack["data"] = dict(d.pack_settings)
        self._sync_data()

    # --- media page -----------------------------------------------------------------------

    def _media_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.rows: dict[str, QWidget] = {}

        def add(key: str, label: str, widget: QWidget) -> None:
            form.addRow(label, widget)
            self.rows[key] = widget

        self.export_type = QComboBox()
        for label, key in (("Animation", "animation"), ("Current pose", "current"), ("Setup pose", "setup")):
            self.export_type.addItem(label, key)
        defaults, preview = QPushButton("↺ Defaults"), QPushButton("👁 Preview")
        defaults.clicked.connect(self._defaults)
        preview.clicked.connect(self.preview)
        add("type", "Export type:", row(self.export_type, 24, defaults, preview))

        names = self.ctx.doc.animation_names()
        self.anim_current, self.anim_all, self.anim_one = (QRadioButton("Current"), QRadioButton("All"),
                                                           QRadioButton(""))
        self.anim_group = QButtonGroup(self)
        for b in (self.anim_current, self.anim_all, self.anim_one):
            self.anim_group.addButton(b)
        self.anim_name = QComboBox()
        self.anim_name.addItems(names)
        if self.ctx.animation in names:
            self.anim_name.setCurrentText(self.ctx.animation)
        self.anim_name.activated.connect(lambda _i: self.anim_one.setChecked(True))
        self.anim_current.setChecked(True)
        add("animations", "Animations:", row(self.anim_current, self.anim_all, self.anim_one, self.anim_name))

        self.skin_current, self.skin_all, self.skin_all_none = (QRadioButton("Current"), QRadioButton("All"),
                                                                QRadioButton("All + <none>"))
        self.skin_group = QButtonGroup(self)
        for b in (self.skin_current, self.skin_all, self.skin_all_none):
            self.skin_group.addButton(b)
        self.skin_current.setChecked(True)
        add("skins", "Skins:", row(self.skin_current, self.skin_all, self.skin_all_none))

        self.output_type = QComboBox()
        self.output_type.addItem("Single file", "single")
        self.output_type.addItem("File per animation and skin", "each")
        add("output_type", "Output type:", row(self.output_type))
        self.output = PathField("save", store=self.store, key="favorites/files")
        add("output", "Output file:", self.output)
        self.repeat = SliderField(1, 100, 1)
        add("repeat", "Animation repeat:", self.repeat)
        self.pause = SliderField(0, 10, 0, decimals=2, note="seconds")
        add("pause", "Pause after:", self.pause)

        self.r_bones, self.r_images, self.r_others = QCheckBox("Bones"), QCheckBox("Images"), QCheckBox("Others")
        self.r_others.setToolTip("Bounding boxes, clipping, paths and points")
        self.linear = QCheckBox("Linear filtering")
        add("render", "Render:", row(self.r_bones, self.r_images, self.r_others, self.linear))
        self.msaa = QComboBox()
        for n in (1, 2, 4, 8, 16):
            self.msaa.addItem("None" if n == 1 else f"{n}x", n)
        add("msaa", "Multisample AA:", row(self.msaa))
        self.crop = QCheckBox("Crop")
        self.crop.setToolTip("Export only the area the viewport shows")
        add("crop", "Viewport:", row(self.crop))
        self.size_mode = QComboBox()
        for label, key in (("Scale", "scale"), ("Width", "width"), ("Height", "height")):
            self.size_mode.addItem(label, key)
        self.size = SliderField(1, 4096, 100)
        self.size_mode.currentIndexChanged.connect(self._size_mode_changed)
        add("size", "Size:", row(self.size_mode, self.size, stretch=False))
        self.use_range = QCheckBox("Range")
        self.range_start, self.range_end = QSpinBox(), QSpinBox()
        for spin in (self.range_start, self.range_end):
            spin.setRange(0, 100000)
        self.range_end.setValue(30)
        self.use_range.toggled.connect(lambda on: (self.range_start.setEnabled(on), self.range_end.setEnabled(on)))
        add("range", "Frames:", row(self.use_range, self.range_start, QLabel("to"), self.range_end))
        form.addRow(rule())

        self.encoding = QComboBox()
        add("encoding", "Encoding:", row(self.encoding))
        self.colors = SliderField(2, 256, 256)
        add("colors", "Colors:", self.colors)
        self.color_dither = SliderField(0, 100, 50)
        add("color_dither", "Color dither:", self.color_dither)
        self.alpha_threshold = SliderField(0, 255, 8)
        add("alpha_threshold", "Alpha threshold:", self.alpha_threshold)
        self.alpha_dither = SliderField(0, 100, 40)
        add("alpha_dither", "Alpha dither:", self.alpha_dither)
        self.transparent = QCheckBox("Transparent")
        self.matte = QCheckBox("Matte")
        self.matte.setToolTip("Blend soft edges toward the matte colour (GIF)")
        self.color_button = QPushButton()
        self.color_button.setFixedWidth(70)
        self.color_button.clicked.connect(self._pick_color)
        self.transparent.toggled.connect(self._sync_media)
        self.matte.toggled.connect(self._sync_media)
        add("background", "Background:", row(self.transparent, self.matte, self.color_button))
        self.quality = SliderField(1, 10, 8)
        add("quality", "Quality:", self.quality)
        self.jpeg_quality = SliderField(1, 100, 90)
        add("jpeg_quality", "Quality:", self.jpeg_quality)
        self.compression = SliderField(0, 9, 6)
        add("compression", "Compression:", self.compression)
        self.fps = SliderField(1, 120, 20)
        add("fps", "FPS:", self.fps)
        self.forever, self.include_last = QCheckBox("Forever"), QCheckBox("Include last frame")
        add("playback", "Playback:", row(self.forever, self.include_last))
        self.media_pack = QCheckBox("Pack")
        media_pack_settings = QPushButton("Pack settings…")
        media_pack_settings.clicked.connect(lambda: self._edit_pack(self.kind))
        add("pack", "Texture atlas:", row(self.media_pack, 12, media_pack_settings))
        self.media_form = form
        for w in (self.compression, self.quality, self.fps):
            w.changed.connect(self._notes)
        self.export_type.currentIndexChanged.connect(self._sync_media)
        self.encoding.currentIndexChanged.connect(self._sync_media)
        return page

    def _size_mode_changed(self) -> None:
        self.size.note.setText("%" if self.size_mode.currentData() == "scale" else "pixels")

    def _notes(self) -> None:
        self.compression.note.setText(COMPRESSION.get(self.compression.value(), ""))
        self.quality.note.setText(GIF_QUALITY.get(self.quality.value(), ""))
        self.fps.note.setText(f"{1 / self.fps.value():.3g} seconds/frame")

    def _sync_media(self) -> None:
        kind = self.kind
        if not kind or KINDS[kind][1] == "Data":
            return
        form = self.media_form
        for key, kinds in VISIBLE.items():
            form.setRowVisible(self.rows[key], kind in kinds)
        animation = self.export_type.currentData() == "animation"
        form.setRowVisible(self.rows["animations"], animation)
        form.setRowVisible(self.rows["range"], animation)
        for key in ("repeat", "pause"):
            form.setRowVisible(self.rows[key], animation and kind in VISIBLE[key])
        form.labelForField(self.rows["output"]).setText("Output file:" if kind in SINGLE_FILE else "Output prefix:")
        encoding = self.encoding.currentData()
        opaque_only = kind in ("jpeg", "mp4") or (kind == "avi" and encoding == "mjpeg") or \
            (kind == "mov" and encoding == "jpeg")
        self.transparent.setEnabled(not opaque_only)
        if opaque_only:
            self.transparent.setChecked(False)
        self.matte.setVisible(kind == "gif")
        self.matte.setEnabled(self.transparent.isChecked())
        matte = self.transparent.isChecked() and kind == "gif"
        self.color_button.setEnabled(not self.transparent.isChecked() or (matte and self.matte.isChecked()))
        color = self._matte_color if matte else self._color
        self.color_button.setText(color.name())
        self.color_button.setStyleSheet(f"background: {color.name()}; color: "
                                        f"{'#000' if color.lightness() > 140 else '#fff'};")
        self.forever.setVisible(kind in ("gif", "apng", "webp"))
        if kind == "mov":
            form.setRowVisible(self.rows["compression"], encoding == "png")
            form.setRowVisible(self.rows["jpeg_quality"], encoding == "jpeg")
        if kind == "avi":
            form.setRowVisible(self.rows["jpeg_quality"], encoding == "mjpeg")
        self._notes()

    def _pick_color(self) -> None:
        matte = self.transparent.isChecked() and self.kind == "gif"
        chosen = QColorDialog.getColor(self._matte_color if matte else self._color, self, "Colour")
        if chosen.isValid():
            if matte:
                self._matte_color = chosen
            else:
                self._color = chosen
            self._sync_media()

    def media_settings(self) -> MediaExport:
        animations = "all" if self.anim_all.isChecked() else ("one" if self.anim_one.isChecked() else "current")
        skins = "all" if self.skin_all.isChecked() else ("all_none" if self.skin_all_none.isChecked() else "current")
        crop = None
        if self.crop.isChecked():
            rect = self.ctx.crop()
            if rect is not None and not rect.isEmpty():
                crop = (rect.x(), rect.y(), rect.width(), rect.height())
        return MediaExport(
            kind=self.kind, export_type=self.export_type.currentData(), animations=animations,
            animation=self.anim_name.currentText(), skins=skins, output=self.output.text(),
            output_type=self.output_type.currentData(), repeat=self.repeat.value(), pause_after=self.pause.value(),
            bones=self.r_bones.isChecked(), images=self.r_images.isChecked(), others=self.r_others.isChecked(),
            linear_filtering=self.linear.isChecked(), msaa=self.msaa.currentData(), crop=crop,
            size_mode=self.size_mode.currentData(), size=self.size.value(),
            frame_range=(self.range_start.value(), self.range_end.value()) if self.use_range.isChecked() else None,
            fps=self.fps.value(), include_last_frame=self.include_last.isChecked(), forever=self.forever.isChecked(),
            transparent=self.transparent.isChecked(), background=self._color.name(),
            matte=self._matte_color.name() if self.matte.isChecked() else None, colors=self.colors.value(),
            color_dither=self.color_dither.value(), alpha_threshold=self.alpha_threshold.value(),
            alpha_dither=self.alpha_dither.value(), quality=self.quality.value(),
            compression=self.compression.value(), jpeg_quality=self.jpeg_quality.value(),
            encoding=self.encoding.currentData() or "png", pack=self.media_pack.isChecked(),
            pack_settings=dict(self._pack.get(self.kind) or load_settings(self.store).to_dict()))

    def set_media(self, s: MediaExport) -> None:
        self.export_type.setCurrentIndex(max(0, self.export_type.findData(s.export_type)))
        {"all": self.anim_all, "one": self.anim_one}.get(s.animations, self.anim_current).setChecked(True)
        if s.animation and self.anim_name.findText(s.animation) >= 0:
            self.anim_name.setCurrentText(s.animation)
        {"all": self.skin_all, "all_none": self.skin_all_none}.get(s.skins, self.skin_current).setChecked(True)
        self.output.setText(s.output)
        self.output_type.setCurrentIndex(max(0, self.output_type.findData(s.output_type)))
        self.repeat.set(s.repeat)
        self.pause.set(s.pause_after)
        self.r_bones.setChecked(s.bones)
        self.r_images.setChecked(s.images)
        self.r_others.setChecked(s.others)
        self.linear.setChecked(s.linear_filtering)
        self.msaa.setCurrentIndex(max(0, self.msaa.findData(s.msaa)))
        self.crop.setChecked(s.crop is not None)
        self.size_mode.setCurrentIndex(max(0, self.size_mode.findData(s.size_mode)))
        self._size_mode_changed()
        self.size.set(s.size)
        ranged = s.frame_range is not None
        self.use_range.setChecked(ranged)
        self.range_start.setEnabled(ranged)
        self.range_end.setEnabled(ranged)
        if ranged:
            self.range_start.setValue(s.frame_range[0])
            self.range_end.setValue(s.frame_range[1])
        self.fps.set(s.fps)
        self.include_last.setChecked(s.include_last_frame)
        self.forever.setChecked(s.forever)
        self._color = QColor(s.background)
        if s.matte:
            self._matte_color = QColor(s.matte)
        self.encoding.blockSignals(True)
        self.encoding.clear()
        if self.kind == "avi":
            self.encoding.addItem("Motion JPEG", "mjpeg")
            self.encoding.addItem("Uncompressed (keeps alpha)", "raw")
        elif self.kind == "mov":
            self.encoding.addItem("PNG (keeps alpha)", "png")
            self.encoding.addItem("JPEG", "jpeg")
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(s.encoding)))
        self.encoding.blockSignals(False)
        self.transparent.setChecked(s.transparent)
        self.matte.setChecked(s.matte is not None)
        self.colors.set(s.colors)
        self.color_dither.set(s.color_dither)
        self.alpha_threshold.set(s.alpha_threshold)
        self.alpha_dither.set(s.alpha_dither)
        self.quality.set(s.quality)
        self.compression.set(s.compression)
        self.jpeg_quality.set(s.jpeg_quality)
        self.media_pack.setChecked(s.pack)
        if s.pack_settings:
            self._pack[self.kind] = dict(s.pack_settings)
        self._sync_media()

    def default_media(self, kind: str) -> MediaExport:
        name = self.ctx.project_name + (f"_{self.ctx.animation}" if self.ctx.animation else "")
        base = str(Path(self.ctx.start_dir or Path.home()) / name)
        s = MediaExport(kind=kind, output=base + (KINDS[kind][2] if kind in SINGLE_FILE else ""), fps=20)
        if kind in ("jpeg", "mp4", "avi"):
            s.transparent = False
        s.encoding = {"avi": "mjpeg", "mov": "png"}.get(kind, s.encoding)
        return s

    def _defaults(self) -> None:
        current = self.output.text()
        fresh = self.default_media(self.kind)
        fresh.output = current or fresh.output
        self.set_media(fresh)

    # --- switching kinds, remembering -------------------------------------------------------

    def select_kind(self, kind: str) -> None:
        self.kind_buttons[kind].setChecked(True)

    def _kind_changed(self, kind: str) -> None:
        if self.kind:
            self._remember_current()
        self.kind = kind
        label, group, _ = KINDS[kind]
        names = {"mov": "QuickTime MOV", "mp4": "MP4 (H.264)", "webp": "animated WebP"}
        self.title.setText(f"Export {names.get(kind, label)}")
        stored = self._stored(f"export2/{kind}")
        if group == "Data":
            self.pages.setCurrentIndex(0)
            try:
                data = DataExport(**stored) if stored else None
            except TypeError:
                data = None
            self.set_data(data or DataExport(folder=self.ctx.start_dir, extension=KINDS[kind][2]))
        else:
            self.pages.setCurrentIndex(1)
            try:
                settings = MediaExport.from_dict(stored) if stored else self.default_media(kind)
            except TypeError:
                settings = self.default_media(kind)
            self.set_media(settings)

    def _stored(self, key: str) -> dict[str, Any] | None:
        if self.store is None:
            return None
        try:
            value = json.loads(str(self.store.value(key, "")))
            return value if isinstance(value, dict) else None
        except ValueError:
            return None

    def current_dict(self) -> dict[str, Any]:
        if KINDS[self.kind][1] == "Data":
            return dict(vars(self.data_settings()))
        d = self.media_settings().to_dict()
        d["crop"] = (0, 0, 0, 0) if self.crop.isChecked() else None  # the viewport area is taken at export time
        return d

    def _remember_current(self) -> None:
        if self.store is not None and self.kind:
            self.store.setValue(f"export2/{self.kind}", json.dumps(self.current_dict()))
            self.store.setValue("export2/kind", self.kind)
            self.store.setValue("export2/open_after", str(self.open_after.isChecked()).lower())

    def _restore(self) -> None:
        kind = "json"
        if self.store is not None:
            stored = str(self.store.value("export2/kind", "json"))
            kind = stored if stored in KINDS else "json"
            self.open_after.setChecked(str(self.store.value("export2/open_after", "false")) == "true")
        self.select_kind(kind)

    def save_settings_file(self, path: Path) -> None:
        Path(path).write_text(json.dumps({"kind": self.kind, "settings": self.current_dict()}, indent=1),
                              encoding="utf-8")

    def load_settings_file(self, path: Path) -> None:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        kind, values = saved["kind"], saved["settings"]
        if kind not in KINDS:
            raise ValueError(f"unknown format '{kind}'")
        self.kind_buttons[kind].blockSignals(True)
        self.kind_buttons[kind].setChecked(True)
        self.kind_buttons[kind].blockSignals(False)
        self.kind = kind
        self.title.setText(f"Export {KINDS[kind][0]}")
        if KINDS[kind][1] == "Data":
            self.pages.setCurrentIndex(0)
            self.set_data(DataExport(**values))
        else:
            self.pages.setCurrentIndex(1)
            self.set_media(MediaExport.from_dict(values))

    def _save_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save export settings", self.ctx.start_dir,
                                              "Export settings (*.json)")
        if path:
            self.save_settings_file(Path(path))

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load export settings", self.ctx.start_dir,
                                              "Export settings (*.json)")
        if path:
            try:
                self.load_settings_file(Path(path))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                QMessageBox.warning(self, "Export", f"Cannot load {Path(path).name}: {exc}")

    def _edit_pack(self, key: str) -> None:
        current = self._pack.get(key)
        settings = PackSettings.from_dict(current) if current else load_settings(self.store)
        dialog = TexturePackerSettingsDialog(settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._pack[key] = dialog.settings().to_dict()

    # --- doing it -----------------------------------------------------------------------------

    def preview(self) -> None:
        try:
            frames = preview_frames(self.ctx.doc, self.media_settings(), self.ctx.animation, self.ctx.skin,
                                    self.ctx.time)
        except EditorError as exc:
            QMessageBox.warning(self, "Preview", str(exc))
            return
        PreviewDialog(frames, self.fps.value(), self).exec()

    def run_export(self, overwrite: bool = False) -> list[Path]:
        """Export with the current settings. Raises EditorError."""
        doc = self.ctx.doc
        self.warnings = []
        if KINDS[self.kind][1] == "Data":
            d = self.data_settings()
            if not d.folder:
                raise EditorError("Choose the output folder")
            out = Path(d.folder) / f"{self.ctx.project_name}{d.extension}"
            pack = PackSettings.from_dict(d.pack_settings) if d.pack else None
            warnings = doc.export_spine_json(out, d.version, overwrite=overwrite, binary=self.kind == "binary",
                                             atlas=pack, nonessential=d.nonessential, pretty=d.pretty,
                                             cleanup=d.cleanup, javascript=d.javascript)
            self.warnings = warnings if d.warnings else []
            return [out]
        return export_media(doc, self.media_settings(), self.ctx.animation, self.ctx.skin, self.ctx.time,
                            overwrite=overwrite)

    def _export_clicked(self) -> None:
        self._remember_current()
        try:
            try:
                self.written = self.run_export()
            except EditorError as exc:
                if "already exists" not in str(exc):
                    raise
                answer = QMessageBox.question(self, "Export", f"{exc}. Replace the existing files?")
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.written = self.run_export(overwrite=True)
        except EditorError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        if self.warnings:
            QMessageBox.information(self, "Export", "Exported with notes:\n\n" + "\n".join(self.warnings[:15]))
        if self.open_after.isChecked() and self.written:
            target = self.written[0] if len(self.written) == 1 else self.written[0].parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))  # Explorer, Finder or its app
        self.accept()


class PreviewDialog(QDialog):
    """Plays the frames the export will write, on a grey background."""

    def __init__(self, frames, fps: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export preview")
        self.frames = [QPixmap.fromImage(f) for f in frames]
        self.index = 0
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background-color: #6a6a6a;")
        size = self.frames[0].size() if self.frames else None
        if size is not None:
            self.label.setMinimumSize(min(size.width(), 900), min(size.height(), 700))
        info = QLabel(f"{len(self.frames)} frame(s), {size.width()} x {size.height()} px" if size else "No frames")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(row(info, close))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next)
        self.timer.start(max(8, round(1000 / max(1.0, fps))))
        self._next()

    def _next(self) -> None:
        if not self.frames:
            return
        pix = self.frames[self.index % len(self.frames)]
        if pix.width() > 900 or pix.height() > 700:
            pix = pix.scaled(900, 700, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(pix)
        self.index += 1
