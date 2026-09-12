# 项目约定：AUTOSAR CanNm 仿真上位机

## 项目性质
- Python 版 AUTOSAR CanNm 网络管理模拟上位机，**纯动画演示，不与实物交互**。
- 交付对象是客户评审用的演示工具 + 规范符合性对照文档。

## 规范来源（两套并存，不要只认一套）
- `庆铃AUTOSAR CAN网络管理技术规范V1.0.pdf`：21 页，权威依据。
- 用户口述口径（客户 A）：ID=0x400+NodeID、T_NMTxTimeout=1000ms、T_NMTimeout=2000ms、T_ImmediateNmTimeout=100ms。
- 默认用客户 A 口径（`profile: customer_a`），庆铃口径保留为 `qingling_v1`。

## 拓扑约定
```
CAN_BODY  GW(0x10) BCM(0x20) PEPS(0x30)     PEPS=主动唤醒源
CAN_INFO  GW(0x10) IC(0x40)  IVI(0x50)      由网关应用层联动拉起
```
- 网关是演示主角，两个网段是**两个独立 CanNm 实例**。
- NM 报文**不跨网段路由**；跨网段联动必须由应用层显式调用 NetworkRequest。

## 架构约定（已定型，勿轻易推翻）
1. `src/core/` 是零 Qt 依赖的纯内核，所有状态机逻辑都在这里，必须能脱离界面单测。
2. 单一全局时钟（1ms 节拍）驱动所有节点，**内核不使用多线程**——迁移顺序必须可复现。
   多线程只出现在「内核线程 ↔ UI 线程」这一条边界上：`src/presenter/engine_worker.py`。
3. 状态迁移只在 `src/core/nm_state.py` 的 `TRANSITIONS` 表里定义，是文档/UI/测试的单一事实来源。
4. NM PDU 位序只在 `src/core/nm_pdu.py` 的 `BIT_LAYOUT` 里定义，客户确认位序后改一行。
5. 引擎单 tick 顺序固定：收（总线 poll）→ 算（节点 tick）→ 发（统一入队）→ 网络级睡眠仲裁。
6. 收发任一帧 NM PDU 后必须重启 T_NMTimeout，这是「同睡同醒」的核心，改动前先看测试。
7. 文档 `docs/状态机说明.md` 由 `python tools/gen_docs.py` 生成，不要手改。

## UI 层约定（勿推翻）
8. worker 按**墙钟累积**折算仿真毫秒批量推进，每 40ms 汇总一批推给 UI；
   禁止把 1ms 节拍的每个事件逐条 emit 信号（会压垮 UI 事件队列）。
9. UI→内核只投递闭包到 `queue.Queue`，由内核线程统一取出执行，不在 UI 线程碰引擎对象。
10. 界面截图/自动化验证用真实平台插件 + `Qt.WA_DontShowOnScreen`；
    **禁用 `QT_QPA_PLATFORM=offscreen`**（本机该插件字体数为 0，截图无文字会误导结论）。
11. 报文表用 `QAbstractTableModel` + deque + `beginInsertRows`，
    不用 `beginResetModel`（会重置滚动位置），也不用 QTableWidget 逐行插入。
12. 批量推送的收集列表只能 `clear()`，**禁止 `self._pending = []` 重新绑定**（订阅者会读不到新事件）。

## 已知规范冲突（已做成配置开关，勿擅自删掉开关）
- `send_sleep_indication_on_rss`：进入 RSS 是否补发一帧带 Remote Sleep Indication Bit 的报文。
- `rss_local_wakeup_to_rms`：RSS 收到本地唤醒进 RMS（NM_15 口径）还是 NOS（4.3.3.3 口径）。
- 待客户确认：Remote Sleep Indication Bit 位序、AC_4_OTA 位序、状态码 16/32、T_WakeUp 取值。

## 环境
- 内核与脚本仅用 Python 标准库；跑 `sim_demo.py` 与单测无需安装依赖。
- PyQt5 只在实际启动界面时需要。验证环境：Python 3.13.14 + PyQt5 5.15.11 / Qt 5.15.2（Windows）。

## 常用命令（用户会反复问"怎么运行"）
- 启动界面：`python main.py`（`--speed 5` 倍速 / `--auto-connect` / `--channel CAN_INFO --node-id 0x10`）
- **环境自检**：`python main.py --smoke`（不弹窗口，跑 3 秒后打印"结论：通过/未通过"）
- 无界面回放：`python sim_demo.py --summary-only`
- 测试：`python -m unittest discover -s tests -v`（24 项）
- 生成文档 / 截图：`python tools/gen_docs.py`、`python tools/ui_screenshot.py`
- 界面按钮、`--smoke`、自动化测试共用 `MainWindow.connect_simulation()` 这一公开入口。
