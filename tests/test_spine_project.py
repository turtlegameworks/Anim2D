""".spine project import (Spine 3.8.x and 4.2.x).

The reader basics are tested on a synthetic stream. The rest needs .spine projects that cannot ship
with the repository and is skipped unless they are provided: set ANIM2D_SPINE_EXAMPLES to Spine's
examples folder (each project is compared with its JSON export and, through the official spine-ts 3.8
runtime, with its full-precision binary export) and ANIM2D_SPINE_PROJECTS to a folder of your own."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from anim2d.editor.document import Document, EditorError
from anim2d.formats.spine_project import SpineProjectError, load_spine_project, parse
from anim2d.formats.spine_project import reader as kp
from anim2d.formats.spine_project.convert import anim_name
from anim2d.formats.spine_project.reader import Ref

# Optional fixtures that cannot ship with the repository (Spine's own example projects and your own
# .spine files). Point these at local folders to run the tests that need them.
EXAMPLES = Path(os.environ["ANIM2D_SPINE_EXAMPLES"]) if os.environ.get("ANIM2D_SPINE_EXAMPLES") else None
USER = Path(os.environ["ANIM2D_SPINE_PROJECTS"]) if os.environ.get("ANIM2D_SPINE_PROJECTS") else None
RUNTIME = Path(__file__).parent / "runtime"
CORE38 = RUNTIME / "vendor" / "spine-core-3.8.js"
NODE = shutil.which("node")
needs_examples = pytest.mark.skipif(not (EXAMPLES and EXAMPLES.is_dir()),
                                    reason="set ANIM2D_SPINE_EXAMPLES to Spine's examples folder")
needs_user = pytest.mark.skipif(not (USER and USER.is_dir()),
                                reason="set ANIM2D_SPINE_PROJECTS to a folder of .spine files")


def all_projects() -> list[str]:
    found = glob.glob(str(EXAMPLES / "*" / "*.spine")) if EXAMPLES else []
    return sorted(found + (glob.glob(str(USER / "**" / "*.spine"), recursive=True) if USER else []))


# --- animation folder paths -----------------------------------------------------------------------

def _folder(name, parent=None):
    f = {"_class": "Obj6c", 1: name}
    if parent is not None:
        f[3] = parent
    return f


def _anim(name, folder=None):
    a = {"_class": "Animation", 1: name}
    if folder is not None:
        a[7] = folder
    return a


def test_anim_name_no_folder():
    assert anim_name(_anim("Happy")) == "Happy"


def test_anim_name_single_folder():
    folder = _folder("UI")
    assert anim_name(_anim("Button", folder)) == "UI/Button"


def test_anim_name_nested_folders():
    inner = _folder("Effects")
    outer = _folder("UI", inner)
    assert anim_name(_anim("Sparkle", outer)) == "Effects/UI/Sparkle"


def test_anim_name_with_ref():
    folder = _folder("Walk")
    a = _anim("cycle", folder)
    a[7] = Ref(0, folder)
    assert anim_name(a) == "Walk/cycle"


def test_anim_name_folder_ref_chain():
    top = _folder("Attacks")
    nested = _folder("Combo", Ref(0, top))
    a = _anim("punch", nested)
    assert anim_name(a) == "Attacks/Combo/punch"


# --- reader basics --------------------------------------------------------------------------------

def test_reader_primitives():
    data = bytes([0xAC, 0x02]) + b"ab" + bytes([ord("c") | 0x80]) + bytes([0x82]) + "é".encode() + b"\x3f\x80\x00\x00"
    r = kp.Reader(data)
    assert r.varint() == 300
    assert r.kstring() == "abc"
    assert r.kstring() == "é"
    assert r.float() == 1.0


def test_reference_spaces():
    # two strings, then back-references to them; with a value space they get their own numbering
    data = b"\x01" + b"ab" + bytes([0x80 | ord("c")]) + b"\x01" + b"d" + bytes([0x80 | ord("e")]) + b"\x02\x03"
    r = kp.Reader(data)
    a, b, c, d = r.string(), r.string(), r.string(), r.string()
    assert (a, b, c.target, d.target) == ("abc", "de", "abc", "de")
    r = kp.Reader(data)
    r.value_space = {"str"}
    r.objects.append("entity")  # entities are numbered separately
    assert [r.string(), r.string(), r.string().target] == ["abc", "de", "abc"]


def test_not_a_project(tmp_path):
    bad = tmp_path / "x.spine"
    bad.write_bytes(b"not deflate at all")
    with pytest.raises(EditorError, match="Cannot open"):
        Document.import_spine_project(bad)
    other = tmp_path / "old.spine"
    stream = bytes([0x10, 0xB0, 0x20, 0xB0]) + b" 3.7.9" + bytes([0x80 | ord("4")])
    comp = zlib.compressobj(wbits=-15)
    other.write_bytes(comp.compress(stream) + comp.flush())
    with pytest.raises(SpineProjectError, match="not supported"):
        parse(zlib.decompressobj(-15).decompress(other.read_bytes()))


# --- every project provided ---------------------------------------------------------------

@pytest.mark.skipif(not all_projects(), reason="needs Spine projects")
def test_every_project_imports_and_validates():
    versions = set()
    for f in all_projects():
        doc, notes = Document.import_spine_project(Path(f))
        assert doc.validate()[0] == [], f
        versions.add(notes[0])
        assert doc.bones and doc.slots
    assert any("3.8" in v for v in versions)


# --- 3.8: Spine's examples against their exports --------------------------------------------------

def _close(a, b, tol=0.02):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(b) / 100)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_close(a.get(k, DEFAULTS.get(k)), b.get(k, DEFAULTS.get(k)), tol) for k in set(a) | set(b)
                   if k not in ("skin", "triangles", "order"))
    return a == b


DEFAULTS = {"x": 0, "y": 0, "rotation": 0, "shearX": 0, "shearY": 0, "scaleX": 1, "scaleY": 1, "length": 0}
PAIRS = ["alien/alien-pro", "coin/coin-pro", "goblins/goblins-pro", "hero/hero-pro", "mix-and-match/mix-and-match-pro",
         "owl/owl-pro", "raptor/raptor-pro", "spineboy/spineboy-pro", "stretchyman/stretchyman-pro", "tank/tank-pro",
         "vine/vine-pro", "powerup/powerup-pro", "windmill/windmill-ess"]


@needs_examples
@pytest.mark.parametrize("name", PAIRS)
def test_setup_matches_the_json_export(name):
    folder, base = name.split("/")
    got, notes, version = load_spine_project(EXAMPLES / folder / f"{base}.spine")
    want = json.loads((EXAMPLES / folder / "export" / f"{base}.json").read_text(encoding="utf-8"))
    assert version == want["skeleton"]["spine"]
    assert [b["name"] for b in got["bones"]] == [b["name"] for b in want["bones"]]
    for g, w in zip(got["bones"], want["bones"]):
        assert _close(g, w), (g, w)
    assert [s["name"] for s in got["slots"]] == [s["name"] for s in want["slots"]]
    for g, w in zip(got["slots"], want["slots"]):
        assert _close(g, w), (g, w)
    gs = {s["name"]: s["attachments"] for s in got["skins"] if s["attachments"] or s["name"] != "default"}
    ws = {s["name"]: s["attachments"] for s in want["skins"]}
    assert set(gs) == set(ws)
    for skin in ws:
        assert _close(gs[skin], ws[skin]), skin
    for kind in ("ik", "transform", "path"):
        g = {c["name"]: c for c in got.get(kind, [])}
        w = {c["name"]: c for c in want.get(kind, [])}
        assert set(g) == set(w)
        for n in w:
            assert _close(g[n], w[n]), (kind, n)
    assert set(got["animations"]) == set(want["animations"])


def _runtime(skeleton: Path, anim: str, times: list[float]) -> dict:
    out = subprocess.run([NODE, str(RUNTIME / "check_spine38.js"), str(CORE38), str(skeleton), f"--anim={anim}",
                          "--full"] + [str(t) for t in times], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout)


@needs_examples
@pytest.mark.skipif(not NODE or not CORE38.is_file(), reason="needs node and the spine-ts 3.8 core")
@pytest.mark.parametrize("name", ["alien/alien-pro", "raptor/raptor-pro", "spineboy/spineboy-pro", "tank/tank-pro",
                                  "stretchyman/stretchyman-pro", "goblins/goblins-pro"])
def test_animations_match_the_binary_export_in_the_official_runtime(name, tmp_path):
    """Spine's .skel keeps full precision, so the imported animations must reproduce it exactly
    (the JSON export rounds values and is off by up to a few pixels)."""
    folder, base = name.split("/")
    data, _, _ = load_spine_project(EXAMPLES / folder / f"{base}.spine")
    data["skeleton"]["spine"] = "3.8.99"
    converted = tmp_path / "rig.json"
    converted.write_text(json.dumps(data), encoding="utf-8")
    skel = EXAMPLES / folder / "export" / f"{base}.skel"
    times = [round(i * 0.05, 3) for i in range(41)]
    for anim in data["animations"]:
        a, b = _runtime(skel, anim, times), _runtime(converted, anim, times)
        for sa, sb in zip(a["samples"], b["samples"]):
            for bone, va in sa["bones"].items():
                assert abs(va[0] - sb["bones"][bone][0]) <= 0.03 and abs(va[1] - sb["bones"][bone][1]) <= 0.03, \
                    (anim, sa["time"], bone)
            for slot, va in sa["slots"].items():
                assert va.get("attachment") == sb["slots"][slot].get("attachment"), (anim, sa["time"], slot)


@needs_examples
def test_deform_and_events():
    data, _, _ = load_spine_project(EXAMPLES / "alien" / "alien-pro.spine")
    want = json.loads((EXAMPLES / "alien" / "export" / "alien-pro.json").read_text(encoding="utf-8"))
    death = data["animations"]["death"]
    key = death["deform"]["default"]["burst-bg"]["burst03-bg"][0]
    wkey = want["animations"]["death"]["deform"]["default"]["burst-bg"]["burst03-bg"][0]
    assert key["offset"] == wkey["offset"] and _close(key["vertices"], wkey["vertices"])
    assert [e["name"] for e in death["events"]] == [e["name"] for e in want["animations"]["death"]["events"]]
    assert death["drawOrder"][0]["offsets"] == want["animations"]["death"]["drawOrder"][0]["offsets"]


# --- your own projects ----------------------------------------------------------------------------

@needs_user
def test_import_data_from_a_spine_project(tmp_path):
    projects = [p for p in all_projects() if USER and Path(p).is_relative_to(USER)]
    if len(projects) < 2:
        pytest.skip("needs two projects")
    doc, _ = Document.import_spine_project(Path(projects[0]))
    before = set(doc.animation_names())
    notes = doc.merge_spine_json(Path(projects[1]))
    assert set(doc.animation_names()) > before and isinstance(notes, list)
    assert not [e for e in doc.validate()[0] if "image is missing" not in e]  # its images are in its own folder
    doc.undo()
    assert set(doc.animation_names()) == before
