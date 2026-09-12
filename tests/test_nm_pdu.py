"""NM PDU 编解码与报文 ID 规则测试。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.can_frame import (  # noqa: E402
    is_nm_can_id,
    make_nm_can_id,
    node_id_from_can_id,
)
from src.core.nm_pdu import NmPdu, PduCodec  # noqa: E402

ID_BASE = 0x400


class TestCanId(unittest.TestCase):
    def test_customer_id_rule(self):
        """客户口径：报文 ID = 0x400 + Node_ID。"""
        self.assertEqual(make_nm_can_id(ID_BASE, 0x10), 0x410)
        self.assertEqual(make_nm_can_id(ID_BASE, 0xFF), 0x4FF)
        self.assertEqual(node_id_from_can_id(ID_BASE, 0x430), 0x30)

    def test_node_id_range(self):
        with self.assertRaises(ValueError):
            make_nm_can_id(ID_BASE, 0x100)

    def test_monitor_range(self):
        """规范要求节点监测基地址 + 0x00~0xFF 全范围。"""
        self.assertTrue(is_nm_can_id(ID_BASE, 0x400))
        self.assertTrue(is_nm_can_id(ID_BASE, 0x4FF))
        self.assertFalse(is_nm_can_id(ID_BASE, 0x500))

    def test_qingling_extended_id_rule(self):
        """庆铃规范口径：0x18FFE100 | Node_ID。"""
        base = 0x18FFE100
        self.assertEqual(make_nm_can_id(base, 0x10), 0x18FFE110)
        self.assertTrue(is_nm_can_id(base, 0x18FFE1FF))


class TestPduCodec(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = PduCodec(id_base=ID_BASE)

    def test_round_trip_all_flags(self):
        pdu = NmPdu(
            source_node_id=0x30,
            repeat_message_request=True,
            remote_sleep_indication=True,
            active_wakeup=True,
            partial_network_info=True,
            nm_state=16,
            wakeup_reason=2,
            ac_kl15=True,
            ac_diagnosis=True,
            ac_tmin=False,
            ac_remote_control=True,
            ac_ota=True,
        )
        decoded = self.codec.decode(self.codec.encode_frame(pdu))
        for field in ("source_node_id", "repeat_message_request", "remote_sleep_indication",
                      "active_wakeup", "partial_network_info", "nm_state", "wakeup_reason",
                      "ac_kl15", "ac_diagnosis", "ac_tmin", "ac_remote_control", "ac_ota"):
            self.assertEqual(getattr(decoded, field), getattr(pdu, field), f"字段 {field} 往返不一致")

    def test_byte_layout(self):
        """逐字节核对规范 4.6.2 的布局，防止位序回归。"""
        pdu = NmPdu(
            source_node_id=0x30,
            repeat_message_request=True,       # Byte1 bit0
            active_wakeup=True,                # Byte1 bit4
            remote_sleep_indication=True,      # Byte1 bit3（位序待客户确认）
            nm_state=4,                        # Byte2 bit2
            wakeup_reason=1,                   # Byte3
            ac_kl15=True,                      # Byte4 bit0
            ac_tmin=True,                      # Byte4 bit2
        )
        data = self.codec.encode(pdu)
        self.assertEqual(len(data), 8)
        self.assertEqual(data[0], 0x30, "Byte0 = SourceNodeIdentifier")
        self.assertEqual(data[1], 0b00011001, "Byte1 = RMR(bit0)+SLP(bit3)+AWB(bit4)")
        self.assertEqual(data[2], 0x04, "Byte2 = 网络管理状态码")
        self.assertEqual(data[3], 0x01, "Byte3 = 唤醒原因")
        self.assertEqual(data[4], 0b00000101, "Byte4 = KL15(bit0)+Tmin(bit2)")
        self.assertEqual(list(data[5:]), [0, 0, 0], "Byte5~7 部分网络标志暂不实施，恒为 0")

    def test_can_id_contains_node_id(self):
        frame = self.codec.encode_frame(NmPdu(source_node_id=0x48))
        self.assertEqual(frame.can_id, 0x448)
        self.assertEqual(frame.dlc, 8)
        self.assertEqual(self.codec.decode(frame).source_node_id, 0x48)

    def test_unconfirmed_fields_flagged(self):
        """位序待客户确认的字段必须能被识别出来，UI 上要醒目标注。"""
        names = {f.name for f in self.codec.unconfirmed_fields()}
        self.assertIn("remote_sleep_indication", names)
        self.assertIn("ac_ota", names)

    def test_decode_rejects_short_frame(self):
        from src.core.can_frame import CanFrame

        with self.assertRaises(ValueError):
            self.codec.decode(CanFrame(can_id=0x410, data=b"\x10\x00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
