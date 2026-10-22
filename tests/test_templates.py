"""Template round trip: generated package -> edited in 'Spine' -> exported -> registered -> generated again.

The 'Spine edit' is simulated by changing the JSON the way an artist would in the editor
(retime, add bones/slots/meshes/events/draw order, recolour). A real Spine 3.8 export
(spineboy-ess, fetched by tools/fetch_spine_runtime.py) is tested too.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime_cores import core_for

from anim2d.models.preset import PresetError
from anim2d.models.settings import validate_settings
from anim2d.packaging.package import build_document, write_package
from anim2d.presets.library import KNOWN_GENERATORS, PresetLibrary
from anim2d.templates.register import analyze_export, register_template
from anim2d.validation.spine38_validator import validate_output

ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).parent / "runtime" / "vendor"
SPINE_CORE = VENDOR / "spine-core-3.8.js"
SPINEBOY = VENDOR / "spineboy" / "spineboy-ess.json"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


def _times(anim):
    for group in ("bones", "slots"):
        for tls in anim.get(group, {}).values():
            for keys in tls.values():
                yield from keys
    for skin in anim.get("deform", {}).values():
        for slot in skin.values():
            for keys in slot.values():
                yield from keys
    yield from anim.get("drawOrder", [])
    yield from anim.get("events", [])


@pytest.fixture
def artist_export(tmp_path, library, make_settings):
    """A Spine 'export' of fx_impact_01 after an artist edited it."""
    settings = make_settings("impact_basic", "fx_impact_01")
    pkg = write_package(library.get("impact_basic"), settings, tmp_path / "pkg").output_dir
    data = json.loads((pkg / "fx_impact_01.json").read_text())
    anim = data["animations"]["fx_impact_01_play"]

    for k in _times(anim):  # artist stretches the animation from 0.35s to 0.525s
        k["time"] = round(k.get("time", 0) * 1.5, 5)
    data["bones"].append({"name": "fx_impact_01_glow", "parent": "fx_impact_01_root", "y": 12})
    data["bones"].append({"name": "sparkle_extra", "parent": "fx_impact_01_root", "x": 30})
    data["slots"].append({"name": "fx_impact_01_glow_slot", "bone": "fx_impact_01_glow", "color": "66ff99ff"})
    data["slots"].append({"name": "fx_impact_01_streak_slot", "bone": "sparkle_extra", "color": "ffffffff",
                          "attachment": "fx_impact_01_streak"})
    skin = data["skins"][0]["attachments"]
    skin["fx_impact_01_glow_slot"] = {"fx_impact_01_glow": {"path": "fx_impact_01/glow", "width": 256, "height": 256}}
    skin["fx_impact_01_streak_slot"] = {"fx_impact_01_streak": {
        "type": "mesh", "path": "fx_impact_01/ray", "uvs": [1, 1, 0, 1, 0, 0, 1, 0],
        "triangles": [1, 2, 3, 1, 3, 0], "vertices": [128, -24, -128, -24, -128, 24, 128, 24],
        "hull": 4, "width": 256, "height": 48}}
    anim["slots"]["fx_impact_01_glow_slot"] = {
        "attachment": [{"time": 0, "name": "fx_impact_01_glow"}, {"time": 0.525, "name": None}],
        "color": [{"time": 0, "color": "66ff99ff"}, {"time": 0.525, "color": "66ff9900"}]}
    anim["slots"]["fx_impact_01_streak_slot"] = {"attachment": [{"time": 0.525, "name": None}]}
    anim["bones"]["fx_impact_01_glow"] = {"shear": [{"time": 0, "x": 0, "y": 0}, {"time": 0.3, "x": 15, "y": 0}]}
    anim["deform"] = {"default": {"fx_impact_01_streak_slot": {"fx_impact_01_streak": [
        {"time": 0}, {"time": 0.2, "offset": 2, "vertices": [0, 10, 0, -10]}]}}}
    anim["drawOrder"] = [{"time": 0.15, "offsets": [{"slot": "fx_impact_01_glow_slot", "offset": -3}]}]
    data["events"] = {"fx_impact_01_hit": {"int": 1}}
    anim["events"] = [{"time": 0.09, "name": "fx_impact_01_hit"}]

    export = tmp_path / "spine_export"
    (export / "images" / "fx_impact_01").mkdir(parents=True)
    for name in ("flash", "ray"):
        shutil.copy(pkg / "images" / "fx_impact_01" / f"{name}.png", export / "images" / "fx_impact_01")
    shutil.copy(pkg / "images" / "fx_impact_01" / "flash.png", export / "images" / "fx_impact_01" / "glow.png")
    path = export / "impact_edit.json"
    path.write_text(json.dumps(data))
    return path


def _register(analysis, user_lib, preset_id="my_hit", **kw):
    return register_template(analysis, user_lib, preset_id=preset_id, name="My Hit",
                             known_generators=KNOWN_GENERATORS, **kw)


def test_analysis_strips_prefix_and_detects_roles(artist_export):
    a = analyze_export(artist_export)
    assert a.ok, a.errors
    assert a.old_prefix == "fx_impact_01"
    assert a.source_duration == pytest.approx(0.525)
    bones = [b["name"] for b in a.template["bones"]]
    assert bones[:2] == ["root", "ray_01"] and "glow" in bones and "sparkle_extra" in bones
    assert set(a.image_files) == {"flash", "ray", "glow"}
    roles = {s.name: s.role for s in a.slots}
    assert all(roles[f"ray_{i:02d}_slot"] == "primary" for i in range(1, 9))
    assert roles["flash_slot"] == "secondary"
    assert roles["glow_slot"] == "keep" and roles["streak_slot"] == "keep"
    assert (a.primary_rgb, a.secondary_rgb) == ("ffb340", "fff6dc")


def test_register_and_generate_new_instance(tmp_path, artist_export, isolated_user_library):
    preset = _register(analyze_export(artist_export), isolated_user_library)
    library = PresetLibrary.load(ROOT / "src" / "anim2d" / "preset_library", isolated_user_library)
    preset = library.get("my_hit")
    assert preset.source == "user" and preset.is_template and preset.version == 1
    assert {p.name for p in preset.parameters} == set(validate_settings(preset, "fx_x_01", {})[0].as_dict())
    assert preset.parameter("duration").default == pytest.approx(0.525)
    assert preset.parameter("primary_color").default == "ffb340ff"

    settings, report = validate_settings(preset, "fx_myhit_01", {
        "duration": 1.05, "delay": 0.2, "primary_color": "3399ff", "opacity": 0.5, "mirror": True, "scale": 2})
    assert settings is not None, report.errors
    result = write_package(preset, settings, tmp_path / "out")
    assert result.ok, result.report.errors
    data = json.loads((result.output_dir / "fx_myhit_01.json").read_text())
    anim = data["animations"]["fx_myhit_01_play"]

    names = [b["name"] for b in data["bones"]] + [s["name"] for s in data["slots"]] + list(data["events"])
    assert all(n.startswith("fx_myhit_01_") for n in names)
    assert data["bones"][0]["scaleX"] == -2 and data["bones"][0]["scaleY"] == 2
    times = [k.get("time", 0) for k in _times(anim)]
    assert max(times) == pytest.approx(1.25)  # delay + duration
    assert anim["events"][0] == {"time": pytest.approx(0.38), "name": "fx_myhit_01_hit"}
    ray = anim["slots"]["fx_myhit_01_ray_01_slot"]
    assert ray["attachment"][0] == {"time": 0, "name": None}
    assert all(k["color"].startswith("3399ff") for k in ray["color"])
    assert ray["color"][0]["color"].endswith("80")  # alpha 1.0 * opacity 0.5
    glow = anim["slots"]["fx_myhit_01_glow_slot"]["color"]
    assert glow[0]["color"] == "66ff9980"  # 'keep' role: colour kept, opacity applied
    streak = data["skins"][0]["attachments"]["fx_myhit_01_streak_slot"]["fx_myhit_01_streak"]
    assert streak["path"] == "fx_myhit_01/ray"
    assert "fx_myhit_01_streak" in anim["deform"]["default"]["fx_myhit_01_streak_slot"]
    assert anim["drawOrder"][0]["offsets"][0]["slot"] == "fx_myhit_01_glow_slot"
    # Visible in setup + delay: pre-roll hides it, then shows it at activation.
    assert anim["slots"]["fx_myhit_01_streak_slot"]["attachment"][:2] == [
        {"time": 0, "name": None}, {"time": 0.2, "name": "fx_myhit_01_streak"}]
    files = {p.relative_to(result.output_dir).as_posix() for p in result.output_dir.rglob("*.png")}
    assert files == {f"images/fx_myhit_01/{n}.png" for n in ("flash", "ray", "glow")}


def test_template_round_trips_twice_with_stable_names(tmp_path, artist_export, isolated_user_library):
    first = analyze_export(artist_export)
    preset = _register(first, isolated_user_library)
    settings, _ = validate_settings(preset, "fx_again_07", {})
    out = write_package(preset, settings, tmp_path / "again").output_dir
    second = analyze_export(out / "fx_again_07.json")
    assert second.ok, second.errors
    assert second.old_prefix == "fx_again_07"
    for key in ("bones", "slots"):
        assert [x["name"] for x in second.template[key]] == [x["name"] for x in first.template[key]]
    assert second.source_duration == pytest.approx(first.source_duration)
    assert {s.name: s.role for s in second.slots} == {s.name: s.role for s in first.slots}


def test_generating_does_not_change_the_template(artist_export, isolated_user_library):
    preset = _register(analyze_export(artist_export), isolated_user_library)
    before = preset.template_file.read_bytes()
    for name in ("fx_a_01", "fx_b_01"):
        settings, _ = validate_settings(preset, name, {"duration": 2, "primary_color": "00ff00"})
        build_document(preset, settings)
    assert preset.template_file.read_bytes() == before
    _, a = build_document(preset, validate_settings(preset, "fx_a_01", {})[0])
    _, b = build_document(preset, validate_settings(preset, "fx_b_01", {})[0])
    assert not ({x["name"] for x in a["bones"]} & {x["name"] for x in b["bones"]})


def test_replace_requires_opt_in_and_keeps_backup(artist_export, isolated_user_library):
    analysis = analyze_export(artist_export)
    _register(analysis, isolated_user_library)
    with pytest.raises(PresetError, match="already exists"):
        _register(analysis, isolated_user_library)
    replaced = _register(analysis, isolated_user_library, replace=True)
    assert replaced.version == 2
    backups = list((isolated_user_library / "_backup").iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("my_hit-v1-")
    library = PresetLibrary.load(ROOT / "src" / "anim2d" / "preset_library", isolated_user_library)
    assert not library.errors, "backup folders must not be loaded as presets"


def test_role_overrides_are_respected(artist_export, isolated_user_library):
    analysis = analyze_export(artist_export)
    roles = {s.name: s.role for s in analysis.slots}
    roles["glow_slot"] = "secondary"
    preset = _register(analysis, isolated_user_library, roles=roles)
    settings, _ = validate_settings(preset, "fx_r_01", {"secondary_color": "ff0000"})
    _, data = build_document(preset, settings)
    assert data["animations"]["fx_r_01_play"]["slots"]["fx_r_01_glow_slot"]["color"][0]["color"] == "ff0000ff"


def _edit(path: Path, change) -> Path:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("change, message", [
    (lambda d: d.__setitem__("ik", [{"name": "x", "bones": ["fx_impact_01_glow"], "target": "fx_impact_01_root"}]),
     "IK constraints"),
    (lambda d: d["skeleton"].__setitem__("spine", "4.1.23"), "Spine 4.1.23"),
    (lambda d: d["skins"].append({"name": "blue", "attachments": {}}), "Only the default skin"),
    (lambda d: d["skins"][0]["attachments"]["fx_impact_01_streak_slot"]["fx_impact_01_streak"].update(
        {"parent": "fx_impact_01_glow"}), "Linked mesh"),
    (lambda d: d["skins"][0]["attachments"]["fx_impact_01_glow_slot"]["fx_impact_01_glow"].update(
        {"path": "fx_impact_01/missing"}), "missing.png"),
    (lambda d: d["bones"].append({"name": "glow", "parent": "fx_impact_01_root"}), "collide"),
    (lambda d: d["events"].__setitem__("fx_impact_01_hit", {"audio": "boom.wav"}), "audio"),
    (lambda d: d["animations"].__setitem__("idle", {}), "several animations"),
])
def test_unsupported_exports_are_rejected_with_reasons(artist_export, change, message):
    analysis = analyze_export(_edit(artist_export, change))
    assert not analysis.ok
    assert any(message in e for e in analysis.errors), analysis.errors


def test_choosing_an_animation(artist_export):
    def add_idle(d):
        d["animations"]["idle"] = copy.deepcopy(d["animations"]["fx_impact_01_play"])
    path = _edit(artist_export, add_idle)
    a = analyze_export(path, animation="fx_impact_01_play")
    assert a.ok and list(a.template["animations"]) == ["play"]
    assert any("dropped: idle" in w for w in a.warnings)


def _runtime(json_path: Path, times: list[float]) -> dict:
    out = subprocess.run([NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(core_for(json_path)),
                          str(json_path), *map(str, times)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@needs_runtime
def test_template_output_loads_in_official_runtime(tmp_path, artist_export, isolated_user_library):
    preset = _register(analyze_export(artist_export), isolated_user_library)
    settings, _ = validate_settings(preset, "fx_rt_01", {"delay": 0.25, "duration": 0.6})
    result = write_package(preset, settings, tmp_path / "rt")
    run = _runtime(result.output_dir / "fx_rt_01.json", [0.1, 0.4, 0.86])
    assert run["animations"][0]["duration"] == pytest.approx(0.85, abs=1e-4)
    visible = {s["time"]: {v["slot"] for v in s["visible"]} for s in run["samples"]}
    assert visible[0.1] == set(), "visible during start delay"
    assert {"fx_rt_01_glow_slot", "fx_rt_01_streak_slot", "fx_rt_01_ray_01_slot"} <= visible[0.4]
    assert visible[0.86] == set()


@pytest.mark.skipif(not SPINEBOY.is_file(), reason="needs tools/fetch_spine_runtime.py")
def test_real_spine_export_spineboy(tmp_path, isolated_user_library):
    a = analyze_export(SPINEBOY)
    assert not a.ok and any("several animations" in e for e in a.errors)
    a = analyze_export(SPINEBOY, animation="run")
    assert a.ok, a.errors
    assert a.old_prefix == "" and a.counts["images"] == 26 and a.counts["events"] == 1
    preset = _register(a, isolated_user_library, preset_id="boy_run")
    settings, _ = validate_settings(preset, "fx_boy_01", {"delay": 0.2, "duration": 1.0, "mirror": True})
    result = write_package(preset, settings, tmp_path / "boy")
    assert result.ok, result.report.errors
    data = json.loads((result.output_dir / "fx_boy_01.json").read_text())
    assert validate_output(data, result.output_dir, "fx_boy_01", strict_one_shot=False).ok
    assert data["animations"]["fx_boy_01_run"]["events"][0]["name"] == "fx_boy_01_footstep"
    if NODE and SPINE_CORE.is_file():
        run = _runtime(result.output_dir / "fx_boy_01.json", [0.1, 0.6])
        assert run["animations"][0]["duration"] == pytest.approx(1.2, abs=1e-4)
        counts = [len(s["visible"]) for s in run["samples"]]
        assert counts[0] == 0 and counts[1] > 10


SPINEBOY_40 = VENDOR / "v40" / "spineboy-ess.json"


@pytest.mark.skipif(not SPINEBOY_40.is_file() or not SPINEBOY.is_file(), reason="needs tools/fetch_spine_runtime.py")
def test_spine_40_export_registers_as_template(tmp_path, isolated_user_library):
    """A Spine 4.0 export (same art as the 3.8 spineboy) becomes a template; curves 3.8 cannot express are noted."""
    export = tmp_path / "export"
    shutil.copytree(SPINEBOY.parent / "images", export / "images")
    shutil.copy(SPINEBOY_40, export / "spineboy-ess.json")
    a = analyze_export(export / "spineboy-ess.json", animation="run")
    assert a.ok, a.errors
    assert a.spine_version.startswith("4.0")
    preset = _register(a, isolated_user_library, preset_id="boy40")
    settings, _ = validate_settings(preset, "fx_boy40_01", {"duration": 1.0})
    result = write_package(preset, settings, tmp_path / "out")
    assert result.ok, result.report.errors
