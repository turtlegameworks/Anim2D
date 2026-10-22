"""Animation core: graph editing (key values, bezier handles, inserting keys on a curve), copy and
paste of keys, and crossfade preview, with the motion checked against the official 4.0 runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from anim2d.editor.document import Document, EditorError, KeyRef
from anim2d.formats import curves as cv
from anim2d.preview.evaluator import MixEvaluator, PoseEvaluator, _wrap180

VENDOR = Path(__file__).parent / "runtime" / "vendor"
CORE40 = VENDOR / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")
EASE = (0.45, 0.0, 0.55, 1.0)


def run(path: Path, times, anim: str | None = None, mix: str | None = None) -> list[dict]:
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(path), "--full"]
    if anim:
        args.append(f"--anim={anim}")
    if mix:
        args.append(f"--mix={mix}")
    out = subprocess.run(args + [str(t) for t in times], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)["samples"]


@pytest.fixture
def doc() -> Document:
    d = Document()
    d.add_bone("root", "arm", x=10, length=50)
    d.add_bone("arm", "hand", x=50, length=20)
    d.add_animation("wave")
    for t, v in ((0, 0), (0.5, 60), (1.0, -20)):
        d.set_bone_key("wave", "arm", "rotate", t, value=v)
    d.set_bone_key("wave", "arm", "translate", 0, x=0, y=0)
    d.set_bone_key("wave", "arm", "translate", 0.8, x=30, y=-10)
    d.set_curve("wave", [KeyRef("bones", "arm", "rotate", 0)], EASE)
    return d


def rot(doc: Document, t: float, anim: str = "wave", bone: str = "arm") -> float:
    return PoseEvaluator(doc.data, anim).bone_locals(t)[bone][2]


ROTATE = KeyRef("bones", "arm", "rotate", 0)


# --- graph editing -------------------------------------------------------------------------

def test_key_channels_and_names(doc):
    keys = doc.timeline_keys("wave", ROTATE)
    assert [doc.key_channels(ROTATE, k) for k in keys] == [[0.0], [60.0], [-20.0]]
    translate = KeyRef("bones", "arm", "translate", 0)
    assert doc.key_channels(translate, doc.timeline_keys("wave", translate)[1]) == [30.0, -10.0]


def test_editing_a_value_keeps_the_curve_shape(doc):
    before = doc.timeline_keys("wave", ROTATE)[0]["curve"]
    norm = cv.to_normalized(tuple(before), 0, 0, 0.5, 60)
    doc.set_key_channel("wave", KeyRef("bones", "arm", "rotate", 0.5), 0, 90)
    key0, key1 = doc.timeline_keys("wave", ROTATE)[:2]
    assert key1["value"] == 90
    assert cv.to_normalized(tuple(key0["curve"]), 0, 0, 0.5, 90) == pytest.approx(norm, abs=1e-4)
    assert rot(doc, 0.5) == pytest.approx(90)
    doc.undo()
    assert doc.timeline_keys("wave", ROTATE)[1]["value"] == 60

    translate = KeyRef("bones", "arm", "translate", 0.8)
    doc.set_key_channel("wave", translate, 1, 25)
    assert doc.timeline_keys("wave", translate)[1] == {"time": 0.8, "x": 30, "y": 25}
    with pytest.raises(EditorError, match="no longer exists"):
        doc.set_key_channel("wave", KeyRef("bones", "arm", "rotate", 0.3), 0, 1)


def test_color_and_ik_channels():
    d = Document()
    d.add_bone("root", "a", length=40)
    d.add_bone("a", "b", x=40, length=40)
    d.add_slot("a", "s")
    d.add_animation("anim")
    d.set_slot_key("anim", "s", "color", 0, "ff0000ff")
    d.set_slot_key("anim", "s", "color", 1, "00ff00ff")
    color = KeyRef("slots", "s", "rgba", 0)
    d.set_key_channel("anim", color, 3, 0.5)
    d.set_key_channel("anim", color, 2, 1.7)  # clamped
    assert d.timeline_keys("anim", color)[0]["color"] == "ff00ff80"
    d.add_ik_with_target(["a", "b"], "leg")
    d.set_ik_key("anim", "leg", 0, mix=1)
    d.set_ik_key("anim", "leg", 1, mix=0.2)
    ik = KeyRef("ik", "leg", "ik", 1)
    d.set_key_channel("anim", ik, 0, 0.75)
    d.set_key_channel("anim", ik, 1, 12)
    assert (d.timeline_keys("anim", ik)[1]["mix"], d.timeline_keys("anim", ik)[1]["softness"]) == (0.75, 12)


def test_handles_turn_linear_into_bezier_and_clamp(doc):
    seg = KeyRef("bones", "arm", "rotate", 0.5)  # linear 60 -> -20 over 0.5..1.0
    assert rot(doc, 0.75) == pytest.approx(20)
    doc.set_key_handles("wave", seg, 0, (0.9, 60, 1.0, -20))  # hold near 60, then drop
    curve = doc.timeline_keys("wave", ROTATE)[1]["curve"]
    assert curve == [0.9, 60, 1.0, -20]
    assert rot(doc, 0.75) > 40
    doc.set_key_handles("wave", seg, 0, (0.2, 60, 1.4, -20))
    assert doc.timeline_keys("wave", ROTATE)[1]["curve"][0::2] == [0.5, 1.0], "handle times stay in the segment"

    translate = KeyRef("bones", "arm", "translate", 0)
    doc.set_key_handles("wave", translate, 1, (0.1, -10, 0.2, -10))
    curve = doc.timeline_keys("wave", translate)[0]["curve"]
    assert len(curve) == 8 and curve[:4] == pytest.approx([0.8 / 3, 10, 1.6 / 3, 20], abs=1e-4), \
        "the untouched x channel stays a straight line"
    with pytest.raises(EditorError, match="last key"):
        doc.set_key_handles("wave", KeyRef("bones", "arm", "rotate", 1.0), 0, (1, 0, 1, 0))


@pytest.mark.parametrize("t", [0.1, 0.25, 0.42, 0.6, 0.93, 1.3])
def test_insert_key_keeps_the_motion(doc, t):
    doc.set_curve("wave", [KeyRef("bones", "arm", "translate", 0)], (0.1, 0.9, 0.3, 1.0))
    samples = [i / 40 for i in range(0, 61)]
    before = [PoseEvaluator(doc.data, "wave").bone_locals(s)["arm"] for s in samples]
    for timeline in ("rotate", "translate"):
        doc.insert_key("wave", KeyRef("bones", "arm", timeline, 0), t)
    assert any(abs(k["time"] - t) < 1e-6 for k in doc.timeline_keys("wave", ROTATE))
    after = [PoseEvaluator(doc.data, "wave").bone_locals(s)["arm"] for s in samples]
    # The runtime samples each bezier segment at 9 points. After a split each half is sampled more
    # finely, so the motion moves slightly towards the true curve: allow 1% of the value range.
    for s, b, a in zip(samples, before, after):
        assert a[0] == pytest.approx(b[0], abs=0.3) and a[1] == pytest.approx(b[1], abs=0.1), s
        assert a[2] == pytest.approx(b[2], abs=0.8), s


def test_insert_key_on_stepped_and_existing_keys(doc):
    doc.set_curve("wave", [KeyRef("bones", "arm", "rotate", 0.5)], "stepped")
    ref = doc.insert_key("wave", ROTATE, 0.7)
    keys = doc.timeline_keys("wave", ROTATE)
    assert [k["time"] for k in keys] == [0, 0.5, 0.7, 1.0]
    assert keys[1]["curve"] == keys[2]["curve"] == "stepped" and keys[2]["value"] == 60
    assert doc.insert_key("wave", ROTATE, 0.5) == KeyRef("bones", "arm", "rotate", 0.5)
    assert len(doc.timeline_keys("wave", ROTATE)) == 4
    assert ref.time == 0.7
    with pytest.raises(EditorError, match="cannot be added"):
        doc.insert_key("wave", KeyRef("events", "", "events", 0), 0.2)


@needs_runtime
def test_inserted_keys_play_alike_in_the_runtime(tmp_path, doc):
    doc.add_slot("arm", "glow")
    doc.set_slot_key("wave", "glow", "color", 0, "ffffffff")
    doc.set_slot_key("wave", "glow", "color", 1, "ff000040")
    doc.set_curve("wave", [KeyRef("slots", "glow", "rgba", 0)], (0.6, 0.0, 0.85, 0.3))
    times = [0.0503, 0.2003, 0.3337, 0.5503, 0.7003, 0.9337]
    first = tmp_path / "before.json"
    doc.export_spine_json(first, copy_images=False)
    for row in (ROTATE, KeyRef("bones", "arm", "translate", 0), KeyRef("slots", "glow", "rgba", 0)):
        doc.insert_key("wave", row, 0.3)
        doc.insert_key("wave", row, 0.62)
    second = tmp_path / "after.json"
    doc.export_spine_json(second, copy_images=False)
    for a, b in zip(run(first, times, "wave"), run(second, times, "wave"), strict=True):
        for bone, values in a["bones"].items():
            assert values[:2] == pytest.approx(b["bones"][bone][:2], abs=0.3), (a["time"], bone)
        assert a["slots"]["glow"]["color"] == pytest.approx(b["slots"]["glow"]["color"], abs=0.01)


def test_insert_deform_key_keeps_the_mesh():
    data = {
        "skeleton": {"spine": "4.0.64"}, "bones": [{"name": "root"}],
        "slots": [{"name": "s", "bone": "root", "attachment": "m"}],
        "skins": [{"name": "default", "attachments": {"s": {"m": {
            "type": "mesh", "uvs": [0, 0, 1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2, 2, 3, 0], "hull": 4,
            "vertices": [0, 0, 10, 0, 10, 10, 0, 10], "width": 10, "height": 10}}}}],
        "animations": {"a": {"deform": {"default": {"s": {"m": [
            {"time": 0, "curve": [0.3, 0.0, 0.5, 1.0]},
            {"time": 1, "offset": 2, "vertices": [4, 6, 8, 2]}]}}}}},
    }
    d = Document(data)
    row = KeyRef("deform", "s\nm", "deform", 0)
    times = [i / 20 for i in range(21)]
    before = [PoseEvaluator(d.data, "a").mesh_world_vertices("s", "m", t) for t in times]
    d.insert_key("a", row, 0.35)
    keys = d.timeline_keys("a", row)
    assert len(keys) == 3 and "vertices" in keys[1] and isinstance(keys[1]["curve"], list)
    after = [PoseEvaluator(d.data, "a").mesh_world_vertices("s", "m", t) for t in times]
    for t, b, a in zip(times, before, after):
        for (bx, by), (ax, ay) in zip(b, a):
            assert (ax, ay) == pytest.approx((bx, by), abs=0.05), t


# --- copy and paste ------------------------------------------------------------------------

def test_copy_paste_keeps_timing_and_curve_shape(doc):
    refs = [KeyRef("bones", "arm", "rotate", t) for t in (0, 0.5)]
    clip = doc.copy_keys("wave", refs)
    json.dumps(clip)  # plain data
    pasted = doc.paste_keys("wave", clip, 2.0)
    assert {r.time for r in pasted} == {2.0, 2.5}
    keys = doc.timeline_keys("wave", ROTATE)
    assert [k["time"] for k in keys] == [0, 0.5, 1.0, 2.0, 2.5]
    for dt in (0.1, 0.2, 0.37):
        assert rot(doc, 2.0 + dt) == pytest.approx(rot(doc, dt), abs=1e-3)
    assert "curve" not in keys[-1], "the last key has no segment"

    doc.paste_keys("wave", clip, 0.0, owner="hand")  # onto another bone
    assert rot(doc, 0.2, bone="hand") == pytest.approx(rot(doc, 0.2))
    with pytest.raises(EditorError, match="several bones"):
        doc.paste_keys("wave", doc.copy_keys("wave", refs + [KeyRef("bones", "hand", "rotate", 0)]), 0, owner="arm")


def test_paste_replaces_keys_and_undoes(doc):
    clip = doc.copy_keys("wave", [KeyRef("bones", "arm", "rotate", 0.5)])
    doc.paste_keys("wave", clip, 1.0)
    assert [k["value"] for k in doc.timeline_keys("wave", ROTATE)] == [0, 60, 60]
    doc.undo()
    assert [k["value"] for k in doc.timeline_keys("wave", ROTATE)] == [0, 60, -20]


def test_paste_events_and_missing_rows(doc):
    doc.add_event("step")
    doc.key_event("wave", "step", 0.25)
    clip = doc.copy_keys("wave", [KeyRef("events", "", "events", 0.25)])
    doc.paste_keys("wave", clip, 0.25)
    assert len(doc.data["animations"]["wave"]["events"]) == 2, "events are added, not replaced"
    bone_clip = doc.copy_keys("wave", [KeyRef("bones", "arm", "translate", 0)])
    doc.delete_bone("arm")
    undo_depth = len(doc._undo)
    with pytest.raises(EditorError, match="no longer exist"):
        doc.paste_keys("wave", bone_clip, 0)
    assert len(doc._undo) == undo_depth
    with pytest.raises(EditorError, match="no keys"):
        doc.paste_keys("wave", {"format": "other"}, 0)


# --- crossfade preview ---------------------------------------------------------------------

def test_wrap180():
    assert _wrap180(190) == pytest.approx(-170)
    assert _wrap180(-190) == pytest.approx(170)
    assert _wrap180(45) == pytest.approx(45)


def test_mix_phases(doc):
    doc.add_animation("rest")
    doc.set_bone_key("rest", "arm", "rotate", 0, value=-90)
    doc.set_bone_key("rest", "arm", "rotate", 1, value=-90)
    ev = MixEvaluator(doc.data, "wave", "rest", switch=0.4, mix_duration=0.2)
    assert ev.phase(0.3) == (0.3, None, 0.0)
    assert ev.phase(0.5)[1:] == pytest.approx((0.1, 0.5))
    assert ev.phase(2.0) == (1.0, 1.0, 1.0)  # neither loops: both hold their last frame
    assert ev.bone_locals(0.3)["arm"][2] == pytest.approx(rot(doc, 0.3))
    assert ev.bone_locals(0.7)["arm"][2] == pytest.approx(-90)
    mid = ev.bone_locals(0.5)["arm"][2]
    assert mid == pytest.approx(rot(doc, 0.5) + _wrap180(-90 - rot(doc, 0.5)) * 0.5)


MIXES = [
    (VENDOR / "v40" / "spineboy-ess.json", "walk", "run", 0.4, 0.3),
    (VENDOR / "v40" / "spineboy-ess.json", "run", "jump", 0.25, 0.2),
    (VENDOR / "v40" / "spineboy-ess.json", "idle", "death", 0.5, 0.4),
    (VENDOR / "v40" / "raptor-pro.json", "walk", "roar", 0.5, 0.4),
    (VENDOR / "v40" / "raptor-pro.json", "roar", "jump", 0.3, 0.6),
]


@needs_runtime
@pytest.mark.parametrize("fixture, a, b, switch, duration", MIXES)
def test_mix_matches_official_animation_state(fixture, a, b, switch, duration):
    data = json.loads(fixture.read_text())
    ev = MixEvaluator(data, a, b, switch, duration)
    times = [round(switch + duration * f + 0.0003, 4) for f in (-0.5, 0, 0.13, 0.37, 0.5, 0.81, 1.0, 1.7)]
    samples = run(fixture, times, mix=f"{a}:{b}:{switch}:{duration}")
    worst = 0.0
    for sample in samples:
        t = sample["time"]
        world = ev.bone_world(t)
        for bone, values in sample["bones"].items():
            worst = max(worst, abs(world[bone][4] - values[0]), abs(world[bone][5] - values[1]))
        mine = {i.slot: i for i in ev.draw_items(t)}
        for slot, entry in sample["slots"].items():
            if entry["attachment"] is None:
                assert slot not in mine, (t, slot)
                continue
            assert mine[slot].attachment == entry["attachment"], (t, slot)
            assert mine[slot].color == pytest.approx(entry["color"], abs=0.01), (t, slot)
        for entry in sample["visible"]:
            if "mesh" in entry:
                verts = ev.mesh_world_vertices(entry["slot"], entry["attachment"], t)
                worst = max(worst, max(max(abs(x - entry["mesh"][2 * i]), abs(y - entry["mesh"][2 * i + 1]))
                                       for i, (x, y) in enumerate(verts)))
    assert worst < 0.05, worst
    # Mid-mix poses are neither A's nor B's, so the comparison above really tests the blending.
    mid = next(s for s in samples if abs(s["time"] - (switch + duration * 0.5 + 0.0003)) < 1e-6)
    pure_a = PoseEvaluator(data, a).bone_world(mid["time"])
    pure_b = PoseEvaluator(data, b).bone_world(mid["time"] - switch)
    for pure in (pure_a, pure_b):
        assert max(abs(pure[n][4] - v[0]) + abs(pure[n][5] - v[1]) for n, v in mid["bones"].items()) > 1
