"""AUTOSAR CanNm 网络管理仿真上位机 —— 启动入口。

    python main.py                          # 用默认配置启动
    python main.py --speed 5                # 启动即 5 倍速
    python main.py --auto-connect           # 启动后自动连接第一个节点
    python main.py --channel CAN_INFO --node-id 0x50 --auto-connect
    python main.py --config my.json
    python main.py --smoke                  # 自检：不弹窗口，跑 3 秒后打印结果并退出
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 高分屏缩放必须在 QApplication 创建之前设置
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from src.config.loader import load_config  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.theme import STYLESHEET  # noqa: E402


def parse_node_id(text: str) -> int:
    """Node ID 统一按十六进制解析：'30' 与 '0x30' 等价。"""
    token = text.strip()
    try:
        return int(token, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无法解析 Node ID：{text!r}（按十六进制，如 30 或 0x30）")


def main() -> int:
    parser = argparse.ArgumentParser(description="AUTOSAR CanNm 网络管理仿真上位机")
    parser.add_argument("--config", default=None, help="配置文件路径，默认 config/network.json")
    parser.add_argument("--speed", type=float, default=1.0, help="仿真速度倍率 0.5/1/2/5/10，默认 1")
    parser.add_argument("--channel", default=None, help="启动时选中的 CAN 通道名，默认第一条")
    parser.add_argument("--node-id", type=parse_node_id, default=None,
                        help="启动时选中的 Node ID（十六进制），默认第一条通道的第一个节点")
    parser.add_argument("--auto-connect", action="store_true", help="启动后自动连接，省去手动点击")
    parser.add_argument("--smoke", action="store_true",
                        help="自检模式：不显示窗口，自动连接并行进若干秒后打印结果退出")
    parser.add_argument("--smoke-seconds", type=float, default=3.0, help="自检运行的时长（秒）")
    args = parser.parse_args()

    config = load_config(args.config)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("CanNm 仿真上位机")
    app.setStyleSheet(STYLESHEET)

    window = MainWindow(config)

    # 把命令行参数落到界面上，保证界面显示与启动参数一致
    if args.speed:
        for index in range(window.speed_combo.count()):
            if abs(window.speed_combo.itemData(index) - args.speed) < 1e-6:
                window.speed_combo.setCurrentIndex(index)
                break
    if args.channel:
        index = window.config_panel.channel_combo.findData(args.channel)
        if index < 0:
            parser.error(f"配置中不存在通道 {args.channel!r}，可选："
                         f"{[ch['name'] for ch in config.channels]}")
        window.config_panel.channel_combo.setCurrentIndex(index)
    if args.node_id is not None:
        panel = window.config_panel
        index = panel.node_combo.findData(args.node_id)
        if index < 0:
            available = "、".join(f"0x{n['node_id']:02X}" for n in config.channels[0]["nodes"])
            parser.error(f"通道 {panel.current_channel()} 上没有 Node ID 0x{args.node_id:02X}，"
                         f"当前拓扑可用：{available}")
        panel.node_combo.setCurrentIndex(index)

    if args.smoke:
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()

    if args.auto_connect or args.smoke:
        channel = window.config_panel.current_channel()
        node_id = window.config_panel.current_node_id()
        window.connect_simulation(channel, node_id, window.config_panel.current_baudrate())
        print(f"[启动] 已自动连接 {channel} / Node ID 0x{node_id:02X}，"
              f"速度 {window.speed_combo.currentText()}")

    if args.smoke:
        def finish() -> None:
            frames = window.frame_table.frame_model.row_count()
            lines = len(window.log_view.text.toPlainText().splitlines())
            state = window.local_status.state_short.text()
            print(f"[自检] {window.time_label.text()}｜本机状态 {state}｜"
                  f"报文表 {frames} 行｜日志 {lines} 行｜{window.thread_label.text()}")
            window.disconnect_simulation()
            ok = window.worker is None and window.local_status.state_short.text() == "未连接"
            print("[自检] 线程停止与界面复位：" + ("正常" if ok else "异常"))
            print("[自检] 结论：" + ("通过，环境可用" if ok and frames > 0 else "未通过，请检查上面的输出"))
            app.quit()

        # 自检时走一遍整车电源流程：KL15 上电 → KL15 下电
        QTimer.singleShot(300, lambda: window.control_panel.kl15_btn.setChecked(True))
        QTimer.singleShot(int(args.smoke_seconds * 500), lambda: window.control_panel.kl15_btn.setChecked(False))
        QTimer.singleShot(int(args.smoke_seconds * 1000), finish)

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
