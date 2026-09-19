# SPDX-License-Identifier: Apache-2.0
"""Contract and regression tests; all measurements here are synthetic."""

import asyncio
import copy
import importlib.metadata
import math
from pathlib import Path
from typing import Any

import orjson
import pytest
import yaml

from aiperf_sglang_metrics.config import Settings
from aiperf_sglang_metrics.demo import DEMO_CONFIG, demo_source
from aiperf_sglang_metrics.normalize import build_report, quantile, snapshot
from aiperf_sglang_metrics.report import render_html, write_bundle


def source_for(kind: str, slices: list[dict], name: str = "sglang:queue_time_seconds", stats: dict | None = None) -> dict:
    return {"benchmark_id": "test", "start_ns": 10**18, "end_ns": 10**18+10**10,
            "endpoint_summaries": {"http://p:8000/metrics": {
                "metrics": {name: {"type": kind, "series": [{"labels": {"rank": "0"},
                    "stats": stats or {}, "timeslices": slices}]}}}}}


def ts(**fields: Any) -> dict:
    return {"start_ns": 10**18, "end_ns": 10**18+10**9, **fields}


def settings() -> Settings:
    return Settings.from_text("schema_version: 1\nendpoints:\n - {url: 'http://p:8000/metrics', role: prefill, name: p0}")


def test_registered_entry_point() -> None:
    entry = next(e for e in importlib.metadata.entry_points(group="aiperf.plugins") if e.name == "sglang-metrics")
    assert entry.value == "aiperf_sglang_metrics:plugins.yaml"


def test_default_title_is_string() -> None:
    assert Settings.from_text("schema_version: 1").title == "SGLang P/D Metrics"


@pytest.mark.parametrize("config", [
    "schema_version: 2", "schema_version: 1\ntypo: true",
    "schema_version: 1\nmax_points: 0", "schema_version: 1\nmax_series: true",
    "schema_version: 1\nmetrics: {x: {group: mystery}}",
    "schema_version: 1\nmetrics: {x: {scale: .nan}}",
    "schema_version: 1\nmetrics: {x: {scale: -1}}",
    "schema_version: 1\nendpoints: [{url: 'http://a/metrics?token=secret', role: prefill, name: p}]",
    "schema_version: 1\nendpoints: [{url: 'http://a/metrics', role: P, name: p}]",
    "schema_version: 1\nendpoints: [{url: 'file:///x', role: prefill, name: p}]",
    "schema_version: 1\nendpoints: [{url: 'http://a', role: prefill, name: p}, {url: 'http://a/', role: decode, name: d}]",
])
def test_config_rejects_invalid_input(config: str) -> None:
    with pytest.raises(ValueError):
        Settings.from_text(config)


def test_counter_is_rate_not_lifetime_value() -> None:
    report = build_report(source_for("counter", [ts(rate=7, total=7)], "sglang:prompt_tokens_total", {"total": 100000}), settings())
    assert report["series"][0]["points"][0]["avg"] == 7
    assert report["series"][0]["unit"] == "tokens/s"


def test_cache_ratio_and_raw_values() -> None:
    report = build_report(source_for("gauge", [ts(avg=.72, min=.5, max=.8)], "sglang:token_usage"), settings())
    series = report["series"][0]
    assert series["points"][0]["avg"] == 72
    assert series["raw_timeslices"][0]["avg"] == .72
    assert series["group"] == "kvcache"


def test_millisecond_transfer_is_not_rescaled() -> None:
    r = build_report(source_for("histogram", [ts(count=2, sum=14, avg=7)], "sglang:kv_transfer_latency_ms"), settings())
    assert r["series"][0]["points"][0]["avg"] == 7
    assert r["series"][0]["unit"] == "ms"


def test_histogram_interval_estimates() -> None:
    r = build_report(source_for("histogram", [ts(count=100, sum=50, buckets={"1": 90, "2": 100, "+Inf": 100})]), settings())
    point = r["series"][0]["points"][0]
    assert point["avg"] == 500
    assert point["p95"] == 1500
    assert point["p99"] == 1900


@pytest.mark.parametrize("buckets", [None, {}, {"1": 0, "+Inf": 0}, {"1": 9, "2": 3, "+Inf": 10},
    {"1": -1, "+Inf": 3}, {"bad": 1, "+Inf": 2}, {"1": 2}, {"1": math.nan, "+Inf": 3},
    {"-1": 2, "+Inf": 3}, {"1": 10, "+Inf": 100}])
def test_invalid_empty_or_censored_histograms_are_missing(buckets: dict | None) -> None:
    assert quantile(buckets, .95) is None


def test_empty_interval_not_zero_latency() -> None:
    r = build_report(source_for("histogram", [ts(count=0, sum=0, avg=0)]), settings())
    assert r["series"][0]["points"][0]["avg"] is None


def test_no_timeslices_does_not_fabricate_a_line() -> None:
    r = build_report(source_for("gauge", [], stats={"avg": 7}), settings())
    assert r["series"][0]["points"] == []
    assert r["series"][0]["status"] == "no_timeslices"
    assert any("--slice-duration" in warning for warning in r["warnings"])


def test_aggregation_window_excludes_warmup_and_crossing_intervals() -> None:
    source = source_for("gauge", [ts(avg=99), ts(start_ns=10**18+2*10**9, end_ns=10**18+4*10**9, avg=22),
                                   ts(start_ns=10**18+3*10**9, end_ns=10**18+4*10**9, avg=3)])
    source["aggregation_time_filter"] = {"start_ns": 10**18+3*10**9, "end_ns": 10**18+8*10**9}
    report = build_report(source, settings())
    assert [p["avg"] for p in report["series"][0]["points"]] == [3000]
    assert report["series"][0]["points"][0]["start"] == 0


def test_invalid_ns_bounds_are_not_guessed() -> None:
    source = source_for("gauge", [ts(avg=1)])
    source["start_ns"] = "2026-09-19"
    assert not build_report(source, settings())["series"]


def test_concrete_phases_do_not_duplicate_aggregate() -> None:
    source = demo_source()
    original = copy.deepcopy(source)
    report = build_report(source, Settings.from_text(DEMO_CONFIG))
    assert {p["kind"] for p in report["phases"]} == {"warmup", "profiling"}
    assert len(report["series"]) == 36
    assert len({s["id"] for s in report["series"]}) == 36
    assert source == original


def test_tp_ranks_stay_separate() -> None:
    source = source_for("gauge", [ts(avg=2)], "sglang:num_queue_reqs")
    family = source["endpoint_summaries"]["http://p:8000/metrics"]["metrics"]["sglang:num_queue_reqs"]
    family["series"].append({"labels": {"rank": "1"}, "timeslices": [ts(avg=8)]})
    report = build_report(source, settings())
    assert len(report["series"]) == 2
    assert {s["points"][0]["avg"] for s in report["series"]} == {2, 8}


def test_unknown_endpoint_is_not_misclassified() -> None:
    report = build_report(source_for("gauge", [ts(avg=2)]), Settings())
    assert report["series"][0]["role"] == "unknown"


def test_missing_endpoints_visible() -> None:
    report = build_report({}, settings())
    assert report["endpoints"][0]["status"] == "missing"
    assert report["status"] == "partial"
    assert not report["series"]


def test_non_finite_values_and_partial_slice() -> None:
    report = build_report(source_for("gauge", [ts(avg=math.inf, min=math.nan, max=1, is_complete=False)]), settings())
    p = report["series"][0]["points"][0]
    assert p["avg"] is None and p["min"] is None and p["complete"] is False
    orjson.loads(orjson.dumps(report))


def test_credentials_are_not_persisted() -> None:
    src = source_for("gauge", [ts(avg=1)])
    src["input_config"] = {"api_key": "TOP_SECRET"}
    raw = src["endpoint_summaries"].pop("http://p:8000/metrics")
    raw["metrics"]["sglang:queue_time_seconds"]["series"][0]["labels"]["authorization"] = "TOP_SECRET"
    src["endpoint_summaries"]["http://user:TOP_SECRET@p:8000/metrics?token=TOP_SECRET"] = raw
    result = orjson.dumps(snapshot(src)).decode()
    assert "TOP_SECRET" not in result
    assert "http://p:8000/metrics" in result


def test_unknown_non_sglang_not_auto_included() -> None:
    src = source_for("gauge", [ts(avg=2)], "process_open_fds")
    assert not build_report(src, settings())["series"]
    cfg = Settings(metrics={"process_open_fds": {"group": "other", "title": "FDs"}})
    assert build_report(src, cfg)["series"][0]["title"] == "FDs"


def test_limits_fail_instead_of_silent_truncation() -> None:
    with pytest.raises(ValueError, match="max_points"):
        build_report(source_for("gauge", [ts(avg=1), ts(avg=2)]), Settings(max_points=1))
    with pytest.raises(ValueError, match="max_series"):
        build_report(demo_source(), Settings(max_series=1))


def test_html_escapes_script_terminators() -> None:
    report = build_report({}, Settings(title='</script><script>window.pwned=1</script>'))
    html = render_html(report)
    assert '</script><script>window.pwned' not in html
    assert '\\u003c/script' in html
    assert "connect-src 'none'" in html


def test_bundle_is_self_contained(tmp_path: Path) -> None:
    asyncio.run(write_bundle(demo_source(), Settings.from_text(DEMO_CONFIG), tmp_path))
    manifest = orjson.loads((tmp_path / "manifest.json").read_bytes())
    assert set(manifest["files"]) == {"metrics.json", "source.json", "report.html"}
    assert manifest["status"] == "complete"
    assert orjson.loads((tmp_path / "metrics.json").read_bytes())["benchmark_id"] == "synthetic-demo"


def test_symlink_output_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        asyncio.run(write_bundle(demo_source(), Settings(), link))


def test_plugin_manifest_resource() -> None:
    path = Path(__file__).parents[1] / "src/aiperf_sglang_metrics/plugins.yaml"
    assert yaml.safe_load(path.read_text())["data_exporter"]["sglang_metrics_report"]["class"].endswith(":SGLangMetricsExporter")


@pytest.mark.parametrize("config", [
    "schema_version: true", "schema_version: 1\nendpoints: null",
    "schema_version: 1\ntitle: [bad]", "schema_version: 1\nmetrics: {x: {title: 7}}",
])
def test_config_type_errors_are_explicit(config: str) -> None:
    with pytest.raises(ValueError):
        Settings.from_text(config)


def test_inconsistent_histogram_count_is_not_used_for_percentile() -> None:
    source = source_for("histogram", [ts(count=1, sum=1, buckets={"1": 100, "+Inf": 100})])
    point = build_report(source, settings())["series"][0]["points"][0]
    assert point["p95"] is None


def test_failed_rerun_cannot_leave_stale_success(tmp_path: Path) -> None:
    asyncio.run(write_bundle(demo_source(), Settings(), tmp_path))
    assert (tmp_path / "report.html").exists()
    with pytest.raises(ValueError, match="max_series"):
        asyncio.run(write_bundle(demo_source(), Settings(max_series=1), tmp_path))
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "report.html").exists()


def test_endpoint_redaction_collision_fails_instead_of_merging() -> None:
    source = source_for("gauge", [ts(avg=1)])
    endpoint = source["endpoint_summaries"].pop("http://p:8000/metrics")
    source["endpoint_summaries"] = {
        "http://p:8000/metrics?target=p": endpoint,
        "http://p:8000/metrics?target=d": endpoint,
    }
    with pytest.raises(ValueError, match="collide"):
        snapshot(source)
