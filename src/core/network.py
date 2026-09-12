"""网络仿真引擎：多网段、多节点、统一时钟。

一个 tick 内的执行顺序（这是可复现性的关键）：
  1. 时钟 +1
  2. 各网段 poll 出已到达的帧，广播给除发送者外的所有节点
  3. 所有节点各自 tick：推进定时器、产生待发帧
  4. 收集全部待发帧后统一入总线队列
先「收」后「算」再「发」，保证同一 tick 内多个节点的动作互不干扰 —— 换成多线程就做不到。

NM 报文不跨网段路由（规范如此）。网关要拉起另一个网段，是应用层决定后再调用
自己在该网段的 NetworkRequest，这个联动由场景脚本完成，不藏在总线里。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Optional

from ..config.params import NmProtocolParams, NmTimingParams
from .can_frame import CanFrame, is_nm_can_id
from .events import LogBus, LogEvent, LogLevel
from .nm_node import CanNmNode
from .nm_pdu import PduCodec
from .virtual_bus import VirtualBus

EXTERNAL_ECU = "EXT"   # 上位机注入报文时的伪源节点名


class ChannelRuntime:
    def __init__(self, name: str, bitrate: int, bus: VirtualBus, nodes: list[CanNmNode]) -> None:
        self.name = name
        self.bitrate = bitrate
        self.bus = bus
        self.nodes = nodes
        self.sleep_indication_raised = False
        self.sleep_ack_raised = False
        self.ever_active = False     # 该网段是否曾经进入网络模式，避免上电瞬间误报睡眠事件


class NmEngine:
    def __init__(
        self,
        timing: NmTimingParams,
        protocol: NmProtocolParams,
        channels: list[dict[str, Any]],
        log_bus: Optional[LogBus] = None,
        tick_ms: int = 1,
        propagation_delay_ms: int = 0,
    ) -> None:
        self.timing = timing
        self.proto = protocol
        self.tick_ms = tick_ms
        self.now_ms = 0
        self.log_bus = log_bus or LogBus()
        self.codec = PduCodec(id_base=protocol.id_base, dlc=protocol.dlc)
        self.channels: dict[str, ChannelRuntime] = {}
        # 历史帧只保留最近 N 帧，长时间运行不会无限增长
        self.bus_frames: deque[dict[str, Any]] = deque(maxlen=2000)
        self.frame_subscribers: list[Callable[[dict[str, Any]], None]] = []

        for ch in channels:
            bus = VirtualBus(
                name=ch["name"],
                id_base=protocol.id_base,
                propagation_delay_ms=ch.get("propagation_delay_ms", propagation_delay_ms),
                drop_every=ch.get("drop_every", 0),
            )
            nodes = [
                CanNmNode(
                    ecu=n["ecu"],
                    node_id=n["node_id"],
                    channel=ch["name"],
                    timing=timing,
                    proto=protocol,
                    codec=self.codec,
                    log_bus=self.log_bus,
                )
                for n in ch["nodes"]
            ]
            bus.add_observer(self._on_bus_frame)
            self.channels[ch["name"]] = ChannelRuntime(ch["name"], ch.get("bitrate", 500000), bus, nodes)

        for runtime in self.channels.values():
            for node in runtime.nodes:
                node.can_nm_init(now_ms=0)

    # =====================================================================
    # 访问与操作
    # =====================================================================
    def node(self, ecu: str, channel: str) -> CanNmNode:
        for node in self.channels[channel].nodes:
            if node.ecu == ecu:
                return node
        raise KeyError(f"{channel} 上没有节点 {ecu}")

    def all_nodes(self) -> list[CanNmNode]:
        return [n for rt in self.channels.values() for n in rt.nodes]

    def request_network(self, ecu: str, channel: str, source: str = "KL15", reason: int = 2) -> None:
        self.node(ecu, channel).can_nm_network_request(source, reason)

    def release_network(self, ecu: str, channel: str, source: Optional[str] = None) -> None:
        self.node(ecu, channel).can_nm_network_release(source)

    def repeat_message_request(self, ecu: str, channel: str) -> None:
        self.node(ecu, channel).can_nm_repeat_message_request()

    def diag_request(self, ecu: str, channel: str) -> None:
        self.node(ecu, channel).report_diag_request()

    def inject_frame(self, channel: str, can_id: int, data: bytes) -> CanFrame:
        """上位机主动注入一帧 NM 报文（模拟外部测试节点 / 手动发报）。

        源 ECU 记为 EXT，因此不会被任何节点当作自身报文过滤掉，
        但节点仍会按 Byte0（源节点地址）判断是否是自己发的，避免自唤醒。
        """
        runtime = self.channels[channel]
        frame = CanFrame(
            can_id=can_id,
            data=bytes(data),
            channel=channel,
            src_ecu=EXTERNAL_ECU,
            tx_time_ms=self.now_ms,
            is_nm=True,
        )
        runtime.bus.send(frame, self.now_ms)
        self._on_bus_frame(frame)
        return frame

    def channel_names(self) -> list[str]:
        return list(self.channels)

    def nodes_of(self, channel: str) -> list[CanNmNode]:
        return list(self.channels[channel].nodes)

    def find_node(self, channel: str, node_id: int) -> Optional[CanNmNode]:
        for node in self.channels[channel].nodes:
            if node.node_id == node_id:
                return node
        return None

    # =====================================================================
    # 时钟
    # =====================================================================
    def tick(self) -> None:
        self.now_ms += self.tick_ms
        now = self.now_ms

        # 1) 收：总线到达帧广播给节点
        for runtime in self.channels.values():
            for frame in runtime.bus.poll(now):
                for node in runtime.nodes:
                    if node.ecu == frame.src_ecu:
                        continue
                    node.on_frame(frame, now)

        # 2) 算 + 3) 发：收集本 tick 所有待发帧后统一入队
        outgoing: list[tuple[VirtualBus, CanFrame]] = []
        for runtime in self.channels.values():
            for node in runtime.nodes:
                for frame in node.tick(self.tick_ms, now):
                    outgoing.append((runtime.bus, frame))
        for bus, frame in outgoing:
            bus.send(frame, now)

        # 4) 网络级睡眠仲裁
        for runtime in self.channels.values():
            self._arbitrate_sleep(runtime)

    def run(
        self,
        duration_ms: int,
        on_tick: Optional[Callable[["NmEngine"], None]] = None,
    ) -> None:
        target = self.now_ms + duration_ms
        while self.now_ms < target:
            self.tick()
            if on_tick is not None:
                on_tick(self)

    # =====================================================================
    # 网络级事件
    # =====================================================================
    def _arbitrate_sleep(self, runtime: ChannelRuntime) -> None:
        states = [n.state for n in runtime.nodes]
        if any(s.is_network_mode for s in states):
            runtime.ever_active = True

        if not runtime.ever_active:
            return

        all_ready = all(s.is_sleep_capable for s in states)
        all_asleep = all(s.is_asleep for s in states)

        if all_ready and not runtime.sleep_indication_raised:
            runtime.sleep_indication_raised = True
            self._bus_log(
                runtime.name,
                "网络级 SleepIndication 置起：所有节点均已进入 Ready Sleep / 预睡眠，网络可开始同步休眠",
            )
        elif not all_ready:
            runtime.sleep_indication_raised = False

        if all_asleep and not runtime.sleep_ack_raised:
            runtime.sleep_ack_raised = True
            self._bus_log(
                runtime.name,
                "网络级 SleepAcknowledge 置起：全部节点同意睡眠，总线即将完全静默",
            )
        elif not all_asleep:
            runtime.sleep_ack_raised = False

    def _on_bus_frame(self, frame: CanFrame) -> None:
        node_id = frame.can_id - self.proto.id_base if is_nm_can_id(self.proto.id_base, frame.can_id) else None
        item = {
            "time_ms": frame.tx_time_ms,
            "channel": frame.channel,
            "ecu": frame.src_ecu,
            "node_id": node_id,
            "can_id": frame.can_id,
            "can_id_hex": frame.id_hex,
            "data_hex": frame.data_hex,
            "data": list(frame.data),
        }
        self.bus_frames.append(item)
        for callback in self.frame_subscribers:
            callback(item)

    def subscribe_frames(self, callback: Callable[[dict], None]) -> None:
        """UI / 适配层订阅总线上的每一帧（用于报文监控表）。"""
        self.frame_subscribers.append(callback)

    def _bus_log(self, channel: str, message: str) -> None:
        self.log_bus.emit(
            LogEvent(time_ms=self.now_ms, level=LogLevel.BUS, channel=channel, ecu="BUS", message=message)
        )

    # =====================================================================
    # 快照
    # =====================================================================
    def snapshot(self) -> dict[str, Any]:
        return {
            "time_ms": self.now_ms,
            "channels": {
                name: {
                    "name": name,
                    "bitrate": rt.bitrate,
                    "bus": rt.bus.snapshot(),
                    "sleep_indication": rt.sleep_indication_raised,
                    "sleep_acknowledge": rt.sleep_ack_raised,
                    "nodes": [n.snapshot() for n in rt.nodes],
                }
                for name, rt in self.channels.items()
            },
        }
