"""整车网络拓扑视图：两条总线 + 网关双实例 + 各节点实时 NM 状态。

和 docs/拓扑图.svg 表达的是同一件事，区别是这张图是**活的**：
框线颜色 = 当前 NM 状态，另有
- 琥珀色脉冲环 = 该节点刚发出/收到报文（约 0.4 秒衰减，靠 25fps 的批量刷新自然重绘）
- 蓝色粗边 + "本机"角标 = 当前连接控制的那个节点
几何全部按控件尺寸相对计算，缩放窗口不会错位。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from ..core.nm_state import NmState
from .theme import STATE_COLORS, TEXT, TEXT_DIM, TEXT_FAINT

NODE_W = 138
NODE_H = 58
GW_W = 152
GW_H = 78
PULSE_SECONDS = 0.4
LOCAL_BORDER = "#2563EB"


class TopologyView(QWidget):
    def __init__(self, config: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self._channels: dict[str, dict[str, Any]] = {}
        self._order: list[str] = [ch["name"] for ch in config.channels]
        self._bitrate: dict[str, int] = {ch["name"]: ch.get("bitrate", 500000) for ch in config.channels}
        self._id_base: int = config.protocol.id_base
        self._local: Optional[tuple[str, int]] = None
        self._pulses: dict[tuple[str, int], float] = {}
        self._geometry: dict[str, Any] = {}
        self.setMinimumSize(430, 90 + 118 * max(1, len(self._order)))
        self.setMouseTracking(True)
        self._font_cache: dict[tuple[int, int], QFont] = {}

    # ==================================================================
    # 外部接口
    # ==================================================================
    def set_snapshot(self, channels: dict[str, dict[str, Any]], local: Optional[tuple[str, int]]) -> None:
        self._channels = channels
        self._local = local
        self.update()

    def set_frames(self, frames: list[dict[str, Any]]) -> None:
        """收到新报文时给相关节点打一个脉冲，让"总线上有报文"这件事看得见。"""
        if not frames:
            return
        expire = time.monotonic() + PULSE_SECONDS
        for frame in frames:
            node_id = frame.get("node_id")
            if node_id is None:
                continue
            self._pulses[(frame["channel"], node_id)] = expire
        self.update()

    def state_short_of(self, channel: str, node_id: int) -> str:
        snap = self._snap(channel, node_id)
        return snap["state_short"] if snap else "—"

    # ==================================================================
    # 几何
    # ==================================================================
    def _snap(self, channel: str, node_id: int) -> Optional[dict[str, Any]]:
        channel_snap = self._channels.get(channel)
        if not channel_snap:
            return None
        for node in channel_snap["nodes"]:
            if node["node_id"] == node_id:
                return node
        return None

    def _rebuild_geometry(self) -> None:
        w, h = self.width(), self.height()
        count = max(1, len(self._order))
        top = 0.26 * h
        span = 0.74 * h - top
        step = span / (count - 1) if count > 1 else 0.0

        buses: list[tuple[str, float, float, float]] = []
        nodes: dict[tuple[str, int], QRectF] = {}

        region_x0 = 208.0
        region_w = max(120.0, w - 26.0 - region_x0)

        for index, name in enumerate(self._order):
            bus_y = top + step * index
            buses.append((name, 40.0, bus_y, w - 22.0))
            node_list = [n for n in self._config_nodes(name)]
            n = len(node_list)
            for i, node in enumerate(node_list):
                cx = region_x0 + region_w * (i + 0.5) / n
                rect = QRectF(cx - NODE_W / 2, bus_y - 18 - NODE_H, NODE_W, NODE_H)
                if rect.top() < 2:                      # 空间不足时改画在总线下方
                    rect = QRectF(cx - NODE_W / 2, bus_y + 18, NODE_W, NODE_H)
                nodes[(name, node["node_id"])] = rect

        first_bus_y = buses[0][2]
        last_bus_y = buses[-1][2]
        gw_rect = QRectF(40.0, (first_bus_y + last_bus_y) / 2 - GW_H / 2, GW_W, GW_H)

        self._geometry = {"buses": buses, "nodes": nodes, "gw": gw_rect}

    def _config_nodes(self, channel: str) -> list[dict[str, Any]]:
        for ch in self.config.channels:
            if ch["name"] == channel:
                return list(ch["nodes"])
        return []

    # ==================================================================
    # 绘制
    # ==================================================================
    def _font(self, size: int, weight: int = 400) -> QFont:
        key = (size, weight)
        if key not in self._font_cache:
            font = QFont()
            font.setPointSizeF(size)
            font.setWeight(weight)
            self._font_cache[key] = font
        return self._font_cache[key]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._rebuild_geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        geo = self._geometry
        # ---------------- 总线 ----------------
        for name, x0, y, x1 in geo["buses"]:
            pen = QPen(QColor("#4B5563"), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))

            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(self._font(8))
            painter.drawText(
                QRectF(x0, y - 20, x1 - x0 - 4, 16),
                int(Qt.AlignRight | Qt.AlignVCenter),
                f"{name}　{self._bitrate.get(name, 500000) // 1000} kbps",
            )

        # ---------------- 网关 ----------------
        gw = geo["gw"]
        painter.setPen(QPen(QColor("#2563EB"), 1.8))
        painter.setBrush(QColor("#16233B"))
        painter.drawRoundedRect(gw, 10, 10)
        # 网关到两条总线的连线
        cx = gw.center().x()
        for name, x0, y, x1 in geo["buses"]:
            painter.setPen(QPen(QColor("#2563EB"), 1.8))
            painter.drawLine(QPointF(cx, y), QPointF(cx, gw.top() if y < gw.top() else gw.bottom()))

        painter.setPen(QColor("#93C5FD"))
        painter.setFont(self._font(11, 500))
        painter.drawText(QRectF(gw.x(), gw.y() + 10, gw.width(), 18),
                         int(Qt.AlignCenter), "GW 网关")
        painter.setPen(QColor("#60A5FA"))
        painter.setFont(self._font(7))
        painter.drawText(QRectF(gw.x(), gw.y() + 30, gw.width(), 14),
                         int(Qt.AlignCenter), f"NodeID 0x{self._gw_node_id():02X}")
        painter.setPen(QColor(TEXT_FAINT))
        painter.drawText(QRectF(gw.x() + 6, gw.y() + 46, gw.width() - 12, 24),
                         int(Qt.AlignCenter), "两个网段各一个\n独立 NM 实例")

        # ---------------- 节点 ----------------
        now = time.monotonic()
        for key, rect in geo["nodes"].items():
            channel, node_id = key
            snap = self._snap(channel, node_id)
            state = NmState(snap["state"]) if snap else None
            color = QColor(STATE_COLORS.get(state, "#4B5563"))
            is_local = key == self._local

            # 到总线的连线
            bus_y = next(y for name, _, y, _ in geo["buses"] if name == channel)
            painter.setPen(QPen(color, 1.8))
            above = rect.bottom() <= bus_y
            painter.drawLine(
                QPointF(rect.center().x(), rect.bottom() if above else rect.top()),
                QPointF(rect.center().x(), bus_y),
            )

            # 脉冲环：刚有报文收发
            expire = self._pulses.get(key, 0.0)
            if expire > now:
                alpha = int(150 * (expire - now) / PULSE_SECONDS)
                ring = QColor(color)
                ring.setAlpha(max(0, alpha))
                painter.setPen(QPen(ring, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 12, 12)

            painter.setPen(QPen(QColor(LOCAL_BORDER) if is_local else QColor("#39414F"), 2 if is_local else 1.4))
            painter.setBrush(QColor("#1B1E24"))
            painter.drawRoundedRect(rect, 9, 9)

            # 状态灯
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(rect.x() + 9, rect.y() + 10, 11, 11))

            name = snap["ecu"] if snap else str(node_id)
            painter.setPen(QColor(TEXT))
            painter.setFont(self._font(10, 500))
            painter.drawText(QRectF(rect.x() + 25, rect.y() + 7, rect.width() - 60, 17),
                             int(Qt.AlignLeft | Qt.AlignVCenter), name)

            if is_local:
                painter.setPen(QColor(LOCAL_BORDER))
                painter.setFont(self._font(7, 500))
                painter.drawText(QRectF(rect.right() - 34, rect.y() + 7, 28, 16),
                                 int(Qt.AlignRight | Qt.AlignVCenter), "本机")

            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(self._font(8))
            state_text = f"{snap['state_short']} {snap['state_label']}" if snap else "未连接"
            painter.drawText(QRectF(rect.x() + 9, rect.y() + 24, rect.width() - 18, 16),
                             int(Qt.AlignLeft | Qt.AlignVCenter), state_text)

            painter.setPen(QColor(TEXT_FAINT))
            painter.setFont(self._font(7))
            # 这里只放 NodeID 和定时器；CAN ID 放 tooltip，否则一行挤不下（悬停可见）
            painter.drawText(
                QRectF(rect.x() + 9, rect.y() + 39, rect.width() - 18, 15),
                int(Qt.AlignLeft | Qt.AlignVCenter),
                f"0x{node_id:02X}"
                + (f" · {self._nearest_timer(snap)}" if snap else ""),
            )

    @staticmethod
    def _nearest_timer(snap: dict[str, Any]) -> str:
        order = [("nm_timeout", "T_NMTimeout"), ("wait_bus_sleep", "T_WAIT_BUS"),
                 ("repeat_message", "T_REPEAT"), ("wait_diag_req", "T_DIAG")]
        for key, label in order:
            info = snap["timers"].get(key, {})
            if info.get("running"):
                return f"{label} {info['remaining']}ms"
        return "—"

    def _gw_node_id(self) -> int:
        for node in self._config_nodes(self._order[0]):
            if node["ecu"].upper() == "GW":
                return node["node_id"]
        return 0x10

    # ==================================================================
    # 悬停提示：把节点卡片里的细节挪到 tooltip，省掉一张表
    # ==================================================================
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.pos()
        for (channel, node_id), rect in self._geometry.get("nodes", {}).items():
            if rect.contains(QPointF(position)):
                self.setToolTip(self._tooltip(channel, node_id))
                break
        else:
            self.setToolTip("")

    def _tooltip(self, channel: str, node_id: int) -> str:
        snap = self._snap(channel, node_id)
        if not snap:
            return f"{channel} / NodeID 0x{node_id:02X}：暂无数据"
        timers = "、".join(
            f"{name} {info['remaining']}ms"
            for name, info in (
                ("T_REPEAT_MESSAGE", snap["timers"].get("repeat_message", {})),
                ("T_NMTimeout", snap["timers"].get("nm_timeout", {})),
                ("T_WAIT_BUS_SLEEP", snap["timers"].get("wait_bus_sleep", {})),
                ("T_WAIT_DiagReq", snap["timers"].get("wait_diag_req", {})),
            )
            if info.get("running")
        ) or "无运行中的定时器"
        demand = "+".join(snap["keep_awake"]) or "无"
        return (
            f"{snap['ecu']}　{channel}　NodeID {snap['node_id_hex']}（CAN ID {snap['can_id_hex']}）\n"
            f"状态：{snap['state_short']} {snap['state_label']}\n"
            f"标志位：RMR={int(snap['rmr'])}　AWB={int(snap['awb'])}　"
            f"上次唤醒原因={snap['wakeup_reason']}\n"
            f"本地网络需求：{demand}\n"
            f"定时器：{timers}\n"
            f"收发：TX {snap['nm_tx_total']} 帧 / RX {snap['nm_rx_count']} 帧　"
            f"已侦测远端节点 {len(snap['remote_nodes'])} 个"
        )
