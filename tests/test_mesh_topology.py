"""Adding/removing mesh vertices, re-triangulation and UV editing."""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anim2d.editor import mesh_ops  # noqa: E402
from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.preview.evaluator import PoseEvaluator  # noqa: E402
from anim2d.preview.mesh import parse_vertices  # noqa: E402
from anim2d.preview.qt_renderer import ImageCache, render_image, world_bounds  # noqa: E402

from test_mesh import NODE, SPINE_CORE, _compare_meshes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
needs_runtime = pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def arm(tmp_path) -> Document:
    images = tmp_path / "images"
    images.mkdir()
    shutil.copy(ROOT / "tests" / "runtime" / "vendor" / "spineboy" / "images" / "gun.png", images / "arm.png") \
        if (ROOT / "tests/runtime/vendor/spineboy/images/gun.png").is_file() else \
        shutil.copy(ROOT / "src" / "anim2d" / "preset_library" / "minimal_pop" / "images" / "dot.png", images / "arm.png")
    doc = Document(images_dir=images)
    doc.add_bone("root", "upper", y=50, rotation=10, length=100)
    doc.add_bone("upper", "lower", x=100, length=100)
    doc.add_slot("upper", "arm", image="arm")
    doc.set_attachment("arm", "arm", x=100, scaleX=1.2, scaleY=1.2)
    mesh_ops.convert_to_mesh(doc, "arm", "arm", 3, 2)
    mesh_ops.bind(doc, "arm", "arm", ["upper", "lower"])
    doc.add_animation("wave")
    doc.set_bone_key("wave", "lower", "rotate", 0.6, angle=40)
    start = mesh_ops.vertex_world(doc, "arm", "arm", "wave", 0.4)
    mesh_ops.set_deform_key(doc, "wave", "arm", "arm", 0.4, {2: (start[2][0] + 15, start[2][1] + 10)})
    return doc


def _render(doc: Document, anim: str = "", t: float = 0.0, bounds=None):
    cache = ImageCache()
    cache.set_images({p: doc.image_file(p) for p in doc.used_images()})
    ev = PoseEvaluator(doc.data, anim)
    bounds = bounds if bounds is not None else world_bounds(ev)
    return render_image(ev, cache, t, 160, QColor("black"), bounds), bounds


def _mean_diff(a, b) -> float:
    total = 0
    for x in range(0, 160, 2):
        for y in range(0, 160, 2):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            total += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue())
    return total / (80 * 80 * 3)


def _att(doc):
    return doc.attachments("arm")["arm"]


def test_add_interior_vertex_changes_nothing_visible(qapp, arm):
    before_setup, bounds = _render(arm)
    before_posed, posed_bounds = _render(arm, "wave", 0.4)
    old_setup = mesh_ops.vertex_world(arm, "arm", "arm")
    old_posed = mesh_ops.vertex_world(arm, "arm", "arm", "wave", 0.4)
    hull = _att(arm)["hull"]
    a, b, c = (_att(arm)["triangles"][k] for k in range(3))
    point = tuple(sum(old_setup[i][k] for i in (a, b, c)) / 3 for k in range(2))  # centroid of a triangle

    index = mesh_ops.add_vertex(arm, "arm", "arm", point)
    mesh = parse_vertices(_att(arm))
    assert index == len(old_setup) and mesh.count == len(old_setup) + 1 and _att(arm)["hull"] == hull
    new_setup = mesh_ops.vertex_world(arm, "arm", "arm")
    assert all(math.dist(p, q) < 1e-3 for p, q in zip(new_setup, old_setup)), "old vertices stay"
    assert math.dist(new_setup[index], point) < 1e-3
    assert abs(sum(i.weight for i in mesh.influences[index]) - 1) < 1e-3
    new_posed = mesh_ops.vertex_world(arm, "arm", "arm", "wave", 0.4)
    assert all(math.dist(p, q) < 1e-3 for p, q in zip(new_posed, old_posed)), "deform keys keep old vertices"
    assert arm.validate()[0] == []
    assert _mean_diff(before_setup, _render(arm, bounds=bounds)[0]) < 2.0
    assert _mean_diff(before_posed, _render(arm, "wave", 0.4, posed_bounds)[0]) < 3.0
    assert arm.undo() and parse_vertices(_att(arm)).count == len(old_setup)


def test_add_vertex_on_outline_joins_hull(arm):
    pos = mesh_ops.vertex_world(arm, "arm", "arm")
    hull = _att(arm)["hull"]
    mid = ((pos[0][0] + pos[1][0]) / 2, (pos[0][1] + pos[1][1]) / 2)
    index = mesh_ops.add_vertex(arm, "arm", "arm", (mid[0] + 1, mid[1]), edge_tolerance=3)
    assert index == 1 and _att(arm)["hull"] == hull + 1, "inserted into the ring between 0 and 1"
    new = mesh_ops.vertex_world(arm, "arm", "arm")
    assert math.dist(new[1], mid) < 1.01
    assert arm.validate()[0] == []
    with pytest.raises(EditorError, match="inside the mesh"):
        mesh_ops.add_vertex(arm, "arm", "arm", (5000, 5000))
    with pytest.raises(EditorError, match="already exists"):
        mesh_ops.add_vertex(arm, "arm", "arm", pos[0], edge_tolerance=3)


def test_remove_vertices(qapp, arm):
    count = parse_vertices(_att(arm)).count
    hull = _att(arm)["hull"]
    interior = [i for i in range(hull, count)]
    before, bounds = _render(arm)
    mesh_ops.remove_vertices(arm, "arm", "arm", {1})  # a collinear outline point: the look is unchanged
    assert parse_vertices(_att(arm)).count == count - 1 and _att(arm)["hull"] == hull - 1
    assert _mean_diff(before, _render(arm, bounds=bounds)[0]) < 2.0
    mesh_ops.remove_vertices(arm, "arm", "arm", {interior[0] - 1})
    assert arm.validate()[0] == []
    assert len(arm.animation("wave")["deform"]["default"]["arm"]["arm"]) == 1, "deform keys follow"
    with pytest.raises(EditorError, match="at least 3"):
        mesh_ops.remove_vertices(arm, "arm", "arm", set(range(_att(arm)["hull"] - 2)))


def test_retriangulate_and_reject_bad_outline(arm):
    mesh_ops.retriangulate(arm, "arm", "arm")
    assert arm.validate()[0] == []
    att = _att(arm)
    before = json.dumps(att, sort_keys=True)
    uvs = att["uvs"]
    uvs[0], uvs[2] = uvs[2], uvs[0]  # swap two hull UVs: the outline crosses itself in UV space
    uvs[1], uvs[3] = uvs[3], uvs[1]
    with pytest.raises(EditorError, match="crosses itself"):
        mesh_ops.retriangulate(arm, "arm", "arm")
    uvs[0], uvs[2] = uvs[2], uvs[0]
    uvs[1], uvs[3] = uvs[3], uvs[1]
    assert json.dumps(att, sort_keys=True) == before


def test_drag_modes(qapp, arm):
    before, bounds = _render(arm)
    pos = mesh_ops.vertex_world(arm, "arm", "arm")
    hull = _att(arm)["hull"]
    v = hull  # first interior vertex
    target = {v: (pos[v][0] + 12, pos[v][1] - 6)}
    uv_before = (_att(arm)["uvs"][2 * v], _att(arm)["uvs"][2 * v + 1])

    mapper = mesh_ops.uv_mapper(arm, "arm", "arm")
    expected_uv = mapper(*target[v])
    mesh_ops.drag_vertices(arm, "arm", "arm", target, "mesh", mapper)
    assert math.dist(mesh_ops.vertex_world(arm, "arm", "arm")[v], target[v]) < 1e-3
    assert (_att(arm)["uvs"][2 * v], _att(arm)["uvs"][2 * v + 1]) == pytest.approx(expected_uv, abs=1e-4)
    assert _mean_diff(before, _render(arm, bounds=bounds)[0]) < 2.0, "edit-mesh drag keeps the image in place"
    arm.undo()

    mesh_ops.drag_vertices(arm, "arm", "arm", target, "uv", mesh_ops.uv_mapper(arm, "arm", "arm"))
    assert math.dist(mesh_ops.vertex_world(arm, "arm", "arm")[v], pos[v]) < 1e-3, "UV drag keeps the vertex"
    assert (_att(arm)["uvs"][2 * v], _att(arm)["uvs"][2 * v + 1]) != pytest.approx(uv_before, abs=1e-4)
    arm.undo()

    mesh_ops.drag_vertices(arm, "arm", "arm", target, "reshape")
    assert (_att(arm)["uvs"][2 * v], _att(arm)["uvs"][2 * v + 1]) == pytest.approx(uv_before)


def test_viewport_alt_click_and_delete(qapp, arm, tmp_path, library):
    from PySide6.QtCore import QSettings
    from anim2d.editor.main_window import EditorWindow
    win = EditorWindow(arm, library, QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    win.state.select("attachment", "arm", "arm")
    win.viewport.frame_all()
    count = parse_vertices(_att(arm)).count
    pos = mesh_ops.vertex_world(arm, "arm", "arm")
    hull = _att(arm)["hull"]
    inner = [pos[i] for i in range(hull, count)]
    point = (sum(p[0] for p in inner) / len(inner) + 3, sum(p[1] for p in inner) / len(inner) + 2)
    index = win.viewport.add_vertex_at(win.viewport.to_screen(*point))
    assert index is not None and win.state.vertex_selection == {index}
    assert parse_vertices(_att(arm)).count == count + 1
    win.delete_selected()
    assert parse_vertices(_att(arm)).count == count and not win.state.vertex_selection
    arm.dirty = False
    win.close()


@needs_runtime
def test_topology_edits_match_runtime(tmp_path, arm):
    pos = mesh_ops.vertex_world(arm, "arm", "arm")
    hull = _att(arm)["hull"]
    mesh_ops.add_vertex(arm, "arm", "arm", ((pos[hull][0] + pos[0][0]) / 2, (pos[hull][1] + pos[0][1]) / 2))
    mesh_ops.add_vertex(arm, "arm", "arm", ((pos[2][0] + pos[3][0]) / 2, (pos[2][1] + pos[3][1]) / 2),
                        edge_tolerance=2)
    mesh_ops.remove_vertices(arm, "arm", "arm", {1})
    out = tmp_path / "arm.json"
    arm.export_spine_json(out)
    # Sample off the key times: the runtime stores times as float32, so "exactly on a key" is ambiguous.
    assert _compare_meshes(out, "wave", [0.0007, 0.2, 0.4007, 0.55]) == 4
