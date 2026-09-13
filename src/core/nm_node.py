"""单个 AUTOSAR CanNm 实例（一个 ECU 在一个网段上的网络管理实体）。

这是整个仿真的核心。它对应 AUTOSAR CanNm 模块的状态机：
  - 应用层接口（对应 CanNm_* API）：can_nm_init / can_nm_network_request /
    can_nm_network_release / can_nm_repeat_message_request
  - 事件驱动：所有状态迁移都由 handle(NmEvent) 依据 nm_state.TRANSITIONS 表完成
  - 时间驱动：tick() 推进定时器，产生发送需求
  - 总线上行：on_frame() 接收远端 NM PDU

一个节点实例 = 一个 (ECU, 网段)。网关横跨两个网段，因此有两个实例、两套独立状态。
"""

from __future__ import annotations

from typing import Any, Optional

from ..config.params import NmProtocolParams, NmTimingParams
from .can_frame import CanFrame
from .events import LogBus, LogLevel
from .nm_pdu import NM_STATE_CODE_LABELS, NmPdu, PduCodec
from .nm_state import (
    STATE_CODE_MAP,
    STATE_SHORT,
    TIMER_EVENT_MAP,
    NmEvent,
    NmState,
    resolve_transition,
)
from .timer import TimerSet

TIMER_LABELS = {
    "repeat_message": "T_REPEAT_MESSAGE",
    "nm_timeout": "T_NMTimeout",
    "wait_bus_sleep": "T_WAIT_BUS_SLEEP",
    "wait_diag_req": "T_WAIT_DiagReq",
}

KEEP_AWAKE_LABELS = {
    "KL15": "AC_0_KL15",
    "DIAG": "AC_1_Diagnosis",
    "REMOTE": "AC_3_Remotecontrol",
    "OTA": "AC_4_OTA",
}


class CanNmNode:
    def __init__(
        self,
        ecu: str,
        node_id: int,
        channel: str,
        timing: NmTimingParams,
        proto: NmProtocolParams,
        codec: PduCodec,
        log_bus: Optional[LogBus] = None,
    ) -> None:
        self.ecu = ecu
        self.node_id = node_id
        self.channel = channel
        self.timing = timing
        self.proto = proto
        self.codec = codec
        self.log_bus = log_bus

        # ---- 状态机 ----
        self.state = NmState.BUS_SLEEP
        self.state_code = 0
        self.keep_awake: set[str] = set()      # 本地网络需求来源（对应 AC_x 保持事件）
        self.repeat_message_request = False    # 本节点置位的 RMR
        self.active_wakeup = False             # AWB
        self.wakeup_reason = 0
        self._prev_state = NmState.BUS_SLEEP
        self._pending_init_reason = False      # 唤醒后首帧发唤醒原因 0

        # ---- 远端观测 ----
        self.remote_nodes: dict[int, dict[str, Any]] = {}
        self.remote_sleep_indicated: dict[int, bool] = {}

        # ---- 定时器 ----
        self.timers = TimerSet()
        self.timers.define("repeat_message", timing.repeat_message)
        self.timers.define("nm_timeout", timing.nm_timeout)
        self.timers.define("wait_bus_sleep", timing.wait_bus_sleep)
        self.timers.define("wait_diag_req", timing.wait_diag_req)

        # ---- 发送调度 ----
        self._tx_countdown: Optional[int] = None   # None = 当前不发送
        self._immediate_remaining = 0
        self._tx_pending: list[CanFrame] = []
        self._extra_tx_pending = False
        self.now_ms = 0
        self._wake_ms: Optional[int] = None
        self.nm_tx_count = 0        # 本次唤醒周期内的发送帧数（用于首帧判定）
        self.nm_tx_total = 0        # 累计发送帧数
        self.nm_rx_count = 0

    # =====================================================================
    # 应用层接口（对应 CanNm_* API）
    # =====================================================================
    def can_nm_init(self, now_ms: int = 0) -> None:
        self.now_ms = now_ms
        self.state = NmState.BUS_SLEEP
        self.state_code = 0
        self.keep_awake.clear()
        self.timers.stop_all()
        self._tx_countdown = None
        self._immediate_remaining = 0
        self._log(LogLevel.APP, "CanNm_Init()：进入 BusSleepMode，等待本地/远程唤醒")

    def can_nm_network_request(self, source: str = "KL15", wakeup_reason: int = 2) -> None:
        """本地唤醒：应用层状态有网络通信需求。"""
        if source not in self.keep_awake:
            self.keep_awake.add(source)
            self._log(LogLevel.APP, f"CanNm_NetworkRequest()：{KEEP_AWAKE_LABELS.get(source, source)} 置位")
        if self.state is NmState.BUS_SLEEP or self.state is NmState.PREPARE_BUS_SLEEP:
            self.wakeup_reason = wakeup_reason
        self.handle(NmEvent.LOCAL_WAKEUP)

    def can_nm_network_release(self, source: Optional[str] = None) -> None:
        """网络需求解除（如 KL15 OFF）。"""
        if source is None:
            self.keep_awake.clear()
        else:
            self.keep_awake.discard(source)
        self._log(
            LogLevel.APP,
            f"CanNm_NetworkRelease()：剩余需求 {sorted(self.keep_awake) or '无'}",
        )
        if not self.keep_awake:
            self.handle(NmEvent.NETWORK_RELEASE)

    def can_nm_repeat_message_request(self) -> None:
        self.handle(NmEvent.REPEAT_MESSAGE_REQUEST)

    def report_diag_request(self) -> bool:
        """收到诊断报文（规范 4.3.3.3）。

        返回是否被接受。BSM / PBS 下**不接收应用报文**（规范表 4），诊断报文无法唤醒网络，
        此时返回 False 并打一条告警 —— 真实车辆里诊断仪是靠唤醒收发器间接让 ECU 上电的，
        等效于先来一次本地唤醒，而不是诊断报文直接把 CanNm 拉起来。
        """
        accepted = self.handle(NmEvent.DIAG_REQUEST_RECEIVED)
        if not accepted:
            hint = (
                "如需演示诊断直接唤醒，请把 protocol.diag_can_wake_from_sleep 置为 true"
                if not self.proto.diag_can_wake_from_sleep else ""
            )
            self._log(
                LogLevel.WARN,
                f"诊断报文被忽略：{self.state.short} 状态下不接收应用报文（规范表 4）。"
                f"{hint or '真实车辆需先由本地事件或远程 NM 报文唤醒'}",
            )
        return accepted

    # =====================================================================
    # 事件派发
    # =====================================================================
    def handle(self, event: NmEvent, payload: Any = None) -> bool:
        tr = resolve_transition(self.state, event, self, payload)
        if tr is None:
            return False
        prev = self.state
        self._prev_state = prev
        if tr.dst is not prev:
            # 先落状态并打印迁移，再执行动作细节，日志顺序才符合阅读习惯
            self.state = tr.dst
            self._on_state_change(prev, tr)
        getattr(self, tr.action)(payload)
        return True

    def _on_state_change(self, prev: NmState, tr) -> None:
        code = STATE_CODE_MAP.get((prev, self.state))
        if code is not None:
            self.state_code = code
            code_text = f"｜状态码 {NM_STATE_CODE_LABELS.get(code, code)}"
        else:
            code_text = "｜状态码 无（规范未定义该迁移的状态码，沿用上一值）"
        self._log(
            LogLevel.STATE,
            f"{STATE_SHORT[prev]} → {STATE_SHORT[self.state]}  {self.state.label}"
            f"{code_text}｜{tr.note}",
            event=tr.event.value,
            src=prev.value,
            dst=self.state.value,
            src_ref=tr.src_ref,
        )

        # 离开 RMS 必须清零 RMR
        if prev is NmState.REPEAT_MESSAGE and self.state is not NmState.REPEAT_MESSAGE:
            self.repeat_message_request = False

        if self.state is NmState.REPEAT_MESSAGE:
            self.timers.start("nm_timeout")
            self.timers.start("repeat_message")
        elif self.state is NmState.NORMAL_OPERATION:
            self.timers.start("nm_timeout")
        elif self.state is NmState.READY_SLEEP:
            self.timers.start("nm_timeout")
        elif self.state is NmState.PREPARE_BUS_SLEEP:
            self.timers.stop("nm_timeout")
            self.timers.stop("repeat_message")
            self.timers.start("wait_bus_sleep")
        elif self.state is NmState.BUS_SLEEP:
            self.timers.stop_all()
            self.repeat_message_request = False
            self.active_wakeup = False
            self.state_code = 0
            self._nd_reset()

        # 发送调度
        if self.state in (NmState.REPEAT_MESSAGE, NmState.NORMAL_OPERATION):
            if self._tx_countdown is None:
                self._tx_countdown = self.timing.tx_offset
        else:
            self._tx_countdown = None
            self._immediate_remaining = 0

        # 网络模式内的 T_NMTimeout 一律重启（收/发报文也会重启，这里兜底状态进入）
        if self.state.is_network_mode:
            self.timers.restart("nm_timeout")

    # =====================================================================
    # 动作（由迁移表按名字调用，只设置标志与定时器，不改状态）
    # =====================================================================
    def action_init(self, payload: Any) -> None:
        pass

    def action_mark_release(self, payload: Any) -> None:
        pass

    def action_enter_rms_active(self, payload: Any) -> None:
        """本地唤醒进 RMS：AWB=1 + 快速发送机制。"""
        self.active_wakeup = True
        self.repeat_message_request = False
        self._immediate_remaining = self.timing.immediate_times
        self._tx_countdown = 0
        self._wake_ms = self.now_ms
        self._pending_init_reason = True
        self.nm_tx_count = 0
        self._log(
            LogLevel.APP,
            f"启用快速发送：{self.timing.immediate_times} 帧 @ {self.timing.immediate_cycle}ms，"
            f"之后转 {self.timing.tx_cycle}ms 周期",
        )

    def action_enter_rms_passive(self, payload: Any) -> None:
        """远程唤醒（收到 NM 报文）进 RMS：AWB=0 + 正常周期。"""
        self.active_wakeup = False
        self._immediate_remaining = 0
        self._tx_countdown = 0
        self._wake_ms = self.now_ms
        self._pending_init_reason = True
        self.wakeup_reason = 1
        self.nm_tx_count = 0
        self._log(LogLevel.BUS, "被动唤醒（远程 NM 报文），AWB=0，按正常周期发送")

    def action_enter_rms_from_remote_rmr(self, payload: Any) -> None:
        # AWB 保持原值：由本地唤醒置起的 AWB 要一直保持到 RSS，
        # 被远程 RMR 拉回 RMS 不改变"是不是本节点主动唤醒的"这个事实。
        self._immediate_remaining = 0
        self._tx_countdown = 0
        self.nm_tx_count = 0
        self.timers.start("repeat_message")
        self._log(LogLevel.BUS, "收到 RMR=1 的 NM 报文，重入 RMS 并重置 T_REPEAT_MESSAGE")

    def action_repeat_message_request_local(self, payload: Any) -> None:
        self.repeat_message_request = True
        self._immediate_remaining = self.timing.immediate_times
        self._tx_countdown = 0
        self.timers.start("repeat_message")
        if self._wake_ms is None:
            self._wake_ms = self.now_ms
        self.nm_tx_count = 0
        self._log(
            LogLevel.APP,
            f"CanNm_RepeatMessageRequest()：RMR 置位 + 快速发送 "
            f"{self.timing.immediate_times} 帧 @ {self.timing.immediate_cycle}ms",
        )

    def action_enter_nos(self, payload: Any) -> None:
        """进入 NOS。

        注意：**不要**在这里清 AWB。规范 4.6.4 要求 AWB 置 1 后"直到其进入预睡眠状态
        (ReadySleepState)"才清零 —— 也就是说 RMS→NOS 的过程中 AWB 必须一直保持为 1。
        AWB 的清零点只有两个：进入 RSS，或进入 BSM。
        """
        if self._prev_state is NmState.READY_SLEEP:
            self._wake_ms = self.now_ms
            self._log(LogLevel.APP, "本地唤醒事件使睡眠条件不再满足，重新开始发送 NM 报文")

    def action_enter_nos_diag(self, payload: Any) -> None:
        self.keep_awake.add("DIAG")
        self.timers.start("wait_diag_req")
        self.active_wakeup = False
        self._log(
            LogLevel.APP,
            f"收到诊断请求：进入 NOS 并启动 T_WAIT_DiagReq={self.timing.wait_diag_req}ms",
        )

    def action_restart_diag_timer(self, payload: Any) -> None:
        self.timers.restart("wait_diag_req")
        self._log(LogLevel.TIMER, f"诊断保持中，重启 T_WAIT_DiagReq={self.timing.wait_diag_req}ms")

    def action_enter_rss(self, payload: Any) -> None:
        """进入 RSS：停发 NM 报文，启动 T_NMTimeout 等待总线静默。"""
        self.repeat_message_request = False
        self.active_wakeup = False
        self._tx_countdown = None
        self._immediate_remaining = 0
        self.keep_awake.discard("DIAG")
        self.timers.stop("wait_diag_req")
        self._log(
            LogLevel.STATE,
            f"停止发送 NM 报文，启动 T_NMTimeout={self.timing.nm_timeout}ms 等待总线静默",
        )
        if self.proto.send_sleep_indication_on_rss:
            self._extra_tx_pending = True
            self._log(
                LogLevel.WARN,
                "补发最后一帧 NM PDU 并将 NM Remote Sleep Indication Bit 置 1"
                "（该行为当前为推断实现，客户确认后可关闭）",
            )

    def action_enter_pbs(self, payload: Any) -> None:
        self._tx_countdown = None
        self._log(
            LogLevel.STATE,
            f"总线已静默 T_NMTimeout，进入 PBS，启动 T_WAIT_BUS_SLEEP={self.timing.wait_bus_sleep}ms",
        )

    def action_enter_bsm(self, payload: Any) -> None:
        self._log(LogLevel.STATE, "总线睡眠，仅保留唤醒能力，等待本地或远程唤醒")

    def action_restart_repeat_message_timer(self, payload: Any) -> None:
        self.timers.restart("repeat_message")
        self._log(
            LogLevel.TIMER,
            f"重置 T_REPEAT_MESSAGE={self.timing.repeat_message}ms",
        )

    def action_restart_nm_timeout(self, payload: Any) -> None:
        self.timers.restart("nm_timeout")
        self._log(LogLevel.TIMER, f"重启 T_NMTimeout={self.timing.nm_timeout}ms")

    # =====================================================================
    # 周期任务
    # =====================================================================
    def tick(self, dt_ms: int, now_ms: int) -> list[CanFrame]:
        self.now_ms = now_ms

        for name in self.timers.tick(dt_ms):
            self._log(LogLevel.TIMER, f"{TIMER_LABELS.get(name, name)} 超时")
            self.handle(TIMER_EVENT_MAP[name])

        if self._extra_tx_pending:
            self._extra_tx_pending = False
            frame, pdu = self._build_frame(sleep_indication=True)
            self._tx_pending.append(frame)
            self.nm_tx_total += 1
            self._log(
                LogLevel.TX,
                f"→ {frame.id_hex} [{frame.data_hex}] {pdu.summary()}  （睡眠指示帧）",
                pdu=pdu.summary(),
            )

        if self._tx_countdown is not None and self.state in (
            NmState.REPEAT_MESSAGE,
            NmState.NORMAL_OPERATION,
        ):
            self._tx_countdown -= dt_ms
            if self._tx_countdown <= 0:
                self._transmit()

        frames = self._tx_pending
        self._tx_pending = []
        return frames

    def on_frame(self, frame: CanFrame, now_ms: int) -> None:
        """收到同网段远端 NM 报文。"""
        self.now_ms = now_ms
        pdu = self.codec.decode(frame)
        if pdu.source_node_id == self.node_id:
            return

        first_seen = pdu.source_node_id not in self.remote_nodes
        self.remote_nodes[pdu.source_node_id] = {
            "node_id": pdu.source_node_id,
            "ecu": frame.src_ecu,
            "last_seen_ms": now_ms,
            "rmr": pdu.repeat_message_request,
            "awb": pdu.active_wakeup,
            "sleep_indication": pdu.remote_sleep_indication,
            "wakeup_reason": pdu.wakeup_reason,
            "nm_state": pdu.nm_state,
        }
        self.remote_sleep_indicated[pdu.source_node_id] = pdu.remote_sleep_indication
        self.nm_rx_count += 1

        # 网络模式下，成功接收一帧 NM 报文即重启 T_NMTimeout
        if self.state.is_network_mode:
            self.timers.restart("nm_timeout")

        self._log(
            LogLevel.RX,
            f"← NodeID=0x{pdu.source_node_id:02X} {frame.id_hex} [{frame.data_hex}] {pdu.summary()}",
            pdu=pdu.summary(),
            src_node=pdu.source_node_id,
        )

        if self.state in (NmState.BUS_SLEEP, NmState.PREPARE_BUS_SLEEP) and first_seen:
            self._log(LogLevel.BUS, f"检测到总线活动（{frame.src_ecu} 的 NM 报文），准备被动唤醒")

        self.handle(NmEvent.NM_PDU_RECEIVED, pdu)

    # =====================================================================
    # 发送
    # =====================================================================
    def _transmit(self) -> None:
        frame, pdu = self._build_frame()
        self._tx_pending.append(frame)
        self.nm_tx_count += 1
        self.nm_tx_total += 1

        # 成功发送一帧 NM 报文 → 重启 T_NMTimeout
        self.timers.restart("nm_timeout")

        if self._wake_ms is not None and self.nm_tx_count == 1:
            latency = self.now_ms - self._wake_ms
            if latency > self.timing.wakeup:
                self._log(
                    LogLevel.WARN,
                    f"首帧 NM 时延 {latency}ms 超过 T_WakeUp={self.timing.wakeup}ms，不符合规范",
                )
            else:
                self._log(
                    LogLevel.APP,
                    f"唤醒后首帧 NM 在 {latency}ms 发出，满足 T_WakeUp={self.timing.wakeup}ms",
                )

        self._log(
            LogLevel.TX,
            f"→ {frame.id_hex} [{frame.data_hex}] {pdu.summary()}",
            pdu=pdu.summary(),
        )

        self._pending_init_reason = False
        if self._immediate_remaining > 0:
            self._immediate_remaining -= 1
        self._tx_countdown = self._next_tx_period()

    def _next_tx_period(self) -> int:
        if self.state is NmState.REPEAT_MESSAGE and self._immediate_remaining > 0:
            return self.timing.immediate_cycle
        return self.timing.tx_cycle

    def _build_frame(self, sleep_indication: bool = False) -> tuple[CanFrame, NmPdu]:
        pdu = NmPdu(
            source_node_id=self.node_id,
            repeat_message_request=self.repeat_message_request,
            remote_sleep_indication=sleep_indication,
            active_wakeup=self.active_wakeup,
            partial_network_info=False,
            nm_state=self.state_code,
            wakeup_reason=0 if self._pending_init_reason else self.wakeup_reason,
            ac_kl15="KL15" in self.keep_awake,
            ac_diagnosis="DIAG" in self.keep_awake,
            ac_tmin=self.state is NmState.REPEAT_MESSAGE and self.timers.running("repeat_message"),
            ac_remote_control="REMOTE" in self.keep_awake,
            ac_ota="OTA" in self.keep_awake,
        )
        frame = self.codec.encode_frame(pdu, channel=self.channel, src_ecu=self.ecu, time_ms=self.now_ms)
        return frame, pdu

    # =====================================================================
    # 辅助
    # =====================================================================
    def _nd_reset(self) -> None:
        self._pending_init_reason = False
        self._wake_ms = None
        self.nm_tx_count = 0
        self.remote_sleep_indicated = {k: False for k in self.remote_sleep_indicated}

    def _log(self, level: LogLevel, message: str, **detail: Any) -> None:
        if self.log_bus is None:
            return
        from .events import LogEvent

        self.log_bus.emit(
            LogEvent(
                time_ms=self.now_ms,
                level=level,
                channel=self.channel,
                ecu=self.ecu,
                message=message,
                detail=detail,
            )
        )

    @property
    def sends_nm(self) -> bool:
        return self.state in (NmState.REPEAT_MESSAGE, NmState.NORMAL_OPERATION)

    @property
    def next_tx_ms(self) -> Optional[int]:
        return None if self._tx_countdown is None else self.now_ms + self._tx_countdown

    def snapshot(self) -> dict[str, Any]:
        """给 UI 用的只读快照，后续 Qt 信号直接推这个。"""
        return {
            "ecu": self.ecu,
            "channel": self.channel,
            "node_id": self.node_id,
            "node_id_hex": f"0x{self.node_id:02X}",
            "can_id_hex": f"0x{self.proto.id_base + self.node_id:03X}",
            "state": self.state.value,
            "state_short": self.state.short,
            "state_label": self.state.label,
            "state_code": self.state_code,
            "keep_awake": sorted(self.keep_awake),
            "rmr": self.repeat_message_request,
            "awb": self.active_wakeup,
            "wakeup_reason": self.wakeup_reason,
            "nm_tx_count": self.nm_tx_count,
            "nm_tx_total": self.nm_tx_total,
            "nm_rx_count": self.nm_rx_count,
            "timers": self.timers.snapshot(),
            "remote_nodes": dict(self.remote_nodes),
            "remote_sleep_indicated": dict(self.remote_sleep_indicated),
        }
