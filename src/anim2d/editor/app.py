"""Entry point: anim2d [project.anim2d | skeleton.json | project.spine]"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QMessageBox

from ..resources import apply_app_icon
from . import APP_NAME
from .document import PROJECT_SUFFIXES, Document, EditorError
from .main_window import EditorWindow
from .theme import apply_theme


def _self_test(window: EditorWindow, out_dir: Path) -> dict:
    """Build check (anim2d --self-test DIR): build a rig, insert an FX, export, screenshot."""
    import json
    from ..models.settings import validate_settings
    from .fx_insert import insert_fx
    out_dir.mkdir(parents=True, exist_ok=True)
    library = window.library
    report = {"presets": sorted(library.presets), "library_errors": library.errors}
    images = out_dir / "images"
    images.mkdir(exist_ok=True)
    flash = library.get("impact_basic").image_map()["flash"]
    (images / "body.png").write_bytes(flash.read_bytes())
    doc = Document(images_dir=images)
    doc.add_bone("root", "arm", y=100, length=120)
    doc.add_slot("arm", "body", image="body")
    doc.add_animation("attack")
    doc.set_bone_key("attack", "arm", "rotate", 0.5, value=45)
    settings, _ = validate_settings(library.get("impact_basic"), "fx_hit_01", {})
    result = insert_fx(doc, library.get("impact_basic"), settings, "arm", "attack", 0.2)
    window.set_document(doc)
    window.state.set_animation("attack")
    window.state.set_mode("animate")
    window.state.set_time(0.3)
    window.viewport.frame_all()
    QApplication.processEvents()
    doc.export_spine_json(out_dir / "export" / "rig.json", overwrite=True)
    doc.export_spine_json(out_dir / "export" / "rig.skel", overwrite=True, binary=True)
    window.grab().save(str(out_dir / "anim2d.png"))
    from .video import export_animation  # GIF through Pillow, MP4 through Qt Multimedia (bundled codecs)
    videos = {fmt: export_animation(doc, "attack", out_dir / "export" / f"attack.{fmt}", fmt, size=128,
                                    overwrite=True).stat().st_size for fmt in ("gif", "mp4")}
    from ..formats import atlas
    from .media_export import MediaExport, export_media
    for kind, encoding in (("avi", "mjpeg"), ("mov", "png"), ("psd", "png"), ("apng", "png")):
        s = MediaExport(kind=kind, encoding=encoding, output=str(out_dir / "export" / f"attack_{kind}"), fps=15,
                        size=50)
        videos[kind] = export_media(doc, s, "attack", None, overwrite=True)[0].stat().st_size
    packed = atlas.pack_folder(images, out_dir / "export" / "packed", "body", atlas.PackSettings(premultiply_alpha=True))
    unpacked = atlas.unpack(packed[0], out_dir / "export" / "unpacked")
    from ..formats import spine_project  # .spine import is bundled
    videos["spine_project"] = len(spine_project.schema42.SCHEMA)
    report.update(inserted=result.prefix, bones=len(doc.bones), valid=doc.validate()[0] == [], videos=videos,
                  packed=[p.name for p in packed], unpacked=len(unpacked), ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    doc.dirty = False
    return report


def _write_failure(out_dir: Path) -> None:
    import json
    import traceback
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps({"ok": False, "error": traceback.format_exc()}, indent=2),
                                         encoding="utf-8")


def open_path(window: EditorWindow, path: Path) -> None:
    """Open a project, a .spine project or a Spine JSON export in the window (or say why it can't)."""
    try:
        doc = (Document.load(path) if path.suffix.lower() in PROJECT_SUFFIXES else
               Document.import_spine_project(path)[0] if path.suffix.lower() == ".spine" else
               Document.import_spine_json(path)[0])
    except EditorError as exc:
        QMessageBox.warning(window, APP_NAME, str(exc))
        return
    if window._confirm_discard():
        window.set_document(doc)


class FileOpenFilter(QObject):
    """macOS hands a file opened from Finder (double-click, the Dock, Open With) to the running app as a
    FileOpen event, not as an argument."""

    def __init__(self, window: EditorWindow):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.Type.FileOpen and event.file():
            open_path(self.window, Path(event.file()))
            return True
        return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    from .. import logs, paths
    logs.setup("editor")
    logs.install_error_handler()
    paths.migrate()
    apply_theme(app)
    apply_app_icon(app)
    window = EditorWindow()
    app.installEventFilter(FileOpenFilter(window))
    window.show()
    if len(sys.argv) > 2 and sys.argv[1] == "--self-test":
        try:
            _self_test(window, Path(sys.argv[2]))
            return 0
        except Exception:  # report any failure of the build check
            _write_failure(Path(sys.argv[2]))
            return 1
    if len(sys.argv) > 1:
        open_path(window, Path(sys.argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
