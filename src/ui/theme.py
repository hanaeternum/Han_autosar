"""界面配色与样式表。

深色配色，贴合车载测试上位机的习惯（长时间盯屏、暗环境）。
状态色与内核 NmState 一一对应，改这里就能改全局观感。
"""

from __future__ import annotations

from ..core.nm_state import NmState

BG_WINDOW = "#14161A"
BG_PANEL = "#1B1E24"
BG_INPUT = "#111318"
BORDER = "#2A2F39"
BORDER_LIGHT = "#3A4150"
TEXT = "#E6E8EB"
TEXT_DIM = "#9BA3AF"
TEXT_FAINT = "#6B7280"
ACCENT = "#3B82F6"

# 状态配色：BSM 灰暗 → PBS 灰 → RMS 琥珀（闪烁）→ NOS 绿 → RSS 蓝
STATE_COLORS: dict[NmState, str] = {
    NmState.BUS_SLEEP: "#4B5563",
    NmState.PREPARE_BUS_SLEEP: "#7C8798",
    NmState.REPEAT_MESSAGE: "#F59E0B",
    NmState.NORMAL_OPERATION: "#22C55E",
    NmState.READY_SLEEP: "#3B82F6",
}

LOG_COLORS = {
    "TX": "#60A5FA",
    "RX": "#34D399",
    "STATE": "#FBBF24",
    "TIMER": "#A78BFA",
    "BUS": "#F472B6",
    "APP": "#94A3B8",
    "WARN": "#F87171",
}

STYLESHEET = f"""
QWidget {{
    background-color: {BG_WINDOW};
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif;
    font-size: 12px;
}}
QMainWindow, QDialog {{ background-color: {BG_WINDOW}; }}

QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: 500;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 1px 6px;
    color: {TEXT_DIM};
    background-color: {BG_WINDOW};
    border-radius: 3px;
}}

QLabel {{ background: transparent; }}
QLabel#hint {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#sectionTitle {{ color: {TEXT_DIM}; font-size: 11px; }}

QPushButton {{
    background-color: #232833;
    border: 1px solid {BORDER_LIGHT};
    border-radius: 5px;
    padding: 6px 10px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: #2C323F; border-color: #4A5164; }}
QPushButton:pressed {{ background-color: #1A1E26; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background-color: #1A1D23; border-color: {BORDER}; }}
QPushButton#primary {{
    background-color: #1D4ED8; border-color: #2563EB; font-weight: 500;
}}
QPushButton#primary:hover {{ background-color: #2563EB; }}
QPushButton#danger {{ background-color: #7F1D1D; border-color: #B91C1C; }}
QPushButton#danger:hover {{ background-color: #991B1B; }}
QPushButton#ghost {{ background-color: transparent; }}

QComboBox, QLineEdit, QSpinBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 5px;
    padding: 5px 7px;
    color: {TEXT};
    min-height: 18px;
}}
QComboBox:hover, QLineEdit:hover {{ border-color: #55607A; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {TEXT_FAINT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER_LIGHT};
    selection-background-color: #1D4ED8;
    outline: none;
}}

QTableView {{
    background-color: {BG_INPUT};
    alternate-background-color: #161A20;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 5px;
    selection-background-color: #1D4ED8;
    selection-color: #FFFFFF;
}}
QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 5px 6px;
    font-weight: 500;
}}
QTableView QTableCornerButton::section {{ background-color: {BG_PANEL}; border: none; }}

QPlainTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    color: {TEXT};
    selection-background-color: #1D4ED8;
}}

QCheckBox {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {BORDER_LIGHT};
    border-radius: 3px;
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}

QSplitter::handle {{ background-color: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3A4150; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #4A5164; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3A4150; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background-color: {BG_PANEL}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}

QToolBar {{ background-color: {BG_PANEL}; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 4px 6px; }}
QToolBar QLabel {{ color: {TEXT_DIM}; }}
QToolButton {{ color: {TEXT}; padding: 4px 8px; border-radius: 4px; }}
QToolButton:hover {{ background-color: #2C323F; }}
"""
