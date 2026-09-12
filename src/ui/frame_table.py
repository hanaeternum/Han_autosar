"""NM 报文监控表。

用 QAbstractTableModel + deque 实现，而不是 QTableWidget：
每秒可能上百帧，QTableWidget 逐行插入/删除会明显掉帧；
模型化之后用 beginInsertRows 增量更新，且环上界不会内存膨胀。

注意两点容易踩的坑：
1. 不能用 beginResetModel 刷新（会重置滚动位置和选中项，用户一滚动就被拽回去）；
2. deque 的 maxlen 自动淘汰会绕过模型的 beginRemoveRows，必须先手动淘汰再插入。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from ..core.nm_pdu import NM_STATE_CODE_LABELS, WAKEUP_REASON_LABELS
from .theme import BORDER, TEXT, TEXT_DIM

HEADERS = ["时间(ms)", "通道", "方向", "源节点", "CAN ID", "数据（8 Byte）", "解析"]
KEEP_AWAKE_BITS = ["KL15", "Diag", "Tmin", "Rmt", "OTA"]
DIRECTION_COLORS = {"TX": "#60A5FA", "RX": "#34D399", "HOST": "#FBBF24"}
DIRECTION_TEXT = {"TX": "发送", "RX": "接收", "HOST": "注入"}


class FrameTableModel(QAbstractTableModel):
    def __init__(self, max_rows: int = 500, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._rows: deque[dict[str, Any]] = deque(maxlen=max_rows)
        self._local_ecu = ""
        self._mono = QFont("Consolas")
        self._mono.setStyleHint(QFont.Monospace)
        self._mono.setPointSize(9)

    # ---------------- 数据 ----------------
    def set_local(self, ecu: str) -> None:
        """切换"本机节点"后，方向列的 TX/RX 判定要跟着变。"""
        self._local_ecu = ecu
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, len(HEADERS) - 1),
                [Qt.DisplayRole, Qt.ForegroundRole],
            )

    def append_frames(self, frames: list[dict[str, Any]]) -> None:
        if not frames:
            return
        max_rows = self._rows.maxlen or len(frames)
        new = list(frames)
        if len(new) >= max_rows:
            new = new[-max_rows:]

        overflow = len(self._rows) + len(new) - max_rows
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            for _ in range(overflow):
                self._rows.popleft()
            self.endRemoveRows()

        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(new) - 1)
        self._rows.extend(new)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def row_count(self) -> int:
        return len(self._rows)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return HEADERS[section]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        frame = self._rows[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            return self._text(frame, column)
        if role == Qt.ForegroundRole:
            if column == 2:
                return QColor(DIRECTION_COLORS.get(self._direction(frame), TEXT_DIM))
            if column in (1, 4):
                return QColor(TEXT_DIM)
            return QColor(TEXT)
        if role == Qt.FontRole and column in (3, 4, 5, 6):
            return self._mono
        if role == Qt.TextAlignmentRole and column in (0, 1, 2, 5):
            return int(Qt.AlignCenter)
        return None

    # ---------------- 内部 ----------------
    def _direction(self, frame: dict[str, Any]) -> str:
        if frame["ecu"] == "EXT":
            return "HOST"
        return "TX" if frame["ecu"] == self._local_ecu else "RX"

    def _text(self, frame: dict[str, Any], column: int) -> str:
        if column == 0:
            return str(frame["time_ms"])
        if column == 1:
            return frame["channel"]
        if column == 2:
            return DIRECTION_TEXT.get(self._direction(frame), "?")
        if column == 3:
            node = frame.get("node_id")
            return f"{frame['ecu']}(0x{node:02X})" if node is not None else frame["ecu"]
        if column == 4:
            return frame["can_id_hex"]
        if column == 5:
            return frame["data_hex"]
        return self.decode_frame(frame)

    @staticmethod
    def decode_frame(frame: dict[str, Any]) -> str:
        data = frame["data"]
        if len(data) < 5:
            return ""
        cbv = data[1]
        keep = [name for bit, name in enumerate(KEEP_AWAKE_BITS) if data[4] >> bit & 0x1]
        return (
            f"RMR{cbv & 1} AWB{(cbv >> 4) & 1} SLP{(cbv >> 3) & 1} | "
            f"ST={NM_STATE_CODE_LABELS.get(data[2], data[2])} "
            f"RSN={WAKEUP_REASON_LABELS.get(data[3], data[3])} "
            f"KEEP={'+'.join(keep) or '-'}"
        )


class FrameTableView(QTableView):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.frame_model = FrameTableModel(parent=self)
        self.setModel(self.frame_model)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(21)
        self.setStyleSheet(f"QTableView {{ border: 1px solid {BORDER}; }}")

        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        for column, width in enumerate([62, 72, 46, 100, 62, 190]):
            self.setColumnWidth(column, width)

    def set_local(self, ecu: str) -> None:
        self.frame_model.set_local(ecu)

    def append_frames(self, frames: list[dict[str, Any]]) -> None:
        stick = self._at_bottom()
        self.frame_model.append_frames(frames)
        if stick:
            self.scrollToBottom()

    def clear(self) -> None:
        self.frame_model.clear()

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2
