"""从代码生成 docs/状态机说明.md。

迁移表、位布局、参数表都以代码为唯一事实来源，文档由脚本生成，
避免「文档写一套、代码是另一套」。

    python tools/gen_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.params import CUSTOMER_A, QINGLING_V1  # noqa: E402
from src.core.nm_pdu import BIT_LAYOUT, NM_STATE_CODE_LABELS, WAKEUP_REASON_LABELS  # noqa: E402
from src.core.nm_state import TRANSITIONS, NmState  # noqa: E402

OUT_PATH = ROOT / "docs" / "状态机说明.md"

GUARD_TEXT = {
    "always": "无条件",
    "network_requested": "仍有本地网络需求",
    "no_network_request": "本地网络需求已全部解除",
    "pdu_rmr": "报文 RMR 位 = 1",
    "pdu_no_rmr": "报文 RMR 位 = 0",
    "rss_local_wakeup_to_nos": "配置 rss_local_wakeup_to_rms = false",
    "rss_local_wakeup_to_rms": "配置 rss_local_wakeup_to_rms = true",
    "diag_only_demand": "除去诊断外已无其它需求",
}


def build_markdown() -> str:
    lines: list[str] = []
    add = lines.append

    add("# CanNm 状态机说明（由代码自动生成）")
    add("")
    add("> 本文档由 `tools/gen_docs.py` 从 `src/core/nm_state.py` 与 `src/core/nm_pdu.py` 生成，")
    add("> 请勿手工修改。规范出处中的 4.x 指《庆铃 AUTOSAR CAN 网络管理技术规范 V1.0》。")
    add("")

    add("## 1. 运行模式与状态")
    add("")
    add("| 顶层模式 | 状态 | 简称 | 说明 |")
    add("| --- | --- | --- | --- |")
    add("| BusSleepMode | 总线睡眠模式 | BSM | 仅保留唤醒能力，不参与应用报文收发 |")
    add("| PrepareBusSleepMode | 准备总线睡眠模式 | PBS | 清空发送缓存，等 T_WAIT_BUS_SLEEP |")
    add("| NetworkMode | 重复报文状态 | RMS | 进入网络模式后的默认状态，快速发送机制 |")
    add("| NetworkMode | 常规运行状态 | NOS | 有网络需求期间持续周期发送 |")
    add("| NetworkMode | 预睡眠状态 | RSS | 停发 NM 报文，等 T_NMTimeout 静默 |")
    add("")

    add("## 2. 状态迁移表")
    add("")
    add("| # | 源状态 | 事件 | 目标状态 | 触发条件 | 规范出处 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for i, tr in enumerate(TRANSITIONS, 1):
        guard = GUARD_TEXT.get(tr.guard, tr.guard)
        condition = tr.note if tr.guard == "always" else f"{tr.note}；附加条件：{guard}"
        add(f"| {i} | {tr.src.short} | {tr.event.value} | {tr.dst.short} | {condition} | {tr.src_ref} |")
    add("")

    add("## 3. 定时参数")
    add("")
    add("| 参数 | 客户口径 | 庆铃规范 V1.0 | 含义 |")
    add("| --- | --- | --- | --- |")
    rows = [
        ("T_NMTxTimeout / T_NM_MessageCycle", "tx_cycle", "NM 报文正常发送周期"),
        ("T_NMTimeout", "nm_timeout", "离网超时；RSS 下超时进 PBS"),
        ("T_ImmediateNmTimeout", "immediate_cycle", "快速发送周期"),
        ("N_ImmediateNM_TIMES", "immediate_times", "快速发送帧数"),
        ("T_REPEAT_MESSAGE", "repeat_message", "RMS 持续时长"),
        ("T_WAIT_BUS_SLEEP", "wait_bus_sleep", "PBS 持续时长"),
        ("T_WakeUp", "wakeup", "唤醒到首帧 NM 的最大允许时延"),
        ("T_START_App_TX", "start_app_tx", "首帧应用报文相对首帧 NM 的时延"),
        ("T_WAIT_DiagReq", "wait_diag_req", "诊断请求带来的网络保持时长"),
    ]
    for name, attr, desc in rows:
        a = getattr(CUSTOMER_A, attr)
        b = getattr(QINGLING_V1, attr)
        unit = "" if attr == "immediate_times" else "ms"
        add(f"| {name} | {a}{unit} | {b}{unit} | {desc} |")
    add("")
    add("切换口径：修改 `config/network.json` 的 `profile` 字段（`customer_a` / `qingling_v1`）。")
    add("")

    add("## 4. NM PDU 位布局")
    add("")
    add("| 字段 | 位置 | 位宽 | 位序状态 | 说明 |")
    add("| --- | --- | --- | --- | --- |")
    for f in BIT_LAYOUT:
        pos = f"Byte{f.byte} bit{f.bit}" if f.length == 1 else f"Byte{f.byte} bit{f.bit}~{f.bit + f.length - 1}"
        status = "已确认" if f.confirmed else "**待客户确认**"
        add(f"| `{f.name}` | {pos} | {f.length} | {status} | {f.comment} |")
    add("")
    add("Byte 5~7 为部分网络编号标志，规范标注「暂不实施」，发送时恒为 0。")
    add("")

    add("## 5. Byte2 网络管理状态码")
    add("")
    add("| 编码 | 含义 |")
    add("| --- | --- |")
    for code, label in sorted(NM_STATE_CODE_LABELS.items()):
        if code:
            add(f"| {code} | {label} |")
    add("")
    add("## 6. Byte3 唤醒原因")
    add("")
    add("| 编码 | 含义 |")
    add("| --- | --- |")
    for code, label in sorted(WAKEUP_REASON_LABELS.items()):
        suffix = "（仅首帧允许，最迟第二帧须为实际值）" if code == 0 else ""
        add(f"| {code} | {label}{suffix} |")
    add("")
    add("3~127 为外设 I/O 唤醒，128~255 为功能唤醒，具体编码由网络组释放后补充。")
    add("")

    add("## 7. 待客户确认清单")
    add("")
    add("1. **NM Remote Sleep Indication Bit 的位序**：客户规范提及该位但未给出位置，")
    add("   当前占位在 Byte1 bit3（配置驱动，确认后改一行即可）。")
    add("2. **AC_4_OTA 的位序**：规范表格中写作 AC_3_OTA，疑为笔误，当前按 Byte4 bit4 处理。")
    add("3. **Byte2 状态码 16 与 32**：规范中两条的说明文字重复，当前按 16 = RMS←NOS、")
    add("   32 = RMS←RSS 推断，但 8 已是 NOS←RSS，存在冲突。")
    add("4. **T_WakeUp 取值**：规范表 4 中同时出现 100ms 与 200ms 两个值，当前取 100ms。")
    add("5. **RSS 收到本地唤醒的出口状态**：4.3.3.3 要求进 NOS，部分网络章节 NM_15 要求进 RMS")
    add("   快速发送；当前默认按 4.3.3.3 进 NOS，可用配置项切换。")
    add("6. **进入 RSS 是否补发一帧睡眠指示**：因 RSS 本身停发 NM 报文，若不补发则该位无法广播；")
    add("   当前默认补发一帧，可用配置项 `send_sleep_indication_on_rss` 关闭。")
    add("")
    return "\n".join(lines)


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build_markdown(), encoding="utf-8")
    print(f"已生成 {OUT_PATH}（{len(TRANSITIONS)} 条迁移，{len(BIT_LAYOUT)} 个 PDU 字段）")


if __name__ == "__main__":
    main()
