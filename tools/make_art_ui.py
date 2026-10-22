"""Art for the casual UI and gameplay presets (same rules as make_art.py: white, shape in alpha).

    python tools/make_art_ui.py
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter

from make_art import SS, _canvas, _finish, _save, dot, ring, smoke, soft_glow, sparkle
from make_art_casual import streak
from make_art_slots import god_ray, star5


def exclaim(width: int = 64, height: int = 160) -> Image.Image:
    """Exclamation mark: a tapered bar over a round dot."""
    W, H = width * SS, height * SS
    mask, draw = _canvas(width, height)
    draw.polygon([(W * 0.18, H * 0.04), (W * 0.82, H * 0.04), (W * 0.62, H * 0.66), (W * 0.38, H * 0.66)], fill=255)
    draw.ellipse((W * 0.18, H * 0.04 - W * 0.32, W * 0.82, H * 0.04 + W * 0.32), fill=255)
    r = W * 0.24
    cx, cy = W / 2, H * 0.86
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    return _finish(mask, width, height)


def letter_z(size: int = 96) -> Image.Image:
    """A bold rounded Z (sleep)."""
    S = size * SS
    mask, draw = _canvas(size, size)
    t = S * 0.17
    draw.rounded_rectangle((S * 0.12, S * 0.1, S * 0.88, S * 0.1 + t), radius=t * 0.4, fill=255)
    draw.rounded_rectangle((S * 0.12, S * 0.9 - t, S * 0.88, S * 0.9), radius=t * 0.4, fill=255)
    draw.polygon([(S * 0.88 - t * 1.2, S * 0.1 + t * 0.5), (S * 0.88, S * 0.1 + t), (S * 0.12 + t * 1.2, S * 0.9 - t * 0.5),
                  (S * 0.12, S * 0.9 - t)], fill=255)
    draw.line([(S * 0.86, S * 0.12 + t * 0.5), (S * 0.14, S * 0.88 - t * 0.5)], fill=255, width=int(t * 1.05))
    return _finish(mask, size, size)


def flame(width: int = 96, height: int = 160) -> Image.Image:
    """A soft flame tongue pointing up: round base, pointed top, bright core."""
    W, H = width * SS, height * SS
    outer, od = _canvas(width, height)
    r = W * 0.42
    cx, cy = W / 2, H - r - H * 0.04
    od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=170)
    od.polygon([(cx, H * 0.02), (cx + r * 0.98, cy - r * 0.15), (cx - r * 0.98, cy - r * 0.15)], fill=170)
    outer = outer.filter(ImageFilter.GaussianBlur(W * 0.06))
    core, cd = _canvas(width, height)
    r2 = r * 0.5
    cy2 = cy + r * 0.25
    cd.ellipse((cx - r2, cy2 - r2, cx + r2, cy2 + r2), fill=255)
    cd.polygon([(cx, H * 0.3), (cx + r2, cy2), (cx - r2, cy2)], fill=255)
    core = core.filter(ImageFilter.GaussianBlur(W * 0.05))
    return _finish(ImageChops.lighter(outer, core), width, height)


def main() -> None:
    _save(dot(64), "notification_ping", "dot")
    _save(ring(), "notification_ping", "ring")
    _save(dot(48), "loading_spinner", "dot")
    _save(god_ray(), "chest_open_rays", "ray")
    _save(sparkle(), "chest_open_rays", "sparkle")
    _save(soft_glow(), "chest_open_rays", "glow")
    _save(streak(256, 16), "focus_lines", "line")
    _save(exclaim(), "alert_pop", "exclaim")
    _save(streak(128, 20), "alert_pop", "line")
    _save(smoke(), "dust_puff", "puff")
    _save(streak(256, 20), "dash_lines", "line")
    _save(star5(96), "stun_stars", "star")
    _save(letter_z(), "sleep_zzz", "z")
    _save(flame(), "flame_burst", "flame")
    _save(soft_glow(), "flame_burst", "glow")


if __name__ == "__main__":
    main()
