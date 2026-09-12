"""自定义 NM 报文对话框。

两种编辑方式：
- 字段模式：按规范字段勾选/下拉，右侧实时预览 8 个字节；
- 原始模式：直接输入 8 字节十六进制，用于复现抓包或异常帧。

位序完全取自内核的 BIT_LAYOUT，客户确认位序后这里不用改。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.can_frame import make_nm_can_id
from ..core.nm_pdu import NM_STATE_CODE_LABELS, WAKEUP_REASON_LABELS, NmPdu, PduCodec
from .theme import TEXT_FAINT, TEXT_DIM

STATE_CODE_CHOICES = sorted(NM_STATE_CODE_LABELS.items())
REASON_CHOICES = sorted(WAKEUP_REASON_LABELS.items())


class CustomFrameDialog(QDialog):
    def __init__(self, config: Any, channel: str, node_id: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.codec: PduCodec = PduCodec(id_base=config.protocol.id_base, dlc=config.protocol.dlc)
        self.setWindowTitle("发送自定义 NM 报文")
        self.setMinimumWidth(560)

        self.channel_combo = QComboBox()
        for ch in config.channels:
            self.channel_combo.addItem(ch["name"], ch["name"])
        index = self.channel_combo.findData(channel)
        if index >= 0:
            self.channel_combo.setCurrentIndex(index)

        self.source_spin = QSpinBox()
        self.source_spin.setRange(0x00, 0xFF)
        self.source_spin.setValue(node_id)
        self.source_spin.setDisplayIntegerBase(16)
        self.source_spin.setPrefix("0x")
        self.source_spin.valueChanged.connect(self._refresh_preview)

        self.rmr_check = QCheckBox("RMR 重复报文请求位（bit0）")
        self.awb_check = QCheckBox("AWB 主动唤醒位（bit4）")
        self.slp_check = QCheckBox("SLP 远程睡眠指示位（bit3，位序待确认）")
        self.pni_check = QCheckBox("PNI 部分网络位（bit6，规范暂不实施）")
        for box in (self.rmr_check, self.awb_check, self.slp_check, self.pni_check):
            box.stateChanged.connect(self._refresh_preview)

        self.state_combo = QComboBox()
        for code, label in STATE_CODE_CHOICES:
            text = f"{code} · 无（不携带状态）" if code == 0 else f"{code} · {label}"
            self.state_combo.addItem(text, code)
        self.state_combo.currentIndexChanged.connect(self._refresh_preview)

        self.reason_combo = QComboBox()
        for code, label in REASON_CHOICES:
            self.reason_combo.addItem(f"{code} · {label}", code)
        self.reason_combo.setCurrentIndex(1)   # 默认 1 总线唤醒
        self.reason_combo.currentIndexChanged.connect(self._refresh_preview)

        self.keep_checks = {
            "ac_kl15": QCheckBox("AC_0_KL15（bit0）"),
            "ac_diagnosis": QCheckBox("AC_1_Diagnosis（bit1）"),
            "ac_tmin": QCheckBox("AC_2_Tmin（bit2）"),
            "ac_remote_control": QCheckBox("AC_3_Remotecontrol（bit3）"),
            "ac_ota": QCheckBox("AC_4_OTA（bit4，位序待确认）"),
        }
        for box in self.keep_checks.values():
            box.stateChanged.connect(self._refresh_preview)

        self.preview = QLabel()
        self.preview.setStyleSheet("font-family: Consolas; font-size: 15px; letter-spacing: 2px;")
        self.preview_id = QLabel()
        self.preview_id.setStyleSheet(f"color: {TEXT_DIM}; font-family: Consolas;")

        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText("如 30 10 01 02 05 00 00 00（留空则按上方字段生成）")
        self.raw_edit.textChanged.connect(self._refresh_preview)

        # ---------------- 组装 ----------------
        field_box = QGroupBox("控制比特向量 / 状态 / 唤醒原因")
        field_form = QFormLayout(field_box)
        field_form.addRow(self.rmr_check)
        field_form.addRow(self.awb_check)
        field_form.addRow(self.slp_check)
        field_form.addRow(self.pni_check)
        field_form.addRow("网络管理状态码", self.state_combo)
        field_form.addRow("唤醒原因", self.reason_combo)

        keep_box = QGroupBox("网络保持事件（Byte4）")
        keep_grid = QGridLayout(keep_box)
        for row, box in enumerate(self.keep_checks.values()):
            keep_grid.addWidget(box, row // 2, row % 2)

        raw_box = QGroupBox("原始字节（可选，优先级高于字段）")
        raw_layout = QVBoxLayout(raw_box)
        raw_layout.addWidget(self.raw_edit)

        head = QFormLayout()
        head.addRow("CAN 通道", self.channel_combo)
        head.addRow("源节点地址（Byte0）", self.source_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("发送到总线")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addLayout(head)
        root.addWidget(field_box)
        root.addWidget(keep_box)
        root.addWidget(raw_box)

        preview_box = QGroupBox("发送预览")
        preview_layout = QHBoxLayout(preview_box)
        preview_layout.addWidget(self.preview_id)
        preview_layout.addWidget(self.preview)
        preview_layout.addStretch(1)
        root.addWidget(preview_box)

        note = QLabel("提示：注入的报文以 EXT 为源节点名，本机节点不会把它当成自己发的报文。")
        note.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px;")
        root.addWidget(note)
        root.addWidget(buttons)

        self._refresh_preview()

    # ------------------------------------------------------------------
    def _build_pdu(self) -> NmPdu:
        return NmPdu(
            source_node_id=self.source_spin.value(),
            repeat_message_request=self.rmr_check.isChecked(),
            remote_sleep_indication=self.slp_check.isChecked(),
            active_wakeup=self.awb_check.isChecked(),
            partial_network_info=self.pni_check.isChecked(),
            nm_state=self.state_combo.currentData(),
            wakeup_reason=self.reason_combo.currentData(),
            **{name: box.isChecked() for name, box in self.keep_checks.items()},
        )

    def payload(self) -> bytes:
        raw = self.raw_edit.text().strip()
        if raw:
            tokens = re.split(r"[\s,]+", raw)
            values = [int(token, 16) for token in tokens if token]
            if len(values) != self.config.protocol.dlc:
                raise ValueError(f"原始字节需要 {self.config.protocol.dlc} 个，当前 {len(values)} 个")
            return bytes(values)
        return self.codec.encode(self._build_pdu())

    def selected_channel(self) -> str:
        return self.channel_combo.currentData()

    def can_id(self) -> int:
        return make_nm_can_id(self.config.protocol.id_base, self.source_spin.value())

    # ------------------------------------------------------------------
    def _refresh_preview(self) -> None:
        try:
            data = self.payload()
        except ValueError as exc:
            self.preview.setText("— 原始字节无效 —")
            self.preview.setStyleSheet("font-family: Consolas; font-size: 15px; color: #F87171;")
            self.preview_id.setText(str(exc))
            return
        self.preview.setStyleSheet("font-family: Consolas; font-size: 15px; letter-spacing: 2px;")
        self.preview.setText(" ".join(f"{b:02X}" for b in data))
        self.preview_id.setText(
            f"ID 0x{self.can_id():03X}  DLC {len(data)}  |  "
            f"{'原始模式' if self.raw_edit.text().strip() else '字段模式'}"
        )

    def accept(self) -> None:  # noqa: D102
        try:
            self.payload()
        except ValueError as exc:
            QMessageBox.warning(self, "报文格式错误", str(exc))
            return
        super().accept()
