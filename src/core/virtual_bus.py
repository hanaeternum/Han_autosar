"""虚拟 CAN 总线：只做广播、传播时延、可选的丢帧与 busoff 注入。

这里刻意不实现仲裁/位填充等物理层细节 —— 演示不需要，且会让时序不可复现。
将来要接真实硬件时，把 VirtualBus 换成 python-can 的 Bus 包装即可，
CanNmNode 只需要一个「能收到帧」的接口。
"""

from __future__ import annotations

from dataclasses import dataclass

from .can_frame import CanFrame, is_nm_can_id


@dataclass
class BusStats:
    tx_frames: int = 0
    rx_frames: int = 0
    dropped_frames: int = 0
    last_tx_ms: int = -1


class VirtualBus:
    """单网段总线。

    发送即入队，按 propagation_delay_ms 延迟后统一 poll 出来广播，
    保证「同一 tick 内多个节点发送」不会互相看到对方刚发的帧（贴近真实总线的时序）。
    """

    def __init__(
        self,
        name: str,
        id_base: int = 0x400,
        propagation_delay_ms: int = 0,
        drop_every: int = 0,
    ) -> None:
        self.name = name
        self.id_base = id_base
        self.propagation_delay_ms = propagation_delay_ms
        self.drop_every = drop_every     # >0 时每 N 帧丢一帧，用于演示容错
        self._queue: list[tuple[int, CanFrame]] = []
        self.bus_off = False
        self.stats = BusStats()
        self._tx_counter = 0
        # 旁观者（示波器 / UI 报文表），收到总线上出现的所有 NM 帧
        self._observers: list = []

    def add_observer(self, callback) -> None:
        self._observers.append(callback)

    @property
    def pending(self) -> int:
        return len(self._queue)

    def send(self, frame: CanFrame, now_ms: int) -> bool:
        """总线发送。busoff 或丢帧时返回 False。"""
        if self.bus_off:
            self.stats.dropped_frames += 1
            return False
        self._tx_counter += 1
        if self.drop_every and self._tx_counter % self.drop_every == 0:
            self.stats.dropped_frames += 1
            return False
        frame.channel = self.name
        self._queue.append((now_ms + self.propagation_delay_ms, frame))
        self.stats.tx_frames += 1
        self.stats.last_tx_ms = now_ms
        for observer in self._observers:
            observer(frame)
        return True

    def poll(self, now_ms: int) -> list[CanFrame]:
        """取出到期（已到达）的帧。"""
        arrived = [f for (t, f) in self._queue if t <= now_ms]
        self._queue = [(t, f) for (t, f) in self._queue if t > now_ms]
        self.stats.rx_frames += len(arrived)
        return arrived

    def idle_ms(self, now_ms: int) -> int:
        """距上一帧发送已过去多久（用于「总线静默」判定）。"""
        if self.stats.last_tx_ms < 0:
            return now_ms
        return now_ms - self.stats.last_tx_ms

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "tx_frames": self.stats.tx_frames,
            "dropped_frames": self.stats.dropped_frames,
            "pending": len(self._queue),
            "bus_off": self.bus_off,
        }


def frame_targets(frame: CanFrame, nodes, id_base: int) -> list:
    """过滤出应该收到该帧的节点：同网段、非发送者、ID 落在 NM 监测范围。"""
    if not is_nm_can_id(id_base, frame.can_id):
        return []
    return [n for n in nodes if n.ecu != frame.src_ecu]
