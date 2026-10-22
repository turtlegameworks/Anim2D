"""Graph editor: each value channel of the shown timelines as a curve over time.

Rows shown: the rows of the keys selected in the dopesheet, or else every row of the selected
bone, slot or IK constraint. Drag a key point up or down to change its value, drag a bezier
handle to shape the segment, double-click a curve to add a key there (the motion is unchanged).
Time uses the dopesheet's zoom and scroll; values fit the view (F) and zoom with the wheel.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMessageBox, QWidget

from ..formats import curves as cv
from ..formats import timelines40 as tl40
from .document import EditorError, KeyRef
from .state import EditorState

NAME_W = 190  # same layout as the dopesheet (timeline.py)
RULER_H = 22
PALETTE = ["#ff6b6b", "#6bd66b", "#6ba8ff", "#e8e8e8", "#ffb36b", "#d9a0ff", "#7fe0d0"]
HANDLE_R = 4.5
POINT_R = 4.0


class GraphView(QWidget):
    selection_changed = Signal()

    def __init__(self, state: EditorState, sheet, parent=None):
        super().__init__(parent)
        self.state = state
        self.sheet = sheet  # the dopesheet: shared key selection, time zoom and scroll
        self.v_min, self.v_max = -1.0, 1.0
        self._fitted_for: tuple | None = None
        self.hidden: set[tuple[KeyRef, int]] = set()
        self._hits: list[tuple[str, QPointF, tuple]] = []
        self._legend: list[tuple[QRectF, tuple[KeyRef, int]]] = []
        self._drag: dict | None = None
        self.setMinimumHeight(60)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for sig in (state.document_changed, state.time_changed, state.mode_changed, state.selection_changed):
            sig.connect(self.update)

    # --- what is shown ---------------------------------------------------------------------
    def rows(self) -> list[KeyRef]:
        st = self.state
        if not st.animation:
            return []
        all_rows = [row for row, _ in st.doc.timeline_rows(st.animation)
                    if tl40.channel_names(row.group, row.timeline)]
        picked = {(r.group, r.owner, r.timeline) for r in self.sheet.selected}
        if picked:
            return [r for r in all_rows if (r.group, r.owner, r.timeline) in picked]
        sel = st.selection
        groups = {"bone": "bones", "slot": "slots", "ik": "ik", "transform": "transform", "path": "path"}
        group = groups.get(sel.kind or "")
        if group is None:
            return []
        return [r for r in all_rows if r.group == group and r.owner == sel.name
                or group == "slots" and r.group == "deform" and r.owner.split("\n")[0] == sel.name]

    def _channels(self):
        """(row, channel index, name, keys, values per key) for every visible channel."""
        out = []
        doc, anim = self.state.doc, self.state.animation
        for row in self.rows():
            keys = doc.timeline_keys(anim, row)
            values = [doc.key_channels(row, k) for k in keys]
            for c, name in enumerate(tl40.channel_names(row.group, row.timeline)):
                out.append((row, c, name, keys, [v[c] for v in values]))
        return out

    # --- mapping ---------------------------------------------------------------------------
    def value_to_y(self, v: float) -> float:
        top, h = RULER_H + 10, max(10.0, self.height() - RULER_H - 20)
        span = (self.v_max - self.v_min) or 1.0
        return top + (self.v_max - v) / span * h

    def y_to_value(self, y: float) -> float:
        top, h = RULER_H + 10, max(10.0, self.height() - RULER_H - 20)
        return self.v_max - (y - top) / h * ((self.v_max - self.v_min) or 1.0)

    def fit(self) -> None:
        lo, hi = math.inf, -math.inf
        for row, c, _, keys, values in self._channels():
            if (row, c) in self.hidden:
                continue
            lo, hi = min(lo, *values), max(hi, *values)
            for i, k in enumerate(keys[:-1]):
                h = cv.channel_handles(k.get("curve"), c)
                if h is not None and row.group != "deform":
                    lo, hi = min(lo, h[1], h[3]), max(hi, h[1], h[3])
        if lo is math.inf:
            lo, hi = -1.0, 1.0
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        self.v_min, self.v_max = lo - pad, hi + pad
        self.update()

    def _segment_value(self, row: KeyRef, keys, values, c: int, t: float) -> float:
        i = cv.find_segment(keys, t)
        if i is None:
            return values[0]
        if i + 1 >= len(keys):
            return values[-1]
        if row.group == "deform":  # blend 0..1 within each segment, drawn as such
            return cv.segment_value(keys[i], keys[i + 1], t, 0, 0.0, 1.0)
        return cv.segment_value(keys[i], keys[i + 1], t, c, values[i], values[i + 1])

    # --- painting --------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        signature = (self.state.animation, tuple(self.rows()))
        if signature != self._fitted_for:
            self._fitted_for = signature
            self.fit()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1f2126"))
        self.sheet.paint_ruler(p, self.width(), self.height())
        self._paint_value_grid(p)
        self._hits, self._legend = [], []
        channels = self._channels()
        selected = self.sheet.selected
        x0, x1 = NAME_W, self.width()
        for n, (row, c, name, keys, values) in enumerate(channels):
            color = QColor(PALETTE[(c if len(channels) <= len(PALETTE) else n) % len(PALETTE)])
            ly = RULER_H + 6 + n * 18
            hidden = (row, c) in self.hidden
            parts = [row.owner.replace("\n", " / "), row.timeline, name]
            label = " · ".join(dict.fromkeys(p for p in parts if p))  # "arm · rotate", "arm · translate · x"
            rect = QRectF(6, ly, NAME_W - 12, 16)
            self._legend.append((rect, (row, c)))
            p.setPen(QColor(color) if not hidden else QColor("#5a5e66"))
            p.drawText(rect, Qt.AlignmentFlag.AlignVCenter, ("○ " if hidden else "● ") + label)
            if hidden or not keys:
                continue
            path = QPainterPath()
            for px in range(int(x0), int(x1) + 1, 2):
                t = self.sheet.x_to_time(px)
                y = self.value_to_y(self._segment_value(row, keys, values, c, t))
                if px == int(x0):
                    path.moveTo(px, y)
                else:
                    path.lineTo(px, y)
            p.setPen(QPen(color, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            for i, k in enumerate(keys):
                t = k.get("time", 0)
                ref = KeyRef(row.group, row.owner, row.timeline, t)
                value = values[i] if row.group != "deform" else 0.0
                pt = QPointF(self.sheet.time_to_x(t), self.value_to_y(value))
                if pt.x() < NAME_W:
                    continue
                is_sel = ref in selected
                if is_sel:
                    self._paint_handles(p, row, c, keys, values, i, color)
                p.setPen(QPen(QColor("#000"), 1))
                p.setBrush(QColor("#ffcf40") if is_sel else color)
                p.drawRect(QRectF(pt.x() - POINT_R, pt.y() - POINT_R, POINT_R * 2, POINT_R * 2))
                self._hits.append(("key", pt, (row, c, i)))
        x = self.sheet.time_to_x(self.state.time)
        if x >= NAME_W:
            p.setPen(QPen(QColor("#ff5c5c"), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setPen(QColor(255, 255, 255, 40))
        p.drawLine(QPointF(NAME_W, 0), QPointF(NAME_W, self.height()))
        if not channels:
            p.setPen(QColor("#9aa0aa"))
            p.drawText(QRectF(NAME_W, RULER_H, self.width() - NAME_W, self.height() - RULER_H),
                       Qt.AlignmentFlag.AlignCenter,
                       "Select keys in the dopesheet, or a bone, slot or IK constraint with keys")
        p.end()

    def _paint_value_grid(self, p: QPainter) -> None:
        span = self.v_max - self.v_min
        if span <= 0:
            return
        step = 10 ** math.floor(math.log10(span / 4))
        for mult in (1, 2, 5, 10):
            if span / (step * mult) <= 8:
                step *= mult
                break
        v = math.ceil(self.v_min / step) * step
        while v <= self.v_max:
            y = self.value_to_y(v)
            p.setPen(QColor(255, 255, 255, 45 if abs(v) < step / 2 else 18))
            p.drawLine(QPointF(NAME_W, y), QPointF(self.width(), y))
            p.setPen(QColor("#7d828c"))
            p.drawText(QPointF(NAME_W + 4, y - 2), f"{v:g}")
            v += step

    @staticmethod
    def _segment_handles(row, c, keys, i):
        """Absolute handles of the segment starting at key i, or None (linear, stepped, last key)."""
        if i + 1 >= len(keys):
            return None
        return cv.channel_handles(keys[i].get("curve"), c if row.group != "deform" else 0)

    def _paint_handles(self, p, row, c, keys, values, i, color) -> None:
        """The key's outgoing handle and the previous segment's incoming handle. Deform segments
        blend 0 -> 1, so their handles are drawn on that scale."""
        def at(t, v):
            return QPointF(self.sheet.time_to_x(t), self.value_to_y(v))

        for seg, end in ((i, 0), (i - 1, 1)):
            if seg < 0:
                continue
            h = self._segment_handles(row, c, keys, seg)
            if h is None:
                continue
            anchor_value = float(end) if row.group == "deform" else values[seg + end]
            anchor = at(keys[seg + end].get("time", 0), anchor_value)
            hp = at(h[2 * end], h[2 * end + 1])
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.drawLine(anchor, hp)
            p.setBrush(QColor("#1f2126"))
            p.setPen(QPen(color, 1.4))
            p.drawEllipse(hp, HANDLE_R, HANDLE_R)
            self._hits.append(("handle", hp, (row, c, seg, end)))

    # --- interaction -----------------------------------------------------------------------
    def _hit(self, pos: QPointF):
        """Nearest key point or handle within 8 px; a handle wins over a key it sits on."""
        best, best_d = None, 8.0
        for kind, pt, payload in self._hits:
            d = math.hypot(pt.x() - pos.x(), pt.y() - pos.y()) - (2 if kind == "handle" else 0)
            if d < best_d:
                best, best_d = (kind, payload), d
        return best

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        st = self.state
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag = {"pan": True, "x": pos.x(), "y": pos.y(), "scroll": self.sheet.scroll_x,
                          "range": (self.v_min, self.v_max)}
            return
        if event.button() != Qt.MouseButton.LeftButton or not st.animation:
            return
        if pos.x() < NAME_W:
            for rect, item in self._legend:
                if rect.contains(pos):
                    self.hidden ^= {item}
                    self.update()
            return
        if pos.y() < RULER_H:
            self._drag = {"scrub": True}
            st.set_time(self.sheet.snap(self.sheet.x_to_time(pos.x())))
            return
        hit = self._hit(pos)
        if hit is None:
            if not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.sheet.selected.clear()
                self._changed_selection()
            return
        kind, payload = hit
        doc = st.doc
        row = payload[0]
        keys = doc.timeline_keys(st.animation, row)
        if kind == "key":
            _, c, i = payload
            ref = KeyRef(row.group, row.owner, row.timeline, keys[i].get("time", 0))
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.sheet.selected ^= {ref}
            elif ref not in self.sheet.selected:
                self.sheet.selected = {ref}
            self._changed_selection()
            st.set_time(ref.time)
            if row.group != "deform":
                self._drag = {"key": (ref, c), "y": pos.y(), "started": False}
        else:
            _, c, seg, end = payload
            ref = KeyRef(row.group, row.owner, row.timeline, keys[seg].get("time", 0))
            self._drag = {"handle": (ref, c, end), "started": False}

    def mouseMoveEvent(self, event) -> None:
        d = self._drag
        if not d:
            return
        pos = event.position()
        st = self.state
        if d.get("pan"):
            self.sheet.scroll_x = max(0.0, d["scroll"] - (pos.x() - d["x"]))
            lo, hi = d["range"]
            dv = (pos.y() - d["y"]) / max(10.0, self.height() - RULER_H - 20) * (hi - lo)
            self.v_min, self.v_max = lo + dv, hi + dv
            self.sheet.update()
            self.update()
            return
        if d.get("scrub"):
            st.set_time(self.sheet.snap(self.sheet.x_to_time(pos.x())))
            return
        try:
            if "key" in d:
                if not d["started"] and abs(pos.y() - d["y"]) < 3:
                    return
                ref, c = d["key"]
                if not d["started"]:
                    st.doc.checkpoint("Edit key value")
                    d["started"] = True
                st.doc.set_key_channel(st.animation, ref, c, self.y_to_value(pos.y()), record=False)
            elif "handle" in d:
                ref, c, end = d["handle"]
                keys = st.doc.timeline_keys(st.animation, ref)
                i = next(j for j, k in enumerate(keys) if abs(k.get("time", 0) - ref.time) < 1e-4)
                current = cv.channel_handles(keys[i].get("curve"), c if ref.group != "deform" else 0)
                if current is None:
                    return
                h = list(current)
                h[2 * end], h[2 * end + 1] = self.sheet.x_to_time(pos.x()), self.y_to_value(pos.y())
                if not d["started"]:
                    st.doc.checkpoint("Edit curve")
                    d["started"] = True
                st.doc.set_key_handles(st.animation, ref, c if ref.group != "deform" else 0, tuple(h), record=False)
        except EditorError as exc:
            self._drag = None
            QMessageBox.warning(self, "Anim2D", str(exc))

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click a curve: add a key there without changing the motion."""
        pos = event.position()
        st = self.state
        if pos.x() < NAME_W or pos.y() < RULER_H or not st.animation:
            return
        t = self.sheet.snap(self.sheet.x_to_time(pos.x()))
        best = None
        for row, c, _, keys, values in self._channels():
            if (row, c) in self.hidden or not keys:
                continue
            y = self.value_to_y(self._segment_value(row, keys, values, c, self.sheet.x_to_time(pos.x())))
            if abs(y - pos.y()) < 8 and (best is None or abs(y - pos.y()) < best[1]):
                best = (row, abs(y - pos.y()))
        if best is None:
            return
        try:
            ref = st.doc.insert_key(st.animation, best[0], t)
        except EditorError as exc:
            QMessageBox.information(self, "Anim2D", str(exc))
            return
        self.sheet.selected = {ref}
        self._changed_selection()
        st.set_time(ref.time)

    def make_bezier(self) -> None:
        """Give the selected keys' segments straight-line bezier handles so they can be dragged."""
        st = self.state
        if not st.animation:
            return
        refs = [r for r in self.sheet.selected if tl40.channel_names(r.group, r.timeline)]
        if not refs:
            return
        st.doc.checkpoint("Make bezier")
        for ref in refs:
            keys = st.doc.timeline_keys(st.animation, ref)
            i = next((j for j, k in enumerate(keys) if abs(k.get("time", 0) - ref.time) < 1e-4), None)
            if i is None or i + 1 >= len(keys) or isinstance(keys[i].get("curve"), list):
                continue
            t0, t1 = keys[i].get("time", 0), keys[i + 1].get("time", 0)
            if ref.group == "deform":
                v0, v1 = 0.0, 1.0
            else:
                v0 = st.doc.key_channels(ref, keys[i])[0]
                v1 = st.doc.key_channels(ref, keys[i + 1])[0]
            st.doc.set_key_handles(st.animation, ref, 0, tuple(cv.to_absolute((1 / 3, 1 / 3, 2 / 3, 2 / 3),
                                                                             t0, v0, t1, v1)), record=False)
        st.doc.notify()

    def wheelEvent(self, event) -> None:
        up = event.angleDelta().y() > 0
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.sheet.frame_px = max(2.0, min(80.0, self.sheet.frame_px * (1.2 if up else 1 / 1.2)))
            self.sheet.update()
        else:
            factor = 1 / 1.2 if up else 1.2
            v = self.y_to_value(event.position().y())
            self.v_min, self.v_max = v + (self.v_min - v) * factor, v + (self.v_max - v) * factor
        self.update()

    def event(self, event) -> bool:
        # F fits the graph while it has focus (the window's F frames the viewport otherwise).
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_F:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F:
            self.fit()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.sheet.delete_selected()
        else:
            super().keyPressEvent(event)

    def _changed_selection(self) -> None:
        self.sheet.selection_changed.emit()
        self.sheet.update()
        self.update()
