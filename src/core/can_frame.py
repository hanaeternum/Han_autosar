"""CAN 帧模型与 NM 报文 ID 编解码。

客户口径：NM 报文 ID = 0x400 + Node_ID，DLC = 8，标准帧（11 位）。
庆铃规范口径：0x18FFE100 | Node_ID，扩展帧。两者都通过 NmProtocolParams.id_base 配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CanFrame:
    can_id: int
    data: bytes
    channel: str = ""
    src_ecu: str = ""
    tx_time_ms: int = 0
    is_nm: bool = True

    @property
    def dlc(self) -> int:
        return len(self.data)

    @property
    def id_hex(self) -> str:
        return f"0x{self.can_id:03X}"

    @property
    def data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def __str__(self) -> str:
        return f"{self.id_hex} [{self.data_hex}]"


def make_nm_can_id(id_base: int, node_id: int) -> int:
    """NM 报文 ID = 基地址 + 源节点地址。"""
    if not 0 <= node_id <= 0xFF:
        raise ValueError(f"NodeID 必须落在 0x00~0xFF，当前 {node_id!r}")
    return id_base + node_id


def node_id_from_can_id(id_base: int, can_id: int) -> int:
    return can_id - id_base


def is_nm_can_id(id_base: int, can_id: int) -> bool:
    """规范要求：节点需监测基地址 + 0x00~0xFF 整个范围的 NM 报文 ID。"""
    return id_base <= can_id <= id_base + 0xFF


def nm_frame_bits(dlc: int = 8) -> int:
    """标准帧的近似位数（含位填充余量），用于按波特率估算单帧时长。

    SOF 1 + ID 11 + RTR 1 + IDE 1 + r0 1 + DLC 4 + data(dlc*8)
    + CRC 15 + CRCdel 1 + ACK 1 + ACKdel 1 + EOF 7 + IFS 3 = 47 + dlc*8
    位填充最坏情况约 +19 位，这里按最坏值估算。
    """
    return 47 + dlc * 8 + 19


def nm_frame_time_us(baudrate: int, dlc: int = 8) -> float:
    """按波特率估算一帧 NM 报文占用的总线时间（微秒）。"""
    if baudrate <= 0:
        return 0.0
    return nm_frame_bits(dlc) / baudrate * 1_000_000
