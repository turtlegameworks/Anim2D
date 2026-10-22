# Preset authoring

A preset is a folder in `src/anim2d/preset_library/` containing `preset.json` and its PNG images. Presets are
data only: they pick a built-in generator by name and never contain code.

```text
src/anim2d/preset_library/impact_basic/
  preset.json
  images/flash.png
  images/ray.png
```

## preset.json (application schema, not Spine's)

| Field | Meaning |
| --- | --- |
| `schema_version` | Must be `1` |
| `id` | Lowercase letters, digits, underscores; unique in the library |
| `version` | Bump when output for the same settings changes; saved variants of older versions are rejected |
| `name`, `category`, `description`, `tags` | Browser display and search |
| `generator`, `generator_version` | Built-in generator and its expected version: `single_pop`, `radial_burst`, `slash_arc`, `ring_wave`, `scatter_pop`, `particle_spray`, `rising_particles`, `explosion`, `pillar_burst`, `spin_glow`, `flicker_bolt`, `coin_rain`, `rotating_rays`, `pulse_glow`, `shine_sweep`, `confetti`, `collect_trail`, `poof_reveal`, `light_reveal`, `shatter`, `bubble_pop`, `heart_burst`, `star_rating`, `firework`, `vortex`, `splash`, `badge_ping`, `orbit_spinner`, `fan_burst`, `focus_lines`, `alert_pop`, `dust_puff`, `dash_lines`, `orbit_stars`, `float_letters`, `flame_rise` (see [the catalogue](fx_presets.md)) |
| `source_duration_seconds` | Authored length; also the usual duration default |
| `supports_loop` | `false` for one-shot effects |
| `required_features` | Spine features the output needs; checked against the compatibility profile |
| `images` | `image_id → relative PNG path` inside the preset folder |
| `common_overrides` | Change `default`, `min`, `max`, `label`, `description` or `unit` of a common control |
| `parameters` | Effect-specific controls: `type` (`integer`, `number`, `boolean`, `color`, `choice`), `default`, `min`, `max`, `unit`, `label`, `description`, `group` |

Common controls every preset gets: primary/secondary colour, opacity, scale, rotation, position X/Y,
mirror, duration, start delay, random seed.

## Artwork rules

* White art; the shape lives in the alpha channel (straight alpha). Slot colour tints it.
* Transparent PNG, clean edges, no noise. `tools/make_art.py`, `tools/make_art_slots.py`,
  `tools/make_art_casual.py` and `tools/make_art_ui.py` regenerate the built-in art.
* One image per reusable part (e.g. one `ray.png` shared by every ray).

## Coordinates

Spine convention: +X right, +Y up, rotation counter-clockwise in degrees, 0° points right.
