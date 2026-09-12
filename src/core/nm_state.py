"""CanNm 状态、事件与迁移表。

迁移表是「单一事实来源」——声明式、可直接序列化给 UI 展示，测试也基于它校验。
每条迁移都带规范出处（src_ref），便于和客户规范逐条对照评审。

规范出处简写：
  4.3.x  = 庆铃规范《AUTOSAR CAN网络管理技术规范 V1.0》运行模式章节
  NM_xx  = 同规范部分网络管理状态迁移图条目（该章节标注「暂不实施」）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class NmState(Enum):
    BUS_SLEEP = "BusSleepMode"
    PREPARE_BUS_SLEEP = "PrepareBusSleepMode"
    REPEAT_MESSAGE = "RepeatMessageState"
    NORMAL_OPERATION = "NormalOperationState"
    READY_SLEEP = "ReadySleepState"

    @property
    def is_network_mode(self) -> bool:
        return self in _NETWORK_MODE_STATES

    @property
    def is_sleep_capable(self) -> bool:
        """是否处于「已无网络需求」一侧（RSS/PBS/BSM），用于网络级睡眠仲裁。"""
        return self in (NmState.READY_SLEEP, NmState.PREPARE_BUS_SLEEP, NmState.BUS_SLEEP)

    @property
    def is_asleep(self) -> bool:
        return self in (NmState.PREPARE_BUS_SLEEP, NmState.BUS_SLEEP)

    @property
    def label(self) -> str:
        return STATE_LABELS[self]

    @property
    def short(self) -> str:
        return STATE_SHORT[self]


_NETWORK_MODE_STATES = (
    NmState.REPEAT_MESSAGE,
    NmState.NORMAL_OPERATION,
    NmState.READY_SLEEP,
)

STATE_LABELS = {
    NmState.BUS_SLEEP: "总线睡眠模式",
    NmState.PREPARE_BUS_SLEEP: "准备总线睡眠模式",
    NmState.REPEAT_MESSAGE: "重复报文状态",
    NmState.NORMAL_OPERATION: "常规运行状态",
    NmState.READY_SLEEP: "预睡眠状态",
}

STATE_SHORT = {
    NmState.BUS_SLEEP: "BSM",
    NmState.PREPARE_BUS_SLEEP: "PBS",
    NmState.REPEAT_MESSAGE: "RMS",
    NmState.NORMAL_OPERATION: "NOS",
    NmState.READY_SLEEP: "RSS",
}


class NmEvent(Enum):
    INIT = "CanNm_Init"
    LOCAL_WAKEUP = "本地唤醒/NetworkRequest"
    NETWORK_RELEASE = "网络需求解除/NetworkRelease"
    REPEAT_MESSAGE_REQUEST = "RepeatMessageRequest()"
    NM_PDU_RECEIVED = "收到NM PDU"
    DIAG_REQUEST_RECEIVED = "收到诊断请求"
    T_REPEAT_MESSAGE_EXPIRED = "T_REPEAT_MESSAGE 超时"
    T_NM_TIMEOUT_EXPIRED = "T_NMTimeout 超时"
    T_WAIT_BUS_SLEEP_EXPIRED = "T_WAIT_BUS_SLEEP 超时"
    T_WAIT_DIAGREQ_EXPIRED = "T_WAIT_DiagReq 超时"


# 定时器名 -> 事件
TIMER_EVENT_MAP = {
    "repeat_message": NmEvent.T_REPEAT_MESSAGE_EXPIRED,
    "nm_timeout": NmEvent.T_NM_TIMEOUT_EXPIRED,
    "wait_bus_sleep": NmEvent.T_WAIT_BUS_SLEEP_EXPIRED,
    "wait_diag_req": NmEvent.T_WAIT_DIAGREQ_EXPIRED,
}


# --------------------------------------------------------------------------
# Byte2「网络管理状态」状态码
# --------------------------------------------------------------------------
# 注意：庆铃规范 4.6.5 中 16 与 32 的说明文字重复（均写作 RepeatMessageState<-ReadySleepState），
# 这里按上下文推断为 16 = RMS<-NOS、32 = RMS<-RSS，需网络组确认后再固化。
STATE_CODE_MAP: dict[tuple[NmState, NmState], int] = {
    (NmState.BUS_SLEEP, NmState.REPEAT_MESSAGE): 1,          # NM_RMS_frm_BSM
    (NmState.PREPARE_BUS_SLEEP, NmState.REPEAT_MESSAGE): 2,  # NM_RMS_frm_PBS
    (NmState.REPEAT_MESSAGE, NmState.NORMAL_OPERATION): 4,   # NM_NOS_frm_RMS
    (NmState.READY_SLEEP, NmState.NORMAL_OPERATION): 8,      # NM_NOS_frm_RSS
    (NmState.NORMAL_OPERATION, NmState.REPEAT_MESSAGE): 16,  # NM_RMS_frm_NOS（待确认）
    (NmState.READY_SLEEP, NmState.REPEAT_MESSAGE): 32,       # NM_RMS_frm_RSS（待确认）
}


# --------------------------------------------------------------------------
# 哨兵（guard）与动作（action）：都用字符串键，使迁移表保持纯数据
# --------------------------------------------------------------------------
def _g_always(node: Any, payload: Any) -> bool:
    return True


def _g_network_requested(node: Any, payload: Any) -> bool:
    return bool(node.keep_awake)


def _g_no_network_request(node: Any, payload: Any) -> bool:
    return not node.keep_awake


def _g_pdu_rmr(node: Any, payload: Any) -> bool:
    return bool(payload is not None and payload.repeat_message_request)


def _g_pdu_no_rmr(node: Any, payload: Any) -> bool:
    return not (payload is not None and payload.repeat_message_request)


def _g_rss_local_wakeup_to_nos(node: Any, payload: Any) -> bool:
    return not node.proto.rss_local_wakeup_to_rms


def _g_rss_local_wakeup_to_rms(node: Any, payload: Any) -> bool:
    return node.proto.rss_local_wakeup_to_rms


def _g_diag_only_demand(node: Any, payload: Any) -> bool:
    """诊断保持超时后是否还有别的网络需求。"""
    return not (node.keep_awake - {"DIAG"})


GUARDS = {
    "always": _g_always,
    "network_requested": _g_network_requested,
    "no_network_request": _g_no_network_request,
    "pdu_rmr": _g_pdu_rmr,
    "pdu_no_rmr": _g_pdu_no_rmr,
    "rss_local_wakeup_to_nos": _g_rss_local_wakeup_to_nos,
    "rss_local_wakeup_to_rms": _g_rss_local_wakeup_to_rms,
    "diag_only_demand": _g_diag_only_demand,
}


@dataclass(frozen=True)
class Transition:
    src: NmState
    event: NmEvent
    dst: NmState
    action: str          # CanNmNode 上的方法名
    guard: str = "always"
    src_ref: str = ""    # 规范出处
    note: str = ""       # 迁移条件说明（UI 上直接展示）


# 判定顺序即优先级：同一 (状态, 事件) 下按列表顺序取第一个满足哨兵的迁移。
TRANSITIONS: list[Transition] = [
    # ---------------- BusSleepMode ----------------
    Transition(NmState.BUS_SLEEP, NmEvent.INIT, NmState.BUS_SLEEP, "action_init",
               "always", "4.3", "上电初始化后必须停留在 BSM，直到被唤醒"),
    Transition(NmState.BUS_SLEEP, NmEvent.LOCAL_WAKEUP, NmState.REPEAT_MESSAGE, "action_enter_rms_active",
               "always", "4.3.3.1", "本地唤醒→RMS，AWB=1，启用快速发送"),
    Transition(NmState.BUS_SLEEP, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE, "action_enter_rms_passive",
               "always", "4.3.2", "收到 NM 报文→RMS，AWB=0，按正常周期发送"),
    Transition(NmState.BUS_SLEEP, NmEvent.NETWORK_RELEASE, NmState.BUS_SLEEP, "action_mark_release",
               "always", "4.3.1", "睡眠中释放网络需求，无状态变化"),

    # ---------------- PrepareBusSleepMode ----------------
    Transition(NmState.PREPARE_BUS_SLEEP, NmEvent.LOCAL_WAKEUP, NmState.REPEAT_MESSAGE, "action_enter_rms_active",
               "always", "4.3.2", "本地唤醒→RMS，AWB=1，启用快速发送"),
    Transition(NmState.PREPARE_BUS_SLEEP, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE, "action_enter_rms_passive",
               "always", "4.3.2", "收到 NM 报文→RMS，AWB=0，按正常周期发送"),
    Transition(NmState.PREPARE_BUS_SLEEP, NmEvent.T_WAIT_BUS_SLEEP_EXPIRED, NmState.BUS_SLEEP, "action_enter_bsm",
               "always", "4.3.2", "T_WAIT_BUS_SLEEP 超时→BSM"),
    Transition(NmState.PREPARE_BUS_SLEEP, NmEvent.NETWORK_RELEASE, NmState.PREPARE_BUS_SLEEP, "action_mark_release",
               "always", "4.3.2", "已准备睡眠，无状态变化"),

    # ---------------- RepeatMessageState ----------------
    Transition(NmState.REPEAT_MESSAGE, NmEvent.T_REPEAT_MESSAGE_EXPIRED, NmState.NORMAL_OPERATION, "action_enter_nos",
               "network_requested", "4.3.3.1", "T_REPEAT_MESSAGE 超时且有网络需求→NOS"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.T_REPEAT_MESSAGE_EXPIRED, NmState.READY_SLEEP, "action_enter_rss",
               "no_network_request", "4.3.3.1", "T_REPEAT_MESSAGE 超时且无网络需求→RSS，并清零 RMR"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE,
               "action_restart_repeat_message_timer", "pdu_rmr", "4.3.3.1",
               "收到 RMR=1 的 NM 报文→重置 T_REPEAT_MESSAGE"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE,
               "action_restart_nm_timeout", "pdu_no_rmr", "4.3.3.1", "收到 NM 报文→重启 T_NMTimeout"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.LOCAL_WAKEUP, NmState.REPEAT_MESSAGE,
               "action_restart_repeat_message_timer", "always", "NM_04",
               "新的本地事件→重置 T_REPEAT_MESSAGE，留在 RMS"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.REPEAT_MESSAGE_REQUEST, NmState.REPEAT_MESSAGE,
               "action_repeat_message_request_local", "always", "4.3.3.2",
               "RMR 置位 + 启用快速发送，重置 T_REPEAT_MESSAGE"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.NETWORK_RELEASE, NmState.REPEAT_MESSAGE, "action_mark_release",
               "always", "4.3.3.1", "RMS 内先保持发送，等 T_REPEAT_MESSAGE 超时再决断"),
    Transition(NmState.REPEAT_MESSAGE, NmEvent.T_NM_TIMEOUT_EXPIRED, NmState.REPEAT_MESSAGE,
               "action_restart_nm_timeout", "always", "4.3.3.1", "RMS 下 T_NMTimeout 溢出需重启"),

    # ---------------- NormalOperationState ----------------
    Transition(NmState.NORMAL_OPERATION, NmEvent.NETWORK_RELEASE, NmState.READY_SLEEP, "action_enter_rss",
               "always", "4.3.3.2", "本地睡眠条件满足→RSS，停止发送 NM 报文"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE,
               "action_enter_rms_from_remote_rmr", "pdu_rmr", "4.3.3.2", "收到 RMR=1→RMS，按正常周期发送"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.NM_PDU_RECEIVED, NmState.NORMAL_OPERATION,
               "action_restart_nm_timeout", "pdu_no_rmr", "4.3.3.2", "收到 NM 报文→重启 T_NMTimeout"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.REPEAT_MESSAGE_REQUEST, NmState.REPEAT_MESSAGE,
               "action_repeat_message_request_local", "always", "4.3.3.2",
               "RMR 置位 + 启用快速发送→RMS"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.T_NM_TIMEOUT_EXPIRED, NmState.NORMAL_OPERATION,
               "action_restart_nm_timeout", "always", "4.3.3.2", "NOS 下 T_NMTimeout 溢出需重启"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.DIAG_REQUEST_RECEIVED, NmState.NORMAL_OPERATION,
               "action_restart_diag_timer", "always", "4.3.3.3", "诊断保持中，重启 T_WAIT_DiagReq"),
    Transition(NmState.NORMAL_OPERATION, NmEvent.T_WAIT_DIAGREQ_EXPIRED, NmState.READY_SLEEP, "action_enter_rss",
               "diag_only_demand", "4.3.3.3", "诊断保持超时且无其它需求→RSS"),

    # ---------------- ReadySleepState ----------------
    Transition(NmState.READY_SLEEP, NmEvent.NM_PDU_RECEIVED, NmState.REPEAT_MESSAGE,
               "action_enter_rms_from_remote_rmr", "pdu_rmr", "4.3.3.3", "收到 RMR=1→RMS"),
    Transition(NmState.READY_SLEEP, NmEvent.NM_PDU_RECEIVED, NmState.READY_SLEEP,
               "action_restart_nm_timeout", "pdu_no_rmr", "4.3.3.3",
               "收到 NM 报文→重启 T_NMTimeout，继续留在 RSS（推迟睡眠）"),
    Transition(NmState.READY_SLEEP, NmEvent.T_NM_TIMEOUT_EXPIRED, NmState.PREPARE_BUS_SLEEP, "action_enter_pbs",
               "always", "4.3.3.3", "T_NMTimeout 超时→PBS"),
    Transition(NmState.READY_SLEEP, NmEvent.LOCAL_WAKEUP, NmState.NORMAL_OPERATION, "action_enter_nos",
               "rss_local_wakeup_to_nos", "4.3.3.3", "本地唤醒使睡眠条件不再满足→NOS"),
    Transition(NmState.READY_SLEEP, NmEvent.LOCAL_WAKEUP, NmState.REPEAT_MESSAGE, "action_enter_rms_active",
               "rss_local_wakeup_to_rms", "NM_15",
               "本地唤醒→RMS 快速发送（部分网络章节口径，与 4.3.3.3 冲突，默认关闭）"),
    Transition(NmState.READY_SLEEP, NmEvent.REPEAT_MESSAGE_REQUEST, NmState.REPEAT_MESSAGE,
               "action_repeat_message_request_local", "always", "4.3.3.3",
               "RMR 置位 + 启用快速发送→RMS"),
    Transition(NmState.READY_SLEEP, NmEvent.DIAG_REQUEST_RECEIVED, NmState.NORMAL_OPERATION,
               "action_enter_nos_diag", "always", "4.3.3.3",
               "收到诊断报文→NOS 并启动 T_WAIT_DiagReq"),
]


def resolve_transition(state: NmState, event: NmEvent, node: Any, payload: Any = None) -> Optional[Transition]:
    """按迁移表顺序取第一个满足哨兵的迁移；无匹配返回 None 表示事件被忽略。"""
    for tr in TRANSITIONS:
        if tr.src is state and tr.event is event:
            guard = GUARDS[tr.guard]
            if guard(node, payload):
                return tr
    return None


def transitions_for(state: NmState) -> list[Transition]:
    return [t for t in TRANSITIONS if t.src is state]


def transition_table_rows() -> list[dict]:
    """导出给 UI / 文档用的纯数据迁移表。"""
    return [
        {
            "src": t.src.short,
            "event": t.event.value,
            "dst": t.dst.short,
            "guard": t.guard,
            "condition": t.note,
            "src_ref": t.src_ref,
        }
        for t in TRANSITIONS
    ]
