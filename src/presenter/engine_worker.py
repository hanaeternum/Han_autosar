"""QThread 适配层：把仿真内核搬离 UI 线程。

为什么这样设计（不是"随便套个线程"）：

1. **内核仍然单线程**。引擎在 worker 线程里跑，内部仍是一个 1ms 节拍的
   固定顺序循环（收→算→发→仲裁），迁移顺序完全可复现。多线程只用在
   "内核线程 ↔ UI 线程"这一条边界上。

2. **按墙钟累积推进，而不是按循环次数**。循环里算「距上次跑了多少真实时间 × 倍率」，
   折算成仿真毫秒数一次性批量推进。所以就算操作系统把 sleep 睡过头了，
   仿真时间轴也不会漂移。

3. **批量推送，而不是逐事件发信号**。1ms 节拍下每秒可能产生上千个事件，
   逐条 emit 信号会把 UI 线程的事件队列压垮（这才是"卡死"的真正原因）。
   这里每 40ms 汇总一批（日志 + 报文 + 状态快照）一次推过去，UI 侧每批只做一次
   HTML 插入和一次表格刷新。

4. **UI → 内核用命令队列**。跨线程直接调内核方法会有竞态；这里所有操作
   只向 queue.Queue 投递闭包，由内核线程在循环开头统一取出执行。
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from ..config.loader import NetworkConfig, build_engine
from ..core.events import LogBus, LogEvent, LogLevel


@dataclass
class BatchPayload:
    """一次推送给 UI 的汇总数据。"""

    sim_time_ms: int
    logs: list[LogEvent] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    bus_stats: dict[str, Any] = field(default_factory=dict)
    speed: float = 1.0
    paused: bool = False


class EngineWorker(QThread):
    """仿真内核线程。"""

    batch = pyqtSignal(object)        # BatchPayload
    error = pyqtSignal(str)
    runningChanged = pyqtSignal(bool)

    TICK_MS = 1
    PUSH_INTERVAL_S = 0.04            # 40ms 推一次，约 25fps
    MAX_TICKS_PER_LOOP = 2000         # 单次最多推进 2 秒仿真时间，防止卡顿后暴走
    IDLE_SLEEP_S = 0.001

    def __init__(self, config: NetworkConfig, speed: float = 1.0, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.engine = None
        self._speed = float(speed)
        self._paused = False
        self._stopping = False
        self._commands: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._pending_logs: list[LogEvent] = []
        self._pending_frames: list[dict[str, Any]] = []

    # =====================================================================
    # 供 UI 线程调用的命令接口（全部走队列，不直接碰内核）
    # =====================================================================
    def submit(self, action: Callable[[], None]) -> None:
        self._commands.put(action)

    def request_network(self, channel: str, node_id: int, source: str = "KL15", reason: int = 2) -> None:
        def action() -> None:
            node = self._require_node(channel, node_id)
            node.can_nm_network_request(source, reason)

        self.submit(action)

    def release_network(self, channel: str, node_id: int, source: Optional[str] = None) -> None:
        def action() -> None:
            node = self._require_node(channel, node_id)
            node.can_nm_network_release(source)

        self.submit(action)

    def repeat_message_request(self, channel: str, node_id: int) -> None:
        def action() -> None:
            self._require_node(channel, node_id).can_nm_repeat_message_request()

        self.submit(action)

    def diag_request(self, channel: str, node_id: int) -> None:
        def action() -> None:
            self._require_node(channel, node_id).report_diag_request()

        self.submit(action)

    def inject_frame(self, channel: str, can_id: int, data: bytes, note: str = "") -> None:
        def action() -> None:
            if self.engine is None:
                return
            self.engine.inject_frame(channel, can_id, data)
            if note:
                self.engine.log_bus.emit(
                    LogEvent(
                        time_ms=self.engine.now_ms,
                        level=LogLevel.APP,
                        channel=channel,
                        ecu="HOST",
                        message=f"上位机手动注入 NM 报文：{note}",
                    )
                )

        self.submit(action)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    def stop(self) -> None:
        self._stopping = True

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        return self._paused

    # =====================================================================
    # 线程主体
    # =====================================================================
    def run(self) -> None:  # noqa: C901 - 循环体本身就很直白，拆开反而难读
        try:
            log_bus = LogBus(capacity=5000)
            log_bus.subscribe(self._pending_logs.append)
            engine = build_engine(self.config, log_bus=log_bus, tick_ms=self.TICK_MS)
            engine.subscribe_frames(self._pending_frames.append)
            engine.now_ms = 0
            self.engine = engine
        except Exception as exc:  # pragma: no cover - 只在配置错误时触发
            self.error.emit(f"仿真内核启动失败：{exc}")
            return

        self.runningChanged.emit(True)
        accumulator = 0.0
        last_wall = time.monotonic()
        next_push = last_wall

        while not self._stopping:
            # 1) 处理 UI 下发的命令
            while True:
                try:
                    self._commands.get_nowait()()
                except queue.Empty:
                    break
                except Exception as exc:  # 命令执行异常不能拖垮线程
                    self.error.emit(f"命令执行失败：{exc}")

            now = time.monotonic()
            elapsed = now - last_wall
            last_wall = now

            # 2) 按墙钟累积推进仿真时间（暂停时只推进 UI 刷新）
            if not self._paused:
                accumulator += elapsed * 1000.0 * self._speed
                ticks = int(accumulator)
                accumulator -= ticks
                ticks = min(ticks, self.MAX_TICKS_PER_LOOP)
                for _ in range(ticks):
                    self.engine.tick()

            # 3) 定时向 UI 推送一批
            if now >= next_push:
                next_push = now + self.PUSH_INTERVAL_S
                self._flush()

            time.sleep(self.IDLE_SLEEP_S)

        self._flush()
        self.runningChanged.emit(False)

    # =====================================================================
    # 内部
    # =====================================================================
    def _flush(self) -> None:
        if self.engine is None:
            return
        # 注意：必须用 list(...) 拷贝 + clear()，不能写 self._pending_logs = []。
        # 订阅者（log_bus / bus observer）持有的是**列表对象本身**，
        # 一旦重新绑定属性，后续事件就会进到一个没人读的新列表里 —— 日志表会莫名变空。
        payload = BatchPayload(
            sim_time_ms=self.engine.now_ms,
            logs=list(self._pending_logs),
            frames=list(self._pending_frames),
            snapshot=self.engine.snapshot(),
            bus_stats={name: rt.bus.snapshot() for name, rt in self.engine.channels.items()},
            speed=self._speed,
            paused=self._paused,
        )
        self._pending_logs.clear()
        self._pending_frames.clear()
        self.batch.emit(payload)

    def _require_node(self, channel: str, node_id: int):
        if self.engine is None:
            raise RuntimeError("仿真内核尚未启动")
        node = self.engine.find_node(channel, node_id)
        if node is None:
            raise KeyError(f"{channel} 上不存在 NodeID=0x{node_id:02X} 的节点")
        return node
