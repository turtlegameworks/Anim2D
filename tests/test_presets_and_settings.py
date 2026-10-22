from __future__ import annotations

import pytest

from anim2d.generation.registry import GENERATORS
from anim2d.models.preset import PresetError, load_preset_file
from anim2d.models.settings import load_variant, save_variant, validate_settings


def test_library_lists_and_searches(library):
    ids = [s.id for s in library.list_presets()]
    assert {"impact_basic", "minimal_pop"} <= set(ids)
    assert "impact_basic" in [s.id for s in library.list_presets("rays")]
    assert [s.id for s in library.list_presets("confetti")] == ["confetti_burst"]
    assert [s.id for s in library.list_presets(category="test")] == ["minimal_pop"]


@pytest.mark.parametrize("changes, message", [
    ({"id": "Bad Id"}, "preset id"),
    ({"generator": "run_my_script"}, "unknown generator"),
    ({"schema_version": 99}, "schema_version"),
    ({"images": {"ray": "../../secret.png"}}, "inside the preset folder"),
    ({"images": {"ray": "C:/Windows/x.png"}}, "relative"),
    ({"images": {"ray": "images/missing.png"}}, "missing"),
    ({"parameters": {"ray_count": {"type": "integer", "default": 50, "min": 3, "max": 16}}}, "maximum"),
    ({"parameters": {"ray_count": {"type": "integer", "default": 5}}}, "min and max"),
    ({"parameters": {"opacity": {"type": "number", "default": 1, "min": 0, "max": 1}}}, "clashes"),
    ({"common_overrides": {"duration": {"default": -1}}}, "minimum"),
    ({"common_overrides": {"duration": {"type": "boolean"}}}, "cannot override"),
])
def test_malformed_presets_fail_with_readable_errors(preset_dir, changes, message):
    _, edit = preset_dir
    path = edit(**changes)
    with pytest.raises(PresetError, match=message):
        load_preset_file(path, set(GENERATORS))


def test_settings_bounds_and_types(library):
    preset = library.get("impact_basic")
    for bad in ({"ray_count": 2}, {"ray_count": 17}, {"ray_count": 4.5}, {"duration": float("nan")},
                {"duration": 0}, {"opacity": 1.5}, {"delay": -0.1}, {"primary_color": "orange"},
                {"mirror": "maybe"}, {"no_such_setting": 1}):
        settings, report = validate_settings(preset, "fx_impact_01", bad)
        assert settings is None and report.errors, bad


@pytest.mark.parametrize("name", ["", "fx", "Fx_Impact", "1fx", "fx-impact", "fx impact", "a" * 49])
def test_instance_name_rules(library, name):
    settings, report = validate_settings(library.get("impact_basic"), name, {})
    assert settings is None
    assert "Instance name" in report.errors[0]


def test_zero_opacity_warns(library):
    settings, report = validate_settings(library.get("impact_basic"), "fx_impact_01", {"opacity": 0})
    assert settings is not None
    assert any("invisible" in w for w in report.warnings)


def test_variant_round_trip_and_mismatch(tmp_path, library, make_settings):
    preset = library.get("impact_basic")
    settings = make_settings("impact_basic", "fx_hit_07", ray_count=12, primary_color="3399ff")
    path = tmp_path / "v.json"
    save_variant(path, preset, settings, "spine-3.8.75")
    name, raw = load_variant(path, preset)
    again, report = validate_settings(preset, name, raw)
    assert again == settings
    with pytest.raises(ValueError, match="is for preset 'impact_basic'"):
        load_variant(path, library.get("minimal_pop"))
