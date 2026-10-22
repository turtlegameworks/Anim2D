"""Preview: a second viewport that plays animations on its own, like Spine's Preview view.

It follows the animation being edited (or one you pick), loops it at any speed, can show another
skin, and can crossfade into a second animation the way the game's AnimationState does (one
track, setAnimation with a mix duration). It only shows: clicks never change the project.
"""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from ..preview.evaluator import MixEvaluator, PoseEvaluator, layered_animation
from .document import Document, EditorError
from .state import EditorState
from .viewport import Viewport

FOLLOW = "(animation being edited)"
EDIT_SKIN = "(skin being edited)"


class PreviewState(EditorState):
    """Editor state for the preview viewport: always posed, never edited, nothing selected."""

    def __init__(self, doc: Document):
        super().__init__(doc)
        self.mode = "animate"
        self.from_animation: str | None = None
        self.to_animation: str | None = None  # None: no crossfade
        self.switch = 0.0
        self.mix_duration = 0.2
        self.loop = True
        self.skin: str | None = None  # None: the skin being edited
        self.layer: str | None = None  # an animation on a track above (its keyed timelines win)
        self.physics = True  # simulate physics constraints
        self._physics_cache: tuple[str, dict] | None = None  # (animation, simulated copy)
        doc.listeners.append(self._forget_physics)

    def _forget_physics(self) -> None:
        self._physics_cache = None

    def _skin(self) -> str | None:
        name = self.skin if self.skin is not None else self.doc.edit_skin
        return name if name in self.doc.skin_names() and name != "default" else None

    def evaluator(self) -> PoseEvaluator:
        names = set(self.doc.data["animations"])
        a, b = self.from_animation, self.to_animation
        if a in names and b in names:
            return MixEvaluator(self.doc.data, a, b, self.switch, self.mix_duration, self.loop, self.loop,
                                self._skin())
        if a in names and self.physics and self.doc.data.get("physics") and self.layer not in names:
            from .physics import baked_animation
            try:
                if self._physics_cache is None or self._physics_cache[0] != a:
                    self._physics_cache = (a, baked_animation(self.doc, a))
                data = dict(self.doc.data)
                data["animations"] = {"(physics)": self._physics_cache[1]}
                return PoseEvaluator(data, "(physics)", self._skin())
            except EditorError:
                pass
        if a in names and self.layer in names and self.layer != a:
            anims = self.doc.data["animations"]
            data = dict(self.doc.data)
            data["animations"] = {"(layered)": layered_animation(anims[a], anims[self.layer], self.loop)}
            return PoseEvaluator(data, "(layered)", self._skin())
        return PoseEvaluator(self.doc.data, a if a in names else "", self._skin())

    @property
    def total_duration(self) -> float:
        names = self.doc.data["animations"]
        if self.to_animation in names and self.from_animation in names:
            return self.switch + max(self.doc.animation_duration(self.to_animation), self.mix_duration, 0.1)
        if self.from_animation in names and self.layer in names:
            return max(self.doc.animation_duration(self.from_animation), self.doc.animation_duration(self.layer))
        return self.doc.animation_duration(self.from_animation) if self.from_animation in names else 0.0


class PreviewPanel(QWidget):
    def __init__(self, main_state: EditorState, parent=None):
        super().__init__(parent)
        self.main_state = main_state
        self.state = PreviewState(main_state.doc)
        self.viewport = Viewport(self.state)
        self.viewport.read_only = True
        self.viewport.show_bones = False  # like Spine's Preview: the images only
        self.viewport.checker = True
        self.viewport.setMinimumSize(260, 220)

        self.from_combo = QComboBox()
        self.from_combo.setToolTip("The animation to play")
        self.skin_combo = QComboBox()
        self.layer_combo = QComboBox()
        self.layer_combo.setToolTip("Play another animation on a track above: what it keys replaces the "
                                    "animation below (like AnimationState track 1)")
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.05, 4.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix(" ×")
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setChecked(True)
        self.sound_check = QCheckBox("🔊")
        self.sound_check.setChecked(True)
        self.sound_check.setToolTip("Play event sounds")
        self.physics_check = QCheckBox("Physics")
        self.physics_check.setChecked(True)
        self.physics_check.setToolTip("Simulate physics constraints (springy bones)")
        self.bones_check = QCheckBox("Bones")
        self.bones_check.toggled.connect(self._show_bones)
        row = QHBoxLayout()
        row.addWidget(QLabel("Speed"))
        row.addWidget(self.speed_spin)
        row.addWidget(self.loop_check)
        row.addWidget(self.sound_check)
        row.addWidget(self.physics_check)
        row.addWidget(self.bones_check)
        row.addStretch(1)
        form = QFormLayout()
        form.addRow("Animation", self.from_combo)
        form.addRow("Skin", self.skin_combo)
        form.addRow("Layer on top", self.layer_combo)
        form.addRow(row)

        # Crossfade into a second animation (AnimationState.setAnimation with a mix duration).
        self.mix_group = QGroupBox("Crossfade into another animation")
        self.mix_group.setCheckable(True)
        self.mix_group.setChecked(False)
        self.to_combo = QComboBox()
        self.switch_spin = QDoubleSpinBox()
        self.switch_spin.setRange(0, 600)
        self.switch_spin.setDecimals(3)
        self.switch_spin.setSingleStep(0.1)
        self.switch_spin.setSuffix(" s")
        self.switch_spin.setToolTip("When the second animation is set (setAnimation)")
        self.mix_spin = QDoubleSpinBox()
        self.mix_spin.setRange(0, 10)
        self.mix_spin.setDecimals(3)
        self.mix_spin.setSingleStep(0.05)
        self.mix_spin.setValue(0.2)
        self.mix_spin.setSuffix(" s")
        self.mix_spin.setToolTip("Crossfade length (the mix duration in AnimationStateData)")
        mix_form = QFormLayout(self.mix_group)
        mix_form.addRow("Then", self.to_combo)
        mix_form.addRow("Switch at", self.switch_spin)
        mix_form.addRow("Mix", self.mix_spin)

        self.play_button = QPushButton("⏸ Pause")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.time_label = QLabel()
        bar = QHBoxLayout()
        bar.addWidget(self.play_button)
        bar.addWidget(self.slider, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(form)
        layout.addWidget(self.mix_group)
        layout.addWidget(self.viewport, 1)
        layout.addLayout(bar)
        layout.addWidget(self.time_label)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._start = 0.0
        from .event_audio import EventAudio
        self.audio = EventAudio(main_state.doc)
        self._heard = 0.0
        self._autoplay = True  # starts playing when shown
        self.play_button.clicked.connect(self.toggle_play)
        self.slider.valueChanged.connect(self._slider_moved)
        for w in (self.from_combo, self.to_combo, self.skin_combo, self.layer_combo):
            w.activated.connect(self._apply)
        self.to_combo.activated.connect(self._default_switch)
        self.mix_group.toggled.connect(self._mix_toggled)
        for w in (self.switch_spin, self.mix_spin):
            w.valueChanged.connect(self._apply)
        self.loop_check.toggled.connect(self._apply)
        self.physics_check.toggled.connect(self._apply)
        main_state.document_changed.connect(self.refresh)
        main_state.time_changed.connect(self._follow_editor)
        self.refresh()

    # --- choices ---------------------------------------------------------------------------
    def set_document(self, doc: Document) -> None:
        self.stop()
        self.state.set_document(doc)
        self.state.mode = "animate"
        self.refresh()

    def _show_bones(self, on: bool) -> None:
        self.viewport.show_bones = on
        self.viewport.update()

    def _fill(self, combo: QComboBox, items: list[str], keep_first: str | None = None) -> None:
        current = combo.currentText()
        if [combo.itemText(i) for i in range(combo.count())] == items:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)  # Qt selects the first item here; keep the old choice if it still exists
        combo.setCurrentIndex(items.index(current) if current in items else (0 if keep_first else -1))
        combo.blockSignals(False)

    def refresh(self) -> None:
        doc = self.state.doc
        names = doc.animation_names()
        self._fill(self.from_combo, [FOLLOW, *names], FOLLOW)
        self._fill(self.to_combo, names)
        if self.to_combo.currentIndex() < 0 and names:
            self.to_combo.setCurrentIndex(min(1, len(names) - 1))
            self._default_switch()
        self._fill(self.skin_combo, [EDIT_SKIN, *doc.skin_names()], EDIT_SKIN)
        self._fill(self.layer_combo, ["(none)", *names], "(none)")
        self._apply()

    def playing_animation(self) -> str | None:
        choice = self.from_combo.currentText()
        if choice == FOLLOW or not choice:
            return self.main_state.animation
        return choice

    def _default_switch(self) -> None:
        name = self.playing_animation()
        if name in self.state.doc.data["animations"]:
            self.switch_spin.blockSignals(True)
            self.switch_spin.setValue(self.state.doc.animation_duration(name))
            self.switch_spin.blockSignals(False)

    def _mix_toggled(self, on: bool) -> None:
        if on:
            self._default_switch()
        self._apply()

    def _apply(self) -> None:
        st = self.state
        st.from_animation = self.playing_animation()
        st.to_animation = (self.to_combo.currentText() or None) if self.mix_group.isChecked() else None
        layer = self.layer_combo.currentText()
        st.layer = layer if layer and layer != "(none)" else None
        st.animation = st.from_animation
        st.switch = self.switch_spin.value()
        st.mix_duration = self.mix_spin.value()
        st.loop = self.loop_check.isChecked()
        st.physics = self.physics_check.isChecked()
        skin = self.skin_combo.currentText()
        st.skin = None if skin in ("", EDIT_SKIN) else skin
        if st.time > st.total_duration:
            st.set_time(0)
        self._show_time()
        self.viewport.update()

    def _follow_editor(self) -> None:
        """Following the edited animation: switching animations in the editor switches the preview."""
        if self.from_combo.currentText() == FOLLOW and self.state.from_animation != self.main_state.animation:
            self._apply()

    # --- playback --------------------------------------------------------------------------
    def set_time(self, t: float) -> None:
        self.state.set_time(t)
        total = self.state.total_duration
        self.slider.blockSignals(True)
        self.slider.setValue(round(1000 * min(1.0, t / total)) if total > 0 else 0)
        self.slider.blockSignals(False)
        self._show_time()

    def _show_time(self) -> None:
        st = self.state
        ev = st.evaluator()
        if isinstance(ev, MixEvaluator):
            _, tb, mix = ev.phase(st.time)
            phase = f"{st.from_animation}" if tb is None else \
                f"mixing {mix * 100:.0f}% into {st.to_animation}" if mix < 1 else f"{st.to_animation}"
        else:
            phase = st.from_animation or "no animation"
        self.time_label.setText(f"{st.time:.3f}s / {st.total_duration:.3f}s · {phase}")

    def _slider_moved(self, value: int) -> None:
        self.stop()
        self.set_time(value / 1000 * self.state.total_duration)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_framed", False):  # frame the skeleton the first time it opens
            self._framed = True
            self.viewport.frame_all()
        if self._autoplay and not self._timer.isActive() and self.state.total_duration > 0:
            self.toggle_play()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()  # no drawing while hidden; resumes when shown
            self._autoplay = True

    def toggle_play(self) -> None:
        if self._timer.isActive():
            self.stop()
            return
        if self.state.time >= self.state.total_duration:
            self.set_time(0)
        self._start = self.state.time
        self._heard = self.state.time - 1e-6
        self._clock.restart()
        self._timer.start()
        self._autoplay = True
        self.play_button.setText("⏸ Pause")

    def stop(self) -> None:
        self._timer.stop()
        self._autoplay = False
        self.play_button.setText("▶ Play")

    def _tick(self) -> None:
        t = self._start + self._clock.elapsed() / 1000 * self.speed_spin.value()
        total = self.state.total_duration
        if total <= 0:
            return
        if t >= total:
            if self.loop_check.isChecked():
                t %= total
                self._start, t = t, t
                self._clock.restart()
            else:
                t = total
                self.stop()
        st = self.state
        self.audio.doc, self.audio.enabled = st.doc, self.sound_check.isChecked()
        # sounds of the animation playing at t: after a crossfade switch, the "to" animation from its start
        if st.to_animation and t >= st.switch:
            before = self._heard - st.switch if self._heard >= st.switch else -1e-6
            self.audio.advance(st.to_animation, before, t - st.switch)
        else:
            self.audio.advance(st.from_animation, self._heard, t)
        self._heard = t
        self.set_time(t)
