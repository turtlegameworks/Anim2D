from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime_cores import core_for

from anim2d.models.session import SettingsSession, suggest_instance_name
from anim2d.packaging.package import build_document, write_package
from anim2d.preview.evaluator import PoseEvaluator

RUNTIME_DIR = Path(__file__).parent / "runtime"
SPINE_CORE = RUNTIME_DIR / "vendor" / "spine-core-3.8.js"
NODE = shutil.which("node")


def test_undo_redo_and_merge(library):
    s = SettingsSession(library.get("impact_basic"), "fx_impact_01")
    s.set("ray_count", 10)
    for v in (11, 12, 13):  # one slider drag
        s.set("scale", v / 10, merge=True)
    s.end_merge()
    s.set("scale", 2.0, merge=True)  # new drag = new step
    assert s.get("scale") == 2.0
    assert s.undo() and s.get("scale") == 1.3
    assert s.undo() and s.get("scale") == 1.0 and s.get("ray_count") == 10
    assert s.undo() and s.get("ray_count") == 8
    assert not s.undo()
    assert s.redo() and s.get("ray_count") == 10
    s.set("opacity", 0.5)
    assert not s.can_redo, "a new edit clears redo"


def test_reset_is_undoable(library):
    s = SettingsSession(library.get("impact_basic"), "fx_impact_01")
    s.set("ray_count", 5)
    s.set("primary_color", "00ff00ff")
    s.reset()
    assert s.values == library.get("impact_basic").defaults()
    s.undo()
    assert s.get("ray_count") == 5 and s.get("primary_color") == "00ff00ff"
    s.reset("ray_count")
    assert s.get("ray_count") == 8 and s.get("primary_color") == "00ff00ff"


def test_sessions_do_not_share_state(library):
    a = SettingsSession(library.get("impact_basic"), "fx_a_01")
    b = SettingsSession(library.get("impact_basic"), "fx_b_01")
    a.set("ray_count", 16)
    assert b.get("ray_count") == 8
    assert library.get("impact_basic").defaults()["ray_count"] == 8


def test_variant_load_is_atomic(tmp_path, library):
    s = SettingsSession(library.get("impact_basic"), "fx_impact_01")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"variant_schema_version": 1, "preset_id": "impact_basic", "preset_version": 1,
                               "settings": {"ray_count": 999}}))
    with pytest.raises(ValueError, match="maximum"):
        s.load_variant(bad)
    assert s.get("ray_count") == 8 and not s.can_undo


def test_suggest_instance_name_skips_existing(tmp_path, library):
    preset = library.get("impact_basic")
    assert suggest_instance_name(preset, tmp_path) == "fx_impact_01"
    (tmp_path / "fx_impact_01").mkdir()
    (tmp_path / "fx_impact_02").mkdir()
    assert suggest_instance_name(preset, tmp_path) == "fx_impact_03"


def test_preview_follows_settings_and_visibility(library, make_settings):
    preset = library.get("impact_basic")
    _, data = build_document(preset, make_settings("impact_basic", delay=0.2, ray_count=5))
    ev = PoseEvaluator(data)
    assert ev.duration == pytest.approx(0.55)
    assert ev.draw_items(0.1) == []  # hidden during delay
    mid = ev.draw_items(0.3)
    assert len([i for i in mid if "_ray_" in i.slot]) == 5
    assert ev.draw_items(0.56) == []  # hidden after end


@pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(), reason="needs node and tools/fetch_spine_runtime.py")
@pytest.mark.parametrize("values", [
    {},
    {"rotation": 35, "scale": 1.7, "mirror": True, "position_x": 40, "position_y": -25, "delay": 0.1, "seed": 5},
    {"spread": 90, "ray_count": 5, "stagger": 0.5, "travel_distance": 250},
])
def test_preview_matches_official_runtime(tmp_path, library, make_settings, values):
    """The approximate preview must agree with spine-ts 3.8 on positions, visibility and alpha."""
    preset = library.get("impact_basic")
    settings = make_settings("impact_basic", "fx_cmp_01", **values)
    result = write_package(preset, settings, tmp_path)
    data = json.loads((result.output_dir / "fx_cmp_01.json").read_text())
    ev = PoseEvaluator(data)
    # Avoid sampling exactly on a key: the runtime stores times as float32, so "on the key"
    # can land on either side of it.
    key_times = {k.get("time", 0) for anim in data["animations"].values() for group in anim.values()
                 for tls in group.values() for keys in tls.values() for k in keys}
    times = []
    for f in (0.05, 0.3, 0.5, 0.7, 0.9):
        t = round(ev.duration * f, 4) + 0.0007
        while any(abs(t - k) < 2e-4 for k in key_times):
            t = round(t + 0.0005, 4)
        times.append(t)
    out = subprocess.run([NODE, str(RUNTIME_DIR / "check_spine38.js"), str(core_for(result.output_dir / "fx_cmp_01.json")),
                          str(result.output_dir / "fx_cmp_01.json"), *map(str, times)],
                         capture_output=True, text=True, timeout=60, check=True)
    runtime = {s["time"]: s["visible"] for s in json.loads(out.stdout)["samples"]}
    slot_bone = {s["name"]: s["bone"] for s in data["slots"]}
    for t in times:
        world = ev.bone_world(t)
        mine = {i.slot: i for i in ev.draw_items(t)}
        theirs = {v["slot"]: v for v in runtime[t]}
        assert set(mine) == set(theirs), f"visible slots differ at {t}s"
        for name, rt in theirs.items():
            _, _, _, _, x, y = world[slot_bone[name]]
            assert x == pytest.approx(rt["x"], abs=1.0) and y == pytest.approx(rt["y"], abs=1.0), (name, t)
            assert mine[name].color[3] == pytest.approx(rt["alpha"], abs=0.03), (name, t)
