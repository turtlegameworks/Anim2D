"""Render an animation to PNG frames or a sprite sheet (approximate renderer: regions only)."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from ..preview.qt_renderer import ImageCache, render_image, world_bounds
from .document import Document, EditorError


def _cache(doc: Document) -> ImageCache:
    cache = ImageCache()
    cache.set_images({p: f for p in doc.used_images() if (f := doc.image_file(p)) is not None})
    return cache


def render_frames(doc: Document, animation: str, out_dir: Path, size: int = 512, fps: float | None = None,
                  sheet: bool = False, overwrite: bool = False) -> list[Path]:
    """Write <animation>_0000.png ... (or one <animation>_sheet.png). Returns written files."""
    if animation not in doc.data["animations"]:
        raise EditorError(f"No animation named '{animation}'")
    fps = fps or doc.fps
    evaluator = doc.evaluator(animation)
    duration = evaluator.duration
    count = max(1, int(math.floor(duration * fps + 1e-6)) + 1)
    bounds = world_bounds(evaluator, samples=max(16, count * 2))  # one framing for every frame
    if bounds.isEmpty():
        raise EditorError(f"'{animation}' shows no images to render")
    cache = _cache(doc)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = [render_image(evaluator, cache, i / fps, size, None, bounds) for i in range(count)]
    if sheet:
        cols = math.ceil(math.sqrt(count))
        rows = math.ceil(count / cols)
        target = out_dir / f"{animation}_sheet.png"
        if target.exists() and not overwrite:
            raise EditorError(f"{target.name} already exists")
        image = QImage(cols * size, rows * size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        for i, frame in enumerate(frames):
            p.drawImage((i % cols) * size, (i // cols) * size, frame)
        p.end()
        image.save(str(target))
        return [target]
    files = [out_dir / f"{animation}_{i:04d}.png" for i in range(count)]
    existing = [f for f in files if f.exists()]
    if existing and not overwrite:
        raise EditorError(f"{existing[0].name} already exists")
    for frame, file in zip(frames, files):
        frame.save(str(file))
    return files
