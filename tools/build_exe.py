"""把仿真上位机打包成 Windows 可执行文件（PyInstaller）。

用法（在没有 PyInstaller 的环境里先装依赖）：

    pip install pyinstaller
    python tools/build_exe.py              # 打 GUI 版 + 控制台自检版
    python tools/build_exe.py --only gui   # 只打 GUI 版
    python tools/build_exe.py --onedir     # 打成目录而不是单文件（启动更快）

产物在 dist/ 下：

    CanNmSim.exe           双击即用，无控制台黑框
    CanNmSim-console.exe   带控制台，用来跑 --smoke 自检（GUI 版没有 stdout）

关于配置：config/network.json 会内嵌进 exe。如果把同名文件放在 exe 旁边，
程序优先读旁边这份 —— 改配置不用重新打包（见 src/config/paths.py）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 用不到的重型 Qt 模块，排除掉能显著减小体积
EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "PyQt5.QtWebEngine",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtQml",
    "PyQt5.QtQuick",
    "PyQt5.QtQuickWidgets",
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtBluetooth",
    "PyQt5.QtNfc",
    "PyQt5.QtPositioning",
    "PyQt5.QtSensors",
    "PyQt5.QtSerialPort",
    "PyQt5.QtSql",
    "PyQt5.QtTest",
    "PyQt5.QtDesigner",
]


def build(name: str, *, console: bool, onedir: bool, clean: bool) -> Path:
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", name,
        "--onefile" if not onedir else "--onedir",
        # 注意：--add-data 的源路径是相对 spec 文件所在目录解析的（不是 cwd），
        # 而 spec 放在 build/ 下，所以这里必须用绝对路径。
        "--add-data", f"{ROOT / 'config'}{os.pathsep}config",
        "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / name),
        "--specpath", str(ROOT / "build"),
    ]
    if not console:
        cmd.append("--windowed")     # 双击不弹控制台黑框
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    if clean:
        cmd.append("--clean")
    cmd.append("main.py")

    label = f"{name}（{'控制台' if console else 'GUI'}）"
    print(f"[打包] {label}\n       {' '.join(cmd[3:])}\n")
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    exe = ROOT / "dist" / name / f"{name}.exe" if onedir else ROOT / "dist" / f"{name}.exe"
    if exe.exists():
        print(f"[完成] {exe}　{exe.stat().st_size / 1024 / 1024:.1f} MB")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 AUTOSAR CanNm 仿真上位机")
    parser.add_argument("--only", choices=["gui", "console", "all"], default="all",
                        help="只打 GUI 版 / 控制台版 / 两个都打（默认）")
    parser.add_argument("--onedir", action="store_true",
                        help="打成目录而非单文件：启动更快，但要连目录一起分发")
    parser.add_argument("--no-clean", action="store_true", help="跳过 PyInstaller 缓存清理（重打包更快）")
    args = parser.parse_args()

    try:
        subprocess.run(
            [sys.executable, "-c", "import PyInstaller"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        print("当前解释器没有 PyInstaller，请先安装：\n"
              f"    {sys.executable} -m pip install pyinstaller", file=sys.stderr)
        return 1

    built: list[Path] = []
    if args.only in ("gui", "all"):
        built.append(build("CanNmSim", console=False, onedir=args.onedir, clean=not args.no_clean))
    if args.only in ("console", "all"):
        built.append(build("CanNmSim-console", console=True, onedir=args.onedir, clean=not args.no_clean))

    print("\n[提示] 自检用控制台版：\n"
          "    dist\\CanNmSim-console.exe --smoke\n"
          "[提示] 改配置不用重新打包：把 config\\network.json 复制到 exe 旁边再改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
