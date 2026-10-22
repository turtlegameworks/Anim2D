"""Auto-rig helpers: bone chains and rigging a mesh to the bones over it."""

from __future__ import annotations

import pytest
from PIL import Image

from anim2d.editor import affine, mesh_ops
from anim2d.editor.autorig import bone_chain, chain_from_bone, path_points, resample, rig_mesh
from anim2d.editor.document import Document


def _tips(doc, names):
    world = doc.evaluator("").bone_world(0)
    return [affine.apply(world[n], doc.bone(n)["length"], 0) for n in names]


def test_resample_even():
    pts = resample([(0, 0), (10, 0), (10, 10)], 4)
    assert pts == [(0, 0), (5, 0), (10, 0), (10, 5), (10, 10)]


def test_chain_from_bone_continues_from_tip():
    doc = Document()
    doc.add_bone("root", "tail", x=10, y=5, rotation=90, length=20)
    names = chain_from_bone(doc, "tail", 4, 100)
    assert len(names) == 4
    end = _tips(doc, names)[-1]
    assert end == pytest.approx((10, 125), abs=1e-3)
    doc.undo()
    assert not doc.has_bone(names[0])


def test_chain_along_path():
    doc = Document()
    doc.add_slot("root", "curve")
    # a straight open path from (0,0) to (90,0): groups of (in, point, out)
    doc.skin["curve"]["p"] = {"type": "path", "vertexCount": 6,
                              "vertices": [-10, 0, 0, 0, 30, 0, 60, 0, 90, 0, 100, 0], "lengths": [90]}
    names = bone_chain(doc, "root", path_points(doc, "curve", "p"), 3, "hair")
    assert [doc.bone(n)["length"] for n in names] == pytest.approx([30, 30, 30], abs=1e-2)
    assert _tips(doc, names)[-1] == pytest.approx((90, 0), abs=1e-3)


def test_rig_mesh_picks_overlapping_bones(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (100, 20), (255, 255, 255, 255)).save(images / "arm.png")
    doc = Document(images_dir=images)
    doc.add_bone("root", "upper", x=-50, length=50)
    doc.add_bone("upper", "lower", x=50, length=50)
    doc.add_bone("root", "far", x=500, y=500, length=10)
    doc.add_slot("root", "arm", image="arm")
    mesh_ops.convert_to_mesh(doc, "arm", "arm", 4, 2)
    bones = rig_mesh(doc, "arm", "arm")
    assert set(bones) == {"upper", "lower"}
    used = {b for vw in mesh_ops.vertex_bone_weights(doc, "arm", "arm") for b, _ in vw}
    assert used == {"upper", "lower"}
