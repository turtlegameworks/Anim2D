"""Retime: scale, reverse and loop a section, keeping the pose at matching times (curves included)."""

from __future__ import annotations

import pytest

from anim2d.editor.document import Document, EditorError, KeyRef
from anim2d.editor.retime import loop_section, reverse, scale_time


def _doc() -> Document:
    d = Document()
    d.add_bone("root", "arm", length=50)
    d.add_animation("wave")
    d.set_bone_key("wave", "arm", "rotate", 0, value=0)
    d.set_bone_key("wave", "arm", "rotate", 0.5, value=60)
    d.set_bone_key("wave", "arm", "rotate", 1.0, value=10)
    d.set_bone_key("wave", "arm", "translate", 0.25, x=0, y=0)
    d.set_bone_key("wave", "arm", "translate", 1.0, x=40, y=20)
    refs = [KeyRef("bones", "arm", "rotate", 0.0), KeyRef("bones", "arm", "rotate", 0.5)]
    d.set_curve("wave", refs, (0.15, 0.7, 0.35, 1.0))  # ease out on both rotate segments
    return d


def _pose(d: Document, t: float) -> tuple[float, ...]:
    return tuple(round(v, 3) for v in d.evaluator("wave").bone_locals(t)["arm"][:3])


def test_scale_keeps_the_motion():
    d = _doc()
    before = [_pose(d, t) for t in (0.1, 0.3, 0.5, 0.8, 1.0)]
    assert scale_time(d, "wave", 2.0) == 5
    assert d.animation_duration("wave") == pytest.approx(2.0)
    after = [_pose(d, t * 2) for t in (0.1, 0.3, 0.5, 0.8, 1.0)]
    assert after == pytest.approx(before, abs=1e-2)
    d.undo()
    assert d.animation_duration("wave") == pytest.approx(1.0)
    with pytest.raises(EditorError):
        scale_time(d, "wave", 0)


def test_reverse_plays_backwards():
    d = _doc()
    before = {t: _pose(d, t) for t in (0.0, 0.2, 0.35, 0.5, 0.7, 1.0)}
    reverse(d, "wave")
    for t, pose in before.items():
        assert _pose(d, 1.0 - t) == pytest.approx(pose, abs=1e-2), t


def test_scale_selected_keys_and_loop():
    d = _doc()
    scale_time(d, "wave", 0.5, refs=[KeyRef("bones", "arm", "rotate", 0.5), KeyRef("bones", "arm", "rotate", 1.0)])
    times = [k.get("time", 0) for k in d.animation("wave")["bones"]["arm"]["rotate"]]
    assert times == pytest.approx([0, 0.5, 0.75]), "pivot is the first selected key"
    d.undo()
    added = loop_section(d, "wave", 0.0, 1.0, times=2)
    assert added == 6  # rotate 0, 0.5 and translate 0.25, twice
    assert d.animation_duration("wave") == pytest.approx(3.0)
    for t in (0.1, 0.3, 0.6, 0.9):  # each repeat moves like the one before it
        assert _pose(d, t + 1) == pytest.approx(_pose(d, t), abs=1e-2), t
    assert _pose(d, 3.0) == pytest.approx((40, 20, 10), abs=1e-2), "the old end key comes after the repeats"
    with pytest.raises(EditorError):
        loop_section(d, "wave", 0.5, 0.5)
