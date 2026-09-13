"""UI 冒烟测试：不显示窗口，走完"连接 → 唤醒 → 释放 → 断开"全流程。

跑法：python -m unittest tests.test_ui_smoke -v

要点：
- 用 WA_DontShowOnScreen 而不是 QT_QPA_PLATFORM=offscreen。offscreen 平台插件
  在本机 QFontDatabase().families() 返回 0（拿不到任何字体），界面能构造但全是空白，
  拿它做像素级校验会得到假结论。
- 不用 app.exec_()（会卡住），改成手动 processEvents 泵事件循环，
  这样才能在单测里推进 QThread 与信号槽。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PYQT_AVAILABLE = False

from src.config.loader import load_config  # noqa: E402


@unittest.skipUnless(PYQT_AVAILABLE, "未安装 PyQt5，跳过界面测试")
class TestUiSmoke(unittest.TestCase):
    app: "QApplication"

    @classmethod
    def setUpClass(cls) -> None:
        from src.ui.theme import STYLESHEET

        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setStyleSheet(STYLESHEET)
        cls.config = load_config()

    @classmethod
    def pump(cls, milliseconds: int) -> None:
        """手动泵事件循环，替代 app.exec_()。"""
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            cls.app.processEvents()
            time.sleep(0.005)
        cls.app.processEvents()

    def setUp(self) -> None:
        from src.ui.main_window import MainWindow

        self.window = MainWindow(self.config)
        self.window.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.window.resize(1400, 900)
        self.window.show()
        self.pump(60)

    def tearDown(self) -> None:
        self.window.close()
        self.pump(60)

    # ------------------------------------------------------------------
    def test_connect_wake_release_disconnect(self) -> None:
        self.assertEqual(self.window.local_status.state_short.text(), "未连接")

        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(300)
        self.assertIsNotNone(self.window.worker)
        self.assertIn("运行中", self.window.thread_label.text())
        self.assertEqual(self.window.local_status.state_short.text(), "BSM")

        # 唤醒后应尽快离开 BSM
        self.window._on_network_request("KL15", 2)
        self.pump(400)
        self.assertIn(self.window.local_status.state_short.text(), {"RMS", "NOS"})

        # 报文表与日志都应该有数据
        self.assertGreater(self.window.frame_table.frame_model.row_count(), 0, "报文监控表为空")
        self.assertNotEqual(self.window.log_view.count_label.text(), "0 条", "日志区为空")

        # 日志里必须出现状态迁移与报文收发
        text = self.window.log_view.text.toPlainText()
        self.assertIn("STATE", text)
        self.assertIn("TX", text)

        # 释放 → 规范 4.3.3.1：RMS 内先保持发送，等 T_REPEAT_MESSAGE(1000ms) 超时后才进 RSS
        self.window._on_network_release()
        self.pump(200)
        self.assertEqual(self.window.local_status.state_short.text(), "RMS",
                         "RMS 中释放需求不应立刻跳走，要先等 T_REPEAT_MESSAGE 超时")
        self.pump(1200)
        self.assertEqual(self.window.local_status.state_short.text(), "RSS")

        # 断开：线程要停干净
        self.window.disconnect_simulation()
        self.assertEqual(self.window.worker, None)
        self.assertIn("未启动", self.window.thread_label.text())
        self.assertEqual(self.window.local_status.state_short.text(), "未连接")

    def test_pause_and_speed(self) -> None:
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(200)
        self.window._on_speed()          # 走一遍下拉变更处理
        self.window._on_pause(True)
        self.assertTrue(self.window.worker.paused)

        count_before = self.window.frame_table.frame_model.row_count()
        self.pump(400)
        self.assertEqual(
            self.window.frame_table.frame_model.row_count(),
            count_before,
            "暂停后不应再产生新的总线报文（时间已冻结）",
        )

        self.window._on_pause(False)
        self.assertFalse(self.window.worker.paused)
        self.window._on_network_request("KL15", 2)
        self.pump(400)
        self.assertGreater(self.window.frame_table.frame_model.row_count(), count_before)

    def test_state_cards_reflect_engine(self) -> None:
        """被唤醒的完整流程里，不在同一个网段的节点不能跟着变。"""
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(200)
        self.window._on_network_request("KL15", 2)
        self.pump(500)

        topo = self.window.topology_view
        self.assertNotEqual(topo.state_short_of("CAN_BODY", 0x30), "BSM", "PEPS 应已被唤醒")
        self.assertEqual(topo.state_short_of("CAN_INFO", 0x50), "BSM", "信息网节点不应被车身网唤醒")
        # 拓扑图上两台网关实例必须各显示自己的状态
        self.assertNotEqual(topo.state_short_of("CAN_BODY", 0x10), "BSM")
        self.assertEqual(topo.state_short_of("CAN_INFO", 0x10), "BSM")

    def test_custom_frame_dialog_builds_pdu(self) -> None:
        from src.ui.custom_frame_dialog import CustomFrameDialog

        dialog = CustomFrameDialog(self.config, "CAN_BODY", 0x30)
        dialog.rmr_check.setChecked(True)
        dialog.awb_check.setChecked(True)
        payload = dialog.payload()
        self.assertEqual(len(payload), 8)
        self.assertEqual(payload[0], 0x30, "Byte0 应为源节点地址")
        self.assertEqual(payload[1] & 0b10001, 0b10001, "RMR(bit0) 与 AWB(bit4) 都应为 1")
        self.assertEqual(dialog.can_id(), 0x430)

        # 原始字节模式应覆盖字段模式
        dialog.raw_edit.setText("30 00 01 00 00 00 00 00")
        self.assertEqual(list(dialog.payload())[1], 0x00)
        dialog.close()

    def test_demand_indicator_and_release_button(self) -> None:
        """控制区要实时显示"当前网络需求"，且只在有需求时才允许点释放。"""
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        self.assertIn("无", panel.demand_label.text())
        self.assertFalse(panel.release_btn.isEnabled(), "没有需求时不该能点释放")

        self.window._on_network_request("KL15", 2)
        self.pump(300)
        self.assertIn("KL15", panel.demand_label.text())
        self.assertTrue(panel.release_btn.isEnabled())

        self.window._on_network_release()
        self.pump(250)
        self.assertIn("无", panel.demand_label.text())
        self.assertFalse(panel.release_btn.isEnabled())

    def test_kl15_keeps_node_in_nos_until_released(self) -> None:
        """回归：KL15 是"网络保持事件"而不是一次性脉冲。

        只点主动唤醒、不点释放，节点会一直停在 NOS —— 这是规范语义（4.3.3.2），
        因为 NOS 下每发一帧就重启 T_NMTimeout，没有任何路径能自动退出。
        """
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        self.window._on_network_request("KL15", 2)
        self.pump(2600)      # 远超 T_REPEAT_MESSAGE=1000ms

        self.assertEqual(self.window.local_status.state_short.text(), "NOS")
        self.assertNotIn("PBS", self.window.log_view.text.toPlainText(),
                         "未释放网络需求，节点不应该进入 PBS")

        # 释放后才允许走完休眠流程（完整 RSS→PBS→BSM 时序由 test_state_machine 覆盖）
        self.window._on_network_release()
        self.pump(2500)
        self.assertIn(self.window.local_status.state_short.text(), {"RSS", "PBS"})

    def test_kl15_switch_on_then_off(self) -> None:
        """KL15 上电 → NOS 周期发送；KL15 下电 → RSS，之后才能睡下去。

        这是整车视角的主演示流程：两个动作分别对应 NetworkRequest / NetworkRelease。
        """
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        self.assertFalse(panel.kl15_btn.isChecked())
        self.assertIn("未上电", panel.kl15_state.text())

        # ---- KL15 上电 ----
        panel.kl15_btn.setChecked(True)
        self.assertIn("下电", panel.kl15_btn.text(), "上电后按钮应变成「KL15 下电」")
        self.pump(1600)          # 等 T_REPEAT_MESSAGE=1000ms 过去
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")
        self.assertIn("KL15", panel.demand_label.text())
        self.assertIn("AC_0_KL15 = 1", panel.kl15_state.text())

        # 报文里 AC_0_KL15(Byte4 bit0) 必须为 1
        text = self.window.log_view.text.toPlainText()
        self.assertIn("KEEP=KL15", text)

        # ---- KL15 下电 ----
        panel.kl15_btn.setChecked(False)
        self.assertIn("上电", panel.kl15_btn.text(), "下电后按钮应变成「KL15 上电」")
        self.pump(300)
        self.assertEqual(self.window.local_status.state_short.text(), "RSS")
        self.assertIn("无", panel.demand_label.text())
        self.assertIn("AC_0_KL15 = 0", panel.kl15_state.text())

        # 之后总线静默 → PBS → BSM
        self.pump(2300)
        self.assertIn(self.window.local_status.state_short.text(), {"PBS", "BSM"})

    def test_kl15_off_only_releases_kl15(self) -> None:
        """KL15 下电只清除 KL15 这一路需求，诊断保持不受影响。"""
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        panel.kl15_btn.setChecked(True)
        self.pump(1600)
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")

        # 通过诊断请求引入 DIAG 保持事件（需先在 RSS 下才会置位）
        panel.kl15_btn.setChecked(False)
        self.pump(300)
        self.assertEqual(self.window.local_status.state_short.text(), "RSS")
        self.window._on_diag()
        self.pump(300)
        self.assertEqual(self.window.local_status.state_short.text(), "NOS", "诊断请求应把节点拉回 NOS")
        self.assertIn("DIAG", panel.demand_label.text())

        # 此时再上电/下电 KL15，DIAG 需求不应被清掉
        panel.kl15_btn.setChecked(True)
        self.pump(1200)
        panel.kl15_btn.setChecked(False)
        self.pump(400)
        self.assertIn("DIAG", panel.demand_label.text(), "KL15 下电不应清除诊断保持事件")
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")

    def test_io_wakeup_has_paired_release(self) -> None:
        """I/O 唤醒要有配对的关闭动作，下拉选谁就开/关谁。"""
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)

        # 下拉默认停在 I/O 唤醒
        self.assertIn("关闭 IO", panel.source_release_btn.text())
        self.assertFalse(panel.source_release_btn.isEnabled(), "没唤醒时不该能点关闭")

        # ---- I/O 唤醒 ----
        self.window._on_network_request("IO", 3)
        self.pump(1600)          # 等 T_REPEAT_MESSAGE=1000ms 过去
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")
        self.assertIn("IO", panel.demand_label.text())
        self.assertTrue(panel.source_release_btn.isEnabled(), "持有 I/O 需求后应可关闭")

        # ---- 关闭 I/O 唤醒：只走这一路，节点应能进 RSS ----
        panel.source_release_btn.click()
        self.pump(300)
        self.assertNotIn("IO", panel.demand_label.text())
        self.assertIn("无", panel.demand_label.text())
        self.assertEqual(self.window.local_status.state_short.text(), "RSS")
        self.assertFalse(panel.source_release_btn.isEnabled())

    def test_io_release_does_not_touch_kl15(self) -> None:
        """关闭 I/O 唤醒不能顺手把 KL15 也清掉 —— 这是成对开关的关键语义。"""
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)

        panel.kl15_btn.setChecked(True)
        self.window._on_network_request("IO", 3)
        self.pump(1600)
        self.assertIn("KL15", panel.demand_label.text())
        self.assertIn("IO", panel.demand_label.text())
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")

        panel.source_release_btn.click()
        self.pump(300)
        self.assertNotIn("IO", panel.demand_label.text())
        self.assertIn("KL15", panel.demand_label.text(), "关闭 I/O 不应清掉 KL15 保持事件")
        self.assertEqual(
            self.window.local_status.state_short.text(), "NOS", "KL15 还在，节点应停在 NOS"
        )

    def test_lifecycle_view_tracks_local_state(self) -> None:
        """生命周期流程图必须跟着本机节点状态走。"""
        machine = self.window.state_machine_view
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        self.assertEqual(machine.current_state_short, "BSM")

        self.window.control_panel.kl15_btn.setChecked(True)
        self.pump(150)
        self.assertEqual(machine.current_state_short, "RMS", "上电后应高亮 RMS")
        self.pump(1400)
        self.assertEqual(machine.current_state_short, "NOS", "T_REPEAT 超时后应高亮 NOS")

        self.window.control_panel.kl15_btn.setChecked(False)
        self.pump(250)
        self.assertEqual(machine.current_state_short, "RSS", "下电后应高亮 RSS")

    def test_diag_button_disabled_in_sleep_states(self) -> None:
        """BSM / PBS 下诊断报文不接收，按钮必须置灰，避免"点了没反应"。"""
        panel = self.window.control_panel
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(250)
        self.assertFalse(panel.diag_btn.isEnabled(), "BSM 下诊断按钮应置灰")

        panel.kl15_btn.setChecked(True)
        self.pump(1600)
        self.assertEqual(self.window.local_status.state_short.text(), "NOS")
        self.assertTrue(panel.diag_btn.isEnabled(), "NOS 下诊断按钮应可用")

        panel.kl15_btn.setChecked(False)
        self.pump(2300)
        self.assertIn(self.window.local_status.state_short.text(), {"PBS", "BSM"})
        self.assertFalse(panel.diag_btn.isEnabled(), "PBS/BSM 下诊断按钮应再次置灰")

    def test_worker_batches_logs_to_ui(self) -> None:
        """回归：曾经因为 _flush 里把 self._pending_logs = [] 重新绑定，
        订阅者持有的旧列表被抛弃，导致日志区只有最初几条。"""
        self.window.connect_simulation("CAN_BODY", 0x30, 500000)
        self.pump(500)
        self.window._on_network_request("KL15", 2)
        self.pump(1500)

        text = self.window.log_view.text.toPlainText()
        self.assertGreater(len(text.splitlines()), 20, "日志行数过少，批量推送可能又断了")
        self.assertGreater(self.window.frame_table.frame_model.row_count(), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
