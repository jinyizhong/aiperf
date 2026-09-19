# SPDX-License-Identifier: Apache-2.0
"""Deterministic synthetic fixture, explicitly not a production benchmark."""

from __future__ import annotations

import math
from typing import Any

ORIGIN = 1789819200000000000
DEMO_CONFIG = """schema_version: 1
title: 'SGLang P/D · Synthetic demo (not measured)'
endpoints:
  - {url: 'http://prefill:8000/metrics', role: prefill, name: p0}
  - {url: 'http://decode:8000/metrics', role: decode, name: d0}
"""


def demo_source() -> dict[str, Any]:
    """Build the completed ServerMetricsResults shape for offline verification."""
    phases = []
    for phase, offset, duration in (("warmup", 0, 8), ("profiling", 8, 90)):
        start, end = ORIGIN + offset * 10**9, ORIGIN + (offset + duration) * 10**9
        endpoints = {}
        for role in ("prefill", "decode"):
            url = f"http://{role}:8000/metrics"
            families = {}
            names = ["sglang:num_queue_reqs", "sglang:num_running_reqs", "sglang:token_usage",
                     "sglang:cache_hit_rate", "sglang:per_stage_req_latency_seconds",
                     "sglang:prompt_tokens" if role == "prefill" else "sglang:generation_tokens",
                     "sglang:time_to_first_token_seconds" if role == "prefill" else "sglang:time_per_output_token_seconds"]
            if role == "decode":
                names += ["sglang:kv_transfer_latency_ms", "sglang:kv_transfer_speed_gb_s",
                          "sglang:num_decode_prealloc_queue_reqs", "sglang:num_decode_transfer_queue_reqs"]
            for index, name in enumerate(names):
                histogram = "seconds" in name or "latency_ms" in name or "speed_gb_s" in name
                kind = "histogram" if histogram else ("counter" if name.endswith("tokens") else "gauge")
                slices = []
                for i in range(duration):
                    wave = (math.sin(i / 7 + index) + 1) / 2
                    base = .72 + wave * .13 if name.endswith("token_usage") else .86 + wave * .1 if name.endswith("cache_hit_rate") else 2 + wave * (24 if role == "decode" else 12)
                    point = {"start_ns": start + i * 10**9, "end_ns": start + (i+1)*10**9}
                    if kind == "histogram":
                        mean = .005 + wave*.02
                        if name.endswith("_ms"):
                            mean *= 1000
                        if name.endswith("gb_s"):
                            mean *= 10000
                        point.update(count=20, sum=20*mean, avg=mean,
                                     buckets={str(mean*.5): 3, str(mean): 10,
                                              str(mean*2): 19, str(mean*4): 20, "+Inf": 20})
                    elif kind == "counter":
                        rate = 1000 + 300*wave if role == "decode" else 70000 + 20000*wave
                        point.update(total=rate, rate=rate)
                    else:
                        point.update(avg=base, min=base*.9, max=base*1.1)
                    slices.append(point)
                label = {"model_name": "demo-model", "tp_rank": "0"}
                if "per_stage" in name:
                    label["stage"] = "prefill_forward" if role == "prefill" else "decode_waiting"
                families[name] = {"type": kind, "description": "Synthetic demo fixture, not measured",
                                  "series": [{"labels": label, "stats": {}, "timeslices": slices}]}
            endpoints[url] = {"endpoint_url": url, "info": {"total_fetches": duration,
                "first_fetch_ns": start, "last_fetch_ns": end, "avg_fetch_latency_ms": 1,
                "unique_updates": duration, "first_update_ns": start, "last_update_ns": end,
                "duration_seconds": duration, "avg_update_interval_ms": 1000}, "metrics": families}
        phases.append({"benchmark_id": "synthetic-demo", "phase_name": phase, "phase_kind": phase,
                       "start_ns": start, "end_ns": end, "endpoint_summaries": endpoints})
    return {"benchmark_id": "synthetic-demo", "start_ns": ORIGIN, "end_ns": ORIGIN+98*10**9,
            "endpoints_configured": list(phases[0]["endpoint_summaries"]),
            "endpoints_successful": list(phases[0]["endpoint_summaries"]), "phase_results": phases}
