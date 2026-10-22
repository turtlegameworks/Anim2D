"""Draws the Anim2D app icon (original art) into src/anim2d/assets/anim2d.ico.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "assets" / "anim2d.ico"
S = 1024  # drawn large, downsampled per icon size


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=(36, 39, 48, 255))
    # Two bones (tapered diamonds) joined at an elbow, like the editor's bone glyphs.
    orange, light = (255, 170, 60, 255), (255, 214, 150, 255)

    def bone(x0, y0, x1, y1, width):
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5
        nx, ny = -dy / length * width, dx / length * width
        kx, ky = x0 + dx * 0.18, y0 + dy * 0.18
        d.polygon([(x0, y0), (kx + nx, ky + ny), (x1, y1), (kx - nx, ky - ny)], fill=orange)

    bone(250, 760, 560, 430, 70)
    bone(560, 430, 800, 250, 55)
    for (x, y), r in (((250, 760), 70), ((560, 430), 62), ((800, 250), 48)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=light, outline=(36, 39, 48, 255), width=18)
    # A small spark for the FX side.
    cx, cy = 770, 720
    for (ax, ay), (bx, by) in (((0, -150), (0, 150)), ((-150, 0), (150, 0))):
        d.polygon([(cx + ax, cy + ay), (cx + 30, cy), (cx + bx, cy + by), (cx - 30, cy)], fill=(127, 224, 208, 255))
        d.polygon([(cx + ay, cy + ax), (cx, cy + 30), (cx + by, cy + bx), (cx, cy - 30)], fill=(127, 224, 208, 255))
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
