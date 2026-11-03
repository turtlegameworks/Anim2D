"""Draws the Anim2D app icon (original art) into src/anim2d/assets/anim2d.ico.

    python tools/make_icon.py

The mark is the bone "i" from the Anim2D wordmark (tools/make_logo.py): a joint dot, a tapered bone and a
ring joint, in the wordmark's orange on its ink colour.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "assets" / "anim2d.ico"
S = 1024  # drawn large, downsampled per icon size
INK = (38, 38, 38, 255)
ORANGE = (251, 162, 8, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=INK)
    # Measured on the wordmark (x centred on 0, y from its top), scaled to fill the tile.
    k, cx, top = 2.45, S / 2, S / 2 - 2.45 * 313

    def at(x: float, y: float) -> tuple[float, float]:
        return cx + x * k, top + y * k

    def circle(x: float, y: float, r: float, fill) -> None:
        (px, py), pr = at(x, y), r * k
        d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=fill)

    circle(0, 195, 35, ORANGE)  # the dot
    circle(0, 254, 9, ORANGE)  # the bone's rounded top
    d.polygon([at(-9, 254), at(9, 254), at(33, 398), at(-33, 398)], fill=ORANGE)
    circle(-2, 422, 45, ORANGE)  # the ring joint
    circle(-2, 422, 20, INK)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
