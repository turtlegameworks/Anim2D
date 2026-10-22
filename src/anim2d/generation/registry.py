"""Maps generator names used in preset.json to built-in generator functions."""

from __future__ import annotations

import random
from typing import Callable

from ..models.effect import EffectDocument
from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings
from . import casual_generators, generators, more_generators, slot_generators, ui_generators
from .builder import EffectBuilder

Generator = Callable[[EffectBuilder, random.Random], None]

GENERATORS: dict[str, tuple[int, Generator]] = {
    # name: (generator_version, function)
    "single_pop": (1, generators.single_pop),
    "radial_burst": (1, generators.radial_burst),
    "slash_arc": (1, more_generators.slash_arc),
    "ring_wave": (1, more_generators.ring_wave),
    "scatter_pop": (1, more_generators.scatter_pop),
    "particle_spray": (1, more_generators.particle_spray),
    "rising_particles": (1, more_generators.rising_particles),
    "explosion": (1, more_generators.explosion),
    "pillar_burst": (1, more_generators.pillar_burst),
    "spin_glow": (1, more_generators.spin_glow),
    "flicker_bolt": (1, more_generators.flicker_bolt),
    "coin_rain": (1, slot_generators.coin_rain),
    "rotating_rays": (1, slot_generators.rotating_rays),
    "pulse_glow": (1, slot_generators.pulse_glow),
    "shine_sweep": (1, slot_generators.shine_sweep),
    "confetti": (1, slot_generators.confetti),
    "collect_trail": (1, slot_generators.collect_trail),
    "poof_reveal": (1, slot_generators.poof_reveal),
    "light_reveal": (1, slot_generators.light_reveal),
    "shatter": (1, casual_generators.shatter),
    "bubble_pop": (1, casual_generators.bubble_pop),
    "heart_burst": (1, casual_generators.heart_burst),
    "star_rating": (1, casual_generators.star_rating),
    "firework": (1, casual_generators.firework),
    "vortex": (1, casual_generators.vortex),
    "splash": (1, casual_generators.splash),
    "badge_ping": (1, ui_generators.badge_ping),
    "orbit_spinner": (1, ui_generators.orbit_spinner),
    "fan_burst": (1, ui_generators.fan_burst),
    "focus_lines": (1, ui_generators.focus_lines),
    "alert_pop": (1, ui_generators.alert_pop),
    "dust_puff": (1, ui_generators.dust_puff),
    "dash_lines": (1, ui_generators.dash_lines),
    "orbit_stars": (1, ui_generators.orbit_stars),
    "float_letters": (1, ui_generators.float_letters),
    "flame_rise": (1, ui_generators.flame_rise),
}


def generate_effect(preset: PresetDefinition, settings: EffectSettings) -> EffectDocument:
    """Build the IR for one effect instance. Settings must already be validated."""
    version, func = GENERATORS[preset.generator]
    if preset.generator_version != version:
        raise ValueError(f"preset '{preset.id}' needs generator '{preset.generator}' version "
                         f"{preset.generator_version}, but this tool has version {version}")
    builder = EffectBuilder(settings, preset.image_map())
    func(builder, random.Random(int(settings["seed"])))
    return builder.build()
