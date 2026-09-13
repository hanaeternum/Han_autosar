"""核心状态机行为测试。

覆盖：唤醒时序、快速发送、T_NMTimeout 的重启语义、RMR 处理、
      Ready Sleep 的"推迟睡眠"、以及网络级 SleepIndication / SleepAcknowledge。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.params import CUSTOMER_A, NmProtocolParams  # noqa: E402
from src.core.events import LogBus, LogLevel  # noqa: E402
from src.core.network import NmEngine  # noqa: E402
from src.core.nm_state import NmState  # noqa: E402

TOL = 5   # ms，允许的时间戳抖动（由 1ms 节拍与总线传播时延引入）


class Recorder:
    """记录状态迁移与总线级事件，便于断言时序。"""

    def __init__(self, engine: NmEngine) -> None:
        self.transitions: list[tuple[int, str, str, NmState]] = []
        self.bus_events: list[tuple[int, str]] = []
        engine.log_bus.subscribe(self._on_event)

    def _on_event(self, event) -> None:
        if event.level is LogLevel.STATE and "dst" in event.detail:
            self.transitions.append(
                (event.time_ms, event.ecu, event.channel, NmState(event.detail["dst"]))
            )
        elif event.level is LogLevel.BUS and event.ecu == "BUS":
            self.bus_events.append((event.time_ms, event.message))

    def time_of(self, ecu: str, state: NmState, channel: str | None = None) -> int | None:
        for t, e, ch, s in self.transitions:
            if e == ecu and s is state and (channel is None or ch == channel):
                return t
        return None

    def assert_near(self, case: unittest.TestCase, actual: int | None, expected: int, label: str) -> None:
        case.assertIsNotNone(actual, f"{label} 未发生")
        case.assertLessEqual(abs(actual - expected), TOL, f"{label} 时刻 {actual}ms，期望 {expected}±{TOL}ms")


def make_engine(ecu_node_ids: list[tuple[str, int]], channel: str = "CAN_TEST",
                proto: NmProtocolParams | None = None) -> NmEngine:
    channels = [
        {
            "name": channel,
            "bitrate": 500000,
            "nodes": [{"ecu": ecu, "node_id": nid} for ecu, nid in ecu_node_ids],
        }
    ]
    return NmEngine(
        timing=replace(CUSTOMER_A),
        protocol=proto or NmProtocolParams(),
        channels=channels,
        log_bus=LogBus(),
    )


class TestSingleNodeLifecycle(unittest.TestCase):
    """单节点：本地唤醒 → RMS 快速发送 → NOS → RSS → PBS → BSM。"""

    def setUp(self) -> None:
        self.engine = make_engine([("A", 0x10)])
        self.node = self.engine.node("A", "CAN_TEST")
        self.rec = Recorder(self.engine)
        self.engine.run(100)

    def test_full_sequence(self):
        # t=100 本地唤醒
        self.node.can_nm_network_request("KL15", wakeup_reason=2)
        self.assertIs(self.node.state, NmState.REPEAT_MESSAGE)
        self.assertTrue(self.node.active_wakeup, "本地唤醒必须置 AWB=1")

        # 首帧必须在 T_WakeUp(100ms) 内发出
        self.engine.run(2)
        self.assertEqual(self.node.nm_tx_total, 1, "唤醒后应尽早在 T_WakeUp 内发出首帧")
        first_frame_ms = self.engine.now_ms

        # 快速发送：5 帧 @ T_ImmediateNmTimeout=100ms
        self.engine.run(500)
        self.assertEqual(self.node.nm_tx_total, 5, f"快速发送应为 5 帧，实际 {self.node.nm_tx_total}")
        # 第 6 帧要等到 1000ms 周期
        self.engine.run(600)   # t≈1202
        self.assertEqual(self.node.nm_tx_total, 5, "快速发送结束后不应再按 100ms 发")
        self.engine.run(300)   # t≈1502
        self.assertEqual(self.node.nm_tx_total, 6)

        # T_REPEAT_MESSAGE=1000ms 超时后，有网络需求 → NOS
        self.rec.assert_near(self, self.rec.time_of("A", NmState.NORMAL_OPERATION), first_frame_ms + 1000,
                             "RMS→NOS")

        # KL15 OFF → RSS
        self.engine.run(1)
        release_ms = self.engine.now_ms
        self.node.can_nm_network_release("KL15")
        self.assertIs(self.node.state, NmState.READY_SLEEP)
        self.assertEqual(self.node.nm_tx_total, 6, "进入 RSS 后不再有周期发送（仅补发一帧睡眠指示）")

        # RSS: T_NMTimeout=2000ms 无总线活动 → PBS
        self.engine.run(2000 + TOL)
        self.rec.assert_near(self, self.rec.time_of("A", NmState.PREPARE_BUS_SLEEP), release_ms + 2000,
                             "RSS→PBS")
        # PBS: T_WAIT_BUS_SLEEP=5000ms → BSM
        self.engine.run(5000 + TOL)
        self.rec.assert_near(self, self.rec.time_of("A", NmState.BUS_SLEEP), release_ms + 7000, "PBS→BSM")
        self.assertEqual(self.node.state_code, 0)
        self.assertTrue(all(not t["running"] for t in self.node.timers.snapshot().values()),
                        "进入 BSM 后所有定时器必须停止")

    def test_wakeup_reason_first_frame_is_init(self):
        """规范 4.6.6：唤醒原因首帧允许为 0，最迟第二帧必须为实际值。"""
        self.node.can_nm_network_request("KL15", wakeup_reason=2)
        self.engine.run(1)
        frames = [f for f in self.engine.bus_frames]
        self.assertEqual(frames[0]["data"][3], 0, "首帧唤醒原因应为 0（初始化）")
        self.engine.run(200)
        self.assertEqual(self.engine.bus_frames[1]["data"][3], 2, "第二帧应为实际唤醒原因 KL15")

    def test_state_code_in_byte2(self):
        """规范 4.6.5：从 BSM 进 RMS 的状态码为 1。"""
        self.node.can_nm_network_request("KL15", wakeup_reason=2)
        self.engine.run(1)
        self.assertEqual(self.engine.bus_frames[0]["data"][2], 1, "RMS<-BSM 状态码应为 1")

    def test_first_frame_timing_within_wakeup(self):
        self.node.can_nm_network_request("KL15", wakeup_reason=2)
        self.engine.run(1)
        self.assertLessEqual(self.engine.now_ms - 100, CUSTOMER_A.wakeup)


class TestTimeoutSemantics(unittest.TestCase):
    """T_NMTimeout 必须被每一帧收/发重启 —— 这是同睡同醒的关键。"""

    def test_rx_restarts_nm_timeout_and_delays_sleep(self):
        engine = make_engine([("A", 0x10), ("B", 0x20)])
        node_a = engine.node("A", "CAN_TEST")
        node_b = engine.node("B", "CAN_TEST")

        # A 主动唤醒，B 被动唤醒
        node_a.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(10)
        self.assertIs(node_b.state, NmState.REPEAT_MESSAGE)
        self.assertFalse(node_b.active_wakeup, "被动唤醒必须 AWB=0")

        # A 释放 → RSS；B 无需求也在 T_REPEAT_MESSAGE 后进 RSS
        node_a.can_nm_network_release("KL15")
        engine.run(1200)
        self.assertIs(node_a.state, NmState.READY_SLEEP)
        self.assertIs(node_b.state, NmState.READY_SLEEP)

        # B 仍然在发（模拟另一个节点还有需求）→ A 的 T_NMTimeout 被持续重启，不能入睡
        node_b.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(3000)
        self.assertIs(node_a.state, NmState.READY_SLEEP,
                      "RSS 期间只要还收到 NM 报文就必须推迟睡眠")

        # B 释放后总线静默，A 才开始 T_NMTimeout 计时
        node_b.can_nm_network_release("KL15")
        engine.run(2000 + TOL)
        self.assertIs(node_a.state, NmState.PREPARE_BUS_SLEEP)
        self.assertIs(node_b.state, NmState.PREPARE_BUS_SLEEP)

    def test_channel_sleep_events(self):
        engine = make_engine([("A", 0x10), ("B", 0x20)])
        rec = Recorder(engine)
        engine.node("A", "CAN_TEST").can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(50)
        self.assertEqual(rec.bus_events, [], "网络活跃期间不应出现睡眠事件")

        engine.node("A", "CAN_TEST").can_nm_network_release("KL15")
        engine.node("B", "CAN_TEST").can_nm_network_release("KL15")
        engine.run(9000)
        msgs = [m for _, m in rec.bus_events]
        self.assertTrue(any("SleepIndication" in m for m in msgs), "应置起 SleepIndication")
        self.assertTrue(any("SleepAcknowledge" in m for m in msgs), "应置起 SleepAcknowledge")
        self.assertTrue(
            msgs.index(next(m for m in msgs if "SleepIndication" in m))
            < msgs.index(next(m for m in msgs if "SleepAcknowledge" in m)),
            "SleepIndication 必须早于 SleepAcknowledge",
        )


class TestDiagWakeScope(unittest.TestCase):
    """诊断报文的作用范围：只能把 RSS 拉回 NOS，不能从 BSM / PBS 唤醒。

    依据庆铃规范表 4：BSM 与 PBS 下"应用报文 Rx"都是 N，即根本不接收诊断报文。
    """

    def test_diag_ignored_in_bus_sleep(self):
        engine = make_engine([("A", 0x10)])
        node = engine.node("A", "CAN_TEST")
        self.assertIs(node.state, NmState.BUS_SLEEP)
        self.assertFalse(node.report_diag_request(), "BSM 下诊断请求应被拒绝")
        engine.run(200)
        self.assertIs(node.state, NmState.BUS_SLEEP, "BSM 不应被诊断报文唤醒")

    def test_diag_ignored_in_prepare_bus_sleep(self):
        engine = make_engine([("A", 0x10)])
        node = engine.node("A", "CAN_TEST")
        node.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1300)
        node.can_nm_network_release()
        engine.run(2200)
        self.assertIs(node.state, NmState.PREPARE_BUS_SLEEP)
        self.assertFalse(node.report_diag_request(), "PBS 下诊断请求应被拒绝")
        engine.run(200)
        self.assertIs(node.state, NmState.PREPARE_BUS_SLEEP, "PBS 不应被诊断报文打断")

    def test_diag_wakes_ready_sleep(self):
        """对照：RSS 下诊断必须把节点拉回 NOS 并启动 T_WAIT_DiagReq。"""
        engine = make_engine([("A", 0x10)])
        node = engine.node("A", "CAN_TEST")
        node.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1300)
        node.can_nm_network_release()
        engine.run(50)
        self.assertIs(node.state, NmState.READY_SLEEP)
        self.assertTrue(node.report_diag_request())
        self.assertIs(node.state, NmState.NORMAL_OPERATION)
        self.assertIn("DIAG", node.keep_awake)
        self.assertTrue(node.timers.running("wait_diag_req"))

    def test_diag_wake_flag_allows_sleep_wake(self):
        """扩展开关：diag_can_wake_from_sleep=True 时允许从 BSM 直接进 NOS。"""
        engine = make_engine(
            [("A", 0x10)],
            proto=NmProtocolParams(diag_can_wake_from_sleep=True),
        )
        node = engine.node("A", "CAN_TEST")
        self.assertTrue(node.report_diag_request(), "开启开关后 BSM 下诊断应被接受")
        self.assertIs(node.state, NmState.NORMAL_OPERATION)
        self.assertIn("DIAG", node.keep_awake)
        self.assertTrue(node.timers.running("wait_diag_req"))


class TestAwbSemantics(unittest.TestCase):
    """规范 4.6.4：AWB 由本地唤醒置 1 后，必须保持到"进入预睡眠状态"才清零。

    曾经的 bug：进入 NOS 时就把 AWB 清了，导致 RMS 结束后报文里 AWB 立刻变 0 ——
    评审时会被问"你到底是不是主动唤醒网络的"。
    """

    def test_awb_held_from_rms_through_nos_until_rss(self):
        engine = make_engine([("A", 0x10)])
        node = engine.node("A", "CAN_TEST")

        node.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1)
        self.assertIs(node.state, NmState.REPEAT_MESSAGE)
        self.assertTrue(node.active_wakeup)

        engine.run(1200)   # T_REPEAT_MESSAGE 超时且有需求 → NOS
        self.assertIs(node.state, NmState.NORMAL_OPERATION)
        self.assertTrue(node.active_wakeup, "规范 4.6.4：进 NOS 后 AWB 仍须为 1")
        self.assertEqual(engine.bus_frames[-1]["data"][1] >> 4 & 1, 1, "报文 Byte1 bit4(AWB) 应为 1")

        # 收到 RMR=1 被拉回 RMS，AWB 也不应被清掉
        node.can_nm_repeat_message_request()
        engine.run(5)
        self.assertIs(node.state, NmState.REPEAT_MESSAGE)
        self.assertTrue(node.active_wakeup, "被远程 RMR 拉回 RMS 不应改变 AWB")

        # 进 RSS 才清零
        node.can_nm_network_release()
        engine.run(1200)
        self.assertIs(node.state, NmState.READY_SLEEP)
        self.assertFalse(node.active_wakeup, "进入 RSS 必须清零 AWB")

    def test_passive_wakeup_keeps_awb_zero(self):
        """规范 4.6.4：因接收到 NM 报文而进入 RMS 的节点，AWB 必须为 0。"""
        engine = make_engine([("A", 0x10), ("B", 0x20)])
        node_a = engine.node("A", "CAN_TEST")
        node_b = engine.node("B", "CAN_TEST")

        node_a.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1300)
        self.assertIs(node_a.state, NmState.NORMAL_OPERATION)
        self.assertTrue(node_a.active_wakeup)
        self.assertIs(node_b.state, NmState.READY_SLEEP)
        self.assertFalse(node_b.active_wakeup, "被动唤醒的节点 AWB 必须始终为 0")


class TestRepeatMessageRequest(unittest.TestCase):
    def test_local_rmr_sets_bit_and_fast_transmit(self):
        engine = make_engine([("A", 0x10)])
        node = engine.node("A", "CAN_TEST")
        node.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1200)   # 进入 NOS
        self.assertIs(node.state, NmState.NORMAL_OPERATION)

        node.can_nm_repeat_message_request()
        self.assertIs(node.state, NmState.REPEAT_MESSAGE, "RMR 请求应把 NOS 拉回 RMS")
        self.assertTrue(node.repeat_message_request, "本节点发出的 RMR 位应为 1")

        engine.run(1)
        self.assertEqual(engine.bus_frames[-1]["data"][1] & 0x01, 0x01, "Byte1 bit0(RMR) 应为 1")

        # 离开 RMS 后必须清零 RMR
        node.can_nm_network_release("KL15")
        engine.run(1200)
        self.assertIs(node.state, NmState.READY_SLEEP)
        self.assertFalse(node.repeat_message_request, "离开 RMS 必须清零 RMR")

    def test_remote_rmr_returns_node_to_rms(self):
        engine = make_engine([("A", 0x10), ("B", 0x20)])
        node_a = engine.node("A", "CAN_TEST")
        node_b = engine.node("B", "CAN_TEST")
        node_a.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(50)
        node_a.can_nm_network_release("KL15")
        engine.run(1200)
        self.assertIs(node_b.state, NmState.READY_SLEEP)

        node_a.can_nm_repeat_message_request()   # A 广播 RMR=1
        engine.run(5)
        self.assertIs(node_a.state, NmState.REPEAT_MESSAGE)
        self.assertIs(node_b.state, NmState.REPEAT_MESSAGE, "收到 RMR=1 的节点应回 RMS")


class TestGatewayIndependence(unittest.TestCase):
    """网关两个网段的 NM 状态必须相互独立。"""

    def test_two_channels_drive_independently(self):
        channels = [
            {"name": "CAN_BODY", "bitrate": 500000,
             "nodes": [{"ecu": "GW", "node_id": 16}, {"ecu": "PEPS", "node_id": 48}]},
            {"name": "CAN_INFO", "bitrate": 500000,
             "nodes": [{"ecu": "GW", "node_id": 16}, {"ecu": "IVI", "node_id": 80}]},
        ]
        engine = NmEngine(timing=replace(CUSTOMER_A), protocol=NmProtocolParams(),
                          channels=channels, log_bus=LogBus())

        engine.request_network("PEPS", "CAN_BODY", "KL15", reason=2)
        engine.run(200)
        self.assertIs(engine.node("GW", "CAN_BODY").state, NmState.REPEAT_MESSAGE)
        self.assertIs(engine.node("GW", "CAN_INFO").state, NmState.BUS_SLEEP,
                      "车身网被唤醒不应自动拉起信息网——NM 报文不跨网段")

        engine.request_network("GW", "CAN_INFO", "KL15", reason=2)
        engine.run(200)
        self.assertIs(engine.node("GW", "CAN_INFO").state, NmState.REPEAT_MESSAGE)
        self.assertIs(engine.node("IVI", "CAN_INFO").state, NmState.REPEAT_MESSAGE)


class TestDiagKeepAlive(unittest.TestCase):
    """规范 4.3.3.3：RSS 收到诊断报文 → NOS，T_WAIT_DiagReq 超时后回 RSS。"""

    def test_diag_keeps_network_then_releases(self):
        engine = make_engine([("A", 0x10)])
        rec = Recorder(engine)
        node = engine.node("A", "CAN_TEST")
        node.can_nm_network_request("KL15", wakeup_reason=2)
        engine.run(1200)
        node.can_nm_network_release("KL15")
        self.assertIs(node.state, NmState.READY_SLEEP)

        node.report_diag_request()
        self.assertIs(node.state, NmState.NORMAL_OPERATION, "诊断请求应把 RSS 拉回 NOS")
        engine.run(1000)
        self.assertIs(node.state, NmState.NORMAL_OPERATION, "T_WAIT_DiagReq=5000ms 内应保持 NOS")
        engine.run(4500)
        self.assertIs(node.state, NmState.READY_SLEEP, "诊断保持超时且无其它需求→RSS")
        self.assertIsNotNone(rec.time_of("A", NmState.READY_SLEEP))


if __name__ == "__main__":
    unittest.main(verbosity=2)
