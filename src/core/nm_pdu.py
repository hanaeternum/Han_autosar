"""NM PDU 位域定义与编解码。

为什么用「位域表 + 通用打包/解包」而不是手写位运算或 ctypes 结构体：
1. AUTOSAR 的位序（bit0 在最右）与 C 位域在不同编译器下不一致，ctypes 会让
   Byte1 的 RMR/AWB/PNI 在界面上显示错位；
2. 客户位序一旦调整（例如 Remote Sleep Indication Bit 从占位 bit3 挪到别处），
   只改下面 BIT_LAYOUT 一行，状态机和 UI 都不用动。

庆铃规范 4.6.2 报文数据格式：
  Byte0  SourceNodeIdentifier
  Byte1  Res | PNI(bit6) | Res | AWB(bit4) | Res | Res | Res | RMR(bit0)
  Byte2  Res(bit7) | 网络管理状态(bit6~0)
  Byte3  唤醒原因
  Byte4  AC_7 | AC_6 | AC_5 | OTA(bit4) | RemoteCtl(bit3) | Tmin(bit2) | Diag(bit1) | KL15(bit0)
  Byte5~7 部分网络编号标志（规范标注「暂不实施」，恒为 0）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .can_frame import CanFrame, node_id_from_can_id


@dataclass(frozen=True)
class BitField:
    name: str
    byte: int
    bit: int
    length: int = 1
    confirmed: bool = True       # False 表示该位序为客户规范未覆盖、待确认
    comment: str = ""


# ---------------------------------------------------------------------------
# 位布局（唯一事实来源）
# ---------------------------------------------------------------------------
BIT_LAYOUT: list[BitField] = [
    BitField("source_node_id", 0, 0, 8, True, "源节点地址，等于 NM 报文 ID 低 8 位"),
    BitField("repeat_message_request", 1, 0, 1, True, "RMR 重复报文请求位，规范 4.6.4 明确"),
    BitField("remote_sleep_indication", 1, 3, 1, False,
             "NM Remote Sleep Indication Bit：客户规范提及但未给出位序，暂按 bit3 占位，待客户确认"),
    BitField("active_wakeup", 1, 4, 1, True, "AWB 主动唤醒网络标志位，规范 4.6.4 明确"),
    BitField("partial_network_info", 1, 6, 1, True, "PNI 部分网络标志，规范标注暂不使用，恒 0"),
    BitField("nm_state", 2, 0, 7, True, "网络管理状态码，规范 4.6.5"),
    BitField("wakeup_reason", 3, 0, 8, True, "唤醒原因，规范 4.6.6"),
    BitField("ac_kl15", 4, 0, 1, True, "AC_0_KL15"),
    BitField("ac_diagnosis", 4, 1, 1, True, "AC_1_Diagnosis"),
    BitField("ac_tmin", 4, 2, 1, True, "AC_2_Tmin：T_REPEAT_MESSAGE 计时中"),
    BitField("ac_remote_control", 4, 3, 1, True, "AC_3_Remotecontrol"),
    BitField("ac_ota", 4, 4, 1, False,
             "AC_4_OTA：规范表格写作 AC_3_OTA，属文档笔误，暂按 bit4，待确认"),
]

RESERVED_BYTES = (5, 6, 7)   # 部分网络编号标志，暂不实施

WAKEUP_REASON_LABELS = {
    0: "初始化",
    1: "总线唤醒",
    2: "KL15上电",
}

NM_STATE_CODE_LABELS = {
    0: "-",
    1: "RMS<-BSM",
    2: "RMS<-PBS",
    4: "NOS<-RMS",
    8: "NOS<-RSS",
    16: "RMS<-NOS",
    32: "RMS<-RSS",
}


@dataclass
class NmPdu:
    """一帧解码后的 NM PDU。字段名与 BIT_LAYOUT 一一对应。"""

    source_node_id: int = 0
    repeat_message_request: bool = False
    remote_sleep_indication: bool = False
    active_wakeup: bool = False
    partial_network_info: bool = False
    nm_state: int = 0
    wakeup_reason: int = 0
    ac_kl15: bool = False
    ac_diagnosis: bool = False
    ac_tmin: bool = False
    ac_remote_control: bool = False
    ac_ota: bool = False

    can_id: int = 0
    channel: str = ""
    tx_time_ms: int = 0
    raw: bytes = field(default=b"", repr=False)

    def flags_text(self) -> str:
        parts = [
            f"RMR={int(self.repeat_message_request)}",
            f"AWB={int(self.active_wakeup)}",
            f"SLP={int(self.remote_sleep_indication)}",
        ]
        if self.partial_network_info:
            parts.append("PNI=1")
        return " ".join(parts)

    def keep_awake_text(self) -> str:
        names = []
        if self.ac_kl15:
            names.append("KL15")
        if self.ac_diagnosis:
            names.append("Diag")
        if self.ac_tmin:
            names.append("Tmin")
        if self.ac_remote_control:
            names.append("Rmt")
        if self.ac_ota:
            names.append("OTA")
        return "+".join(names) if names else "-"

    def summary(self) -> str:
        state = NM_STATE_CODE_LABELS.get(self.nm_state, str(self.nm_state))
        reason = WAKEUP_REASON_LABELS.get(self.wakeup_reason, str(self.wakeup_reason))
        return (
            f"{self.flags_text()} STATE={state} REASON={reason} "
            f"KEEP={self.keep_awake_text()}"
        )


class PduCodec:
    """按 BIT_LAYOUT 做通用打包/解包。"""

    def __init__(self, id_base: int = 0x400, dlc: int = 8, layout: list[BitField] | None = None) -> None:
        self.id_base = id_base
        self.dlc = dlc
        self.layout = list(layout or BIT_LAYOUT)
        self._field_names = {f.name for f in self.layout}

    # ---------------- 编码 ----------------
    def encode(self, pdu: NmPdu) -> bytes:
        data = bytearray(self.dlc)
        for f in self.layout:
            if not hasattr(pdu, f.name):
                continue
            value = int(getattr(pdu, f.name))
            if value < 0 or value >= (1 << f.length):
                raise ValueError(f"字段 {f.name}={value} 超出 {f.length} 位取值范围")
            for offset in range(f.length):
                if value >> offset & 0x1:
                    data[f.byte] |= 1 << (f.bit + offset)
        return bytes(data)

    def encode_frame(self, pdu: NmPdu, channel: str = "", src_ecu: str = "", time_ms: int = 0) -> CanFrame:
        from .can_frame import make_nm_can_id

        can_id = make_nm_can_id(self.id_base, pdu.source_node_id)
        return CanFrame(
            can_id=can_id,
            data=self.encode(pdu),
            channel=channel,
            src_ecu=src_ecu,
            tx_time_ms=time_ms,
            is_nm=True,
        )

    # ---------------- 解码 ----------------
    def decode(self, frame: CanFrame) -> NmPdu:
        if len(frame.data) < self.dlc:
            raise ValueError(f"NM 报文 DLC 不足：{len(frame.data)} < {self.dlc}")
        pdu = NmPdu(can_id=frame.can_id, channel=frame.channel, tx_time_ms=frame.tx_time_ms,
                    raw=frame.data[: self.dlc])
        for f in self.layout:
            value = 0
            for offset in range(f.length):
                if frame.data[f.byte] >> (f.bit + offset) & 0x1:
                    value |= 1 << offset
            if f.name == "source_node_id":
                pdu.source_node_id = value
                continue
            if hasattr(pdu, f.name):
                setattr(pdu, f.name, bool(value) if f.length == 1 else value)

        expected = node_id_from_can_id(self.id_base, frame.can_id)
        if pdu.source_node_id != expected:
            pdu.source_node_id = expected
            pdu.raw = bytes(frame.data)
        return pdu

    # ---------------- 自检 ----------------
    def unconfirmed_fields(self) -> list[BitField]:
        """返回位序待客户确认的字段，用于 UI 上醒目标注。"""
        return [f for f in self.layout if not f.confirmed]

    def describe(self) -> list[dict]:
        return [
            {
                "name": f.name,
                "byte": f.byte,
                "bit": f.bit,
                "length": f.length,
                "confirmed": f.confirmed,
                "comment": f.comment,
            }
            for f in self.layout
        ]
