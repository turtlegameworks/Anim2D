"""Anim2D editor: document model, posing, keys, import/export, FX insertion, render, UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor.document import Document, EditorError, KeyRef  # noqa: E402
from runtime_cores import core_for  # noqa: E402
from anim2d.editor.fx_insert import insert_fx  # noqa: E402
from anim2d.editor.render import render_frames  # noqa: E402
from anim2d.editor.state import EditorState  # noqa: E402
from anim2d.models.settings import validate_settings  # noqa: E402
from anim2d.preview.evaluator import PoseEvaluator  # noqa: E402
from anim2d.validation.spine38_validator import validate_output  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).parent / "runtime" / "vendor"
SPINE_CORE = VENDOR / "spine-core-3.8.js"
SPINEBOY = VENDOR / "spineboy"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")
needs_spineboy = pytest.mark.skipif(not (SPINEBOY / "spineboy-ess.json").is_file(),
                                    reason="needs tools/fetch_spine_runtime.py")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def images(tmp_path) -> Path:
    folder = tmp_path / "images"
    (folder / "parts").mkdir(parents=True)
    shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "impact_basic" / "images" / "ray.png", folder / "parts" / "arm.png")
    shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "minimal_pop" / "images" / "dot.png", folder / "head.png")
    return folder


@pytest.fixture
def rig(images) -> Document:
    """A small rig built through the editing API: root > body > arm, with two slots and a wave animation."""
    doc = Document(images_dir=images)
    doc.add_bone("root", "body", y=100, rotation=90, length=80)
    doc.add_bone("body", "arm", x=60, rotation=-90, length=120)
    doc.add_slot("body", "head", image="head")
    doc.add_slot("arm", "arm", image="parts/arm")
    doc.set_attachment("arm", "arm", x=60)
    doc.add_animation("wave")
    doc.set_bone_key("wave", "arm", "rotate", 0, value=0)
    doc.set_bone_key("wave", "arm", "rotate", 0.5, value=45)
    doc.set_bone_key("wave", "arm", "rotate", 1.0, value=0)
    doc.set_slot_key("wave", "head", "color", 0.5, "ff0000ff")
    return doc


def _runtime(json_path: Path, times, anim=None, atlas=None) -> dict:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(core_for(json_path)), str(json_path)]
    if atlas:
        args.append(f"--atlas={atlas}")
    if anim:
        args.append(f"--anim={anim}")
    out = subprocess.run(args + [str(t) for t in times], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


# --- document model ------------------------------------------------------------------------

def test_built_rig_is_valid_and_ordered(rig):
    assert rig.validate()[0] == []
    assert [b["name"] for b in rig.bones] == ["root", "body", "arm"]
    assert rig.attachments("arm")["arm"] == {"path": "parts/arm", "width": 256, "height": 48, "x": 60}
    assert rig.slot("head")["attachment"] == "head"


def test_undo_redo_restores_exact_state(rig):
    before = json.dumps(rig.data, sort_keys=True)
    rig.rename_bone("arm", "left_arm")
    rig.delete_slot("head")
    assert rig.undo() and rig.undo()
    assert json.dumps(rig.data, sort_keys=True) == before
    assert rig.redo() and rig.has_bone("left_arm")


def test_rename_updates_every_reference(rig):
    rig.rename_bone("arm", "left_arm")
    rig.rename_slot("arm", "arm_slot")
    rig.rename_attachment("arm_slot", "arm", "arm_img")
    rig.rename_animation("wave", "hello")
    assert rig.slot("arm_slot")["bone"] == "left_arm"
    assert rig.slot("arm_slot")["attachment"] == "arm_img"
    assert rig.attachments("arm_slot")["arm_img"]["path"] == "parts/arm"
    assert "left_arm" in rig.animation("hello")["bones"]
    assert rig.validate()[0] == []
    with pytest.raises(EditorError, match="already exists"):
        rig.rename_bone("left_arm", "body")


def test_delete_bone_cascades(rig):
    rig.delete_bone("body")
    assert [b["name"] for b in rig.bones] == ["root"]
    assert rig.slots == [] and rig.skin == {}
    assert "bones" not in rig.animation("wave") or "arm" not in rig.animation("wave")["bones"]
    with pytest.raises(EditorError):
        rig.delete_bone("root")


def test_reparent_keeps_world_pose(rig):
    before = rig.bone_world_setup()["arm"]
    rig.reparent_bone("arm", "root")
    after = rig.bone_world_setup()["arm"]
    assert all(a == pytest.approx(b, abs=1e-3) for a, b in zip(before, after))
    with pytest.raises(EditorError):
        rig.reparent_bone("body", "body")


def test_key_editing(rig):
    rig.set_bone_key("wave", "arm", "rotate", 0.5, angle=30)  # 3.8 name still accepted; replaces the key at 0.5
    keys = rig.animation("wave")["bones"]["arm"]["rotate"]
    assert [k["time"] for k in keys] == [0, 0.5, 1.0] and keys[1]["value"] == 30
    ref = KeyRef("bones", "arm", "rotate", 0.5)
    rig.set_curve("wave", [ref], (0.25, 0.1, 0.25, 1))
    # 4.0 stores absolute handles: segment 0.5s -> 1.0s, value 30 -> 0.
    assert keys[1]["curve"] == pytest.approx([0.625, 27.0, 0.625, 0.0])
    moved = rig.move_keys("wave", [ref], 0.5)  # lands on the key at 1.0 and replaces it
    keys = rig.animation("wave")["bones"]["arm"]["rotate"]
    assert [k["time"] for k in keys] == [0, 1.0] and keys[1]["value"] == 30 and moved[0].time == 1.0
    assert "curve" not in keys[1], "the last key has no segment, so no curve"
    rig.delete_keys("wave", [KeyRef("bones", "arm", "rotate", 0), KeyRef("bones", "arm", "rotate", 1.0)])
    assert "bones" not in rig.animation("wave")
    rows = rig.timeline_rows("wave")
    assert [(r.owner, r.timeline) for r, _ in rows] == [("head", "rgba")]


def test_curves_keep_their_shape_when_keys_change(rig):
    """Absolute 4.0 handles are re-fitted when a key is retimed or its value changes."""
    rig.set_curve("wave", [KeyRef("bones", "arm", "rotate", 0)], (0.42, 0.0, 0.58, 1.0))  # ease in-out 0 -> 45

    def progress(t, v0, v1):
        return (PoseEvaluator(rig.data, "wave").bone_locals(t)["arm"][2] - rig.bone("arm")["rotation"] - v0) / (v1 - v0)
    before = [progress(0.5 * f, 0, 45) for f in (0.2, 0.5, 0.8)]
    rig.move_keys("wave", [KeyRef("bones", "arm", "rotate", 0.5)], 0.25)  # segment now 0 -> 0.75s
    assert [progress(0.75 * f, 0, 45) for f in (0.2, 0.5, 0.8)] == pytest.approx(before, abs=1e-6)
    rig.set_bone_key("wave", "arm", "rotate", 0.75, value=90)  # value 45 -> 90
    assert [progress(0.75 * f, 0, 90) for f in (0.2, 0.5, 0.8)] == pytest.approx(before, abs=1e-6)
    assert before[0] < 0.2 and before[2] > 0.8, "an ease in-out curve"


def test_animate_mode_writes_relative_keys(qapp, rig):
    state = EditorState(rig)
    state.set_animation("wave")
    state.set_mode("animate")
    state.set_time(0.25)
    state.apply_bone_pose("body", rotation=120, x=10, scaleX=2)
    anim = rig.animation("wave")["bones"]["body"]
    assert anim["rotate"][0] == {"time": 0.25, "value": 30}  # setup rotation 90
    assert anim["translate"][0] == {"time": 0.25, "x": 10, "y": 0}  # setup y=100 kept
    assert anim["scale"][0] == {"time": 0.25, "x": 2, "y": 1}
    posed = PoseEvaluator(rig.data, "wave").bone_locals(0.25)["body"]
    assert posed[:4] == pytest.approx((10, 100, 120, 2))
    state.set_mode("setup")
    state.apply_bone_pose("body", rotation=45)
    assert rig.bone("body")["rotation"] == 45


def test_viewport_tools(qapp, rig):
    from anim2d.editor.viewport import Viewport
    state = EditorState(rig)
    vp = Viewport(state)
    vp.resize(800, 600)
    # Rotate: drag a quarter turn around the arm's origin.
    state.select("bone", "arm")
    world = rig.bone_world_setup()["arm"]
    ox, oy = world[4], world[5]
    assert vp.begin_drag(ox + 100, oy, "rotate")
    vp.drag_to(ox, oy + 100)
    vp.end_drag()
    assert rig.bone("arm")["rotation"] == pytest.approx(0, abs=1e-3)  # -90 + 90
    # Translate in world space; the parent is rotated 90°, so world +X becomes local -Y.
    assert vp.begin_drag(0, 0, "translate")
    vp.drag_to(30, 0)
    vp.end_drag()
    assert (rig.bone("arm")["x"], rig.bone("arm")["y"]) == pytest.approx((60, -30), abs=1e-3)
    assert rig.undo_label() == "Translate arm"
    # Create bone: drag from (200, 0) to (200, 50) under root.
    state.select("bone", "root")
    vp.begin_drag(200, 0, "create")
    vp.drag_to(200, 50)
    name = vp.end_drag()
    bone = rig.bone(name)
    assert (bone["x"], bone["y"], bone["rotation"], bone["length"]) == pytest.approx((200, 0, 90, 50), abs=1e-3)
    assert state.selection.name == name


def test_project_save_load(tmp_path, rig):
    path = tmp_path / "proj" / "hero.rig2d"
    path.parent.mkdir()
    rig.save(path)
    assert not rig.dirty
    loaded = Document.load(path)
    assert loaded.data == rig.data
    assert loaded.images_dir == rig.images_dir.resolve()
    assert json.loads(path.read_text())["images_dir"] == "../images"
    (tmp_path / "bad.rig2d").write_text("{}")
    with pytest.raises(EditorError, match="not an Anim2D project"):
        Document.load(tmp_path / "bad.rig2d")


@needs_runtime
@pytest.mark.parametrize("stamp, core_folder", [("4.0.64", "v40"), ("3.8.99", "vendor")])
def test_export_plays_in_official_runtime(tmp_path, rig, stamp, core_folder):
    rig.set_curve("wave", [KeyRef("bones", "arm", "rotate", 0)], (0.42, 0.0, 0.58, 1.0))
    out = tmp_path / "export" / "hero.json"
    rig.export_spine_json(out, stamp=stamp)
    assert (out.parent / "images" / "parts" / "arm.png").is_file()
    data = json.loads(out.read_text())
    assert data["skeleton"]["spine"] == stamp
    assert validate_output(data, out.parent, None, strict_one_shot=False, spine_version=None, allow_ik=True).ok
    assert core_for(out).parent.name == core_folder, "the matching official runtime plays the export"
    run = _runtime(out, [0.1003, 0.25, 0.5], anim="wave")
    assert run["restamped_for_runtime_check"] is False
    mine = PoseEvaluator(rig.data, "wave")  # the editor's own data, not the export
    for sample in run["samples"]:
        world = mine.bone_world(sample["time"])
        for name, (x, y) in sample["bones"].items():
            assert (world[name][4], world[name][5]) == pytest.approx((x, y), abs=0.05), (name, sample["time"])
    with pytest.raises(EditorError, match="already exists"):
        rig.export_spine_json(out, stamp=stamp)


def test_binary_export_plays_like_json(tmp_path, rig):
    rig.set_curve("wave", [KeyRef("bones", "arm", "rotate", 0)], (0.42, 0.0, 0.58, 1.0))
    json_out, skel_out = tmp_path / "export" / "hero.json", tmp_path / "export" / "hero.skel"
    rig.export_spine_json(json_out)
    rig.export_spine_json(skel_out, binary=True)
    assert skel_out.read_bytes()[:8] == b"\0" * 8, "no hash"
    times = [0.1003, 0.25, 0.5]
    from_json, from_skel = _runtime(json_out, times, anim="wave"), _runtime(skel_out, times, anim="wave")
    assert from_skel["version"] == "4.0.64"
    for a, b in zip(from_json["samples"], from_skel["samples"], strict=True):
        assert a["bones"] == b["bones"] and a["visible"] == b["visible"]
    with pytest.raises(EditorError, match="Spine 4.0 only"):
        rig.export_spine_json(tmp_path / "old.skel", stamp="3.8.99", binary=True)


def test_export_with_packed_atlas_loads_through_the_runtime_atlas(tmp_path, rig):
    from anim2d.formats.atlas import PackSettings
    out = tmp_path / "packed" / "hero.skel"
    rig.export_spine_json(out, binary=True, atlas=PackSettings(max_width=512, max_height=512))
    atlas = out.with_suffix(".atlas")
    assert atlas.is_file() and (out.parent / "hero.png").is_file()
    assert not (out.parent / "images").exists(), "packed exports do not copy loose images"
    run = _runtime(out, [0.25], anim="wave", atlas=atlas)
    assert {a["name"] for a in run["attachments"]} == {"arm", "head"}  # keyed by name; "arm" uses the region parts/arm
    assert all(a["page"] == "hero.png" and len(a["uvs"]) == 8 for a in run["attachments"])
    with pytest.raises(EditorError, match="already exists"):
        rig.export_spine_json(out.with_suffix(".json"), atlas=PackSettings())


@needs_spineboy
def test_spineboy_import_export_round_trip(tmp_path):
    """A 3.8 file imported (converted to 4.0) and exported back as 3.8 keeps its structure and poses."""
    source_path = SPINEBOY / "spineboy-ess.json"
    source = json.loads(source_path.read_text())
    doc, warnings = Document.import_spine_json(source_path)
    assert not [w for w in warnings if "not editable" in w], "every attachment type is editable now"
    assert doc.data["skeleton"]["spine"].startswith("4.0")
    assert doc.validate()[0] == []
    out = tmp_path / "boy.json"
    notes = doc.export_spine_json(out, stamp="3.8.99", copy_images=False)
    assert not any("converted" in n for n in notes), "3.8 data round-trips without extra keys"
    data = json.loads(out.read_text())
    for key in ("bones", "slots", "skins", "events"):
        assert data[key] == source[key], key
    assert set(data["animations"]) == set(source["animations"])
    if NODE and SPINE_CORE.is_file():
        times = [0.0003, 0.2003, 0.5003]
        for anim in ("run", "shoot", "jump"):
            a, b = _runtime(source_path, times, anim=anim), _runtime(out, times, anim=anim)
            for sa, sb in zip(a["samples"], b["samples"]):
                for bone, (x, y) in sa["bones"].items():
                    assert sb["bones"][bone] == pytest.approx([x, y], abs=0.02), (anim, bone)


@needs_spineboy
def test_insert_fx_into_character(tmp_path, library):
    shutil.copytree(SPINEBOY, tmp_path / "boy")
    doc, _ = Document.import_spine_json(tmp_path / "boy" / "spineboy-ess.json")
    before_shoot = json.dumps(doc.animation("shoot"), sort_keys=True)
    preset = library.get("impact_basic")
    settings, _ = validate_settings(preset, "fx_hit_01", {"scale": 0.5, "delay": 0.05})
    result = insert_fx(doc, preset, settings, "gun-tip", "shoot", 0.1)
    assert (result.bones, result.slots, result.images) == (10, 9, 2)
    assert result.end == pytest.approx(0.1 + 0.05 + 0.35)
    assert doc.bone("fx_hit_01_root")["parent"] == "gun-tip"
    assert doc.slots[-1]["name"] == "fx_hit_01_flash_slot", "FX draws on top"
    assert (tmp_path / "boy" / "images" / "fx_hit_01" / "ray.png").is_file()
    assert doc.validate()[0] == []
    shoot = doc.animation("shoot")
    original = json.loads(before_shoot)
    for group in ("bones", "slots"):
        for name, tls in original.get(group, {}).items():
            assert shoot[group][name] == tls, "existing keys must be untouched"
    times = [k["time"] for k in shoot["slots"]["fx_hit_01_ray_01_slot"]["attachment"]]
    assert times[0] == pytest.approx(0.1) and times[-1] == pytest.approx(0.5)
    with pytest.raises(EditorError, match="already used"):
        insert_fx(doc, preset, settings, "gun-tip", "shoot", 0.3)
    assert doc.undo() and not doc.has_bone("fx_hit_01_root")

    if NODE and SPINE_CORE.is_file():
        assert doc.redo()
        out = tmp_path / "out" / "boy.json"
        doc.export_spine_json(out)
        run = _runtime(out, [0.12, 0.3, 0.6], anim="shoot")
        visible = {s["time"]: {v["slot"] for v in s["visible"]} for s in run["samples"]}
        assert not any(n.startswith("fx_hit_01") for n in visible[0.12]), "hidden during the FX delay"
        assert "fx_hit_01_flash_slot" in visible[0.3]
        assert not any(n.startswith("fx_hit_01") for n in visible[0.6]), "hidden after the FX ends"
        assert "head" in visible[0.3], "character still drawn"


def test_import_photoshop_to_spine_export(tmp_path):
    """The Photoshop-to-Spine script writes no version, skins as an object, and an absolute image folder."""
    folder = tmp_path / "level"
    (folder / "images").mkdir(parents=True)
    shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "minimal_pop" / "images" / "dot.png", folder / "images" / "Bamboo Level1.png")
    shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "minimal_pop" / "images" / "dot.png", folder / "images" / "Ground.png")
    psd = {
        "skeleton": {"images": "C:/Users/someone/Downloads/images/"},
        "bones": [{"name": "root"}],
        "slots": [{"name": "Ground", "bone": "root", "attachment": "Ground"},
                  {"name": "Bamboo Level1", "bone": "root", "attachment": "Bamboo Level1"}],
        "skins": {"default": {
            "Ground": {"Ground": {"x": 64, "y": -64, "width": 128, "height": 128}},
            "Bamboo Level1": {"Bamboo Level1": {"x": 30, "y": -20, "width": 128, "height": 128}}}},
        "animations": {"animation": {}},
    }
    path = folder / "level.json"
    path.write_text(json.dumps(psd))
    doc, warnings = Document.import_spine_json(path)
    assert doc.images_dir == folder / "images"
    assert any("no Spine version" in w for w in warnings) and any("another computer" in w for w in warnings)
    assert doc.data["skins"][0]["name"] == "default" and doc.validate()[0] == []
    out = tmp_path / "out" / "level.json"
    doc.export_spine_json(out)
    assert (out.parent / "images" / "Bamboo Level1.png").is_file()
    exported = json.loads(out.read_text())
    assert exported["skeleton"]["spine"] == "4.0.64" and isinstance(exported["skins"], list)
    if NODE and SPINE_CORE.is_file():
        assert len(_runtime(out, [0.01])["samples"][0]["visible"]) == 2

    path.write_text(json.dumps({**psd, "skeleton": {"spine": "4.1.23"}}))
    with pytest.raises(EditorError, match="Spine 4.1.23 data"):
        Document.import_spine_json(path)


def test_insert_fx_needs_images_folder(library):
    doc = Document()
    preset = library.get("impact_basic")
    settings, _ = validate_settings(preset, "fx_hit_01", {})
    with pytest.raises(EditorError, match="images folder"):
        insert_fx(doc, preset, settings, "root", "fx", 0)


def test_render_frames(qapp, tmp_path, rig):
    files = render_frames(rig, "wave", tmp_path / "frames", size=128)
    assert len(files) == 31 and all(f.is_file() for f in files)  # 1s at 30 fps, both ends
    sheet = render_frames(rig, "wave", tmp_path / "frames", size=64, sheet=True)
    assert sheet[0].name == "wave_sheet.png"
    with pytest.raises(EditorError, match="already exists"):
        render_frames(rig, "wave", tmp_path / "frames", size=128)


def test_editor_window_smoke(qapp, tmp_path, rig, library):
    from anim2d.editor.fx_dialog import InsertFxDialog
    from anim2d.editor.main_window import EditorWindow
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    win = EditorWindow(rig, library, store)
    win.show()
    qapp.processEvents()
    win.state.select("bone", "arm")
    assert win.properties.title.text() == "Bone  arm"
    win.state.set_animation("wave")
    win.state.set_mode("animate")
    win.state.set_time(0.5)
    qapp.processEvents()
    assert win.timeline.sheet.rows()
    assert "valid" in win.validation_label.text()
    image = win.viewport.grab().toImage()
    assert image.pixelColor(image.width() // 2, image.height() // 2).name() != "#1e1f26"

    dialog = InsertFxDialog(rig, library, "arm", "wave", 0.2, win)
    dialog.select_preset("impact_basic")
    assert dialog.insert_button.isEnabled(), [dialog.messages.item(i).text() for i in range(dialog.messages.count())]
    dialog._insert()
    assert dialog.result is not None and rig.has_bone(f"{dialog.result.prefix}_root")
    win._after_insert(dialog.result)
    assert win.state.selection.name == f"{dialog.result.prefix}_root"
    rig.dirty = False
    win.close()
