"""Viewport: draws the posed skeleton and handles the transform tools.

World space is y-up (Spine convention). Drag logic lives in begin_drag/drag_to/end_drag
so it can be driven without a mouse in tests.
"""

from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from ..preview.qt_renderer import ImageCache, draw_frame
from . import affine
from .document import EditorError
from .state import EditorState

BONE_COLOR = QColor("#9aa0aa")
GIZMO_SIZE = 70  # screen pixels from the origin to the end of an axis handle
AXIS_X, AXIS_Y, AXIS_FREE, RING = QColor("#ff5a5a"), QColor("#5ad15a"), QColor("#ffcf40"), QColor("#5aa8ff")
SHAPE_COLORS = {"boundingbox": "#60f000", "clipping": "#ce3a3a", "path": "#ff7f00", "point": "#f1f100"}
SELECTED = QColor("#ffcf40")
PICK_PX = 7


class Viewport(QWidget):
    status = Signal(str)

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.cache = ImageCache()
        self.zoom = 1.0
        self.pan = QPointF(0, 120)
        self.show_bones = True
        self.show_images = True
        self._drag: dict | None = None
        self._pan_from: QPointF | None = None
        self.read_only = False  # pan and zoom only (the mix preview)
        self._mouse: QPointF | None = None  # for the weight brush outline
        self._gizmo: list[tuple[str, str | None, str, tuple]] = []  # (op, axis, shape, geometry) for hit tests
        self._gizmo_hover: tuple[str, str | None] | None = None
        self.pose_chain = 2  # Pose tool: the dragged bone and its parent (Shift: the bone only, Ctrl: to the root)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(400, 300)
        self.checker = False  # checkerboard background (the Preview, like Spine's)
        self.reference = None  # ReferenceMedia: footage drawn behind (or over) the skeleton
        from .zoom_bar import ZoomBar
        self.zoom_bar = ZoomBar(self)
        for sig in (state.document_changed, state.time_changed, state.selection_changed, state.mode_changed,
                    state.vertex_selection_changed, state.view_changed):
            sig.connect(self.update)

    # --- coordinates -----------------------------------------------------------------------

    def view(self) -> QTransform:
        t = QTransform()
        t.translate(self.width() / 2 + self.pan.x(), self.height() / 2 + self.pan.y())
        t.scale(self.zoom, -self.zoom)
        return t

    def to_world(self, p: QPointF) -> tuple[float, float]:
        inv, _ = self.view().inverted()
        w = inv.map(p)
        return w.x(), w.y()

    def to_screen(self, x: float, y: float) -> QPointF:
        return self.view().map(QPointF(x, y))

    def frame_all(self) -> None:
        ev = self.state.evaluator()
        rect = QRectF()
        for name, m in ev.bone_world(self.state.time).items():
            pt = QPointF(m[4], m[5])
            rect = rect.united(QRectF(pt, pt).adjusted(-20, -20, 20, 20))
        for item in ev.draw_items(self.state.time):
            for x, y in item.points():
                # A zero-size QRectF is "null" and united() ignores it, so give each point some size.
                rect = rect.united(QRectF(x - 1, y - 1, 2, 2))
        if rect.isEmpty():
            rect = QRectF(-200, -200, 400, 400)
        self.zoom = max(0.05, min(8.0, 0.85 * min(self.width() / rect.width(), self.height() / rect.height())))
        c = rect.center()
        self.pan = QPointF(-c.x() * self.zoom, c.y() * self.zoom)
        self.zoom_bar.sync()
        self.update()

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """Zoom keeping the world point under `anchor` (default: the centre) in place."""
        from .zoom_bar import MAX_ZOOM, MIN_ZOOM
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        before = self.to_world(anchor)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        after = self.view().map(QPointF(*before))
        self.pan += anchor - after
        self.zoom_bar.sync()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.zoom_bar.place()

    # --- painting --------------------------------------------------------------------------

    def _sync_images(self) -> None:
        doc = self.state.doc
        images = {}
        for path in doc.used_images():
            file = doc.image_file(path)
            if file is not None:
                images[path] = file
        self.cache.set_images(images)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from ..preview.grid import background_for
        bg = self.state.background
        chosen = background_for(bg["name"], bg.get("custom"))
        if chosen == "checker" or (chosen is None and self.checker):
            p.fillRect(self.rect(), _checker_brush())
        elif chosen is not None:
            p.fillRect(self.rect(), QColor(chosen))
        else:
            p.fillRect(self.rect(), QColor("#2a2c33") if self.state.mode == "setup" else QColor("#262a33"))
        view = self.view()
        self._draw_grid(p, view)
        self._draw_reference(p, front=False)
        ev = self.state.evaluator()
        t = self.state.time
        opts = self.state.view_options
        if self.show_images and opts["images"]["visible"]:
            self._sync_images()
            front = self.state.onion.get("front", True)
            if not front:
                self._draw_onion(p, ev, t, view)
            items = [i for i in ev.draw_items(t) if not self.state.slot_hidden(i.slot)]
            draw_frame(p, items, self.cache, view)
            if front:
                self._draw_onion(p, ev, t, view)
        p.resetTransform()
        p.setOpacity(1)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._draw_reference(p, front=True)
        self._draw_selected_slot(p, ev, t)
        self._draw_weights(p, ev, t)
        if opts["others"]["visible"]:
            self._draw_shapes(p, ev, t)
        world = ev.bone_world(t)
        if self.show_bones and opts["bones"]["visible"]:
            self._draw_bones(p, world)
            if opts["others"]["visible"]:
                self._draw_iks(p, world)
                self._draw_constraints(p, world)
        self._draw_motion_path(p, t)
        self._draw_names(p, ev, t, world)
        self._draw_gizmo(p, ev, t)
        if self._drag and self._drag["op"] == "create":
            p.setPen(QPen(SELECTED, 2, Qt.PenStyle.DashLine))
            p.drawLine(self.to_screen(*self._drag["start"]), self.to_screen(*self._drag["now"]))
        if self.read_only:  # the Preview: no tools, no mode border
            p.setPen(QColor("#9aa0aa"))
            p.drawText(10, 20, f"PREVIEW  ·  zoom {self.zoom * 100:.0f}%")
            p.end()
            return
        mode = "ANIMATE" if self.state.animating else "SETUP"
        p.setPen(QColor("#ff6b6b") if self.state.animating else QColor("#9aa0aa"))
        p.drawText(10, 20, f"{mode}  ·  tool: {self.state.tool}  ·  zoom {self.zoom * 100:.0f}%")
        if self.state.animating:
            p.setBrush(Qt.BrushStyle.NoBrush)  # the bone joints leave a solid brush set
            p.setPen(QPen(QColor("#ff6b6b"), 3))
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()

    def _draw_grid(self, p: QPainter, view: QTransform) -> None:
        from ..preview.grid import draw_grid, style_for
        from ..preview.grid import background_for
        grid, bg = self.state.grid, self.state.background
        chosen = background_for(bg["name"], bg.get("custom"))
        light = chosen == "checker" or (chosen is not None and QColor(chosen).lightness() > 150)
        draw_grid(p, view, self.width(), self.height(), style_for(grid["style"], grid.get("custom")), light=light)

    def _draw_names(self, p: QPainter, ev, t: float, world) -> None:
        """Names of bones, images and other attachments when the Options ask for them."""
        opts = self.state.view_options
        p.setBrush(Qt.BrushStyle.NoBrush)
        if opts["bones"]["names"] and opts["bones"]["visible"] and self.show_bones:
            p.setPen(QColor("#d6d6d6"))
            for bone in self.state.doc.bones:
                if not self.state.bone_hidden(bone["name"]):
                    m = world[bone["name"]]
                    p.drawText(self.to_screen(m[4], m[5]) + QPointF(6, -6), bone["name"])
        if opts["images"]["names"] and opts["images"]["visible"]:
            p.setPen(QColor("#9fd3ff"))
            for item in ev.draw_items(t):
                if self.state.slot_hidden(item.slot):
                    continue
                pts = item.points()
                cx, cy = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
                p.drawText(self.to_screen(cx, cy), item.attachment)
        if opts["others"]["names"] and opts["others"]["visible"]:
            p.setPen(QColor("#ffb347"))
            for item in ev.shape_items(t):
                p.drawText(self.to_screen(*item.points[0]) + QPointF(6, 12), item.attachment)

    def _draw_bones(self, p: QPainter, world: dict[str, affine.Affine]) -> None:
        sel = self.state.selection
        for bone in self.state.doc.bones:
            if self.state.bone_hidden(bone["name"]):
                continue
            m = world[bone["name"]]
            origin = self.to_screen(m[4], m[5])
            selected = sel.kind == "bone" and sel.name == bone["name"]
            color = SELECTED if selected else QColor(f"#{bone['color'][:6]}") if bone.get("color") else BONE_COLOR
            length = bone.get("length", 0)
            if length > 0:
                tip = self.to_screen(*affine.apply(m, length, 0))
                dx, dy = tip.x() - origin.x(), tip.y() - origin.y()
                screen_len = math.hypot(dx, dy) or 1
                w = min(6.0, screen_len * 0.12)
                nx, ny = -dy / screen_len * w, dx / screen_len * w
                knee = QPointF(origin.x() + dx * 0.15, origin.y() + dy * 0.15)
                poly = QPolygonF([origin, QPointF(knee.x() + nx, knee.y() + ny), tip,
                                  QPointF(knee.x() - nx, knee.y() - ny)])
                fill = QColor(color)
                fill.setAlpha(150 if selected else 90)
                p.setBrush(fill)
                p.setPen(QPen(color, 1.5 if selected else 1))
                p.drawPolygon(poly)
            p.setBrush(QColor("#1e1f26"))
            p.setPen(QPen(color, 2 if selected else 1.5))
            p.drawEllipse(origin, 4, 4)
            if selected:
                p.setPen(SELECTED)
                p.drawText(origin + QPointF(8, -8), bone["name"])

    def _draw_iks(self, p: QPainter, world: dict[str, affine.Affine]) -> None:
        sel = self.state.selection
        for c in self.state.doc.iks:
            if c["target"] not in world or c["bones"][0] not in world:
                continue
            selected = (sel.kind == "ik" and sel.name == c["name"]) or \
                (sel.kind == "bone" and sel.name in (*c["bones"], c["target"]))
            color = QColor("#ff8af0") if selected else QColor(255, 138, 240, 150)
            start = world[c["bones"][0]]
            tgt = world[c["target"]]
            a, b = self.to_screen(start[4], start[5]), self.to_screen(tgt[4], tgt[5])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color, 1.5 if selected else 1, Qt.PenStyle.DashLine))
            p.drawLine(a, b)
            p.setPen(QPen(color, 2))
            p.drawEllipse(b, 9, 9)
            p.drawLine(b + QPointF(-13, 0), b + QPointF(13, 0))
            p.drawLine(b + QPointF(0, -13), b + QPointF(0, 13))
            if selected:
                p.drawText(b + QPointF(12, 14), c["name"])

    # --- gizmo -------------------------------------------------------------------------------

    def gizmo_frame(self, ev=None, t: float | None = None):
        """(origin, parent x axis, parent y axis, own x axis, own y axis, kind) in screen space for the
        selected bone or attachment, or None when no gizmo applies."""
        st = self.state
        if st.tool in ("pose", "weights", "create") or self.read_only:
            return None
        if st.tool != "shear" and (st.active_mesh() is not None or st.active_shape() is not None):
            return None  # vertices are edited instead (shear still shears the bone)
        target = self._target()
        if target is None:
            return None
        ev = ev or st.evaluator()
        t = st.time if t is None else t
        world = ev.bone_world(t)
        kind, name = target
        doc = st.doc
        if kind == "bone":
            parent = doc.bone(name).get("parent")
            parent_world = world[parent] if parent else affine.IDENTITY
            own = world[name]
        else:
            slot, att_name = name.split("\n")
            att = doc.attachments(slot).get(att_name)
            if att is None or att.get("type", "region") != "region":
                return None
            parent_world = world[doc.slot(slot)["bone"]]
            own = affine.mul(parent_world, affine.local(att.get("x", 0), att.get("y", 0), att.get("rotation", 0),
                                                        att.get("scaleX", 1), att.get("scaleY", 1)))
        origin = self.to_screen(own[4], own[5])

        def axis(m, vx, vy) -> QPointF:
            wx, wy = affine.apply_vector(m, vx, vy)
            tip = self.to_screen(own[4] + wx, own[5] + wy)
            d = QPointF(tip.x() - origin.x(), tip.y() - origin.y())
            length = math.hypot(d.x(), d.y())
            return QPointF(d.x() / length, d.y() / length) if length > 1e-9 else QPointF(1, 0)
        frame = {"world": affine.IDENTITY, "parent": parent_world, "local": own}[st.axes]
        return origin, axis(frame, 1, 0), axis(frame, 0, 1), axis(own, 1, 0), axis(own, 0, 1), kind

    def _gizmo_ops(self) -> tuple[str, ...]:
        tool = self.state.tool
        return ("translate", "rotate", "scale") if tool == "select" else (tool,)

    def _draw_gizmo(self, p: QPainter, ev, t: float) -> None:
        self._gizmo = []
        frame = self.gizmo_frame(ev, t)
        if frame is None:
            return
        origin, px, py, ox, oy, kind = frame
        ops = self._gizmo_ops()
        size = GIZMO_SIZE
        hover = self._gizmo_hover

        def pen(color: QColor, op: str, axis: str | None, width: float = 2.0) -> QPen:
            lit = hover == (op, axis)
            return QPen(QColor("#ffffff") if lit else color, width + (1 if lit else 0))

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if "rotate" in ops:
            radius = size * (1.25 if len(ops) > 1 else 0.85)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(pen(RING, "rotate", None, 2))
            p.drawEllipse(origin, radius, radius)
            self._gizmo.append(("rotate", None, "ring", (origin, radius)))
        if "translate" in ops:
            for axis, direction, color in (("x", px, AXIS_X), ("y", py, AXIS_Y)):
                tip = origin + direction * size
                p.setPen(pen(color, "translate", axis))
                p.drawLine(origin, tip)
                normal = QPointF(-direction.y(), direction.x())
                head = QPolygonF([tip + direction * 12, tip + normal * 6, tip - normal * 6])
                p.setBrush(QColor("#ffffff") if hover == ("translate", axis) else color)
                p.drawPolygon(head)
                self._gizmo.append(("translate", axis, "segment", (origin + direction * 14, tip + direction * 12)))
            p.setBrush(QColor(255, 207, 64, 90))
            p.setPen(pen(AXIS_FREE, "translate", "xy", 1.5))
            p.drawRect(QRectF(origin.x() - 8, origin.y() - 8, 16, 16))
            self._gizmo.insert(0, ("translate", "xy", "box", (origin, 9)))
        if "scale" in ops:
            reach = size * (0.72 if len(ops) > 1 else 1.0)
            for axis, direction, color in (("x", ox, AXIS_X), ("y", oy, AXIS_Y)):
                end = origin + direction * reach
                p.setPen(pen(color, "scale", axis, 1.5))
                if len(ops) == 1:
                    p.drawLine(origin, end)
                p.setBrush(QColor("#ffffff") if hover == ("scale", axis) else color)
                p.drawRect(QRectF(end.x() - 5, end.y() - 5, 10, 10))
                self._gizmo.insert(0, ("scale", axis, "box", (end, 7)))
            if len(ops) == 1:  # uniform scale from the centre
                p.setBrush(QColor("#ffffff") if hover == ("scale", "xy") else AXIS_FREE)
                p.setPen(pen(AXIS_FREE, "scale", "xy", 1.5))
                p.drawRect(QRectF(origin.x() - 6, origin.y() - 6, 12, 12))
                self._gizmo.insert(0, ("scale", "xy", "box", (origin, 8)))
        if "shear" in ops and kind == "bone":
            for axis, direction, color in (("x", ox, AXIS_X), ("y", oy, AXIS_Y)):
                end = origin + direction * size * 0.9
                p.setPen(pen(color, "shear", axis, 1.5))
                p.drawLine(origin, end)
                diamond = QPolygonF([end + QPointF(0, -7), end + QPointF(7, 0), end + QPointF(0, 7), end + QPointF(-7, 0)])
                p.setBrush(QColor("#ffffff") if hover == ("shear", axis) else color)
                p.drawPolygon(diamond)
                self._gizmo.insert(0, ("shear", axis, "box", (end, 8)))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def gizmo_hit(self, screen: QPointF) -> tuple[str, str | None] | None:
        """(op, axis) of the gizmo handle under the cursor; axis "x", "y", "xy" (free) or None."""
        for op, axis, kind, geo in self._gizmo:
            if kind == "box":
                centre, half = geo
                if abs(screen.x() - centre.x()) <= half and abs(screen.y() - centre.y()) <= half:
                    return op, axis
            elif kind == "segment":
                if _dist_to_segment(screen, *geo) <= 6:
                    return op, axis
            elif kind == "ring":
                centre, radius = geo
                if abs(math.hypot(screen.x() - centre.x(), screen.y() - centre.y()) - radius) <= 6:
                    return op, axis
        return None

    def _path_curve(self, pts: list[QPointF], closed: bool) -> QPainterPath:
        """Path vertices are (in handle, point, out handle) per point; curves join the points."""
        path = QPainterPath()
        count = len(pts) // 3
        if count < 1:
            return path
        path.moveTo(pts[1])
        for i in range(count - 1 + (1 if closed else 0)):
            a, b = i, (i + 1) % count
            path.cubicTo(pts[a * 3 + 2], pts[b * 3], pts[b * 3 + 1])
        return path

    def _draw_shapes(self, p: QPainter, ev, t: float) -> None:
        """Bounding boxes, clipping polygons, paths and points (the game does not draw them)."""
        active = self.state.active_shape()
        chosen_vertices = self.state.vertex_selection
        for item in ev.shape_items(t):
            chosen = active is not None and (item.slot, item.attachment) == active[:2]
            color = QColor(SHAPE_COLORS[item.kind])
            if not chosen:
                color.setAlpha(140)
            pts = [self.to_screen(x, y) for x, y in item.points]
            p.setBrush(Qt.BrushStyle.NoBrush)
            style = Qt.PenStyle.DashLine if item.kind == "clipping" else Qt.PenStyle.SolidLine
            p.setPen(QPen(color, 2 if chosen else 1, style))
            if item.kind == "point":
                p.drawEllipse(pts[0], 5, 5)
                p.drawLine(pts[0], pts[1])
            elif item.kind == "path":
                p.drawPath(self._path_curve(pts, item.closed))
            elif len(pts) >= 2:
                p.drawPolygon(QPolygonF(pts))
            if not chosen or self.state.animating:
                continue
            if item.kind == "path":  # handle lines from each point to its handles
                p.setPen(QPen(QColor(255, 255, 255, 110), 1))
                for i in range(len(pts) // 3):
                    p.drawLine(pts[i * 3 + 1], pts[i * 3])
                    p.drawLine(pts[i * 3 + 1], pts[i * 3 + 2])
            for i, pt in enumerate(pts[:1] if item.kind == "point" else pts):
                handle = item.kind == "path" and i % 3 != 1
                sel = i in chosen_vertices
                p.setBrush(SELECTED if sel else QColor("#1e1f26"))
                p.setPen(QPen(SELECTED if sel else color, 1.5))
                if handle:
                    p.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
                else:
                    p.drawEllipse(pt, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_constraints(self, p: QPainter, world: dict[str, affine.Affine]) -> None:
        """Transform constraints: a dashed line from each constrained bone to the target."""
        sel = self.state.selection
        for c in self.state.doc.transforms:
            if c["target"] not in world:
                continue
            selected = (sel.kind == "transform" and sel.name == c["name"]) or \
                (sel.kind == "bone" and sel.name in (*c["bones"], c["target"]))
            color = QColor("#ffb347") if selected else QColor(255, 179, 71, 130)
            tgt = world[c["target"]]
            b = self.to_screen(tgt[4], tgt[5])
            p.setPen(QPen(color, 1.5 if selected else 1, Qt.PenStyle.DotLine))
            for name in c["bones"]:
                if name in world:
                    m = world[name]
                    p.drawLine(self.to_screen(m[4], m[5]), b)
            p.drawRect(QRectF(b.x() - 6, b.y() - 6, 12, 12))
            if selected:
                p.drawText(b + QPointF(10, 16), c["name"])
        for c in self.state.doc.paths:
            selected = sel.kind == "path" and sel.name == c["name"]
            if not selected:
                continue
            p.setPen(QPen(QColor("#ff7f00"), 2))
            for name in c["bones"]:
                if name in world:
                    m = world[name]
                    p.drawEllipse(self.to_screen(m[4], m[5]), 7, 7)

    # --- shape editing (Setup mode) ---------------------------------------------------------

    def _shape_item(self):
        shape = self.state.active_shape()
        if shape is None:
            return None, None
        for item in self.state.evaluator().shape_items(self.state.time):
            if (item.slot, item.attachment) == shape[:2]:
                return shape, item
        return shape, None

    def pick_shape_vertex(self, screen: QPointF) -> int | None:
        shape, item = self._shape_item()
        if item is None:
            return None
        points = item.points[:1] if item.kind == "point" else item.points
        best, best_d = None, PICK_PX
        for i, (x, y) in enumerate(points):
            s = self.to_screen(x, y)
            d = math.hypot(s.x() - screen.x(), s.y() - screen.y())
            if d <= best_d:
                best, best_d = i, d
        return best

    def _slot_bone_world(self, slot: str) -> affine.Affine:
        return self.state.evaluator().bone_world(self.state.time)[self.state.doc.slot(slot)["bone"]]

    def add_shape_vertex_at(self, screen: QPointF) -> int | None:
        """Alt+click: add a vertex to the polygon's nearest edge, or a point to the path."""
        shape, item = self._shape_item()
        if item is None or item.kind == "point":
            return None
        slot, name, kind = shape
        wx, wy = self.to_world(screen)
        lx, ly = affine.apply(affine.invert(self._slot_bone_world(slot)), wx, wy)
        doc = self.state.doc
        try:
            if kind == "path":
                points = item.points[1::3]
                count = len(points)
                segments = count if item.closed else count - 1
                click = QPointF(wx, wy)
                after = min(range(max(1, segments)), key=lambda i: _dist_to_segment(
                    click, QPointF(*points[i]), QPointF(*points[(i + 1) % count])))
                if not item.closed and after == count - 2:
                    # Beyond the last point (its nearest spot on the last segment is the end): extend the path.
                    to_end = math.hypot(wx - points[-1][0], wy - points[-1][1])
                    if _dist_to_segment(click, QPointF(*points[-2]), QPointF(*points[-1])) >= to_end - 1e-6:
                        after = count - 1
                index = doc.insert_path_point(slot, name, after, (lx, ly)) * 3 + 1
            else:
                pts = item.points
                after = min(range(len(pts)), key=lambda i: _dist_to_segment(
                    QPointF(wx, wy), QPointF(*pts[i]), QPointF(*pts[(i + 1) % len(pts)])))
                index = doc.insert_polygon_vertex(slot, name, after, (lx, ly))
        except EditorError as exc:
            self.status.emit(str(exc))
            return None
        self.state.set_vertex_selection({index})
        return index

    def begin_shape_drag(self, wx: float, wy: float, op: str) -> bool:
        shape, item = self._shape_item()
        if item is None:
            return False
        slot, name, kind = shape
        att = self.state.doc.attachments(slot)[name]
        self._drag = {"op": op, "kind": "shape", "shape": shape, "start_mouse": (wx, wy), "recorded": False,
                      "name": name, "label": f"Edit {name}", "bone": self._slot_bone_world(slot),
                      "att": {k: (list(v) if isinstance(v, list) else v) for k, v in att.items()},
                      "chosen": set(self.state.vertex_selection) or ({0} if kind == "point" else
                                                                     set(range(att.get("vertexCount", 0))))}
        return True

    def _drag_shape(self, d: dict, wx: float, wy: float) -> None:
        slot, name, kind = d["shape"]
        inv = affine.invert(d["bone"])
        sx, sy = d["start_mouse"]
        doc = self.state.doc
        start = d["att"]
        if kind == "point":
            if d["op"] == "rotate":
                ox, oy = affine.apply(d["bone"], start.get("x", 0), start.get("y", 0))
                delta = math.degrees(math.atan2(wy - oy, wx - ox) - math.atan2(sy - oy, sx - ox))
                doc.set_shape(slot, name, record=False, rotation=start.get("rotation", 0) + delta)
            else:
                dx, dy = affine.apply_vector(inv, wx - sx, wy - sy)
                doc.set_shape(slot, name, record=False, x=start.get("x", 0) + dx, y=start.get("y", 0) + dy)
            return
        dx, dy = affine.apply_vector(inv, wx - sx, wy - sy)
        verts = list(start["vertices"])
        moving = set(d["chosen"])
        if kind == "path":  # a point carries its handles
            for i in list(moving):
                if i % 3 == 1:
                    moving |= {i - 1, i + 1}
        for i in moving:
            if 0 <= i < len(verts) // 2:
                verts[i * 2] += dx
                verts[i * 2 + 1] += dy
        if kind == "path":
            doc.set_path_vertices(slot, name, verts, record=False)
        else:
            doc.set_polygon_vertices(slot, name, verts, record=False)

    def onion_times(self, t: float, duration: float) -> list[tuple[float, int]]:
        """(time, signed distance) of the ghost frames around t: negative before, positive after."""
        onion = self.state.onion
        if not (onion["on"] and self.state.animating and not self.read_only):
            return []
        step = onion["step"] / max(1.0, self.state.doc.fps)
        out = []
        for k in range(1, onion["before"] + 1):
            if t - k * step >= -1e-9:
                out.append((t - k * step, -k))
        for k in range(1, onion["after"] + 1):
            if t + k * step <= duration + 1e-9:
                out.append((t + k * step, k))
        return out

    def _draw_onion(self, p: QPainter, ev, t: float, view) -> None:
        """Faint copies of nearby frames: earlier ones red, later ones green, fading with distance."""
        ghosts = self.onion_times(t, ev.duration)
        if not ghosts:
            return
        onion = self.state.onion
        only = self.onion_slots() if onion.get("selected", True) else None
        for time, k in sorted(ghosts, key=lambda g: -abs(g[1])):  # far ghosts first
            tint = (1.0, 0.25, 0.25) if k < 0 else (0.25, 1.0, 0.35)
            fade = onion["opacity"] / abs(k)
            items = [replace(i, color=(i.color[0] * tint[0], i.color[1] * tint[1], i.color[2] * tint[2],
                                       i.color[3] * fade))
                     for i in ev.draw_items(time)
                     if not self.state.slot_hidden(i.slot) and (only is None or i.slot in only)]
            draw_frame(p, items, self.cache, view)

    def onion_slots(self) -> set[str] | None:
        """The slots the ghosts show: a selected slot (or attachment's slot), or the slots of a
        selected bone and the bones under it. None (all slots) when nothing like that is selected."""
        sel, doc = self.state.selection, self.state.doc
        if sel.kind == "slot":
            return {sel.name}
        if sel.kind == "attachment":
            return {sel.owner}
        if sel.kind == "bone" and doc.has_bone(sel.name):
            bones = {sel.name, *doc.descendants(sel.name)}
            return {s["name"] for s in doc.slots if s["bone"] in bones}
        return None

    def _draw_selected_slot(self, p: QPainter, ev, t: float) -> None:
        sel = self.state.selection
        slot = sel.name if sel.kind == "slot" else sel.owner if sel.kind == "attachment" else None
        if not slot:
            return
        for item in ev.draw_items(t):
            if item.slot != slot:
                continue
            p.setBrush(Qt.BrushStyle.NoBrush)
            if item.mesh is None:
                corners = [self.to_screen(x, y) for x, y in item.points()]
                path = QPainterPath()
                path.addPolygon(QPolygonF(corners + corners[:1]))
                p.setPen(QPen(SELECTED, 1.5, Qt.PenStyle.DashLine))
                p.drawPath(path)
                continue
            screen = [self.to_screen(x, y) for x, y in item.mesh.vertices]
            p.setPen(QPen(QColor(127, 224, 208, 110), 1))
            for a, b, c in item.mesh.triangles:
                p.drawPolygon(QPolygonF([screen[a], screen[b], screen[c]]))
            hull = item.mesh.hull or len(screen)
            p.setPen(QPen(QColor("#7fe0d0"), 1.5))
            p.drawPolygon(QPolygonF(screen[:hull]))
            selected = self.state.vertex_selection
            if self.state.tool == "weights" and not self.state.animating:
                continue  # the Weights view draws it (_draw_weights)
            for i, pt in enumerate(screen):
                chosen = i in selected
                p.setBrush(SELECTED if chosen else QColor("#1e1f26"))
                p.setPen(QPen(SELECTED if chosen else QColor("#7fe0d0"), 1.5))
                p.drawEllipse(pt, 4 if chosen else 3, 4 if chosen else 3)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_weights(self, p: QPainter, ev, t: float) -> None:
        """Weights tool: the mesh being weighted with an overlay of the painted bone's weights (red = 1,
        blue = 0), a pie per vertex of all its bones' weights, and the brush with its feather."""
        from . import mesh_ops
        from .weights_panel import bone_color
        st = self.state
        mesh = st.weights_mesh()
        bone = st.paint_bone()
        if st.tool != "weights" or st.animating or mesh is None or bone is None:
            return
        item = next((i for i in ev.draw_items(t) if i.slot == mesh[0] and i.attachment == mesh[1]), None)
        if item is None or item.mesh is None:
            return
        screen = [self.to_screen(x, y) for x, y in item.mesh.vertices]
        weights = mesh_ops.bone_weights(st.doc, mesh[0], mesh[1], bone)

        def heat(w: float, alpha: int) -> QColor:
            return QColor(int(255 * w), int(60 + 80 * (1 - abs(w - 0.5) * 2)), int(255 * (1 - w)), alpha)
        p.setPen(QPen(QColor(127, 224, 208, 90), 1))
        for a, b, c in item.mesh.triangles:
            if st.weight_overlay:
                p.setBrush(heat((weights[a] + weights[b] + weights[c]) / 3, 120))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(QPolygonF([screen[a], screen[b], screen[c]]))
        pies = mesh_ops.vertex_bone_weights(st.doc, mesh[0], mesh[1]) if st.weight_pies else None
        selected = st.vertex_selection
        for i, pt in enumerate(screen):
            ring = QPen(SELECTED if i in selected else QColor("#111"), 2 if i in selected else 1)
            if pies:  # a pie of the vertex's bone weights, in the bones' colours
                radius, start = 7.0, 90 * 16
                rect = QRectF(pt.x() - radius, pt.y() - radius, radius * 2, radius * 2)
                p.setPen(Qt.PenStyle.NoPen)
                for name, w in pies[i]:
                    span = -round(w * 360 * 16)
                    p.setBrush(bone_color(st.doc, name))
                    p.drawPie(rect, start, span)
                    start += span
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(ring)
                p.drawEllipse(pt, radius, radius)
            else:
                p.setBrush(heat(weights[i], 255))
                p.setPen(ring)
                p.drawEllipse(pt, 4, 4)
        p.setPen(QColor("#ffffff"))
        p.drawText(10, 40, f"Weights: painting '{bone}' · {st.paint_mode} · strength {st.brush_strength:.2f}")
        if self._mouse is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 190), 1))
            p.drawEllipse(self._mouse, st.brush_size, st.brush_size)
            core = st.brush_size * (1 - st.brush_feather)
            if core > 1:  # full strength inside, fading to the outer ring
                p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.PenStyle.DashLine))
                p.drawEllipse(self._mouse, core, core)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def paint_at(self, screen: QPointF, record: bool) -> int:
        """Weights tool: brush the active mesh at a screen point (Setup mode)."""
        from . import mesh_ops
        st = self.state
        mesh = st.weights_mesh()
        bone = st.paint_bone()
        if mesh is None or bone is None or st.animating:
            return 0
        only = set(st.vertex_selection) if st.weights_selected_only else None
        try:
            return mesh_ops.paint_weights(st.doc, *mesh, bone, self.to_world(screen), st.brush_size / self.zoom,
                                          st.brush_strength, st.paint_mode, record=record, feather=st.brush_feather,
                                          only=only, locked=st.locked_bones)
        except EditorError as exc:
            self.status.emit(str(exc))
            return 0

    def pick_vertex(self, screen: QPointF) -> int | None:
        """Index of the active mesh's vertex under the cursor."""
        mesh = self.state.active_mesh()
        if mesh is None:
            return None
        ev = self.state.evaluator()
        best, best_d = None, PICK_PX
        for i, (x, y) in enumerate(ev.mesh_world_vertices(*mesh, self.state.time)):
            s = self.to_screen(x, y)
            d = math.hypot(s.x() - screen.x(), s.y() - screen.y())
            if d <= best_d:
                best, best_d = i, d
        return best

    # --- picking ---------------------------------------------------------------------------

    def pick(self, screen: QPointF) -> tuple[str, str] | None:
        """('bone', name) or ('slot', name) under the cursor; bones win."""
        ev = self.state.evaluator()
        t = self.state.time
        opts = self.state.view_options
        if self.show_bones and opts["bones"]["visible"] and opts["bones"]["select"]:
            world = ev.bone_world(t)
            for bone in reversed(self.state.doc.bones):
                if self.state.bone_hidden(bone["name"]) or bone["name"] in self.state.unselectable:
                    continue
                m = world[bone["name"]]
                origin = self.to_screen(m[4], m[5])
                length = bone.get("length", 0)
                tip = self.to_screen(*affine.apply(m, length, 0)) if length > 0 else origin
                if _dist_to_segment(screen, origin, tip) <= PICK_PX:
                    return "bone", bone["name"]
        if self.show_images and opts["images"]["visible"] and opts["images"]["select"]:
            wx, wy = self.to_world(screen)
            for item in reversed(ev.draw_items(t)):
                if self.state.slot_hidden(item.slot):
                    continue
                if item.mesh is not None:
                    if item.color[3] > 0.02 and any(_in_triangle(wx, wy, *(item.mesh.vertices[i] for i in tri))
                                                    for tri in item.mesh.triangles):
                        return "slot", item.slot
                    continue
                px, py = affine.apply(affine.invert(item.transform), wx, wy)
                if 0 <= px <= item.width and 0 <= py <= item.height and item.color[3] > 0.02:
                    image = self.cache.tinted(item.image_path, 1, 1, 1)
                    if image is not None and not image.isNull():
                        ix = int(px / item.width * (image.width() - 1))
                        iy = int(py / item.height * (image.height() - 1))
                        if image.pixelColor(ix, iy).alpha() < 16:
                            continue
                    return "slot", item.slot
        return None

    # --- drag operations (also used by tests) ------------------------------------------------

    def _target(self) -> tuple[str, str] | None:
        sel = self.state.selection
        if sel.kind == "bone":
            return "bone", sel.name
        if sel.kind in ("slot", "attachment"):
            slot = sel.name if sel.kind == "slot" else sel.owner
            att = sel.name if sel.kind == "attachment" else self.state.doc.slot(slot).get("attachment")
            if not self.state.animating and att and self.state.tool != "shear":  # shear acts on the bone
                return "attachment", f"{slot}\n{att}"
            return "bone", self.state.doc.slot(slot)["bone"]
        return None

    def begin_drag(self, wx: float, wy: float, op: str | None = None, axis: str | None = None) -> bool:
        """axis (from a gizmo handle): "x" or "y" constrains the change to that axis, "xy" is free."""
        op = op or self.state.tool
        doc = self.state.doc
        if op == "create":
            parent = self.state.selection.name if self.state.selection.kind == "bone" else doc.root_name
            self._drag = {"op": "create", "start": (wx, wy), "now": (wx, wy), "parent": parent}
            return True
        if op == "pose":
            target = self._target()
            if target is None:
                return False
            bone = target[1] if target[0] == "bone" else doc.slot(target[1].split("\n")[0])["bone"]
            if bone == doc.root_name:
                return False
            self._drag = {"op": "pose", "kind": "pose", "name": bone, "start_mouse": (wx, wy), "recorded": False,
                          "chain": self.pose_chain, "label": f"Pose {bone}"}
            return True
        if op not in ("translate", "rotate", "scale", "shear"):
            return False
        mesh = self.state.active_mesh()
        if mesh is not None and op != "shear":
            return self._begin_vertex_drag(mesh, op, wx, wy)
        target = self._target()
        if target is None:
            return False
        ev = self.state.evaluator()
        world = ev.bone_world(self.state.time)
        kind, name = target
        if kind == "bone":
            bone = doc.bone(name)
            parent_world = world[bone["parent"]] if bone.get("parent") else affine.IDENTITY
            own = world[name]
            start = ev.bone_locals(self.state.time)[name][:7]  # x, y, rotation, scaleX, scaleY, shearX, shearY
        else:
            slot, att_name = name.split("\n")
            att = doc.attachments(slot)[att_name]
            parent_world = world[doc.slot(slot)["bone"]]
            own = affine.mul(parent_world, affine.local(att.get("x", 0), att.get("y", 0), att.get("rotation", 0),
                                                        att.get("scaleX", 1), att.get("scaleY", 1)))
            start = (att.get("x", 0), att.get("y", 0), att.get("rotation", 0), att.get("scaleX", 1), att.get("scaleY", 1),
                     0.0, 0.0)
            if op == "shear":
                return False  # region attachments have no shear
        frame = {"world": affine.IDENTITY, "parent": parent_world, "local": own}[self.state.axes]
        axes = {}
        for key, (vx, vy) in (("x", (1, 0)), ("y", (0, 1))):  # world directions of the translate axes
            ax, ay = affine.apply_vector(frame, vx, vy)
            length = math.hypot(ax, ay) or 1e-9
            axes[key] = (ax / length, ay / length)
        self._drag = {"op": op, "kind": kind, "name": name, "parent": parent_world, "own": own, "axis": axis,
                      "axes": axes,
                      "start_mouse": (wx, wy), "start": start, "recorded": False,
                      "label": f"{op.capitalize()} {name.split(chr(10))[-1]}"}
        return True

    def _begin_vertex_drag(self, mesh: tuple[str, str], op: str, wx: float, wy: float) -> bool:
        """Selected vertices (or all) move/rotate/scale around their centre: the setup shape in Setup
        mode, a deform key in Animate mode."""
        positions = self.state.evaluator().mesh_world_vertices(*mesh, self.state.time)
        chosen = sorted(self.state.vertex_selection) or list(range(len(positions)))
        start = {i: positions[i] for i in chosen}
        cx = sum(p[0] for p in start.values()) / len(start)
        cy = sum(p[1] for p in start.values()) / len(start)
        mode = "deform" if self.state.animating else self.state.mesh_drag_mode
        what = {"deform": "deform", "reshape": "vertices", "mesh": "mesh", "uv": "UVs"}[mode]
        mapper = None
        if mode in ("mesh", "uv"):
            from . import mesh_ops
            mapper = mesh_ops.uv_mapper(self.state.doc, *mesh)  # the mapping before the drag
        self._drag = {"op": op, "kind": "vertices", "mesh": mesh, "start_mouse": (wx, wy), "points": start,
                      "pivot": (cx, cy), "recorded": False, "name": mesh[1], "mode": mode, "mapper": mapper,
                      "label": f"{op.capitalize()} {what} {mesh[1]}"}
        return True

    def add_vertex_at(self, screen: QPointF) -> int | None:
        """Alt+click in Setup mode: add a vertex to the active mesh (on the outline or inside)."""
        mesh = self.state.active_mesh()
        if mesh is None or self.state.animating:
            return None
        from . import mesh_ops
        wx, wy = self.to_world(screen)
        try:
            index = mesh_ops.add_vertex(self.state.doc, *mesh, (wx, wy), edge_tolerance=PICK_PX / self.zoom)
        except EditorError as exc:
            self.status.emit(str(exc))
            return None
        self.state.set_vertex_selection({index})
        return index

    def _drag_vertices(self, d: dict, wx: float, wy: float, axis_only: bool) -> None:
        sx, sy = d["start_mouse"]
        cx, cy = d["pivot"]
        targets = {}
        if d["op"] == "translate":
            targets = {i: (x + wx - sx, y + wy - sy) for i, (x, y) in d["points"].items()}
        elif d["op"] == "rotate":
            angle = math.atan2(wy - cy, wx - cx) - math.atan2(sy - cy, sx - cx)
            cos, sin = math.cos(angle), math.sin(angle)
            targets = {i: (cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos)
                       for i, (x, y) in d["points"].items()}
        elif d["op"] == "scale":
            factor = math.hypot(wx - cx, wy - cy) / (math.hypot(sx - cx, sy - cy) or 1e-6)
            targets = {i: (cx + (x - cx) * factor, y if axis_only else cy + (y - cy) * factor)
                       for i, (x, y) in d["points"].items()}
        from . import mesh_ops
        slot, name = d["mesh"]
        if self.state.animating:
            mesh_ops.set_deform_key(self.state.doc, self.state.animation, slot, name, self.state.time,
                                    targets, record=False)
        else:
            mesh_ops.drag_vertices(self.state.doc, slot, name, targets, d["mode"], d["mapper"], record=False)

    def drag_to(self, wx: float, wy: float, axis_only: bool = False) -> None:
        d = self._drag
        if not d:
            return
        if d["op"] == "create":
            d["now"] = (wx, wy)
            self.update()
            return
        sx, sy = d["start_mouse"]
        if not d["recorded"]:  # one undo step per drag, recorded only once something moves
            self.state.doc.checkpoint(d["label"])
            d["recorded"] = True
        if d["kind"] == "vertices":
            self._drag_vertices(d, wx, wy, axis_only)
            return
        if d["kind"] == "shape":
            self._drag_shape(d, wx, wy)
            return
        if d["kind"] == "pose":
            from .pose import pose_chain
            st = self.state
            chain = d["chain"] if d["chain"] > 0 else len(st.doc.bones)
            rotations = pose_chain(st.doc, st.evaluator(), st.time, d["name"], (wx, wy), chain)
            for bone, rotation in rotations.items():
                st.apply_bone_pose(bone, record=False, rotation=rotation)
            return
        x0, y0, r0, scx0, scy0, shx0, shy0 = d["start"]
        ox, oy = d["own"][4], d["own"][5]
        axis = d.get("axis")
        values: dict[str, float] = {}
        if d["op"] == "translate":
            mx, my = wx - sx, wy - sy
            if axis in ("x", "y"):  # along one gizmo axis (Local, Parent or World axes)
                ax, ay = d["axes"][axis]
                along = mx * ax + my * ay
                mx, my = ax * along, ay * along
            dx, dy = affine.apply_vector(affine.invert(d["parent"]), mx, my)
            values = {"x": x0 + dx, "y": y0 + dy}
        elif d["op"] == "rotate":
            a0 = math.degrees(math.atan2(sy - oy, sx - ox))
            a1 = math.degrees(math.atan2(wy - oy, wx - ox))
            delta = (a1 - a0 + 180) % 360 - 180
            if affine.is_flipped(d["parent"]):
                delta = -delta
            values = {"rotation": r0 + delta}
        elif d["op"] == "scale":
            if axis in ("x", "y"):  # along the bone's own axis: the projected distance changes
                vx, vy = affine.apply_vector(d["own"], *((1, 0) if axis == "x" else (0, 1)))
                length = math.hypot(vx, vy) or 1e-9
                vx, vy = vx / length, vy / length
                dist0 = (sx - ox) * vx + (sy - oy) * vy
                dist1 = (wx - ox) * vx + (wy - oy) * vy
                factor = dist1 / dist0 if abs(dist0) > 1e-6 else 1.0
                values = {"scaleX": scx0 * factor} if axis == "x" else {"scaleY": scy0 * factor}
            else:
                dist0 = math.hypot(sx - ox, sy - oy) or 1e-6
                factor = math.hypot(wx - ox, wy - oy) / dist0
                values = {"scaleX": scx0 * factor} if axis_only else {"scaleX": scx0 * factor, "scaleY": scy0 * factor}
        elif d["op"] == "shear":
            a0 = math.degrees(math.atan2(sy - oy, sx - ox))
            a1 = math.degrees(math.atan2(wy - oy, wx - ox))
            delta = (a1 - a0 + 180) % 360 - 180
            if affine.is_flipped(d["parent"]):
                delta = -delta
            values = {"shearY": shy0 + delta} if axis == "y" else {"shearX": shx0 + delta}
        if d["kind"] == "bone":
            self.state.apply_bone_pose(d["name"], record=False, **values)
        else:
            slot, att = d["name"].split("\n")
            self.state.doc.set_attachment(slot, att, record=False, **values)

    def end_drag(self) -> str | None:
        d, self._drag = self._drag, None
        if not d:
            return None
        if d["op"] != "create":
            self.update()
            return d["name"]
        (x0, y0), (x1, y1) = d["start"], d["now"]
        doc = self.state.doc
        # The displayed (posed, IK-solved) parent is what the new bone is drawn relative to.
        parent_world = self.state.evaluator().bone_world(self.state.time)[d["parent"]]
        lx, ly = affine.apply(affine.invert(parent_world), x0, y0)
        vx, vy = affine.apply_vector(affine.invert(parent_world), x1 - x0, y1 - y0)
        length = math.hypot(vx, vy)
        rotation = math.degrees(math.atan2(vy, vx)) if length > 1e-6 else 0.0
        try:
            name = doc.add_bone(d["parent"], x=lx, y=ly, rotation=rotation,
                                length=length if math.hypot(x1 - x0, y1 - y0) * self.zoom > 4 else 0)
        except EditorError as exc:
            self.status.emit(str(exc))
            return None
        self.state.select("bone", name)
        return name

    # --- mouse ---------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_from = pos
            return
        if event.button() != Qt.MouseButton.LeftButton or self.read_only:
            return
        if self._press_motion_path(pos):
            return
        tool = self.state.tool
        if tool == "weights" and self.state.weights_mesh() is not None and not self.state.animating:
            hit = self.pick(pos) if event.modifiers() & Qt.KeyboardModifier.ControlModifier else None
            if hit and hit[0] == "bone":  # Ctrl+click a bone: paint that bone; the mesh stays
                self.state.weight_bone = hit[1]
                self.state.select("bone", hit[1])
                return
            # One undo step per stroke, recorded when the stroke first changes a weight.
            self._drag = {"op": "paint", "kind": "paint", "name": "", "recorded": self.paint_at(pos, record=True) > 0}
            return
        handle = self.gizmo_hit(pos)
        if handle is not None:  # a gizmo handle: change only what it controls
            wx, wy = self.to_world(pos)
            self.begin_drag(wx, wy, handle[0], handle[1])
            return
        mods = event.modifiers()
        self.pose_chain = 1 if mods & Qt.KeyboardModifier.ShiftModifier else \
            0 if mods & Qt.KeyboardModifier.ControlModifier else 2
        shape = self.state.active_shape() if not self.state.animating and tool != "create" else None
        if shape is not None:
            if event.modifiers() & Qt.KeyboardModifier.AltModifier and shape[2] != "point":
                self.add_shape_vertex_at(pos)
                self.update()
                return
            vertex = self.pick_shape_vertex(pos)
            if vertex is not None:
                chosen = set(self.state.vertex_selection)
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    chosen ^= {vertex}
                elif vertex not in chosen:
                    chosen = {vertex}
                self.state.set_vertex_selection(chosen)
                wx, wy = self.to_world(pos)
                self.begin_shape_drag(wx, wy, "rotate" if tool == "rotate" else "translate")
                self.update()
                return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier and self.state.active_mesh() is not None:
            self.add_vertex_at(pos)
            self.update()
            return
        vertex = self.pick_vertex(pos) if tool != "create" else None
        if vertex is not None:
            chosen = set(self.state.vertex_selection)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                chosen ^= {vertex}
            elif vertex not in chosen:
                chosen = {vertex}
            self.state.set_vertex_selection(chosen)
            if chosen:
                wx, wy = self.to_world(pos)
                self.begin_drag(wx, wy, tool if tool in ("translate", "rotate", "scale") else "translate")
            self.update()
            return
        if tool == "select" and self.state.vertex_selection:
            self.state.set_vertex_selection(set())
        hit = self.pick(pos)
        if tool != "create" and hit:
            self.state.select(*hit)
        elif tool == "select" and not hit:
            self.state.select(None)
        wx, wy = self.to_world(pos)
        self.begin_drag(wx, wy)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if not self._drag and self._gizmo:
            hover = self.gizmo_hit(pos)
            if hover != self._gizmo_hover:
                self._gizmo_hover = hover
                self.update()
        if self.state.tool == "weights":
            self._mouse = pos
            if self._drag and self._drag.get("kind") == "paint":
                changed = self.paint_at(pos, record=not self._drag["recorded"])
                self._drag["recorded"] = self._drag["recorded"] or changed > 0
                return
            self.update()
        if self._pan_from is not None:
            self.pan += pos - self._pan_from
            self._pan_from = pos
            self.update()
            return
        if self._drag and self._drag.get("op") == "motion":
            self._drag_motion_path(pos)
            return
        if self._drag:
            wx, wy = self.to_world(pos)
            self.drag_to(wx, wy, axis_only=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        else:
            wx, wy = self.to_world(pos)
            self.status.emit(f"x {wx:.1f}  y {wy:.1f}")

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_from is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_from = None
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag and self._drag.get("op") == "motion":
                self._drag = None
                self.state.doc.notify()
                return
            self.end_drag()

    # --- reference footage -------------------------------------------------------------------
    def reference_rect(self):
        """The footage's corners on screen (QRectF), or None when there is nothing to show."""
        from PySide6.QtCore import QRectF
        from .reference import ReferenceSettings
        ref = self.reference
        if ref is None or not ref.frames or self.read_only:
            return None
        s = ReferenceSettings.of(self.state.doc)
        if not s.on:
            return None
        w, h = ref.size.width() * s.scale, ref.size.height() * s.scale
        return QRectF(self.to_screen(s.x - w / 2, s.y + h / 2), self.to_screen(s.x + w / 2, s.y - h / 2)).normalized()

    def _draw_reference(self, p: QPainter, front: bool) -> None:
        from .reference import ReferenceSettings
        rect = self.reference_rect()
        if rect is None:
            return
        s = ReferenceSettings.of(self.state.doc)
        if s.front != front:
            return
        frame = self.reference.frame_at(s.start + self.state.time * s.speed)
        if frame is None:
            return
        p.save()
        p.resetTransform()
        p.setOpacity(max(0.0, min(1.0, s.opacity)))
        if s.flip:
            p.translate(rect.center().x() * 2, 0)
            p.scale(-1, 1)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(rect, frame)
        p.restore()

    # --- motion path -------------------------------------------------------------------------
    def _motion_bone(self) -> str | None:
        st = self.state
        if not (st.motion_paths and st.animating and st.animation and not self.read_only):
            return None
        sel, doc = st.selection, st.doc
        bone = None
        if sel.kind == "bone":
            bone = sel.name
        elif sel.kind in ("slot", "attachment"):  # an image: the path of the bone it's on
            slot = sel.owner if sel.kind == "attachment" else sel.name
            bone = doc.slot(slot)["bone"] if doc.has_slot(slot) else None
        return bone if bone and st.doc.has_bone(bone) else None

    def _draw_motion_path(self, p: QPainter, t: float) -> None:
        from .motion_path import path_points
        bone = self._motion_bone()
        if bone is None:
            return
        points = path_points(self.state.doc, self.state.animation, bone)
        if len(points) < 2:
            return
        screen = [self.to_screen(pt.x, pt.y) for pt in points]
        p.setPen(QPen(QColor(255, 210, 90, 150), 1.5))
        p.drawPolyline(screen)
        for pt, s in zip(points, screen):
            now = abs(pt.time - t) < 1e-4
            if pt.key:
                p.setPen(QPen(QColor("#1e1f26"), 1))
                p.setBrush(QColor("#ff9f3a") if not now else QColor("#ffffff"))
                path = QPainterPath()
                path.addPolygon(QPolygonF([QPointF(s.x(), s.y() - 6), QPointF(s.x() + 6, s.y()),
                                           QPointF(s.x(), s.y() + 6), QPointF(s.x() - 6, s.y())]))
                p.drawPath(path)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 230) if now else QColor(255, 210, 90, 200))
                p.drawEllipse(s, 2.6 if not now else 4, 2.6 if not now else 4)

    def _press_motion_path(self, pos: QPointF) -> bool:
        """Grab a dot of the motion path: dragging it keys the bone's position at that frame."""
        from .motion_path import path_points, translate_key_at
        bone = self._motion_bone()
        if bone is None:
            return False
        st = self.state
        best = None
        for pt in path_points(st.doc, st.animation, bone):
            s = self.to_screen(pt.x, pt.y)
            d = math.hypot(s.x() - pos.x(), s.y() - pos.y())
            if d <= (8 if pt.key else 6) and (best is None or d < best[0] or (pt.key and not best[1].key)):
                best = (d, pt)
        if best is None:
            return False
        pt = best[1]
        st.doc.checkpoint(f"Move {bone} on its path")
        st.set_time(pt.time)
        self._drag = {"op": "motion", "kind": "motion", "name": bone, "time": pt.time,
                      "press": self.to_world(pos), "base": translate_key_at(st.doc, st.animation, bone, pt.time)}
        return True

    def _drag_motion_path(self, pos: QPointF) -> None:
        from .motion_path import move_to
        d, st = self._drag, self.state
        wx, wy = self.to_world(pos)
        move_to(st.doc, st.animation, d["name"], d["time"], d["base"], wx - d["press"][0], wy - d["press"][1])
        self.update()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.set_zoom(self.zoom * factor, event.position())


_CHECKER = None


def _checker_brush():
    """Grey checkerboard, 16-pixel squares."""
    global _CHECKER
    if _CHECKER is None:
        from PySide6.QtGui import QBrush, QPixmap
        tile = QPixmap(32, 32)
        tile.fill(QColor("#5c5c5c"))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 16, 16, QColor("#4f4f4f"))
        painter.fillRect(16, 16, 16, 16, QColor("#4f4f4f"))
        painter.end()
        _CHECKER = QBrush(tile)
    return _CHECKER


def _in_triangle(px: float, py: float, a, b, c) -> bool:
    def side(p1, p2):
        return (px - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (py - p2[1])
    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _dist_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    abx, aby = b.x() - a.x(), b.y() - a.y()
    length2 = abx * abx + aby * aby
    if length2 < 1e-9:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = max(0.0, min(1.0, ((p.x() - a.x()) * abx + (p.y() - a.y()) * aby) / length2))
    return math.hypot(p.x() - (a.x() + t * abx), p.y() - (a.y() + t * aby))
