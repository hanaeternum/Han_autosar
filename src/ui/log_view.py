"""Log 打印区。

性能要点：一批日志只做一次编辑操作（beginEditBlock + insertText），
而不是每条 appendHtml 一次 —— 后者在 25fps × 数十条/批的情况下会明显掉帧。
用 setMaximumBlockCount 给内存上界，长时间演示不会越跑越卡。

筛选变化时整体重画：日志缓存另存一份（_events），视图只负责渲染筛选结果。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.events import LogEvent
from .theme import LOG_COLORS

FILTER_OPTIONS = [
    ("全部日志", None),
    ("仅状态迁移", ("STATE",)),
    ("仅报文收发", ("TX", "RX")),
    ("仅定时器", ("TIMER",)),
    ("仅总线 / 告警", ("BUS", "WARN", "APP")),
]

MAX_CACHE = 6000
MAX_LINE_CHARS = 400


class LogView(QGroupBox):
    cleared = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("网络管理日志", parent)
        self._events: list[LogEvent] = []
        self._paused_buffer: list[LogEvent] = []
        self._paused = False

        self.filter_combo = QComboBox()
        for label, levels in FILTER_OPTIONS:
            self.filter_combo.addItem(label, levels)
        self.filter_combo.currentIndexChanged.connect(lambda _: self.refresh())

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)

        self.pause_btn = QPushButton("暂停刷新")
        self.pause_btn.setCheckable(True)
        self.clear_btn = QPushButton("清空")
        self.export_btn = QPushButton("导出")
        self.pause_btn.toggled.connect(self._on_pause)
        self.clear_btn.clicked.connect(self.clear)
        self.export_btn.clicked.connect(self._on_export)

        self.count_label = QLabel("0 条")
        self.count_label.setObjectName("hint")

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(self.filter_combo)
        toolbar.addWidget(self.autoscroll)
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)
        toolbar.addWidget(self.pause_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addWidget(self.export_btn)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_CACHE)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.text.setFont(font)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.addLayout(toolbar)
        root.addWidget(self.text, 1)

    # ==================================================================
    # 对外接口
    # ==================================================================
    def append_events(self, events: list[LogEvent]) -> None:
        if not events:
            return
        self._events.extend(events)
        if len(self._events) > MAX_CACHE:
            del self._events[: len(self._events) - MAX_CACHE]
        self.count_label.setText(f"{len(self._events)} 条")
        if self._paused:
            self._paused_buffer.extend(events)
            return
        self._write(self._filter(events))

    def refresh(self) -> None:
        """筛选条件变化后整体重画（仅用户操作时触发，不是每帧都做）。"""
        self.text.setUpdatesEnabled(False)
        self.text.clear()
        self._write(self._filter(self._events))
        self.text.setUpdatesEnabled(True)

    def clear(self) -> None:
        self.text.clear()
        self._events.clear()
        self._paused_buffer.clear()
        self.count_label.setText("0 条")
        self.cleared.emit()

    def export(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(self._format(e) for e in self._filter(self._events)))

    # ==================================================================
    # 内部
    # ==================================================================
    def _filter(self, events: list[LogEvent]) -> list[LogEvent]:
        levels = self.filter_combo.currentData()
        if not levels:
            return list(events)
        return [e for e in events if e.level.value in levels]

    def _write(self, events: list[LogEvent]) -> None:
        if not events:
            return
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        for event in events:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(LOG_COLORS.get(event.level.value, "#94A3B8")))
            cursor.insertText(self._format(event)[:MAX_LINE_CHARS] + "\n", fmt)
        cursor.endEditBlock()
        if self.autoscroll.isChecked():
            self.text.setTextCursor(cursor)
            self.text.ensureCursorVisible()

    def _format(self, event: LogEvent) -> str:
        return (
            f"[{event.time_ms:>7d}ms] "
            f"{event.channel:<9}/{event.ecu:<5} "
            f"{event.level.value:<5} "
            f"{event.message}"
        )

    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setText("继续刷新" if paused else "暂停刷新")
        if not paused and self._paused_buffer:
            buffered, self._paused_buffer = self._paused_buffer, []
            self._write(self._filter(buffered))

    def _on_export(self) -> None:
        default = f"cannm_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "导出网络管理日志", default, "文本文件 (*.txt)")
        if path:
            self.export(path)
