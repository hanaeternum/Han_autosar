"""网络管理控制区。

分两层，避免"接口名"和"整车事件"混在一起让人误解：

- 整车电源（KL15 上电 / 下电）：演示时的主控制。一次点击 = 一个整车事件，
  对应 CanNm_NetworkRequest() / CanNm_NetworkRelease()，保持事件是 AC_0_KL15。
- 接口级控制：主动唤醒（其他唤醒源）、释放全部需求、RMR、诊断、自定义报文。
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# 除 KL15 外的唤醒源（KL15 由上方的电源开关负责，不在这里重复）
WAKEUP_SOURCES = [
    ("外设 I/O 唤醒", "IO", 3),
    ("功能唤醒", "FUNC", 128),
]

KL15_SYNC_GRACE_S = 0.3   # 用户点击后多久内不回读开关状态，避免与引擎快照打架


class ControlPanel(QGroupBox):
    networkRequest = pyqtSignal(str, int)      # source, wakeup_reason
    networkRelease = pyqtSignal()
    kl15Changed = pyqtSignal(bool)             # True = KL15 上电
    repeatMessageRequest = pyqtSignal()
    diagRequest = pyqtSignal()
    customFrameRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("网络管理控制", parent)
        self._actions_enabled = False
        self._has_demand = False
        self._kl15_on = False
        self._kl15_sync_block_until = 0.0

        # ---------------- 整车电源：KL15 ----------------
        self.kl15_btn = QPushButton("KL15 上电（Network Request）")
        self.kl15_btn.setCheckable(True)
        self.kl15_btn.setObjectName("primary")
        self.kl15_btn.setToolTip(
            "模拟整车 15 电上/下电：\n"
            "• 上电 = CanNm_NetworkRequest()，置位网络保持事件 AC_0_KL15，唤醒原因 = 2（KL15 上电）\n"
            "• 下电 = CanNm_NetworkRelease('KL15')，清除该保持事件，节点才可能睡下去\n\n"
            "上电后节点会一直停在 NOS 周期发送 —— 这是规范语义，不是卡住。"
        )
        self.kl15_btn.toggled.connect(self._on_kl15_clicked)

        self.kl15_state = QLabel("AC_0_KL15 = 0　整车 15 电未上电")
        self.kl15_state.setWordWrap(True)

        power_box = QVBoxLayout()
        power_box.setSpacing(6)
        power_box.addWidget(QLabel("整车电源（演示主流程）"))
        power_box.addWidget(self.kl15_btn)
        power_box.addWidget(self.kl15_state)

        # ---------------- 接口级控制 ----------------
        self.source_combo = QComboBox()
        for label, source, reason in WAKEUP_SOURCES:
            self.source_combo.addItem(label, (source, reason))

        self.wake_btn = QPushButton("主动唤醒（Network Request）")
        self.wake_btn.setToolTip(
            "对应 CanNm_NetworkRequest()，使用上方选定的唤醒源（KL15 请用电源开关）。\n"
            "注意这不是一次性脉冲 —— 保持事件只要还在，节点就会一直停在 NOS 周期发送。"
        )
        self.release_btn = QPushButton("释放全部需求（Release Network）")
        self.release_btn.setToolTip(
            "对应 CanNm_NetworkRelease()：清除本节点的全部网络保持事件。\n"
            "清除后节点才可能离开 NOS —— 若在 RMS 中，需等 T_REPEAT_MESSAGE 超时后才进入 Ready Sleep。"
        )
        self.rmr_btn = QPushButton("重复报文请求（RMR）")
        self.diag_btn = QPushButton("模拟诊断请求")
        self.custom_btn = QPushButton("发送自定义 NM 报文…")

        self.wake_btn.clicked.connect(
            lambda: self.networkRequest.emit(*self.source_combo.currentData())
        )
        self.release_btn.clicked.connect(self.networkRelease.emit)
        self.rmr_btn.clicked.connect(self.repeatMessageRequest.emit)
        self.diag_btn.clicked.connect(self.diagRequest.emit)
        self.custom_btn.clicked.connect(self.customFrameRequested.emit)

        self.demand_label = QLabel("当前网络需求：无")
        self.demand_label.setWordWrap(True)

        api_label = QLabel("接口级控制（其它唤醒源 / RMR / 诊断 / 报文注入）")
        api_label.setObjectName("sectionTitle")

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(self.source_combo, 0, 0)
        grid.addWidget(self.wake_btn, 1, 0)
        grid.addWidget(self.demand_label, 2, 0)
        grid.addWidget(self.release_btn, 3, 0)
        grid.addWidget(self.rmr_btn, 4, 0)
        grid.addWidget(self.diag_btn, 5, 0)
        grid.addWidget(self.custom_btn, 6, 0)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #2A2F39;")

        hint = QLabel(
            "KL15 上电后节点会一直停在 NOS 周期发送，想让它睡下去请点 KL15 下电；\n"
            "之后约 7 秒走完 RSS → PBS → BSM（1× 速度）。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addLayout(power_box)
        root.addWidget(separator)
        root.addWidget(api_label)
        root.addLayout(grid)
        root.addWidget(hint)
        self.set_enabled_actions(False)
        self.set_demand([])

    # ------------------------------------------------------------------
    def set_enabled_actions(self, enabled: bool) -> None:
        self._actions_enabled = enabled
        for widget in (self.wake_btn, self.rmr_btn, self.diag_btn, self.custom_btn):
            widget.setEnabled(enabled)
        self.source_combo.setEnabled(enabled)
        self.kl15_btn.setEnabled(enabled)
        self.release_btn.setEnabled(enabled and self._has_demand)
        if not enabled:
            # 断开时把电源开关复位到"下电"，避免下次连接显示成还在上电状态
            self._apply_kl15(False, force=True)

    def set_demand(self, sources: list[str]) -> None:
        """由主窗口按内核快照回调：把"当前是否还有网络需求"直接摆在按钮旁边。

        这是本项目最容易被误解的一点：KL15 是"网络保持事件"而不是一次性脉冲，
        只要它还在，节点就会一直停在 NOS。
        """
        self._has_demand = bool(sources)
        text = " + ".join(sources) if sources else "无"
        self.demand_label.setText(f"当前网络需求：{text}")
        self.demand_label.setStyleSheet(
            "color: #F59E0B; font-weight: 500;" if sources else "color: #6B7280;"
        )
        self.release_btn.setEnabled(self._actions_enabled and self._has_demand)
        self._apply_kl15("KL15" in sources)

    # ------------------------------------------------------------------
    def _apply_kl15(self, on: bool, force: bool = False) -> None:
        if not force and time.monotonic() < self._kl15_sync_block_until:
            return
        self._kl15_on = on
        if self.kl15_btn.isChecked() != on:
            self.kl15_btn.blockSignals(True)
            self.kl15_btn.setChecked(on)
            self.kl15_btn.blockSignals(False)
        self.kl15_btn.setText("KL15 下电（Network Release）" if on else "KL15 上电（Network Request）")
        self.kl15_state.setText(
            "AC_0_KL15 = 1　整车 15 电已上电，网络保持唤醒"
            if on else "AC_0_KL15 = 0　整车 15 电未上电"
        )
        self.kl15_state.setStyleSheet("color: #F59E0B; font-weight: 500;" if on else "color: #6B7280;")

    def _on_kl15_clicked(self, checked: bool) -> None:
        self._kl15_sync_block_until = time.monotonic() + KL15_SYNC_GRACE_S
        self._apply_kl15(checked, force=True)
        self.kl15Changed.emit(checked)
