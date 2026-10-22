"""Art for the casual-game presets (same rules as make_art.py: white, shape in alpha).

    python tools/make_art_casual.py
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter

from make_art import SS, _canvas, _finish, _save, dot, flash, ring, soft_glow, sparkle
from make_art_slots import star5


def shard(size: int = 64) -> Image.Image:
    """An irregular crystal shard with a brighter facet."""
    S = size * SS
    mask, draw = _canvas(size, size)
    outer = [(0.5, 0.04), (0.86, 0.38), (0.7, 0.96), (0.22, 0.82), (0.12, 0.34)]
    draw.polygon([(x * S, y * S) for x, y in outer], fill=190)
    facet = [(0.5, 0.04), (0.86, 0.38), (0.5, 0.55), (0.12, 0.34)]
    draw.polygon([(x * S, y * S) for x, y in facet], fill=255)
    return _finish(mask, size, size)


def bubble(size: int = 160) -> Image.Image:
    """Soap bubble: faint fill, brighter rim and a crescent highlight at the top left."""
    S = size * SS
    c = S / 2
    mask, draw = _canvas(size, size)
    r = S * 0.46
    draw.ellipse((c - r, c - r, c + r, c + r), fill=70)
    inner = r - S * 0.035
    draw.ellipse((c - inner, c - inner, c + inner, c + inner), fill=28)
    hl, hd = _canvas(size, size)
    hd.ellipse((c - r * 0.62, c - r * 0.7, c + r * 0.1, c + r * 0.05), fill=230)
    hd.ellipse((c - r * 0.5, c - r * 0.56, c + r * 0.22, c + r * 0.2), fill=0)
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.01))
    return _finish(ImageChops.lighter(mask, hl), size, size)


def heart(size: int = 112) -> Image.Image:
    """A plump heart."""
    S = size * SS
    mask, draw = _canvas(size, size)
    r = S * 0.24
    draw.ellipse((S * 0.06, S * 0.12, S * 0.06 + 2 * r, S * 0.12 + 2 * r), fill=255)
    draw.ellipse((S * 0.94 - 2 * r, S * 0.12, S * 0.94, S * 0.12 + 2 * r), fill=255)
    draw.polygon([(S * 0.08, S * 0.44), (S * 0.92, S * 0.44), (S * 0.5, S * 0.92)], fill=255)
    draw.rectangle((S * 0.3, S * 0.3, S * 0.7, S * 0.5), fill=255)
    return _finish(mask.filter(ImageFilter.GaussianBlur(S * 0.004)), size, size)


def drop(width: int = 48, height: int = 72) -> Image.Image:
    """Water drop pointing up (+Y in Spine is up; the image's top is the tip)."""
    W, H = width * SS, height * SS
    mask, draw = _canvas(width, height)
    r = W * 0.42
    cx, cy = W / 2, H - r - H * 0.04
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    draw.polygon([(cx, H * 0.03), (cx + r * 0.93, cy - r * 0.3), (cx - r * 0.93, cy - r * 0.3)], fill=255)
    return _finish(mask, width, height)


def streak(width: int = 128, height: int = 24) -> Image.Image:
    """A short soft streak toward +X, for firework trails (bright head at the right)."""
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    cy = (height - 1) / 2
    for x in range(width):
        f = x / (width - 1)
        for y in range(height):
            d = abs(y - cy) / (height / 2)
            px[x, y] = int(255 * f ** 1.5 * max(0.0, 1 - d) ** 1.2)
    out = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def swirl(size: int = 256) -> Image.Image:
    """Spiral arms fading toward the centre, for a portal or vortex."""
    mask = Image.new("L", (size, size), 0)
    px = mask.load()
    c = (size - 1) / 2
    arms = 3
    for y in range(size):
        for x in range(size):
            dx, dy = x - c, y - c
            r = math.hypot(dx, dy) / (size / 2)
            if r >= 1:
                continue
            a = math.atan2(dy, dx)
            band = 0.5 + 0.5 * math.cos(arms * a + r * 7.0)
            px[x, y] = int(255 * band ** 3 * (1 - r) ** 0.6 * min(1.0, r * 4))
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(mask.filter(ImageFilter.GaussianBlur(1.2)))
    return out


def main() -> None:
    _save(shard(), "gem_shatter", "shard")
    _save(flash(), "gem_shatter", "flash")
    _save(sparkle(), "gem_shatter", "sparkle")
    _save(bubble(), "bubble_pop", "bubble")
    _save(dot(48), "bubble_pop", "droplet")
    _save(ring(), "bubble_pop", "ring")
    _save(heart(), "heart_burst", "heart")
    _save(soft_glow(), "heart_burst", "glow")
    _save(star5(160), "star_rating", "star")
    _save(sparkle(), "star_rating", "sparkle")
    _save(soft_glow(), "star_rating", "glow")
    _save(ring(), "tap_ripple", "ring")
    _save(soft_glow(), "tap_ripple", "glow")
    _save(streak(), "firework", "streak")
    _save(dot(48), "firework", "spark")
    _save(soft_glow(), "firework", "glow")
    _save(swirl(), "portal_swirl", "swirl")
    _save(soft_glow(), "portal_swirl", "glow")
    _save(sparkle(), "portal_swirl", "sparkle")
    _save(drop(), "water_splash", "drop")
    _save(ring(), "water_splash", "ring")


if __name__ == "__main__":
    main()
