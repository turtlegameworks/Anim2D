"""Draws PoseEvaluator output with QPainter (y-up world mapped to the widget)."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPolygonF, QTransform

from .evaluator import DrawItem, PoseEvaluator

BLEND_MODES = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "additive": QPainter.CompositionMode.CompositionMode_Plus,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "screen": QPainter.CompositionMode.CompositionMode_Screen,
}


class ImageCache:
    """Source images keyed by attachment path, plus tinted copies keyed by (path, rgb)."""

    def __init__(self) -> None:
        self._images: dict[str, QImage] = {}
        self._stamps: dict[str, tuple[str, float]] = {}  # key -> (file, modified time) it was loaded from
        self._tinted: dict[tuple[str, int, int, int], QImage] = {}

    def set_images(self, images: dict[str, Path]) -> None:
        """Load new images; reload any whose file changed since (re-exported art, live layer sync)."""
        for key, path in images.items():
            try:
                stamp = (str(path), Path(path).stat().st_mtime)
            except OSError:
                stamp = (str(path), 0.0)
            if key in self._images and self._stamps.get(key) == stamp:
                continue
            self._images[key] = QImage(str(path)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            self._stamps[key] = stamp
            for cached in [k for k in self._tinted if k[0] == key]:
                del self._tinted[cached]

    def tinted(self, key: str, r: float, g: float, b: float) -> QImage | None:
        source = self._images.get(key)
        if source is None:
            return None
        rgb = (round(r * 255), round(g * 255), round(b * 255))
        cache_key = (key, *rgb)
        if cache_key not in self._tinted:
            image = source.copy()
            p = QPainter(image)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(image.rect(), QColor(*rgb))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.drawImage(0, 0, source)
            p.end()
            if len(self._tinted) > 512:
                self._tinted.clear()
            self._tinted[cache_key] = image
        return self._tinted[cache_key]


def world_bounds(evaluator: PoseEvaluator, samples: int = 16) -> QRectF:
    rect = QRectF()
    for i in range(samples + 1):
        t = evaluator.duration * i / samples
        for item in evaluator.draw_items(t):
            for x, y in item.points():
                rect = rect.united(QRectF(x - 0.5, y - 0.5, 1, 1))
    return rect


def _triangle_transform(src, dst) -> QTransform | None:
    """Affine mapping three source points onto three destination points."""
    (x0, y0), (x1, y1), (x2, y2) = src
    (u0, v0), (u1, v1), (u2, v2) = dst
    sx1, sy1, sx2, sy2 = x1 - x0, y1 - y0, x2 - x0, y2 - y0
    det = sx1 * sy2 - sx2 * sy1
    if abs(det) < 1e-9:
        return None
    dx1, dy1, dx2, dy2 = u1 - u0, v1 - v0, u2 - u0, v2 - v0
    m11 = (dx1 * sy2 - dx2 * sy1) / det  # d(u)/dx
    m21 = (dx2 * sx1 - dx1 * sx2) / det  # d(u)/dy
    m12 = (dy1 * sy2 - dy2 * sy1) / det  # d(v)/dx
    m22 = (dy2 * sx1 - dy1 * sx2) / det  # d(v)/dy
    return QTransform(m11, m12, m21, m22, u0 - m11 * x0 - m21 * y0, v0 - m12 * x0 - m22 * y0)


def _draw_mesh(painter: QPainter, item: DrawItem, image: QImage, view: QTransform, smooth: bool = True) -> None:
    mesh = item.mesh
    device = [view.map(QPointF(x, y)) for x, y in mesh.vertices]
    if not device:
        return
    left = math.floor(min(p.x() for p in device)) - 2
    top = math.floor(min(p.y() for p in device)) - 2
    width = min(4096, math.ceil(max(p.x() for p in device)) + 2 - left)
    height = min(4096, math.ceil(max(p.y() for p in device)) + 2 - top)
    if width <= 0 or height <= 0:
        return
    # Draw the triangles opaque into a layer, then composite once with the slot's opacity/blend.
    layer = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
    w, h = image.width(), image.height()
    for tri in mesh.triangles:
        pts = [QPointF(device[i].x() - left, device[i].y() - top) for i in tri]
        cx, cy = sum(p.x() for p in pts) / 3, sum(p.y() for p in pts) / 3
        grown = []
        for p in pts:  # grow by half a pixel so neighbouring triangles leave no seams
            dx, dy = p.x() - cx, p.y() - cy
            length = math.hypot(dx, dy) or 1
            grown.append(QPointF(p.x() + dx / length * 0.6, p.y() + dy / length * 0.6))
        src = [(mesh.uvs[i][0] * w, mesh.uvs[i][1] * h) for i in tri]
        transform = _triangle_transform(src, [(p.x(), p.y()) for p in pts])
        if transform is None:
            continue
        path = QPainterPath()
        path.addPolygon(QPolygonF(grown))
        lp.save()
        lp.setClipPath(path)
        lp.setTransform(transform)
        lp.drawImage(0, 0, image)
        lp.restore()
    lp.end()
    painter.save()
    painter.resetTransform()
    painter.setOpacity(max(0.0, min(1.0, item.color[3])))
    painter.setCompositionMode(BLEND_MODES.get(item.blend, BLEND_MODES["normal"]))
    painter.drawImage(left, top, layer)
    painter.restore()


def draw_frame(painter: QPainter, items: list[DrawItem], cache: ImageCache, view: QTransform,
               smooth: bool = True) -> None:
    """view maps world (y up) to device coordinates."""
    for item in items:
        r, g, b, a = item.color
        if a <= 0.001:
            continue
        image = cache.tinted(item.image_path, r, g, b)
        if image is None:
            continue
        if item.clip:  # a clipping attachment: draw only inside its polygon
            painter.save()
            clip = QPainterPath()
            clip.addPolygon(view.map(QPolygonF([QPointF(x, y) for x, y in item.clip])))
            painter.setClipPath(clip)
        if item.mesh is not None:
            _draw_mesh(painter, item, image, view, smooth)
        else:
            ta, tb, tc, td, tx, ty = item.transform
            painter.save()
            painter.setTransform(QTransform(ta, tb, tc, td, tx, ty) * view)
            painter.setOpacity(max(0.0, min(1.0, a)))
            painter.setCompositionMode(BLEND_MODES.get(item.blend, BLEND_MODES["normal"]))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
            # Scale image pixels to the attachment's declared size.
            painter.drawImage(QRectF(0, 0, item.width, item.height), image)
            painter.restore()
        if item.clip:
            painter.restore()


def fit_view(bounds: QRectF, width: float, height: float, margin: float = 0.12, zoom: float | None = None) -> QTransform:
    """World->device transform centring bounds (or the origin when empty) in a width x height area."""
    if bounds.isEmpty():
        bounds = QRectF(-100, -100, 200, 200)
    if zoom is None:
        usable_w, usable_h = width * (1 - 2 * margin), height * (1 - 2 * margin)
        zoom = min(usable_w / max(bounds.width(), 1), usable_h / max(bounds.height(), 1), 4.0)
    center = bounds.center()
    view = QTransform()
    view.translate(width / 2, height / 2)
    view.scale(zoom, -zoom)  # y up -> y down
    view.translate(-center.x(), -center.y())
    return view


def render_image(evaluator: PoseEvaluator, cache: ImageCache, time: float, size: int,
                 background: QColor | None = None, bounds: QRectF | None = None) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(background or Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    view = fit_view(bounds if bounds is not None else world_bounds(evaluator), size, size, margin=0.06)
    draw_frame(painter, evaluator.draw_items(time), cache, view)
    painter.end()
    return image
