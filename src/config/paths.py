"""资源路径解析：源码运行与 PyInstaller 打包后都指向正确位置。

打包后（onefile）程序运行在临时解压目录 ``sys._MEIPASS`` 里，
``Path(__file__).parents[2]`` 会指到临时目录外面去，必须换一套算法。

查找顺序刻意让**外部文件优先**：把 config/network.json 放在 exe 旁边就能
改配置，不用重新打包；删掉它则回落到打包时内嵌的那份。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller / cx_Freeze 打包出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """只读资源的根目录。

    - 源码运行：项目根目录（src/config/paths.py 往上三级）
    - 打包运行：临时解压目录 sys._MEIPASS
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def writable_dir() -> Path:
    """可写目录，用来找"允许用户自己改"的外部文件。

    - 源码运行：项目根目录
    - 打包运行：exe 所在目录
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resolve_data(relative: str) -> Path:
    """先找 exe 同目录 / 项目根目录，找不到再回落到打包内嵌的资源。"""
    external = writable_dir() / relative
    if external.exists():
        return external
    return app_root() / relative
