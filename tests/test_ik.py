"""IK constraints in Anim2D: document operations, and poses checked against the official 3.8 runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime_cores import core_for

from anim2d.editor.document import Document, EditorError, KeyRef
from anim2d.preview.evaluator import PoseEvaluator

VENDOR = Path(__file__).parent / "runtime" / "vendor"
SPINE_CORE = VENDOR / "spine-core-3.8.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


def leg_rig() -> Document:
    """root > hip > thigh > shin > foot (tip marker), plus a target under root."""
    doc = Document()
    doc.add_bone("root", "hip", y=200, length=20)
    doc.add_bone("hip", "thigh", rotation=-80, length=100)
    doc.add_bone("thigh", "shin", x=100, rotation=-15, length=110)
    doc.add_bone("shin", "foot", x=110)
    doc.add_bone("root", "foot-target", x=60, y=40)
    return doc


def test_add_ik_validation_and_order():
    doc = leg_rig()
    with pytest.raises(EditorError, match="direct child"):
        doc.add_ik(["thigh", "foot"], "foot-target")
    with pytest.raises(EditorError, match="target cannot"):
        doc.add_ik(["thigh", "shin"], "foot")
    with pytest.raises(EditorError, match="root bone"):
        doc.add_ik(["root"], "foot-target")
    leg = doc.add_ik(["thigh", "shin"], "foot-target", bend_positive=False)
    assert doc.ik(leg) == {"name": "shin-ik", "order": 0, "bones": ["thigh", "shin"], "target": "foot-target",
                           "bendPositive": False}
    with pytest.raises(EditorError, match="already constrained"):
        doc.add_ik(["shin"], "foot-target")
    doc.add_bone("root", "look", x=50, y=300)
    head = doc.add_ik(["hip"], "look", "hip-ik")
    assert doc.ik(head)["order"] == 1
    doc.delete_ik(leg)
    assert doc.ik(head)["order"] == 0, "orders stay contiguous"
    assert doc.validate()[0] == []


def test_ik_cascades_and_undo():
    doc = leg_rig()
    name, target = doc.add_ik_with_target(["thigh", "shin"])
    assert target == "shin-target" and doc.has_bone(target)
    # The new target sits at the shin tip, so the solved pose matches the unconstrained one.
    world = PoseEvaluator(doc.data, "").bone_world(0)
    assert (world["foot"][4], world["foot"][5]) == pytest.approx((world[target][4], world[target][5]), abs=1e-3)
    doc.undo()
    assert not doc.has_bone(target) and not doc.iks, "one undo removes target and constraint"
    doc.redo()
    doc.add_animation("walk")
    doc.set_ik_key("walk", name, 0.2, mix=0.5)
    doc.rename_bone("shin", "calf")
    doc.rename_ik(name, "leg")
    assert doc.ik("leg")["bones"] == ["thigh", "calf"] and "leg" in doc.animation("walk")["ik"]
    with pytest.raises(EditorError, match="child bone of IK"):
        doc.reparent_bone("calf", "root")
    doc.delete_bone("thigh")
    assert not doc.iks and "ik" not in doc.data and "ik" not in doc.animation("walk")


def test_ik_keys():
    doc = leg_rig()
    name = doc.add_ik(["thigh", "shin"], "foot-target", mix=0.8)
    doc.add_animation("a")
    doc.set_ik_key("a", name, 0, mix=0)  # partial: other fields from the constraint
    doc.set_ik_key("a", name, 0.5, bendPositive=False)  # mix taken from the posed value at 0.5
    keys = doc.animation("a")["ik"][name]
    assert keys == [{"time": 0, "mix": 0}, {"time": 0.5, "mix": 0, "bendPositive": False}]
    doc.set_ik_key("a", name, 0.5, mix=1)  # updating a key keeps its other fields
    assert keys[1] == {"time": 0.5, "mix": 1, "bendPositive": False}
    doc.set_curve("a", [KeyRef("ik", name, "ik", 0)], "stepped")
    assert keys[0]["curve"] == "stepped"
    rows = doc.timeline_rows("a")
    assert [(r.group, r.owner) for r, _ in rows] == [("ik", name)]
    doc.move_keys("a", [KeyRef("ik", name, "ik", 0.5)], 0.25)
    assert [k["time"] for k in doc.animation("a")["ik"][name]] == [0, 0.75]
    state = PoseEvaluator(doc.data, "a").ik_state(0.75)[name]
    assert state["bend"] == -1 and state["mix"] == 1
    with pytest.raises(EditorError, match="between 0 and 1"):
        doc.set_ik_key("a", name, 1, mix=2)


def _runtime(json_path: Path, anim: str, times) -> dict:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(core_for(json_path)), str(json_path),
            f"--anim={anim}", *map(str, times)]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=60, check=True).stdout)


def _compare(tmp_path: Path, doc: Document, anim: str, times, tol=0.05) -> None:
    out = tmp_path / f"{anim}.json"
    doc.export_spine_json(out, overwrite=True, copy_images=False)
    data = json.loads(out.read_text())
    run = _runtime(out, anim, times)
    mine = PoseEvaluator(data, anim)
    for sample in run["samples"]:
        world = mine.bone_world(sample["time"])
        for bone, (x, y) in sample["bones"].items():
            assert (world[bone][4], world[bone][5]) == pytest.approx((x, y), abs=tol), (anim, sample["time"], bone)


@needs_runtime
def test_two_bone_ik_matches_runtime(tmp_path):
    doc = leg_rig()
    name = doc.add_ik(["thigh", "shin"], "foot-target")
    doc.add_animation("step")
    for t, x, y in ((0, 0, 0), (0.3, 80, 60), (0.6, 400, 40), (0.9, -50, 150), (1.2, 0, -120)):
        doc.set_bone_key("step", "foot-target", "translate", t, x=x, y=y)  # includes out-of-reach poses
    doc.set_ik_key("step", name, 0, mix=1)
    doc.set_ik_key("step", name, 0.45, mix=0.4, bendPositive=False)
    doc.set_ik_key("step", name, 0.8, mix=1, bendPositive=False)
    doc.set_curve("step", [KeyRef("ik", name, "ik", 0)], (0.25, 0.1, 0.25, 1))
    doc.set_bone_key("step", "hip", "rotate", 0, angle=170)
    doc.set_bone_key("step", "hip", "rotate", 1.2, angle=-170)  # shortest path crosses 180
    _compare(tmp_path, doc, "step", [0.0007, 0.15, 0.31, 0.5, 0.62, 0.85, 1.0, 1.2])


@needs_runtime
@pytest.mark.parametrize("options", [
    {"softness": 25},
    {"stretch": True},
    {"softness": 15, "stretch": True, "bendPositive": False},
])
def test_two_bone_ik_options_match_runtime(tmp_path, options):
    doc = leg_rig()
    name = doc.add_ik(["thigh", "shin"], "foot-target")
    doc.set_ik(name, **options)
    doc.add_animation("reach")
    doc.set_bone_key("reach", "foot-target", "translate", 0, x=0, y=0)
    doc.set_bone_key("reach", "foot-target", "translate", 1, x=300, y=-100)
    _compare(tmp_path, doc, "reach", [0.0007, 0.25, 0.5, 0.75, 1])


@needs_runtime
@pytest.mark.parametrize("options", [{}, {"stretch": True}, {"compress": True, "uniform": True}])
def test_one_bone_ik_matches_runtime(tmp_path, options):
    doc = leg_rig()
    name = doc.add_ik(["shin"], "foot-target")
    doc.set_ik(name, **options)
    doc.add_animation("aim")
    doc.set_bone_key("aim", "foot-target", "translate", 0, x=0, y=0)
    doc.set_bone_key("aim", "foot-target", "translate", 1, x=200, y=200)
    doc.set_ik_key("aim", name, 0.5, mix=0.5)
    _compare(tmp_path, doc, "aim", [0.0007, 0.3, 0.5, 0.8, 1])


@needs_runtime
def test_ik_with_scaled_and_mirrored_parents_matches_runtime(tmp_path):
    """Non-uniform and negative parent scale take the runtime's other solver branches."""
    doc = leg_rig()
    doc.set_bone("thigh", scaleX=1.3, scaleY=0.8)
    doc.set_bone("hip", scaleX=-1)
    doc.set_bone("shin", shearX=10)
    name = doc.add_ik(["thigh", "shin"], "foot-target", bend_positive=False)
    doc.add_animation("odd")
    doc.set_bone_key("odd", "foot-target", "translate", 0, x=-150, y=-20)
    doc.set_bone_key("odd", "foot-target", "translate", 1, x=-40, y=120)
    doc.set_bone_key("odd", "shin", "shear", 0.5, x=-20, y=5)
    doc.set_ik_key("odd", name, 0.6, mix=0.7)
    _compare(tmp_path, doc, "odd", [0.0007, 0.2, 0.5, 0.7, 1])


@needs_runtime
def test_chained_constraints_match_runtime(tmp_path):
    """Two constraints where the second target is driven by the first chain (update order matters)."""
    doc = leg_rig()
    doc.add_bone("foot", "toe", x=0, rotation=-90, length=30)
    doc.add_bone("toe", "toe-tip", x=30)
    doc.add_bone("shin", "toe-target-holder", x=110, y=20)
    leg = doc.add_ik(["thigh", "shin"], "foot-target")
    doc.add_ik(["toe"], "toe-target-holder", "toe-ik")
    doc.add_animation("chain")
    doc.set_bone_key("chain", "foot-target", "translate", 0, x=0, y=0)
    doc.set_bone_key("chain", "foot-target", "translate", 1, x=120, y=90)
    doc.set_ik_key("chain", leg, 0.5, mix=0.6)
    _compare(tmp_path, doc, "chain", [0.0007, 0.4, 0.5, 0.9])


RAPTOR = VENDOR / "raptor" / "raptor-pro.json"


@needs_runtime
@pytest.mark.skipif(not RAPTOR.is_file(), reason="needs tools/fetch_spine_runtime.py")
@pytest.mark.parametrize("anim", ["walk", "roar", "jump", "gun-grab"])
def test_real_rig_raptor_matches_runtime(tmp_path, anim):
    """Esoteric's raptor-pro (9 IK constraints, IK keys, meshes) imported, exported and solved."""
    doc, warnings = Document.import_spine_json(RAPTOR)  # images are not fetched; only bones are compared
    assert doc.images_dir is None
    assert len(doc.iks) == 9
    assert any(att.get("type") == "mesh" for slot in doc.skin.values() for att in slot.values())
    assert doc.validate()[0] == []
    duration = doc.animation_duration(anim)
    times = [round(duration * f, 4) + 0.0007 for f in (0.0, 0.13, 0.37, 0.5, 0.71, 0.93)]
    _compare(tmp_path, doc, anim, times, tol=0.05)


def test_ik_export_validates_and_project_round_trips(tmp_path):
    doc = leg_rig()
    doc.add_ik(["thigh", "shin"], "foot-target", softness=10)
    doc.save(tmp_path / "leg.rig2d")
    again = Document.load(tmp_path / "leg.rig2d")
    assert again.iks == doc.iks
    broken = Document(json.loads(json.dumps(doc.data)))
    broken.data["ik"][0]["order"] = 3
    assert any("orders" in e for e in broken.validate()[0])
