"""Command search, motion paths, live layer sync (.psd/.ora) and the Retime window."""

from __future__ import annotations

import os
import tempfile
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.editor.main_window import EditorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(doc: Document) -> EditorWindow:
    w = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    return w


# --- command search -----------------------------------------------------------------------------

def test_command_search_finds_and_runs(qapp):
    from anim2d.editor.command_search import CommandSearch, collect_commands, score
    assert score("exp unity", "Export to Unity", "File") is not None
    assert score("zzz", "Export to Unity", "File") is None
    assert score("retime", "Retime", "Edit") > score("retime", "Retime the clip", "Edit")
    w = _window(Document())
    names = [n for n, _, _ in collect_commands(w.menuBar())]
    assert {"Retime", "Motion paths", "Appearance", "Search commands"} <= set(names)
    dialog = CommandSearch(w.menuBar(), w)
    dialog.query.setText("motion paths")
    assert dialog.results.currentItem().text().startswith("Motion paths")
    QTest.keyClick(dialog.query, Qt.Key.Key_Return)
    assert dialog.ran == "Motion paths" and w.state.motion_paths, "Enter ran the command (a toggle)"
    w.doc.dirty = False
    w.close()


# --- motion paths --------------------------------------------------------------------------------

def _hop_doc() -> Document:
    d = Document()
    d.add_bone("root", "body", rotation=90, length=40)  # rotated parent: drags must turn into local keys
    d.add_bone("body", "hand", x=40, length=20)
    d.add_animation("hop")
    d.set_bone_key("hop", "hand", "translate", 0, x=0, y=0)
    d.set_bone_key("hop", "hand", "translate", 1.0, x=0, y=-100)
    return d


def test_motion_path_points_and_move():
    from anim2d.editor.motion_path import move_to, path_points, translate_key_at
    d = _hop_doc()
    pts = path_points(d, "hop", "hand", fps=10)
    assert len(pts) == 11 and pts[0].key and pts[-1].key and not pts[5].key
    assert (pts[-1].x, pts[-1].y) == pytest.approx((100, 40), abs=1e-6), "local -y under a 90° parent is world +x"
    base = translate_key_at(d, "hop", "hand", 0.5)
    move_to(d, "hop", "hand", 0.5, base, 0, 30, record=True)  # 30 px up in the world
    after = d.evaluator("hop").bone_world(0.5)["hand"]
    assert (after[4], after[5]) == pytest.approx((pts[5].x, pts[5].y + 30), abs=1e-3)
    assert [k.get("time", 0) for k in d.animation("hop")["bones"]["hand"]["translate"]] == [0, 0.5, 1.0]


def test_motion_path_drag_in_viewport(qapp):
    w = _window(_hop_doc())
    st, vp = w.state, w.viewport
    st.set_mode("animate")
    st.animation = "hop"
    st.select("bone", "hand")
    w.motion_paths_action.trigger()
    QApplication.processEvents()
    vp.grab()  # draws the path
    from anim2d.editor.motion_path import path_points
    end = path_points(w.doc, "hop", "hand")[-1]
    s = vp.to_screen(end.x, end.y)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=QPoint(round(s.x()), round(s.y())))
    QTest.mouseMove(vp, QPoint(round(s.x()), round(s.y()) - 40))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=QPoint(round(s.x()), round(s.y()) - 40))
    moved = w.doc.evaluator("hop").bone_world(1.0)["hand"]
    assert moved[5] == pytest.approx(end.y + 40 / vp.zoom, abs=0.5), "the key followed the mouse up"
    assert st.time == pytest.approx(1.0), "grabbing a dot goes to its frame"
    w.doc.undo()
    assert w.doc.evaluator("hop").bone_world(1.0)["hand"][5] == pytest.approx(end.y, abs=1e-3)
    w.doc.dirty = False
    w.close()


# --- live layer sync -----------------------------------------------------------------------------

def _psd(path, layers, size=(80, 60), names=("hat", "face")):
    from anim2d.editor.media_export import MediaExport, write_psd
    s = MediaExport()
    s.transparent = True
    frames = []
    for color, box in layers:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        img.paste(color, box)
        frames.append(img)
    write_psd(frames, list(names[:len(layers)]), path, s)  # the first frame is the top layer


def test_resync_keeps_rigging_and_moves(tmp_path):
    from anim2d.editor.psd_import import import_psd, layer_sources, resync_layers
    images = tmp_path / "images"
    images.mkdir()
    src = tmp_path / "hero.psd"
    _psd(src, [((255, 0, 0, 255), (0, 0, 10, 10)), ((0, 0, 255, 255), (20, 20, 40, 40))])
    d = Document(images_dir=images)
    d.add_bone("root", "head")
    import_psd(d, src, "head")
    assert layer_sources(d) == [src.resolve()]
    d.set_attachment("hat", "hat", x=d.attachments("hat")["hat"]["x"] + 5)  # moved by hand in Anim2D
    before = dict(d.attachments("hat")["hat"])
    _psd(src, [((0, 255, 0, 255), (0, 0, 10, 10)), ((0, 0, 255, 255), (20, 20, 40, 40))])
    r = resync_layers(d, src)
    assert sorted(r["updated"]) == ["face", "hat"] and not r["added"]
    assert Image.open(images / "hero" / "hat.png").getpixel((5, 5)) == (0, 255, 0, 255), "new pixels"
    assert d.attachments("hat")["hat"]["x"] == before["x"], "the hand-made move is kept"
    assert d.slot("hat")["bone"] == "head"
    for _ in range(3):  # the sync, the hand-made move, the import
        d.undo()
    with pytest.raises(EditorError, match="import it again"):
        resync_layers(d, src)
    assert layer_sources(d) == []


def test_openraster_import(tmp_path):
    from anim2d.editor.psd_import import import_psd, read_layers
    import io
    ora = tmp_path / "sketch.ora"
    with zipfile.ZipFile(ora, "w") as z:
        z.writestr("mimetype", "image/openraster")
        z.writestr("stack.xml", '<image w="100" h="80"><stack>'
                                '<layer name="eye" src="data/eye.png" x="60" y="10"/>'
                                '<layer name="body" src="data/body.png" x="0" y="0"/></stack></image>')
        for name, size, color in (("eye", (10, 10), (0, 0, 0, 255)), ("body", (100, 80), (200, 150, 100, 255))):
            buf = io.BytesIO()
            Image.new("RGBA", size, color).save(buf, "PNG")
            z.writestr(f"data/{name}.png", buf.getvalue())
    canvas, layers = read_layers(ora)
    assert canvas == (100, 80) and [n for n, _, _ in layers] == ["body", "eye"], "bottom first"
    images = tmp_path / "images"
    images.mkdir()
    d = Document(images_dir=images)
    result = import_psd(d, ora)
    assert result["slots"] == ["body", "eye"]
    eye = d.attachments("eye")["eye"]
    assert (eye["x"], eye["y"]) == pytest.approx((65 - 50, 40 - 15)), "placed where it sits on the canvas"


def test_window_follows_the_file(qapp, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    src = tmp_path / "prop.psd"
    _psd(src, [((255, 0, 0, 255), (0, 0, 10, 10))])
    w = _window(Document(images_dir=images))
    from anim2d.editor.psd_import import import_psd
    import_psd(w.doc, src)
    w._watch_layer_files()
    assert str(src.resolve()) in w.layer_watcher.files()
    time.sleep(0.05)
    _psd(src, [((0, 255, 0, 255), (0, 0, 10, 10))])
    w._layer_file_changed(str(src.resolve()))  # what the watcher calls when the file is saved
    w._sync_changed_layers()
    assert "Layers synced" in w.status_label.text()
    assert Image.open(images / "prop" / "hat.png").getpixel((5, 5)) == (0, 255, 0, 255)
    w.doc.dirty = False
    w.close()


# --- retime window --------------------------------------------------------------------------------

def test_retime_dialog(qapp):
    from anim2d.editor.retime_dialog import RetimeDialog
    d = _hop_doc()
    dialog = RetimeDialog(d, "hop", [], 0.0)
    assert not dialog.scope_keys.isEnabled() and dialog.scope_all.isChecked()
    dialog.length.setValue(3.0)
    assert dialog.factor.value() == pytest.approx(3.0)
    assert "Scaled" in dialog.apply()
    assert d.animation_duration("hop") == pytest.approx(3.0)
    dialog.action_loop.setChecked(True)
    dialog.loop_start.setValue(0.0)
    dialog.loop_end.setValue(3.0)
    dialog.loop_times.setValue(1)
    assert "Looped" in dialog.apply() and d.animation_duration("hop") == pytest.approx(6.0)
    dialog.action_reverse.setChecked(True)
    assert "Reversed" in dialog.apply()
