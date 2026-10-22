"""Image and video export, laid out like Spine's Export dialog.

Image: GIF, PNG (frames, optionally packed into a texture atlas), APNG, PSD (one layer per frame),
JPEG (frames) and animated WebP. Video: AVI (Motion JPEG or uncompressed with alpha), QuickTime
MOV (PNG with alpha, or JPEG) and MP4 (H.264 through Qt Multimedia).

Frames come from the editor's renderer. Export type picks the animation(s), the current pose or
the setup pose; animations and skins can be the current one, all, or one named. Size is a scale
(percent of world units) or a width or height in pixels; Multisample AA renders larger and scales
down; Viewport crop uses the area the editor's viewport shows.

AVI, MOV and PSD files are written here (RIFF, QuickTime atoms and Photoshop layers); GIF, PNG,
APNG, JPEG and WebP with Pillow.
"""

from __future__ import annotations

import io
import math
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

from ..formats import atlas as atlas_packer
from ..preview.qt_renderer import draw_frame, fit_view
from .document import Document, EditorError
from .render import _cache

KINDS = {  # key: (label, group, suffix)
    "json": ("JSON", "Data", ".json"), "binary": ("Binary", "Data", ".skel"),
    "gif": ("GIF", "Image", ".gif"), "png": ("PNG", "Image", ".png"), "apng": ("APNG", "Image", ".png"),
    "psd": ("PSD", "Image", ".psd"), "jpeg": ("JPEG", "Image", ".jpg"), "webp": ("WebP", "Image", ".webp"),
    "avi": ("AVI", "Video", ".avi"), "mov": ("MOV", "Video", ".mov"), "mp4": ("MP4", "Video", ".mp4"),
}
SINGLE_FILE = {"gif", "apng", "psd", "webp", "avi", "mov", "mp4"}  # the others write one file per frame
MSAA = [1, 2, 4, 8, 16]
BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


@dataclass
class MediaExport:
    kind: str = "gif"
    export_type: str = "animation"  # "animation", "current" (pose at the current time) or "setup"
    animations: str = "current"  # "current", "all" or "one"
    animation: str = ""  # with animations == "one"
    skins: str = "current"  # "current", "all" or "all_none" (all + no skin)
    output: str = ""  # a file (single-file kinds) or a prefix (frames)
    output_type: str = "single"  # single-file kinds: "single" (everything in one file) or "each" (per animation/skin)
    repeat: int = 1
    pause_after: float = 0.0  # seconds the last frame is held
    bones: bool = False
    images: bool = True
    others: bool = False  # bounding boxes, clipping, paths, points
    linear_filtering: bool = True
    msaa: int = 1
    crop: tuple[float, float, float, float] | None = None  # world rect (x, y, w, h), y up
    size_mode: str = "scale"  # "scale" (percent), "width" or "height" (pixels)
    size: float = 100
    frame_range: tuple[int, int] | None = None
    fps: float = 20
    include_last_frame: bool = False
    forever: bool = True
    transparent: bool = True
    background: str = "#000000"
    matte: str | None = None  # GIF: blend soft edges toward this colour
    colors: int = 256
    color_dither: int = 50
    alpha_threshold: int = 8
    alpha_dither: int = 40
    quality: int = 8  # GIF 1..10, JPEG/MJPEG/WebP 0..100 use jpeg_quality
    compression: int = 6  # PNG/APNG/MOV-PNG 0..9
    jpeg_quality: int = 90
    encoding: str = "png"  # MOV: "png" or "jpeg"; AVI: "mjpeg" or "raw"
    pack: bool = False  # PNG/JPEG frames: pack into a texture atlas
    pack_settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "MediaExport":
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in values.items() if k in known}
        for key in ("crop", "frame_range"):
            if clean.get(key) is not None:
                clean[key] = tuple(clean[key])
        return cls(**clean)


@dataclass
class Job:
    """One animation (or pose) in one skin, rendered at the given times."""
    animation: str  # "" = setup pose
    skin: str | None
    times: list[float]
    label: str


def plan(doc: Document, s: MediaExport, animation: str | None, skin: str | None, time: float = 0.0) -> list[Job]:
    names = doc.animation_names()
    if s.export_type == "setup":
        anims = [""]
    elif s.export_type == "current":
        if not animation or animation not in names:
            raise EditorError("Choose an animation to export its current pose")
        anims = [animation]
    elif s.animations == "all":
        anims = names
    elif s.animations == "one":
        anims = [s.animation]
    else:
        anims = [animation] if animation else []
    anims = [a for a in anims if a == "" or a in names]
    if not anims:
        raise EditorError("There is no animation to export; create one or choose the setup pose")
    skin_names = [n for n in doc.skin_names() if n != "default"]
    if s.skins == "all":
        skins = skin_names or [None]
    elif s.skins == "all_none":
        skins = [None] + skin_names
    else:
        skins = [skin if skin and skin != "default" else None]
    if not 1 <= s.fps <= 120:
        raise EditorError("FPS must be between 1 and 120")
    jobs = []
    for anim in anims:
        if s.export_type == "setup":
            times = [0.0]
        elif s.export_type == "current":
            times = [time]
        else:
            duration = doc.animation_duration(anim)
            count = int(math.floor(duration * s.fps + 1e-6))
            count = count + 1 if s.include_last_frame or count == 0 else count
            frames = list(range(count))
            if s.frame_range is not None:
                first, last = s.frame_range
                frames = [f for f in frames if first <= f <= last]
                if not frames:
                    raise EditorError(f"The frame range {first}-{last} is outside '{anim}' (0-{count - 1})")
            times = [f / s.fps for f in frames] * max(1, int(s.repeat))
        for sk in skins:
            label = (anim or "setup") + (f"_{sk}" if sk and len(skins) > 1 else "")
            jobs.append(Job(anim, sk, times, label))
    return jobs


def _evaluator(doc: Document, job: Job):
    from ..preview.evaluator import PoseEvaluator
    return PoseEvaluator(doc.data, job.animation, job.skin)


def _bone_segments(doc: Document, ev, t: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    world = ev.bone_world(t)
    out = []
    for bone in doc.data["bones"]:
        m = world.get(bone["name"])
        if m is None:
            continue
        length = float(bone.get("length", 0) or 0)
        out.append(((m[4], m[5]), (m[4] + m[0] * length, m[5] + m[1] * length)))
    return out


def bounds_of(doc: Document, s: MediaExport, jobs: list[Job]) -> QRectF:
    if s.crop is not None:
        return QRectF(*s.crop)
    rect = QRectF()

    def add(x: float, y: float) -> None:
        nonlocal rect
        rect = rect.united(QRectF(x - 0.5, y - 0.5, 1, 1))
    for job in jobs:
        ev = _evaluator(doc, job)
        for t in sorted(set(job.times))[:: max(1, len(set(job.times)) // 48)]:
            if s.images:
                for item in ev.draw_items(t):
                    for x, y in item.points():
                        add(x, y)
            if s.bones:
                for a, b in _bone_segments(doc, ev, t):
                    add(*a)
                    add(*b)
            if s.others:
                for shape in ev.shape_items(t):
                    for x, y in shape.points:
                        add(x, y)
    if rect.isEmpty():
        raise EditorError("Nothing to render: turn on images, bones or others, or show some attachments")
    return rect.adjusted(-2, -2, 2, 2)


def frame_size(s: MediaExport, bounds: QRectF) -> tuple[int, int, float]:
    """(width, height, pixels per world unit)."""
    if s.size_mode == "width":
        scale = s.size / max(bounds.width(), 1e-6)
    elif s.size_mode == "height":
        scale = s.size / max(bounds.height(), 1e-6)
    else:
        scale = s.size / 100
    if scale <= 0:
        raise EditorError("Size must be more than 0")
    width, height = max(1, math.ceil(bounds.width() * scale)), max(1, math.ceil(bounds.height() * scale))
    if width * s.msaa > 16384 or height * s.msaa > 16384:
        raise EditorError(f"{width}x{height} at {s.msaa}x multisampling is too large; lower the size")
    return width, height, scale


SHAPE_COLORS = {"boundingbox": "#5fd35f", "clipping": "#e04040", "path": "#ff9a2e", "point": "#e8e04a"}


def _draw_overlays(p: QPainter, doc: Document, ev, t: float, view, s: MediaExport, ms: int) -> None:
    if s.others:
        for shape in ev.shape_items(t):
            pen = QPen(QColor(SHAPE_COLORS.get(shape.kind, "#ffffff")), 1.5 * ms)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            points = [view.map(QPointF(x, y)) for x, y in shape.points]
            if shape.kind == "point" and points:
                p.drawEllipse(points[0], 3 * ms, 3 * ms)
                if len(points) > 1:
                    p.drawLine(points[0], points[1])
            elif shape.kind == "path":
                path = QPainterPath()
                knots = [points[i:i + 3] for i in range(0, len(points) - 2, 3)]
                for i, (_, point, _) in enumerate(knots):
                    if i == 0:
                        path.moveTo(point)
                    else:
                        path.cubicTo(knots[i - 1][2], knots[i][0], point)
                if shape.closed and len(knots) > 1:
                    path.cubicTo(knots[-1][2], knots[0][0], knots[0][1])
                p.drawPath(path)
            elif points:
                p.drawPolygon(QPolygonF(points))
    if s.bones:
        for bone, (a, b) in zip(doc.data["bones"], _bone_segments(doc, ev, t)):
            color = QColor("#" + str(bone.get("color", "9b9b9bff"))[:6])
            pa, pb = view.map(QPointF(*a)), view.map(QPointF(*b))
            p.setPen(QPen(color, 2 * ms))
            p.drawLine(pa, pb)
            p.setBrush(color)
            p.drawEllipse(pa, 2.5 * ms, 2.5 * ms)


def render_job(doc: Document, s: MediaExport, job: Job, bounds: QRectF) -> list[QImage]:
    """The job's frames, transparent, at the export size."""
    width, height, scale = frame_size(s, bounds)
    ms = s.msaa if s.msaa in MSAA else 1
    view = fit_view(bounds, width * ms, height * ms, margin=0, zoom=scale * ms)
    ev = _evaluator(doc, job)
    cache = _cache(doc)
    frames: dict[float, QImage] = {}
    out = []
    for t in job.times:
        if t not in frames:
            image = QImage(width * ms, height * ms, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if s.images:
                draw_frame(p, ev.draw_items(t), cache, view, smooth=s.linear_filtering)
            _draw_overlays(p, doc, ev, t, view, s, ms)
            p.end()
            if ms > 1:
                image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            frames[t] = image
        out.append(frames[t])
    return out


def to_pil(image: QImage) -> Image.Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()), "raw", "RGBA",
                            rgba.bytesPerLine(), 1).copy()


def to_qimage(image: Image.Image) -> QImage:
    data = image.convert("RGBA").tobytes()
    return QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()


def flatten(image: Image.Image, color: str) -> Image.Image:
    base = Image.new("RGBA", image.size, QColor(color).getRgb())
    base.alpha_composite(image)
    return base.convert("RGB")


# --- writers ---------------------------------------------------------------------------------

def _durations(count: int, fps: float, pause: float) -> list[int]:
    ms = [round(1000 / fps)] * count
    if count and pause > 0:
        ms[-1] += round(pause * 1000)
    return ms


def _alpha_mask(alpha: Image.Image, threshold: int, dither: int) -> Image.Image:
    """255 where the pixel stays opaque in the GIF: alpha above the threshold, dithered."""
    if dither <= 0:
        return alpha.point(lambda a: 255 if a >= max(1, threshold) else 0)
    w, h = alpha.size
    tile = Image.new("L", (4, 4))
    amount = dither / 100
    for y in range(4):
        for x in range(4):
            level = threshold + (BAYER4[y][x] + 0.5) / 16 * amount * (255 - threshold)
            tile.putpixel((x, y), max(1, min(255, round(level))))
    pattern = Image.new("L", (w, h))
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            pattern.paste(tile, (x, y))
    below = ImageChops.subtract(pattern, alpha)  # > 0 where alpha < level
    return below.point(lambda v: 0 if v > 0 else 255)


def write_gif(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    colors = max(2, min(256, int(s.colors)))
    q = max(1, min(10, int(s.quality)))
    method = Image.Quantize.FASTOCTREE if q <= 3 else Image.Quantize.MEDIANCUT
    dither = Image.Dither.FLOYDSTEINBERG if s.color_dither > 0 else Image.Dither.NONE
    out = []
    for img in frames:
        if s.transparent:
            alpha = img.getchannel("A")
            opaque = _alpha_mask(alpha, s.alpha_threshold, s.alpha_dither)
            if s.matte:
                rgb = flatten(img, s.matte)
            else:
                rgb = img.convert("RGB")
            pal = rgb.quantize(colors=colors - 1, method=method, kmeans=max(0, q - 7), dither=dither)
            palette = pal.getpalette() or []
            pal.putpalette(palette + [0] * (768 - len(palette)))
            pal.paste(colors - 1, mask=ImageChops.invert(opaque))
        else:
            pal = flatten(img, s.background).quantize(colors=colors, method=method, kmeans=max(0, q - 7),
                                                      dither=dither)
        out.append(pal)
    options = {"save_all": True, "append_images": out[1:], "duration": _durations(len(out), s.fps, s.pause_after),
               "disposal": 2, "optimize": False}
    if s.forever:
        options["loop"] = 0
    if s.transparent:
        options["transparency"] = colors - 1
    out[0].save(path, **options)


def write_apng(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    images = frames if s.transparent else [flatten(f, s.background).convert("RGBA") for f in frames]
    options = {"format": "PNG", "save_all": True, "append_images": images[1:],
               "duration": _durations(len(images), s.fps, s.pause_after), "disposal": 1,
               "compress_level": max(0, min(9, s.compression))}
    options["loop"] = 0 if s.forever else 1
    images[0].save(path, **options)


def write_webp(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    images = frames if s.transparent else [flatten(f, s.background).convert("RGBA") for f in frames]
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=_durations(len(images), s.fps, s.pause_after), loop=0 if s.forever else 1,
                   lossless=s.jpeg_quality >= 100, quality=max(1, min(100, s.jpeg_quality)))


def write_psd(frames: list[Image.Image], names: list[str], path: Path, s: MediaExport) -> None:
    """Photoshop file with one layer per frame (the first frame on top and visible)."""
    images = frames if s.transparent else [flatten(f, s.background).convert("RGBA") for f in frames]
    width, height = images[0].size
    be = struct.pack
    records, channel_data = io.BytesIO(), io.BytesIO()
    layers = list(zip(images, names))[::-1]  # Photoshop lists layers bottom first
    for i, (img, name) in enumerate(layers):
        planes = [img.getchannel(c).tobytes() for c in "ARGB"]
        records.write(be(">iiiiH", 0, 0, height, width, 4))
        for channel_id, plane in zip((-1, 0, 1, 2), planes):
            records.write(be(">hI", channel_id, 2 + len(plane)))
        visible = i == len(layers) - 1
        records.write(b"8BIMnorm" + be(">BBBB", 255, 0, 0 if visible else 2, 0))
        raw = name.encode("ascii", "replace")[:255]
        pascal = bytes([len(raw)]) + raw
        pascal += b"\0" * (-len(pascal) % 4)
        extra = be(">II", 0, 0) + pascal
        records.write(be(">I", len(extra)) + extra)
        for plane in planes:
            channel_data.write(be(">H", 0) + plane)
    info = be(">h", len(layers)) + records.getvalue() + channel_data.getvalue()
    info += b"\0" * (len(info) % 2)
    layer_section = be(">I", len(info)) + info + be(">I", 0)
    top = images[0]
    with open(path, "wb") as f:
        f.write(b"8BPS" + be(">H", 1) + b"\0" * 6 + be(">HIIHH", 4, height, width, 8, 3))
        f.write(be(">I", 0) + be(">I", 0))  # colour mode data, image resources
        f.write(be(">I", len(layer_section)) + layer_section)
        f.write(be(">H", 0) + b"".join(top.getchannel(c).tobytes() for c in "RGBA"))


def _even(frames: list[Image.Image], color: str) -> list[Image.Image]:
    """JPEG video (4:2:0 colour) needs even sizes: pad the right and bottom edges with the background."""
    w, h = frames[0].size
    if w % 2 == 0 and h % 2 == 0:
        return frames
    out = []
    for img in frames:
        padded = Image.new("RGBA", (w + w % 2, h + h % 2), QColor(color).getRgb())
        padded.paste(img, (0, 0))
        out.append(padded)
    return out


def _chunk(fourcc: bytes, data: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def _list(kind: bytes, data: bytes) -> bytes:
    return b"LIST" + struct.pack("<I", len(data) + 4) + kind + data


def write_avi(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    """AVI: Motion JPEG ('mjpeg') or uncompressed 32-bit BGRA ('raw', keeps alpha)."""
    raw = s.encoding == "raw"
    if not raw:
        frames = _even(frames, s.background)
    width, height = frames[0].size
    payloads = []
    for img in frames:
        if raw:
            src = img if s.transparent else flatten(img, s.background).convert("RGBA")
            payloads.append(src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes("raw", "BGRA"))
        else:
            buf = io.BytesIO()
            flatten(img, s.background).save(buf, "JPEG", quality=max(1, min(100, s.jpeg_quality)))
            payloads.append(buf.getvalue())
    hold = max(0, round(s.pause_after * s.fps))
    payloads += [payloads[-1]] * hold
    count = len(payloads)
    rate, scale = round(s.fps * 1000), 1000
    biggest = max(len(p) for p in payloads)
    avih = struct.pack("<IIIIIIIIII4I", round(1_000_000 / s.fps), int(biggest * s.fps), 0, 0x10, count, 0, 1,
                       biggest, width, height, 0, 0, 0, 0)
    handler = b"\0\0\0\0" if raw else b"MJPG"
    strh = (b"vids" + handler + struct.pack("<IHHIIIIIIiI", 0, 0, 0, 0, scale, rate, 0, count, biggest, -1, 0)
            + struct.pack("<hhhh", 0, 0, width, height))
    strf = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32 if raw else 24, 0 if raw else 0x47504A4D,
                       width * height * (4 if raw else 3), 0, 0, 0, 0)
    hdrl = _list(b"hdrl", _chunk(b"avih", avih) + _list(b"strl", _chunk(b"strh", strh) + _chunk(b"strf", strf)))
    fourcc = b"00db" if raw else b"00dc"
    movi, index, offset = io.BytesIO(), io.BytesIO(), 4
    for p in payloads:
        block = _chunk(fourcc, p)
        movi.write(block)
        index.write(fourcc + struct.pack("<III", 0x10, offset, len(p)))
        offset += len(block)
    body = b"AVI " + hdrl + _list(b"movi", movi.getvalue()) + _chunk(b"idx1", index.getvalue())
    if len(body) > 0xFFFFFFF0:
        raise EditorError("The AVI would be larger than 4 GB; lower the size or the frame count")
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _atom(kind: bytes, *parts: bytes) -> bytes:
    data = b"".join(parts)
    return struct.pack(">I", len(data) + 8) + kind + data


def write_mov(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    """QuickTime MOV, PNG (keeps alpha) or Photo-JPEG frames."""
    png = s.encoding != "jpeg"
    if not png:
        frames = _even(frames, s.background)
    width, height = frames[0].size
    samples = []
    for img in frames:
        buf = io.BytesIO()
        if png:
            src = img if s.transparent else flatten(img, s.background).convert("RGBA")
            src.save(buf, "PNG", compress_level=max(0, min(9, s.compression)))
        else:
            flatten(img, s.background).save(buf, "JPEG", quality=max(1, min(100, s.jpeg_quality)))
        samples.append(buf.getvalue())
    hold = max(0, round(s.pause_after * s.fps))
    samples += [samples[-1]] * hold
    timescale, step = round(s.fps * 1000), 1000
    duration = len(samples) * step
    be = struct.pack
    matrix = be(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    ftyp = _atom(b"ftyp", b"qt  ", be(">I", 0x200), b"qt  ")
    mdat_header = 8
    mdat_data = b"".join(samples)
    if len(mdat_data) > 0xFFFFFFF0:
        raise EditorError("The MOV would be larger than 4 GB; lower the size or the frame count")
    chunk_offset = len(ftyp) + mdat_header
    mvhd = _atom(b"mvhd", be(">IIIII", 0, 0, 0, timescale, duration), be(">IH", 0x10000, 0x100), b"\0" * 10,
                 matrix, b"\0" * 24, be(">I", 2))
    tkhd = _atom(b"tkhd", be(">IIIIII", 3, 0, 0, 1, 0, duration), b"\0" * 8, be(">hhhh", 0, 0, 0, 0), matrix,
                 be(">II", width << 16, height << 16))
    mdhd = _atom(b"mdhd", be(">IIIIIHH", 0, 0, 0, timescale, duration, 0, 0))
    name = b"Video Media Handler"
    hdlr = _atom(b"hdlr", be(">I", 0), b"mhlr", b"vide", be(">III", 0, 0, 0), bytes([len(name)]) + name)
    vmhd = _atom(b"vmhd", be(">IHHHH", 1, 0x40 if png and s.transparent else 0, 0x8000, 0x8000, 0x8000))
    dname = b"Data Handler"
    dhlr = _atom(b"hdlr", be(">I", 0), b"dhlr", b"alis", be(">III", 0, 0, 0), bytes([len(dname)]) + dname)
    dinf = _atom(b"dinf", _atom(b"dref", be(">II", 0, 1), _atom(b"alis", be(">I", 1))))
    codec = b"png " if png else b"jpeg"
    compressor = b"PNG" if png else b"Photo - JPEG"
    entry = (codec + b"\0" * 6 + be(">H", 1) + be(">HH", 0, 0) + b"appl" + be(">II", 0, 1023 if not png else 0)
             + be(">HH", width, height) + be(">II", 0x480000, 0x480000) + be(">IH", 0, 1)
             + bytes([len(compressor)]) + compressor + b"\0" * (31 - len(compressor))
             + be(">hh", 32 if png else 24, -1))
    stsd = _atom(b"stsd", be(">II", 0, 1), be(">I", len(entry) + 4) + entry)
    stts = _atom(b"stts", be(">III", 0, 1, len(samples)) + be(">I", step))
    stsc = _atom(b"stsc", be(">IIIII", 0, 1, 1, len(samples), 1))
    stsz = _atom(b"stsz", be(">III", 0, 0, len(samples)), b"".join(be(">I", len(x)) for x in samples))
    stco = _atom(b"stco", be(">III", 0, 1, chunk_offset))
    stbl = _atom(b"stbl", stsd, stts, stsc, stsz, stco)
    minf = _atom(b"minf", vmhd, dhlr, dinf, stbl)
    mdia = _atom(b"mdia", mdhd, hdlr, minf)
    moov = _atom(b"moov", mvhd, _atom(b"trak", tkhd, mdia))
    with open(path, "wb") as f:
        f.write(ftyp)
        f.write(be(">I", len(mdat_data) + 8) + b"mdat")
        f.write(mdat_data)
        f.write(moov)


def write_mp4(frames: list[Image.Image], path: Path, s: MediaExport) -> None:
    from .video import write_mp4 as qt_mp4
    hold = max(0, round(s.pause_after * s.fps))
    images = [to_qimage(flatten(f, s.background)) for f in frames + [frames[-1]] * hold]
    qt_mp4(images, path, s.fps)


def _frame_files(base: Path, count: int, suffix: str) -> list[Path]:
    if count == 1:
        return [base.with_name(base.name + suffix)]
    return [base.with_name(f"{base.name}_{i:04d}{suffix}") for i in range(count)]


def output_base(s: MediaExport) -> Path:
    if not s.output.strip():
        raise EditorError("Choose the output file or prefix")
    out = Path(s.output)
    suffix = KINDS[s.kind][2]
    if s.kind in SINGLE_FILE or out.suffix.lower() == suffix:
        out = out.with_suffix("")
    return out


def export_media(doc: Document, s: MediaExport, animation: str | None, skin: str | None, time: float = 0.0,
                 overwrite: bool = False) -> list[Path]:
    """Render and write everything the settings ask for. Returns the files written."""
    if s.kind not in KINDS or KINDS[s.kind][1] == "Data":
        raise EditorError(f"'{s.kind}' is not an image or video format")
    jobs = plan(doc, s, animation, skin, time)
    base = output_base(s)
    suffix = KINDS[s.kind][2]
    single = s.kind in SINGLE_FILE
    groups: list[tuple[Path, list[Job]]]
    if single and s.output_type == "single" or not single and len(jobs) == 1:
        groups = [(base, jobs)]
    else:
        groups = [(base.with_name(f"{base.name}_{j.label}"), [j]) for j in jobs]
    planned = []
    for target, group in groups:
        if single:
            files = [target.with_name(target.name + suffix)]
        elif s.pack:
            files = [target.with_name(target.name + ".atlas")]
        else:
            files = _frame_files(target, sum(len(j.times) for j in group), suffix)
        planned.append((target, group, files))
    if not overwrite:
        for _, _, files in planned:
            for f in files:
                if f.exists():
                    raise EditorError(f"{f.name} already exists")
    written: list[Path] = []
    for target, group, files in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        bounds = bounds_of(doc, s, group)
        frames, names = [], []
        for job in group:
            rendered = render_job(doc, s, job, bounds)
            frames += [to_pil(f) for f in rendered]
            names += [f"{job.label} {i:04d}" for i in range(len(rendered))]
        if s.kind == "gif":
            write_gif(frames, files[0], s)
        elif s.kind == "apng":
            write_apng(frames, files[0], s)
        elif s.kind == "webp":
            write_webp(frames, files[0], s)
        elif s.kind == "psd":
            write_psd(frames, names, files[0], s)
        elif s.kind == "avi":
            write_avi(frames, files[0], s)
        elif s.kind == "mov":
            write_mov(frames, files[0], s)
        elif s.kind == "mp4":
            write_mp4(frames, files[0], s)
        elif s.pack:
            settings = atlas_packer.PackSettings.from_dict(s.pack_settings)
            if s.kind == "jpeg":
                settings.output_format = "jpg"
                settings.jpeg_quality = s.jpeg_quality
            images = {name.replace(" ", "_"): (f if s.transparent and s.kind == "png"
                                               else flatten(f, s.background).convert("RGBA"))
                      for f, name in zip(frames, names)}
            try:
                packed = [(target.with_name(atlas_name + (settings.atlas_extension or ".atlas")), result)
                          for atlas_name, result in atlas_packer.pack_all(images, target.name, settings)]
                if not overwrite:  # the page images, which the plan above could not know about
                    for file in (f for atlas_file, result in packed for f in result.files(atlas_file)):
                        if file.exists():
                            raise EditorError(f"{file.name} already exists")
                for atlas_file, result in packed:
                    written += result.write(atlas_file)
            except atlas_packer.PackError as exc:
                raise EditorError(f"Cannot pack the frames: {exc}") from None
            continue
        else:
            for img, file in zip(frames, files):
                if s.kind == "jpeg":
                    flatten(img, s.background).save(file, "JPEG", quality=max(1, min(100, s.jpeg_quality)))
                else:
                    (img if s.transparent else flatten(img, s.background)).save(
                        file, "PNG", compress_level=max(0, min(9, s.compression)))
        written += files
    return written


def preview_frames(doc: Document, s: MediaExport, animation: str | None, skin: str | None,
                   time: float = 0.0, limit: int = 240) -> list[QImage]:
    """The first job's frames as they will be exported (for the dialog's Preview)."""
    jobs = plan(doc, s, animation, skin, time)
    job = jobs[0]
    job = Job(job.animation, job.skin, job.times[:limit], job.label)
    frames = render_job(doc, s, job, bounds_of(doc, s, [jobs[0]]))
    if not s.transparent or s.kind in ("jpeg", "mp4") or s.kind == "avi" and s.encoding == "mjpeg" or \
            s.kind == "mov" and s.encoding == "jpeg":
        frames = [to_qimage(flatten(to_pil(f), s.background)) for f in frames]
    return frames
