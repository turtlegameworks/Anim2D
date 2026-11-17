"""Export for Spine 4.1, 4.2 and 4.3 runtimes: the layout conversion, and parity in the official runtimes.

The parity tests load the same rig in the official spine-core 4.0 runtime (the editor's 4.0 export)
and in the 4.1, 4.2 and 4.3 runtimes (the 4.1 to 4.3 exports), sample every animation of every
skin, and compare each bone's world transform, each slot's colors, attachment and deform, and the
draw order.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from anim2d.editor.document import Document, EditorError  # noqa: E402
from anim2d.formats import spine4x  # noqa: E402
from runtime_cores import CORES_4X, HAVE_4X_RUNTIMES, NODE, VENDOR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHECK = Path(__file__).parent / "runtime" / "check_spine4x.js"
V40 = VENDOR / "v40"
RIGS = ["spineboy-pro", "raptor-pro", "tank-pro", "vine-pro", "stretchyman-pro", "mix-and-match-pro", "goblins-pro"]
needs_4x = pytest.mark.skipif(not HAVE_4X_RUNTIMES, reason="needs node and tools/fetch_spine_runtime.py")


def _sample(path: Path, version: str, count: int = 7) -> dict:
    out = subprocess.run([NODE, str(CHECK), str(CORES_4X[version]), str(path), str(count)],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def _worst(a: dict, b: dict) -> tuple[float, str]:
    """The largest difference between two runtimes' samples (inf for a different attachment or
    draw order), with where it is."""
    worst = (0.0, "")
    assert a["animations"].keys() == b["animations"].keys()
    for name, anim in a["animations"].items():
        other = b["animations"][name]
        assert anim["duration"] == pytest.approx(other["duration"], abs=1e-3), name
        for skin, samples in anim["skins"].items():
            for x, y in zip(samples, other["skins"][skin], strict=True):
                where = f"{name}/{skin} at {x['time']}"
                if x["drawOrder"] != y["drawOrder"]:
                    return float("inf"), f"{where}: draw order"
                for bone, v in x["bones"].items():
                    d = max(abs(p - q) for p, q in zip(v, y["bones"][bone], strict=True))
                    if d > worst[0]:
                        worst = (d, f"{where}: bone {bone}")
                for slot, v in x["slots"].items():
                    w = y["slots"][slot]
                    if (v["attachment"], v["dark"] is None, len(v["deform"])) != \
                            (w["attachment"], w["dark"] is None, len(w["deform"])):
                        return float("inf"), f"{where}: slot {slot}"
                    pairs = list(zip(v["color"], w["color"])) + list(zip(v["deform"], w["deform"]))
                    pairs += list(zip(v["dark"] or [], w["dark"] or []))
                    d = max([abs(p - q) for p, q in pairs] + [0.0])
                    if d > worst[0]:
                        worst = (d, f"{where}: slot {slot}")
    return worst


# --- the layout conversion ---------------------------------------------------------------------

def _rig_40() -> dict:
    return {
        "skeleton": {"spine": "4.0.64"},
        "bones": [{"name": "root"}, {"name": "a", "parent": "root", "transform": "noScale"},
                  {"name": "b", "parent": "a"}, {"name": "c", "parent": "root"}],
        "slots": [{"name": "s", "bone": "a", "attachment": "m"}, {"name": "p", "bone": "root"}],
        "ik": [{"name": "ik", "order": 2, "bones": ["b"], "target": "c", "uniform": True, "stretch": True}],
        "transform": [{"name": "tc", "order": 0, "bones": ["b"], "target": "c", "local": True, "relative": True,
                       "rotation": 10, "mixRotate": 0.5, "mixX": 0, "mixScaleX": 0, "mixShearY": 0}],
        "path": [{"name": "pc", "order": 1, "bones": ["c"], "target": "p"}],
        "skins": [{"name": "default", "attachments": {
            "s": {"m": {"type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2],
                        "vertices": [0, 0, 10, 0, 0, 10], "hull": 3, "width": 10, "height": 10},
                  "l": {"type": "linkedmesh", "parent": "m", "deform": False, "width": 10, "height": 10}},
            "p": {"p": {"type": "path", "lengths": [10], "vertexCount": 6, "vertices": [0] * 12}}}}],
        "animations": {"a": {
            "deform": {"default": {"s": {"m": [{"time": 0, "vertices": [1, 1]}, {"time": 1}]}}},
            # a key without a mix sets it to 1, so these keys leave x, y and shearY at 0
            "transform": {"tc": [{"time": 0, "mixRotate": 0, "mixX": 0, "mixScaleX": 0.25, "mixShearY": 0},
                                 {"time": 1, "mixRotate": 0, "mixX": 0, "mixScaleX": 0.25, "mixScaleY": 0.5,
                                  "mixShearY": 0}]},
        }},
    }


def test_stamps_and_runtimes():
    assert spine4x.RUNTIMES == ["4.3", "4.2", "4.1", "4.0", "3.8"]
    assert spine4x.stamp_for("4.3").startswith("4.3.")
    assert spine4x.stamp_for("4.0") == "4.0.64"
    with pytest.raises(ValueError):
        spine4x.stamp_for("4.4")
    with pytest.raises(ValueError):
        spine4x.upgrade(_rig_40(), "4.4.1")
    assert spine4x.upgrade(_rig_40(), "4.0.64") == _rig_40()


def test_41_moves_deform_keys_and_renames_the_linked_mesh_flag():
    data = spine4x.upgrade(_rig_40(), spine4x.STAMP_41)
    assert data["skeleton"]["spine"] == spine4x.STAMP_41
    anim = data["animations"]["a"]
    assert "deform" not in anim
    assert anim["attachments"]["default"]["s"]["m"]["deform"][0] == {"time": 0, "vertices": [1, 1]}
    linked = data["skins"][0]["attachments"]["s"]["l"]
    assert linked["timelines"] is False and "deform" not in linked and linked["parent"] == "m"
    assert data["bones"][1]["transform"] == "noScale" and "ik" in data  # 4.2 and 4.3 changes not applied


def test_42_names_the_transform_mode_inherit():
    data = spine4x.upgrade(_rig_40(), spine4x.STAMP_42)
    assert data["bones"][1] == {"name": "a", "parent": "root", "inherit": "noScale"}
    assert "constraints" not in data and data["ik"][0]["uniform"] is True


def test_43_lists_constraints_in_update_order_with_the_new_fields():
    source = _rig_40()
    data = spine4x.upgrade(source, spine4x.STAMP_43)
    assert source == _rig_40()  # the input is left alone
    assert not {"ik", "transform", "path"} & data.keys()
    tc, pc, ik = data["constraints"]
    assert (tc["type"], pc["type"], ik["type"]) == ("transform", "path", "ik")
    assert not any("order" in c for c in data["constraints"])
    assert ik == {"type": "ik", "name": "ik", "bones": ["b"], "target": "c", "stretch": True, "scaleY": "uniform"}
    assert pc == {"type": "path", "name": "pc", "slot": "p", "bones": ["c"]}
    assert tc["source"] == "c" and "target" not in tc
    assert tc["localSource"] is tc["localTarget"] is tc["additive"] is True
    assert not {"local", "relative"} & tc.keys()
    # rotate has a mix; scaleX and scaleY are keyed; x, y and shearY never move anything
    assert tc["properties"] == {"rotate": {"to": {"rotate": {}}}, "scaleX": {"to": {"scaleX": {}}},
                                "scaleY": {"to": {"scaleY": {}}}}
    keys = data["animations"]["a"]["transform"]["tc"]
    assert keys[0]["mixScaleY"] == 0.25 and keys[1]["mixScaleY"] == 0.5  # 4.3 defaults it to 1
    linked = data["skins"][0]["attachments"]["s"]["l"]
    assert linked["source"] == "m" and "parent" not in linked and linked["timelines"] is False


def test_constraints_with_the_same_order_keep_the_40_update_order():
    data = _rig_40()
    for kind in ("ik", "transform", "path"):
        for c in data[kind]:
            c["order"] = 0
    assert [c["type"] for c in spine4x.upgrade(data, spine4x.STAMP_43)["constraints"]] == ["ik", "transform", "path"]


def test_the_shear_note_names_world_shear_that_a_later_constraint_reads():
    data = _rig_40()
    data["transform"][0].update(local=False, relative=False, mixShearY=1)
    for stamp in (spine4x.STAMP_42, spine4x.STAMP_43):
        notes = spine4x.runtime_notes(data, stamp)
        assert len(notes) == 1 and "'tc'" in notes[0] and "ik" in notes[0] and "opposite sign" in notes[0]
    assert spine4x.runtime_notes(data, spine4x.STAMP_41) == []  # 4.1 applies them like 4.0
    data["ik"][0]["order"] = -1  # the IK now comes first, before there is any shear
    assert spine4x.runtime_notes(data, spine4x.STAMP_43) == []
    data["ik"][0]["order"] = 2
    data["transform"][0]["mixShearY"] = 0
    assert spine4x.runtime_notes(data, spine4x.STAMP_43) == []
    data["animations"]["a"]["transform"]["tc"][1]["mixShearY"] = 0.5  # keyed shear counts too
    assert len(spine4x.runtime_notes(data, spine4x.STAMP_43)) == 1
    data["animations"]["a"]["transform"]["tc"][1]["mixShearY"] = 0
    assert spine4x.runtime_notes(data, spine4x.STAMP_43) == []
    key = data["animations"]["a"]["transform"]["tc"][0]
    curve = [0.25, 0, 0.75, 0] * 6  # a straight curve for every channel, then shearY's handles rise
    curve[4 * 5 + 1] = curve[4 * 5 + 3] = 0.5
    key["curve"] = curve
    assert len(spine4x.runtime_notes(data, spine4x.STAMP_43)) == 1  # shear along a curve counts too


def test_the_local_mix_note_names_partway_rotation_in_local_absolute_constraints():
    data = _rig_40()
    data["transform"][0].update(local=True, relative=False, mixRotate=0.5)
    notes = spine4x.runtime_notes(data, spine4x.STAMP_42)
    assert len(notes) == 1 and "'tc'" in notes[0] and "rotate partway" in notes[0]
    data["transform"][0]["relative"] = True  # relative constraints add, so nothing changed for them
    assert spine4x.runtime_notes(data, spine4x.STAMP_42) == []
    data["transform"][0].update(relative=False, mixRotate=1)  # all the way lands on the same angle
    assert spine4x.runtime_notes(data, spine4x.STAMP_42) == []
    data["animations"]["a"]["transform"]["tc"][0]["mixShearY"] = 0.5
    assert "shear partway" in spine4x.runtime_notes(data, spine4x.STAMP_42)[0]


# --- the editor's export -------------------------------------------------------------------------

@pytest.fixture
def boy(tmp_path) -> Document:
    if not (V40 / "spineboy-pro.json").is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    doc, _ = Document.import_spine_json(V40 / "spineboy-pro.json")
    return doc


@pytest.mark.parametrize("version", ["4.1", "4.2", "4.3"])
def test_export_writes_the_layout_of_the_chosen_version(boy, tmp_path, version):
    out = tmp_path / f"{version}" / "boy.json"
    boy.export_spine_json(out, stamp=spine4x.stamp_for(version), copy_images=False, nonessential=False, cleanup=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["skeleton"]["spine"] == spine4x.stamp_for(version)
    assert not any("deform" in anim for anim in data["animations"].values())
    assert ("constraints" in data) == (version == "4.3")
    assert ("ik" in data) == (version != "4.3")
    assert all("transform" not in b for b in data["bones"]) or version == "4.1"
    # the same as converting the 4.0 export
    base = tmp_path / "40" / "boy.json"
    boy.export_spine_json(base, copy_images=False, nonessential=False, cleanup=True)
    assert data == spine4x.upgrade(json.loads(base.read_text(encoding="utf-8")), spine4x.stamp_for(version))


def test_an_unknown_4x_version_is_an_editor_error(boy, tmp_path):
    out = tmp_path / "boy.json"
    for stamp in ("4.4.1", "4.x"):
        with pytest.raises(EditorError, match="4"):
            boy.export_spine_json(out, stamp=stamp, copy_images=False)
        with pytest.raises(EditorError):
            boy.export_data(stamp)
    assert not out.exists()


def test_binary_export_stays_40_only(boy, tmp_path):
    with pytest.raises(EditorError, match="Spine 4.0 only"):
        boy.export_spine_json(tmp_path / "boy.skel", stamp=spine4x.STAMP_43, binary=True, copy_images=False)


def test_export_notes_what_the_newer_runtimes_apply_differently(boy):
    shoulder = next(c for c in boy.data["transform"] if c["name"] == "shoulder")
    shoulder.update(mixShearY=1, shearY=12)  # shears back-shoulder, under which aim-ik moves rear-upper-arm
    _, notes = boy.export_data(spine4x.STAMP_43)
    assert any("'shoulder'" in n and "opposite sign" in n for n in notes)
    _, notes = boy.export_data()
    assert not any("opposite sign" in n for n in notes)


def test_engine_export_for_43(tmp_path, library, make_settings):
    from anim2d.packaging.engine_export import EngineExport, export_for_engine
    preset, settings = library.get("star_rating"), make_settings("star_rating", "fx_stars_43")
    result = export_for_engine(preset, settings, tmp_path, EngineExport(version="4.3", unity=True))
    assert result.ok, result.report.errors
    data = json.loads((result.output_dir / "fx_stars_43.json").read_text(encoding="utf-8"))
    assert data["skeleton"]["spine"] == spine4x.STAMP_43
    if HAVE_4X_RUNTIMES:
        assert _sample(result.output_dir / "fx_stars_43.json", "4.3", 3)["version"] == spine4x.STAMP_43
    bad = export_for_engine(preset, settings, tmp_path / "a", EngineExport(version="4.4"))
    assert not bad.ok and "4.4" in bad.report.errors[0]
    binary = export_for_engine(preset, settings, tmp_path / "b", EngineExport(version="4.3", binary=True))
    assert not binary.ok and "Spine 4.0 only" in binary.report.errors[0]


# --- parity in the official runtimes -------------------------------------------------------------

@needs_4x
@pytest.mark.parametrize("name", RIGS)
def test_exports_play_the_same_in_the_41_to_43_runtimes(name, tmp_path):
    """Each official example rig, opened in Anim2D and exported for 4.0, 4.1, 4.2 and 4.3, poses the
    same in each version's official runtime (to the samples' rounding)."""
    source = V40 / f"{name}.json"
    if not source.is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    doc, _ = Document.import_spine_json(source)
    exported = {}
    for version in ("4.0", "4.1", "4.2", "4.3"):
        out = tmp_path / version / f"{name}.json"
        doc.export_spine_json(out, stamp=spine4x.stamp_for(version), copy_images=False)
        exported[version] = _sample(out, version)
    for version in ("4.1", "4.2", "4.3"):
        assert exported[version]["version"] == spine4x.stamp_for(version)
        difference, where = _worst(exported["4.0"], exported[version])
        assert difference <= 0.011, f"Spine {version}: {difference} at {where}"


PARTIAL = dict(mixRotate=0.5, mixX=0.7, mixY=0.3, mixScaleX=0.6)


def _variant(local: bool = False, relative: bool = False, mixes: dict | None = None, offsets: bool = False,
             ik: bool = False) -> dict:
    """spineboy with every transform constraint in the given mode. mixes: set up and keyed (a key
    without mixScaleY takes mixScaleX's). offsets: translation, rotation and scale offsets. ik:
    every IK constraint stretching, compressing, scaling uniformly and soft."""
    data = json.loads((V40 / "spineboy-pro.json").read_text(encoding="utf-8"))
    for c in data["transform"]:
        c.update(local=local, relative=relative, **(mixes or {}))
        if offsets:
            c.update(rotation=c.get("rotation", 0) + 5, x=3, y=-2, scaleX=0.2, scaleY=0.2)
    for c in data["ik"] if ik else []:
        c.update(uniform=True, stretch=True, compress=True, softness=3)
    for anim in data["animations"].values() if mixes else []:
        for keys in anim.get("transform", {}).values():
            for key in keys:
                key.pop("mixScaleY", None)
                key.update(mixes)
    return data


def _difference(tmp_path: Path, data: dict, version: str) -> tuple[float, str]:
    base, out = tmp_path / "40.json", tmp_path / f"{version}.json"
    base.write_text(json.dumps(data), encoding="utf-8")
    out.write_text(json.dumps(spine4x.upgrade(copy.deepcopy(data), spine4x.stamp_for(version))), encoding="utf-8")
    return _worst(_sample(base, "4.0"), _sample(out, version))


VARIANTS = {
    "world, partial mixes": dict(mixes=PARTIAL),
    "local relative, partial mixes": dict(local=True, relative=True, mixes=PARTIAL),
    "local, partial translate mixes": dict(local=True, mixes=dict(mixRotate=1, mixX=0.7, mixY=0.3, mixScaleX=0)),
    "world, offsets": dict(offsets=True), "relative, offsets": dict(relative=True, offsets=True),
    "local, offsets": dict(local=True, offsets=True, mixes=dict(mixRotate=1)),
    "local relative, offsets": dict(local=True, relative=True, offsets=True),
    "IK stretch, compress, uniform, softness": dict(ik=True),
    "world, offsets and IK": dict(offsets=True, ik=True),
}


@needs_4x
@pytest.mark.parametrize("variant", VARIANTS)
def test_transform_and_ik_settings_play_the_same(tmp_path, variant):
    """Local and relative transform constraints, partial and keyed mixes, offsets, and IK stretch,
    compress, uniform scale and softness, which the example rigs do not cover."""
    if not (V40 / "spineboy-pro.json").is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    data = _variant(**VARIANTS[variant])
    assert spine4x.runtime_notes(data, spine4x.STAMP_43) == []
    for version in ("4.1", "4.2", "4.3"):
        difference, where = _difference(tmp_path, data, version)
        assert difference <= 0.025, f"Spine {version}: {difference} at {where}"


CHANGES = {  # what Spine changed in its runtimes: variant, note on export, last version playing it like 4.0
    "4.2: local constraint mixing rotation partway": (dict(local=True, mixes=PARTIAL), "partway", "4.1"),
    "4.2: shear from uneven relative scaling": (dict(relative=True, mixes=PARTIAL), None, "4.1"),
    "4.3: local constraint mixing scale into scaled bones":
        (dict(local=True, mixes=dict(mixRotate=1, mixX=0, mixScaleX=0.6)), "own scale", "4.2"),
}


@needs_4x
@pytest.mark.parametrize("change", CHANGES)
def test_what_the_newer_runtimes_changed(tmp_path, change):
    """The changes in Spine's runtimes that no data can undo play like 4.0 up to a version and
    differently after it. The notes on export name those they can: shear from uneven scale depends
    on the pose, so it is left out."""
    if not (V40 / "spineboy-pro.json").is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    variant, note, same_until = CHANGES[change]
    data = _variant(**variant)
    notes = spine4x.runtime_notes(data, spine4x.STAMP_43)
    assert (any(note in n for n in notes) if note else notes == [])
    after = {"4.1": "4.2", "4.2": "4.3"}[same_until]
    assert _difference(tmp_path, data, same_until)[0] <= 0.025
    assert _difference(tmp_path, data, after)[0] > 1
    if note:
        assert not any(note in n for n in spine4x.runtime_notes(data, spine4x.stamp_for(same_until)))


@needs_4x
def test_path_constraint_partial_translate_mix_plays_the_same(tmp_path):
    """A path constraint's mixY defaults to its mixX in 4.0 and 4.3 alike, so nothing is written for
    it: writing 1 (4.2's reader also defaults to mixX) moved the legs 93 units in the 4.3 runtime."""
    source = V40 / "stretchyman-pro.json"
    if not source.is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    data = json.loads(source.read_text(encoding="utf-8"))
    for c in data["path"]:
        c["mixX"] = 0.5
        c.pop("mixY", None)
    converted = spine4x.upgrade(data, spine4x.STAMP_43)
    assert all("mixY" not in c for c in converted["constraints"] if c["type"] == "path")
    for version in ("4.1", "4.2", "4.3"):
        difference, where = _difference(tmp_path, data, version)
        assert difference <= 0.025, f"Spine {version}: {difference} at {where}"


def _curve(rotate_handles: tuple[float, float] = (0, 0)) -> list[float]:
    """A 4.0 transform key curve over its six channels (mixRotate first), every other channel flat at 0."""
    out = []
    for channel in range(6):
        cy1, cy2 = rotate_handles if channel == 0 else (0, 0)
        out += [0.0 + 0.1 * channel, cy1, 0.2 + 0.1 * channel, cy2]
    return out


def _zero_key(time: float, **extra) -> dict:
    return {"time": time, "mixRotate": 0, "mixX": 0, "mixScaleX": 0, "mixShearY": 0, **extra}


@needs_4x
def test_a_mix_only_its_bezier_handles_move_still_drives_the_property(tmp_path):
    """Keys at 0 with a Bezier curve whose handles rise to 1: the rotate mix is about 0.75 between
    them, so the 4.3 constraint needs its rotate mapping although no key or setup value is set."""
    source = V40 / "spineboy-pro.json"
    if not source.is_file():
        pytest.skip("needs tools/fetch_spine_runtime.py")
    data = json.loads(source.read_text(encoding="utf-8"))
    c = next(c for c in data["transform"] if c["name"] == "aim-torso-transform")
    c.update(mixRotate=0, mixX=0, mixScaleX=0, mixShearY=0)
    data["animations"]["aim"]["transform"]["aim-torso-transform"] = [
        _zero_key(0, curve=_curve((1, 1))), _zero_key(1)]
    converted = spine4x.upgrade(data, spine4x.STAMP_43)
    torso = next(c for c in converted["constraints"] if c["name"] == "aim-torso-transform")
    assert set(torso["properties"]) == {"rotate"}
    difference, where = _difference(tmp_path, data, "4.3")
    assert difference <= 0.025, f"{difference} at {where}"


def test_the_local_mix_note_sees_mixes_between_keys():
    data = _rig_40()
    data["transform"][0].update(local=True, relative=False, mixRotate=1, mixShearY=0)
    keys = data["animations"]["a"]["transform"]["tc"]
    keys[:] = [{"time": 0, "mixRotate": 0, "mixShearY": 0}, {"time": 1, "mixRotate": 1, "mixShearY": 0}]
    assert "rotate partway" in spine4x.runtime_notes(data, spine4x.STAMP_42)[0]  # 0.5 halfway, linearly
    keys[0]["curve"] = "stepped"  # jumps from 0 to 1: never partway
    assert spine4x.runtime_notes(data, spine4x.STAMP_42) == []
    keys[:] = [{"time": 0, "mixRotate": 1, "mixShearY": 0, "curve": _curve((0.5, 1))},
               {"time": 1, "mixRotate": 1, "mixShearY": 0}]  # both at 1, but the curve dips to about 0.8
    assert "rotate partway" in spine4x.runtime_notes(data, spine4x.STAMP_42)[0]
    keys[0]["curve"] = _curve((1, 1))  # a curve that stays at 1
    assert spine4x.runtime_notes(data, spine4x.STAMP_42) == []
    keys[0]["mixRotate"] = keys[1]["mixRotate"] = 2  # whole mixes turn by whole turns: the same
    keys[0]["curve"] = _curve((2, 2))
    assert spine4x.runtime_notes(data, spine4x.STAMP_42) == []


def _scale_rig() -> dict:
    """root > s (scaleX 2) and root > a (scale 1); 'copy' is local and absolute and mixes a's scale."""
    return {
        "skeleton": {"spine": "4.0.64"},
        "bones": [{"name": "root"}, {"name": "s", "parent": "root", "scaleX": 2}, {"name": "a", "parent": "root"}],
        "slots": [],
        "transform": [{"name": "copy", "order": 1, "bones": ["a"], "target": "root", "local": True,
                       "mixRotate": 0, "mixX": 0, "mixScaleX": 1, "mixShearY": 0}],
        "skins": [{"name": "default", "attachments": {}}],
        "animations": {"a": {}},
    }


def test_the_scale_note_counts_scale_set_by_an_earlier_constraint():
    data = _scale_rig()
    assert not any("own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))  # a is unscaled
    data["transform"].insert(0, {"name": "grow", "order": 0, "bones": ["a"], "target": "s", "local": True,
                                 "relative": True, "mixRotate": 0, "mixX": 0, "mixScaleX": 1, "mixShearY": 0})
    notes = spine4x.runtime_notes(data, spine4x.STAMP_43)  # 'grow' scales a before 'copy' runs
    assert any("'copy'" in n and "own scale" in n for n in notes)
    data["transform"][0]["order"] = 2  # after 'copy': a still has scale 1 when 'copy' runs
    assert not any("'copy'" in n and "own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    data["transform"][0]["order"] = 0
    data["transform"][0]["mixScaleX"] = 0  # 'grow' no longer scales
    assert not any("own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    data["ik"] = [{"name": "reach", "order": 0, "bones": ["a"], "target": "s", "stretch": True}]  # IK stretch scales
    assert any("'copy'" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    data["ik"][0]["stretch"] = False
    assert not any("'copy'" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    data["animations"]["a"]["ik"] = {"reach": [{"time": 0.5, "stretch": True}]}  # stretch turned on by a key
    assert any("'copy'" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))


def test_the_scale_note_counts_scale_mixes_and_bone_scales_along_curves():
    data = _scale_rig()
    data["bones"][2]["scaleX"] = 2  # a is scaled
    data["transform"][0]["mixScaleX"] = 0
    data["animations"]["a"]["transform"] = {"copy": [
        {"time": 0, "mixRotate": 0, "mixX": 0, "mixScaleX": 0, "mixShearY": 0,
         "curve": [0.1, 0, 0.2, 0, 0.2, 0, 0.3, 0, 0.3, 0, 0.4, 0, 0.4, 1, 0.5, 1, 0.5, 0, 0.6, 0, 0.6, 0, 0.7, 0]},
        {"time": 1, "mixRotate": 0, "mixX": 0, "mixScaleX": 0, "mixShearY": 0}]}  # only mixScaleX's handles rise
    assert any("'copy'" in n and "own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    assert "scaleX" in spine4x.upgrade(data, spine4x.STAMP_43)["constraints"][0]["properties"]
    data["bones"][2].pop("scaleX")  # unscaled, but a scale key's curve leaves 1 between keys at 1
    assert not any("own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
    data["animations"]["a"]["bones"] = {"a": {"scale": [{"time": 0, "curve": [0.3, 2, 0.6, 2, 0.3, 1, 0.6, 1]},
                                                         {"time": 1}]}}
    assert any("'copy'" in n and "own scale" in n for n in spine4x.runtime_notes(data, spine4x.STAMP_43))
