"""Texture packer: packs attachment images into atlas pages and writes a Spine 4.0 .atlas file.

The .atlas text is the libgdx format the Spine 4.0 runtimes read (TextureAtlas): per page its
image name, size, filter and pma; per region its name and

  bounds: x, y, width, height       position on the page; width/height before rotation
  offsets: x, y, origW, origH       only when whitespace was stripped; y counts from the bottom
  rotate: 90                        stored turned 90 degrees counter-clockwise on the page

Packing is MaxRects (best short side fit), largest images first, onto as few pages as fit
the maximum page size; each page is then shrunk to its content (to a power of two if asked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import re

from PIL import Image, ImageChops, ImageDraw, ImageMath


RESAMPLE = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR,
            "bicubic": Image.Resampling.BICUBIC, "lanczos": Image.Resampling.LANCZOS, "box": Image.Resampling.BOX}
FILTERS = ["Nearest", "Linear", "MipMap", "MipMapNearestNearest", "MipMapLinearNearest",
           "MipMapNearestLinear", "MipMapLinearLinear"]
WRAPS = ["ClampToEdge", "Repeat", "MirroredRepeat"]
FORMATS = ["RGBA8888", "RGBA4444", "RGB888", "RGB565", "Alpha", "Intensity", "LuminanceAlpha"]


@dataclass
class PackSettings:
    """Texture packer settings (the defaults keep the packer's original behaviour; the Texture Packer
    dialog starts from Spine's defaults)."""
    max_width: int = 2048
    max_height: int = 2048
    padding: int = 2  # X: transparent pixels between regions (and around the page edge with edge_padding)
    strip_whitespace: bool = True  # X
    rotation: bool = True
    scale: float = 1.0
    power_of_two: bool = False
    premultiply_alpha: bool = False
    filter_min: str = "Linear"
    filter_mag: str = "Linear"
    padding_y: int | None = None  # None: same as padding
    strip_whitespace_y: bool | None = None  # None: same as strip_whitespace
    alias: bool = False  # identical images share one region on the page
    ignore_blank: bool = False  # leave fully transparent images out
    alpha_threshold: int = 0  # pixels with alpha at or below this count as whitespace
    edge_padding: bool = True
    duplicate_padding: bool = False  # repeat the edge pixels into the padding
    min_width: int = 0
    min_height: int = 0
    divisible_by_4: bool = False
    square: bool = False
    wrap_x: str = "ClampToEdge"
    wrap_y: str = "ClampToEdge"
    format: str = "RGBA8888"
    output_format: str = "png"  # or "jpg" (no alpha)
    jpeg_quality: int = 90
    packing: str = "rectangles"  # or "grid": equal cells
    bleed: bool = False  # transparent pixels take the colour of their neighbours
    resample: str = "lanczos"
    scales: list[tuple[float, str, str]] = field(default_factory=list)  # (scale, suffix, resample); empty: scale
    atlas_extension: str = ".atlas"
    flatten_paths: bool = False
    use_indexes: bool = False  # name_3 -> region "name" with index 3
    debug: bool = False  # outline the regions on the pages
    fast: bool = False  # one packing pass instead of trying several heuristics
    limit_memory: bool = True  # pages are written one at a time (they are small; kept for Spine parity)

    @property
    def pad_x(self) -> int:
        return max(0, int(self.padding))

    @property
    def pad_y(self) -> int:
        return max(0, int(self.padding if self.padding_y is None else self.padding_y))

    @property
    def strip_x(self) -> bool:
        return self.strip_whitespace

    @property
    def strip_y(self) -> bool:
        return self.strip_whitespace if self.strip_whitespace_y is None else self.strip_whitespace_y

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> "PackSettings":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in values.items() if k in known}
        if "scales" in clean:
            clean["scales"] = [tuple(s) for s in clean["scales"]]
        return cls(**clean)


@dataclass
class PackedRegion:
    name: str
    page: int
    x: int  # on the page
    y: int
    width: int  # packed (stripped, scaled) size, before rotation
    height: int
    offset_x: int  # stripped area inside the original, offset_y from the bottom
    offset_y: int
    original_width: int
    original_height: int
    rotated: bool
    index: int = -1  # use_indexes: the number taken off the name
    degrees: int = 0  # unpacking: 90, 180 or 270 (rotated means 90)

    @property
    def stripped(self) -> bool:
        return (self.offset_x, self.offset_y, self.original_width, self.original_height) != \
            (0, 0, self.width, self.height)


@dataclass
class PackResult:
    pages: list[Image.Image] = field(default_factory=list)
    page_names: list[str] = field(default_factory=list)
    regions: list[PackedRegion] = field(default_factory=list)
    settings: PackSettings = field(default_factory=PackSettings)

    def atlas_text(self) -> str:
        lines: list[str] = []
        s = self.settings
        repeat = ("x" if s.wrap_x != "ClampToEdge" else "") + ("y" if s.wrap_y != "ClampToEdge" else "")
        for index, (page, name) in enumerate(zip(self.pages, self.page_names)):
            if lines:
                lines.append("")
            lines += [name, f"size: {page.width},{page.height}"]
            if s.format != "RGBA8888":
                lines.append(f"format: {s.format}")
            lines.append(f"filter: {s.filter_min},{s.filter_mag}")
            if repeat:
                lines.append(f"repeat: {repeat}")
            if s.premultiply_alpha:
                lines.append("pma: true")
            for r in self.regions:
                if r.page != index:
                    continue
                lines += [r.name, f"bounds: {r.x},{r.y},{r.width},{r.height}"]
                if r.stripped:
                    lines.append(f"offsets: {r.offset_x},{r.offset_y},{r.original_width},{r.original_height}")
                if r.rotated:
                    lines.append("rotate: 90")
                if r.index != -1:
                    lines.append(f"index: {r.index}")
        return "\n".join(lines) + "\n"

    def files(self, atlas_file: Path) -> list[Path]:
        """Every file write() would create for this atlas: the .atlas and each page image."""
        atlas_file = Path(atlas_file)
        return [atlas_file] + [atlas_file.parent / name for name in self.page_names]

    def write(self, atlas_file: Path) -> list[Path]:
        """Write <name>.atlas and its page images next to it. Returns the files written."""
        atlas_file = Path(atlas_file)
        atlas_file.parent.mkdir(parents=True, exist_ok=True)
        written = []
        for page, name in zip(self.pages, self.page_names):
            target = atlas_file.parent / name
            if target.suffix.lower() in (".jpg", ".jpeg"):
                page.convert("RGB").save(target, quality=self.settings.jpeg_quality)
            else:
                page.save(target)
            written.append(target)
        atlas_file.write_text(self.atlas_text(), encoding="utf-8")
        return [atlas_file] + written


class PackError(ValueError):
    pass


class _MaxRects:
    def __init__(self, width: int, height: int, heuristic: str = "short") -> None:
        self.free = [(0, 0, width, height)]
        self.heuristic = heuristic

    def find(self, w: int, h: int, rotation: bool) -> tuple[int, int, bool] | None:
        best = None
        for fx, fy, fw, fh in self.free:
            for rw, rh, rot in ((w, h, False), (h, w, True)) if rotation and w != h else ((w, h, False),):
                if rw <= fw and rh <= fh:
                    if self.heuristic == "area":
                        score = (fw * fh - rw * rh, min(fw - rw, fh - rh))
                    elif self.heuristic == "bottom":
                        score = (fy + rh, fx)
                    else:
                        score = (min(fw - rw, fh - rh), max(fw - rw, fh - rh))
                    if best is None or score < best[0]:
                        best = (score, fx, fy, rot)
        return None if best is None else best[1:]

    def place(self, x: int, y: int, w: int, h: int) -> None:
        out = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                out.append((fx, fy, fw, fh))
                continue
            if x > fx:
                out.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                out.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                out.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                out.append((fx, y + h, fw, fy + fh - y - h))
        self.free = [r for i, r in enumerate(out)
                     if not any(i != j and _contains(o, r) and (o != r or j < i) for j, o in enumerate(out))]


def _contains(a, b) -> bool:
    return a[0] <= b[0] and a[1] <= b[1] and a[0] + a[2] >= b[0] + b[2] and a[1] + a[3] >= b[1] + b[3]


def _pot(v: int) -> int:
    p = 1
    while p < v:
        p <<= 1
    return p


def _premultiply(image: Image.Image) -> Image.Image:
    r, g, b, a = image.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a), ImageChops.multiply(b, a), a))


def _unpremultiply(image: Image.Image) -> Image.Image:
    r, g, b, a = image.split()
    channels = [ImageMath.lambda_eval(lambda v: v["c"] * 255 / (v["a"] + 0.0001), c=c.convert("F"),
                                      a=a.convert("F")).convert("L")  # F -> L clamps to 0..255
                for c in (r, g, b)]
    return Image.merge("RGBA", channels + [a])


def _bleed(page: Image.Image, iterations: int = 4) -> Image.Image:
    """Give transparent pixels the colour of their nearest opaque neighbours (alpha stays 0), so
    filtering at region edges does not pull in black."""
    rgb = page.convert("RGB")
    alpha = page.getchannel("A")
    filled = alpha.point(lambda a: 255 if a > 0 else 0)
    for _ in range(iterations):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            take = ImageChops.subtract(ImageChops.offset(filled, dx, dy), filled)  # filled there, empty here
            rgb = Image.composite(ImageChops.offset(rgb, dx, dy), rgb, take)
            filled = ImageChops.lighter(filled, take)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _region_name(name: str, s: PackSettings) -> tuple[str, int]:
    if s.flatten_paths:
        name = name.rsplit("/", 1)[-1]
    if s.use_indexes:
        m = re.fullmatch(r"(.*?)_?(\d+)", name)
        if m and m.group(1):
            return m.group(1), int(m.group(2))
    return name, -1


def _prepare(images: dict[str, Image.Image], s: PackSettings, scale: float, resample: str) -> list[tuple]:
    """(name, crop, offset_x, offset_y, original_w, original_h, alias_key) per image."""
    prepared = []
    thr = max(0, min(254, int(s.alpha_threshold)))
    for region_name, image in sorted(images.items()):
        image = image.convert("RGBA")
        if scale != 1:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(size, RESAMPLE.get(resample, Image.Resampling.LANCZOS))
        ow, oh = image.size
        mask = image.getchannel("A")
        if thr:
            mask = mask.point(lambda a: 255 if a > thr else 0)
        content = mask.getbbox()
        if content is None and s.ignore_blank:
            continue
        if content is None:  # fully transparent: keep one pixel when stripping
            box = (0, 0, 1 if s.strip_x else ow, 1 if s.strip_y else oh)
        else:
            box = (content[0] if s.strip_x else 0, content[1] if s.strip_y else 0,
                   content[2] if s.strip_x else ow, content[3] if s.strip_y else oh)
        crop = image.crop(box)
        key = (crop.size, crop.tobytes(), box[0], oh - box[3], ow, oh) if s.alias else None
        prepared.append((region_name, crop, box[0], oh - box[3], ow, oh, key))
    return prepared


def _layout(items: list[tuple], s: PackSettings, heuristic: str) -> list[list[tuple]]:
    """Pages of (item, x, y, rotated) with x/y the region's top-left on the page."""
    pad_x, pad_y = s.pad_x, s.pad_y
    edge_x, edge_y = (pad_x, pad_y) if s.edge_padding else (0, 0)
    pages = []
    if s.packing == "grid":
        cell_w = max(c.width for _, c, *_ in items) + pad_x
        cell_h = max(c.height for _, c, *_ in items) + pad_y
        cols = max(1, (s.max_width - 2 * edge_x + pad_x) // cell_w)
        rows = max(1, (s.max_height - 2 * edge_y + pad_y) // cell_h)
        per_page = cols * rows
        for start in range(0, len(items), per_page):
            pages.append([(item, edge_x + (i % cols) * cell_w, edge_y + (i // cols) * cell_h, False)
                          for i, item in enumerate(items[start:start + per_page])])
        return pages
    pending = items
    while pending:
        bins = _MaxRects(s.max_width - 2 * edge_x + pad_x, s.max_height - 2 * edge_y + pad_y, heuristic)
        placed, left = [], []
        for item in pending:
            crop = item[1]
            spot = bins.find(crop.width + pad_x, crop.height + pad_y, s.rotation)
            if spot is None:
                left.append(item)
                continue
            x, y, rot = spot
            w, h = (crop.height, crop.width) if rot else (crop.width, crop.height)
            bins.place(x, y, w + pad_x, h + pad_y)
            placed.append((item, x + edge_x, y + edge_y, rot))
        if not placed:
            big = left[0][1]
            raise PackError(f"'{left[0][0]}' ({big.width}x{big.height}) does not fit a "
                            f"{s.max_width}x{s.max_height} page")
        pages.append(placed)
        pending = left
    return pages


def _page_size(placed: list[tuple], s: PackSettings) -> tuple[int, int]:
    edge_x, edge_y = (s.pad_x, s.pad_y) if s.edge_padding else (0, 0)
    width = max(x + (c.height if rot else c.width) for (_, c, *_), x, _, rot in placed) + edge_x
    height = max(y + (c.width if rot else c.height) for (_, c, *_), _, y, rot in placed) + edge_y
    width, height = max(width, s.min_width), max(height, s.min_height)
    if s.divisible_by_4:
        width, height = (width + 3) // 4 * 4, (height + 3) // 4 * 4
    if s.power_of_two:
        width, height = _pot(width), _pot(height)
    if s.square:
        width = height = max(width, height)
    return width, height


def _duplicate_edges(page: Image.Image, tile: Image.Image, x: int, y: int, dx: int, dy: int) -> None:
    """Repeat the tile's edge pixels dx/dy pixels into the padding around it."""
    w, h = tile.size
    if dx:
        page.paste(tile.crop((0, 0, 1, h)).resize((dx, h)), (x - dx, y))
        page.paste(tile.crop((w - 1, 0, w, h)).resize((dx, h)), (x + w, y))
    if dy:
        page.paste(tile.crop((0, 0, w, 1)).resize((w, dy)), (x, y - dy))
        page.paste(tile.crop((0, h - 1, w, h)).resize((w, dy)), (x, y + h))
    if dx and dy:
        for cx, cy, px, py in ((0, 0, x - dx, y - dy), (w - 1, 0, x + w, y - dy),
                               (0, h - 1, x - dx, y + h), (w - 1, h - 1, x + w, y + h)):
            page.paste(tile.getpixel((cx, cy)), (px, py, px + dx, py + dy))


def _pack_scale(images: dict[str, Image.Image], name: str, s: PackSettings, scale: float,
                resample: str) -> PackResult:
    if not 0.01 <= scale <= 10:
        raise PackError("scale must be between 0.01 and 10")
    prepared = _prepare(images, s, scale, resample)
    unique, aliases = [], {}
    for item in prepared:  # identical images (alias) are packed once
        key = item[6]
        if key is not None and key in aliases:
            aliases[key].append(item)
            continue
        if key is not None:
            aliases[key] = []
        unique.append(item)
    for region_name, crop, *_ in unique:
        w, h = crop.width + 2 * s.pad_x, crop.height + 2 * s.pad_y
        if not (w <= s.max_width and h <= s.max_height or s.rotation and h <= s.max_width and w <= s.max_height):
            raise PackError(f"'{region_name}' ({crop.width}x{crop.height}) does not fit a "
                            f"{s.max_width}x{s.max_height} page")
    result = PackResult(settings=s)
    if not unique:
        return result
    unique.sort(key=lambda p: (-max(p[1].width, p[1].height), -p[1].width * p[1].height, p[0]))
    best = None
    for heuristic in ("short",) if s.fast or s.packing == "grid" else ("short", "area", "bottom"):
        pages = _layout(unique, s, heuristic)
        area = sum(w * h for w, h in (_page_size(pl, s) for pl in pages))
        if best is None or (len(pages), area) < best[0]:
            best = ((len(pages), area), pages)
    ext = ".jpg" if s.output_format.lower() in ("jpg", "jpeg") else ".png"
    dx, dy = (s.pad_x // 2, s.pad_y // 2) if s.duplicate_padding else (0, 0)
    for index, placed in enumerate(best[1]):
        width, height = _page_size(placed, s)
        page = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for item, x, y, rot in placed:
            region_name, crop, off_x, off_y, ow, oh, key = item
            tile = crop.transpose(Image.Transpose.ROTATE_90) if rot else crop
            page.paste(tile, (x, y))
            if dx or dy:
                _duplicate_edges(page, tile, x, y, dx, dy)
            for same in [item] + (aliases.get(key, []) if key is not None else []):
                shown, number = _region_name(same[0], s)
                result.regions.append(PackedRegion(shown, index, x, y, crop.width, crop.height,
                                                   off_x, off_y, ow, oh, rot, number))
        if s.bleed:
            page = _bleed(page)
        if s.premultiply_alpha:
            page = _premultiply(page)
        if s.debug:
            draw = ImageDraw.Draw(page)
            for r in result.regions:
                if r.page == index:
                    w, h = (r.height, r.width) if r.rotated else (r.width, r.height)
                    draw.rectangle((r.x, r.y, r.x + w - 1, r.y + h - 1), outline=(255, 0, 255, 255))
        result.pages.append(page)
        result.page_names.append(f"{name}{ext}" if index == 0 else f"{name}_{index + 1}{ext}")
    result.regions.sort(key=lambda r: (r.page, r.name, r.index))
    return result


def pack(images: dict[str, Image.Image], name: str, settings: PackSettings | None = None) -> PackResult:
    """images: region name (the attachment path, e.g. 'parts/arm') -> image. name: page file stem."""
    s = settings or PackSettings()
    return _pack_scale(images, name, s, s.scale, s.resample)


def pack_all(images: dict[str, Image.Image], name: str, settings: PackSettings) -> list[tuple[str, PackResult]]:
    """One atlas per entry of settings.scales (or the single scale): [(atlas name, result)]."""
    scales = settings.scales or [(settings.scale, "", settings.resample)]
    return [(f"{name}{suffix}", _pack_scale(images, f"{name}{suffix}", settings, float(scale), resample))
            for scale, suffix, resample in scales]


def load_folder(folder: Path) -> dict[str, Image.Image]:
    """Every PNG/JPEG under folder, named by its path without extension ('parts/arm')."""
    folder = Path(folder)
    if not folder.is_dir():
        raise PackError(f"{folder} is not a folder")
    images = {}
    for file in sorted(folder.rglob("*")):
        if file.suffix.lower() in (".png", ".jpg", ".jpeg") and file.is_file():
            name = file.relative_to(folder).with_suffix("").as_posix()
            with Image.open(file) as img:
                images[name] = img.convert("RGBA")
    if not images:
        raise PackError(f"No PNG or JPEG images in {folder}")
    return images


def pack_folder(input_dir: Path, output_dir: Path, name: str, settings: PackSettings) -> list[Path]:
    """Texture Packer: pack every image under input_dir into <output_dir>/<name><atlas extension>."""
    images = load_folder(input_dir)
    written = []
    for atlas_name, result in pack_all(images, name, settings):
        written += result.write(Path(output_dir) / f"{atlas_name}{settings.atlas_extension or '.atlas'}")
    return written


# --- unpacking ------------------------------------------------------------------------------

def read_atlas(text: str) -> tuple[list[tuple[str, dict[str, str]]], list[PackedRegion]]:
    """Parse a Spine 4.0 or 3.8 .atlas. Returns ([(page name, fields)], regions)."""
    pages: list[tuple[str, dict[str, str]]] = []
    regions: list[PackedRegion] = []
    page: dict[str, str] | None = None
    region: dict[str, str] | None = None
    name = ""

    def finish() -> None:
        if region is None:
            return
        f = region
        if "bounds" in f:
            x, y, w, h = (int(v) for v in f["bounds"].split(","))
        else:  # 3.8: xy + size
            x, y = (int(v) for v in f["xy"].split(","))
            w, h = (int(v) for v in f["size"].split(","))
        rot = f.get("rotate", "false")
        degrees = 90 if rot == "true" else (int(rot) % 360 if rot.lstrip("-").isdigit() else 0)
        if "offsets" in f:
            ox, oy, ow, oh = (int(v) for v in f["offsets"].split(","))
        elif "orig" in f:
            ow, oh = (int(v) for v in f["orig"].split(","))
            ox, oy = (int(v) for v in f.get("offset", "0,0").split(","))
        else:
            ox, oy, ow, oh = 0, 0, w, h
        regions.append(PackedRegion(name, len(pages) - 1, x, y, w, h, ox, oy, ow, oh, degrees == 90,
                                    int(f.get("index", "-1")), degrees))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            finish()
            region, page = None, None
            continue
        if ":" in line and (region is not None or page is not None):
            key, value = (part.strip() for part in line.split(":", 1))
            (region if region is not None else page)[key] = value.replace(" ", "")
            continue
        finish()
        region = None
        if page is None:
            page = {}
            pages.append((line, page))
        else:
            region, name = {}, line
    finish()
    return pages, regions


def _inside(root: Path, name: str, what: str) -> Path:
    """root/name, refusing names from the atlas that would reach outside root (.., absolute or drive
    paths, alternate data streams)."""
    pure = PurePosixPath(name.replace("\\", "/"))
    if not name or pure.is_absolute() or ".." in pure.parts or ":" in name:
        raise PackError(f"{what} name '{name}' must be a relative path inside the folder")
    path = (root / pure).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise PackError(f"{what} name '{name}' leads outside {root}")
    return path


def unpack(atlas_file: Path, output_dir: Path, images_dir: Path | None = None) -> list[Path]:
    """Texture Unpacker: write every region of an atlas as its own PNG at its original size
    (whitespace and rotation restored, premultiplied alpha undone). A region with an index is
    written as <name>_<index>.png."""
    atlas_file = Path(atlas_file)
    images_dir = Path(images_dir) if images_dir else atlas_file.parent
    try:
        pages, regions = read_atlas(atlas_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, KeyError) as exc:
        raise PackError(f"Cannot read {atlas_file.name}: {exc}") from None
    if not pages:
        raise PackError(f"{atlas_file.name} has no pages")
    loaded = []
    for page_name, fields in pages:
        file = _inside(images_dir, page_name, "Page")
        if not file.is_file():
            raise PackError(f"Page image {page_name} not found in {images_dir}")
        with Image.open(file) as img:
            page = img.convert("RGBA")
        loaded.append(_unpremultiply(page) if fields.get("pma") == "true" else page)
    written = []
    output_dir = Path(output_dir)
    targets = [_inside(output_dir, f"{r.name if r.index == -1 else f'{r.name}_{r.index}'}.png", "Region")
               for r in regions]  # all checked before anything is written
    for r, target in zip(regions, targets):
        turned = r.degrees in (90, 270)
        pw, ph = (r.height, r.width) if turned else (r.width, r.height)
        tile = loaded[r.page].crop((r.x, r.y, r.x + pw, r.y + ph))
        if r.degrees == 90:
            tile = tile.transpose(Image.Transpose.ROTATE_270)
        elif r.degrees == 180:
            tile = tile.transpose(Image.Transpose.ROTATE_180)
        elif r.degrees == 270:
            tile = tile.transpose(Image.Transpose.ROTATE_90)
        image = Image.new("RGBA", (r.original_width, r.original_height), (0, 0, 0, 0))
        image.paste(tile, (r.offset_x, r.original_height - r.offset_y - r.height))
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        written.append(target)
    return written
