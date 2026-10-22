"""Timeline: mode switch, animation picker, playback, and a dopesheet or graph editor for keys."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from .document import EditorError, KeyRef
from .graph import GraphView
from .state import EditorState

NAME_W = 190
RULER_H = 22
ROW_H = 20
from .easing import BAKED_EASES, EASE_PRESETS

CURVES = {
    "Linear": None,
    "Stepped": "stepped",
    **EASE_PRESETS,
    **{name: "baked" for name in BAKED_EASES},
    "Bezier (edit handles in the graph)": "bezier",
    "Copy curve": "copy",
    "Paste curve": "paste",
}
TIMELINE_COLORS = {"rotate": "#7fc8ff", "translate": "#8ee08e", "translatex": "#8ee08e", "translatey": "#8ee08e",
                   "scale": "#ffb36b", "scalex": "#ffb36b", "scaley": "#ffb36b", "shear": "#d9a0ff",
                   "shearx": "#d9a0ff", "sheary": "#d9a0ff", "attachment": "#e0e0e0", "rgba": "#ff8ec6",
                   "rgb": "#ff8ec6", "alpha": "#ff8ec6", "rgba2": "#ff8ec6", "rgb2": "#ff8ec6", "events": "#ffd27f",
                   "drawOrder": "#b0b0b0", "ik": "#ff8af0", "deform": "#7fe0d0", "transform": "#ffb347",
                   "position": "#ff7f00", "spacing": "#ff7f00", "mix": "#ff7f00"}


class Dopesheet(QWidget):
    selection_changed = Signal()

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.frame_px = 14.0
        self.scroll_x = 0.0
        self.scroll_y = 0
        self.selected: set[KeyRef] = set()
        self._drag: dict | None = None
        self.setMinimumHeight(60)  # the window can be made small; it scrolls
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for sig in (state.document_changed, state.time_changed, state.mode_changed):
            sig.connect(self.update)

    @property
    def fps(self) -> float:
        return self.state.doc.fps

    def rows(self):
        st = self.state
        return st.doc.timeline_rows(st.animation) if st.animation else []

    def time_to_x(self, t: float) -> float:
        return NAME_W + t * self.fps * self.frame_px - self.scroll_x + 8

    def x_to_time(self, x: float) -> float:
        return max(0.0, (x - NAME_W - 8 + self.scroll_x) / (self.fps * self.frame_px))

    def snap(self, t: float) -> float:
        return round(round(t * self.fps) / self.fps, 4)

    def paint_ruler(self, p: QPainter, width: float, height: float) -> None:
        """Frame ruler, frame grid lines and the shaded area after the animation's end."""
        st = self.state
        duration = st.doc.animation_duration(st.animation) if st.animation else 0
        p.fillRect(QRectF(0, 0, width, RULER_H), QColor("#2c2f37"))
        first = max(0, int(self.x_to_time(NAME_W) * self.fps))
        last = int(self.x_to_time(width) * self.fps) + 1
        label_every = 1 if self.frame_px >= 30 else 5 if self.frame_px >= 8 else 10
        for f in range(first, last + 1):
            x = self.time_to_x(f / self.fps)
            if x < NAME_W:
                continue
            major = f % label_every == 0
            p.setPen(QColor(255, 255, 255, 70 if major else 30))
            p.drawLine(QPointF(x, RULER_H - (10 if major else 5)), QPointF(x, height))
            if major:
                p.setPen(QColor("#b8bcc6"))
                p.drawText(QPointF(x + 2, 12), str(f))
        if duration > 0:
            end_x = max(NAME_W, self.time_to_x(duration))
            p.fillRect(QRectF(end_x, RULER_H, width - end_x, height), QColor(0, 0, 0, 60))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#22242a"))
        st = self.state
        rows = self.rows()
        self.paint_ruler(p, self.width(), self.height())
        # rows
        for i, (row, times) in enumerate(rows):
            y = RULER_H + i * ROW_H - self.scroll_y
            if y + ROW_H < RULER_H or y > self.height():
                continue
            if i % 2:
                p.fillRect(QRectF(0, y, self.width(), ROW_H), QColor(255, 255, 255, 8))
            color = QColor(TIMELINE_COLORS.get(row.timeline, "#cccccc"))
            p.setPen(QColor("#d0d3da"))
            owner = row.owner.replace("\n", " / ")  # deform rows: "slot / attachment"
            label = f"{owner} · {row.timeline}" if owner else row.timeline
            p.drawText(QRectF(6, y, NAME_W - 10, ROW_H), Qt.AlignmentFlag.AlignVCenter, label)
            for t in times:
                ref = KeyRef(row.group, row.owner, row.timeline, t)
                shown = t + (self._drag["dt"] if self._drag and self._drag.get("moving") and ref in self.selected else 0)
                x = self.time_to_x(shown)
                if x < NAME_W:
                    continue
                cy = y + ROW_H / 2
                sel = ref in self.selected
                p.setBrush(QColor("#ffcf40") if sel else color)
                p.setPen(QPen(QColor("#000"), 1))
                p.drawPolygon(QPolygonF([QPointF(x, cy - 5), QPointF(x + 5, cy), QPointF(x, cy + 5), QPointF(x - 5, cy)]))
        p.fillRect(QRectF(0, RULER_H, NAME_W, self.height()), QColor(0, 0, 0, 0))
        p.setPen(QColor(255, 255, 255, 40))
        p.drawLine(QPointF(NAME_W, 0), QPointF(NAME_W, self.height()))
        if self._drag and self._drag.get("box"):
            p.setPen(QPen(QColor("#ffcf40"), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 207, 64, 30))
            p.drawRect(self._box_rect())
        # playhead
        x = self.time_to_x(st.time)
        if x >= NAME_W:
            p.setPen(QPen(QColor("#ff5c5c"), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        if not st.animation:
            p.setPen(QColor("#9aa0aa"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No animation. Create one in the tree or switch to Animate.")
        elif not rows:
            p.setPen(QColor("#9aa0aa"))
            p.drawText(QRectF(NAME_W, RULER_H, self.width() - NAME_W, 40), Qt.AlignmentFlag.AlignCenter,
                       "No keys yet: in Animate mode move a bone, or press K")
        p.end()

    def key_at(self, pos: QPointF) -> KeyRef | None:
        for i, (row, times) in enumerate(self.rows()):
            y = RULER_H + i * ROW_H - self.scroll_y
            if not (y <= pos.y() < y + ROW_H):
                continue
            for t in times:
                if abs(self.time_to_x(t) - pos.x()) <= 6:
                    return KeyRef(row.group, row.owner, row.timeline, t)
        return None

    def keys_in_rect(self, rect: QRectF) -> set[KeyRef]:
        found = set()
        for i, (row, times) in enumerate(self.rows()):
            cy = RULER_H + i * ROW_H - self.scroll_y + ROW_H / 2
            if not rect.top() - 5 <= cy <= rect.bottom() + 5:
                continue
            for t in times:
                if rect.left() - 5 <= self.time_to_x(t) <= rect.right() + 5:
                    found.add(KeyRef(row.group, row.owner, row.timeline, t))
        return found

    def _box_rect(self) -> QRectF:
        d = self._drag
        return QRectF(QPointF(min(d["x0"], d["x1"]), min(d["y0"], d["y1"])),
                      QPointF(max(d["x0"], d["x1"]), max(d["y0"], d["y1"])))

    def select_all(self) -> None:
        self.selected = {KeyRef(row.group, row.owner, row.timeline, t) for row, times in self.rows() for t in times}
        self.selection_changed.emit()
        self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if pos.y() < RULER_H:  # the ruler scrubs
            self._drag = {"scrub": True}
            self.state.set_time(self.snap(self.x_to_time(pos.x())))
        elif pos.x() > NAME_W:
            key = self.key_at(pos)
            if key:
                if additive:
                    self.selected ^= {key}
                elif key not in self.selected:
                    self.selected = {key}
                self._drag = {"start_x": pos.x(), "dt": 0.0, "moving": False}
                self.state.set_time(key.time)
            else:  # empty space: drag a box to select keys
                self._drag = {"box": True, "x0": pos.x(), "y0": pos.y(), "x1": pos.x(), "y1": pos.y(),
                              "base": set(self.selected) if additive else set()}
                if not additive:
                    self.selected.clear()
        else:
            return
        self.selection_changed.emit()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._drag:
            return
        pos = event.position()
        if self._drag.get("scrub"):
            self.state.set_time(self.snap(self.x_to_time(pos.x())))
            return
        if self._drag.get("box"):
            self._drag["x1"], self._drag["y1"] = pos.x(), pos.y()
            self.selected = self._drag["base"] | self.keys_in_rect(self._box_rect())
            self.selection_changed.emit()
            self.update()
            return
        dt = self.snap((pos.x() - self._drag["start_x"]) / (self.fps * self.frame_px))
        if abs(pos.x() - self._drag["start_x"]) > 3:
            self._drag["moving"] = True
        self._drag["dt"] = dt
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag and drag.get("box"):
            if abs(drag["x1"] - drag["x0"]) < 3 and abs(drag["y1"] - drag["y0"]) < 3 and not drag["base"]:
                self.state.set_time(self.snap(self.x_to_time(drag["x0"])))  # a plain click moves the playhead
            self.update()
            return
        if drag and drag.get("moving") and drag["dt"] and self.selected and self.state.animation:
            try:
                moved = self.state.doc.move_keys(self.state.animation, sorted(self.selected, key=lambda r: r.time),
                                                 drag["dt"])
                self.selected = set(moved)
                if moved:
                    self.state.set_time(moved[0].time)
            except EditorError as exc:
                QMessageBox.warning(self, "Anim2D", str(exc))
            self.selection_changed.emit()
        self.update()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            self.frame_px = max(2.0, min(80.0, self.frame_px * factor))
        else:
            self.scroll_y = max(0, self.scroll_y - event.angleDelta().y() // 4)
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        animating = self.state.animating
        for label, slot, enabled in (("Copy keys", self.copy_selected, bool(self.selected)),
                                     ("Paste keys at the playhead", self.paste,
                                      animating and self.state.key_clipboard is not None),
                                     ("Delete keys", self.delete_selected, bool(self.selected)),
                                     ("Select all keys", self.select_all, bool(self.rows()))):
            action = QAction(label, menu)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.exec(event.globalPos())

    def copy_selected(self) -> None:
        if self.selected and self.state.animation:
            self.state.key_clipboard = self.state.doc.copy_keys(self.state.animation, list(self.selected))
            self.state.message.emit(f"Copied {len(self.selected)} key(s)")

    def paste(self) -> None:
        """Paste at the playhead. Keys copied from one bone (or slot) go onto the selected bone
        (or slot) when one is selected; otherwise back onto the rows they came from."""
        st = self.state
        clip = st.key_clipboard
        if clip is None or not st.animating:
            return
        owner = None
        owners = {(r["row"][0], r["row"][1]) for r in clip["rows"]}
        sel_group = {"bone": "bones", "slot": "slots"}.get(st.selection.kind or "")
        if len(owners) == 1 and sel_group == next(iter(owners))[0]:
            owner = st.selection.name
        try:
            self.selected = set(st.doc.paste_keys(st.animation, clip, st.time, owner=owner))
        except EditorError as exc:
            QMessageBox.information(self, "Anim2D", str(exc))
            return
        self.selection_changed.emit()
        self.update()

    def delete_selected(self) -> None:
        if self.selected and self.state.animation:
            self.state.doc.delete_keys(self.state.animation, list(self.selected))
            self.selected.clear()
            self.selection_changed.emit()


class TimelinePanel(QWidget):
    graph_requested = Signal()

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.sheet = Dopesheet(state)
        self.graph = GraphView(state, self.sheet)
        self.sheet.selection_changed.connect(self.graph.update)  # the window shows the sheet and graph in docks
        self.setup_button = QPushButton("Setup")
        self.animate_button = QPushButton("Animate")
        for b in (self.setup_button, self.animate_button):
            b.setCheckable(True)
            b.setMinimumWidth(80)
        self.setup_button.clicked.connect(lambda: state.set_mode("setup"))
        self.animate_button.clicked.connect(lambda: state.set_mode("animate"))
        self.skin_combo = QComboBox()
        self.skin_combo.setMinimumWidth(110)
        self.skin_combo.setToolTip("The skin shown and played (like Spine's skin picker)")
        self.skin_combo.activated.connect(lambda _: self._pick_skin(self.skin_combo.currentText()))
        self.skin_only = QCheckBox("This skin only")
        self.skin_only.setChecked(True)
        self.skin_only.setToolTip("List only the animations of the chosen skin (by name: 'L1_intro' belongs to "
                                  "skin 'L1') and those that belong to no skin")
        self.skin_only.toggled.connect(lambda _: self.refresh())
        self.animation_combo = QComboBox()
        self.animation_combo.setMinimumWidth(160)
        self.animation_combo.activated.connect(lambda _: state.set_animation(self.animation_combo.currentText() or None))
        self.first_button = QPushButton("⏮")
        self.play_button = QPushButton("▶")
        self.last_button = QPushButton("⏭")
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setChecked(True)
        self.sound_check = QCheckBox("🔊")
        self.sound_check.setChecked(True)
        self.sound_check.setToolTip("Play event sounds while playing (Events window: Audio path)")
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(170)
        self.key_button = QPushButton("Key (K)")
        self.key_button.setToolTip("Key rotate, translate and scale of the selected bone at the current time")
        self.curve_combo = QComboBox()
        self.curve_combo.addItems(["Curve…", *CURVES])
        self.curve_combo.setToolTip("Interpolation from the selected keys to the next key")
        self.curve_combo.activated.connect(self._apply_curve)
        self.delete_button = QPushButton("Delete keys")
        self.auto_key_button = QPushButton("● Auto Key")
        self.auto_key_button.setCheckable(True)
        self.auto_key_button.setChecked(state.auto_key)
        self.auto_key_button.setToolTip("On: changes in Animate mode become keys at once. Off: changes show until "
                                        "you change the time; press Key (K) to keep them")
        self.auto_key_button.setStyleSheet("QPushButton:checked { background: #c0392b; color: white; }")
        self.auto_key_button.toggled.connect(state.set_auto_key)
        self.onion_button = QToolButton()
        self.onion_button.setText("👻 Onion")
        self.onion_button.setCheckable(True)
        self.onion_button.setToolTip("Onion skin (Animate mode): faint copies of earlier (red) and later (green) "
                                     "frames. ▸ for how many, how far apart, and how strong")
        self.onion_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.onion_button.toggled.connect(lambda on: state.set_onion(on=on))
        menu = QMenu(self.onion_button)
        self.onion_fields = {}
        for key, label, low, high in (("before", "Frames before", 0, 10), ("after", "Frames after", 0, 10),
                                      ("step", "Step (frames)", 1, 30)):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(state.onion[key])
            spin.setPrefix(f"{label}: ")
            spin.valueChanged.connect(lambda v, k=key: state.set_onion(**{k: v}))
            holder = QWidgetAction(menu)
            holder.setDefaultWidget(spin)
            menu.addAction(holder)
            self.onion_fields[key] = spin
        opacity = QSpinBox()
        opacity.setRange(10, 100)
        opacity.setSuffix(" %")
        opacity.setPrefix("Strength: ")
        opacity.setValue(round(state.onion["opacity"] * 100))
        opacity.valueChanged.connect(lambda v: state.set_onion(opacity=v / 100))
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(opacity)
        menu.addAction(holder)
        self.onion_fields["opacity"] = opacity
        selected = menu.addAction("Selected only (bone and its children, or slot)")
        selected.setCheckable(True)
        selected.setChecked(state.onion["selected"])
        selected.toggled.connect(lambda on: state.set_onion(selected=on))
        self.onion_selected_action = selected
        front = menu.addAction("Draw ghosts in front")
        front.setCheckable(True)
        front.setChecked(state.onion["front"])
        front.toggled.connect(lambda on: state.set_onion(front=on))
        self.onion_front_action = front
        self.onion_button.setMenu(menu)
        self.first_button.clicked.connect(lambda: state.set_time(0))
        self.last_button.clicked.connect(lambda: state.set_time(self._duration()))
        self.play_button.clicked.connect(self.toggle_play)
        self.key_button.clicked.connect(self._key)
        self.delete_button.clicked.connect(self.sheet.delete_selected)

        bar = QHBoxLayout()
        for w in (self.setup_button, self.animate_button, QLabel("Skin"), self.skin_combo, self.skin_only,
                  QLabel("Animation"), self.animation_combo,
                  self.first_button, self.play_button, self.last_button, self.loop_check, self.sound_check,
                  self.time_label):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.onion_button, self.auto_key_button, self.key_button, self.curve_combo, self.delete_button):
            bar.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._start_time = 0.0
        from .event_audio import EventAudio
        self.audio = EventAudio(state.doc)
        self._heard = 0.0  # where the playhead was at the last tick, for event sounds
        for sig in (state.document_changed, state.mode_changed, state.time_changed):
            sig.connect(self.refresh)
        self.refresh()

    def listed_animations(self) -> list[str]:
        doc = self.state.doc
        if self.skin_only.isChecked() and doc.edit_skin != "default":
            return doc.skin_animations(doc.edit_skin)
        return doc.animation_names()

    def _pick_skin(self, skin: str) -> None:
        """Show that skin; if the current animation is another skin's, switch to one of this skin's."""
        st = self.state
        if not skin:
            return
        st.doc.set_edit_skin(skin)
        listed = self.listed_animations()
        if st.animation not in listed:
            own = [a for a in listed if st.doc.animation_skin(a) == skin]
            current = st.animation or ""
            # keep the same kind of animation: L2_intro -> L1_intro when switching to L1
            suffix = current[len(st.doc.animation_skin(current) or ""):]
            same = [a for a in own if suffix and a.endswith(suffix)]
            st.set_animation((same or own or listed or [None])[0])
        self.refresh()

    def _duration(self) -> float:
        return self.state.doc.animation_duration(self.state.animation) if self.state.animation else 0.0

    def refresh(self) -> None:
        st = self.state
        self.setup_button.setChecked(st.mode == "setup")
        self.animate_button.setChecked(st.mode == "animate")
        skins = st.doc.skin_names()
        if [self.skin_combo.itemText(i) for i in range(self.skin_combo.count())] != skins:
            self.skin_combo.clear()
            self.skin_combo.addItems(skins)
        self.skin_combo.setCurrentText(st.doc.edit_skin)
        names = self.listed_animations()
        if [self.animation_combo.itemText(i) for i in range(self.animation_combo.count())] != names:
            self.animation_combo.clear()
            self.animation_combo.addItems(names)
        self.animation_combo.setCurrentText(st.animation or "")
        animate = st.animating
        for w in (self.play_button, self.first_button, self.last_button, self.key_button, self.curve_combo,
                  self.delete_button):
            w.setEnabled(animate)
        frame = round(st.time * st.doc.fps)
        self.time_label.setText(f"frame {frame}  ·  {st.time:.3f}s / {self._duration():.3f}s")
        if not animate and self._timer.isActive():
            self.stop()

    def toggle_play(self) -> None:
        if self._timer.isActive():
            self.stop()
        else:
            if self._duration() <= 0:
                return
            if self.state.time >= self._duration():
                self.state.set_time(0)
            self._start_time = self.state.time
            self._heard = self.state.time - 1e-6  # an event right at the start plays too
            self._clock.restart()
            self._timer.start()
            self.play_button.setText("⏸")

    def stop(self) -> None:
        self._timer.stop()
        self.play_button.setText("▶")

    def _tick(self) -> None:
        duration = self._duration()
        t = self._start_time + self._clock.elapsed() / 1000
        if t > duration:
            if self.loop_check.isChecked() and duration > 0:
                t %= duration
                self._start_time, t = t, t
                self._clock.restart()
            else:
                t = duration
                self.stop()
        self.audio.doc, self.audio.enabled = self.state.doc, self.sound_check.isChecked()
        self.audio.advance(self.state.animation, self._heard, t)
        self._heard = t
        self.state.set_time(t)

    def _key(self) -> None:
        st = self.state
        try:
            if st.selection.kind == "ik" and st.animating:
                st.doc.set_ik_key(st.animation, st.selection.name, st.time)
                return
            if st.selection.kind == "transform" and st.animating:
                st.doc.set_transform_key(st.animation, st.selection.name, st.time)
                return
            if st.selection.kind == "path" and st.animating:
                st.doc.checkpoint(f"Key {st.selection.name}")
                for timeline in ("position", "spacing", "mix"):
                    st.doc.set_path_key(st.animation, st.selection.name, timeline, st.time, record=False)
                return
            st.key_selected_bone()
        except EditorError as exc:
            QMessageBox.information(self, "Anim2D", str(exc))

    def _apply_curve(self, index: int) -> None:
        name = self.curve_combo.itemText(index)
        self.curve_combo.setCurrentIndex(0)
        if name not in CURVES or not self.sheet.selected or not self.state.animation:
            return
        from . import easing
        st, refs = self.state, sorted(self.sheet.selected, key=lambda r: r.time)
        try:
            if CURVES[name] == "bezier":
                self.graph.make_bezier()
                self.graph_requested.emit()  # the window brings the Graph to the front
            elif CURVES[name] == "copy":
                st.curve_clipboard = easing.copy_curve(st.doc, st.animation, refs[0])
                st.message.emit("Curve copied: choose keys, then Curve… > Paste curve")
            elif CURVES[name] == "paste":
                if not hasattr(st, "curve_clipboard"):
                    raise EditorError("Copy a curve first (Curve… > Copy curve)")
                easing.paste_curve(st.doc, st.animation, refs, st.curve_clipboard)
            elif CURVES[name] == "baked":
                added = easing.bake_ease(st.doc, st.animation, refs, name)
                st.message.emit(f"{name.split(' (')[0]}: {added} key(s) added")
            else:
                st.doc.set_curve(st.animation, refs, CURVES[name])
        except EditorError as exc:
            QMessageBox.information(self, "Anim2D", str(exc))
