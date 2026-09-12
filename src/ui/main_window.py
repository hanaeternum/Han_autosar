"""主窗口：把配置区、状态显示区、控制区、报文表与日志区组装起来。

线程模型：
    UI 线程  ──命令队列──▶  EngineWorker(QThread) ──批量信号──▶  UI 线程
所有跨线程交互只经过这两个通道，UI 线程不做任何仿真计算。
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.nm_pdu import PduCodec
from ..presenter.engine_worker import BatchPayload, EngineWorker
from .config_panel import ConfigPanel
from .control_panel import ControlPanel
from .custom_frame_dialog import CustomFrameDialog
from .frame_table import FrameTableView
from .local_status import LocalStatusPanel
from .log_view import LogView
from .state_machine_view import StateMachineView
from .topology_view import TopologyView
from .theme import TEXT_DIM, TEXT_FAINT

SPEED_OPTIONS = [("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("5×", 5.0), ("10×", 10.0)]


class MainWindow(QMainWindow):
    def __init__(self, config: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.codec = PduCodec(id_base=config.protocol.id_base, dlc=config.protocol.dlc)
        self.worker: Optional[EngineWorker] = None
        self.local_channel: Optional[str] = None
        self.local_node_id: Optional[int] = None

        self.setWindowTitle("AUTOSAR CanNm 网络管理仿真上位机")
        self.setMinimumSize(1200, 820)
        self.resize(1520, 1000)

        self._build_toolbar()
        self._build_body()
        self._build_status_bar()

    # ==================================================================
    # 界面组装
    # ==================================================================
    def _build_toolbar(self) -> None:
        bar = QToolBar("主工具栏")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addWidget(QLabel("  仿真速度 "))
        self.speed_combo = QComboBox()
        for label, value in SPEED_OPTIONS:
            self.speed_combo.addItem(label, value)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.setFixedWidth(78)
        self.speed_combo.currentIndexChanged.connect(self._on_speed)
        bar.addWidget(self.speed_combo)

        self.pause_action = QAction("暂停", self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._on_pause)
        bar.addAction(self.pause_action)

        bar.addSeparator()
        clear_frames = QAction("清空报文表", self)
        clear_frames.triggered.connect(lambda: self.frame_table.clear())
        bar.addAction(clear_frames)

        about = QAction("规范符合性说明", self)
        about.triggered.connect(self._show_about)
        bar.addAction(about)
        bar.addSeparator()

        self.thread_label = QLabel("内核线程：未启动")
        self.thread_label.setStyleSheet(f"color: {TEXT_DIM};")
        bar.addWidget(self.thread_label)

    def _build_body(self) -> None:
        left = QWidget()
        left.setFixedWidth(332)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(10)

        self.config_panel = ConfigPanel(self.config)
        self.config_panel.connectRequested.connect(self._on_connect)
        self.config_panel.disconnectRequested.connect(self._on_disconnect)
        self.config_panel.invalidNodeId.connect(
            lambda text: QMessageBox.warning(self, "Node ID 无效", text)
        )

        self.control_panel = ControlPanel()
        self.control_panel.kl15Changed.connect(self._on_kl15)
        self.control_panel.networkRequest.connect(self._on_network_request)
        self.control_panel.networkRelease.connect(self._on_network_release)
        self.control_panel.repeatMessageRequest.connect(self._on_rmr)
        self.control_panel.diagRequest.connect(self._on_diag)
        self.control_panel.customFrameRequested.connect(self._on_custom_frame)

        self.local_status = LocalStatusPanel()

        left_layout.addWidget(self.config_panel)
        left_layout.addWidget(self.control_panel)
        left_layout.addWidget(self.local_status)
        left_layout.addStretch(1)

        # ---------------- 右上：拓扑图 + 状态机流程图 ----------------
        topology_box = QGroupBox("整车网络拓扑（实时状态）")
        self.topology_view = TopologyView(self.config)
        topology_layout = QVBoxLayout(topology_box)
        topology_layout.setContentsMargins(8, 14, 8, 8)
        topology_layout.addWidget(self.topology_view)

        machine_box = QGroupBox("CanNm 生命周期（本机节点）")
        self.state_machine_view = StateMachineView()
        machine_layout = QVBoxLayout(machine_box)
        machine_layout.setContentsMargins(8, 14, 8, 8)
        machine_layout.addWidget(self.state_machine_view)

        views_split = QSplitter(Qt.Horizontal)
        views_split.addWidget(topology_box)
        views_split.addWidget(machine_box)
        views_split.setStretchFactor(0, 3)
        views_split.setStretchFactor(1, 1)
        views_split.setSizes([760, 380])

        # ---------------- 右中：报文监控 ----------------
        frames_box = QGroupBox("NM 报文监控")
        self.frame_table = FrameTableView()
        frames_toolbar = QHBoxLayout()
        frames_toolbar.setSpacing(6)
        frames_toolbar.addWidget(
            self._dim_label("只显示 NM 报文（ID = 0x400 + NodeID）；方向以左侧选定的本机节点为参照")
        )
        frames_toolbar.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.frame_table.clear())
        frames_toolbar.addWidget(clear_btn)
        frames_layout = QVBoxLayout(frames_box)
        frames_layout.setSpacing(6)
        frames_layout.addLayout(frames_toolbar)
        frames_layout.addWidget(self.frame_table, 1)

        # ---------------- 右下：日志 ----------------
        self.log_view = LogView()

        right_split = QSplitter(Qt.Vertical)
        right_split.addWidget(views_split)
        right_split.addWidget(frames_box)
        right_split.addWidget(self.log_view)
        right_split.setStretchFactor(0, 0)
        right_split.setStretchFactor(1, 2)
        right_split.setStretchFactor(2, 3)
        right_split.setSizes([368, 210, 360])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right_split)
        split.setStretchFactor(1, 1)
        split.setSizes([332, 1160])
        self.setCentralWidget(split)

    def _build_status_bar(self) -> None:
        self.time_label = QLabel("仿真时间 0.00s")
        self.bus_label = QLabel("总线 —")
        self.sleep_label = QLabel("SleepIndication — / SleepAcknowledge —")
        for label in (self.time_label, self.bus_label, self.sleep_label):
            label.setStyleSheet(f"color: {TEXT_DIM}; padding: 0 12px;")
        self.statusBar().addPermanentWidget(self.sleep_label)
        self.statusBar().addPermanentWidget(self.bus_label)
        self.statusBar().addPermanentWidget(self.time_label)
        self.statusBar().showMessage("未连接：请在上方选择通道与 Node ID，然后点击「连接」")

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px;")
        return label

    # ==================================================================
    # 连接 / 断开
    # ==================================================================
    def _on_connect(self, channel: str, node_id: int, baudrate: int) -> None:
        self.connect_simulation(channel, node_id, baudrate)

    def connect_simulation(self, channel: str, node_id: int, baudrate: int = 500000) -> None:
        """启动内核线程并让本机节点上线。

        界面按钮、自检（--smoke）与自动化测试都走这个入口，行为一致。
        """
        self.local_channel = channel
        self.local_node_id = node_id
        ecu = self._ecu_of(channel, node_id)
        self.config_panel.set_connected(True)
        self.control_panel.set_enabled_actions(True)
        self.frame_table.set_local(ecu)

        self.worker = EngineWorker(self.config, speed=self.speed_combo.currentData(), parent=self)
        self.worker.batch.connect(self._on_batch)
        self.worker.error.connect(self._on_worker_error)
        self.worker.runningChanged.connect(self._on_running_changed)
        self.worker.start()

        self.statusBar().showMessage(
            f"已连接：{channel} / {ecu}（NodeID 0x{node_id:02X}，CAN ID "
            f"0x{self.config.protocol.id_base + node_id:03X}），{baudrate // 1000} kbps"
        )

    def _on_disconnect(self) -> None:
        self.disconnect_simulation()

    def disconnect_simulation(self) -> None:
        """停止内核线程并把界面恢复到未连接状态。"""
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)
            self.worker = None
        self.config_panel.set_connected(False)
        self.control_panel.set_enabled_actions(False)
        self.control_panel.set_demand([])
        self.local_status.set_idle()
        self.thread_label.setText("内核线程：未启动")
        self.statusBar().showMessage("已断开：仿真已冻结，可重新连接继续观察")

    def _on_running_changed(self, running: bool) -> None:
        self.thread_label.setText("内核线程：运行中" if running else "内核线程：已停止")

    def _on_worker_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.critical(self, "内核线程异常", message)

    # ==================================================================
    # 控制动作
    # ==================================================================
    def _on_kl15(self, on: bool) -> None:
        """整车 15 电上/下电：置位或清除保持事件 AC_0_KL15。"""
        if not self.worker or self.local_channel is None:
            return
        if on:
            self.worker.request_network(self.local_channel, self.local_node_id, "KL15", 2)
        else:
            # 只释放 KL15 这一路需求（其它保持事件如诊断保持不受影响）
            self.worker.release_network(self.local_channel, self.local_node_id, "KL15")

    def _on_network_request(self, source: str, reason: int) -> None:
        if self.worker and self.local_channel is not None:
            self.worker.request_network(self.local_channel, self.local_node_id, source, reason)

    def _on_network_release(self) -> None:
        if self.worker and self.local_channel is not None:
            self.worker.release_network(self.local_channel, self.local_node_id)

    def _on_rmr(self) -> None:
        if self.worker and self.local_channel is not None:
            self.worker.repeat_message_request(self.local_channel, self.local_node_id)

    def _on_diag(self) -> None:
        if self.worker and self.local_channel is not None:
            self.worker.diag_request(self.local_channel, self.local_node_id)

    def _on_custom_frame(self) -> None:
        if not self.worker or self.local_channel is None:
            return
        dialog = CustomFrameDialog(self.config, self.local_channel, self.local_node_id, self)
        if dialog.exec_() != dialog.Accepted:
            return
        channel = dialog.selected_channel()
        can_id = dialog.can_id()
        payload = dialog.payload()
        self.worker.inject_frame(
            channel, can_id, payload, note=f"0x{can_id:03X} [{' '.join(f'{b:02X}' for b in payload)}]"
        )

    def _on_speed(self) -> None:
        if self.worker:
            self.worker.set_speed(self.speed_combo.currentData())

    def _on_pause(self, paused: bool) -> None:
        self.pause_action.setText("继续" if paused else "暂停")
        if self.worker:
            self.worker.set_paused(paused)

    # ==================================================================
    # 内核 → UI
    # ==================================================================
    def _on_batch(self, payload: BatchPayload) -> None:
        self.log_view.append_events(payload.logs)
        if payload.frames:
            self.frame_table.append_frames(payload.frames)
            self.topology_view.set_frames(payload.frames)

        channels = payload.snapshot.get("channels", {})
        local = (self.local_channel, self.local_node_id) if self.local_channel else None
        self.topology_view.set_snapshot(channels, local)

        local_snap = None
        if local:
            channel_snap = channels.get(local[0])
            if channel_snap:
                for node_snap in channel_snap["nodes"]:
                    if node_snap["node_id"] == local[1]:
                        local_snap = node_snap
                        break
        if local_snap is not None:
            self.local_status.update_from_snapshot(local_snap)
            self.state_machine_view.set_snapshot(local_snap)
            # 把"当前还有没有网络需求"直接摆到控制按钮旁边：
            # KL15 这类保持事件不清除，节点就会一直停在 NOS（规范语义，不是卡住）
            self.control_panel.set_demand(local_snap["keep_awake"])
        else:
            self.state_machine_view.set_snapshot(None)

        self.time_label.setText(f"仿真时间 {payload.sim_time_ms / 1000:.2f}s")
        bus_text = "  ".join(
            f"{name} {stats['tx_frames']}帧" for name, stats in payload.bus_stats.items()
        )
        self.bus_label.setText(f"总线 {bus_text}")
        flags = []
        for name, channel_snap in channels.items():
            flags.append(
                f"{name} SLP={'✓' if channel_snap['sleep_indication'] else '—'}"
                f"/ACK={'✓' if channel_snap['sleep_acknowledge'] else '—'}"
            )
        self.sleep_label.setText("  ".join(flags))

    # ==================================================================
    # 辅助
    # ==================================================================
    def _ecu_of(self, channel: str, node_id: int) -> str:
        for ch in self.config.channels:
            if ch["name"] != channel:
                continue
            for node in ch["nodes"]:
                if node["node_id"] == node_id:
                    return node["ecu"]
        return f"0x{node_id:02X}"

    def _show_about(self) -> None:
        unconfirmed = "、".join(
            f"{f.name}（Byte{f.byte}.bit{f.bit}）" for f in self.codec.unconfirmed_fields()
        )
        QMessageBox.information(
            self,
            "规范符合性说明",
            f"参数档位：{self.config.profile}\n"
            f"NM 报文 ID：0x{self.config.protocol.id_base:03X} + NodeID，"
            f"DLC={self.config.protocol.dlc}\n\n"
            f"以下信号在客户规范中未给出位序，当前为占位实现，已标注待确认：\n"
            f"　{unconfirmed}\n\n"
            "内核状态机按《庆铃 AUTOSAR CAN 网络管理技术规范 V1.0》4.3 节实现，\n"
            "迁移表与规范条款的对应关系见 docs/状态机说明.md。",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)
        super().closeEvent(event)
