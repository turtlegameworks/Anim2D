"""Regression tests for defects found in the October 2026 audit (docs/audit-2026-10.md)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from anim2d.editor import data_export
from anim2d.editor.document import Document, EditorError
from anim2d.formats import atlas
from anim2d.models.preset import PresetError, load_preset_file
from anim2d.generation.registry import GENERATORS
from anim2d.packaging.package import write_package

ROOT = Path(__file__).resolve().parents[1]
DOT = ROOT / "src" / "anim2d" / "preset_library" / "minimal_pop" / "images" / "dot.png"


def _rig(tmp_path: Path) -> Document:
    images = tmp_path / "images"
    images.mkdir()
    shutil.copy(DOT, images / "rig.png")
    doc = Document(images_dir=images)
    doc.add_bone("root", "arm", length=50)
    doc.add_slot("arm", "arm", image="rig")
    return doc


# F01: atlas names cannot reach outside the chosen folders

def test_unpack_refuses_region_names_that_escape(tmp_path):
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(tmp_path / "page.png")
    (tmp_path / "evil.atlas").write_text("page.png\nsize: 2,2\n../escaped\nbounds: 0,0,2,2\n", encoding="utf-8")
    out = tmp_path / "output"
    with pytest.raises(atlas.PackError, match="outside|relative"):
        atlas.unpack(tmp_path / "evil.atlas", out)
    assert not (tmp_path / "escaped.png").exists() and not out.exists()


def test_unpack_refuses_page_names_that_escape(tmp_path):
    (tmp_path / "evil.atlas").write_text("C:/elsewhere/page.png\nsize: 2,2\nr\nbounds: 0,0,2,2\n", encoding="utf-8")
    with pytest.raises(atlas.PackError, match="relative"):
        atlas.unpack(tmp_path / "evil.atlas", tmp_path / "output")


# F02: an existing page image blocks an export without overwrite

def test_export_does_not_overwrite_an_existing_atlas_page(tmp_path):
    doc = _rig(tmp_path)
    out = tmp_path / "export"
    out.mkdir()
    sentinel = out / "rig.png"
    sentinel.write_bytes(b"keep me")
    with pytest.raises(EditorError, match="rig.png already exists"):
        doc.export_spine_json(out / "rig.json", atlas=atlas.PackSettings())
    assert sentinel.read_bytes() == b"keep me" and not (out / "rig.json").exists()


# F03: weighted vertices follow their bones by name when merged into another skeleton

def test_merge_remaps_weighted_bone_indices():
    source = {"bones": [{"name": "root"}, {"name": "arm", "parent": "root"}],
              "slots": [{"name": "s", "bone": "arm"}],
              "skins": [{"name": "default", "attachments": {"s": {"m": {
                  "type": "mesh", "uvs": [0, 0, 1, 0, 1, 1],
                  "vertices": [1, 1, 0, 0, 1, 1, 1, 10, 0, 1, 1, 1, 0, 10, 1], "triangles": [0, 1, 2]}}}}]}
    target = {"bones": [{"name": "root"}, {"name": "unrelated", "parent": "root"}], "slots": [],
              "skins": [{"name": "default", "attachments": {}}]}
    data_export.merge_skeleton(target, source)
    mesh = target["skins"][0]["attachments"]["s"]["m"]
    arm = [b["name"] for b in target["bones"]].index("arm")
    assert arm == 2 and mesh["vertices"][1::5] == [arm, arm, arm]


# F07: Portal Swirl's shortest collapse time is valid

def test_portal_swirl_minimum_close_time(tmp_path, make_settings, library):
    settings = make_settings("portal_swirl", close_time=0.3)
    result = write_package(library.get("portal_swirl"), settings, tmp_path)
    assert result.ok, result.report.errors


# F10: malformed files give readable errors

def test_project_without_skeleton_is_a_readable_error(tmp_path):
    path = tmp_path / "broken.anim2d"
    path.write_text(json.dumps({"format": "anim2d-project", "format_version": 2}), encoding="utf-8")
    with pytest.raises(EditorError, match="no skeleton"):
        Document.load(path)


def test_preset_with_string_bound_is_a_preset_error(preset_dir):
    _, edit = preset_dir
    path = edit(common_overrides={"scale": {"min": "wrong type"}})
    with pytest.raises(PresetError, match="min must be a number"):
        load_preset_file(path, set(GENERATORS))


# F12: cleanup keeps the key that sets the animation's length

def test_cleanup_keeps_duration():
    data = {"animations": {"a": {"bones": {"b": {"rotate": [{"value": 0}, {"time": 1, "value": 0}]}}}}}
    data_export.clean_animations(data)
    assert data["animations"]["a"]["bones"]["b"]["rotate"][-1]["time"] == 1


def test_cleanup_still_removes_keys_that_change_nothing():
    keys = [{"value": 0}, {"time": 0.5, "value": 0}, {"time": 1, "value": 0}]
    data = {"animations": {"a": {"bones": {"b": {"rotate": keys}}}}}
    assert data_export.clean_animations(data) == 1 and [k.get("time", 0) for k in keys] == [0, 1]


# F16: Save As into another folder keeps the relative audio folder pointing at the same place

def test_save_as_rebases_audio_folder(tmp_path):
    from anim2d.editor.event_audio import audio_dir, set_audio_dir
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "audio").mkdir(parents=True)
    new.mkdir()
    doc = _rig(tmp_path)
    doc.save(old / "p.anim2d")
    set_audio_dir(doc, old / "audio")
    doc.save(new / "p.anim2d")
    assert audio_dir(doc).resolve() == (old / "audio").resolve()
    assert audio_dir(Document.load(new / "p.anim2d")).resolve() == (old / "audio").resolve()


# Rename: projects saved before Anim2D still open

def test_legacy_rigworks_project_opens(tmp_path):
    doc = _rig(tmp_path)
    doc.save(tmp_path / "hero.anim2d")
    project = json.loads((tmp_path / "hero.anim2d").read_text(encoding="utf-8"))
    project["format"] = "rigworks-project"
    (tmp_path / "hero.rig2d").write_text(json.dumps(project), encoding="utf-8")
    assert [b["name"] for b in Document.load(tmp_path / "hero.rig2d").bones] == ["root", "arm"]


# F14: the uninstaller only removes what the installer created

def _installer():
    spec = importlib.util.spec_from_file_location("anim2d_setup", ROOT / "installer" / "anim2d_setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_uninstall_ignores_paths_outside_the_install(tmp_path):
    setup = _installer()
    target, menu = tmp_path / "install", tmp_path / "menu" / setup.APP_NAME
    target.mkdir()
    menu.mkdir(parents=True)
    outside, unrelated = tmp_path / "outside.txt", tmp_path / "unrelated.lnk"
    outside.write_text("x")
    unrelated.write_text("x")
    (target / setup.PROGRAM).write_text("x")
    owned = menu / f"{setup.APP_NAME}.lnk"
    owned.write_text("x")
    (target / setup.MANIFEST).write_text(json.dumps({
        "files": [setup.PROGRAM, "../outside.txt"], "shortcuts": [str(owned), str(unrelated)],
        "start_menu_dir": str(menu), "registry_key": None}), encoding="utf-8")
    setup.uninstall(target)
    assert outside.exists() and unrelated.exists()
    assert not owned.exists() and not (target / setup.PROGRAM).exists()


def test_installer_version_matches_the_package():
    from anim2d import __version__
    assert _installer().VERSION == __version__
