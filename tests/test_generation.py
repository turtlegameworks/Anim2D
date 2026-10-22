from __future__ import annotations

import copy
import json

import pytest

from anim2d.packaging.package import build_document
from anim2d.validation.spine38_validator import validate_output


def _all_times(data):
    for anim in data["animations"].values():
        for group in ("bones", "slots"):
            for timelines in anim.get(group, {}).values():
                for keys in timelines.values():
                    for k in keys:
                        yield k.get("time", 0)


def test_same_seed_reproduces_and_new_seed_differs(library, make_settings):
    preset = library.get("impact_basic")
    _, a = build_document(preset, make_settings("impact_basic", seed=42))
    _, b = build_document(preset, make_settings("impact_basic", seed=42))
    _, c = build_document(preset, make_settings("impact_basic", seed=43))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True)


def test_source_preset_is_not_mutated(library, make_settings):
    preset = library.get("impact_basic")
    before = copy.deepcopy(preset)
    build_document(preset, make_settings("impact_basic", ray_count=16, scale=3))
    assert preset == before


@pytest.mark.parametrize("delay, duration", [(0, 0.35), (0.25, 1.2), (1.5, 0.1)])
def test_timing_maps_every_key_into_delay_window(library, make_settings, delay, duration):
    _, data = build_document(library.get("impact_basic"),
                             make_settings("impact_basic", delay=delay, duration=duration))
    times = list(_all_times(data))
    # Only the explicit hidden pre-roll key may sit before the delay.
    assert all(t >= delay - 1e-4 or t == 0 for t in times)
    assert max(times) == pytest.approx(delay + duration, abs=1e-4)


def test_visibility_contract(library, make_settings):
    _, data = build_document(library.get("impact_basic"), make_settings("impact_basic", delay=0.2))
    assert all("attachment" not in slot for slot in data["slots"]), "slots must start hidden"
    for timelines in data["animations"]["fx_test_01_play"]["slots"].values():
        keys = timelines["attachment"]
        assert keys[0] == {"time": 0, "name": None}, "delayed effect needs an explicit hidden key at 0"
        assert keys[-1]["name"] is None, "slot must end hidden"


@pytest.mark.parametrize("count", [3, 9, 16])
def test_ray_count_and_flash_toggle(library, make_settings, count):
    _, data = build_document(library.get("impact_basic"), make_settings("impact_basic", ray_count=count))
    rays = [s for s in data["slots"] if "_ray_" in s["name"]]
    assert len(rays) == count
    assert data["slots"][-1]["name"].endswith("_flash_slot"), "flash draws on top"
    _, no_flash = build_document(library.get("impact_basic"),
                                 make_settings("impact_basic", ray_count=count, flash_size=0))
    assert not any(s["name"].endswith("_flash_slot") for s in no_flash["slots"])


def test_two_instances_have_no_shared_names(library, make_settings):
    preset = library.get("impact_basic")
    _, a = build_document(preset, make_settings("impact_basic", "fx_hit_01"))
    _, b = build_document(preset, make_settings("impact_basic", "fx_hit_02"))
    names_a = {x["name"] for x in a["bones"] + a["slots"]} | set(a["animations"])
    names_b = {x["name"] for x in b["bones"] + b["slots"]} | set(b["animations"])
    assert not names_a & names_b
    assert all(n.startswith("fx_hit_01_") for n in names_a)


def test_transform_colour_opacity_and_mirror(library, make_settings):
    s = make_settings("impact_basic", scale=2, rotation=45, mirror=True, primary_color="3399ff",
                      opacity=0.5, position_x=10, position_y=-20)
    _, data = build_document(library.get("impact_basic"), s)
    root = data["bones"][0]
    assert (root["scaleX"], root["scaleY"], root["rotation"], root["x"], root["y"]) == (-2, 2, 45, 10, -20)
    ray_colors = data["animations"]["fx_test_01_play"]["slots"]["fx_test_01_ray_01_slot"]["color"]
    assert ray_colors[0]["color"] == "3399ff80"  # tint rgb, alpha 1.0 * opacity 0.5
    assert all(k["color"].startswith("3399ff") for k in ray_colors)


@pytest.mark.parametrize("values", [
    {}, {"ray_count": 3, "spread": 30, "travel_distance": 0, "stagger": 0},
    {"ray_count": 16, "spread": 360, "ray_length": 400, "stagger": 0.5, "angle_jitter": 30,
     "length_variance": 0.5, "scale": 5, "duration": 2, "delay": 5, "flash_portion": 0.9},
    {"scale": 0.1, "duration": 0.1, "flash_size": 0, "opacity": 0},
])
def test_boundary_values_pass_structure_check(library, make_settings, values):
    s = make_settings("impact_basic", **values)
    doc, data = build_document(library.get("impact_basic"), s)
    report = validate_output(data, None, "fx_test_01", end_time=doc.timing.delay + doc.timing.duration)
    assert report.ok, report.errors


def test_validator_catches_broken_references(library, make_settings):
    _, data = build_document(library.get("impact_basic"), make_settings("impact_basic"))
    broken = copy.deepcopy(data)
    broken["slots"][0]["bone"] = "fx_test_01_nope"
    broken["animations"]["fx_test_01_play"]["bones"]["ghost"] = {"rotate": [{"time": 0, "angle": 1}]}
    broken["animations"]["fx_test_01_play"]["slots"]["fx_test_01_ray_01_slot"]["attachment"][-1]["name"] = "x"
    broken["bones"].append({"name": "other_root", "parent": "missing"})
    errors = " | ".join(validate_output(broken, None, "fx_test_01").errors)
    for fragment in ("missing bone 'fx_test_01_nope'", "missing bone 'ghost'", "unknown attachment 'x'",
                     "still visible", "not defined before it", "does not start with the instance prefix"):
        assert fragment in errors


def test_every_library_preset_generates_a_valid_package(tmp_path, library, make_settings):
    from anim2d.packaging.package import write_package
    for preset_id in sorted(library.presets):
        result = write_package(library.get(preset_id), make_settings(preset_id, f"fx_{preset_id}_01"), tmp_path)
        assert result.ok, (preset_id, result.report.errors)
