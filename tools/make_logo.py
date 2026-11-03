"""Makes the Anim2D wordmarks in src/anim2d/assets from the logo artwork (dark ink and orange on white).

    python tools/make_logo.py path/to/logo.png

    anim2d-logo.png        dark ink, for light backgrounds
    anim2d-logo-light.png  light ink, for dark backgrounds (the app, the website)

The white is made transparent, keeping the antialiased edges, and the image is cropped to the lettering.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parents[1] / "src" / "anim2d" / "assets"
INK, LIGHT_INK = (38, 38, 38), (236, 236, 236)
ORANGE = (251, 162, 8)
PAD = 12


def wordmarks(source: Image.Image) -> tuple[Image.Image, Image.Image]:
    """(dark, light) versions of the logo on a transparent background."""
    rgb = source.convert("RGB")
    dark = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    light = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    src, out_dark, out_light = rgb.load(), dark.load(), light.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = src[x, y]
            # An edge pixel is the colour mixed with white: blue says how much of the orange there is,
            # brightness how much of the ink.
            if r - b > 0.5 * (255 - b) and 255 - b > 8:
                alpha = min(1.0, (255 - b) / (255 - ORANGE[2]))
                out_dark[x, y] = out_light[x, y] = (*ORANGE, round(alpha * 255))
            else:
                alpha = min(1.0, max(0.0, (255 - (r + g + b) / 3) / (255 - INK[0])))
                if alpha > 0.03:
                    out_dark[x, y] = (*INK, round(alpha * 255))
                    out_light[x, y] = (*LIGHT_INK, round(alpha * 255))
    box = dark.getbbox()
    box = (max(0, box[0] - PAD), max(0, box[1] - PAD), min(rgb.width, box[2] + PAD), min(rgb.height, box[3] + PAD))
    return dark.crop(box), light.crop(box)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    with Image.open(argv[0]) as source:
        dark, light = wordmarks(source)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, image in (("anim2d-logo.png", dark), ("anim2d-logo-light.png", light)):
        image.save(ASSETS / name, optimize=True)
        print(f"wrote {ASSETS / name} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
