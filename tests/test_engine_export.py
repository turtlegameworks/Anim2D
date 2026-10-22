"""Export for game engines: skeleton data plus a packed atlas that the official runtimes load."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from runtime_cores import NODE, core_for
from anim2d.packaging.engine_export import EngineExport, export_for_engine

CHECK = os.path.join(os.path.dirname(__file__), "runtime", "check_spine38.js")


@pytest.mark.parametrize("options, data, atlas", [
    (EngineExport(), ".json", ".atlas"),
    (EngineExport(version="3.8", unity=True), ".json", ".atlas.txt"),
    (EngineExport(binary=True, premultiply_alpha=True), ".skel", ".atlas"),
])
def test_exported_files_load_in_the_runtime(tmp_path, library, make_settings, options, data, atlas):
    settings = make_settings("star_rating", "fx_stars_01")
    result = export_for_engine(library.get("star_rating"), settings, tmp_path, options)
    assert result.ok, result.report.errors
    out = result.output_dir
    assert out.name == "fx_stars_01_runtime"
    names = {p.name for p in out.iterdir()}
    assert {f"fx_stars_01{data}", f"fx_stars_01{atlas}", "fx_stars_01.png", "README_ENGINE.md"} <= names
    assert not (out / "images").exists(), "the atlas replaces loose images"
    if data == ".json":
        stamp = json.loads((out / f"fx_stars_01{data}").read_text())["skeleton"]["spine"]
        assert stamp.startswith(options.version)
    if not NODE:
        pytest.skip("needs node")
    skel = out / f"fx_stars_01{data}"
    run = subprocess.run([NODE, CHECK, str(core_for(skel)), str(skel), "0.3", f"--atlas={out / f'fx_stars_01{atlas}'}"],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["animations"][0]["name"] == "fx_stars_01_play"
    assert any(report["samples"][0]["visible"]), "nothing visible through the atlas"


def test_never_overwrites(tmp_path, library, make_settings):
    settings = make_settings("star_rating", "fx_stars_01")
    (tmp_path / "fx_stars_01_runtime").mkdir()
    result = export_for_engine(library.get("star_rating"), settings, tmp_path)
    assert not result.ok and "already exists" in result.report.errors[0]
    bad = export_for_engine(library.get("star_rating"), settings, tmp_path / "x", EngineExport(version="3.8", binary=True))
    assert not bad.ok and "4.0 only" in bad.report.errors[0]
