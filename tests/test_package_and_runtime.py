from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime_cores import core_for

from anim2d.cli import main as cli_main
from anim2d.packaging.package import write_package
from anim2d.validation.spine38_validator import validate_output

RUNTIME_DIR = Path(__file__).parent / "runtime"
SPINE_CORE = RUNTIME_DIR / "vendor" / "spine-core-3.8.js"
NODE = shutil.which("node")


def test_package_contents_and_portability(tmp_path, library, make_settings):
    result = write_package(library.get("impact_basic"), make_settings("impact_basic", "fx_impact_01"), tmp_path / "out")
    assert result.ok, result.report.errors
    out = result.output_dir
    expected = {"fx_impact_01.json", "fx_impact_01.settings.json", "generation_report.json", "IMPORT_IN_SPINE.md",
                "images/fx_impact_01/flash.png", "images/fx_impact_01/ray.png"}
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert files == expected
    assert not any(p.name.startswith(".") for p in (tmp_path / "out").iterdir()), "temp folder left behind"

    report = json.loads((out / "generation_report.json").read_text())
    assert report["compatibility_profile"] == "spine-3.8.75"
    assert "NOT VERIFIED" in report["verification"]["spine_editor_import"]
    assert set(report["files"]) == expected - {"fx_impact_01.settings.json", "generation_report.json",
                                               "IMPORT_IN_SPINE.md"}

    moved = tmp_path / "elsewhere" / "fx_impact_01"
    shutil.move(str(out), moved)
    data = json.loads((moved / "fx_impact_01.json").read_text())
    assert validate_output(data, moved, "fx_impact_01").ok


def test_never_overwrites_existing_folder(tmp_path, library, make_settings):
    settings = make_settings("impact_basic", "fx_impact_01")
    (tmp_path / "fx_impact_01").mkdir()
    (tmp_path / "fx_impact_01" / "artist_notes.txt").write_text("keep me")
    result = write_package(library.get("impact_basic"), settings, tmp_path)
    assert not result.ok and "already exists" in result.report.errors[0]
    assert (tmp_path / "fx_impact_01" / "artist_notes.txt").read_text() == "keep me"


def test_saved_variant_reproduces_package(tmp_path, library, make_settings):
    preset = library.get("impact_basic")
    first = write_package(preset, make_settings("impact_basic", "fx_a_01", ray_count=11, seed=9), tmp_path / "a")
    variant = first.output_dir / "fx_a_01.settings.json"
    assert cli_main(["generate", "impact_basic", "--variant", str(variant), "--out", str(tmp_path / "b")]) == 0
    a = (first.output_dir / "fx_a_01.json").read_text()
    b = (tmp_path / "b" / "fx_a_01" / "fx_a_01.json").read_text()
    assert a == b


def test_cli_rejects_invalid_values(tmp_path, capsys):
    code = cli_main(["generate", "impact_basic", "--name", "fx_x_01", "--out", str(tmp_path),
                     "--set", "ray_count=99"])
    assert code == 1
    assert "above the maximum" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def _runtime_sample(json_path: Path, times: list[float]) -> dict:
    out = subprocess.run([NODE, str(RUNTIME_DIR / "check_spine38.js"), str(core_for(json_path)), str(json_path),
                          *map(str, times)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE or not SPINE_CORE.is_file(),
                    reason="needs node and tools/fetch_spine_runtime.py")
@pytest.mark.parametrize("preset_id, values", [
    ("minimal_pop", {}),
    ("impact_basic", {}),
    *[(p, {}) for p in ("sword_slash", "shockwave", "sparkle_twinkle", "coin_fountain", "heal_rise", "explosion",
                        "level_up", "magic_circle", "lightning_strike", "critical_hit", "big_win_coins",
                        "win_rays", "symbol_glow", "shine_sweep", "confetti_burst", "collect_trail", "poof_reveal",
                        "light_reveal", "gem_shatter", "bubble_pop", "heart_burst", "star_rating", "tap_ripple",
                        "firework", "portal_swirl", "water_splash", "notification_ping", "loading_spinner",
                        "chest_open_rays", "focus_lines", "alert_pop", "dust_puff", "dash_lines", "stun_stars",
                        "sleep_zzz", "flame_burst")],
    ("dust_puff", {"one_side": True}),
    ("notification_ping", {"rings": 0}),
    ("firework", {"launch_portion": 0}),
    ("star_rating", {"stars": 5, "gap": 0}),
    ("collect_trail", {"target_x": -300, "target_y": -200, "arc_height": 0, "trail": 0, "pop_size": 0}),
    ("symbol_glow", {"pulses": 6, "sparkles": 0}),
    ("explosion", {"delay": 0.2, "smoke_puffs": 0, "debris": 0, "mirror": True}),
    ("magic_circle", {"spin": -720, "seed": 4}),
    ("impact_basic", {"delay": 0.3, "duration": 0.5, "ray_count": 16, "stagger": 0.5}),
    ("impact_basic", {"ray_count": 3, "flash_size": 0, "mirror": True}),
])
def test_official_38_runtime_reads_and_plays(tmp_path, library, make_settings, preset_id, values):
    settings = make_settings(preset_id, "fx_rt_01", **values)
    result = write_package(library.get(preset_id), settings, tmp_path)
    assert result.ok, result.report.errors
    delay, duration = settings["delay"], settings["duration"]
    end = delay + duration
    times = [0.0, max(0.0, delay - 0.01), delay + duration * 0.25, delay + duration * 0.5, end + 0.001, end + 1]
    run = _runtime_sample(result.output_dir / "fx_rt_01.json", times)

    assert run["animations"][0]["name"] == "fx_rt_01_play"
    assert run["animations"][0]["duration"] == pytest.approx(end, abs=1e-4)
    assert run["bones"] == result.stats["bones"] and run["slots"] == result.stats["slots"]
    samples = {s["time"]: s["visible"] for s in run["samples"]}
    if delay > 0:
        assert samples[0.0] == [] and samples[times[1]] == [], "visible before activation"
    assert samples[times[2]], "nothing visible during the effect"
    assert samples[times[4]] == [] and samples[times[5]] == [], "still visible after the effect ended"
