"""仿真日志事件与日志总线。

内核只负责往 LogBus 抛事件，不关心谁来消费（控制台 / PyQt 信号槽 / 文件）。
UI 层后面只要 subscribe 一个回调即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class LogLevel(Enum):
    TX = "TX"            # 本节点发出 NM 报文
    RX = "RX"            # 本节点收到 NM 报文
    STATE = "STATE"      # 状态迁移
    TIMER = "TIMER"      # 定时器超时
    BUS = "BUS"          # 总线级事件（睡眠指示/睡眠应答/busoff）
    APP = "APP"          # 应用层动作
    WARN = "WARN"        # 规范符合性告警


@dataclass
class LogEvent:
    time_ms: int
    level: LogLevel
    channel: str
    ecu: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        stamp = f"[{self.time_ms:>7d}ms]"
        where = f"{self.channel:<9}/{self.ecu:<5}"
        return f"{stamp} {where} {self.level.value:<5} {self.message}"


class LogBus:
    """极简发布订阅，同步回调。"""

    def __init__(self, capacity: int = 20000) -> None:
        self._subscribers: list[Callable[[LogEvent], None]] = []
        self.events: list[LogEvent] = []
        self.capacity = capacity

    def subscribe(self, callback: Callable[[LogEvent], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event: LogEvent) -> None:
        self.events.append(event)
        if len(self.events) > self.capacity:
            del self.events[: len(self.events) - self.capacity]
        for callback in self._subscribers:
            callback(event)

    def clear(self) -> None:
        self.events.clear()

    def text_lines(self) -> list[str]:
        return [e.format() for e in self.events]
