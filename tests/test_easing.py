"""Ease presets, bounce/elastic eases (added keys) and curve copy/paste."""

from __future__ import annotations

import pytest

from anim2d.editor import easing
from anim2d.editor.document import Document, EditorError, KeyRef


def _doc() -> Document:
    d = Document()
    d.add_bone("root", "ball", length=20)
    d.add_animation("drop")
    d.set_bone_key("drop", "ball", "translate", 0, x=0, y=200)
    d.set_bone_key("drop", "ball", "translate", 1.0, x=0, y=0)
    d.set_bone_key("drop", "ball", "rotate", 0, value=0)
    d.set_bone_key("drop", "ball", "rotate", 1.0, value=90)
    return d


def _y(d: Document, t: float) -> float:
    return d.evaluator("drop").bone_locals(t)["ball"][1]


def test_overshoot_goes_past_the_target():
    d = _doc()
    d.set_curve("drop", [KeyRef("bones", "ball", "rotate", 0.0)], easing.EASE_PRESETS["Overshoot (back out)"])
    angles = [d.evaluator("drop").bone_locals(t / 20)["ball"][2] for t in range(21)]
    assert max(angles) > 90.5 and angles[-1] == pytest.approx(90)


def test_bounce_adds_keys_that_bounce():
    d = _doc()
    added = easing.bake_ease(d, "drop", [KeyRef("bones", "ball", "translate", 0.0)], "Bounce (adds keys)")
    assert added == 11
    ys = [_y(d, t / 40) for t in range(41)]
    assert ys[-1] == pytest.approx(0, abs=1e-6) and ys[0] == pytest.approx(200)
    rises = sum(1 for a, b in zip(ys, ys[1:]) if b > a + 0.5)
    assert rises >= 2, "it comes back up after hitting the ground"
    d.undo()
    assert len(d.animation("drop")["bones"]["ball"]["translate"]) == 2
    with pytest.raises(EditorError):
        easing.bake_ease(d, "drop", [KeyRef("bones", "ball", "translate", 1.0)], "Elastic (adds keys)")


def test_copy_and_paste_curve():
    d = _doc()
    shape = easing.EASE_PRESETS["Ease out"]
    d.set_curve("drop", [KeyRef("bones", "ball", "rotate", 0.0)], shape)
    copied = easing.copy_curve(d, "drop", KeyRef("bones", "ball", "rotate", 0.0))
    assert copied == pytest.approx(shape, abs=1e-3)
    easing.paste_curve(d, "drop", [KeyRef("bones", "ball", "translate", 0.0)], copied)
    ref_rot = [d.evaluator("drop").bone_locals(t / 10)["ball"][2] / 90 for t in range(11)]
    ref_y = [1 - _y(d, t / 10) / 200 for t in range(11)]
    assert ref_y == pytest.approx(ref_rot, abs=1e-3), "the pasted curve has the same shape"
    assert easing.copy_curve(d, "drop", KeyRef("bones", "ball", "translate", 0.0)) == pytest.approx(shape, abs=1e-3)
    with pytest.raises(EditorError, match="last key"):
        easing.copy_curve(d, "drop", KeyRef("bones", "ball", "rotate", 1.0))
