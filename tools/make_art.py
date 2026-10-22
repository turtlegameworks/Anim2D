"""Regenerates the original source art for the built-in presets.

All art is white with shape in the alpha channel (straight alpha), so slot colour
tints it exactly. Shapes are drawn at 4x and downsampled for clean edges.

    python tools/make_art.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

LIBRARY = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "preset_library"
SS = 4  # supersampling factor


def _canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    mask = Image.new("L", (w * SS, h * SS), 0)
    return mask, ImageDraw.Draw(mask)


def _finish(mask: Image.Image, w: int, h: int) -> Image.Image:
    alpha = mask.resize((w, h), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


def _save(img: Image.Image, preset: str, name: str) -> None:
    path = LIBRARY / preset / "images" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print(f"wrote {path.relative_to(LIBRARY.parent)} {img.size}")


def dot(size: int = 128) -> Image.Image:
    mask, draw = _canvas(size, size)
    c = size * SS / 2
    r = size * SS * 0.42
    draw.ellipse((c - r, c - r, c + r, c + r), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size * SS * 0.03))
    return _finish(mask, size, size)


def flash(size: int = 256) -> Image.Image:
    """Four-point star over a solid core with a soft halo."""
    S = size * SS
    c = S / 2
    halo, hd = _canvas(size, size)
    r = S * 0.30
    hd.ellipse((c - r, c - r, c + r, c + r), fill=110)
    halo = halo.filter(ImageFilter.GaussianBlur(S * 0.06))

    star, sd = _canvas(size, size)
    long_r, waist = S * 0.48, S * 0.07
    for angle in (0, 90):
        if angle == 0:
            pts = [(c - long_r, c), (c, c - waist), (c + long_r, c), (c, c + waist)]
        else:
            pts = [(c, c - long_r), (c + waist, c), (c, c + long_r), (c - waist, c)]
        sd.polygon(pts, fill=255)
    short_r, waist2 = S * 0.26, S * 0.045
    for dx, dy in ((1, 1), (1, -1)):
        k = short_r / 2 ** 0.5
        w = waist2 / 2 ** 0.5
        sd.polygon([(c - dx * k, c - dy * k), (c + w, c - w * dx * dy), (c + dx * k, c + dy * k),
                    (c - w, c + w * dx * dy)], fill=235)
    core_r = S * 0.12
    sd.ellipse((c - core_r, c - core_r, c + core_r, c + core_r), fill=255)
    return _finish(ImageChops.lighter(halo, star), size, size)


def ray(width: int = 256, height: int = 48) -> Image.Image:
    """Tapered streak pointing toward +X: thin tail at the left, sharp tip at the right."""
    W, H = width * SS, height * SS
    cy = H / 2
    mask, draw = _canvas(width, height)
    outer = [(0, cy), (W * 0.35, cy - H * 0.42), (W * 0.75, cy - H * 0.22), (W, cy),
             (W * 0.75, cy + H * 0.22), (W * 0.35, cy + H * 0.42)]
    draw.polygon(outer, fill=150)
    inner = [(W * 0.08, cy), (W * 0.38, cy - H * 0.2), (W * 0.78, cy - H * 0.09), (W * 0.97, cy),
             (W * 0.78, cy + H * 0.09), (W * 0.38, cy + H * 0.2)]
    draw.polygon(inner, fill=255)
    return _finish(mask, width, height)


def soft_glow(size: int = 256) -> Image.Image:
    """Round glow that fades smoothly to the edge (for additive light)."""
    mask = Image.new("L", (size, size), 0)
    c = (size - 1) / 2
    px = mask.load()
    for y in range(size):
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5 / (size / 2)
            px[x, y] = int(255 * max(0.0, 1 - d) ** 2.2)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def ring(size: int = 256, width: float = 0.07) -> Image.Image:
    """Thin ring with a soft outer edge."""
    mask, draw = _canvas(size, size)
    S = size * SS
    c = S / 2
    r = S * 0.46
    draw.ellipse((c - r, c - r, c + r, c + r), fill=255)
    inner = r - S * width
    draw.ellipse((c - inner, c - inner, c + inner, c + inner), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(S * 0.006))
    return _finish(mask, size, size)


def crescent(width: int = 256, height: int = 256) -> Image.Image:
    """Slash arc: a crescent bulging toward +X, thick in the middle and sharp at both ends."""
    W, H = width * SS, height * SS
    mask, draw = _canvas(width, height)
    draw.ellipse((W * 0.02, H * 0.02, W * 0.98, H * 0.98), fill=255)
    draw.ellipse((-W * 0.18, H * 0.06, W * 0.80, H * 0.94), fill=0)
    soft = mask.filter(ImageFilter.GaussianBlur(W * 0.004))
    return _finish(soft, width, height)


def sparkle(size: int = 128) -> Image.Image:
    """Four-point twinkle star with thin long arms."""
    S = size * SS
    c = S / 2
    mask, draw = _canvas(size, size)
    long_r, waist = S * 0.49, S * 0.045
    draw.polygon([(c - long_r, c), (c, c - waist), (c + long_r, c), (c, c + waist)], fill=255)
    draw.polygon([(c, c - long_r), (c + waist, c), (c, c + long_r), (c - waist, c)], fill=255)
    core = S * 0.07
    draw.ellipse((c - core, c - core, c + core, c + core), fill=255)
    glow, gd = _canvas(size, size)
    r = S * 0.2
    gd.ellipse((c - r, c - r, c + r, c + r), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.05))
    return _finish(ImageChops.lighter(glow, mask), size, size)


def plus(size: int = 96) -> Image.Image:
    """Rounded plus sign (healing)."""
    S = size * SS
    mask, draw = _canvas(size, size)
    t, r = S * 0.13, S * 0.42
    c = S / 2
    draw.rounded_rectangle((c - t, c - r, c + t, c + r), radius=t, fill=255)
    draw.rounded_rectangle((c - r, c - t, c + r, c + t), radius=t, fill=255)
    return _finish(mask, size, size)


def smoke(size: int = 192) -> Image.Image:
    """Soft lumpy puff made of overlapping blurred circles."""
    S = size * SS
    mask, draw = _canvas(size, size)
    for cx, cy, r in ((0.5, 0.52, 0.27), (0.33, 0.55, 0.19), (0.67, 0.56, 0.2), (0.45, 0.36, 0.2),
                      (0.6, 0.38, 0.17), (0.5, 0.68, 0.18)):
        draw.ellipse(((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(S * 0.04))
    return _finish(mask, size, size)


def beam(width: int = 96, height: int = 512) -> Image.Image:
    """Vertical light pillar: bright core, soft sides, fading out at the top."""
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    for y in range(height):
        fade = min(1.0, (height - 1 - y) / (height * 0.15)) * min(1.0, y / (height * 0.6))
        fade = max(0.0, min(1.0, 0.25 + 0.75 * fade)) if y > height * 0.02 else 0.0
        for x in range(width):
            d = abs(x - (width - 1) / 2) / (width / 2)
            px[x, y] = int(255 * max(0.0, 1 - d) ** 1.6 * fade)
    out = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def rune_circle(size: int = 320) -> Image.Image:
    """Magic circle: two rings, a six-point star and tick marks between the rings."""
    import math
    S = size * SS
    c = S / 2
    mask, draw = _canvas(size, size)
    line = S * 0.012
    for r in (S * 0.47, S * 0.39, S * 0.18):
        draw.ellipse((c - r, c - r, c + r, c + r), outline=255, width=int(line))
    r = S * 0.39
    pts = [(c + r * math.cos(math.radians(90 + i * 60)), c - r * math.sin(math.radians(90 + i * 60))) for i in range(6)]
    for tri in (pts[0::2], pts[1::2]):
        draw.polygon(tri, outline=255, width=int(line))
    for i in range(24):
        a = math.radians(i * 15)
        r1, r2 = S * 0.405, S * (0.455 if i % 2 else 0.43)
        draw.line((c + r1 * math.cos(a), c + r1 * math.sin(a), c + r2 * math.cos(a), c + r2 * math.sin(a)),
                  fill=255, width=int(line))
    return _finish(mask, size, size)


def bolt(width: int = 128, height: int = 512) -> Image.Image:
    """Jagged lightning bolt from the top to the bottom, with a soft glow."""
    W, H = width * SS, height * SS
    mask, draw = _canvas(width, height)
    xs = [0.5, 0.35, 0.62, 0.3, 0.58, 0.4, 0.55, 0.5]
    pts = [(W * x, H * i / (len(xs) - 1)) for i, x in enumerate(xs)]
    glow = Image.new("L", mask.size, 0)
    ImageDraw.Draw(glow).line(pts, fill=120, width=int(W * 0.22), joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(W * 0.08))
    draw.line(pts, fill=255, width=int(W * 0.07), joint="curve")
    return _finish(ImageChops.lighter(glow, mask), width, height)


def main() -> None:
    _save(dot(), "minimal_pop", "dot")
    _save(flash(), "impact_basic", "flash")
    _save(ray(), "impact_basic", "ray")
    _save(crescent(), "sword_slash", "slash")
    _save(ring(), "shockwave", "ring")
    _save(soft_glow(), "shockwave", "glow")
    _save(sparkle(), "sparkle_twinkle", "sparkle")
    _save(dot(64), "coin_fountain", "dot")
    _save(sparkle(), "coin_fountain", "sparkle")
    _save(plus(), "heal_rise", "plus")
    _save(soft_glow(), "heal_rise", "glow")
    _save(flash(), "explosion", "flash")
    _save(smoke(), "explosion", "smoke")
    _save(dot(64), "explosion", "debris")
    _save(beam(), "level_up", "beam")
    _save(ring(), "level_up", "ring")
    _save(sparkle(), "level_up", "sparkle")
    _save(rune_circle(), "magic_circle", "rune")
    _save(soft_glow(), "magic_circle", "glow")
    _save(bolt(), "lightning_strike", "bolt")
    _save(flash(), "lightning_strike", "flash")
    _save(flash(), "critical_hit", "flash")
    _save(ray(width=256, height=24), "critical_hit", "ray")


if __name__ == "__main__":
    main()
