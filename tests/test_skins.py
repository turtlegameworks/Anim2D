"""Skins: creating and editing them, skin bones and constraints, and playback in the runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.preview.evaluator import PoseEvaluator  # noqa: E402

VENDOR = Path(__file__).parent / "runtime" / "vendor" / "v40"
CORE40 = VENDOR / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


@pytest.fixture
def doc() -> Document:
    d = Document()
    d.add_bone("root", "body", y=50, length=60)
    d.add_bone("body", "hat", x=60, length=20)
    d.add_slot("body", "head")
    d.skin.setdefault("head", {})["face"] = {"width": 40, "height": 40}
    d.set_slot("head", attachment="face")
    d.add_animation("idle")
    d.set_bone_key("idle", "body", "rotate", 0, value=0)
    d.set_bone_key("idle", "body", "rotate", 1, value=30)
    return d


def test_skin_lifecycle_and_attachments(doc):
    doc.add_skin("knight")
    doc.set_edit_skin("knight")
    assert doc.attachments("head") == {}, "a new skin starts empty"
    doc.skin.setdefault("head", {})["face"] = {"width": 80, "height": 30}  # same name, other image
    doc.duplicate_skin("knight", "knight2")
    doc.rename_skin("knight2", "paladin")
    doc.add_bounding_box("head", [(0, 0), (10, 0), (0, 10)])
    assert set(doc.skin_data("knight")["attachments"]["head"]) == {"face", "bounds"}
    assert set(doc.data["skins"][0]["attachments"]["head"]) == {"face"}
    shown = {i.slot: i for i in PoseEvaluator(doc.data, "", "knight").draw_items(0)}
    assert shown["head"].width == 80, "the skin's attachment replaces the default one"
    assert doc.skin_names() == ["default", "knight", "paladin"]
    doc.move_attachment_to_skin("head", "bounds", "paladin")
    assert "bounds" not in doc.attachments("head") and "bounds" in doc.skin_data("paladin")["attachments"]["head"]
    with pytest.raises(EditorError, match="already has"):
        doc.move_attachment_to_skin("head", "face", "paladin")
    doc.delete_skin("knight")
    assert doc.edit_skin == "default" and doc.skin_names() == ["default", "paladin"]
    with pytest.raises(EditorError):
        doc.delete_skin("default")
    doc.undo()
    assert "knight" in doc.skin_names()
    assert doc.validate()[0] == []


def test_skin_bones_and_constraints(doc):
    doc.add_skin("wizard")
    doc.add_transform(["hat"], "body", mixRotate=0.5)
    doc.set_skin_members("wizard", bones=["hat"], transform=["hat-transform"])
    assert doc.bone("hat")["skin"] is True and doc.transform_constraint("hat-transform")["skin"] is True
    assert "hat" not in PoseEvaluator(doc.data, "").active_bones
    assert "hat" in PoseEvaluator(doc.data, "", "wizard").active_bones
    with pytest.raises(EditorError, match="default skin"):
        doc.set_skin_members("default", bones=["hat"])
    doc.rename_bone("hat", "cap")
    assert doc.skin_data("wizard")["bones"] == ["cap"]
    doc.rename_constraint("hat-transform", "cap-follow")
    assert doc.skin_data("wizard")["transform"] == ["cap-follow"]
    doc.delete_constraint("cap-follow")
    assert "transform" not in doc.skin_data("wizard")
    doc.delete_bone("cap")
    assert "bones" not in doc.skin_data("wizard")
    assert doc.validate()[0] == []


def test_deform_keys_follow_the_edit_skin(doc):
    from anim2d.editor import mesh_ops
    doc.add_skin("knight")
    doc.set_edit_skin("knight")
    doc.skin.setdefault("head", {})["face"] = {"width": 40, "height": 40}
    mesh_ops.convert_to_mesh(doc, "head", "face", 1, 1)
    mesh_ops.set_deform_key(doc, "idle", "head", "face", 0.5, {0: (5.0, 5.0)})
    assert "knight" in doc.data["animations"]["idle"]["deform"]
    rows = [r.owner for r, _ in doc.timeline_rows("idle") if r.group == "deform"]
    assert rows == ["head\nface"]
    doc.set_edit_skin("default")
    assert not [r for r, _ in doc.timeline_rows("idle") if r.group == "deform"]
    doc.rename_skin("knight", "squire")
    assert "squire" in doc.data["animations"]["idle"]["deform"]
    assert doc.validate()[0] == []


@needs_runtime
def test_skins_play_like_the_runtime(tmp_path, doc):
    doc.add_skin("wizard")
    doc.set_edit_skin("wizard")
    doc.skin.setdefault("head", {})["face"] = {"width": 70, "height": 50, "x": 10}
    doc.set_edit_skin("default")
    doc.add_transform(["hat"], "body", rotation=45, mixX=0)
    doc.set_skin_members("wizard", bones=["hat"], transform=["hat-transform"])
    out = tmp_path / "skins.skel"
    doc.export_spine_json(out, binary=True, copy_images=False)
    for skin in (None, "wizard"):
        args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(out), "--full",
                "--anim=idle"] + ([f"--skin={skin}"] if skin else []) + ["0.3", "0.8"]
        samples = json.loads(subprocess.run(args, capture_output=True, text=True, timeout=60, check=True).stdout)
        ev = PoseEvaluator(doc.data, "idle", skin)
        for sample in samples["samples"]:
            world = ev.bone_world(sample["time"])
            for bone, values in sample["bones"].items():
                if bone in ev.active_bones:
                    assert (world[bone][4], world[bone][5]) == pytest.approx(values[:2], abs=0.02), (skin, bone)
            assert ("hat" in ev.active_bones) == (skin == "wizard")
            items = {i.slot: i for i in ev.draw_items(sample["time"])}
            assert items["head"].width == (70 if skin else 40)


def test_tree_switches_edit_skin(doc):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from anim2d.editor.main_window import EditorWindow
    app = QApplication.instance() or QApplication([])
    doc.add_skin("knight")
    doc.dirty = False
    win = EditorWindow(doc, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    tree = win.tree.tree
    skins = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                 if tree.topLevelItem(i).text(0) == "Skins")
    knight = next(skins.child(i) for i in range(skins.childCount()) if "knight" in skins.child(i).text(0))
    win.tree._double_clicked(knight, 0)
    assert doc.edit_skin == "knight"
    skins = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                 if tree.topLevelItem(i).text(0) == "Skins")
    assert any("knight  ✎ editing" in skins.child(i).text(0) for i in range(skins.childCount()))
    win.tree.add_shape("head", "point")
    assert "point" in doc.skin_data("knight")["attachments"]["head"]
    win.state.select("skin", "knight")
    app.processEvents()
    assert win.properties.title.text() == "Skin  knight"
    doc.dirty = False
    win.close()


def test_clipping_clips_slots_until_its_end_slot(tmp_path):
    """Draw items after a clipping attachment carry its polygon up to and including the end slot,
    and the renderer only paints inside it."""
    from PIL import Image
    from PySide6.QtGui import QColor, QImage, QPainter, QTransform
    from PySide6.QtWidgets import QApplication
    from anim2d.preview.qt_renderer import ImageCache, draw_frame
    QApplication.instance() or QApplication([])
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(images / "red.png")
    d = Document(images_dir=images)
    d.add_slot("root", "clipper")
    d.add_clipping("clipper", [(-10, -10), (10, -10), (10, 10), (-10, 10)])
    d.add_slot("root", "a", image="red")
    d.add_slot("root", "b", image="red")
    d.add_slot("root", "c", image="red")
    d.set_shape("clipper", "clip", end="b")
    items = {i.slot: i for i in d.evaluator("").draw_items(0)}
    assert items["a"].clip is not None and items["b"].clip is not None and items["c"].clip is None
    cache = ImageCache()
    cache.set_images({"red": images / "red.png"})
    frame = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    frame.fill(QColor(0, 0, 0, 0))
    painter = QPainter(frame)
    view = QTransform()
    view.translate(100, 100)
    view.scale(1, -1)
    draw_frame(painter, [items["a"]], cache, view)
    painter.end()
    assert frame.pixelColor(100, 100).alpha() == 255, "inside the clipping polygon"
    assert frame.pixelColor(100, 130).alpha() == 0, "outside it (the image reaches y=150)"
    assert d.validate()[0] == []


def test_animations_follow_their_skin(qapp=None):
    import os, tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from anim2d.editor.main_window import EditorWindow
    QApplication.instance() or QApplication([])
    d = Document()
    d.add_slot("root", "s")
    for skin in ("L1", "L2"):
        d.add_skin(skin)
    d.data["skins"].append({"name": "Folder/L10", "attachments": {}})  # skins in folders come from imports
    for name in ("L1_intro", "L1_idle", "L2_intro", "L2 idle", "L10-intro", "shared"):
        d.add_animation(name)
    assert d.animation_skin("L1_intro") == "L1" and d.animation_skin("L2 idle") == "L2"
    assert d.animation_skin("L10-intro") == "Folder/L10", "longest match; folders match by their last part"
    assert d.animation_skin("shared") is None and d.animation_skin("L1x") is None
    assert d.skin_animations("L1") == ["L1_intro", "L1_idle", "shared"]
    d.dirty = False
    win = EditorWindow(d, settings_store=QSettings(tempfile.mktemp(suffix=".ini"), QSettings.Format.IniFormat))
    win.state.set_animation("L2_intro")
    assert d.edit_skin == "L2", "an animation shows its own skin"
    win.timeline._pick_skin("L1")
    assert win.state.animation == "L1_intro", "switching skin keeps the same kind of animation"
    assert win.timeline.listed_animations() == ["L1_intro", "L1_idle", "shared"]
    win.timeline.skin_only.setChecked(False)
    assert len(win.timeline.listed_animations()) == 6
    d.dirty = False
    win.close()
