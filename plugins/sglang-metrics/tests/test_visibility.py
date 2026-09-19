# SPDX-License-Identifier: Apache-2.0
"""New coverage semantics, using synthetic interval data rather than a GPU run."""
from __future__ import annotations
import asyncio
import copy
import math
import os
from pathlib import Path
from typing import Any
import orjson
import pytest
from aiperf_sglang_metrics.catalog import describe
from aiperf_sglang_metrics.catalog_extended import DIRECTION_METRICS, EXTRA_CATALOG
from aiperf_sglang_metrics.config import Settings
from aiperf_sglang_metrics.normalize import build_report
from aiperf_sglang_metrics.report import write_bundle
from aiperf_sglang_metrics.visibility import enrich_report

NANO = 10**9
P_URL = "http://p:8000/metrics"
D_URL = "http://d:8000/metrics"


def fixture_source() -> dict[str, Any]:
    start = 1789819200 * NANO
    endpoints = {}
    for url in (P_URL, D_URL):
        metrics = {}
        for names in DIRECTION_METRICS.values():
            for name in names:
                kind = "histogram" if name.endswith(("_seconds", "_pgs", "_bandwidth")) else "counter"
                point = {"start_ns": start, "end_ns": start + NANO}
                if kind == "histogram":
                    point.update(count=100, sum=.4, avg=.004, buckets={"0.001": 0, "0.01": 100, "+Inf": 100})
                else:
                    point.update(total=2048, rate=2048)
                metric = "sglang:" + name + ("_total" if kind == "counter" else "")
                metrics[metric] = {"type": kind, "series": [{"labels": {"tp_rank": "0", "pool": "kv"}, "timeslices": [point]}]}
        for name in ("num_queue_reqs", "num_grammar_queue_reqs", "num_running_reqs", "num_paused_reqs",
                     "num_prefill_bootstrap_queue_reqs", "num_prefill_inflight_queue_reqs",
                     "num_decode_prealloc_queue_reqs", "num_decode_transfer_queue_reqs"):
            metrics["sglang:" + name] = {"type": "gauge", "series": [{"labels": {"tp_rank": "0"},
                "timeslices": [{"start_ns": start, "end_ns": start+NANO, "avg": 0, "min": 0, "max": 0}]}]}
        endpoints[url] = {"endpoint_url": url, "metrics": metrics}
    return {"benchmark_id": "visibility-fixture", "start_ns": start, "end_ns": start+2*NANO,
            "_aiperf_version": "test-host", "endpoint_summaries": endpoints,
            "endpoints_configured": [P_URL,D_URL], "endpoints_successful": [P_URL,D_URL]}


def config() -> Settings:
    return Settings(endpoints={P_URL:{"name":"p0","role":"prefill"},D_URL:{"name":"d0","role":"decode"}})


def enriched(source: dict | None = None) -> dict:
    src = fixture_source() if source is None else source
    return enrich_report(build_report(src, config()), src)


@pytest.mark.parametrize("direction,names", DIRECTION_METRICS.items())
def test_four_directions_are_native_and_separate(direction: str, names: tuple[str, ...]) -> None:
    report = enriched()
    selected = [s for s in report["series"] if s["direction"] == direction]
    assert len(selected) == len(names)*2
    assert {s["role"] for s in selected} == {"prefill", "decode"}
    assert all(s["group"] == "kvcache" and s["points"] for s in selected)


@pytest.mark.parametrize("name,unit,scale", [
    ("sglang:hicache_backup_bytes_total", "bytes/s", 1),
    ("sglang:load_back_bytes_total", "bytes/s", 1),
    ("sglang:hicache_backup_duration_seconds", "ms", 1000),
    ("sglang:load_back_duration_seconds", "ms", 1000),
    ("sglang:backuped_tokens_total", "tokens/s", 1),
    ("sglang:prefetched_tokens_total", "tokens/s", 1),
    ("sglang:backup_pgs", "pages", 1),
    ("sglang:prefetch_bandwidth", "GB/s", 1),
    ("sglang:kv_transfer_alloc_ms", "ms", 1),
    ("sglang:kv_transfer_total_mb", "MB", 1),
    ("sglang:mamba_used_tokens", "slots", 1),
    ("sglang:scheduler_stage_seconds_total", "seconds/s", 1),
])
def test_units_do_not_confuse_rates_sizes_or_state_slots(name: str, unit: str, scale: float) -> None:
    metadata = describe(name,"prefill",{})
    assert metadata["unit"] == unit
    assert metadata["scale"] == scale


def test_zero_queue_is_available_not_missing() -> None:
    report = enriched()
    entry = next(e for e in report["coverage"]["entries"] if e["metric"] == "sglang:num_queue_reqs")
    assert entry["status"] == "available"
    assert all(e["status"] == "available" for e in report["coverage"]["entries"] if e["category"] == "cache_io")


def test_coverage_is_per_endpoint_and_phase() -> None:
    src = fixture_source()
    del src["endpoint_summaries"][D_URL]["metrics"]["sglang:load_back_bytes_total"]
    report = enriched(src)
    entries = [e for e in report["coverage"]["entries"] if e["metric"] == "sglang:load_back_bytes_total"]
    assert {e["role"]:e["status"] for e in entries} == {"prefill":"available","decode":"not_collected"}
    assert not any(e["role"]=="prefill" and e["channel"]=="decode_prealloc" for e in report["coverage"]["entries"])


def test_no_timeslices_and_no_observations_are_distinct() -> None:
    src = fixture_source()
    family = src["endpoint_summaries"][P_URL]["metrics"]["sglang:backup_bandwidth"]
    family["series"][0]["timeslices"] = []
    assert next(e for e in enriched(src)["coverage"]["entries"] if e["role"]=="prefill" and e["metric"]=="sglang:backup_bandwidth")["status"] == "no_timeslices"
    start = src["start_ns"]
    family["series"][0]["timeslices"] = [{"start_ns":start,"end_ns":start+NANO,"count":0,"sum":0,"buckets":{"1":0,"+Inf":0}}]
    assert next(e for e in enriched(src)["coverage"]["entries"] if e["role"]=="prefill" and e["metric"]=="sglang:backup_bandwidth")["status"] == "no_observations"


def test_upstream_instrumentation_gaps_never_create_fake_series() -> None:
    report = enriched()
    assert report["coverage"]["instrumentation_gaps"]
    assert not any("retracted_queue" in s["metric"] for s in report["series"])
    assert not any("rdma_seconds" in s["metric"] for s in report["series"])
    assert "remote RAM" in report["coverage"]["tier_definition"]["L3"]


def test_cache_source_and_scheduler_labels_are_preserved() -> None:
    src = fixture_source()
    metrics = src["endpoint_summaries"][P_URL]["metrics"]
    timeslices = [{"start_ns":src["start_ns"],"end_ns":src["start_ns"]+NANO,"rate":100,"total":100}]
    metrics["sglang:cached_tokens_total"] = {"type":"counter","series":[{"labels":{"cache_source":s},"timeslices":timeslices} for s in ("device","host","storage","total")]}
    metrics["sglang:scheduler_stage_seconds_total"] = {"type":"counter","series":[{"labels":{"category":"process_queue"},"timeslices":timeslices}]}
    original = copy.deepcopy(src)
    report = enriched(src)
    assert {s["tier"] for s in report["series"] if s["metric"]=="sglang:cached_tokens_total"} == {"L1","L2","L3",""}
    assert next(s for s in report["series"] if s["metric"]=="sglang:scheduler_stage_seconds_total")["stage"] == "process_queue"
    assert src == original


def test_legacy_result_without_phase_fields_remains_supported(tmp_path: Path) -> None:
    src = fixture_source()
    report = asyncio.run(write_bundle(src,config(),tmp_path))
    assert report["phases"][0]["name"] == "profiling"
    assert report["runtime"]["aiperf_version"] == "test-host"
    assert orjson.loads((tmp_path/"metrics.json").read_bytes())["coverage"]["entries"]
    assert set(orjson.loads((tmp_path/"manifest.json").read_bytes())["files"]) == {"metrics.json","source.json","report.html"}
    raw = orjson.loads((tmp_path/"source.json").read_bytes())
    assert raw["_aiperf_version"] == "test-host"
    second = asyncio.run(write_bundle(raw,config(),tmp_path/"rerender"))
    assert report == second


def test_every_catalog_row_retains_provenance_and_sane_scale() -> None:
    for name in EXTRA_CATALOG:
        row = describe(name,"decode",{})
        assert row["catalog_source"].endswith("metrics_collector.py")
        assert math.isfinite(row["scale"]) and row["scale"]>0


def test_visibility_browser_controls(tmp_path: Path) -> None:
    if not os.environ.get("AIPERF_SGLANG_BROWSER_TESTS"):
        pytest.skip("Set AIPERF_SGLANG_BROWSER_TESTS=1 for real browser checks")
    from playwright.sync_api import sync_playwright
    asyncio.run(write_bundle(fixture_source(),config(),tmp_path))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),args=["--no-sandbox"])
        page = browser.new_page(viewport={"width":1360,"height":900})
        errors = []
        page.on("pageerror",lambda err: errors.append(str(err)))
        page.set_content((tmp_path/"report.html").read_text(encoding="utf-8"))
        page.locator("#direction").select_option("L2->L3")
        assert page.locator("#grid canvas").count() == 6
        page.locator("#role").select_option("decode")
        assert page.locator("#grid canvas").count() == 3
        page.locator("#direction").select_option("all")
        page.locator("#queues-only").check()
        assert page.locator("#grid canvas").count() == 6
        page.locator("#coverage").evaluate("node=>node.open=true")
        assert "未提供此独立 Gauge" in page.locator("#coverage-gaps").inner_text()
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
