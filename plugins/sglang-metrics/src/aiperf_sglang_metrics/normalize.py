# SPDX-License-Identifier: Apache-2.0
"""Convert completed AIPerf summaries to an experiment-scoped report contract."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

import orjson

from . import __version__
from .catalog import describe
from .config import Settings, public_url, safe_labels


def finite(value: Any) -> float | None:
    """Return a finite number or explicit missingness, including for booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def clean(value: Any) -> Any:
    """Preserve integers (including ns timestamps) while removing non-finite floats."""
    if isinstance(value, float):
        return finite(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def quantile(buckets: dict[str, Any] | None, q: float) -> float | None:
    """Interpolate non-negative cumulative-le interval buckets, not lifetime counts.

    A quantile landing in +Inf is unknown, not a finite last-bucket value.
    Corrupt/nonmonotone buckets and no observations produce None.
    """
    if not buckets or not 0 <= q <= 1:
        return None
    try:
        ordered = sorted((float(k), finite(v)) for k, v in buckets.items())
    except (ValueError, TypeError):
        return None
    if len(ordered) < 2 or ordered[-1][0] != math.inf:
        return None
    previous = 0.0
    last_bound = -math.inf
    for bound, count in ordered:
        if math.isnan(bound) or bound < 0 or bound <= last_bound or count is None or count < previous:
            return None
        previous, last_bound = count, bound
    total = ordered[-1][1]
    if total is None or total <= 0:
        return None
    target = q * total
    lower, before = 0.0, 0.0
    for upper, count in ordered:
        if count is not None and count >= target:
            if not math.isfinite(upper):
                return None
            if count == before:
                return upper
            return lower + (upper - lower) * (target - before) / (count - before)
        lower, before = upper, count
    return None


def snapshot(source: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the completed ServerMetricsResults data; exclude benchmark secrets."""
    result = {k: source[k] for k in (
        "benchmark_id", "start_ns", "end_ns", "aggregation_time_filter", "warmup_start_ns",
        "warmup_end_ns", "phase", "phase_index", "profiling_index", "phase_name", "phase_kind",
    ) if k in source}
    for key in ("endpoints_configured", "endpoints_successful"):
        result[key] = [public_url(url) for url in source.get(key, [])]
    for key in ("endpoint_summaries", "warmup_endpoint_summaries"):
        output = {}
        for url, endpoint in (source.get(key) or {}).items():
            safe_url = public_url(endpoint.get("endpoint_url") or url)
            if safe_url in output:
                raise ValueError("Endpoint URLs collide after credential redaction; use distinct paths")
            metrics = {}
            for name, family in (endpoint.get("metrics") or {}).items():
                series = []
                for raw in family.get("series", []):
                    item = {k: raw[k] for k in ("stats", "timeslices", "buckets") if k in raw}
                    item["labels"] = safe_labels(raw.get("labels"))
                    item["endpoint_url"] = safe_url
                    series.append(item)
                metrics[name] = {"type": family.get("type", "unknown"),
                                 "description": family.get("description", ""),
                                 "unit": family.get("unit"), "series": series}
            info = {k: endpoint.get("info", {}).get(k) for k in (
                "total_fetches", "unique_updates", "first_fetch_ns", "last_fetch_ns",
                "avg_fetch_latency_ms", "avg_update_interval_ms",
            )}
            output[safe_url] = {"endpoint_url": safe_url, "info": info, "metrics": metrics}
        result[key] = output
    result["phase_results"] = [snapshot(p) for p in source.get("phase_results", [])]
    return clean(result)


def scopes(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer concrete phases to avoid double-counting their combined summary."""
    concrete = source.get("phase_results") or []
    if concrete:
        return concrete
    parts = [dict(source, phase_name=source.get("phase_name") or "profiling",
                  phase_kind=source.get("phase_kind") or "profiling")]
    if source.get("warmup_endpoint_summaries"):
        parts.insert(0, {"phase_name": "warmup", "phase_kind": "warmup",
                         "start_ns": source.get("warmup_start_ns"),
                         "end_ns": source.get("warmup_end_ns"),
                         "endpoint_summaries": source["warmup_endpoint_summaries"]})
    return parts


def window(scope: dict[str, Any]) -> tuple[int, int] | None:
    """Use aggregation bounds when available, not the wider collector lifetime."""
    bounds = scope.get("aggregation_time_filter") or {}
    start = bounds.get("start_ns")
    end = bounds.get("end_ns")
    start = scope.get("start_ns") if start is None else start
    end = scope.get("end_ns") if end is None else end
    if type(start) is not int or type(end) is not int or start >= end:
        return None
    return start, end


def _point(raw: dict[str, Any], kind: str, origin: int, scale: float) -> dict[str, Any]:
    result = {"start": (raw["start_ns"] - origin) / 1e9,
              "end": (raw["end_ns"] - origin) / 1e9,
              "complete": raw.get("is_complete") is not False}
    if kind == "counter":
        values = {"avg": finite(raw.get("rate"))}
    elif kind == "histogram":
        count, total = finite(raw.get("count")), finite(raw.get("sum"))
        values = {"avg": total / count if count and count > 0 and total is not None else None}
        buckets = raw.get("buckets")
        bucket_count = finite((buckets or {}).get("+Inf"))
        consistent = count is not None and count > 0 and bucket_count == count
        values.update({f"p{p}": quantile(buckets, p / 100) if consistent else None
                       for p in (50, 95, 99)})
        result["observations"] = count
    else:
        values = {stat: finite(raw.get(stat)) for stat in ("avg", "min", "max")}
    result.update({stat: finite(value * scale) if value is not None else None
                   for stat, value in values.items()})
    return result


def build_report(source: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Build a bounded, loss-explicit contract without cross-endpoint aggregation."""
    source = snapshot(source)
    selected = scopes(source)
    valid = [window(s) for s in selected if window(s)]
    origin = min((w[0] for w in valid), default=0)
    end_ns = max((w[1] for w in valid), default=origin)
    warnings: set[str] = set()
    phases, series, observed = [], [], set()
    for index, scope in enumerate(selected):
        bounds = window(scope)
        if not bounds:
            warnings.add("缺少有效实验时间边界，未将该阶段指标画成趋势")
            continue
        start, end = bounds
        phase_id = f"phase-{index}"
        phases.append({"id": phase_id, "name": scope.get("phase_name") or f"phase {index}",
                       "kind": scope.get("phase_kind") or scope.get("phase") or "profiling",
                       "start_ns": str(start), "end_ns": str(end),
                       "start": (start - origin) / 1e9, "end": (end - origin) / 1e9})
        for url, endpoint in (scope.get("endpoint_summaries") or {}).items():
            observed.add(url)
            for name, family in sorted(endpoint.get("metrics", {}).items()):
                if not name.startswith("sglang:") and name not in settings.metrics:
                    continue
                kind = str(family.get("type", "unknown")).lower()
                if kind not in {"gauge", "counter", "histogram", "unknown"}:
                    warnings.add(f"不支持的指标类型: {name} ({kind})")
                    continue
                for raw_series in family.get("series", []):
                    labels = safe_labels(raw_series.get("labels"))
                    target = settings.endpoints.get(url)
                    if target is None:
                        role = labels.get("disaggregation_mode", labels.get("role", "unknown"))
                        role = role if role in {"prefill", "decode", "transfer"} else "unknown"
                        target = {"name": url, "role": role}
                        if role == "unknown":
                            warnings.add(f"未配置 P/D 角色: {url}")
                    meta = describe(name, target["role"], settings.metrics.get(name, {}))
                    if kind == "counter" and meta["unit"] and not meta["unit"].endswith("/s"):
                        meta["unit"] += "/s"
                    slices = raw_series.get("timeslices") or []
                    if len(slices) > settings.max_points:
                        raise ValueError(f"{name}: exceeds max_points; increase slice duration or limit explicitly")
                    points, raw_points = [], []
                    for raw in slices:
                        a, b = raw.get("start_ns"), raw.get("end_ns")
                        if type(a) is not int or type(b) is not int or a >= b:
                            warnings.add(f"无效的时间片: {name}")
                            continue
                        if a < start or b > end:
                            warnings.add(f"跨阶段或越界时间片已排除: {name}")
                            continue
                        points.append(_point(raw, kind, origin, meta["scale"]))
                        raw_points.append(clean(raw))
                    points.sort(key=lambda p: (p["start"], p["end"]))
                    if not points:
                        warnings.add("部分指标没有时间片；运行 AIPerf 时加入 --slice-duration 1；不会伪造平线")
                    identity = orjson.dumps([phase_id, url, name, labels], option=orjson.OPT_SORT_KEYS)
                    series.append({"id": hashlib.sha256(identity).hexdigest()[:16],
                                   "metric": name, "type": kind, "description": family.get("description", ""),
                                   "endpoint": url, "instance": target["name"], "role": target["role"],
                                   "labels": labels, "phase_id": phase_id, **meta,
                                   "points": points, "raw_timeslices": raw_points,
                                   "raw_stats": clean(raw_series.get("stats") or {}),
                                   "raw_buckets": clean(raw_series.get("buckets")),
                                   "status": "available" if points else "no_timeslices"})
                    if len(series) > settings.max_series:
                        raise ValueError("Report exceeds max_series; narrow source metrics or raise limit explicitly")
    configured = set(source.get("endpoints_configured", [])) | set(settings.endpoints)
    missing = sorted(configured - observed)
    if missing:
        warnings.add("部分端点未采集到数据；检查 /metrics、--enable-metrics 和网络连通性")
    groups = {s["group"] for s in series if s["points"]}
    absent_groups = [g for g in ("prefill", "decode", "transfer", "kvcache") if g not in groups]
    if "transfer" in absent_groups:
        warnings.add("未采集到传输趋势：不同 SGLang 版本指标不同；gather/RDMA/scatter 需服务端已有埋点")
    return {"schema_version": "1.0", "plugin_version": __version__, "title": settings.title,
            "benchmark_id": source.get("benchmark_id"),
            "source_contract": "AIPerf ServerMetricsResults; interval summaries, not raw scrapes",
            "origin_ns": str(origin), "end_ns": str(end_ns),
            "origin_utc": datetime.fromtimestamp(origin / 1e9, tz=timezone.utc).isoformat() if origin else None,
            "duration_s": (end_ns - origin) / 1e9, "phases": phases, "series": series,
            "endpoints": [{"url": u, **settings.endpoints.get(u, {"name": u, "role": "unknown"}),
                           "status": "observed" if u in observed else "missing"} for u in sorted(configured | observed)],
            "missing_groups": absent_groups, "warnings": sorted(warnings),
            "status": "partial" if warnings or absent_groups else "complete"}
