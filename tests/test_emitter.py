"""The particle_emitter generator: physics, determinism, emission timing and colour over life."""

from __future__ import annotations

import random

from anim2d.generation.emitter_generators import simulate
from anim2d.generation.registry import generate_effect

NO_FORCES = dict(gravity=0, drag=0, wind=0, turbulence=0, turbulence_frequency=1, attract_x=0, attract_y=0,
                 attract_strength=0)


def test_a_free_particle_flies_straight():
    path = simulate((0, 0), (100, 0), 1.0, NO_FORCES, random.Random(1), 4)
    assert len(path) == 5
    assert abs(path[-1][0] - 100) < 1e-6 and abs(path[-1][1]) < 1e-6


def test_gravity_pulls_down_and_drag_slows():
    fall = simulate((0, 0), (0, 0), 1.0, {**NO_FORCES, "gravity": 1000}, random.Random(1), 4)
    assert abs(fall[-1][1] + 500) < 10  # y = -g t² / 2
    dragged = simulate((0, 0), (100, 0), 1.0, {**NO_FORCES, "drag": 2}, random.Random(1), 4)
    assert 30 < dragged[-1][0] < 60  # (1 - e^-2) / 2 * 100 ≈ 43


def test_the_attractor_pulls_particles_in():
    path = simulate((300, 0), (0, 0), 1.0, {**NO_FORCES, "attract_strength": 400}, random.Random(1), 4)
    assert path[-1][0] < 300


def test_the_same_seed_gives_the_same_effect(library, make_settings):
    preset = library.get("fire_embers")
    first = generate_effect(preset, make_settings("fire_embers"))
    second = generate_effect(preset, make_settings("fire_embers"))
    assert first.bone_timelines == second.bone_timelines and first.slot_timelines == second.slot_timelines
    third = generate_effect(preset, make_settings("fire_embers", seed=7))
    assert third.bone_timelines != first.bone_timelines


def test_particles_are_emitted_over_the_window_and_end_in_time(library, make_settings):
    doc = generate_effect(library.get("dust_motes"), make_settings("dust_motes", emit_time=0.5, phase="one_shot"))
    starts = sorted(t.attachment[0].u for t in doc.slot_timelines.values())
    assert starts[0] < 0.05 and 0.4 < starts[-1] <= 0.5
    for timelines in doc.slot_timelines.values():
        assert timelines.attachment[-1].name is None and timelines.attachment[-1].u <= 1.0
    burst = generate_effect(library.get("dust_motes"), make_settings("dust_motes", emit_time=0, phase="one_shot"))
    assert {t.attachment[0].u for t in burst.slot_timelines.values()} == {0.0}


def test_colour_over_life_goes_from_primary_to_secondary(library, make_settings):
    doc = generate_effect(library.get("fire_embers"), make_settings("fire_embers", fade_out=0, fade_in=0))
    keys = next(iter(doc.slot_timelines.values())).color
    assert keys[0].color.to_hex()[:6] == "ffd34a" and keys[-1].color.to_hex()[:6] == "ff3b1f"


def _pose(doc, timelines, u):
    """{bone: (x, y)} of the visible particles at u, from translate keys (linear between keys)."""
    bones, slots = timelines
    out = {}
    for slot in doc.slots:
        keys = slots.get(slot.name)
        if not keys:
            continue
        shown = [k for k in keys.attachment if k.u <= u + 1e-9]
        if not shown or shown[-1].name is None:
            continue
        tr = bones[slot.bone].translate
        before = [k for k in tr if k.u <= u + 1e-9][-1]
        after = next((k for k in tr if k.u >= u - 1e-9), before)
        f = 0 if after.u == before.u else (u - before.u) / (after.u - before.u)
        out[slot.bone] = (before.x + (after.x - before.x) * f, before.y + (after.y - before.y) * f)
    return out


def _close(a, b):
    return a.keys() == b.keys() and all(abs(a[k][0] - b[k][0]) < 1e-3 and abs(a[k][1] - b[k][1]) < 1e-3 for k in a)


def test_the_loop_has_no_seam(library, make_settings):
    doc = generate_effect(library.get("dust_motes"), make_settings("dust_motes", phase="loop"))
    timelines = (doc.bone_timelines, doc.slot_timelines)
    start, end = _pose(doc, timelines, 0.0), _pose(doc, timelines, 1 - 1e-7)
    assert start  # particles carried over from the previous cycle are already there
    # every one of them is where it was at the end of the cycle, so looping doesn't jump
    assert _close(start, {k: v for k, v in end.items() if k in start})


def test_an_animation_set_hands_over_without_a_jump(tmp_path, library, make_settings):
    from anim2d.packaging.package import write_package
    settings = make_settings("fire_embers", animation_set=True)
    doc = generate_effect(library.get("fire_embers"), settings)
    names = [doc.animation_name, *doc.extra_animations]
    assert names == ["fx_test_01_intro", "fx_test_01_loop", "fx_test_01_outro"]
    intro = (doc.bone_timelines, doc.slot_timelines)
    loop, outro = doc.extra_animations["fx_test_01_loop"], doc.extra_animations["fx_test_01_outro"]
    # where the intro (or loop) leaves off is where the loop (or outro) picks up: the same particles,
    # in the same places
    late = 1 - 1e-6
    assert _close(_pose(doc, loop, 0.0), {k: v for k, v in _pose(doc, intro, late).items()
                                          if k in _pose(doc, loop, 0.0)})
    assert set(_pose(doc, outro, 0.0)) == set(_pose(doc, loop, 0.0))
    assert not _pose(doc, outro, late)  # the outro dies out
    result = write_package(library.get("fire_embers"), settings, tmp_path)
    assert result.ok, result.report.errors
    assert result.stats["animations"] == 3


def test_the_path_emitter_walks_along_the_curve(library, make_settings):
    doc = generate_effect(library.get("blazing_trail"), make_settings("blazing_trail", speed=0, gravity=0,
                                                                      turbulence=0, lifetime_variance=0))
    firsts = [doc.bone_timelines[s.bone].translate[0] for s in doc.slots]
    xs = [k.x for k in sorted(firsts, key=lambda k: k.u)]
    assert xs == sorted(xs) and xs[0] < 60 and xs[-1] > 540  # from the start of the path to its end
    assert max(k.y for k in firsts) > 80  # bent up towards the bend point (half way, on this curve)
