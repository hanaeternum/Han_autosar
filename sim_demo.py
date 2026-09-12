"""无界面场景回放：把内核完整跑一遍并打印时间轴。

用途：在还没有 GUI 的阶段先证明状态机是对的，同时也作为后续 UI 的数据源参考。

    python sim_demo.py                     # 默认 customer_a 参数，跑完整睡眠流程
    python sim_demo.py --profile qingling_v1
    python sim_demo.py --summary-only      # 只打印状态时间线与统计
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.loader import build_engine, load_config  # noqa: E402
from src.config.params import get_timing  # noqa: E402
from src.core.events import LogBus, LogEvent, LogLevel  # noqa: E402
from src.core.nm_pdu import PduCodec  # noqa: E402

# ---------------------------------------------------------------------------
# 场景时间表（ms）—— 全部是对应用层的操作，不直接改状态
# ---------------------------------------------------------------------------
T_PEPS_WAKE = 1000       # PEPS 检测到 KL15，主动唤醒车身网
T_PEPS_RELEASE = 8000    # KL15 OFF，PEPS 释放网络
T_GW_INFO_RELEASE = 11000  # 网关释放信息网需求


class Scenario:
    """把「用户操作」按时间投喂给引擎，并处理网关的应用层跨网段联动。"""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.gw_bridge_armed = False
        self.gw_bridge_at = None
        self.gw_bridge_done = False

    def step(self, engine) -> None:
        now = engine.now_ms

        if now == T_PEPS_WAKE:
            engine.request_network("PEPS", "CAN_BODY", "KL15", reason=2)
        if now == T_PEPS_RELEASE:
            engine.release_network("PEPS", "CAN_BODY", "KL15")
        if now == T_GW_INFO_RELEASE:
            engine.release_network("GW", "CAN_INFO", "KL15")

        # 网关应用层联动：车身网进入网络模式后延时拉起信息网
        # （NM 报文本身不跨网段路由，这一步必须由应用层决定）
        if not self.gw_bridge_armed:
            if engine.node("GW", "CAN_BODY").state.is_network_mode:
                self.gw_bridge_armed = True
                self.gw_bridge_at = now + 200
        elif not self.gw_bridge_done and self.gw_bridge_at is not None and now >= self.gw_bridge_at:
            self.gw_bridge_done = True
            engine.request_network("GW", "CAN_INFO", "KL15", reason=2)
            engine.log_bus.emit(
                LogEvent(
                    time_ms=now,
                    level=LogLevel.APP,
                    channel="CAN_INFO",
                    ecu="GW",
                    message="网关应用层联动：车身网已唤醒，主动请求信息网",
                )
            )


def print_header(config) -> None:
    t = config.timing
    print("=" * 100)
    print("AUTOSAR CanNm 网络管理仿真（内核自检回放）")
    print("=" * 100)
    print(f"参数档位 : {config.profile}")
    print(
        f"定时参数 : T_NMTxTimeout={t.tx_cycle}ms  T_NMTimeout={t.nm_timeout}ms  "
        f"T_ImmediateNmTimeout={t.immediate_cycle}ms×{t.immediate_times}  "
        f"T_REPEAT_MESSAGE={t.repeat_message}ms  T_WAIT_BUS_SLEEP={t.wait_bus_sleep}ms"
    )
    print(f"报文 ID  : 0x{config.protocol.id_base:03X} + NodeID，DLC={config.protocol.dlc}")
    unconfirmed = [
        f"{f.name}@Byte{f.byte}.bit{f.bit}"
        for f in PduCodec(id_base=config.protocol.id_base).unconfirmed_fields()
    ]
    print(f"待确认位序: {', '.join(unconfirmed) or '无'}")
    print("-" * 100)
    for ch in config.channels:
        nodes = "  ".join(f"{n['ecu']}(0x{n['node_id']:02X})" for n in ch["nodes"])
        print(f"[{ch['name']}] {ch.get('bitrate', 500000) // 1000}kbps   {nodes}")
    print("=" * 100)
    print(f"场景：t={T_PEPS_WAKE}ms PEPS 本地唤醒 → t={T_PEPS_RELEASE}ms KL15 OFF → "
          f"t={T_GW_INFO_RELEASE}ms 网关释放信息网")
    print("=" * 100)


def print_timeline(engine) -> None:
    print()
    print("=" * 100)
    print("状态迁移时间线")
    print("=" * 100)
    for runtime in engine.channels.values():
        for node in runtime.nodes:
            marks = [
                e for e in engine.log_bus.events
                if e.level is LogLevel.STATE and e.ecu == node.ecu and e.channel == node.channel
                and "dst" in e.detail
            ]
            traj = " → ".join(f"{_short_from_event(e)}@{e.time_ms}ms" for e in marks)
            tail = f"BSM@0ms → {traj}" if traj else "BSM@0ms（未发生迁移）"
            print(f"{runtime.name:<9} {node.ecu:<5} {tail}")
    print("-" * 100)
    print("统计数据")
    print("-" * 100)
    for runtime in engine.channels.values():
        bus = runtime.bus.snapshot()
        print(f"[{runtime.name}] 总线 NM 报文 {bus['tx_frames']} 帧，丢帧 {bus['dropped_frames']}，"
              f"SleepIndication={runtime.sleep_indication_raised} "
              f"SleepAcknowledge={runtime.sleep_ack_raised}")
        for node in runtime.nodes:
            print(
                f"    {node.ecu:<5} 终态={node.state.short:<3} "
                f"TX={node.nm_tx_total:>3} RX={node.nm_rx_count:>3} "
                f"需求={sorted(node.keep_awake) or '无'}"
            )


def _short_from_event(event) -> str:
    dst = event.detail.get("dst", "")
    return {
        "BusSleepMode": "BSM",
        "PrepareBusSleepMode": "PBS",
        "RepeatMessageState": "RMS",
        "NormalOperationState": "NOS",
        "ReadySleepState": "RSS",
    }.get(dst, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="CanNm 内核场景回放")
    parser.add_argument("--profile", default=None, help="参数档位：customer_a / qingling_v1")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--duration", type=int, default=20000, help="仿真总时长(ms)")
    parser.add_argument("--summary-only", action="store_true", help="只打印时间线与统计")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.profile:
        config.profile = args.profile
        config.timing = replace(get_timing(args.profile))

    log_bus = LogBus()
    if not args.summary_only:
        log_bus.subscribe(lambda e: print(e.format()))

    print_header(config)
    engine = build_engine(config, log_bus=log_bus)
    scenario = Scenario(engine)
    engine.run(args.duration, on_tick=scenario.step)
    print_timeline(engine)


if __name__ == "__main__":
    main()
