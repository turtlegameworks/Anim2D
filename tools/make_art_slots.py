"""Art for the slot-game reward and reveal presets (same rules as make_art.py: white, shape in alpha).

    python tools/make_art_slots.py
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter

from make_art import SS, _canvas, _finish, _save, flash, ring, smoke, soft_glow, sparkle


def coin(size: int = 96) -> Image.Image:
    """Coin face: solid disc, a fainter rim band and a star stamped in the middle."""
    S = size * SS
    c = S / 2
    mask, draw = _canvas(size, size)
    r = S * 0.47
    draw.ellipse((c - r, c - r, c + r, c + r), fill=255)
    r2 = S * 0.38
    draw.ellipse((c - r2, c - r2, c + r2, c + r2), fill=200)
    pts = []
    for i in range(10):
        a = math.radians(90 + i * 36)
        rr = S * (0.25 if i % 2 == 0 else 0.11)
        pts.append((c + rr * math.cos(a), c - rr * math.sin(a)))
    draw.polygon(pts, fill=255)
    return _finish(mask, size, size)


def god_ray(width: int = 512, height: int = 128) -> Image.Image:
    """A soft wedge of light pointing toward +X: narrow at the left, wide and fading at the right."""
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    cy = (height - 1) / 2
    for x in range(width):
        f = x / (width - 1)
        half = (0.08 + 0.92 * f) * height / 2
        along = min(1.0, f / 0.08) * (1 - f) ** 1.3
        for y in range(height):
            d = abs(y - cy) / max(half, 1e-6)
            if d < 1:
                px[x, y] = int(255 * along * (1 - d) ** 1.5)
    out = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def confetti(width: int = 48, height: int = 24) -> Image.Image:
    """A small rounded paper strip."""
    W, H = width * SS, height * SS
    mask, draw = _canvas(width, height)
    draw.rounded_rectangle((W * 0.04, H * 0.08, W * 0.96, H * 0.92), radius=H * 0.2, fill=255)
    return _finish(mask, width, height)


def shine(width: int = 128, height: int = 512) -> Image.Image:
    """A tall soft streak for a shine sweeping across an item (bright core, soft sides)."""
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    for y in range(height):
        fade = min(1.0, y / (height * 0.2), (height - 1 - y) / (height * 0.2))
        for x in range(width):
            d = abs(x - (width - 1) / 2) / (width / 2)
            core = max(0.0, 1 - d * 3.2)
            soft = max(0.0, 1 - d) ** 2 * 0.45
            px[x, y] = int(255 * fade * min(1.0, core + soft))
    out = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def star5(size: int = 128) -> Image.Image:
    """Five-point star with a soft glow (collectable / reward star)."""
    S = size * SS
    c = S / 2
    mask, draw = _canvas(size, size)
    pts = []
    for i in range(10):
        a = math.radians(90 + i * 36)
        rr = S * (0.46 if i % 2 == 0 else 0.2)
        pts.append((c + rr * math.cos(a), c - rr * math.sin(a)))
    draw.polygon(pts, fill=255)
    glow, gd = _canvas(size, size)
    r = S * 0.3
    gd.ellipse((c - r, c - r, c + r, c + r), fill=80)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.06))
    return _finish(ImageChops.lighter(glow, mask), size, size)


def main() -> None:
    _save(coin(), "big_win_coins", "coin")
    _save(sparkle(), "big_win_coins", "sparkle")
    _save(god_ray(), "win_rays", "ray")
    _save(soft_glow(), "win_rays", "glow")
    _save(soft_glow(), "symbol_glow", "glow")
    _save(ring(), "symbol_glow", "ring")
    _save(sparkle(), "symbol_glow", "sparkle")
    _save(shine(), "shine_sweep", "shine")
    _save(sparkle(), "shine_sweep", "sparkle")
    _save(smoke(), "poof_reveal", "smoke")
    _save(sparkle(), "poof_reveal", "sparkle")
    _save(flash(), "poof_reveal", "flash")
    _save(flash(), "light_reveal", "flash")
    _save(god_ray(), "light_reveal", "ray")
    _save(ring(), "light_reveal", "ring")
    _save(sparkle(), "light_reveal", "sparkle")
    _save(confetti(), "confetti_burst", "confetti")
    _save(star5(), "collect_trail", "star")
    _save(sparkle(), "collect_trail", "sparkle")
    _save(soft_glow(), "collect_trail", "glow")


if __name__ == "__main__":
    main()
