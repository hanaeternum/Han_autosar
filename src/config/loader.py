"""配置加载与校验。

JSON 不支持十六进制字面量，因此 id_base 用十进制书写（1024 = 0x400）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from ..core.events import LogBus
from ..core.network import NmEngine
from .params import NmProtocolParams, NmTimingParams, get_timing
from .paths import resolve_data

# 打包成 exe 后 __file__ 指向临时解压目录，不能再用 parents[2] 拼路径。
# resolve_data 会先看 exe 同目录有没有同名文件（方便改配置不重新打包）。
DEFAULT_CONFIG_PATH = resolve_data("config/network.json")


@dataclass
class NetworkConfig:
    profile: str
    timing: NmTimingParams
    protocol: NmProtocolParams
    channels: list[dict[str, Any]]
    topology: dict[str, Any]
    raw: dict[str, Any]

    @property
    def gateway_policy(self) -> Optional[dict[str, Any]]:
        return self.topology.get("gateway_policy")

    def node_roles(self) -> dict[tuple[str, str], str]:
        roles = {}
        for ch in self.channels:
            for node in ch["nodes"]:
                roles[(ch["name"], node["ecu"])] = node.get("role", "")
        return roles


def load_config(path: str | Path | None = None) -> NetworkConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    profile = raw.get("profile", "customer_a")
    # 拷贝一份，避免 timing_overrides 污染全局档位单例
    timing = replace(get_timing(profile))
    # 允许在配置文件里覆盖档位中的个别参数
    for key, value in (raw.get("timing_overrides") or {}).items():
        if not hasattr(timing, key):
            raise KeyError(f"timing_overrides 中存在未知参数 {key!r}")
        setattr(timing, key, value)

    proto_raw = raw.get("protocol", {})
    protocol = NmProtocolParams(
        id_base=proto_raw.get("id_base", 0x400),
        id_extended=proto_raw.get("id_extended", False),
        dlc=proto_raw.get("dlc", 8),
        send_sleep_indication_on_rss=proto_raw.get("send_sleep_indication_on_rss", True),
        rss_local_wakeup_to_rms=proto_raw.get("rss_local_wakeup_to_rms", False),
        route_nm_between_channels=proto_raw.get("route_nm_between_channels", False),
        diag_can_wake_from_sleep=proto_raw.get("diag_can_wake_from_sleep", False),
    )

    topology = raw.get("topology", {})
    channels = topology.get("channels", [])
    if not channels:
        raise ValueError("配置中没有任何网段")

    errors = timing.validate() + protocol.validate() + _validate_channels(channels, protocol)
    if errors:
        raise ValueError("配置校验失败：\n  - " + "\n  - ".join(errors))

    return NetworkConfig(
        profile=profile,
        timing=timing,
        protocol=protocol,
        channels=channels,
        topology=topology,
        raw=raw,
    )


def _validate_channels(channels: list[dict[str, Any]], protocol: NmProtocolParams) -> list[str]:
    errors: list[str] = []
    seen_ecu_per_channel: dict[str, set[str]] = {}
    for ch in channels:
        name = ch.get("name", "?")
        used_ids: dict[int, str] = {}
        seen_ecu_per_channel.setdefault(name, set())
        for node in ch.get("nodes", []):
            node_id = node.get("node_id")
            ecu = node.get("ecu", "?")
            if node_id is None:
                errors.append(f"{name}/{ecu} 缺少 node_id")
                continue
            if not 0 <= node_id <= 0xFF:
                errors.append(f"{name}/{ecu} 的 node_id=0x{node_id:X} 超出 0x00~0xFF")
            if node_id in used_ids:
                errors.append(f"{name} 上 NodeID 0x{node_id:02X} 被 {used_ids[node_id]} 与 {ecu} 重复占用")
            used_ids[node_id] = ecu
            if ecu in seen_ecu_per_channel[name]:
                errors.append(f"{name} 上节点 {ecu} 重复定义")
            seen_ecu_per_channel[name].add(ecu)
    return errors


def build_engine(config: NetworkConfig, log_bus: Optional[LogBus] = None, tick_ms: int = 1) -> NmEngine:
    return NmEngine(
        timing=config.timing,
        protocol=config.protocol,
        channels=config.channels,
        log_bus=log_bus,
        tick_ms=tick_ms,
    )
