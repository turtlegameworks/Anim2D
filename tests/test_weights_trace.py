"""Weight painting and mesh tracing."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from anim2d.editor import affine, mesh_ops, mesh_trace  # noqa: E402
from anim2d.editor.document import Document, EditorError  # noqa: E402

VENDOR = Path(__file__).parent / "runtime" / "vendor" / "v40"
CORE40 = VENDOR / "spine-core-4.0.js"
NODE = shutil.which("node")


@pytest.fixture
def images(tmp_path) -> Path:
    folder = tmp_path / "images"
    folder.mkdir()
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((10, 10, 190, 90), fill=(200, 80, 40, 255))
    img.save(folder / "blob.png")
    Image.new("RGBA", (100, 40), (90, 90, 250, 255)).save(folder / "bar.png")
    return folder


@pytest.fixture
def arm(images) -> Document:
    d = Document(images_dir=images)
    d.add_bone("root", "upper", length=50)
    d.add_bone("upper", "lower", x=50, length=50)
    d.add_slot("upper", "arm", image="bar")
    d.set_attachment("arm", "bar", x=50)
    mesh_ops.convert_to_mesh(d, "arm", "bar", 6, 2)
    d.add_animation("bend")
    d.set_bone_key("bend", "lower", "rotate", 0, value=0)
    d.set_bone_key("bend", "lower", "rotate", 1, value=60)
    return d


def test_painting_weights(arm):
    before = mesh_ops.vertex_world(arm, "arm", "bar")
    tip = (100.0, 0.0)  # the far end of the bar, where "lower" should take over
    changed = mesh_ops.paint_weights(arm, "arm", "bar", "lower", tip, radius=30, strength=1.0, mode="replace")
    assert changed > 0
    weights = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    for (x, y), w in zip(before, weights):
        d = math.hypot(x - tip[0], y - tip[1])
        if d < 3:
            assert w == pytest.approx(1.0, abs=0.02)
        elif d > 30:
            assert w == 0
    after = mesh_ops.vertex_world(arm, "arm", "bar")
    assert [c for p in after for c in p] == pytest.approx([c for p in before for c in p], abs=1e-3),         "painting moves nothing"
    for v in range(len(before)):
        assert sum(mesh_ops.vertex_weights(arm, "arm", "bar", v).values()) == pytest.approx(1.0, abs=1e-3)
    # add/subtract move the weight by strength; smooth evens out neighbours
    mid = (60.0, 0.0)
    w0 = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    mesh_ops.paint_weights(arm, "arm", "bar", "lower", mid, radius=25, strength=0.5, mode="add")
    w1 = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert all(b >= a - 1e-6 for a, b in zip(w0, w1)) and sum(w1) > sum(w0)
    mesh_ops.paint_weights(arm, "arm", "bar", "lower", mid, radius=25, strength=0.5, mode="subtract")
    w2 = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert sum(w2) < sum(w1)
    spread = max(w2) - min(w2)
    mesh_ops.paint_weights(arm, "arm", "bar", "lower", (50, 0), radius=200, strength=1.0, mode="smooth")
    w3 = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert max(w3) - min(w3) < spread
    with pytest.raises(EditorError, match="Paint mode"):
        mesh_ops.paint_weights(arm, "arm", "bar", "lower", mid, 10, mode="blur")
    assert arm.validate()[0] == []


def test_painting_cannot_strip_a_vertex_of_its_only_bone(arm):
    mesh_ops.paint_weights(arm, "arm", "bar", "upper", (0, 0), radius=500, strength=1.0, mode="subtract")
    assert all(w == pytest.approx(1.0) for w in mesh_ops.bone_weights(arm, "arm", "bar", "upper"))


@pytest.mark.skipif(not NODE or not CORE40.is_file(), reason="needs node and tools/fetch_spine_runtime.py")
def test_painted_mesh_bends_like_the_runtime(tmp_path, arm):
    mesh_ops.paint_weights(arm, "arm", "bar", "lower", (100, 0), radius=60, strength=1.0, mode="replace")
    out = tmp_path / "arm.json"
    arm.export_spine_json(out, copy_images=False)
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(out), "--anim=bend",
            "0.5", "1.0"]
    samples = json.loads(subprocess.run(args, capture_output=True, text=True, timeout=60, check=True).stdout)
    ev = arm.evaluator("bend")
    for sample in samples["samples"]:
        mesh = next(v for v in sample["visible"] if v["slot"] == "arm")["mesh"]
        mine = ev.mesh_world_vertices("arm", "bar", sample["time"])
        assert [c for xy in mine for c in xy] == pytest.approx(mesh, abs=0.05)
    straight = mesh_ops.vertex_world(arm, "arm", "bar")
    bent = ev.mesh_world_vertices("arm", "bar", 1.0)
    assert max(abs(a[1] - b[1]) for a, b in zip(straight, bent)) > 20, "the painted end follows the lower bone"


def test_trace_region_keeps_the_image_in_place(images):
    d = Document(images_dir=images)
    d.add_bone("root", "b", x=30, rotation=20)
    d.add_slot("b", "s", image="blob")
    d.set_attachment("s", "blob", x=5, y=-3, rotation=10, scaleX=1.5)
    region = dict(d.attachments("s")["blob"])
    count = mesh_trace.trace_mesh(d, "s", "blob", detail=2, interior=30)
    mesh = d.attachments("s")["blob"]
    assert mesh["type"] == "mesh" and count == len(mesh["uvs"]) // 2 and mesh["hull"] >= 8
    place = affine.local(region["x"], region["y"], region["rotation"], region["scaleX"], 1)
    w, h = region["width"], region["height"]
    for i in range(count):  # every vertex sits where the region drew that image point
        u, v = mesh["uvs"][2 * i], mesh["uvs"][2 * i + 1]
        expected = affine.apply(place, u * w - w / 2, h / 2 - v * h)
        assert (mesh["vertices"][2 * i], mesh["vertices"][2 * i + 1]) == pytest.approx(expected, abs=0.01)
    # the outline hugs the ellipse: its UV bounding box matches the opaque area (10..190, 10..90 of 200x100)
    hull = [(mesh["uvs"][2 * i] * 200, mesh["uvs"][2 * i + 1] * 100) for i in range(mesh["hull"])]
    xs, ys = [p[0] for p in hull], [p[1] for p in hull]
    assert min(xs) == pytest.approx(10, abs=4) and max(xs) == pytest.approx(190, abs=4)
    assert min(ys) == pytest.approx(10, abs=4) and max(ys) == pytest.approx(90, abs=4)
    with Image.open(images / "blob.png") as img:  # every opaque pixel is inside the outline (+ detail)
        alpha = img.getchannel("A")
        for y in range(0, 100, 7):
            for x in range(0, 200, 7):
                if alpha.getpixel((x, y)) > 8:
                    inside = mesh_trace._inside((x + 0.5, y + 0.5), hull)
                    near = min(mesh_trace._segment_distance((x + 0.5, y + 0.5), hull[i], hull[(i + 1) % len(hull)])
                               for i in range(len(hull)))
                    assert inside or near <= 2.5, (x, y)
    assert d.validate()[0] == []
    d.undo()
    assert d.attachments("s")["blob"].get("type", "region") == "region"


def test_trace_existing_mesh_keeps_its_mapping(arm):
    before = mesh_ops.uv_mapper(arm, "arm", "bar")
    with pytest.raises(EditorError, match="unbind"):
        mesh_ops.bind(arm, "arm", "bar", ["upper", "lower"])
        mesh_trace.trace_mesh(arm, "arm", "bar")
    mesh_ops.unbind(arm, "arm", "bar")
    mesh_trace.trace_mesh(arm, "arm", "bar", detail=1)
    mesh = arm.attachments("arm")["bar"]
    world = mesh_ops.vertex_world(arm, "arm", "bar")
    for i, (x, y) in enumerate(world):  # UVs still say which image point is drawn at each vertex
        assert before(x, y) == pytest.approx((mesh["uvs"][2 * i], mesh["uvs"][2 * i + 1]), abs=0.01)


def test_trace_needs_opaque_pixels(tmp_path):
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(folder / "empty.png")
    d = Document(images_dir=folder)
    d.add_slot("root", "s", image="empty")
    with pytest.raises(EditorError, match="no opaque"):
        mesh_trace.trace_mesh(d, "s", "empty")


def test_weight_brush_in_viewport(arm):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from anim2d.editor.main_window import EditorWindow
    app = QApplication.instance() or QApplication([])
    arm.dirty = False
    win = EditorWindow(arm, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    win.resize(1200, 800)
    win.show()
    st, vp = win.state, win.viewport
    st.select("attachment", "bar", "arm")
    st.set_tool("weights")
    st.weight_bone, st.paint_mode, st.brush_strength = "lower", "replace", 1.0
    vp.frame_all()
    app.processEvents()
    st.brush_size = 25 * vp.zoom  # 25 world units
    depth = len(arm._undo)
    a, b = vp.to_screen(80, 0), vp.to_screen(100, 0)
    for kind, pos, buttons in ((QMouseEvent.Type.MouseButtonPress, a, Qt.MouseButton.LeftButton),
                               (QMouseEvent.Type.MouseMove, (a + b) / 2, Qt.MouseButton.LeftButton),
                               (QMouseEvent.Type.MouseMove, b, Qt.MouseButton.LeftButton),
                               (QMouseEvent.Type.MouseButtonRelease, b, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(vp, QMouseEvent(kind, pos, vp.mapToGlobal(pos), Qt.MouseButton.LeftButton, buttons,
                                               Qt.KeyboardModifier.NoModifier))
    weights = mesh_ops.bone_weights(arm, "arm", "bar", "lower")
    assert max(weights) == pytest.approx(1.0, abs=0.02)
    assert len(arm._undo) == depth + 1, "one undo step per stroke"
    vp.repaint()  # the heat map draws
    arm.dirty = False
    win.close()


# --- animation export (GIF / WebP / APNG / MP4) ------------------------------------------------

def test_animation_export_formats(tmp_path, arm):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from anim2d.editor import video
    QApplication.instance() or QApplication([])
    # make the bar bend, so every frame differs (Pillow merges identical frames)
    mesh_ops.paint_weights(arm, "arm", "bar", "lower", (100, 0), radius=60, strength=1.0, mode="replace")
    for fmt in ("gif", "webp", "apng"):
        out = video.export_animation(arm, "bend", tmp_path / f"bend.{fmt}", fmt, size=96, fps=20)
        with Image.open(out) as im:
            assert im.n_frames == 20, fmt  # 1 s at 20 fps (the frame at 1 s repeats the first when looping)
            assert im.info.get("loop") == 0
            im.seek(10)
            assert im.convert("RGBA").getchannel("A").getextrema()[0] == 0, "transparent background"
    twice = video.export_animation(arm, "bend", tmp_path / "twice.gif", "gif", size=64, fps=10, repeat=2)
    with Image.open(twice) as im:
        assert im.n_frames == 20
    with pytest.raises(EditorError, match="already exists"):
        video.export_animation(arm, "bend", tmp_path / "bend.gif", "gif")
    mp4 = video.export_animation(arm, "bend", tmp_path / "bend.mp4", "mp4", size=96, fps=25,
                                 background=QColor("#336699"))
    data = mp4.read_bytes()
    assert data[4:8] == b"ftyp" and b"avc1" in data[:5000], "an H.264 MP4"
    # Qt can play it back: about one second long
    from PySide6.QtMultimedia import QMediaPlayer
    player = QMediaPlayer()
    loop = QEventLoop()
    player.durationChanged.connect(lambda d: d > 0 and loop.quit())
    QTimer.singleShot(10000, loop.quit)
    player.setSource(QUrl.fromLocalFile(str(mp4)))
    loop.exec()
    assert player.duration() == pytest.approx(1000, abs=120)
