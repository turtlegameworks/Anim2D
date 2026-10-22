"""Creating, editing, keying and exporting transform and path constraints and path attachments."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anim2d.editor.constraints import path_lengths, smooth_path
from anim2d.editor.document import Document, EditorError, KeyRef
from anim2d.preview.evaluator import PoseEvaluator

VENDOR = Path(__file__).parent / "runtime" / "vendor" / "v40"
CORE40 = VENDOR / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


def run(path: Path, times, anim: str | None = None, skin: str | None = None) -> list[dict]:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(path), "--full"]
    if anim:
        args.append(f"--anim={anim}")
    if skin:
        args.append(f"--skin={skin}")
    out = subprocess.run(args + [str(t) for t in times], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)["samples"]


@pytest.fixture
def doc() -> Document:
    d = Document()
    d.add_bone("root", "arm", x=10, length=40)
    d.add_bone("arm", "hand", x=40, length=20)
    d.add_bone("root", "aim", x=-50, y=40, rotation=60)
    d.add_bone("root", "tail1", length=30)
    d.add_bone("tail1", "tail2", x=30, length=30)
    d.add_slot("root", "rail")
    d.add_animation("anim")
    return d


def world(d: Document, anim: str = "", t: float = 0):
    return PoseEvaluator(d.data, anim).bone_world(t)


def test_transform_constraint_create_edit_key(doc):
    before = world(doc)["hand"]
    name = doc.add_transform(["hand"], "aim", mixX=0.5, rotation=10)
    c = doc.transform_constraint(name)
    assert c == {"name": "hand-transform", "order": 0, "bones": ["hand"], "target": "aim", "rotation": 10, "mixX": 0.5}
    moved = world(doc)["hand"]
    assert abs(moved[4] - before[4]) > 1, "the constraint pulls the bone towards its target"
    doc.set_transform(name, mixX=1, local=True, mixRotate=0.25)
    assert "mixX" not in doc.transform_constraint(name) and doc.transform_constraint(name)["local"] is True
    with pytest.raises(EditorError, match="between 0 and 1"):
        doc.set_transform(name, mixY=2)
    with pytest.raises(EditorError, match="own bones|constrained bones"):
        doc.set_transform(name, target="hand")
    doc.set_transform_key("anim", name, 0, mixRotate=0)
    doc.set_transform_key("anim", name, 1, mixX=0.2)
    keys = doc.timeline_keys("anim", KeyRef("transform", name, "transform", 0))
    assert keys[0] == {"time": 0, "mixRotate": 0, "mixX": 1, "mixY": 1, "mixScaleX": 1, "mixScaleY": 1,
                       "mixShearY": 1}
    assert keys[1]["mixX"] == 0.2 and keys[1]["mixRotate"] == 0, "unkeyed mixes keep their posed value"
    row = KeyRef("transform", name, "transform", 0)
    assert any(r == row for r, _ in doc.timeline_rows("anim"))
    doc.set_key_channel("anim", KeyRef("transform", name, "transform", 1), 2, 0.6)  # graph: mix y
    assert doc.timeline_keys("anim", row)[1]["mixY"] == 0.6 and doc.timeline_keys("anim", row)[1]["mixX"] == 0.2
    assert doc.validate()[0] == []


def test_path_constraint_and_path_attachment(doc):
    att = doc.add_path_attachment("rail", [(0, 0), (60, 40), (120, 0)])
    path = doc.attachments("rail")[att]
    assert path["vertexCount"] == 9 and len(path["lengths"]) == 3
    assert 72.1 < path["lengths"][0] < 76 and path["lengths"][1] > path["lengths"][0]  # a bit over the chord
    assert doc.slot("rail")["attachment"] == att
    name = doc.add_path(["tail1", "tail2"], "rail", rotateMode="chain", position=0.1)
    assert doc.path_constraint(name)["order"] == 0 and doc.path_constraint(name)["rotateMode"] == "chain"
    w = world(doc)
    assert w["tail1"][4] == pytest.approx(path_point(doc, 0.1)[0], abs=3), "the first bone sits on the path"
    doc.set_path_key("anim", name, "position", 0, value=0)
    doc.set_path_key("anim", name, "position", 1, value=0.7)
    doc.set_path_key("anim", name, "mix", 0.5, mixRotate=0.3)
    assert world(doc, "anim", 1)["tail1"][4] > world(doc, "anim", 0)["tail1"][4] + 40
    rows = [(r.group, r.timeline) for r, _ in doc.timeline_rows("anim")]
    assert ("path", "position") in rows and ("path", "mix") in rows
    with pytest.raises(EditorError, match="one of"):
        doc.set_path(name, spacingMode="wide")
    # editing the path keeps the lengths right
    verts = list(path["vertices"])
    verts[8] += 30  # move the last point
    doc.set_path_vertices("rail", att, verts)
    assert doc.attachments("rail")[att]["lengths"] == path_lengths(verts, False)
    doc.set_path_options("rail", att, closed=True)
    assert doc.attachments("rail")[att]["closed"] is True and len(doc.attachments("rail")[att]["lengths"]) == 3
    assert doc.validate()[0] == []


def path_point(doc: Document, fraction: float) -> tuple[float, float]:
    """Rough point at a fraction of the rail (world = slot bone = root at the origin)."""
    import math
    verts = doc.attachments("rail")["path"]["vertices"]
    pts = [(verts[i], verts[i + 1]) for i in range(0, len(verts), 2)]
    samples = []
    for c in range(len(pts) // 3 - 1):
        p0, p1, p2, p3 = pts[c * 3 + 1], pts[c * 3 + 2], pts[c * 3 + 3], pts[c * 3 + 4]
        for i in range(200):
            t = i / 200
            u = 1 - t
            samples.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    lengths = [0.0]
    for a, b in zip(samples, samples[1:]):
        lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    target = lengths[-1] * fraction
    return samples[min(range(len(lengths)), key=lambda i: abs(lengths[i] - target))]


def test_order_rename_and_cascades(doc):
    ik, _ = doc.add_ik_with_target(["hand"])
    tc = doc.add_transform(["arm"], "aim")
    doc.add_path_attachment("rail", [(0, 0), (50, 0)])
    pc = doc.add_path(["tail1"], "rail")
    assert [doc.ik(ik)["order"], doc.transform_constraint(tc)["order"], doc.path_constraint(pc)["order"]] == [0, 1, 2]
    doc.move_constraint(pc, -2)
    assert [doc.path_constraint(pc)["order"], doc.ik(ik)["order"], doc.transform_constraint(tc)["order"]] == [0, 1, 2]
    doc.set_transform_key("anim", tc, 0)
    doc.rename_constraint(tc, "aiming")
    assert "aiming" in doc.data["animations"]["anim"]["transform"]
    doc.rename_bone("aim", "sight")
    assert doc.transform_constraint("aiming")["target"] == "sight"
    doc.rename_slot("rail", "track")
    assert doc.path_constraint(pc)["target"] == "track"
    doc.delete_bone("sight")  # the transform constraint goes with its target
    assert not doc.transforms and "transform" not in doc.data["animations"]["anim"]
    assert sorted(c["order"] for c in doc._ordered_constraints()) == [0, 1]
    doc.delete_slot("track")
    assert not doc.paths
    doc.undo()
    assert doc.paths and doc.path_constraint(pc)["target"] == "track"
    assert doc.validate()[0] == []


def test_smooth_path_handles():
    # [in handle, point, out handle] per point; handles are a sixth of the neighbour-to-neighbour span
    verts = smooth_path([(0, 0), (10, 0), (20, 0)])
    assert verts == pytest.approx([-10 / 6, 0, 0, 0, 10 / 6, 0, 10 - 20 / 6, 0, 10, 0, 10 + 20 / 6, 0,
                                   20 - 10 / 6, 0, 20, 0, 20 + 10 / 6, 0], abs=1e-4)
    closed = smooth_path([(0, 0), (10, 10), (20, 0)], closed=True)
    assert closed[0:2] == pytest.approx([0 - (10 - 20) / 6, 0 - (10 - 0) / 6], abs=1e-4)  # uses the last point


ROUND_TRIP = [("spineboy-pro", "aim", None), ("tank-pro", "drive", None), ("vine-pro", "grow", None),
              ("stretchyman-pro", "sneak", None), ("mix-and-match-pro", "dance", "full-skins/girl"),
              ("goblins-pro", "walk", "goblin")]


@needs_runtime
@pytest.mark.parametrize("name, anim, skin", ROUND_TRIP, ids=[r[0] for r in ROUND_TRIP])
def test_import_export_keeps_constraint_rigs(tmp_path, name, anim, skin):
    source = VENDOR / f"{name}.json"
    if not source.is_file():
        pytest.skip("run tools/fetch_spine_runtime.py")
    doc, _ = Document.import_spine_json(source)
    assert doc.validate()[0] == []
    times = [0.1003, 0.4003, 0.7003]
    expected = run(source, times, anim, skin)
    for suffix, binary in ((".json", False), (".skel", True)):
        out = tmp_path / f"{name}{suffix}"
        doc.export_spine_json(out, binary=binary, copy_images=False)
        got = run(out, times, anim, skin)
        for a, b in zip(expected, got, strict=True):
            for bone, values in a["bones"].items():
                assert b["bones"][bone] == pytest.approx(values, abs=0.02), (suffix, a["time"], bone)
            assert [v["slot"] for v in a["visible"]] == [v["slot"] for v in b["visible"]]
