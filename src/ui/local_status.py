"""本地节点状态区：大号状态指示灯 + 四个定时器倒计时 + 标志位。

这是演示时观众看得最多的一块，所以定时器做成进度条 ——
"T_NMTimeout 还剩 1.2s 就进 PBS"这类信息一眼就能看出来。
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..core.nm_pdu import NM_STATE_CODE_LABELS
from ..core.nm_state import NmState
from .theme import STATE_COLORS, TEXT_DIM, TEXT_FAINT
from .widgets.state_led import StateLed

TIMERS = [
    ("repeat_message", "T_REPEAT_MESSAGE", "RMS 持续时间"),
    ("nm_timeout", "T_NMTimeout", "总线静默超时"),
    ("wait_bus_sleep", "T_WAIT_BUS_SLEEP", "PBS 持续时间"),
    ("wait_diag_req", "T_WAIT_DiagReq", "诊断保持"),
]


class LocalStatusPanel(QGroupBox):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("本地节点状态", parent)
        self._bars: dict[str, QProgressBar] = {}
        self._bar_labels: dict[str, QLabel] = {}

        self.led = StateLed(26)
        self.state_short = QLabel("BSM")
        self.state_short.setStyleSheet("font-size: 20px; font-weight: 500;")
        self.state_label = QLabel("总线睡眠模式")
        self.state_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.node_line = QLabel("—")
        self.node_line.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px;")

        state_box = QVBoxLayout()
        state_box.setSpacing(0)
        state_box.addWidget(self.state_short)
        state_box.addWidget(self.state_label)
        state_box.addWidget(self.node_line)

        state_row = QHBoxLayout()
        state_row.setSpacing(10)
        state_row.addWidget(self.led)
        state_row.addLayout(state_box)
        state_row.addStretch(1)

        timer_grid = QGridLayout()
        timer_grid.setHorizontalSpacing(8)
        timer_grid.setVerticalSpacing(4)
        for row, (key, name, desc) in enumerate(TIMERS):
            label = QLabel(f"{name}")
            label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
            label.setToolTip(desc)
            value = QLabel("—")
            value.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px; font-family: Consolas;")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(
                "QProgressBar { background-color: #111318; border: none; border-radius: 3px; }"
                "QProgressBar::chunk { background-color: #3B82F6; border-radius: 3px; }"
            )
            timer_grid.addWidget(label, row, 0)
            timer_grid.addWidget(bar, row, 1)
            timer_grid.addWidget(value, row, 2)
            timer_grid.setColumnStretch(1, 1)
            self._bars[key] = bar
            self._bar_labels[key] = value

        self.flags = QLabel("RMR=0 AWB=0 需求=无")
        self.flags.setStyleSheet("font-family: Consolas; font-size: 11px;")
        self.counters = QLabel("TX 0 / RX 0  远端节点 0")
        self.counters.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addLayout(state_row)
        root.addLayout(timer_grid)
        root.addWidget(self.flags)
        root.addWidget(self.counters)
        self.set_idle()

    # ------------------------------------------------------------------
    def set_idle(self) -> None:
        self.led.set_state(None)
        self.state_short.setText("未连接")
        self.state_label.setText("请先连接仿真通道")
        self.node_line.setText("—")
        for bar in self._bars.values():
            bar.setValue(0)
        for label in self._bar_labels.values():
            label.setText("—")

    def update_from_snapshot(self, snap: dict[str, Any]) -> None:
        state = NmState(snap["state"])
        self.led.set_state(state, blink=(state is NmState.REPEAT_MESSAGE))
        self.state_short.setText(snap["state_short"])
        self.state_short.setStyleSheet(
            f"font-size: 20px; font-weight: 500; color: {STATE_COLORS[state]};"
        )
        code = snap.get("state_code") or 0
        self.state_label.setText(
            f"{snap['state_label']}｜Byte2 状态码 {NM_STATE_CODE_LABELS.get(code, '-')}"
        )
        self.node_line.setText(
            f"{snap['ecu']}　CAN ID {snap['can_id_hex']}　通道 {snap['channel']}"
        )

        for key, _, _ in TIMERS:
            info = snap["timers"].get(key, {})
            bar = self._bars[key]
            duration = max(1, info.get("duration", 1))
            remaining = info.get("remaining", 0) if info.get("running") else 0
            bar.setRange(0, duration)
            bar.setValue(remaining)
            bar.setStyleSheet(
                "QProgressBar { background-color: #111318; border: none; border-radius: 3px; }"
                "QProgressBar::chunk { border-radius: 3px; background-color: "
                + ("#F59E0B" if info.get("running") else "#2A2F39")
                + "; }"
            )
            self._bar_labels[key].setText(
                f"{remaining}ms" if info.get("running") else "停止"
            )

        keep = "+".join(snap["keep_awake"]) if snap["keep_awake"] else "无"
        self.flags.setText(
            f"RMR={int(snap['rmr'])}  AWB={int(snap['awb'])}  "
            f"唤醒原因={snap['wakeup_reason']}  需求={keep}"
        )
        self.counters.setText(
            f"TX {snap.get('nm_tx_total', 0)} / RX {snap.get('nm_rx_count', 0)}　"
            f"远端节点 {len(snap['remote_nodes'])}（"
            + (", ".join(f"0x{k:02X}" for k in sorted(snap["remote_nodes"])) or "无")
            + "）"
        )
