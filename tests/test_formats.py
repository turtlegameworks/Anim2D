"""3.8 <-> 4.0 conversion, checked by playing both sides in the official runtimes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anim2d.formats import spine40
from anim2d.formats.curves import bezier_value, to_absolute
from anim2d.preview.evaluator import _bezier

VENDOR = Path(__file__).parent / "runtime" / "vendor"
CORE38 = VENDOR / "spine-core-3.8.js"
CORE40 = VENDOR / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtimes = pytest.mark.skipif(not NODE or not CORE38.is_file() or not CORE40.is_file(),
                                    reason="needs node and tools/fetch_spine_runtime.py")


def run(core: Path, path: Path, anim: str, times) -> list[dict]:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(core), str(path),
            f"--anim={anim}", *map(str, times)]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=180, check=True).stdout)["samples"]


def compare(a: list[dict], b: list[dict], tol: float) -> float:
    worst = 0.0
    for sa, sb in zip(a, b):
        for bone, (x, y) in sa["bones"].items():
            bx, by = sb["bones"][bone]
            worst = max(worst, abs(x - bx), abs(y - by))
        va = {v["slot"]: v for v in sa["visible"]}
        vb = {v["slot"]: v for v in sb["visible"]}
        assert set(va) == set(vb), (sa["time"], set(va) ^ set(vb))
        for slot, entry in va.items():
            assert entry["alpha"] == pytest.approx(vb[slot]["alpha"], abs=0.02), (sa["time"], slot)
            if "mesh" in entry:
                worst = max(worst, max(abs(p - q) for p, q in zip(entry["mesh"], vb[slot]["mesh"])))
    assert worst <= tol, worst
    return worst


def _times(duration: float, fps: float = 30) -> list[float]:
    """Frame-aligned sample times (baked keys are exact there), nudged off the key times."""
    frames = max(1, round(duration * fps))
    picks = sorted({0, frames // 5, frames // 3, frames // 2, (2 * frames) // 3, frames - 1})
    return [round(f / fps + 0.0003, 4) for f in picks if f >= 0]


def test_bezier_matches_38_sampling_after_conversion():
    """4.0 absolute curves give the same values as the 3.8 normalized ones."""
    norm = (0.25, 0.1, 0.4, 1.0)
    t0, v0, t1, v1 = 0.3, 10.0, 1.1, -40.0
    handles = tuple(to_absolute(norm, t0, v0, t1, v1))
    for f in (0.05, 0.3, 0.5, 0.77, 0.95):
        t = t0 + (t1 - t0) * f
        assert bezier_value(t, t0, v0, handles, t1, v1) == pytest.approx(v0 + _bezier(f, *norm) * (v1 - v0), abs=1e-6)


@needs_runtimes
@pytest.mark.parametrize("fixture, anims", [
    (VENDOR / "spineboy" / "spineboy-ess.json", ["run", "jump", "shoot", "death"]),
    (VENDOR / "raptor" / "raptor-pro.json", ["walk", "roar", "jump"]),
])
def test_upgrade_38_to_40_poses_identically(tmp_path, fixture, anims):
    if not fixture.is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    data = json.loads(fixture.read_text())
    upgraded = spine40.upgrade(data)
    out = tmp_path / "up.json"
    out.write_text(json.dumps(upgraded))
    times = [0.0003, 0.1003, 0.2337, 0.4003, 0.6337, 0.8003]
    for anim in anims:
        # The harness rounds positions to 0.01 px, so allow two rounding steps.
        compare(run(CORE38, fixture, anim, times), run(CORE40, out, anim, times), tol=0.02)


@needs_runtimes
@pytest.mark.parametrize("fixture, anims", [
    (VENDOR / "v40" / "raptor-pro.json", ["walk", "roar", "jump"]),
    (VENDOR / "v40" / "spineboy-ess.json", ["walk", "run", "jump"]),
])
def test_downgrade_40_to_38_poses_alike(tmp_path, fixture, anims):
    """4.0 data in the 4.0 runtime vs its 3.8 conversion in the 3.8 runtime (baked curves allow ~0.3 px)."""
    data = json.loads(fixture.read_text())
    downgraded, notes = spine40.downgrade(data)
    out = tmp_path / "down.json"
    out.write_text(json.dumps(downgraded))
    for anim in anims:
        times = [0.0003, 0.1003, 0.2337, 0.4003, 0.6337, 0.8003, 1.2003]
        compare(run(CORE40, fixture, anim, times), run(CORE38, out, anim, times), tol=0.35)


@needs_runtimes
def test_downgrade_of_constraint_rig_round_trips_in_40_runtime(tmp_path):
    """spineboy-pro's transform+IK arm rig behaves differently in the 3.8 and 4.0 runtimes even for identical
    data, so the converter is checked by 4.0 -> 3.8 -> 4.0 in the same 4.0 runtime."""
    fixture = VENDOR / "v40" / "spineboy-pro.json"
    data = json.loads(fixture.read_text())
    back = spine40.upgrade(spine40.downgrade(data)[0], stamp="4.0.31")
    out = tmp_path / "rt.json"
    out.write_text(json.dumps(back))
    times = [0.0003, 0.1003, 0.2337, 0.4003, 0.6337, 0.8003, 1.2003]
    for anim in ("walk", "run", "jump", "shoot", "aim", "portal", "hoverboard"):
        compare(run(CORE40, fixture, anim, times), run(CORE40, out, anim, times), tol=0.35)


@needs_runtimes
@pytest.mark.parametrize("fixture, anims", [
    (VENDOR / "v40" / "spineboy-ess.json", ["walk", "run", "jump", "death"]),
    (VENDOR / "v40" / "raptor-pro.json", ["walk", "roar", "jump", "gun-grab"]),
])
def test_evaluator_matches_official_40_runtime(fixture, anims):
    """The editor's pose evaluator on native 4.0 exports (per-channel curves, IK, weights, deform)."""
    from anim2d.preview.evaluator import PoseEvaluator
    data = json.loads(fixture.read_text())
    times = [0.0003, 0.1003, 0.2337, 0.4003, 0.6337, 0.8003, 1.2003]
    for anim in anims:
        ev = PoseEvaluator(data, anim)
        for sample in run(CORE40, fixture, anim, times):
            world = ev.bone_world(sample["time"])
            for bone, (x, y) in sample["bones"].items():
                assert (world[bone][4], world[bone][5]) == pytest.approx((x, y), abs=0.02), (anim, sample["time"], bone)
            mine = {i.slot: i for i in ev.draw_items(sample["time"])}
            for entry in sample["visible"]:
                if "mesh" in entry:
                    verts = ev.mesh_world_vertices(entry["slot"], entry["attachment"], sample["time"])
                    assert max(max(abs(x - entry["mesh"][2 * i]), abs(y - entry["mesh"][2 * i + 1]))
                               for i, (x, y) in enumerate(verts)) < 0.05, (anim, entry["slot"])
                if entry["slot"] in mine:
                    assert mine[entry["slot"]].color[3] == pytest.approx(entry["alpha"], abs=0.01)


@needs_runtimes
def test_round_trip_38_40_38_is_exact_without_baking(tmp_path):
    fixture = VENDOR / "spineboy" / "spineboy-ess.json"
    data = json.loads(fixture.read_text())
    back, notes = spine40.downgrade(spine40.upgrade(data))
    assert not notes, notes
    out = tmp_path / "back.json"
    out.write_text(json.dumps(back))
    times = [0.0003, 0.2003, 0.5003]
    for anim in ("run", "shoot"):
        compare(run(CORE38, fixture, anim, times), run(CORE38, out, anim, times), tol=0.01)
