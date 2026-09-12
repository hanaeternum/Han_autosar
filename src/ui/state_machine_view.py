"""CanNm 生命周期流程图：5 个状态 + 迁移箭头，高亮当前所处状态。

纵向排布是因为面板窄而高，比横排更省地方，也正好对应"网络模式内三个子状态 → 睡眠模式"的层次：
    Network Mode 容器里是 RMS → NOS → RSS，出来之后 RSS → PBS → BSM，
    左侧回流箭头代表唤醒事件（本地唤醒 / 收到 NM 报文）把节点重新拉回 RMS。
当前状态用状态色高亮，并在框内实时显示该状态的定时器倒计时。
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QWidget

from ..core.nm_state import NmState
from .theme import STATE_COLORS, TEXT, TEXT_DIM, TEXT_FAINT

BOX_H = 36
GAP = 12
RETURN_X = 22.0
BOX_X = 44.0

# 三个网络模式子状态在容器内，PBS/BSM 在容器外
LAYOUT: dict[NmState, tuple[float, str]] = {
    NmState.REPEAT_MESSAGE: (26.0, "RMS 重复报文"),
    NmState.NORMAL_OPERATION: (78.0, "NOS 常规运行"),
    NmState.READY_SLEEP: (130.0, "RSS 预睡眠"),
    NmState.PREPARE_BUS_SLEEP: (194.0, "PBS 准备睡眠"),
    NmState.BUS_SLEEP: (246.0, "BSM 总线睡眠"),
}

ARROWS = [
    (NmState.REPEAT_MESSAGE, NmState.NORMAL_OPERATION, "T_REPEAT 超时"),
    (NmState.NORMAL_OPERATION, NmState.READY_SLEEP, "需求解除"),
    (NmState.READY_SLEEP, NmState.PREPARE_BUS_SLEEP, "T_NMTimeout 超时"),
    (NmState.PREPARE_BUS_SLEEP, NmState.BUS_SLEEP, "T_WAIT_BUS 超时"),
]

# 每个状态"负责"的定时器：非当前状态显示定时器名，当前状态显示倒计时
STATE_TIMER = {
    NmState.REPEAT_MESSAGE: ("repeat_message", "T_REPEAT_MESSAGE"),
    NmState.NORMAL_OPERATION: ("nm_timeout", "T_NMTimeout"),
    NmState.READY_SLEEP: ("nm_timeout", "T_NMTimeout"),
    NmState.PREPARE_BUS_SLEEP: ("wait_bus_sleep", "T_WAIT_BUS_SLEEP"),
}


class StateMachineView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._snap: Optional[dict[str, Any]] = None
        self._prev: Optional[NmState] = None
        self.setMinimumSize(250, 356)
        self._font_cache: dict[tuple[int, int], QFont] = {}
        self.setToolTip(
            "网络模式内三个子状态的迁移条件与规范条款见 docs/状态机说明.md；\n"
            "另有 RMS→RSS（T_REPEAT 超时且无需求）、RSS→NOS（本地唤醒）、\n"
            "收到 RMR=1 报文回到 RMS 三条旁路，为保持图面简洁未画出。"
        )

    # ==================================================================
    def set_snapshot(self, snap: Optional[dict[str, Any]]) -> None:
        if snap is not None:
            state = NmState(snap["state"])
            if self._snap is not None and NmState(self._snap["state"]) is not state:
                self._prev = NmState(self._snap["state"])
            self._snap = snap
        else:
            self._snap = None
            self._prev = None
        self.update()

    @property
    def current_state_short(self) -> str:
        """当前高亮的状态简称（供测试与外部读取）。"""
        return self._snap["state_short"] if self._snap else "—"

    # ==================================================================
    def _font(self, size: int, weight: int = 400) -> QFont:
        key = (size, weight)
        if key not in self._font_cache:
            font = QFont()
            font.setPointSizeF(size)
            font.setWeight(weight)
            self._font_cache[key] = font
        return self._font_cache[key]

    def _box_width(self) -> float:
        return max(140.0, min(196.0, self.width() - 92.0))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        box_w = self._box_width()
        current = NmState(self._snap["state"]) if self._snap else None

        # ---------------- 网络模式容器 ----------------
        container = QRectF(BOX_X - 10, 4, box_w + 20, self._container_height)
        painter.setPen(QPen(QColor("#39414F"), 1.2))
        painter.setBrush(QColor("#171A20"))
        painter.drawRoundedRect(container, 12, 12)
        painter.setPen(QColor(TEXT_DIM))
        painter.setFont(self._font(8, 500))
        painter.drawText(QRectF(container.x() + 10, container.y() + 5, container.width() - 20, 16),
                         int(Qt.AlignLeft | Qt.AlignVCenter), "Network Mode　网络模式")

        # ---------------- 状态框 ----------------
        for state, (y, caption) in LAYOUT.items():
            rect = QRectF(BOX_X, y, box_w, BOX_H)
            color = QColor(STATE_COLORS[state])
            active = state is current

            if active:
                fill = QColor(color)
                fill.setAlpha(60)
                painter.setBrush(fill)
                painter.setPen(QPen(color, 2))
            else:
                painter.setBrush(QColor("#1B1E24"))
                painter.setPen(QPen(QColor("#39414F"), 1.2))
            painter.drawRoundedRect(rect, 8, 8)

            painter.setPen(QColor(TEXT) if active else QColor(TEXT_DIM))
            painter.setFont(self._font(9, 500 if active else 400))
            painter.drawText(QRectF(rect.x() + 10, rect.y() + 3, rect.width() - 20, 17),
                             int(Qt.AlignLeft | Qt.AlignVCenter), caption)

            second = self._second_line(state, active)
            if second:
                painter.setPen(QColor(color) if active else QColor(TEXT_FAINT))
                painter.setFont(self._font(8))
                painter.drawText(QRectF(rect.x() + 10, rect.y() + 19, rect.width() - 20, 15),
                                 int(Qt.AlignLeft | Qt.AlignVCenter), second)

        # ---------------- 迁移箭头 ----------------
        for src, dst, label in ARROWS:
            self._draw_arrow(painter, src, dst, label, box_w, current)
        self._draw_return_arrow(painter, box_w)

        # ---------------- 图例 ----------------
        painter.setPen(QColor(TEXT_FAINT))
        painter.setFont(self._font(7))
        painter.drawText(QRectF(BOX_X - 10, self.height() - 34, self.width() - 30, 15),
                         int(Qt.AlignLeft | Qt.AlignVCenter),
                         "高亮框 = 本机当前状态；框内为对应定时器倒计时")
        painter.drawText(QRectF(BOX_X - 10, self.height() - 19, self.width() - 30, 15),
                         int(Qt.AlignLeft | Qt.AlignVCenter),
                         "旁路未画：RMS→RSS、RSS→NOS、RMR 回 RMS（见说明文档）")

    # ------------------------------------------------------------------
    def _second_line(self, state: NmState, active: bool) -> str:
        if state in STATE_TIMER:
            key, label = STATE_TIMER[state]
            info = self._snap["timers"].get(key, {}) if self._snap else {}
            if active and info.get("running"):
                return f"剩余 {info['remaining']}ms"
            return label
        return "等待唤醒事件"

    @property
    def _container_height(self) -> float:
        last_y, _ = LAYOUT[NmState.READY_SLEEP]
        return last_y + BOX_H + 10 - 4

    def _draw_arrow(self, painter: QPainter, src: NmState, dst: NmState, label: str,
                    box_w: float, current: Optional[NmState]) -> None:
        src_y, _ = LAYOUT[src]
        dst_y, _ = LAYOUT[dst]
        cx = BOX_X + box_w / 2
        y0 = src_y + BOX_H
        y1 = dst_y
        highlight = current is dst and self._prev is src
        color = QColor("#F59E0B") if highlight else QColor("#6B7280")

        painter.setPen(QPen(color, 2.2 if highlight else 1.4))
        painter.drawLine(QPointF(cx, y0), QPointF(cx, y1 - 5))
        self._arrow_head(painter, QPointF(cx, y1), color)

        painter.setPen(color if highlight else QColor(TEXT_FAINT))
        painter.setFont(self._font(7, 500 if highlight else 400))
        painter.drawText(QRectF(cx + 8, (y0 + y1) / 2 - 8, max(40.0, self.width() - cx - 12), 16),
                         int(Qt.AlignLeft | Qt.AlignVCenter), label)

    def _draw_return_arrow(self, painter: QPainter, box_w: float) -> None:
        rms_y, _ = LAYOUT[NmState.REPEAT_MESSAGE]
        bsm_y, _ = LAYOUT[NmState.BUS_SLEEP]
        highlight = self._prev in (NmState.BUS_SLEEP, NmState.PREPARE_BUS_SLEEP) and \
            self._snap is not None and NmState(self._snap["state"]) is NmState.REPEAT_MESSAGE
        color = QColor("#F59E0B") if highlight else QColor("#6B7280")

        y_from = bsm_y + BOX_H / 2
        y_to = rms_y + BOX_H / 2
        painter.setPen(QPen(color, 2.2 if highlight else 1.4))
        painter.drawLine(QPointF(BOX_X + 2, y_from), QPointF(RETURN_X, y_from))
        painter.drawLine(QPointF(RETURN_X, y_from), QPointF(RETURN_X, y_to))
        painter.drawLine(QPointF(RETURN_X, y_to), QPointF(BOX_X - 6, y_to))
        self._arrow_head(painter, QPointF(BOX_X, y_to), color, direction="right")

    @staticmethod
    def _arrow_head(painter: QPainter, tip: QPointF, color: QColor, direction: str = "down") -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        if direction == "right":
            head = QPolygonF([
                QPointF(tip.x(), tip.y()),
                QPointF(tip.x() - 5, tip.y() - 4.5),
                QPointF(tip.x() - 5, tip.y() + 4.5),
            ])
        else:
            head = QPolygonF([
                QPointF(tip.x(), tip.y()),
                QPointF(tip.x() - 4.5, tip.y() - 5),
                QPointF(tip.x() + 4.5, tip.y() - 5),
            ])
        painter.drawPolygon(head)
