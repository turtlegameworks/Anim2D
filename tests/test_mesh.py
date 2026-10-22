"""Meshes in Anim2D: grid conversion, weights, deform keys, bone-index upkeep, rendering, runtime parity."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor import mesh_ops  # noqa: E402
from anim2d.editor.document import Document, EditorError, KeyRef  # noqa: E402
from runtime_cores import core_for  # noqa: E402
from anim2d.preview.evaluator import PoseEvaluator  # noqa: E402
from anim2d.preview.mesh import parse_vertices  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).parent / "runtime" / "vendor"
SPINE_CORE = VENDOR / "spine-core-3.8.js"
RAPTOR = VENDOR / "raptor" / "raptor-pro.json"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def arm(tmp_path) -> Document:
    """upper > lower arm with one image spanning both bones, as a region."""
    images = tmp_path / "images"
    images.mkdir()
    shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "impact_basic" / "images" / "ray.png", images / "arm.png")
    doc = Document(images_dir=images)
    doc.add_bone("root", "upper", y=50, rotation=10, length=100)
    doc.add_bone("upper", "lower", x=100, length=100)
    doc.add_slot("upper", "arm", image="arm")
    doc.set_attachment("arm", "arm", x=100, scaleX=200 / 256, scaleY=1.5)
    return doc


def _close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for p, q in zip(a, b) for x, y in zip(p, q))


def test_convert_to_mesh_keeps_the_look(qapp, arm):
    region_data = json.loads(json.dumps(arm.data))
    region = PoseEvaluator(region_data, "").draw_items(0)[0].points()
    mesh_ops.convert_to_mesh(arm, "arm", "arm", columns=4, rows=2)
    att = arm.attachments("arm")["arm"]
    mesh = parse_vertices(att)
    assert mesh.count == 15 and not mesh.weighted and att["hull"] == 12 and len(att["triangles"]) == 48
    verts = mesh_ops.vertex_world(arm, "arm", "arm")
    corners = [verts[i] for i in (0, 4, 6, 10)]  # hull ring: top-left, top-right, bottom-right, bottom-left
    assert _close(corners, region, 1e-3)
    assert arm.validate()[0] == []

    # The textured-triangle renderer draws the mesh like the region.
    from anim2d.preview.qt_renderer import ImageCache, render_image, world_bounds
    from PySide6.QtGui import QColor
    cache = ImageCache()
    cache.set_images({"arm": arm.image_file("arm")})
    ev_region, ev_mesh = PoseEvaluator(region_data, ""), PoseEvaluator(arm.data, "")
    bounds = world_bounds(ev_region)
    a = render_image(ev_region, cache, 0, 200, QColor("black"), bounds)
    b = render_image(ev_mesh, cache, 0, 200, QColor("black"), bounds)
    diff = sum(abs(a.pixelColor(x, y).value() - b.pixelColor(x, y).value())
               for x in range(0, 200, 2) for y in range(0, 200, 2)) / 10000
    assert diff < 2.0, f"mesh render differs from region render by {diff:.2f} levels on average"


def test_bind_unbind_and_weights_keep_positions(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 1)
    before = mesh_ops.vertex_world(arm, "arm", "arm")
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    mesh = parse_vertices(arm.attachments("arm")["arm"])
    assert mesh.weighted and all(1 <= len(v) <= 4 for v in mesh.influences)
    assert all(abs(sum(i.weight for i in v) - 1) < 1e-3 for v in mesh.influences)
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), before, 1e-3)
    # Far-end vertices follow the lower bone, near-end ones the upper bone.
    assert mesh_ops.vertex_weights(arm, "arm", "arm", 4).get("lower", 0) > 0.5
    assert mesh_ops.vertex_weights(arm, "arm", "arm", 0).get("upper", 0) > 0.5
    mesh_ops.set_vertex_weights(arm, "arm", "arm", 2, mesh_ops.adjust_weight(
        mesh_ops.vertex_weights(arm, "arm", "arm", 2), "upper", 0.8))
    assert mesh_ops.vertex_weights(arm, "arm", "arm", 2)["upper"] == pytest.approx(0.8, abs=1e-3)
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), before, 1e-3)
    mesh_ops.unbind(arm, "arm", "arm")
    assert not parse_vertices(arm.attachments("arm")["arm"]).weighted
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), before, 1e-3)
    assert arm.undo() and parse_vertices(arm.attachments("arm")["arm"]).weighted


def test_weighted_mesh_bends_with_bones(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 1)
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    arm.add_animation("bend")
    arm.set_bone_key("bend", "lower", "rotate", 0, angle=90)
    straight = mesh_ops.vertex_world(arm, "arm", "arm")
    bent = mesh_ops.vertex_world(arm, "arm", "arm", "bend", 0)
    import math
    far, near = math.dist(bent[4], straight[4]), math.dist(bent[0], straight[0])
    assert far > 100, "the far end follows the rotated lower bone"
    assert near < far * 0.2, "the near end mostly stays with the upper bone"


def test_deform_keys(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 1)
    arm.add_animation("wobble")
    start = mesh_ops.vertex_world(arm, "arm", "arm", "wobble", 0)
    target = (start[2][0] + 10, start[2][1] + 30)
    mesh_ops.set_deform_key(arm, "wobble", "arm", "arm", 0.5, {2: target})
    mesh_ops.set_deform_key(arm, "wobble", "arm", "arm", 0, reset=True)
    keys = arm.animation("wobble")["deform"]["default"]["arm"]["arm"]
    assert keys[0] == {"time": 0} and keys[1]["offset"] == 4 and len(keys[1]["vertices"]) == 2
    at_key = mesh_ops.vertex_world(arm, "arm", "arm", "wobble", 0.5)
    assert _close([at_key[2]], [target], 1e-3)
    assert _close([at_key[i] for i in (0, 1, 3)], [start[i] for i in (0, 1, 3)], 1e-6)
    mid = mesh_ops.vertex_world(arm, "arm", "arm", "wobble", 0.25)
    assert _close([mid[2]], [((start[2][0] + target[0]) / 2, (start[2][1] + target[1]) / 2)], 1e-3)
    rows = arm.timeline_rows("wobble")
    assert [(r.group, r.owner) for r, _ in rows] == [("deform", "arm\narm")]
    arm.set_curve("wobble", [KeyRef("deform", "arm\narm", "deform", 0)], "stepped")
    assert keys[0]["curve"] == "stepped"
    arm.delete_keys("wobble", [KeyRef("deform", "arm\narm", "deform", 0), KeyRef("deform", "arm\narm", "deform", 0.5)])
    assert "deform" not in arm.animation("wobble")


def test_rebinding_preserves_deform_keys(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 3, 1)
    arm.add_animation("a")
    start = mesh_ops.vertex_world(arm, "arm", "arm", "a", 0)
    mesh_ops.set_deform_key(arm, "a", "arm", "arm", 0.4, {1: (start[1][0], start[1][1] + 25),
                                                          5: (start[5][0] - 8, start[5][1])})
    shaped = mesh_ops.vertex_world(arm, "arm", "arm", "a", 0.4)
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm", "a", 0.4), shaped, 1e-3)
    mesh_ops.unbind(arm, "arm", "arm")
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm", "a", 0.4), shaped, 1e-3)


def test_bone_edits_keep_weights_valid(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 1)
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    before = mesh_ops.vertex_world(arm, "arm", "arm")
    arm.add_bone("root", "aaa")  # inserted before 'upper' in the bone list? (after root's descendants)
    arm.add_bone("upper", "elbow-pad", x=95)  # inserted between upper and lower: shifts 'lower'
    arm.reparent_bone("aaa", "lower")
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), before, 1e-3)
    assert arm.validate()[0] == []
    with pytest.raises(EditorError, match="weights mesh"):
        arm.delete_bone("lower")
    arm.delete_bone("elbow-pad")
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), before, 1e-3)


def test_validator_catches_bad_meshes(arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 2, 1)
    mesh_ops.bind(arm, "arm", "arm", ["upper"])
    att = arm.attachments("arm")["arm"]
    att["vertices"][1] = 99  # bone index out of range
    assert any("bone index" in e for e in arm.validate()[0])
    arm.undo()
    arm.add_animation("x")
    arm.animation("x")["deform"] = {"default": {"arm": {"arm": [{"time": 0, "offset": 10, "vertices": [1] * 10}]}}}
    assert any("longer than the mesh" in e for e in arm.validate()[0])


def test_viewport_vertex_editing(qapp, arm):
    from anim2d.editor.state import EditorState
    from anim2d.editor.viewport import Viewport
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 1)
    state = EditorState(arm)
    vp = Viewport(state)
    vp.resize(800, 600)
    state.select("attachment", "arm", "arm")
    assert state.active_mesh() == ("arm", "arm")
    start = mesh_ops.vertex_world(arm, "arm", "arm")
    state.set_vertex_selection({3})
    assert vp.begin_drag(*start[3], "translate")
    vp.drag_to(start[3][0] + 15, start[3][1] - 5)
    vp.end_drag()
    moved = mesh_ops.vertex_world(arm, "arm", "arm")
    assert _close([moved[3]], [(start[3][0] + 15, start[3][1] - 5)], 1e-3)
    assert _close([moved[0]], [start[0]])
    assert arm.undo_label() == "Translate vertices arm"
    # Animate mode: the same drag writes a deform key instead of changing the setup shape.
    arm.add_animation("anim")
    state.set_animation("anim")
    state.set_mode("animate")
    state.set_time(0.3)
    state.set_vertex_selection({0})
    vp.begin_drag(*moved[0], "translate")
    vp.drag_to(moved[0][0], moved[0][1] + 40)
    vp.end_drag()
    assert "deform" in arm.animation("anim")
    assert _close(mesh_ops.vertex_world(arm, "arm", "arm"), moved), "setup shape unchanged"
    posed = mesh_ops.vertex_world(arm, "arm", "arm", "anim", 0.3)
    assert _close([posed[0]], [(moved[0][0], moved[0][1] + 40)], 1e-3)


def test_mesh_properties_panel(qapp, arm):
    from anim2d.editor.properties import PropertiesPanel
    from anim2d.editor.state import EditorState
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 2, 1)
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    state = EditorState(arm)
    panel = PropertiesPanel(state)
    state.select("attachment", "arm", "arm")
    assert panel.title.text() == "Mesh  arm"
    state.set_vertex_selection({1})
    labels = [panel.form.itemAt(i).widget().text() for i in range(panel.form.count())
              if isinstance(panel.form.itemAt(i).widget(), __import__("PySide6.QtWidgets").QtWidgets.QLabel)]
    assert any("Vertex 1 weights" in text for text in labels)


def _runtime_meshes(json_path: Path, anim: str, times) -> dict:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(core_for(json_path)), str(json_path),
            f"--anim={anim}", *map(str, times)]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout)


def _compare_meshes(path: Path, anim: str, times, tol=0.05) -> int:
    data = json.loads(path.read_text())
    ev = PoseEvaluator(data, anim)
    compared = 0
    for sample in _runtime_meshes(path, anim, times)["samples"]:
        for entry in sample["visible"]:
            if "mesh" not in entry:
                continue
            mine = ev.mesh_world_vertices(entry["slot"], entry["attachment"], sample["time"])
            theirs = entry["mesh"]
            assert len(theirs) == 2 * len(mine)
            for i, (x, y) in enumerate(mine):
                assert (x, y) == pytest.approx((theirs[2 * i], theirs[2 * i + 1]), abs=tol), \
                    (anim, sample["time"], entry["slot"], i)
            compared += 1
    return compared


@needs_runtime
def test_edited_meshes_match_runtime(tmp_path, arm):
    mesh_ops.convert_to_mesh(arm, "arm", "arm", 4, 2)
    mesh_ops.bind(arm, "arm", "arm", ["upper", "lower"])
    arm.add_bone("root", "hand-target", x=150, y=120)
    arm.add_ik(["upper", "lower"], "hand-target")
    arm.add_animation("reach")
    arm.set_bone_key("reach", "hand-target", "translate", 0, x=0, y=0)
    arm.set_bone_key("reach", "hand-target", "translate", 1, x=-60, y=-80)
    start = mesh_ops.vertex_world(arm, "arm", "arm", "reach", 0.5)
    mesh_ops.set_deform_key(arm, "reach", "arm", "arm", 0.5, {7: (start[7][0] + 20, start[7][1] + 20)})
    arm.set_curve("reach", [KeyRef("deform", "arm\narm", "deform", 0.5)], (0.3, 0, 0.7, 1))
    mesh_ops.set_deform_key(arm, "reach", "arm", "arm", 1, reset=True)
    out = tmp_path / "arm.json"
    arm.export_spine_json(out)
    assert _compare_meshes(out, "reach", [0.0007, 0.3, 0.5, 0.75, 1]) == 5


@needs_runtime
@pytest.mark.skipif(not RAPTOR.is_file(), reason="needs tools/fetch_spine_runtime.py")
@pytest.mark.parametrize("anim", ["walk", "roar"])  # the raptor animations with deform keys
def test_raptor_meshes_match_runtime(tmp_path, anim):
    doc, _ = Document.import_spine_json(RAPTOR)
    out = tmp_path / "raptor.json"
    doc.export_spine_json(out, copy_images=False)
    duration = doc.animation_duration(anim)
    assert _compare_meshes(out, anim, [round(duration * f, 4) + 0.0007 for f in (0, 0.2, 0.45, 0.8)]) > 20
