"""Physics constraints: springy bones, baking, and keeping them out of exports."""

from __future__ import annotations

import pytest

from anim2d.editor import physics
from anim2d.editor.document import Document, EditorError


def _doc() -> Document:
    d = Document()
    d.add_bone("root", "body", length=10)
    d.add_bone("body", "tail", x=10, length=50)
    d.add_animation("run")
    d.set_bone_key("run", "body", "translate", 0, x=0, y=0)
    d.set_bone_key("run", "body", "translate", 0.25, x=0, y=100)
    d.set_bone_key("run", "body", "translate", 2, x=0, y=100)
    return d


def test_tail_lags_then_settles():
    d = _doc()
    physics.add_physics(d, "tail", strength=200, damping=2)
    times, off = physics.simulate(d, "run")
    tail = off["tail"]
    assert tail[0] == pytest.approx(0, abs=1e-6)
    assert min(tail[:20]) < -5, "the tail swings down behind when the body moves up"
    assert abs(tail[-1]) < 1, "and settles"


def test_gravity_droops():
    d = _doc()
    physics.add_physics(d, "tail", gravity=50, strength=50)
    _, off = physics.simulate(d, "run")
    assert off["tail"][-1] < -5


def test_bake_and_export(tmp_path):
    d = _doc()
    name = physics.add_physics(d, "tail")
    assert physics.bake_physics(d, "run") == 1
    keys = d.animation("run")["bones"]["tail"]["rotate"]
    assert len(keys) > 50 and any(abs(k["value"]) > 1 for k in keys)
    data, notes = d.export_data("3.8.99")
    assert "physics" not in data and any("Physics" in n for n in notes)
    d.rename_bone("tail", "tail2")
    assert physics.constraint(d, name)["bone"] == "tail2"
    d.delete_bone("tail2")
    assert "physics" not in d.data
    with pytest.raises(EditorError):
        physics.add_physics(d, "root")
