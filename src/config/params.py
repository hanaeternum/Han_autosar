"""CanNm 定时参数与协议参数定义。

参数口径说明（两套并存，靠 profile 切换）：
  customer_a  ：本次客户口径 T_NMTxTimeout=1000ms / T_NMTimeout=2000ms / T_ImmediateNmTimeout=100ms
  qingling_v1 ：庆铃规范 V1.0 表4 T_NM_MessageCycle=200ms / T_NM_TIMEOUT=3000ms /
                T_NM_ImmediateCycleTime=20ms

默认使用 customer_a。切换只需改 config/network.json 里的 "profile"。
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class NmTimingParams:
    """CanNm 定时参数，单位统一 ms。字段名用 AUTOSAR 通用写法，别名见下方 property。"""

    tx_cycle: int = 1000          # T_NMTxTimeout / T_NM_MessageCycle：NM 报文正常发送周期
    nm_timeout: int = 2000        # T_NMTimeout：离网超时，RSS 下超时进 PBS
    immediate_cycle: int = 100    # T_ImmediateNmTimeout：快速发送周期
    immediate_times: int = 5      # N_ImmediateNM_TIMES：快速发送帧数
    repeat_message: int = 1000    # T_REPEAT_MESSAGE：RMS 持续时长
    wait_bus_sleep: int = 5000    # T_WAIT_BUS_SLEEP：PBS 持续时长
    wakeup: int = 100             # T_WakeUp：唤醒到首帧 NM 的最大允许时延
    start_app_tx: int = 20        # T_START_App_TX：首帧应用报文相对首帧 NM 的最大时延
    wait_diag_req: int = 5000     # T_WAIT_DiagReq：诊断请求带来的网络保持时长
    tx_offset: int = 0            # CanNmMsgCycleOffset：首帧 NM 发送偏移

    # ---- AUTOSAR / 庆铃规范命名别名，便于与客户文档逐条对照 ----
    @property
    def T_NM_MessageCycle(self) -> int:
        return self.tx_cycle

    @property
    def T_NMTxTimeout(self) -> int:
        return self.tx_cycle

    @property
    def T_NM_TIMEOUT(self) -> int:
        return self.nm_timeout

    @property
    def T_NMTimeout(self) -> int:
        return self.nm_timeout

    @property
    def T_NM_ImmediateCycleTime(self) -> int:
        return self.immediate_cycle

    @property
    def T_ImmediateNmTimeout(self) -> int:
        return self.immediate_cycle

    @property
    def T_REPEAT_MESSAGE(self) -> int:
        return self.repeat_message

    @property
    def T_WAIT_BUS_SLEEP(self) -> int:
        return self.wait_bus_sleep

    @property
    def T_WAKEUP(self) -> int:
        return self.wakeup

    def validate(self) -> list[str]:
        """返回参数一致性错误列表，空列表表示通过。"""
        errs: list[str] = []
        if self.nm_timeout <= self.tx_cycle:
            errs.append(
                f"T_NMTimeout({self.nm_timeout}ms) 必须大于发送周期 T_NMTxTimeout({self.tx_cycle}ms)，"
                "否则节点会在自己还在发报文时就被判离线"
            )
        if self.immediate_cycle >= self.tx_cycle:
            errs.append(
                f"T_ImmediateNmTimeout({self.immediate_cycle}ms) 应小于 T_NMTxTimeout({self.tx_cycle}ms)"
            )
        if self.repeat_message < self.immediate_cycle:
            errs.append(
                f"T_REPEAT_MESSAGE({self.repeat_message}ms) 应不小于 T_ImmediateNmTimeout({self.immediate_cycle}ms)"
            )
        if not 1 <= self.immediate_times <= 20:
            errs.append(f"N_ImmediateNM_TIMES({self.immediate_times}) 取值异常，常规为 3~10")
        if self.wakeup <= 0:
            errs.append("T_WakeUp 必须为正数")
        return errs

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class NmProtocolParams:
    """NM PDU 与协议行为参数。"""

    id_base: int = 0x400            # 报文 ID = id_base + NodeID
    id_extended: bool = False       # False = 11 位标准帧
    dlc: int = 8
    # 进入 RSS 后补发一帧「远程睡眠指示」报文（客户规范提到该 Bit，但 RSS 本身停发，
    # 因此需要这一帧才能把「我准备睡了」广播出去）。客户确认后可关。
    send_sleep_indication_on_rss: bool = True
    # RSS 收到本地唤醒是进 NOS（4.3.3.3）还是进 RMS 快速发送（部分网络章节 NM_15）？
    # 两份规范冲突，默认按主状态机章节进 NOS。置 True 走 NM_15 口径。
    rss_local_wakeup_to_rms: bool = False
    # 总线是否转播 NM 报文（NM 报文不跨网段路由，保持 False）
    route_nm_between_channels: bool = False
    # BSM / PBS 下是否允许诊断报文直接唤醒网络。
    # 规范表 4 里这两个状态的"应用报文 Rx"都是 N（根本不接收诊断报文），所以默认 False。
    # 置 True 后行为与 RSS 保持一致：直接进 NOS 并启动 T_WAIT_DiagReq。需客户确认。
    diag_can_wake_from_sleep: bool = False

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not 0 <= self.id_base <= 0x7F0:
            errs.append(f"id_base=0x{self.id_base:X} 超出 11 位标准帧可用范围")
        if self.dlc != 8:
            errs.append("当前 NM PDU 位布局按 8 字节定义，dlc 必须为 8")
        return errs


# --------------------------------------------------------------------------
# 参数档位
# --------------------------------------------------------------------------
CUSTOMER_A = NmTimingParams(
    tx_cycle=1000,
    nm_timeout=2000,
    immediate_cycle=100,
    immediate_times=5,
    repeat_message=1000,
    wait_bus_sleep=5000,
    wakeup=100,
    start_app_tx=20,
    wait_diag_req=5000,
)

QINGLING_V1 = NmTimingParams(
    tx_cycle=200,
    nm_timeout=3000,
    immediate_cycle=20,
    immediate_times=5,
    repeat_message=1000,
    wait_bus_sleep=5000,
    wakeup=100,
    start_app_tx=20,
    wait_diag_req=5000,
)

TIMING_PROFILES: dict[str, NmTimingParams] = {
    "customer_a": CUSTOMER_A,
    "qingling_v1": QINGLING_V1,
}

DEFAULT_PROFILE = "customer_a"


def get_timing(profile: str) -> NmTimingParams:
    if profile not in TIMING_PROFILES:
        raise KeyError(f"未知参数档位 {profile!r}，可选：{list(TIMING_PROFILES)}")
    return TIMING_PROFILES[profile]
