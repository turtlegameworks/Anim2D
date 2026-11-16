"""Spine 4.2 and 4.3 JSON export (formats.spine42), checked against the official readers' field names."""

from __future__ import annotations

import json

import pytest

from anim2d.editor.document import Document, EditorError
from anim2d.formats import spine42
from anim2d.formats.spine42 import STAMP_42, STAMP_43


def data40() -> dict:
    return {
        "skeleton": {"spine": "4.0.64"},
        "bones": [{"name": "root"}, {"name": "a", "parent": "root", "transform": "noScale"},
                  {"name": "b", "parent": "a", "transform": "normal"}, {"name": "t", "parent": "root"}],
        "slots": [{"name": "s", "bone": "a", "attachment": "m"}, {"name": "p", "bone": "root"}],
        "ik": [{"name": "leg", "order": 2, "bones": ["a", "b"], "target": "t", "uniform": True, "stretch": True}],
        "transform": [{"name": "follow", "order": 0, "bones": ["b"], "target": "t", "local": True, "relative": True,
                       "rotation": 15, "mixRotate": 0.5, "mixX": 0, "mixScaleX": 0.25, "mixShearY": 0}],
        "path": [{"name": "rail", "order": 1, "bones": ["b"], "target": "p", "mixX": 0.5}],
        "skins": [{"name": "default", "attachments": {"s": {
            "m": {"type": "mesh", "uvs": [0, 0, 1, 0, 0, 1], "vertices": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2],
                  "hull": 3},
            "copy": {"type": "linkedmesh", "parent": "m", "deform": False}}}}],
        "animations": {"run": {
            "bones": {"a": {"rotate": [{"value": 10}]}},
            "transform": {"follow": [{"time": 0, "mixRotate": 0.2, "mixX": 0.3, "mixScaleX": 0.4}]},
            "deform": {"default": {"s": {"m": [{"time": 0}, {"time": 1, "vertices": [1, 1]}]}}}}},
    }


def test_42_layout():
    out, notes = spine42.upgrade(data40(), STAMP_42)
    assert out["skeleton"]["spine"] == STAMP_42 and notes == []
    bones = {b["name"]: b for b in out["bones"]}
    assert bones["a"]["inherit"] == "noScale" and "transform" not in bones["a"]
    assert "inherit" not in bones["b"] and "transform" not in bones["b"]  # normal is the default
    copy = out["skins"][0]["attachments"]["s"]["copy"]
    assert copy == {"type": "linkedmesh", "parent": "m", "timelines": False}
    anim = out["animations"]["run"]
    assert "deform" not in anim
    assert anim["attachments"]["default"]["s"]["m"]["deform"][1]["vertices"] == [1, 1]
    assert [c["name"] for c in out["ik"]] == ["leg"] and out["ik"][0]["uniform"] is True  # constraints unchanged
    assert out["transform"][0]["target"] == "t"


def test_43_constraints_in_order():
    out, _ = spine42.upgrade(data40(), STAMP_43)
    assert not {"ik", "transform", "path"} & set(out)
    assert list(out)[:4] == ["skeleton", "bones", "slots", "constraints"]
    assert [(c["type"], c["name"]) for c in out["constraints"]] == \
        [("transform", "follow"), ("path", "rail"), ("ik", "leg")]  # by 4.2's "order"
    follow, rail, leg = out["constraints"]
    assert "order" not in follow and "order" not in leg
    assert leg["scaleY"] == "uniform" and "uniform" not in leg and leg["stretch"] is True
    assert rail["slot"] == "p" and "target" not in rail and rail["mixY"] == 1  # 4.2 default, written out
    assert follow["source"] == "t" and "target" not in follow
    assert follow["localSource"] and follow["localTarget"] and follow["additive"]
    assert follow["rotation"] == 15
    assert set(follow["properties"]) == set(spine42.TRANSFORM_PROPERTIES)
    assert all(list(v["to"]) == [k] for k, v in follow["properties"].items())  # each to the same property
    assert (follow["mixRotate"], follow["mixX"], follow["mixY"], follow["mixScaleX"], follow["mixScaleY"],
            follow["mixShearY"]) == (0.5, 0, 0, 0.25, 0.25, 0)  # 4.2 defaults resolved
    key = out["animations"]["run"]["transform"]["follow"][0]
    assert key["mixY"] == 0.3 and key["mixScaleY"] == 0.4  # 4.3 would default these to 1
    assert out["skins"][0]["attachments"]["s"]["copy"]["source"] == "m"
    assert "attachments" in out["animations"]["run"] and "inherit" in out["bones"][1]  # 4.2 changes too


def test_version_of():
    assert spine42.version_of("4.3.75-beta") == (4, 3)
    assert spine42.version_of("3.8.99") == (3, 8)
    assert spine42.version_of("x") == (0, 0)


@pytest.fixture
def doc(tmp_path) -> Document:
    d = Document()
    d.add_bone("root", "arm", x=10, length=50)
    d.add_bone("arm", "hand", x=50, length=20)
    d.add_bone("root", "target", x=70, y=-20)
    d.add_ik(["arm", "hand"], "target")
    d.add_animation("wave")
    d.set_bone_key("wave", "arm", "rotate", 0.0, value=0)
    d.set_bone_key("wave", "arm", "rotate", 1.0, value=30)
    return d


@pytest.mark.parametrize("stamp,major_minor", [(STAMP_42, "4.2"), (STAMP_43, "4.3")])
def test_document_export(doc, tmp_path, stamp, major_minor):
    out = tmp_path / f"{major_minor}" / "skeleton.json"
    doc.export_spine_json(out, stamp)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["skeleton"]["spine"].startswith(major_minor)
    assert data["animations"]["wave"]["bones"]["arm"]["rotate"][1]["value"] == 30
    if major_minor == "4.3":
        assert data["constraints"][0]["type"] == "ik" and "ik" not in data
    else:
        assert data["ik"][0]["target"] == "target"


def test_binary_stays_40(doc, tmp_path):
    with pytest.raises(EditorError, match="4.0 only"):
        doc.export_spine_json(tmp_path / "s.skel", STAMP_43, binary=True)


def test_unity_bridge_versions():
    from anim2d.editor import unity_bridge
    assert unity_bridge.RUNTIME_STAMPS["4.3"] == STAMP_43 and unity_bridge.RUNTIME_STAMPS["4.2"] == STAMP_42
