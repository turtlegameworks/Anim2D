"""Draws the Anim2D app icon into src/anim2d/assets/anim2d.ico.

    python tools/make_icon.py

The mark is an "A" built as a rig: a joint dot at the apex, two bone legs ending in ring joints, and a
crossbar, in orange on a dark rounded tile.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "assets" / "anim2d.ico"
S = 1250  # drawn large, downsampled per icon size
INK = (27, 27, 27, 255)
ORANGE = (250, 160, 10, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 60, S - 60, S - 60), radius=230, fill=INK)

    def circle(x: float, y: float, r: float, fill) -> None:
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    apex, left, right = (626, 330), (375, 898), (875, 898)
    for foot in (left, right):  # the bone legs
        d.line([apex, foot], fill=ORANGE, width=88)
    d.rectangle((470, 660, 780, 748), fill=ORANGE)  # the crossbar
    for x, y in (left, right):  # ring joints at the feet
        circle(x, y, 80, ORANGE)
        circle(x, y, 38, INK)
    circle(626, 315, 100, INK)  # a gap between the dot and the legs
    circle(626, 315, 83, ORANGE)  # the joint dot
    return img.resize((1024, 1024), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
