"""A number with a bar, like Spine's sliders: the value on the left, a draggable bar on the right.

Click or drag on the bar to set the value; drag sideways on the number to nudge it; double-click
the number to type one; the mouse wheel and arrow keys step it (Shift: steps of 10).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QWidget

NUMBER = 0.45  # share of the width for the number


class ValueBar(QWidget):
    valueChanged = Signal(int)

    def __init__(self, low: int, high: int, value: int = 0, parent=None):
        super().__init__(parent)
        self.low, self.high = low, high
        self._value = max(low, min(high, int(value)))
        self._drag: tuple[str, float, int] | None = None  # ("bar" | "number", start x, start value)
        self._editor: QLineEdit | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(120, 28)
        self.setToolTip("Drag the bar, drag the number sideways, double-click to type, or use the mouse wheel")
        self.spin = self  # the old spin-box API: panel.strength.spin.setValue(30)

    # --- value ------------------------------------------------------------------------------
    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 (Qt naming)
        value = max(self.low, min(self.high, int(round(value))))
        if value != self._value:
            self._value = value
            self.update()
            self.valueChanged.emit(value)

    def set(self, value: int) -> None:
        """Show a value without emitting (the panel refreshing from the state)."""
        self._value = max(self.low, min(self.high, int(round(value))))
        self.update()

    # --- geometry ---------------------------------------------------------------------------
    def _bar(self) -> QRectF:
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        return QRectF(r.left() + r.width() * NUMBER, r.top(), r.width() * (1 - NUMBER), r.height())

    def _value_at(self, x: float) -> int:
        bar = self._bar()
        f = (x - bar.left()) / max(1.0, bar.width())
        return round(self.low + max(0.0, min(1.0, f)) * (self.high - self.low))

    # --- drawing ----------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = pal.base().color()
        p.setPen(QPen(pal.mid().color(), 1))
        p.setBrush(base.darker(115))
        p.drawRoundedRect(r, 4, 4)
        # number area, a little lighter
        num = QRectF(r.left(), r.top(), r.width() * NUMBER, r.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base.lighter(118) if base.lightness() < 128 else base.darker(104))
        p.drawRoundedRect(num.adjusted(1, 1, 0, -1), 3, 3)
        # filled part of the bar and its handle
        bar = self._bar().adjusted(0, 1, -1, -1)
        f = (self._value - self.low) / max(1, self.high - self.low)
        fill = QRectF(bar.left(), bar.top(), bar.width() * f, bar.height())
        enabled = self.isEnabled()
        p.setBrush(QColor("#7b8f8d") if enabled else QColor("#5b5f60"))
        p.drawRect(fill)
        p.setBrush(QColor("#e8fbff") if enabled else QColor("#8a8f90"))
        p.drawRect(QRectF(fill.right() - 2, bar.top(), 3, bar.height()))
        # the number
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.1)
        p.setFont(font)
        p.setPen(pal.text().color() if enabled else pal.placeholderText().color())
        p.drawText(num.adjusted(4, 0, -8, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   str(self._value))
        if self.hasFocus():
            p.setPen(QPen(pal.highlight().color(), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 4, 4)
        p.end()

    # --- input ------------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        if x >= self._bar().left():
            self._drag = ("bar", x, self._value)
            self.setValue(self._value_at(x))
        else:
            self._drag = ("number", x, self._value)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        kind, x0, v0 = self._drag
        x = event.position().x()
        if kind == "bar":
            self.setValue(self._value_at(x))
        else:  # nudge: the full range over about twice the widget's width
            per_px = (self.high - self.low) / max(60.0, self.width() * 2)
            self.setValue(v0 + (x - x0) * per_px)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:
        if event.position().x() < self._bar().left():
            self._edit()

    def wheelEvent(self, event) -> None:
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        self.setValue(self._value + (step if event.angleDelta().y() > 0 else -step))
        event.accept()

    def keyPressEvent(self, event) -> None:
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.setValue(self._value + step)
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.setValue(self._value - step)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self._edit()
        else:
            super().keyPressEvent(event)

    def _edit(self) -> None:
        """Type a value over the number."""
        editor = QLineEdit(str(self._value), self)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        editor.setGeometry(0, 0, int(self.width() * NUMBER), self.height())
        editor.selectAll()

        def done() -> None:
            try:
                self.setValue(float(editor.text()))
            except ValueError:
                pass
            editor.deleteLater()
            self._editor = None
            self.setFocus()

        editor.editingFinished.connect(done)
        editor.show()
        editor.setFocus()
        self._editor = editor
