# SPDX-License-Identifier: Apache-2.0
"""Exercise real AIPerf against HTTP SSE/Prometheus fixtures, without a GPU.

No AIPerf classes are replaced. These are compatibility tests, not SGLang
performance measurements. Tokenizer data is created in an isolated HF cache.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import orjson
from aiohttp import web


def exposition(role: str, elapsed: float) -> str:
    """Emit real Prometheus exposition with increasing counters and histograms."""
    count = max(1, int(elapsed * 20))
    labels = f'tp_rank="0",role="{role}"'
    lines = []

    def gauge(name: str, value: float) -> None:
        lines.extend([f"# HELP {name} Synthetic fixture", f"# TYPE {name} gauge",
                      f"{name}{{{labels}}} {value}"])

    def counter(name: str) -> None:
        lines.extend([f"# HELP {name} Synthetic fixture", f"# TYPE {name} counter",
                      f"{name}{{{labels}}} {count * 1024}"])

    def histogram(name: str, scale: float) -> None:
        lines.extend([f"# HELP {name} Synthetic fixture", f"# TYPE {name} histogram"])
        for bound, n in ((str(.001*scale), 0), (str(.01*scale), count), ("+Inf", count)):
            lines.append(f'{name}_bucket{{{labels},le="{bound}"}} {n}')
        lines.extend([f"{name}_sum{{{labels}}} {count*.004*scale}",
                      f"{name}_count{{{labels}}} {count}"])

    for name in ("num_queue_reqs", "num_running_reqs", "num_grammar_queue_reqs",
                 "num_prefill_bootstrap_queue_reqs", "num_prefill_inflight_queue_reqs",
                 "num_decode_prealloc_queue_reqs", "num_decode_transfer_queue_reqs"):
        gauge("sglang:" + name, float(count % 7))
    for name, value in (("token_usage", .45), ("full_token_usage", .45),
                        ("mamba_usage", .5), ("hicache_host_used_tokens", 4096),
                        ("hicache_host_total_tokens", 8192)):
        gauge("sglang:" + name, value)
    for name in ("prompt_tokens", "generation_tokens", "hicache_backup_tokens",
                 "hicache_backup_bytes", "load_back_tokens", "load_back_bytes",
                 "backuped_tokens", "prefetched_tokens"):
        counter("sglang:" + name + "_total")
    for name in ("hicache_backup_duration_seconds", "load_back_duration_seconds",
                 "queue_time_seconds", "eviction_duration_seconds"):
        histogram("sglang:" + name, 1)
    for name in ("backup_pgs", "prefetch_pgs", "backup_bandwidth", "prefetch_bandwidth",
                 "kv_transfer_latency_ms", "kv_transfer_speed_gb_s"):
        histogram("sglang:" + name, 1000)
    return "\n".join(lines) + "\n"


def make_app() -> web.Application:
    """Serve compatible completions and distinct direct P/D metrics paths."""
    app = web.Application()
    started = time.monotonic()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def models(request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "compat-model", "object": "model"}]})

    async def metrics(request: web.Request) -> web.Response:
        return web.Response(text=exposition(request.match_info.get("role", "prefill"), time.monotonic()-started), content_type="text/plain")

    async def completions(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        count = min(max(int(body.get("max_tokens", body.get("max_completion_tokens", 8))), 1), 16)
        usage = {"prompt_tokens": 32, "completion_tokens": count, "total_tokens": 32+count}
        if not body.get("stream"):
            return web.json_response({"id": "fixture", "object": "text_completion", "model": "compat-model",
                "choices": [{"index": 0, "text": " token"*count, "finish_reason": "length"}], "usage": usage})
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for _ in range(count):
            await asyncio.sleep(.025)
            payload = {"id": "fixture", "object": "text_completion", "created": int(time.time()),
                       "model": "compat-model", "choices": [{"index": 0, "text": " token", "finish_reason": None}]}
            await response.write(b"data: " + orjson.dumps(payload) + b"\n\n")
        payload = {"id": "fixture", "object": "text_completion", "model": "compat-model",
                   "choices": [{"index": 0, "text": "", "finish_reason": "length"}], "usage": usage}
        await response.write(b"data: " + orjson.dumps(payload) + b"\n\ndata: [DONE]\n\n")
        await response.write_eof()
        return response

    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/{role}/metrics", metrics)
    app.router.add_post("/v1/completions", completions)
    return app


def create_tokenizer(cache: Path) -> None:
    """Use a cached repository ID, matching the host's offline resolver."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast

    repo = cache / "models--sglang-metrics--fixture"
    snapshot_id = "0"*40
    snapshot = repo / "snapshots" / snapshot_id
    snapshot.mkdir(parents=True, exist_ok=True)
    (repo / "refs").mkdir(parents=True, exist_ok=True)
    (repo / "refs/main").write_text(snapshot_id, encoding="utf-8")
    vocab = {"[UNK]": 0, "token": 1, **{f"word{i}": i+2 for i in range(1024)}}
    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    fast = PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token="[UNK]")
    fast.save_pretrained(snapshot)


async def run_case(executable: str, output: Path, config: Path, base: str, mode: str) -> dict[str, Any]:
    """Launch the full host flow and validate both native and plugin artifacts."""
    artifact = output / mode
    command = [executable, "profile", "--url", base, "--model", "compat-model",
        "--tokenizer", "sglang-metrics/fixture", "--endpoint-type", "completions", "--streaming",
        "--isl", "32", "--isl-stddev", "0", "--osl", "8", "--osl-stddev", "0",
        "--request-count", "48", "--warmup-request-count", "2", "--concurrency", "2",
        "--num-dataset-entries", "64", "--extra-inputs", "ignore_eos:true",
        "--slice-duration", "1", "--no-gpu-telemetry", "--ui", "simple",
        "--artifact-dir", str(artifact), "--server-metrics",
        base+"/prefill/metrics", base+"/decode/metrics"]
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_CACHE=str(output/"hf-cache"))
    if mode == "disabled":
        env.pop("AIPERF_SGLANG_METRICS_CONFIG", None)
    else:
        env["AIPERF_SGLANG_METRICS_CONFIG"] = str(config)
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=240)
    except asyncio.TimeoutError:
        process.kill()
        stdout, _ = await process.communicate()
        await asyncio.to_thread((output/f"{mode}.log").write_bytes, stdout)
        raise AssertionError(f"{mode}: AIPerf CLI timed out") from None
    await asyncio.to_thread((output/f"{mode}.log").write_bytes, stdout)
    assert process.returncode == 0, stdout.decode(errors="replace")[-18000:]
    assert (artifact/"profile_export_aiperf.json").is_file(), "native benchmark export missing"
    if mode != "enabled":
        assert not (artifact/"sglang-metrics/manifest.json").exists()
        return {"mode": mode, "status": "passed", "returncode": process.returncode}
    for filename in ("manifest.json", "source.json", "metrics.json", "report.html"):
        assert (artifact/"sglang-metrics"/filename).is_file(), filename
    raw = await asyncio.to_thread((artifact/"sglang-metrics/metrics.json").read_bytes)
    report = orjson.loads(raw)
    assert {"prefill", "decode"} <= {s["role"] for s in report["series"]}
    assert {"prefill", "decode", "transfer", "kvcache"} <= {s["group"] for s in report["series"] if s["points"]}
    for series in report["series"]:
        phase = next(p for p in report["phases"] if p["id"] == series["phase_id"])
        for point in series["points"]:
            assert phase["start"] <= point["start"] < point["end"] <= phase["end"]
    if report.get("coverage"):
        assert {"L1->L2", "L2->L1", "L2->L3", "L3->L2"} <= {s.get("direction") for s in report["series"] if s["points"]}
    assert any(p["kind"] == "profiling" for p in report["phases"])
    return {"mode": mode, "status": "passed", "returncode": 0, "series": len(report["series"]),
            "phase_names": [p["name"] for p in report["phases"]], "report_status": report["status"]}


async def main_async(output: Path, executable: str) -> None:
    """Run independent disabled, enabled and invalid-plugin-config cases."""
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(create_tokenizer, output/"hf-cache")
    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    config = output/"report.yaml"
    await asyncio.to_thread(config.write_text, f"schema_version: 1\nendpoints:\n"
        f"  - {{url: '{base}/prefill/metrics', role: prefill, name: p0}}\n"
        f"  - {{url: '{base}/decode/metrics', role: decode, name: d0}}\n", encoding="utf-8")
    results = []
    try:
        for mode in ("disabled", "enabled"):
            results.append(await run_case(executable, output, config, base, mode))
        await asyncio.to_thread(config.write_text, "schema_version: 999\n", encoding="utf-8")
        results.append(await run_case(executable, output, config, base, "invalid_config"))
    finally:
        await runner.cleanup()
        summary = {"aiperf_version": version("aiperf"), "python": sys.version,
                   "fixture": "local HTTP SSE + Prometheus; no SGLang/GPU", "results": results}
        await asyncio.to_thread((output/"compat-results.json").write_bytes, orjson.dumps(summary, option=orjson.OPT_INDENT_2))
    print(orjson.dumps(summary, option=orjson.OPT_INDENT_2).decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aiperf", default=str(Path(sys.executable).with_name("aiperf")))
    arguments = parser.parse_args()
    asyncio.run(main_async(arguments.output.resolve(), arguments.aiperf))
