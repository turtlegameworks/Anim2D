"""The 4.0 solver (IK, transform and path constraints, skins, skin bones, draw-order keys) against
the official spine-ts 4.0 runtime, on Esoteric's 4.0 example exports and on rigs that use the
constraint modes the examples do not (local and relative transform constraints, fixed and
length path modes, closed and non-constant-speed paths, weighted paths with deform keys)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anim2d.formats.binary40 import write_skel
from anim2d.preview.evaluator import MixEvaluator, PoseEvaluator

VENDOR = Path(__file__).parent / "runtime" / "vendor" / "v40"
CORE40 = VENDOR / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")
TIMES = [0.0003, 0.1337, 0.3003, 0.5337, 0.8003, 1.1337, 1.6003]


def run(path: Path, times, anim: str | None = None, skin: str | None = None, mix: str | None = None) -> dict:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(path), "--full"]
    if anim:
        args.append(f"--anim={anim}")
    if skin:
        args.append(f"--skin={skin}")
    if mix:
        args.append(f"--mix={mix}")
    out = subprocess.run(args + [str(t) for t in times], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def compare(ev: PoseEvaluator, samples: list[dict], tol: float = 0.05, time_of=lambda t: t) -> float:
    worst = 0.0
    for sample in samples:
        t = time_of(sample["time"])
        world = ev.bone_world(t)
        for bone, values in sample["bones"].items():
            if bone not in ev.active_bones:
                continue
            mine = world[bone]
            worst = max(worst, abs(mine[4] - values[0]), abs(mine[5] - values[1]))
            # matrix: runtime (a, b, c, d) = this module's (a, c, b, d)
            worst = max(worst, *(abs(m - v) * 100 for m, v in zip((mine[0], mine[2], mine[1], mine[3]), values[2:6])))
        items = ev.draw_items(t)
        mine = {i.slot: i for i in items}
        runtime_order = [v["slot"] for v in sample["visible"] if v["slot"] in mine]
        assert [i.slot for i in items if i.slot in set(runtime_order)] == runtime_order, ("draw order", t)
        for entry in sample["visible"]:
            if entry["slot"] in mine:
                assert mine[entry["slot"]].color[3] == pytest.approx(entry["alpha"], abs=0.01), (t, entry["slot"])
            if "mesh" in entry:  # the runtime reports the attachment's name; skins key it differently
                verts = ev.mesh_world_vertices(entry["slot"], mine[entry["slot"]].attachment, t)
                worst = max(worst, max(max(abs(x - entry["mesh"][2 * i]), abs(y - entry["mesh"][2 * i + 1]))
                                       for i, (x, y) in enumerate(verts)))
    assert worst < tol, worst
    return worst


CASES = [
    ("spineboy-pro", ["aim", "run", "shoot", "walk", "hoverboard", "portal"], None),
    ("tank-pro", ["drive", "shoot"], None),
    ("vine-pro", ["grow"], None),
    ("stretchyman-pro", ["sneak"], None),
    ("goblins-pro", ["walk"], "goblin"),
    ("goblins-pro", ["walk"], "goblingirl"),
    ("mix-and-match-pro", ["walk", "dance", "dress-up"], "full-skins/girl"),
    ("mix-and-match-pro", ["idle", "aware"], "full-skins/boy"),
]


@needs_runtime
@pytest.mark.parametrize("name, anims, skin", CASES, ids=[f"{c[0]}-{c[2] or 'default'}" for c in CASES])
def test_solver_matches_official_40_runtime(name, anims, skin):
    path = VENDOR / f"{name}.json"
    if not path.is_file():
        pytest.skip("run tools/fetch_spine_runtime.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    for anim in anims:
        ev = PoseEvaluator(data, anim, skin)
        assert not [x for x in ev.limitations if "constraint" in x]
        compare(ev, run(path, TIMES, anim, skin)["samples"])


@needs_runtime
def test_skin_bones_are_inactive_without_their_skin():
    path = VENDOR / "mix-and-match-pro.json"
    if not path.is_file():
        pytest.skip("run tools/fetch_spine_runtime.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    skin_bones = {b["name"] for b in data["bones"] if b.get("skin")}
    plain = PoseEvaluator(data, "idle")
    assert not (plain.active_bones & skin_bones)
    dressed = PoseEvaluator(data, "idle", "full-skins/girl")
    assert dressed.active_bones & skin_bones
    compare(plain, run(path, TIMES[:3], "idle")["samples"])


@needs_runtime
def test_mixing_with_constraints():
    path = VENDOR / "spineboy-pro.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ev = MixEvaluator(data, "walk", "aim", 0.3, 0.4)
    samples = run(path, [0.2, 0.3003, 0.4003, 0.5003, 0.7003, 0.9], mix="walk:aim:0.3:0.4")["samples"]
    compare(ev, samples)


def _rig(transform: dict, path: dict, path_att: dict, anim: dict) -> dict:
    """root > a > b chain, a target bone, three path bones on a path slot."""
    return {
        "skeleton": {"spine": "4.0.64"},
        "bones": [{"name": "root"},
                  {"name": "a", "parent": "root", "x": 20, "y": 10, "rotation": 30, "length": 40},
                  {"name": "b", "parent": "a", "x": 40, "rotation": -20, "length": 30, "scaleX": 1.2},
                  {"name": "target", "parent": "root", "x": -40, "y": 60, "rotation": 75, "scaleX": 1.5,
                   "scaleY": 0.8, "shearY": 10},
                  {"name": "pathbone", "parent": "root", "x": 10},
                  {"name": "p1", "parent": "root", "length": 30},
                  {"name": "p2", "parent": "p1", "x": 30, "length": 25},
                  {"name": "p3", "parent": "p2", "x": 25, "length": 20}],
        "slots": [{"name": "path", "bone": "pathbone", "attachment": "curve"}],
        "transform": [{"name": "tc", "order": 0, "bones": ["b"], "target": "target", **transform}],
        "path": [{"name": "pc", "order": 1, "bones": ["p1", "p2", "p3"], "target": "path", **path}],
        "skins": [{"name": "default", "attachments": {"path": {"curve": path_att}}}],
        "animations": {"a": anim},
    }


OPEN_PATH = {"type": "path", "vertexCount": 9, "lengths": [70, 150, 210],
             "vertices": [-20, 0, 0, 0, 20, 40, 40, 60, 60, 80, 80, 60, 100, 40, 110, 0, 120, -20]}
CLOSED_PATH = {"type": "path", "closed": True, "vertexCount": 9, "lengths": [80, 160, 240],
               "vertices": [-20, -20, 0, 0, 20, 30, 60, 60, 80, 70, 100, 40, 100, -10, 70, -40, 20, -40]}
WEIGHTED_PATH = {"type": "path", "vertexCount": 6, "lengths": [90, 180],
                 "vertices": [1, 4, -10, 0, 1, 1, 4, 0, 0, 1, 2, 4, 20, 30, 0.5, 3, 20, 30, 0.5,
                              1, 3, 40, 40, 1, 1, 3, 60, 40, 1, 1, 3, 80, 0, 1]}
ANIM = {
    "bones": {"target": {"rotate": [{"value": 0}, {"time": 1, "value": 120}],
                         "translate": [{"x": 0}, {"time": 1, "x": 30, "y": -20}],
                         "scale": [{}, {"time": 1, "x": 0.6, "y": 1.4}]},
              "a": {"rotate": [{"value": 0}, {"time": 1, "value": -60}]},
              "pathbone": {"rotate": [{"value": 0}, {"time": 1, "value": 25}]}},
    "transform": {"tc": [{"mixRotate": 1, "mixX": 0.5, "mixScaleX": 0.3, "mixShearY": 0.6},
                         {"time": 1, "mixRotate": 0.2, "mixX": 1, "mixY": 0.4, "mixScaleX": 1, "mixScaleY": 0.7}]},
    "path": {"pc": {"position": [{"value": 0.05}, {"time": 1, "value": 0.6}],
                    "mix": [{"mixRotate": 1}, {"time": 1, "mixRotate": 0.5, "mixX": 0.7}]}},
}
SYNTHETIC = {
    "absolute-world": ({}, {"spacingMode": "length"}, OPEN_PATH),
    "relative-world": ({"relative": True, "rotation": 17,  # 15 lands exactly on +-180 at t=0.75
                       "x": 5, "y": -3, "scaleX": 0.2, "shearY": 5},
                       {"spacingMode": "fixed", "positionMode": "fixed", "position": 20, "spacing": 25,
                        "rotateMode": "chain", "rotation": 10}, OPEN_PATH),
    "absolute-local": ({"local": True, "rotation": -30, "x": 10, "scaleX": 0.1, "scaleY": -0.1, "shearY": 8},
                       {"spacingMode": "percent", "spacing": 0.2, "rotateMode": "chainScale"},
                       dict(OPEN_PATH, constantSpeed=False)),
    "relative-local": ({"local": True, "relative": True, "rotation": 20, "y": 8, "scaleY": 0.3},
                       {"spacingMode": "proportional", "spacing": 0.5, "rotateMode": "tangent"}, CLOSED_PATH),
    "weighted-closed-nonconstant": ({"mixShearY": 0}, {"spacingMode": "length", "spacing": 5},
                                    dict(CLOSED_PATH, constantSpeed=False)),
    "weighted-path": ({}, {"spacingMode": "proportional", "spacing": 1, "rotateMode": "chainScale"},
                      WEIGHTED_PATH),
}


@needs_runtime
@pytest.mark.parametrize("case", list(SYNTHETIC))
def test_constraint_modes_match_runtime(tmp_path, case):
    transform, path, att = SYNTHETIC[case]
    anim = json.loads(json.dumps(ANIM))
    spacing = path.get("spacing", 0)  # keyed from the setup value to 1.5 times it (+5)
    anim["path"]["pc"]["spacing"] = [{"value": spacing}, {"time": 1, "value": spacing * 1.5 + 5}]
    if att is WEIGHTED_PATH:  # deform keys on a weighted path
        anim["deform"] = {"default": {"path": {"curve": [
            {"time": 0}, {"time": 1, "offset": 2, "vertices": [10, -10, 0, 0, 0, 0, 5, 15]}]}}}
    data = _rig(transform, path, att, anim)
    # Loaded as binary: spine-ts 4.0.31's JSON reader keeps the first key's mixShearY for every
    # later transform key (it assigns mixScaleX twice). Its binary reader and the Java/C# readers
    # do not have this bug, and Anim2D follows them.
    file = tmp_path / f"{case}.skel"
    file.write_bytes(write_skel(data))
    ev = PoseEvaluator(data, "a")
    compare(ev, run(file, [0, 0.25, 0.5, 0.75, 1.0], "a")["samples"])
    # the constraints really moved something
    still = PoseEvaluator({**data, "transform": [], "path": []}, "a").bone_world(0.5)
    moved = ev.bone_world(0.5)
    for bone in ("b", "p2", "p3"):
        assert abs(moved[bone][4] - still[bone][4]) + abs(moved[bone][5] - still[bone][5]) > 1, (case, bone)
