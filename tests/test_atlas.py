"""Texture packer: page pixels are checked against the source images, and the .atlas is loaded
by the official spine-ts 4.0 TextureAtlas + AtlasAttachmentLoader, whose UVs must land on the
same page pixels the packer wrote."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from anim2d.formats.atlas import PackError, PackSettings, pack

VENDOR = Path(__file__).parent / "runtime" / "vendor"
CORE40 = VENDOR / "v40" / "spine-core-4.0.js"
NODE = shutil.which("node")
needs_runtime = pytest.mark.skipif(not NODE or not CORE40.is_file(),
                                   reason="needs node and tools/fetch_spine_runtime.py")


def _image(w: int, h: int, margin: tuple[int, int, int, int], seed: int) -> Image.Image:
    """Noise inside a box, transparent margins (left, top, right, bottom)."""
    rng = random.Random(seed)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    left, top, right, bottom = margin
    for y in range(top, h - bottom):
        for x in range(left, w - right):
            img.putpixel((x, y), (rng.randrange(256), rng.randrange(256), rng.randrange(256), 255))
    return img


IMAGES = {
    "parts/arm": ((20, 70), (3, 5, 2, 9)),
    "parts/leg": ((16, 90), (0, 0, 0, 0)),
    "head": ((48, 40), (6, 2, 1, 4)),
    "fx/glow": ((30, 30), (10, 10, 10, 10)),
    "empty": ((12, 12), (6, 6, 6, 6)),  # fully transparent
    "wide": ((80, 14), (0, 3, 0, 0)),
}


def _images() -> dict[str, Image.Image]:
    return {name: _image(*size, margin, i) for i, (name, (size, margin)) in enumerate(IMAGES.items())}


def _original_to_page(r, px: float, py: float) -> tuple[float, float]:
    """Original image point (packed scale, y down) -> page point, as the packer laid it out."""
    strip_top = r.original_height - r.offset_y - r.height
    if r.rotated:
        return r.x + (py - strip_top), r.y + (r.offset_x + r.width - px)
    return r.x + (px - r.offset_x), r.y + (py - strip_top)


def _check_pixels(result, images, scale=1.0, pma=False):
    for r in result.regions:
        source = images[r.name].convert("RGBA")
        if scale != 1:
            source = source.resize((r.original_width, r.original_height), Image.Resampling.LANCZOS)
        assert source.size == (r.original_width, r.original_height)
        page = result.pages[r.page]
        strip_top = r.original_height - r.offset_y - r.height
        for j in range(r.original_height):
            for i in range(r.original_width):
                inside = r.offset_x <= i < r.offset_x + r.width and strip_top <= j < strip_top + r.height
                want = source.getpixel((i, j))
                if not inside:
                    assert want[3] == 0, (r.name, i, j)
                    continue
                if r.rotated:
                    got = page.getpixel((r.x + j - strip_top, r.y + r.offset_x + r.width - 1 - i))
                else:
                    got = page.getpixel((r.x + i - r.offset_x, r.y + j - strip_top))
                if pma:
                    want = tuple(round(c * want[3] / 255) for c in want[:3]) + (want[3],)
                    assert got == pytest.approx(want, abs=1), (r.name, i, j)
                else:
                    assert got == want, (r.name, i, j)


def _no_overlap(result, pad):
    for page in range(len(result.pages)):
        rects = []
        for r in result.regions:
            if r.page == page:
                w, h = (r.height, r.width) if r.rotated else (r.width, r.height)
                assert r.x >= pad and r.y >= pad
                assert r.x + w + pad <= result.pages[page].width and r.y + h + pad <= result.pages[page].height
                rects.append((r.x, r.y, w, h))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                apart = a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0] or \
                    a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1]
                assert apart, (a, b)


def test_pack_strips_rotates_and_pages():
    images = _images()
    images.update({f"copy/{name}": img for name, img in _images().items()})
    result = pack(images, "hero", PackSettings(max_width=100, max_height=100, padding=2))
    assert {r.name for r in result.regions} == set(images)
    assert len(result.pages) >= 2, "a small page size spills onto more pages"
    assert any(r.rotated for r in result.regions)
    by_name = {r.name: r for r in result.regions}
    arm = by_name["parts/arm"]
    assert (arm.width, arm.height, arm.offset_x, arm.offset_y) == (15, 56, 3, 9)
    assert not by_name["parts/leg"].stripped
    assert (by_name["empty"].width, by_name["empty"].height) == (1, 1)
    _no_overlap(result, 2)
    _check_pixels(result, images)
    text = result.atlas_text()
    assert text.startswith("hero.png\nsize: ") and "\n\nhero_2.png\nsize: " in text
    assert "rotate: 90" in text and "offsets: 3,9,20,70" in text


def test_pack_options():
    images = _images()
    plain = pack(images, "p", PackSettings(strip_whitespace=False, rotation=False, padding=0, power_of_two=True))
    assert all(not r.rotated and not r.stripped for r in plain.regions)
    assert all(p.width & (p.width - 1) == 0 and p.height & (p.height - 1) == 0 for p in plain.pages)
    _no_overlap(plain, 0)
    _check_pixels(plain, images)

    pma = pack(images, "m", PackSettings(premultiply_alpha=True))
    assert "pma: true" in pma.atlas_text()
    _check_pixels(pma, images, pma=True)

    half = pack(images, "h", PackSettings(scale=0.5))
    assert {r.name: r.original_width for r in half.regions}["head"] == 24
    with pytest.raises(PackError, match="does not fit"):
        pack({"big": Image.new("RGBA", (300, 20), (255, 0, 0, 255))}, "x",
             PackSettings(max_width=128, max_height=512, rotation=False))
    assert pack({"big": Image.new("RGBA", (300, 20), (255, 0, 0, 255))}, "x",
                PackSettings(max_width=128, max_height=512)).regions[0].rotated


def _skeleton(images: dict[str, Image.Image]) -> dict:
    slots, attachments = [], {}
    for i, (name, img) in enumerate(images.items()):
        slot = f"s{i}"
        slots.append({"name": slot, "bone": "root", "attachment": name})
        attachments[slot] = {name: {"width": img.width, "height": img.height}}
    w, h = images["head"].size
    slots.append({"name": "mesh", "bone": "root", "attachment": "headmesh"})
    attachments["mesh"] = {"headmesh": {
        "type": "mesh", "path": "head", "width": w, "height": h, "hull": 4,
        "uvs": [0, 0, 1, 0, 1, 1, 0, 1, 0.3, 0.6], "triangles": [0, 1, 4, 1, 2, 4, 2, 3, 4, 3, 0, 4],
        "vertices": [-w / 2, h / 2, w / 2, h / 2, w / 2, -h / 2, -w / 2, -h / 2, 0, 0]}}
    return {"skeleton": {"spine": "4.0.64"}, "bones": [{"name": "root"}], "slots": slots,
            "skins": [{"name": "default", "attachments": attachments}], "animations": {}}


@needs_runtime
@pytest.mark.parametrize("settings", [PackSettings(max_width=100, max_height=100),
                                      PackSettings(scale=0.5, padding=1), PackSettings(rotation=False),
                                      PackSettings(padding=3, padding_y=1, edge_padding=False, alias=True,
                                                   duplicate_padding=True, bleed=True, wrap_x="Repeat",
                                                   format="RGBA4444", min_width=64, divisible_by_4=True,
                                                   alpha_threshold=3, filter_min="MipMapLinearLinear")])
def test_official_runtime_reads_the_atlas(tmp_path, settings):
    images = _images()
    images.update({f"copy/{name}": img for name, img in _images().items()})
    result = pack(images, "rig", settings)
    assert any(r.rotated for r in result.regions) == settings.rotation
    files = result.write(tmp_path / "rig.atlas")
    assert all(f.is_file() for f in files)
    skeleton = tmp_path / "rig.json"
    skeleton.write_text(json.dumps(_skeleton(images)), encoding="utf-8")
    args = [NODE, str(Path(__file__).parent / "runtime" / "check_spine38.js"), str(CORE40), str(skeleton),
            f"--atlas={tmp_path / 'rig.atlas'}"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    run = json.loads(out.stdout)
    assert [p["name"] for p in run["pages"]] == result.page_names
    pages = {p["name"]: (p["width"], p["height"]) for p in run["pages"]}
    regions = {r.name: r for r in result.regions}
    s = settings.scale
    for att in run["attachments"]:
        r = regions["head" if att["name"] == "headmesh" else att["name"]]
        pw, ph = pages[att["page"]]
        assert att["page"] == result.page_names[r.page]
        source_w, source_h = images[r.name].size
        if att["type"] == "region":
            for c in range(4):  # local corner (attachment space, y up) -> original point -> page
                lx, ly = att["offset"][c * 2], att["offset"][c * 2 + 1]
                px, py = (lx + source_w / 2) * s, (source_h / 2 - ly) * s
                ex, ey = _original_to_page(r, px, py)
                assert att["uvs"][c * 2] * pw == pytest.approx(ex, abs=0.02), (r.name, c)
                assert att["uvs"][c * 2 + 1] * ph == pytest.approx(ey, abs=0.02), (r.name, c)
        else:
            for k in range(0, len(att["uvs"]), 2):
                u, v = att["regionUVs"][k], att["regionUVs"][k + 1]
                ex, ey = _original_to_page(r, u * r.original_width, v * r.original_height)
                assert att["uvs"][k] * pw == pytest.approx(ex, abs=0.02), (r.name, k)
                assert att["uvs"][k + 1] * ph == pytest.approx(ey, abs=0.02), (r.name, k)
