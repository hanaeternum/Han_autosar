"""由仿真时钟驱动的软件定时器组。

设计要点：
- 定时器不自己起线程、不看墙钟，只接受 tick(dt) 推进 —— 这是仿真可复现的前提。
- tick() 返回本步「发生超时」的定时器名（上升沿），节点再把超时翻译成 NmEvent。
"""

from __future__ import annotations


class SimTimer:
    __slots__ = ("name", "duration", "remaining", "running")

    def __init__(self, name: str, duration: int = 0) -> None:
        self.name = name
        self.duration = duration
        self.remaining = 0
        self.running = False

    def start(self, duration: int | None = None) -> None:
        if duration is not None:
            self.duration = duration
        self.remaining = self.duration
        self.running = True

    def restart(self) -> None:
        self.remaining = self.duration
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.remaining = 0

    def tick(self, dt: int) -> bool:
        """推进 dt 毫秒。返回 True 表示本步内发生了超时（仅返回一次上升沿）。"""
        if not self.running:
            return False
        self.remaining -= dt
        if self.remaining <= 0:
            self.running = False
            self.remaining = 0
            return True
        return False

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        state = f"{self.remaining}ms" if self.running else "stopped"
        return f"<Timer {self.name} {state}>"


class TimerSet:
    """一组按名字索引的定时器。"""

    def __init__(self) -> None:
        self._timers: dict[str, SimTimer] = {}

    def define(self, name: str, duration: int = 0) -> SimTimer:
        timer = SimTimer(name, duration)
        self._timers[name] = timer
        return timer

    def __contains__(self, name: str) -> bool:
        return name in self._timers

    def __getitem__(self, name: str) -> SimTimer:
        return self._timers[name]

    def start(self, name: str, duration: int | None = None) -> None:
        self._timers[name].start(duration)

    def restart(self, name: str) -> None:
        self._timers[name].restart()

    def stop(self, name: str) -> None:
        self._timers[name].stop()

    def stop_all(self) -> None:
        for timer in self._timers.values():
            timer.stop()

    def running(self, name: str) -> bool:
        return self._timers[name].running

    def remaining(self, name: str) -> int:
        return self._timers[name].remaining

    def snapshot(self) -> dict[str, dict]:
        return {
            name: {"running": t.running, "remaining": t.remaining, "duration": t.duration}
            for name, t in self._timers.items()
        }

    def tick(self, dt: int) -> list[str]:
        expired: list[str] = []
        for name, timer in self._timers.items():
            if timer.tick(dt):
                expired.append(name)
        return expired
