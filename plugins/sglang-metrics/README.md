<!-- SPDX-License-Identifier: Apache-2.0 -->
# SGLang metrics report plugin

English | [中文安装与使用](README.zh-CN.md)

An opt-in AIPerf `data_exporter` for **one experiment's Prefill, Decode, PD
transfer and hierarchical KV-cache visibility**. No loadgen/core changes,
additional collector, database, live UI server, or cross-experiment platform.
The additional offline HTML borrows MiMo's metric-browser interaction pattern;
it is original code, not copied MiMo assets or an injected AIPerf Dash tab.

## Installation and host compatibility

Install into the environment that actually performs final AIPerf export:

```bash
git clone --branch feature/sglang-metrics https://github.com/jinyizhong/aiperf.git
cd aiperf
uv sync
uv pip install --python .venv/bin/python -e ./plugins/sglang-metrics
```

The compatibility workflow tests these exact hosts with the same plugin:

| Host | Pinned commit |
| --- | --- |
| main / 0.13.0 | `23a63a493604bc4016388543316934afa35e1c56` |
| v0.12.0 | `be53bf2953d30e46c500e6a80fc1f8b6f84bc718` |

The plugin never upgrades AIPerf for you. Keep the feature checkout separate
when using the tag, which does not itself contain this plugin:

```bash
git fetch origin tag v0.12.0
git worktree add --detach ../aiperf-host-v012 v0.12.0
uv venv ../aiperf-v012-env --python 3.12
uv pip install --python ../aiperf-v012-env/bin/python \
  -e ../aiperf-host-v012 -e ./plugins/sglang-metrics
```

A later main revision must be revalidated. Protocols used are entry-point
discovery, `ExporterConfig.server_metrics_results`, completed interval
summaries, artifact directories, and deferred exporters. Optional named-phase
fields are not assumed to exist. The host version is retained in the report.

## Run against SGLang

Start each worker with `--enable-metrics`; ensure direct `/metrics` endpoints
are reachable. Copy `examples/pd-report.yaml` and replace P/D URLs and names.
The mapping does **not** configure collection: pass the endpoints to AIPerf too.

```bash
export AIPERF_SGLANG_METRICS_CONFIG="$PWD/plugins/sglang-metrics/examples/pd-report.yaml"
.venv/bin/aiperf profile \
  --url http://gateway:8000 --model kimi-k3 --endpoint-type chat --streaming \
  --tokenizer /models/Kimi-K3 --tokenizer-trust-remote-code \
  --request-count 32 --concurrency 2 \
  --server-metrics http://prefill-0:8000/metrics http://decode-0:8000/metrics \
  --slice-duration 1 --artifact-dir artifacts/pd-smoke
```

Retain your actual dataset, lengths, load and SLO arguments. `--slice-duration 1`
is needed for trends. Do not scrape a load-balanced URL that alternates workers.
Unset `AIPERF_SGLANG_METRICS_CONFIG` to disable the plugin. In Kubernetes, the
exporting container needs the plugin and mounted configuration; installing on
your laptop does not install it into remote pods. Concurrent deferred uploaders
may require an explicit post-completion upload of the report directory.

## Configuration

```yaml
schema_version: 1
title: SGLang P/D Metrics
endpoints:
  - {url: 'http://prefill-0:8000/metrics', role: prefill, name: p0}
  - {url: 'http://decode-0:8000/metrics', role: decode, name: d0}
max_series: 1000
max_points: 20000
```

Roles: `prefill`, `decode`, `transfer`, `unknown`. Explicit mapping wins over
`role`/`disaggregation_mode` labels. No hostname guessing. Mapping URLs reject
credentials, queries and fragments; configure authentication in AIPerf.
All `sglang:` families are retained. Other names require exact display overrides:

```yaml
metrics:
  my_hicache_read_queue_depth:
    title: Custom storage read queue
    group: kvcache
    unit: requests
    scale: 1
```

This is an **illustrative custom metric**, not a promised SGLang name.
Groups remain `prefill`, `decode`, `transfer`, `kvcache`, `other`. Overrides are
metadata, not PromQL. Genuine custom `direction` labels matching the four
supported directions are searchable without guessing their meaning.

## What is covered

Native names are checked against [this SGLang collector revision](https://github.com/sgl-project/sglang/blob/76f9213a411018547f4fd6a75f36feaa4d6bed58/python/sglang/srt/observability/metrics_collector.py).
Older deployments may expose fewer metrics. TYPE, rank, pool, reason and stage
labels come from the actual data, not this display catalog.

Queues: scheduler waiting, grammar waiting, P bootstrap/inflight, D
preallocation/transfer; running, paused, retracted events and per-stage request
latencies remain separate. A retraction event counter is not a retracted queue.

L1 means GPU/device; L2 means **local host DRAM**; L3 means storage backend,
which may itself be remote RAM (e.g. Mooncake), not necessarily SSD.
Metric names below omit `sglang:`:

| Direction | Native metrics | Meaning |
| --- | --- | --- |
| L1→L2 | `hicache_backup_tokens_total`, `hicache_backup_bytes_total`, `hicache_backup_duration_seconds` | Device-to-host backup counts, physical traffic, merged-copy duration |
| L2→L1 | `load_back_tokens_total`, `load_back_bytes_total`, `load_back_duration_seconds` | Host-to-device load-back; duration does not include all queue/fence waits |
| L2→L3 | `backuped_tokens_total`, `backup_pgs`, `backup_bandwidth` | Host-to-storage write; preserve upstream spelling `backuped` |
| L3→L2 | `prefetched_tokens_total`, `prefetch_pgs`, `prefetch_bandwidth` | Storage-to-host prefetch; loaded does not mean ultimately reused |

The catalog additionally covers per-pool pressure/slots, host capacity, cache
source breakdown, unfulfilled/deferred storage hits, dropped backups, eviction,
PD failures/retries/allocation/bootstrap, scheduler CPU/stage/forward time,
CUDA graph modes, speculative acceptance and aborts.

The coverage panel reports each metric **per endpoint and phase** as available,
not collected, no timeslices, or no observations. It separately documents gaps
in native instrumentation: D retracted-queue depth, host-copy and storage queues,
queue/fence waits, PD gather/RDMA/scatter/staging, and KV kernel physical I/O.
It never invents metric families or zero lines for these gaps. Bring additional
engine instrumentation through explicit mappings when available.

## Artifacts and semantics

Each run adds four files in `<artifact-dir>/sglang-metrics/`:

| File | Contents |
| --- | --- |
| `report.html` | Offline trends, direction/queue/role/phase filters, search, shared time axes |
| `metrics.json` | Series, coverage and instrumentation gaps, raw interval stats, phase windows, host version |
| `source.json` | Sanitized completed interval summaries for regeneration, not raw scrapes |
| `manifest.json` | Manifest-last publication marker, file list and run/time identity |

```bash
.venv/bin/aiperf-sglang-report \
  --source artifacts/pd-smoke/sglang-metrics/source.json \
  --config plugins/sglang-metrics/examples/pd-report.yaml \
  --output artifacts/regenerated-report
```

Use this plugin's `source.json`, **not** the differently structured native
`server_metrics_export.json`. Write into a different directory or back up first.
Native AIPerf results are unchanged. A standalone demo works without AIPerf:
`aiperf-sglang-report --demo --output artifacts/demo` (synthetic, not measured).

Correctness rules:

- Warmup/profiling remain separate. Prefer concrete phases; otherwise use the
  aggregation window. Drop cross-boundary slices with warnings, never prorate.
- Counters use interval rates, gauges interval avg/min/max; histogram mean is
  sum/count and percentiles are estimated from interval cumulative-le buckets.
  Empty/inconsistent/+Inf-censored quantiles are missing. Never average P95s.
- Keep endpoint/rank/pool independent. Logical tokens can repeat across TP
  ranks; physical byte shards differ. No automatic cross-rank sum or ratio mean.
- L3 bandwidth histograms describe per-operation active wire bandwidth, not
  wall-clock experiment throughput. Transfer-size histograms are not MB/s.
- Cache query hits, copies into L2, final reuse and evictions are distinct.
  `prefill_effective_tokens{mode="input"}` is uncached input; all modes form
  the denominator. Legacy `cache_source="total"` is not added to tier splits.
- Mamba metrics named `*_tokens` may actually count state slots. Token pool
  usage is not total HBM. Millisecond latency is not multiplied by 1000 twice.
- Missing data is not zero. `available` is not a health verdict; `complete`
  describes artifact generation, not exhaustive optional instrumentation.
- Limits fail explicitly. Display EMA/log controls do not modify saved data.

Reports run deferred, outside the measured request path. Failures preserve native
results; stale successful reports are invalidated before a rerun. Symlink
rejection, atomic files and manifest-last publication remain in place. URLs and
common credential labels are redacted; this is not a general PII detector.
Embedded data is escaped, labels use `textContent`, and CSP blocks connections.

## Tests and review evidence

[Compatibility workflow](https://github.com/jinyizhong/aiperf/actions/workflows/sglang-metrics-compat.yml)
installs the real pinned main/tag host and the same plugin. It runs unit tests,
real registry/model integration, then actual `aiperf profile` against a local
SSE endpoint and P/D Prometheus fixtures, with enabled, disabled and malformed
plugin configuration. Logs, pinned host SHA, resolved dependencies and output
reports are uploaded. No runtime classes are replaced in the main-flow test.

```bash
uv pip install --python .venv/bin/python -e './plugins/sglang-metrics[test]'
(cd plugins/sglang-metrics && ../../.venv/bin/python -m pytest -q)
.venv/bin/python plugins/sglang-metrics/tools/compat_smoke.py --output artifacts/compat-main
../aiperf-v012-env/bin/python plugins/sglang-metrics/tools/compat_smoke.py --output artifacts/compat-v012
```

The tokenizer is generated locally into an isolated HF cache. No model download
or GPU is required. This validates AIPerf integration, **not a real SGLang/GPU
cluster or cache-performance experiment**. Browser checks use Playwright:
`AIPERF_SGLANG_BROWSER_TESTS=1`; optionally set `CHROMIUM_EXECUTABLE`.
