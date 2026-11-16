"""Playblast: record an animation exactly as the viewport frames it (zoom, pan, background, grid),
without the tools, gizmos and selection, to a video, an animated image or an image sequence.

Unlike File > Export (which frames the skeleton itself), a playblast is a quick review render of
what the animator sees. Frames are painted by the viewport in capture mode, then written with the
same writers as the media export.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable

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


def render_frames(viewport, animation: str, s: PlayblastSettings,
                  progress: Callable[[int, int], bool] | None = None) -> list[QImage]:
    """Paint every frame with the viewport in capture mode. progress(done, total) returning False
    cancels (raises EditorError). The editor's mode, animation, time and pose come back afterwards."""
    st = viewport.state
    doc = st.doc
    if animation not in doc.data["animations"]:
        raise EditorError("Choose an animation to playblast")
    first, last, fps = frame_range(doc, animation, s)
    frames = list(range(first, last + 1))
    length = max(0, round(doc.animation_duration(animation) * fps))  # the counter shows "frame N / length"
    w, h = capture_size(viewport, s)
    k = w / max(1, viewport.width()), h / max(1, viewport.height())
    saved = (st.mode, st.animation, st.time, st.pose_overrides)
    images: list[QImage] = []
    try:
        st.mode, st.animation, st.pose_overrides = "animate", animation, {}  # quietly: no signals per frame
        total = len(frames) * max(1, s.repeat)
        for loop in range(max(1, s.repeat)):
            for i, frame in enumerate(frames):
                if progress is not None and not progress(len(images), total):
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
                images.append(image)
    finally:
        viewport.capture = None
        st.mode, st.animation, st.time, st.pose_overrides = saved
        viewport.update()
    return images


def write(images: list[QImage], path: Path, s: PlayblastSettings, fps: float) -> list[Path]:
    """Write the frames in the chosen format. Returns the files written."""
    if not images:
        raise EditorError("Nothing to record")
    files = files_for(s, path, len(images))
    if not s.overwrite:
        for f in files:
            if f.exists():
                raise EditorError(f"{f.name} already exists. Tick Overwrite or choose another name.")
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = mx.MediaExport(kind=s.format, fps=fps, transparent=False, background="#000000",
                              jpeg_quality=max(1, min(100, int(s.quality))), encoding="png", forever=True)
    if s.format == "avi":
        settings.encoding = "mjpeg"
    frames = [mx.to_pil(i) for i in images]
    writer = {"gif": mx.write_gif, "apng": mx.write_apng, "webp": mx.write_webp, "avi": mx.write_avi,
              "mov": mx.write_mov, "mp4": mx.write_mp4}.get(s.format)
    if writer is not None:
        writer(frames, files[0], settings)
    else:
        for img, f in zip(frames, files):
            flat = mx.flatten(img, "#000000")
            if s.format == "jpeg":
                flat.save(f, "JPEG", quality=settings.jpeg_quality)
            else:
                flat.save(f, "PNG", compress_level=6)
    return files


def playblast(viewport, s: PlayblastSettings, progress: Callable[[int, int], bool] | None = None) -> list[Path]:
    st = viewport.state
    animation = s.animation if s.animation in st.doc.data["animations"] else st.animation
    if not animation:
        raise EditorError("Make an animation first: a playblast records one")
    path = output_path(s, animation)
    if not s.overwrite and FORMATS[s.format][2] and path.exists():
        raise EditorError(f"{path.name} already exists. Tick Overwrite or choose another name.")
    _, _, fps = frame_range(st.doc, animation, s)
    images = render_frames(viewport, animation, s, progress)
    return write(images, path, s, fps)


def reveal(path: Path) -> None:
    """Open a video or image in the system's player; an image sequence opens its folder."""
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - the user's own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
