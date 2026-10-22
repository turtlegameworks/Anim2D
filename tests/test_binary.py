"""Spine 4.0 binary (.skel) export, checked by loading it with the official spine-ts 4.0
SkeletonBinary and comparing every pose with the same data loaded from JSON."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from anim2d.formats.binary40 import BinaryExportError, _encode, _Out, _Strings, write_skel

VENDOR = Path(__file__).parent / "runtime" / "vendor"
CORE40 = VENDOR / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


def run(path: Path, anim: str | None, times) -> dict:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(path), "--full"]
    if anim:
        args.append(f"--anim={anim}")
    out = subprocess.run(args + [str(t) for t in times], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def assert_same(a: dict, b: dict, tol: float = 0.011) -> None:
    assert a["skins"] == b["skins"]
    assert [x["name"] for x in a["animations"]] == [x["name"] for x in b["animations"]]
    assert [x["duration"] for x in a["animations"]] == pytest.approx([x["duration"] for x in b["animations"]])
    for ea, eb in zip(a["events"], b["events"], strict=True):
        assert ea[0] == eb[0] and ea[1] == eb[1] and ea[3:5] == eb[3:5], ea
        assert ea[2] == pytest.approx(eb[2]) and ea[5:] == pytest.approx(eb[5:]), ea
    for sa, sb in zip(a["samples"], b["samples"], strict=True):
        where = sa["time"]
        assert sa["drawOrder"] == sb["drawOrder"], where
        for ia, ib in zip(sa["ik"], sb["ik"], strict=True):
            assert ia[:2] == pytest.approx(ib[:2], abs=1e-3) and ia[2:] == ib[2:], where
        for bone, values in sa["bones"].items():
            assert values == pytest.approx(sb["bones"][bone], abs=tol), (where, bone)
        for slot, entry in sa["slots"].items():
            other = sb["slots"][slot]
            assert entry["attachment"] == other["attachment"], (where, slot)
            assert entry["color"] == pytest.approx(other["color"], abs=0.003), (where, slot)
            assert (entry["dark"] is None) == (other["dark"] is None), (where, slot)
        va = {v["slot"]: v for v in sa["visible"]}
        vb = {v["slot"]: v for v in sb["visible"]}
        assert set(va) == set(vb), where
        for slot, entry in va.items():
            if "mesh" in entry:
                assert entry["mesh"] == pytest.approx(vb[slot]["mesh"], abs=tol), (where, slot)


def _times(duration: float) -> list[float]:
    return [round(duration * f + 0.0007, 4) for f in (0, 0.13, 0.31, 0.5, 0.62, 0.87, 0.999)]


def test_encoding_matches_libgdx_datainput():
    out = _Out(_Strings())
    out.varint(0)
    out.varint(127)
    out.varint(128)
    out.varint(-1)  # optimize positive with a negative value: 5 bytes of two's complement
    out.varint(-3, optimize_positive=False)  # zigzag
    out.string(None)
    out.string("")
    out.string("aé€")
    out.float(1.5)
    assert bytes(out.buf) == (b"\x00" b"\x7f" b"\x80\x01" b"\xff\xff\xff\xff\x0f" b"\x05" b"\x00" b"\x01"
                              b"\x07a\xc3\xa9\xe2\x82\xac" + struct.pack(">f", 1.5))
    assert _encode("\U0001F600") == b"\xed\xa0\xbd\xed\xb8\x80"  # one 3-byte form per UTF-16 unit


def test_refuses_non_40_data():
    with pytest.raises(BinaryExportError):
        write_skel({"skeleton": {"spine": "3.8.99"}})


@needs_runtime
@pytest.mark.parametrize("name", ["spineboy-ess", "spineboy-pro", "raptor-pro"])
def test_official_40_exports_load_identically_from_binary(tmp_path, name):
    source = VENDOR / "v40" / f"{name}.json"
    if not source.is_file():
        pytest.skip("run tools/fetch_spine_runtime.py")
    data = json.loads(source.read_text(encoding="utf-8"))
    skel = tmp_path / f"{name}.skel"
    skel.write_bytes(write_skel(data))
    for anim in data["animations"]:
        duration = max(_duration(data["animations"][anim]), 0.1)
        times = _times(duration)
        assert_same(run(source, anim, times), run(skel, anim, times))


def _duration(anim: dict) -> float:
    times = [0.0]

    def walk(node):
        if isinstance(node, dict):
            if "time" in node:
                times.append(float(node["time"]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(anim)
    return max(times)


def _synthetic() -> dict:
    """Everything the official example exports do not use."""
    mesh = {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2, 2, 3, 0], "hull": 4,
            "vertices": [-10, -10, 10, -10, 10, 10, -10, 10], "width": 20, "height": 20, "edges": [0, 2, 2, 4]}
    return {
        "skeleton": {"spine": "4.0.64", "fps": 30, "images": "./images/", "audio": "", "width": 100, "height": 80},
        "bones": [{"name": "root"}, {"name": "a", "parent": "root", "length": 50, "rotation": 10},
                  {"name": "b", "parent": "a", "x": 50, "transform": "noScale"},
                  {"name": "c", "parent": "root", "x": 100, "y": 5},
                  {"name": "d", "parent": "root", "color": "ff00ffff"},
                  {"name": "skinbone", "parent": "root", "skin": True}],
        "slots": [{"name": "s1", "bone": "a", "attachment": "r1", "color": "ffeeddcc"},
                  {"name": "s2", "bone": "b", "attachment": "m1", "dark": "202020", "blend": "additive"},
                  {"name": "s3", "bone": "root", "attachment": "p1"},
                  {"name": "s4", "bone": "d", "attachment": "pt"},
                  {"name": "s5", "bone": "c", "attachment": "r5", "dark": "000000", "blend": "screen"}],
        "transform": [{"name": "tc", "bones": ["c"], "target": "a", "local": True, "rotation": 15, "mixRotate": 0.5,
                       "mixX": 0.3, "mixScaleX": 0.2, "order": 1}],
        "path": [{"name": "pc", "bones": ["d"], "target": "s3", "spacingMode": "percent", "rotateMode": "chain",
                  "position": 0.25, "order": 0}],
        "skins": [
            {"name": "default", "attachments": {
                "s1": {"r1": {"width": 10, "height": 20, "x": 3, "rotation": 30}},
                "s2": {"m1": mesh},
                "s3": {"p1": {"type": "path", "vertexCount": 6, "lengths": [80, 160],
                              "vertices": [0, 0, 20, 40, 40, 40, 60, 40, 80, 0, 100, -20]}},
                "s4": {"pt": {"type": "point", "x": 4, "y": 5, "rotation": 45}},
                "s5": {"r5": {"width": 8, "height": 8, "path": "r1", "color": "80ff80ff"}}}},
            {"name": "alt", "bones": ["skinbone"], "transform": ["tc"], "attachments": {
                "s1": {"r1": {"name": "r1alt", "path": "r1", "width": 5, "height": 5}},
                "s2": {"lm": {"type": "linkedmesh", "parent": "m1", "skin": "default", "width": 20, "height": 20}},
                "s4": {"clip": {"type": "clipping", "end": "s5", "vertexCount": 3, "vertices": [0, 0, 10, 0, 0, 10]}}}},
        ],
        "events": {"e1": {"int": -2, "float": 0.5, "string": "x", "audio": "a.wav", "volume": 0.8},
                   "e2": {}},
        "animations": {"all": {
            "bones": {
                "a": {"rotate": [{"value": 0, "curve": [0.1, 10, 0.2, 30]}, {"time": 0.4, "value": 45}],
                      "translatex": [{"value": 0}, {"time": 0.3, "value": 20, "curve": "stepped"},
                                     {"time": 0.5, "value": -5}]},
                "b": {"scaley": [{"curve": [0.1, 1, 0.3, 3]}, {"time": 0.5, "value": 2}],
                      "shearx": [{"value": 5}, {"time": 0.5, "value": -10}],
                      "sheary": [{"time": 0.1, "value": 3}]},
                "c": {"translatey": [{"value": 0}, {"time": 0.5, "value": 30}],
                      "scalex": [{"value": 1}, {"time": 0.5, "value": 0.5}]},
            },
            "slots": {
                "s1": {"rgb": [{"color": "ff0000", "curve": [0.1, 1, 0.2, 0.5, 0.1, 0, 0.2, 0, 0.1, 0, 0.2, 0.5]},
                               {"time": 0.5, "color": "00ff00"}],
                       "alpha": [{"value": 1}, {"time": 0.5, "value": 0.2}],
                       "attachment": [{"time": 0.45, "name": None}]},
                "s2": {"rgba2": [{"light": "ffffffff", "dark": "000000"},
                                 {"time": 0.5, "light": "ff8080c0", "dark": "404040"}]},
                "s5": {"rgb2": [{"light": "ffffff", "dark": "000000", "curve": "stepped"},
                                {"time": 0.25, "light": "00ffff", "dark": "ff0000"}]},
            },
            "transform": {"tc": [{"mixRotate": 1, "mixX": 0.5, "curve": [0.1, 1, 0.2, 0.8] * 6},
                                 {"time": 0.5, "mixRotate": 0, "mixScaleX": 0.1}]},
            "path": {"pc": {"position": [{"value": 0}, {"time": 0.5, "value": 1}],
                            "spacing": [{"value": 0}, {"time": 0.5, "value": 0.5}],
                            "mix": [{"mixRotate": 1}, {"time": 0.5, "mixRotate": 0, "mixX": 0.2}]}},
            "deform": {"default": {"s2": {"m1": [{"vertices": [1, 2, 3, 4, 5, 6, 7, 8], "curve": [0.1, 0, 0.3, 1]},
                                                 {"time": 0.5, "offset": 2, "vertices": [5, 5]}]}}},
            "drawOrder": [{"time": 0.2, "offsets": [{"slot": "s1", "offset": 2}, {"slot": "s5", "offset": -3}]},
                          {"time": 0.4}],
            "events": [{"time": 0.1, "name": "e1", "int": 3, "string": "hi"}, {"time": 0.3, "name": "e2"}],
        }},
    }


@needs_runtime
def test_synthetic_rig_loads_identically_from_binary(tmp_path):
    data = _synthetic()
    source = tmp_path / "rig.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    skel = tmp_path / "rig.skel"
    skel.write_bytes(write_skel(data))
    times = [0, 0.05, 0.15, 0.22, 0.27, 0.35, 0.42, 0.48, 0.6]
    a, b = run(source, "all", times), run(skel, "all", times)
    assert a["bones"] == b["bones"] == 6 and a["slots"] == b["slots"] == 5
    assert_same(a, b)
    # The poses really change, so the comparison means something.
    assert a["samples"][0]["drawOrder"] != a["samples"][4]["drawOrder"]
    assert a["samples"][0]["slots"]["s1"]["color"] != a["samples"][5]["slots"]["s1"]["color"]


def test_binary_reports_missing_references():
    data = _synthetic()
    data["slots"][0]["bone"] = "nope"
    with pytest.raises(BinaryExportError, match="slot bone 'nope'"):
        write_skel(data)
