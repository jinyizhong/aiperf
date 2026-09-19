# SPDX-License-Identifier: Apache-2.0
"""Version-qualified SGLang queue, hierarchical-cache and bottleneck catalog.

Only names verified in the pinned collector are native entries. TYPE and labels
come from the actual scrape. No metric value is synthesized by this catalog.
"""
from __future__ import annotations
from typing import Any

SGLANG_REF = "76f9213a411018547f4fd6a75f36feaa4d6bed58"
SOURCE_URL = (f"https://github.com/sgl-project/sglang/blob/{SGLANG_REF}/"
              "python/sglang/srt/observability/metrics_collector.py")
EXTRA_CATALOG: dict[str, tuple[str, str | None, str, float, str]] = {}


def _add(name: str, title: str, group: str | None, unit: str, scale: float,
         note: str) -> None:
    EXTRA_CATALOG[f"sglang:{name}"] = (title, group, unit, scale, note)


_add("num_grammar_queue_reqs", "Grammar 编译等待队列", None, "requests", 1,
     "语法等待请求，不与调度 waiting/running 相加")
_add("num_paused_reqs", "暂停请求", None, "requests", 1,
     "异步权重同步暂停，不是 KV 回退队列")
_add("pending_prealloc_token_usage", "D 待预分配 Token 压力", "kvcache", "%", 100,
     "尚未完成预分配的容量需求，不是已分配 KV 使用率")
for name, title in (
    ("num_bootstrap_failed_reqs", "Bootstrap 失败速率"),
    ("num_transfer_failed_reqs", "PD 传输失败速率"),
    ("num_prefill_retries", "Prefill 重试速率"),
):
    _add(name, title, "transfer", "requests/s", 1, "区间 Counter 速率；零与未上报分开")
for name, title in (("kv_transfer_bootstrap_ms", "PD Bootstrap 耗时"),
                    ("kv_transfer_alloc_ms", "PD 分配等待耗时")):
    _add(name, title, "transfer", "ms", 1, "原始毫秒直方图；不再乘 1000")
_add("kv_transfer_total_mb", "每次 PD 传输大小", "transfer", "MB", 1,
     "按次传输大小的分布，不是累计字节，也不是 MB/s")

# CPU DRAM is L2; storage (including Mooncake remote memory) is L3.
for prefix, direction, verb in (("hicache_backup", "L1->L2", "备份"),
                                 ("load_back", "L2->L1", "回载")):
    _add(f"{prefix}_duration_seconds", f"{direction} {verb}耗时", "kvcache", "ms", 1000,
         "合并拷贝操作的测量时间；不含所有队列/同步等待，不等于端到端请求延迟")
    _add(f"{prefix}_bytes", f"{direction} {verb}物理流量", "kvcache", "bytes/s", 1,
         "各 rank 的真实 KV 分片字节；墙钟速率含空闲，不等于活跃拷贝带宽")
    _add(f"{prefix}_tokens", f"{direction} {verb} Token 速率", "kvcache", "tokens/s", 1,
         "保留 pool/rank；逻辑 token 跨 TP rank 可重复，禁止盲目相加")
for name, direction, verb in (("backup", "L2->L3", "写入"),
                              ("prefetch", "L3->L2", "预取")):
    _add(f"{name}_pgs", f"{direction} 每批{verb}页数", "kvcache", "pages", 1,
         "每批页数分布，不是页数/秒；page-size 与物理字节不可混用")
    _add(f"{name}_bandwidth", f"{direction} {verb}活跃带宽", "kvcache", "GB/s", 1,
         "每操作 wire bytes/IO wall time 的直方图；不是整段实验墙钟带宽")
_add("backuped_tokens", "L2->L3 写入 Token 速率", "kvcache", "tokens/s", 1,
     "原生拼写 backuped；不得与 GPU->Host 的 hicache_backup_tokens 混用")
_add("prefetched_tokens", "L3->L2 已预取 Token 速率", "kvcache", "tokens/s", 1,
     "已传入主机内存，后续仍可能被丢弃；不等于最终 L3 缓存命中")
for name, title, note in (
    ("storage_prefetch_hit_tokens", "L3 查询命中 Token", "查询命中发生在 host 分配/传输之前"),
    ("storage_prefetch_unfulfilled_tokens", "L3 命中但未兑现 Token", "按 reason 展示尝试级损失，不是最终 cache miss"),
    ("storage_prefetch_deferred_tokens", "L3 预取延后 Token", "按 reason 展示容量等待；同 token 可被重复尝试"),
    ("hicache_backup_dropped_tokens", "L3 写入前丢弃 Token", "buffer 模式准入拒绝或数据陈旧，不等于 L1->L2 已完成拷贝"),
    ("hicache_prefetch_aux_alloc_failed_tokens", "预取辅助池分配失败", "SWA/Mamba 等 host staging 不足可能放弃整个预取"),
    ("hicache_dropped_tokens", "L1 无备份丢弃 Token", "保留 pool/reason；并非所有 eviction 都丢失可复用数据"),
    ("evicted_tokens", "L1 驱逐 Token", "释放的设备槽位，可能已备份，也可能被丢弃"),
    ("cached_tokens", "已复用缓存 Token", "按 cache_source=device/host/storage/total 分开；total 是旧版回退，不与分层数据相加"),
):
    _add(name, title, "kvcache", "tokens/s", 1, note)
_add("eviction_duration_seconds", "L1 驱逐耗时", "kvcache", "ms", 1000,
     "write_back 下可含阻塞 D2H 备份，不应与备份耗时重复相加")
for name, title in (("hicache_host_used_tokens", "L2 Host 已用 Token"),
                    ("hicache_host_total_tokens", "L2 Host Token 容量"),
                    ("kv_available_tokens", "L1 KV 空闲 Token"),
                    ("kv_evictable_tokens", "L1 KV 可驱逐 Token"),
                    ("kv_used_tokens", "L1 KV 活跃 Token"),
                    ("swa_available_tokens", "SWA 空闲 Token"),
                    ("swa_evictable_tokens", "SWA 可驱逐 Token"),
                    ("swa_used_tokens", "SWA 活跃 Token"),
                    ("streaming_session_held_tokens", "会话持有 KV Token")):
    _add(name, title, "kvcache", "tokens", 1, "池容量/占用；各 rank 独立显示")
for name, title in (("mamba_available_tokens", "SSM 空闲状态槽"),
                    ("mamba_evictable_tokens", "SSM 可驱逐状态槽"),
                    ("mamba_used_tokens", "SSM 活跃状态槽")):
    _add(name, title, "kvcache", "slots", 1, "原指标名含 tokens，但此处是 SSM 状态槽，不等于 KV token")
for name, title in (("full_token_usage", "Full-attention KV 使用率"),
                    ("swa_token_usage", "SWA KV 使用率"),
                    ("mamba_usage", "SSM 状态池使用率")):
    _add(name, title, "kvcache", "%", 100, "各池 0-1 使用率；不以 token_usage 代替所有池")
for name, title, unit in (("num_retracted_requests", "回退请求速率", "requests/s"),
                           ("num_retracted_input_tokens", "回退输入 Token 速率", "tokens/s"),
                           ("num_retracted_output_tokens", "回退输出 Token 速率", "tokens/s")):
    _add(name, title, "kvcache", unit, 1, "累计回退事件的区间速率，不是当前队列深度")
_add("realtime_tokens", "实际处理 Token 速率", None, "tokens/s", 1,
     "按 mode=prefill_compute/prefill_cache/decode 分开，计算与复用不可混合")
_add("prefill_effective_tokens", "Prefill 有效 Token 分层速率", "kvcache", "tokens/s", 1,
     "mode=input/device_hit/host_hit/storage_hit；input 为未命中量，分母是所有模式之和")
_add("decode_sum_seq_lens", "Decode 总上下文长度", "decode", "tokens", 1,
     "活跃 decode 序列长度之和，反映 attention 压力，不是每秒吞吐")
_add("grammar_compilation_time_seconds", "Grammar 编译耗时", None, "ms", 1000,
     "编译等待可能拉高 TTFT；与 grammar queue 同看")
for name, title in (("scheduler_stage_seconds", "调度器阶段墙钟占比"),
                    ("scheduler_idle_seconds", "调度器空闲占比"),
                    ("forward_execution_seconds", "Forward 累计忙碌时间速率")):
    _add(name, title, None, "seconds/s", 1,
         "Counter 区间秒/墙钟秒；保留 category，不是一次执行耗时，不自动判定瓶颈")
_add("scheduler_process_cpu_seconds", "调度器 CPU 消耗", None, "CPU-seconds/s", 1,
     "所有线程 CPU 秒数之和，可能大于 1，不是 0-100% GPU 利用率")
_add("is_cuda_graph", "CUDA Graph 执行标志", None, "flag", 1, "Gauge；最近批次模式")
_add("cuda_graph_passes", "执行模式批次速率", None, "passes/s", 1,
     "按 mode 展示，观察 graph/eager 路径切换")
_add("spec_accept_length", "投机平均接受长度", "decode", "tokens", 1,
     "仅启用投机解码时有意义，不等于端到端加速比")
_add("spec_accept_rate", "投机接受率", "decode", "%", 100, "引擎上报比例")
_add("num_aborted_requests", "请求中止速率", None, "requests/s", 1,
     "取消/中止，不自动等同于服务器错误率")

DIRECTION_METRICS = {
    "L1->L2": ("hicache_backup_tokens", "hicache_backup_bytes", "hicache_backup_duration_seconds"),
    "L2->L1": ("load_back_tokens", "load_back_bytes", "load_back_duration_seconds"),
    "L2->L3": ("backuped_tokens", "backup_pgs", "backup_bandwidth"),
    "L3->L2": ("prefetched_tokens", "prefetch_pgs", "prefetch_bandwidth"),
}
QUEUE_METRICS = {
    "num_queue_reqs": "scheduler_waiting",
    "num_running_reqs": "running",
    "num_grammar_queue_reqs": "grammar_waiting",
    "num_prefill_bootstrap_queue_reqs": "prefill_bootstrap",
    "num_prefill_inflight_queue_reqs": "prefill_inflight",
    "num_decode_prealloc_queue_reqs": "decode_prealloc",
    "num_decode_transfer_queue_reqs": "decode_transfer",
    "num_paused_reqs": "weight_sync_paused",
}


def details(name: str) -> dict[str, Any]:
    """Attach searchable semantics without guessing missing transport metrics."""
    short = name.removeprefix("sglang:").removesuffix("_total")
    direction = next((key for key, names in DIRECTION_METRICS.items() if short in names), "")
    return {"direction": direction, "stage": QUEUE_METRICS.get(short, ""),
            "catalog_source": SOURCE_URL if name in EXTRA_CATALOG or short in QUEUE_METRICS else ""}
