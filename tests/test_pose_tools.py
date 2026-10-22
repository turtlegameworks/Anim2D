"""Onion skin, pose clipboard, mirroring, bake/simplify and Preview layers."""

from __future__ import annotations

import pytest

from anim2d.editor.document import Document
from anim2d.editor.mirror import mirror_bones, mirror_name
from anim2d.editor import bake
from anim2d.preview.evaluator import layered_animation


def test_mirror_names():
    assert mirror_name("L_Arm") == "R_Arm"
    assert mirror_name("arm.r") == "arm.l"
    assert mirror_name("LeftHand") == "RightHand"
    assert mirror_name("Leg") is None, "a capital L inside a word is not a side"


def _arm_doc() -> Document:
    d = Document()
    d.add_bone("root", "L_arm", x=20, y=0, rotation=30, length=40)
    d.add_bone("L_arm", "L_hand", x=40, length=10)
    return d


def test_mirror_bones_reflects_world():
    d = _arm_doc()
    names = mirror_bones(d, "L_arm")
    assert names == {"L_arm": "R_arm", "L_hand": "R_hand"}
    world = d.evaluator("").bone_world(0)
    for a, b in names.items():
        assert world[b][4] == pytest.approx(-world[a][4], abs=1e-3)
        assert world[b][5] == pytest.approx(world[a][5], abs=1e-3)
    d.undo()
    assert not d.has_bone("R_arm")


def test_simplify_removes_linear_keys():
    keys = [{"value": 0}, {"time": 0.5, "value": 5}, {"time": 1, "value": 10}, {"time": 2, "value": 10.05}]
    removed = bake.simplify_keys(keys, "rotate")
    assert removed == 1 and [k.get("time", 0) for k in keys] == [0, 1, 2]


def test_layered_animation_overrides_per_bone():
    base = {"bones": {"a": {"rotate": [{"value": 10}]}, "b": {"rotate": [{"value": 20}]}}}
    layer = {"bones": {"b": {"rotate": [{"value": 99}]}}}
    out = layered_animation(base, layer, loop=False)
    assert out["bones"]["a"]["rotate"][0]["value"] == 10
    assert out["bones"]["b"]["rotate"][0]["value"] == 99


def test_onion_ghosts_only_selection(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from anim2d.editor.state import EditorState
    from anim2d.editor.viewport import Viewport
    (tmp_path / "img").mkdir()
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(tmp_path / "img" / "box.png")
    d = Document(images_dir=tmp_path / "img")
    d.add_bone("root", "arm")
    d.add_bone("arm", "hand")
    d.add_bone("root", "leg")
    for bone in ("arm", "hand", "leg"):
        d.add_slot(bone, f"{bone}_slot", image="box")
    st = EditorState(d)
    vp = Viewport(st)
    assert vp.onion_slots() is None, "nothing selected: every slot"
    st.select("bone", "arm")
    assert vp.onion_slots() == {"arm_slot", "hand_slot"}, "a bone: its slots and its children's"
    st.select("slot", "leg_slot")
    assert vp.onion_slots() == {"leg_slot"}
