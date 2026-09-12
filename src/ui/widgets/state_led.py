"""状态指示灯控件。

不引图片资源，直接自绘：外圈暗环 + 内圈实心圆 + 中心高光。
RMS（快速发送）状态会闪烁，提示"正在快速唤醒网络"。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QRadialGradient
from PyQt5.QtWidgets import QWidget

from ...core.nm_state import NmState
from ..theme import STATE_COLORS


class StateLed(QWidget):
    def __init__(self, diameter: int = 18, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._color = QColor(STATE_COLORS[NmState.BUS_SLEEP])
        self._blink = False
        self._blink_on = True
        self.setFixedSize(diameter + 8, diameter + 8)

        self._timer = QTimer(self)
        self._timer.setInterval(420)
        self._timer.timeout.connect(self._toggle)
        self._timer.start()

    def set_state(self, state: Optional[NmState], blink: bool = False) -> None:
        color = STATE_COLORS.get(state, QColor("#3A4150")) if state else "#3A4150"
        self._color = QColor(color)
        self._blink = blink
        if not blink:
            self._blink_on = True
        self.update()

    def _toggle(self) -> None:
        if self._blink:
            self._blink_on = not self._blink_on
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        rect = QRectF((self.width() - side) / 2.0 + 3.0,
                      (self.height() - side) / 2.0 + 3.0,
                      side - 6.0, side - 6.0)

        # 外圈暗环
        ring = QColor(self._color)
        ring.setAlpha(70 if self._blink_on else 30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(ring)
        painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))

        # 内圈实心
        fill = QColor(self._color)
        if not self._blink_on:
            fill.setAlpha(70)
        gradient = QRadialGradient(rect.center(), rect.width() / 1.4)
        gradient.setColorAt(0.0, fill.lighter(135))
        gradient.setColorAt(1.0, fill)
        painter.setBrush(gradient)
        painter.drawEllipse(rect)
        painter.end()
