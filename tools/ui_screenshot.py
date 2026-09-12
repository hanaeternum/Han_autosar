"""离屏渲染截图：不需要把窗口显示到屏幕上就能验证界面布局。

    python tools/ui_screenshot.py --out docs/ui_preview.png

实现要点：不要用 QT_QPA_PLATFORM=offscreen —— 那个平台插件在本机拿不到任何字体
（QFontDatabase().families() 返回 0），截出来的图上一片空白没有文字。
正确做法是用真实的 windows 平台插件，但给窗口加 WA_DontShowOnScreen 属性：
Qt 认为窗口"已显示"（布局激活、会收到 paint 事件），却不会真的映射到桌面。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from src.config.loader import load_config  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.theme import STYLESHEET  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="CanNm 上位机离屏截图")
    parser.add_argument("--out", default=str(ROOT / "docs" / "ui_preview.png"))
    parser.add_argument("--out-dialog", default=str(ROOT / "docs" / "ui_dialog_preview.png"))
    parser.add_argument("--width", type=int, default=1520)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--speed", type=float, default=5.0)
    parser.add_argument("--settle-ms", type=int, default=2600, help="截图前的真实等待时间")
    parser.add_argument("--with-release", action="store_true",
                        help="截图前先点一次「释放网络」，用于抓休眠阶段的画面")
    args = parser.parse_args()

    config = load_config()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    window = MainWindow(config)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.resize(args.width, args.height)
    window.show()

    steps: list = []

    def run_connect() -> None:
        panel = window.config_panel
        panel.channel_combo.setCurrentIndex(0)          # CAN_BODY
        index = panel.node_combo.findText("PEPS", Qt.MatchContains)
        panel.node_combo.setCurrentIndex(index if index >= 0 else 0)
        for i in range(window.speed_combo.count()):
            if abs(window.speed_combo.itemData(i) - args.speed) < 1e-6:
                window.speed_combo.setCurrentIndex(i)
                break
        window.connect_simulation("CAN_BODY", 0x30, 500000)
        steps.append("connected")

    def wake() -> None:
        window.control_panel.kl15_btn.setChecked(True)   # KL15 上电
        steps.append("kl15-on")

    def custom_frame() -> None:
        if window.worker:
            window.worker.inject_frame(
                "CAN_BODY", 0x470, bytes([0x70, 0x01, 0x10, 0x02, 0x05, 0x00, 0x00, 0x00]),
                note="演示：上位机注入 RMR=1 报文",
            )
        steps.append("inject")

    def release() -> None:
        window._on_network_release()
        steps.append("release")

    def shot_then_quit() -> None:
        image = window.grab()
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        image.save(args.out)
        steps.append(f"saved {image.width()}x{image.height()} -> {args.out}")

        # 顺带把自定义报文对话框也截一张，交付时能少问一轮"长什么样"
        from src.ui.custom_frame_dialog import CustomFrameDialog

        dialog = CustomFrameDialog(config, "CAN_BODY", 0x30)
        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.rmr_check.setChecked(True)
        dialog.awb_check.setChecked(True)
        dialog.keep_checks["ac_tmin"].setChecked(True)
        dialog.show()
        app.processEvents()
        dialog_image = dialog.grab()
        dialog_image.save(args.out_dialog)
        dialog.close()
        steps.append(f"saved dialog -> {args.out_dialog}")
        app.quit()

    # 截图时刻网络需求仍在，这样预览图里能看到控制区的"当前网络需求"指示与活跃状态
    schedule = [
        (80, run_connect),
        (300, wake),
        (1000, custom_frame),
        (args.settle_ms, shot_then_quit),
    ]
    if args.with_release:
        schedule.insert(-1, (int(args.settle_ms * 0.5), release))
    for delay, func in schedule:
        QTimer.singleShot(delay, func)

    code = app.exec_()
    print("执行步骤：" + " → ".join(steps), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

