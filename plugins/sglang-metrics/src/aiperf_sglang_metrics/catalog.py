# SPDX-License-Identifier: Apache-2.0
"""Display semantics. Metric availability is discovered, never fabricated."""
from __future__ import annotations
from typing import Any
from .catalog_extended import EXTRA_CATALOG, details

CATALOG = {
    "sglang:num_queue_reqs": ("排队请求", None, "requests", 1, "每个来源独立显示，不跨 TP rank 求和"),
    "sglang:num_running_reqs": ("运行请求", None, "requests", 1, "服务端调度请求数，不等于客户端并发"),
    "sglang:queue_time_seconds": ("排队耗时", None, "ms", 1000, "服务端直方图；分位数为桶估计"),
    "sglang:prompt_tokens": ("输入 Token 速率", "prefill", "tokens/s", 1, "逻辑输入量，不能当成未命中 token 的计算速率"),
    "sglang:generation_tokens": ("输出 Token 速率", "decode", "tokens/s", 1, "Counter 的区间速率，非累计值"),
    "sglang:gen_throughput": ("生成吞吐", "decode", "tokens/s", 1, "引擎上报的吞吐 Gauge"),
    "sglang:time_to_first_token_seconds": ("服务端 TTFT", None, "ms", 1000, "服务端视角，不等于 AIPerf 客户端 TTFT"),
    "sglang:inter_token_latency_seconds": ("服务端 ITL", "decode", "ms", 1000, "服务端直方图，不等于客户端逐请求平均 ITL"),
    "sglang:per_stage_req_latency_seconds": ("请求阶段耗时", None, "ms", 1000, "保留 stage 标签；不把阶段 P95 相加"),
    "sglang:time_per_output_token_seconds": ("服务端 TPOT", "decode", "ms", 1000, "每请求或迭代口径以 exporter HELP 为准"),
    "sglang:num_prefill_inflight_queue_reqs": ("P 在途请求", "transfer", "requests", 1, "Prefill inflight queue"),
    "sglang:token_usage": ("Token 池使用率", "kvcache", "%", 100, "多个池的瓶颈使用率，不是整卡 HBM 使用率"),
    "sglang:num_used_tokens": ("已使用 Token 槽位", "kvcache", "tokens", 1, "引擎上报的池使用量"),
    "sglang:cache_hit_rate": ("前缀缓存命中率", "kvcache", "%", 100, "引擎 Gauge；不做跨实例无权平均，不假定代表 L3 命中"),
    "sglang:num_retracted_reqs": ("回退请求", "kvcache", "requests", 1, "回退事件统计，不是 D retracted queue 的深度"),
    "sglang:kv_transfer_latency_ms": ("KV 传输耗时", "transfer", "ms", 1, "SGLang 原始单位为毫秒，不再乘 1000"),
    "sglang:kv_transfer_latency_seconds": ("KV 传输耗时", "transfer", "ms", 1000, "直方图不跨实例平均 P95"),
    "sglang:kv_transfer_speed": ("KV 传输速度", "transfer", "GB/s", 1, "引擎上报的传输速度，不是全部 NIC 带宽"),
    "sglang:kv_transfer_speed_gb_s": ("KV 传输速度", "transfer", "GB/s", 1, "按次传输速度分布，不是整段实验的总带宽"),
    "sglang:num_prefill_bootstrap_queue_reqs": ("P Bootstrap 等待队列", "transfer", "requests", 1, "等待 bootstrap 的请求数"),
    "sglang:num_decode_transfer_queue_reqs": ("D 传输等待队列", "transfer", "requests", 1, "等待 KV 传输完成的请求数"),
    "sglang:num_decode_prealloc_queue_reqs": ("D 预分配等待队列", "transfer", "requests", 1, "等待 KV/状态预分配的请求数"),
}
CATALOG.update(EXTRA_CATALOG)


def describe(name: str, role: str, override: dict[str, Any]) -> dict[str, Any]:
    """Resolve display metadata without renaming or dropping the raw family."""
    key = name.removesuffix("_total")
    base = CATALOG.get(key, (name, "other", "", 1, "原始指标；请结合 exporter HELP 确认口径"))
    title, group, unit, scale, note = base
    group = group or (role if role in {"prefill", "decode", "transfer"} else "other")
    return {"title": override.get("title", title), "group": override.get("group", group),
            "unit": override.get("unit", unit), "scale": override.get("scale", scale),
            "note": note, **details(key)}
