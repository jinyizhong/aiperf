# SPDX-License-Identifier: Apache-2.0
"""Per-source capability coverage, not invented zero-valued monitoring data."""
from __future__ import annotations
from typing import Any
from .catalog_extended import DIRECTION_METRICS, QUEUE_METRICS, SOURCE_URL

TIERS = {"device": "L1", "host": "L2", "storage": "L3"}
QUEUE_ROLES = {
    "num_prefill_bootstrap_queue_reqs": {"prefill"},
    "num_prefill_inflight_queue_reqs": {"prefill"},
    "num_decode_prealloc_queue_reqs": {"decode"},
    "num_decode_transfer_queue_reqs": {"decode"},
}
# These are capability names, deliberately NOT guessed Prometheus metric names.
INSTRUMENTATION_GAPS = (
    ("decode_retracted_queue_depth", "D 回退等待队列深度", "decode",
     "当前 collector 未提供此独立 Gauge；num_retracted_reqs 不是该队列深度"),
    ("l1_l2_copy_queue_depth", "L1↔L2 拷贝队列与同步等待", "kvcache",
     "原生拷贝 duration 不包含完整的排队/fence 等待；需要引擎额外埋点"),
    ("l2_l3_storage_queue_depth", "L2↔L3 读写队列与排队耗时", "kvcache",
     "页数/带宽直方图无法反推出队列深度；需 storage/controller 埋点"),
    ("pd_transfer_substages", "PD gather/RDMA/scatter/staging-wait", "transfer",
     "现有总传输耗时不能拆解这些阶段；已有自定义指标可通过 metrics 映射接入"),
    ("kv_device_memory_io", "L1 内 attention KV 物理读写", "kvcache",
     "层级搬运字节不等于 GPU kernel 的 KV 读写；需 profiler/专用埋点"),
)


def _status(series: list[dict[str, Any]]) -> str:
    if not series:
        return "not_collected"
    points = [point for item in series for point in item.get("points", [])]
    if not points:
        return "no_timeslices"
    if any(any(isinstance(point.get(key), (int, float)) for key in ("avg", "min", "max", "p95"))
           for point in points):
        return "available"
    return "no_observations"


def enrich_report(report: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Keep directions and stages searchable and report missingness per phase/endpoint."""
    for item in report["series"]:
        labels = item.get("labels", {})
        item["stage"] = item.get("stage") or labels.get("stage", labels.get("category", ""))
        item["tier"] = TIERS.get(labels.get("cache_source", ""), "")
        if not item.get("direction") and labels.get("direction") in DIRECTION_METRICS:
            item["direction"] = labels["direction"]
        item["is_queue"] = "queue" in item["metric"]
    entries = []
    for phase in report["phases"]:
        for endpoint in report["endpoints"]:
            local = [s for s in report["series"] if s["phase_id"] == phase["id"]
                     and s["endpoint"] == endpoint["url"]]
            by_name: dict[str, list[dict[str, Any]]] = {}
            for item in local:
                key = item["metric"].removeprefix("sglang:").removesuffix("_total")
                by_name.setdefault(key, []).append(item)
            requirements: list[tuple[str, str, str]] = []
            for name, stage in QUEUE_METRICS.items():
                if endpoint["role"] in QUEUE_ROLES.get(name, {"prefill", "decode"}):
                    requirements.append((name, "queue" if "queue" in name else "state", stage))
            if endpoint["role"] in {"prefill", "decode"}:
                for direction, names in DIRECTION_METRICS.items():
                    requirements.extend((name, "cache_io", direction) for name in names)
                requirements.extend((name, "cache_capacity", "") for name in (
                    "full_token_usage", "mamba_usage", "hicache_host_used_tokens",
                    "hicache_host_total_tokens", "cached_tokens", "prefill_effective_tokens"))
            for name, category, channel in requirements:
                observed = by_name.get(name, [])
                counter_names = {"hicache_backup_tokens", "hicache_backup_bytes", "load_back_tokens",
                                 "load_back_bytes", "backuped_tokens", "prefetched_tokens",
                                 "cached_tokens", "prefill_effective_tokens"}
                native_name = f"sglang:{name}" + ("_total" if name in counter_names else "")
                if observed:
                    native_name = observed[0]["metric"]
                entries.append({"phase_id": phase["id"], "phase_name": phase["name"],
                                "endpoint": endpoint["url"], "role": endpoint["role"],
                                "instance": endpoint["name"], "category": category,
                                "channel": channel, "metric": native_name,
                                "status": _status(observed), "series_count": len(observed),
                                "source": SOURCE_URL})
    report["coverage"] = {
        "entries": entries,
        "instrumentation_gaps": [dict(zip(("id", "title", "role", "reason"), row, strict=True))
                                 for row in INSTRUMENTATION_GAPS],
        "note": "未采集不等于零；可能未启用 HiCache/相应功能、版本无此指标或采集缺失。覆盖按端点/阶段检查，不承诺所有 TP rank 已暴露。",
        "tier_definition": {"L1": "GPU/device KV", "L2": "local host DRAM",
                            "L3": "storage backend (can itself be remote RAM/SSD)"},
    }
    report["runtime"] = {"aiperf_version": str(source.get("_aiperf_version", "unknown"))}
    return report
