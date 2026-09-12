"""连接配置区：CAN 通道 / 波特率 / Node ID / 连接与断开。"""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.can_frame import nm_frame_time_us
from .theme import TEXT_FAINT

BAUDRATE_OPTIONS = [
    ("125 kbps", 125000),
    ("250 kbps", 250000),
    ("500 kbps", 500000),
    ("1 Mbps", 1000000),
]


class ConfigPanel(QGroupBox):
    connectRequested = pyqtSignal(str, int, int)   # channel, node_id, baudrate
    disconnectRequested = pyqtSignal()
    invalidNodeId = pyqtSignal(str)

    def __init__(self, config: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__("连接配置", parent)
        self.config = config
        self._connected = False

        self.channel_combo = QComboBox()
        for ch in config.channels:
            self.channel_combo.addItem(
                f"{ch['name']}（{ch.get('bitrate', 500000) // 1000}kbps）", ch["name"]
            )
        self.channel_combo.currentIndexChanged.connect(self._refresh_nodes)

        self.baudrate_combo = QComboBox()
        for label, value in BAUDRATE_OPTIONS:
            self.baudrate_combo.addItem(label, value)
        self.baudrate_combo.setCurrentIndex(2)          # 默认 500kbps
        self.baudrate_combo.currentIndexChanged.connect(self._refresh_frame_time)

        self.node_combo = QComboBox()
        self.node_combo.setEditable(True)
        self.node_combo.setInsertPolicy(QComboBox.NoInsert)
        self.node_combo.lineEdit().setPlaceholderText("如 0x30 或 PEPS")

        form = QFormLayout()
        form.setLabelAlignment(form.labelAlignment())
        form.setSpacing(6)
        form.addRow("CAN 通道", self.channel_combo)
        form.addRow("波特率", self.baudrate_combo)
        form.addRow("Node ID", self.node_combo)

        self.connect_btn = QPushButton("连接")
        self.connect_btn.setObjectName("primary")
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.connect_btn)
        buttons.addWidget(self.disconnect_btn)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addLayout(form)
        root.addLayout(buttons)
        root.addWidget(self.hint)

        self._refresh_nodes()
        self._refresh_frame_time()

    # ------------------------------------------------------------------
    def _refresh_nodes(self) -> None:
        channel = self.current_channel()
        self.node_combo.clear()
        for ch in self.config.channels:
            if ch["name"] != channel:
                continue
            for node in ch["nodes"]:
                # 描述信息放 tooltip，不要塞进显示文本 —— 下拉框会被撑到显示不全
                self.node_combo.addItem(f"{node['ecu']} · 0x{node['node_id']:02X}", node["node_id"])
                self.node_combo.setItemData(
                    self.node_combo.count() - 1,
                    f"{node['ecu']}（{node.get('role', '')}）",
                    Qt.ToolTipRole,
                )

    def _refresh_frame_time(self) -> None:
        us = nm_frame_time_us(self.current_baudrate(), self.config.protocol.dlc)
        self.hint.setText(f"单帧 NM 报文约 {us:.0f}µs；波特率不影响状态逻辑。")

    # ------------------------------------------------------------------
    def current_channel(self) -> str:
        return self.channel_combo.currentData()

    def current_baudrate(self) -> int:
        return self.baudrate_combo.currentData()

    def current_node_id(self) -> int:
        """统一按十六进制解析：下拉项 'PEPS · 0x30'、手输 '30' 或 '0x30' 均可。"""
        text = self.node_combo.currentText().strip()
        token = text.split("·")[-1].split("(")[0].strip()
        if not token:
            data = self.node_combo.currentData()
            if data is None:
                raise ValueError("请先选择或输入 Node ID")
            return int(data)
        try:
            return int(token, 16)
        except ValueError:
            raise ValueError(f"无法解析 Node ID：{text!r}（按十六进制输入，如 30 或 0x30）")

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        for widget in (self.channel_combo, self.baudrate_combo, self.node_combo):
            widget.setEnabled(not connected)
        if not connected:
            self._refresh_frame_time()

    # ------------------------------------------------------------------
    def _on_connect(self) -> None:
        try:
            node_id = self.current_node_id()
        except ValueError as exc:
            self.invalidNodeId.emit(str(exc))
            return
        channel = self.current_channel()
        known = {n["node_id"] for ch in self.config.channels if ch["name"] == channel for n in ch["nodes"]}
        if node_id not in known:
            listing = "、".join(f"0x{i:02X}" for i in sorted(known))
            self.invalidNodeId.emit(
                f"通道 {channel} 上没有 NodeID=0x{node_id:02X} 的节点。\n当前拓扑可用：{listing}"
            )
            return
        self.connectRequested.emit(channel, node_id, self.current_baudrate())

    def _on_disconnect(self) -> None:
        self.disconnectRequested.emit()
