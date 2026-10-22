"""Reference footage behind the viewport (rotoscoping): image sequences and videos, synced to the timeline."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402
from anim2d.editor.reference import ReferenceMedia, ReferenceSettings, sequence_files  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _frames(n: int, size=(160, 120)) -> list[Image.Image]:
    out = []
    for i in range(n):
        im = Image.new("RGBA", size, (30, 30, 30, 255))
        ImageDraw.Draw(im).rectangle((i * 4, 40, i * 4 + 20, 60), fill=(255, 0, 0, 255))
        out.append(im)
    return out


def _wait(media: ReferenceMedia, seconds: float = 15) -> None:
    loop = QEventLoop()
    media.loaded.connect(loop.quit)
    media.failed.connect(lambda _m: loop.quit())
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    if media.loading:
        loop.exec()


def test_image_sequence(qapp, tmp_path):
    for i, im in enumerate(_frames(5)):
        im.save(tmp_path / f"walk_{i + 1:03d}.png")
    (tmp_path / "other.png").write_bytes((tmp_path / "walk_001.png").read_bytes())
    files = sequence_files(tmp_path / "walk_003.png")
    assert [f.name for f in files] == [f"walk_{i:03d}.png" for i in range(1, 6)], "only that numbered sequence"
    media = ReferenceMedia()
    media.load(tmp_path / "walk_001.png", fps=10)
    assert len(media.frames) == 5 and media.duration == pytest.approx(0.5)
    assert media.frame_index(0.0) == 0 and media.frame_index(0.25) == 2 and media.frame_index(9) == 4


def test_video_decodes(qapp, tmp_path):
    from anim2d.editor.media_export import MediaExport, write_avi
    clip = tmp_path / "clip.avi"
    write_avi(_frames(20), clip, MediaExport(kind="avi", fps=20, encoding="mjpeg"))
    media = ReferenceMedia()
    media.load(clip)
    _wait(media)
    assert len(media.frames) >= 18 and media.size.width() == 160
    assert media.duration == pytest.approx(1.0, abs=0.1)
    assert media.frame_index(0.5) == pytest.approx(10, abs=1)
    media.clear()
    media.deleteLater()
    QApplication.processEvents()


def test_window_draws_and_follows_time(qapp, tmp_path):
    for i, im in enumerate(_frames(4)):
        im.save(tmp_path / f"ref_{i}.png")
    doc = Document()
    doc.add_animation("run")
    doc.set_bone_key("run", "root", "rotate", 1.0, value=10)
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(900, 700)
    w.show()
    QApplication.processEvents()
    w.reference_footage()
    dlg = w.reference_dialog
    dlg.open(tmp_path / "ref_0.png")
    s = ReferenceSettings.of(doc)
    assert s.path.endswith("ref_0.png") and s.scale > 0, "fitted to the view"
    rect = w.viewport.reference_rect()
    assert rect is not None and rect.width() > 100
    img = w.viewport.grab().toImage()
    c = rect.center()
    assert img.pixelColor(int(c.x()), int(c.y())).lightness() > 10, "the footage is drawn"
    dlg.opacity.setValue(80)
    dlg.speed.setValue(1.0)
    assert ReferenceSettings.of(doc).opacity == pytest.approx(0.8)
    w.state.set_mode("animate")
    w.state.animation = "run"
    dlg.match_length()  # 4 frames at 30 fps over a 1 s animation
    assert ReferenceSettings.of(doc).speed == pytest.approx((4 / doc.fps) / 1.0, abs=1e-3)
    w.state.set_time(0.99)
    assert w.reference_media.frame_index(s.start + 0.99 * ReferenceSettings.of(doc).speed) == 3
    dlg.show_box.setChecked(False)
    assert w.viewport.reference_rect() is None
    dlg.remove()
    assert "reference" not in doc.project_settings and not w.reference_media.frames
    doc.dirty = False
    dlg.close()
    w.close()
