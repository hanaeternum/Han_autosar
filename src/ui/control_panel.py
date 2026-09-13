"""网络管理控制区。

分两层，避免"接口名"和"整车事件"混在一起让人误解：

- 整车电源（KL15 上电 / 下电）：演示时的主控制。一次点击 = 一个整车事件，
  对应 CanNm_NetworkRequest() / CanNm_NetworkRelease()，保持事件是 AC_0_KL15。
- 接口级控制：主动唤醒（其他唤醒源）、关闭该唤醒源、释放全部需求、RMR、诊断、自定义报文。

"唤醒 / 关闭"按唤醒源成对提供：下拉选哪个源，下面的按钮就开/关哪个源，
和整车电源 KL15 的上电/下电是一个语义 —— 保持事件置位与清除。
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
# (下拉显示名, 内核里的 source 标识, Byte3 唤醒原因)
WAKEUP_SOURCES = [
    ("外设 I/O 唤醒", "IO", 3),
    ("功能唤醒", "FUNC", 128),
]

KL15_SYNC_GRACE_S = 0.3   # 用户点击后多久内不回读开关状态，避免与引擎快照打架


class ControlPanel(QGroupBox):
    networkRequest = pyqtSignal(str, int)      # source, wakeup_reason
    networkRelease = pyqtSignal()              # 清空全部保持事件
    sourceRelease = pyqtSignal(str)            # 只清除指定唤醒源的保持事件
    kl15Changed = pyqtSignal(bool)             # True = KL15 上电
    repeatMessageRequest = pyqtSignal()
    diagRequest = pyqtSignal()
    customFrameRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("网络管理控制", parent)
        self._actions_enabled = False
        self._has_demand = False
        self._demand: set[str] = set()
        self._diag_allowed = True
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

        # 按钮文字只留短名（面板窄，竖排两列放不下长文案），
        # 完整接口名与语义全部放 tooltip。
        self.wake_btn = QPushButton("主动唤醒")
        self.wake_btn.setToolTip(
            "CanNm_NetworkRequest() —— 使用上方选定的唤醒源（KL15 请用电源开关）。\n"
            "注意这不是一次性脉冲：保持事件只要还在，节点就会一直停在 NOS 周期发送。"
        )
        # 与"主动唤醒"成对的关闭：只清除下拉选中那一路保持事件，
        # 不影响 KL15 / 诊断保持等其它来源（释放全部需求才会一起清掉）。
        self.source_release_btn = QPushButton("关闭 IO")
        self.source_release_btn.setToolTip(
            "CanNm_NetworkRelease(source) —— 只清除下拉选中唤醒源的保持事件，\n"
            "KL15、诊断保持等其它需求不受影响。\n\n"
            "按钮文字跟随上方下拉变化；仅当该唤醒源当前确实持有网络需求时可用。"
        )
        self.release_btn = QPushButton("释放全部需求")
        self.release_btn.setToolTip(
            "CanNm_NetworkRelease() —— 清除本节点的全部网络保持事件。\n"
            "清除后节点才可能离开 NOS；若在 RMS 中，需等 T_REPEAT_MESSAGE 超时后才进入 Ready Sleep。"
        )
        self.rmr_btn = QPushButton("重复报文(RMR)")
        self.rmr_btn.setToolTip("CanNm_RepeatMessageRequest()：把节点拉回 RMS，报文 RMR 位置 1 并启用快速发送。")
        self.diag_btn = QPushButton("诊断请求")
        self.diag_btn.setToolTip(
            "CanNm_DiagRequest() —— 仅在 NOS / RSS 下有效（规范 4.3.3.3）。\n"
            "BSM / PBS 不接收应用报文（规范表 4），诊断报文无法唤醒网络；\n"
            "真实车辆里诊断仪靠唤醒收发器间接让 ECU 上电，等价于先点一次主动唤醒。"
        )
        self.custom_btn = QPushButton("自定义报文…")
        self.custom_btn.setToolTip("打开对话框，手工构造并注入一帧 NM 报文。")

        self.wake_btn.clicked.connect(
            lambda: self.networkRequest.emit(*self.source_combo.currentData())
        )
        self.source_release_btn.clicked.connect(
            lambda: self.sourceRelease.emit(self.source_combo.currentData()[0])
        )
        self.release_btn.clicked.connect(self.networkRelease.emit)
        self.rmr_btn.clicked.connect(self.repeatMessageRequest.emit)
        self.diag_btn.clicked.connect(self.diagRequest.emit)
        self.custom_btn.clicked.connect(self.customFrameRequested.emit)
        self.source_combo.currentIndexChanged.connect(self._refresh_source_release)

        self.demand_label = QLabel("当前网络需求：无")
        self.demand_label.setWordWrap(True)

        api_label = QLabel("接口级控制（其它唤醒源 / RMR / 诊断 / 报文注入）")
        api_label.setObjectName("sectionTitle")

        # 两列布局：唤醒/关闭成对一行，同级别的操作并排，8 行压成 4 行。
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self.source_combo, 0, 0, 1, 2)
        grid.addWidget(self.wake_btn, 1, 0)
        grid.addWidget(self.source_release_btn, 1, 1)
        grid.addWidget(self.demand_label, 2, 0, 1, 2)
        grid.addWidget(self.release_btn, 3, 0)
        grid.addWidget(self.rmr_btn, 3, 1)
        grid.addWidget(self.diag_btn, 4, 0)
        grid.addWidget(self.custom_btn, 4, 1)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #2A2F39;")

        hint = QLabel("KL15 上电后节点停在 NOS 周期发送，点 KL15 下电后约 7 秒走完 RSS → PBS → BSM。")
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
        for widget in (self.wake_btn, self.rmr_btn, self.custom_btn):
            widget.setEnabled(enabled)
        self.source_combo.setEnabled(enabled)
        self.kl15_btn.setEnabled(enabled)
        self.release_btn.setEnabled(enabled and self._has_demand)
        self.diag_btn.setEnabled(enabled and self._diag_allowed)
        self._refresh_source_release()
        if not enabled:
            # 断开时把电源开关复位到"下电"，避免下次连接显示成还在上电状态
            self._apply_kl15(False, force=True)

    def set_diag_allowed(self, allowed: bool) -> None:
        """BSM / PBS 下诊断报文不接收（规范表 4），此时把按钮置灰避免"点了没反应"。"""
        self._diag_allowed = allowed
        self.diag_btn.setEnabled(self._actions_enabled and allowed)

    def set_demand(self, sources: list[str]) -> None:
        """由主窗口按内核快照回调：把"当前是否还有网络需求"直接摆在按钮旁边。

        这是本项目最容易被误解的一点：KL15 是"网络保持事件"而不是一次性脉冲，
        只要它还在，节点就会一直停在 NOS。
        """
        self._has_demand = bool(sources)
        self._demand = set(sources)
        text = " + ".join(sources) if sources else "无"
        self.demand_label.setText(f"当前网络需求：{text}")
        self.demand_label.setStyleSheet(
            "color: #F59E0B; font-weight: 500;" if sources else "color: #6B7280;"
        )
        self.release_btn.setEnabled(self._actions_enabled and self._has_demand)
        self._refresh_source_release()
        self._apply_kl15("KL15" in sources)

    # ------------------------------------------------------------------
    def _refresh_source_release(self) -> None:
        """「关闭 X 唤醒」按钮：文字与可用状态都跟着下拉和当前需求走。

        只有当该唤醒源确实持有一路网络需求时按钮才亮 —— 否则点了不会有任何
        状态变化，反而让人以为界面卡了。
        """
        index = max(0, self.source_combo.currentIndex())
        _label, source, _reason = WAKEUP_SOURCES[index]
        self.source_release_btn.setText(f"关闭 {source}")
        self.source_release_btn.setEnabled(
            self._actions_enabled and source in self._demand
        )

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
