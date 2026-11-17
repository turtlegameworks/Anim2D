"""Playblast: record an animation exactly as the viewport frames it (zoom, pan, background, grid),
without the tools, gizmos and selection, to a video, an animated image or an image sequence.

Unlike File > Export (which frames the skeleton itself), a playblast is a quick review render of
what the animator sees. Frames are painted by the viewport in capture mode, one at a time, and
encoded on worker threads while the next frame paints. Image sequences, AVI, MOV and MP4 are
written as the frames come, so memory stays flat however long the playblast is; GIF keeps one
byte a pixel per frame, and WebP and APNG (which Pillow writes from all the frames) one RGBA image
per frame. The bytes are the same as the media export's writers produce from the same frames.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterator

from PIL import Image
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from . import media_export as mx
from .document import EditorError

FORMATS = {  # key: (label, suffix, one file)
    "mp4": ("MP4 video (H.264)", ".mp4", True),
    "mov": ("QuickTime MOV (PNG, lossless)", ".mov", True),
    "avi": ("AVI video (Motion JPEG)", ".avi", True),
    "gif": ("Animated GIF", ".gif", True),
    "webp": ("Animated WebP", ".webp", True),
    "apng": ("Animated PNG (APNG)", ".png", True),
    "png": ("PNG image sequence", ".png", False),
    "jpeg": ("JPEG image sequence", ".jpg", False),
}
SCALES = (25, 50, 75, 100, 150, 200)


@dataclass
class PlayblastSettings:
    format: str = "mp4"
    folder: str = ""
    name: str = ""  # file name without the suffix; empty: "<animation>_playblast"
    animation: str = ""  # empty: the current animation
    range: str = "animation"  # "animation" (all of it) or "custom" (start..end frames)
    start: int = 0
    end: int = 0
    fps: float = 0  # 0: the skeleton's fps
    scale: int = 100  # percent of the viewport's size
    quality: int = 90  # JPEG, Motion JPEG and WebP
    bones: bool = False
    grid: bool = False
    hud: bool = True  # burn the animation name and frame number in
    repeat: int = 1  # times the animation plays in the file
    overwrite: bool = False
    open_after: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any] | None) -> "PlayblastSettings":
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in (values or {}).items() if k in known})
        if s.format not in FORMATS:
            s.format = "mp4"
        return s


def frame_range(doc, animation: str, s: PlayblastSettings) -> tuple[int, int, float]:
    """(first frame, last frame, fps) the playblast records."""
    fps = float(s.fps) if s.fps and s.fps > 0 else doc.fps
    last = max(0, round(doc.animation_duration(animation) * fps))
    if s.range == "custom":
        first, end = max(0, int(s.start)), max(0, int(s.end))
        if end < first:
            raise EditorError("The end frame is before the start frame")
        return first, end, fps
    return 0, last, fps


def output_path(s: PlayblastSettings, animation: str) -> Path:
    """The file (or, for image sequences, the first frame's name without the number) written."""
    if not s.folder.strip():
        raise EditorError("Choose the folder to save the playblast in")
    name = (s.name.strip() or f"{animation}_playblast").replace("/", "_").replace("\\", "_")
    return Path(s.folder) / (name + FORMATS[s.format][1])


def files_for(s: PlayblastSettings, path: Path, count: int) -> list[Path]:
    if FORMATS[s.format][2]:
        return [path]
    return [path.with_name(f"{path.stem}_{i:04d}{path.suffix}") for i in range(count)]


def capture_size(viewport: QWidget, s: PlayblastSettings) -> tuple[int, int]:
    k = max(1, int(s.scale)) / 100
    w, h = max(2, round(viewport.width() * k)), max(2, round(viewport.height() * k))
    return w // 2 * 2, h // 2 * 2  # even: the video encoders need it


def frames(viewport, animation: str, s: PlayblastSettings,
           progress: Callable[[int, int], bool] | None = None) -> Iterator[QImage]:
    """Paint each frame with the viewport in capture mode, one at a time (opaque ARGB32 images).
    progress(done, total) returning False cancels (raises EditorError). The editor's mode,
    animation, time and pose come back when the generator finishes or is closed."""
    st = viewport.state
    doc = st.doc
    if animation not in doc.data["animations"]:
        raise EditorError("Choose an animation to playblast")
    first, last, fps = frame_range(doc, animation, s)
    numbers = list(range(first, last + 1))
    length = max(0, round(doc.animation_duration(animation) * fps))  # the counter shows "frame N / length"
    w, h = capture_size(viewport, s)
    k = w / max(1, viewport.width()), h / max(1, viewport.height())
    saved = (st.mode, st.animation, st.time, st.pose_overrides)
    total = len(numbers) * max(1, s.repeat)
    done = 0
    try:
        st.mode, st.animation, st.pose_overrides = "animate", animation, {}  # quietly: no signals per frame
        for _ in range(max(1, s.repeat)):
            for frame in numbers:
                if progress is not None and not progress(done, total):
                    raise EditorError("Playblast cancelled")
                st.time = frame / fps
                hud = f"{animation}   frame {frame} / {length}   {fps:g} fps" if s.hud else ""
                viewport.capture = {"bones": s.bones, "grid": s.grid, "hud": hud}
                image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(QColor("#000000"))
                p = QPainter(image)
                p.scale(*k)
                viewport.render(p, QPoint(), QRegion(), QWidget.RenderFlag.DrawWindowBackground)
                p.end()
                done += 1
                yield image
    finally:
        viewport.capture = None
        st.mode, st.animation, st.time, st.pose_overrides = saved
        viewport.update()


def frame_count(doc, animation: str, s: PlayblastSettings) -> int:
    first, last, _ = frame_range(doc, animation, s)
    return (last - first + 1) * max(1, s.repeat)


# Bytes a pixel each frame takes in memory for the formats Pillow writes from all the frames
# (measured: GIF keeps palette frames; APNG compares each frame with a kept copy of the last).
HELD_BYTES = {"gif": 1, "webp": 4, "apng": 8}


def memory_needed(doc, animation: str, s: PlayblastSettings, viewport) -> int:
    """Roughly how many bytes the frames take in memory while writing (0 for the formats written
    as the frames come)."""
    if s.format not in HELD_BYTES or animation not in doc.data["animations"]:
        return 0
    w, h = capture_size(viewport, s)
    return frame_count(doc, animation, s) * w * h * HELD_BYTES[s.format]


def to_rgb(image: QImage) -> Image.Image:
    """An opaque frame as an RGB Pillow image: the same pixels as flattening it on black."""
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB",
                            rgb.bytesPerLine(), 1)


def _workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2)))


def _media_settings(s: PlayblastSettings, fps: float) -> mx.MediaExport:
    return mx.MediaExport(kind=s.format, fps=fps, transparent=False, background="#000000",
                          jpeg_quality=max(1, min(100, int(s.quality))),
                          encoding="mjpeg" if s.format == "avi" else "png", forever=True)


def _jpeg(image: QImage, quality: int) -> bytes:
    buf = io.BytesIO()
    to_rgb(image).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _mov_png(image: QImage) -> bytes:
    buf = io.BytesIO()
    to_rgb(image).convert("RGBA").save(buf, "PNG", compress_level=6)  # 32-bit, as the MOV header says
    return buf.getvalue()


def _save_frame(image: QImage, path: Path, fmt: str, quality: int) -> None:
    if fmt == "jpeg":
        to_rgb(image).save(path, "JPEG", quality=quality)
    else:
        to_rgb(image).save(path, "PNG", compress_level=6)


def _pipeline(source: Iterator[Any], encode: Callable[[Any], Any], take: Callable[[Any], None]) -> None:
    """encode(frame) runs on worker threads while the next frames paint; take(result) gets the
    results in frame order. At most a few frames are in flight, so memory stays flat. (Pillow and
    Qt's image conversion release the GIL, so the encoding really runs in parallel.)"""
    workers = _workers()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        try:
            for image in source:
                pending.append(pool.submit(encode, image))
                while len(pending) > workers * 2:
                    take(pending.popleft().result())
            while pending:
                take(pending.popleft().result())
        except BaseException:
            for future in pending:
                future.cancel()
            raise


def write(source: Iterator[QImage], path: Path, s: PlayblastSettings, fps: float, count: int) -> list[Path]:
    """Encode the frames as they are painted and write them in the chosen format. Returns the
    files written. On an error or a cancel, what was written is removed."""
    files = files_for(s, path, count)
    if not s.overwrite:
        for f in files:
            if f.exists():
                raise EditorError(f"{f.name} already exists. Tick Overwrite or choose another name.")
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = _media_settings(s, fps)
    quality = settings.jpeg_quality
    fmt = s.format
    written: list[Path] = []
    try:
        if fmt == "mp4":
            from .video import write_mp4
            written.append(files[0])
            write_mp4(source, files[0], fps)
        elif fmt in ("png", "jpeg"):
            numbered = iter(files)

            def jobs() -> Iterator[tuple[QImage, Path]]:
                for image in source:
                    f = next(numbered)
                    written.append(f)  # before it is saved: a cancel removes the frames still being saved
                    yield image, f
            _pipeline(jobs(), lambda job: _save_frame(job[0], job[1], fmt, quality), lambda _: None)
        elif fmt in ("avi", "mov"):
            first = next(source, None)
            if first is None:
                raise EditorError("Nothing to record")
            writer = (mx.AviWriter if fmt == "avi" else mx.MovWriter)(files[0], first.width(), first.height(),
                                                                       settings)
            written.append(files[0])
            encode = (lambda image: _jpeg(image, quality)) if fmt == "avi" else _mov_png
            try:
                _pipeline(_chain(first, source), encode, writer.add)
            except BaseException:
                writer.abort()
                raise
            writer.close()
        else:  # gif, webp, apng: Pillow writes these from all the frames, kept as compact as possible
            if fmt == "gif":
                def encode(image: QImage) -> Image.Image:  # one byte a pixel
                    return mx.gif_frame(to_rgb(image).convert("RGBA"), settings)
            else:
                def encode(image: QImage) -> Image.Image:
                    return to_rgb(image).convert("RGBA")
            out: list[Image.Image] = []
            _pipeline(source, encode, out.append)
            if not out:
                raise EditorError("Nothing to record")
            written.append(files[0])
            if fmt == "gif":
                mx.save_gif(out, files[0], settings)
            else:  # the frames are opaque already: nothing to flatten
                (mx.write_webp if fmt == "webp" else mx.write_apng)(out, files[0], replace(settings, transparent=True))
    except BaseException:
        if hasattr(source, "close"):
            source.close()
        for f in written:
            f.unlink(missing_ok=True)
        raise
    return written


def _chain(first: QImage, rest: Iterator[QImage]) -> Iterator[QImage]:
    yield first
    yield from rest


def playblast(viewport, s: PlayblastSettings, progress: Callable[[int, int], bool] | None = None) -> list[Path]:
    st = viewport.state
    animation = s.animation if s.animation in st.doc.data["animations"] else st.animation
    if not animation:
        raise EditorError("Make an animation first: a playblast records one")
    path = output_path(s, animation)
    if not s.overwrite and FORMATS[s.format][2] and path.exists():
        raise EditorError(f"{path.name} already exists. Tick Overwrite or choose another name.")
    _, _, fps = frame_range(st.doc, animation, s)
    count = frame_count(st.doc, animation, s)
    source = frames(viewport, animation, s, progress)
    try:
        return write(source, path, s, fps, count)
    finally:
        source.close()


def reveal(path: Path) -> None:
    """Open a video or image in the system's player; an image sequence opens its folder."""
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - the user's own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
