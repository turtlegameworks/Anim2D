"""Spine-style File menu, Export dialog (data, image and video formats), Texture Packer settings,
Texture Unpacker and Settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor import data_export  # noqa: E402
from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.media_export import MediaExport, export_media, plan  # noqa: E402
from anim2d.formats import atlas  # noqa: E402
from anim2d.formats.atlas import PackSettings, pack, pack_all, read_atlas, unpack  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _noise(w, h, margin, seed):
    import random
    rng = random.Random(seed)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    left, top, right, bottom = margin
    for y in range(top, h - bottom):
        for x in range(left, w - right):
            img.putpixel((x, y), (rng.randrange(256), rng.randrange(256), rng.randrange(256),
                                  rng.choice((255, 255, 128, 40))))
    return img


IMAGES = {"parts/arm": (20, 70, (3, 5, 2, 9)), "head": (48, 40, (6, 2, 1, 4)), "fx/glow_1": (30, 30, (10, 10, 10, 10)),
          "fx/glow_2": (30, 30, (8, 8, 8, 8)), "wide": (80, 14, (0, 3, 0, 0)), "blank": (12, 12, (6, 6, 6, 6))}


def images():
    return {n: _noise(w, h, m, i) for i, (n, (w, h, m)) in enumerate(IMAGES.items())}


# --- texture packer ------------------------------------------------------------------------

def test_packer_options():
    imgs = images()
    imgs["copy/head"] = imgs["head"].copy()
    s = PackSettings(alias=True, ignore_blank=True, padding=3, padding_y=1, edge_padding=False, min_width=256,
                     min_height=16, divisible_by_4=True, use_indexes=True, flatten_paths=True,
                     wrap_x="Repeat", format="RGBA4444", filter_min="MipMapLinearLinear")
    r = pack(imgs, "a", s)
    names = [(x.name, x.index) for x in r.regions]
    assert ("blank", -1) not in names, "blank images ignored"
    assert ("glow", 1) in names and ("glow", 2) in names, "use indexes + flatten paths"
    heads = [x for x in r.regions if x.name == "head"]
    assert len(heads) == 2 and (heads[0].x, heads[0].y) == (heads[1].x, heads[1].y), "alias shares one region"
    page = r.pages[0]
    assert page.width >= 256 and page.width % 4 == 0 and page.height % 4 == 0
    assert min(x.x for x in r.regions) == 0 and min(x.y for x in r.regions) == 0, "no edge padding"
    text = r.atlas_text()
    assert "format: RGBA4444" in text and "repeat: x" in text and "filter: MipMapLinearLinear,Linear" in text
    assert "index: 2" in text
    pages, regions = read_atlas(text)
    assert pages[0][1]["repeat"] == "x" and len(regions) == len(r.regions)

    square = pack(images(), "s", PackSettings(square=True, power_of_two=True))
    assert all(p.width == p.height and p.width & (p.width - 1) == 0 for p in square.pages)
    grid = pack(images(), "g", PackSettings(packing="grid", rotation=False))
    assert len({(x.x % 81, x.y % 71) for x in grid.regions}) >= 1 and not any(x.rotated for x in grid.regions)
    jpg = pack(images(), "j", PackSettings(output_format="jpg"))
    assert jpg.page_names == ["j.jpg"]
    debug = pack({"a": Image.new("RGBA", (10, 10), (0, 0, 0, 0)).copy()}, "d",
                 PackSettings(strip_whitespace=False, debug=True, padding=0))
    assert debug.pages[0].getpixel((0, 0)) == (255, 0, 255, 255)


def test_duplicate_padding_and_bleed():
    red = Image.new("RGBA", (6, 6), (255, 0, 0, 255))
    r = pack({"r": red}, "p", PackSettings(padding=4, duplicate_padding=True, rotation=False))
    reg = r.regions[0]
    assert r.pages[0].getpixel((reg.x - 1, reg.y)) == (255, 0, 0, 255), "edge pixels repeated into the padding"
    assert r.pages[0].getpixel((0, 0))[3] == 0
    bled = pack({"r": red}, "b", PackSettings(padding=4, bleed=True, rotation=False))
    reg = bled.regions[0]
    px = bled.pages[0].getpixel((reg.x - 1, reg.y))
    assert px[3] == 0 and px[:3] == (255, 0, 0), "transparent neighbours take the colour"


def test_multiple_scales(tmp_path):
    s = PackSettings(scales=[(1.0, "", "bicubic"), (0.5, "@0.5x", "nearest")])
    results = pack_all(images(), "hero", s)
    assert [n for n, _ in results] == ["hero", "hero@0.5x"]
    assert results[1][1].page_names[0] == "hero@0.5x.png"
    head = {r.name: r for r in results[1][1].regions}["head"]
    assert head.original_width == 24
    folder = tmp_path / "in"
    for name, img in images().items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        img.save(folder / f"{name}.png")
    written = atlas.pack_folder(folder, tmp_path / "out", "hero", s)
    assert {f.name for f in written} >= {"hero.atlas", "hero@0.5x.atlas", "hero.png", "hero@0.5x.png"}


@pytest.mark.parametrize("pma", [False, True])
def test_unpack_restores_the_images(tmp_path, pma):
    imgs = images()
    r = pack(imgs, "u", PackSettings(max_width=128, max_height=128, premultiply_alpha=pma))
    assert any(x.rotated for x in r.regions) and len(r.pages) >= 1
    r.write(tmp_path / "u.atlas")
    files = unpack(tmp_path / "u.atlas", tmp_path / "out")
    assert len(files) == len(imgs)
    for name, img in imgs.items():
        got = Image.open(tmp_path / "out" / f"{name}.png").convert("RGBA")
        assert got.size == img.size
        for y in range(img.height):
            for x in range(img.width):
                want, have = img.getpixel((x, y)), got.getpixel((x, y))
                if want[3] == 0:
                    assert have[3] == 0
                elif pma:  # premultiplying loses precision at low alpha
                    assert have[3] == want[3]
                    assert all(abs(a - b) <= 255 / want[3] + 1 for a, b in zip(have[:3], want[:3])), (name, x, y)
                else:
                    assert have == want, (name, x, y)


def test_unpack_reads_spine38_atlas(tmp_path):
    Image.new("RGBA", (64, 32), (0, 200, 0, 255)).save(tmp_path / "old.png")
    (tmp_path / "old.atlas").write_text(
        "\nold.png\nsize: 64,32\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n"
        "eye\n  rotate: true\n  xy: 2, 2\n  size: 10, 20\n  orig: 12, 24\n  offset: 1, 2\n  index: 3\n",
        encoding="utf-8")
    files = unpack(tmp_path / "old.atlas", tmp_path / "out")
    assert [f.name for f in files] == ["eye_3.png"]
    img = Image.open(files[0])
    assert img.size == (12, 24) and img.getpixel((1, 24 - 2 - 1)) == (0, 200, 0, 255)
    with pytest.raises(atlas.PackError, match="not found"):
        unpack(tmp_path / "old.atlas", tmp_path / "out", tmp_path / "missing")


# --- data export options -----------------------------------------------------------------------

@pytest.fixture
def rig(tmp_path) -> Document:
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (60, 20), (240, 120, 40, 255)).save(folder / "bar.png")
    d = Document(images_dir=folder)
    d.add_bone("root", "arm", length=60)
    d.add_slot("arm", "arm", image="bar")
    d.set_attachment("arm", "bar", x=30)
    d.add_animation("wave")
    for t, v in ((0, 0), (0.5, 90), (1.0, 0)):
        d.set_bone_key("wave", "arm", "rotate", t, value=v)
    for t in (0, 0.25, 0.5, 0.75, 1.0):  # constant: the middle and last keys change nothing
        d.set_bone_key("wave", "arm", "scale", t, x=1.5, y=1)
    d.add_skin("big")
    d.set_edit_skin("big")
    d.skin.setdefault("arm", {})["bar"] = {"width": 80, "height": 30, "x": 40}
    d.set_edit_skin("default")
    return d


def test_data_options(rig, tmp_path):
    out = tmp_path / "a.json"
    notes = rig.export_spine_json(out, cleanup=True, nonessential=False, pretty=False, copy_images=False)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "fps" not in data["skeleton"] and "color" not in data["bones"][1]
    assert len(data["animations"]["wave"]["bones"]["arm"]["scale"]) == 1 and any("clean up" in n for n in notes)
    assert "\n" not in out.read_text(encoding="utf-8").strip(), "compact"
    from anim2d.preview.evaluator import PoseEvaluator
    full = rig.export_data()[0]
    for t in (0, 0.3, 0.5, 0.9, 1.0):
        a, b = PoseEvaluator(full, "wave").bone_world(t), PoseEvaluator(data, "wave").bone_world(t)
        assert all(x == pytest.approx(y, abs=1e-6) for n in a for x, y in zip(a[n], b[n])), "clean up keeps the pose"
    js = tmp_path / "a.js"
    rig.export_spine_json(js, javascript=True, copy_images=False)
    text = js.read_text(encoding="utf-8")
    assert "skeleton: {" in text and '"skeleton"' not in text
    skel = tmp_path / "b.skel"
    rig.export_spine_json(skel, binary=True, nonessential=False, copy_images=False)
    assert skel.stat().st_size > 0
    keys = [{"time": 0, "x": 1}, {"time": 1, "x": 1, "curve": [0.2, 1, 0.8, 3]}, {"time": 2, "x": 1}]
    assert data_export._clean_keys(keys) == 0, "a bezier with its own value handles may overshoot"


def test_import_data_merges(rig, tmp_path):
    other = Document(images_dir=rig.images_dir)
    other.add_bone("root", "arm", length=60)
    other.add_bone("arm", "hand", x=60, length=20)
    other.add_slot("hand", "hand", image="bar")
    other.add_animation("wave")
    other.set_bone_key("wave", "hand", "rotate", 0.5, value=30)
    other.add_animation("punch")
    other.set_bone_key("punch", "hand", "rotate", 0.2, value=10)
    src = tmp_path / "other.json"
    other.export_spine_json(src, copy_images=False)
    notes = rig.merge_spine_json(src)
    assert rig.has_bone("hand") and "punch" in rig.animation_names() and "wave 2" in rig.animation_names()
    assert any("wave 2" in n for n in notes)
    assert rig.validate()[0] == []
    rig.undo()
    assert not rig.has_bone("hand")


# --- image and video export --------------------------------------------------------------------

def test_plan(rig):
    s = MediaExport(fps=10)
    assert [len(j.times) for j in plan(rig, s, "wave", None)] == [10]
    s.include_last_frame, s.repeat = True, 2
    assert len(plan(rig, s, "wave", None)[0].times) == 22
    s.frame_range, s.repeat = (2, 4), 1
    assert plan(rig, s, "wave", None)[0].times == pytest.approx([0.2, 0.3, 0.4])
    s.frame_range, s.skins = None, "all_none"
    assert [j.skin for j in plan(rig, s, "wave", None)] == [None, "big"]
    s.export_type = "setup"
    assert plan(rig, s, None, None)[0].times == [0.0]
    with pytest.raises(EditorError, match="outside"):
        plan(rig, MediaExport(fps=10, frame_range=(50, 60)), "wave", None)


def test_gif_png_apng_psd_webp_jpeg(qapp, rig, tmp_path):
    def run(kind, name="out", **kw):
        s = MediaExport(kind=kind, output=str(tmp_path / kind / name), fps=10, **kw)
        return export_media(rig, s, "wave", None)
    gif = run("gif", pause_after=0.5, forever=False, colors=32)
    im = Image.open(gif[0])
    assert im.n_frames == 10 and "loop" not in im.info and "transparency" in im.info
    im.seek(9)
    assert im.info["duration"] == 600, "the last frame holds for the pause"
    frames = run("png", size_mode="width", size=200)
    assert len(frames) == 10 and Image.open(frames[0]).width == 200
    with pytest.raises(EditorError, match="already exists"):
        run("png", size_mode="width", size=200)
    apng = run("apng")
    assert Image.open(apng[0]).n_frames == 10
    psd = Image.open(run("psd", export_type="current")[0])
    assert len(psd.layers) == 1
    psd = Image.open(run("psd", "all")[0])
    assert len(psd.layers) == 10 and psd.layers[-1][0] == "wave 0000"
    webp = run("webp")
    assert Image.open(webp[0]).n_frames == 10
    jpeg = run("jpeg", transparent=False, background="#ff0000", jpeg_quality=95)
    corner = Image.open(jpeg[0]).getpixel((0, 0))
    assert corner[0] > 240 and corner[1] < 20
    packed = run("png", "packed", pack=True, pack_settings={"use_indexes": True})
    text = packed[0].read_text(encoding="utf-8")
    assert packed[0].suffix == ".atlas" and "wave\n" in text and "index: 9" in text
    big = run("png", "setup", msaa=4, bones=True, others=True, linear_filtering=False, export_type="setup")
    assert Image.open(big[0]).size[0] > 0


def test_skins_each_file_and_crop(qapp, rig, tmp_path):
    s = MediaExport(kind="gif", output=str(tmp_path / "s.gif"), skins="all_none", output_type="each", fps=5)
    files = export_media(rig, s, "wave", None)
    assert [f.name for f in files] == ["s_wave.gif", "s_wave_big.gif"]
    small, big = (Image.open(f).size for f in files)
    assert big[0] > small[0], "the big skin's attachment is larger"
    crop = MediaExport(kind="png", output=str(tmp_path / "c"), export_type="setup", crop=(-10, -10, 50, 20))
    assert Image.open(export_media(rig, crop, "wave", None)[0]).size == (50, 20)


def _decode(path: Path):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    player, sink, frames = QMediaPlayer(), QVideoSink(), []
    player.setVideoSink(sink)
    sink.videoFrameChanged.connect(lambda f: frames.append(f.toImage()) if f.isValid() else None)
    loop = QEventLoop()
    player.mediaStatusChanged.connect(lambda st: loop.quit() if st in (
        QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia) else None)
    player.setSource(QUrl.fromLocalFile(str(path)))
    player.play()
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    return frames, player.duration()


@pytest.mark.parametrize("kind,encoding", [("avi", "mjpeg"), ("avi", "raw"), ("mov", "png"), ("mov", "jpeg")])
def test_video_files_decode(qapp, rig, tmp_path, kind, encoding):
    s = MediaExport(kind=kind, encoding=encoding, output=str(tmp_path / f"v.{kind}"), fps=10, transparent=False,
                    background="#00ff00", export_type="animation")
    path = export_media(rig, s, "wave", None)[0]
    frames, duration = _decode(path)
    assert duration == pytest.approx(1000, abs=120)
    assert len(frames) >= 8, "decoded by Qt Multimedia's FFmpeg backend"
    first = frames[0].convertToFormat(frames[0].Format.Format_RGB32)
    c = first.pixelColor(0, first.height() - 1)
    assert c.green() > 200 and c.red() < 60, "the background colour"


# --- dialogs and the File menu -----------------------------------------------------------------

def _store(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


def test_export_dialog(qapp, rig, tmp_path):
    from anim2d.editor.export_dialog import ExportContext, ExportDialog
    store = _store(tmp_path)
    ctx = ExportContext(rig, "wave", None, 0.0, project_name="hero", start_dir=str(tmp_path))
    dlg = ExportDialog(ctx, store=store)
    assert dlg.kind == "json" and dlg.pages.currentIndex() == 0
    dlg.folder.setText(str(tmp_path / "data"))
    dlg.nonessential.setChecked(False)
    assert dlg.run_export() == [tmp_path / "data" / "hero.json"]
    dlg.select_kind("binary")
    assert dlg.extension.text() == ".skel" and not dlg.version.isEnabled()
    dlg.select_kind("gif")
    assert dlg.pages.currentIndex() == 1 and dlg.title.text() == "Export GIF"
    assert dlg.media_form.isRowVisible(dlg.rows["colors"]) and not dlg.media_form.isRowVisible(dlg.rows["pack"])
    dlg.output.setText(str(tmp_path / "g.gif"))
    dlg.colors.set(64)
    dlg.pause.set(0.25)
    s = dlg.media_settings()
    assert (s.kind, s.colors, s.pause_after, s.animations) == ("gif", 64, 0.25, "current")
    assert dlg.run_export()[0].is_file()
    dlg.select_kind("mov")
    assert dlg.encoding.count() == 2 and dlg.media_form.isRowVisible(dlg.rows["compression"])
    dlg.encoding.setCurrentIndex(1)  # JPEG: no alpha
    assert not dlg.transparent.isEnabled() and dlg.media_form.isRowVisible(dlg.rows["jpeg_quality"])
    dlg.select_kind("png")
    assert dlg.media_form.labelForField(dlg.rows["output"]).text() == "Output prefix:"
    dlg.select_kind("gif")
    assert dlg.colors.value() == 64, "each format remembers its settings"
    saved = tmp_path / "gif.json"
    dlg.save_settings_file(saved)
    dlg.select_kind("json")
    dlg.load_settings_file(saved)
    assert dlg.kind == "gif" and dlg.media_settings().colors == 64
    dlg.export_type.setCurrentIndex(2)  # setup pose: no animation rows
    assert not dlg.media_form.isRowVisible(dlg.rows["animations"])
    from anim2d.editor.media_export import preview_frames
    assert len(preview_frames(rig, dlg.media_settings(), "wave", None)) == 1
    dlg._defaults()
    assert dlg.media_settings().colors == 256 and dlg.media_settings().export_type == "animation"
    dlg._remember_current()
    again = ExportDialog(ctx, store=store)
    assert again.kind == "gif"


def test_packer_dialogs(qapp, tmp_path):
    from anim2d.editor.packer_dialogs import (TexturePackDialog, TexturePackerSettingsDialog,
                                                         TextureUnpackDialog, spine_defaults)
    dlg = TexturePackerSettingsDialog()
    s = dlg.settings()
    assert (s.alpha_threshold, s.min_width, s.premultiply_alpha, s.alias, s.resample) == (3, 16, True, True, "bicubic")
    dlg.padding_y.set(5)
    dlg.square.setChecked(True)
    dlg.scale_fields[1].setText("0.5")
    dlg.suffix_fields[1].setText("@half")
    s = dlg.settings()
    assert s.pad_y == 5 and s.square and s.scales == [(1.0, "", "bicubic"), (0.5, "@half", "bicubic")]
    other = TexturePackerSettingsDialog(s)
    assert other.settings() == s, "the dialog shows what it was given"
    other._apply(spine_defaults())
    assert other.settings().scales == [] and not other.settings().square
    folder = tmp_path / "in"
    folder.mkdir()
    for name, img in list(images().items())[:3]:
        img.save(folder / f"{name.replace('/', '_')}.png")
    store = _store(tmp_path)
    pack_dialog = TexturePackDialog(store, input_dir=str(folder))
    pack_dialog.output.setText(str(tmp_path / "out"))
    pack_dialog.name.setText("pack")
    pack_dialog.pack()
    assert (tmp_path / "out" / "pack.atlas").is_file() and pack_dialog.written
    unpack_dialog = TextureUnpackDialog(store)
    unpack_dialog.atlas.setText(str(tmp_path / "out" / "pack.atlas"))
    unpack_dialog.output.setText(str(tmp_path / "back"))
    unpack_dialog.unpack()
    assert len(unpack_dialog.written) == 3


def test_file_menu_recent_settings_autosave(qapp, rig, tmp_path):
    from anim2d.editor.main_window import EditorWindow
    from anim2d.editor.settings_dialog import SettingsDialog
    from anim2d.editor import document
    rig.dirty = False
    win = EditorWindow(rig, settings_store=_store(tmp_path))
    texts = [a.text().replace("&", "") for a in win.menuBar().actions()[0].menu().actions() if a.text()]
    for wanted in ("New Project", "Open Project…", "Recent Projects", "Save Project", "Save Project As…",
                   "New Skeleton", "Import Project…", "Import Data…", "Export…", "Texture Packer…",
                   "Texture Unpacker…", "Settings…"):
        assert any(t.endswith(wanted) for t in texts), wanted
    shortcuts = {a.text().replace("&", ""): a.shortcut().toString() for a in win.menuBar().actions()[0].menu().actions()}
    assert shortcuts["New Project"] == "Ctrl+Shift+N" and shortcuts["New Skeleton"] == "Ctrl+N"
    assert shortcuts["Export…"] == "Ctrl+E" and shortcuts["Texture Packer…"] == "Ctrl+P"
    assert shortcuts["Settings…"] == "F12"
    project = tmp_path / "p.rig2d"
    rig.save(project)
    win.add_recent(project)
    win._fill_recent()
    assert win.recent_projects() == [str(project.resolve())]
    assert win.recent_menu.actions()[0].text() == str(project.resolve())
    rig.set_bone("arm", rotation=5)
    assert win.autosave() == project.with_name("p.rig2d.autosave") and rig.dirty and rig.path == project
    assert Document.load(project.with_name("p.rig2d.autosave")).bone("arm")["rotation"] == 5
    dialog = SettingsDialog(win.settings)
    dialog.undo.set(50)
    dialog.fps.set(24)
    dialog.autosave.set(2)
    win.settings = dialog.settings()
    win._apply_settings()
    assert document.UNDO_LIMIT == 50 and win.autosave_timer.isActive()
    images_dir = rig.images_dir
    rig.dirty = False
    win.new_skeleton()
    assert win.doc.images_dir == images_dir and win.doc.fps == 24 and not win.doc.bones[1:]
    ctx = win.export_context()
    assert ctx.crop().width() > 0
    win.settings.undo_levels, win.settings.autosave_minutes = 300, 0
    win._apply_settings()
    win.doc.dirty = False
    win.close()
