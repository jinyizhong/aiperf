<!-- SPDX-License-Identifier: Apache-2.0 -->
# SGLang metrics report plugin

An opt-in AIPerf `data_exporter` plugin for **one experiment's Prefill, Decode,
PD transfer and KV-cache observability**. No load-generator changes, extra
collector, database, UI server or cross-experiment platform.

The offline page borrows MiMo's metric-browser interaction pattern: category
navigation, search, role/phase filters, metric cards and synchronized trend
axes. It is original code, not a copy of MiMo assets. This release creates an
**additional final HTML artifact**, not a new tab in AIPerf's existing Dash UI,
and does not provide live monitoring during a run.

## Compatibility and installation

The integration targets this repository's **0.13.0 main API at
`23a63a493604bc4016388543316934afa35e1c56`**: `aiperf.plugins` entry-point discovery,
`DataExporterProtocol`, `ExporterConfig.server_metrics_results`, interval
`timeslices`, and deferred exporter failure isolation. Older PyPI versions are
not implicitly supported. The plugin does not upgrade or install AIPerf for you.

From this repository, in the same Python environment that runs AIPerf:

```bash
uv sync
uv pip install --python .venv/bin/python -e ./plugins/sglang-metrics
```

This package additionally requires `PyYAML` and `orjson`. Install these using
`uv` if they are not already in that environment. A self-contained demo can run
without AIPerf:

```bash
uv venv /tmp/sglang-report-env
uv pip install --python /tmp/sglang-report-env/bin/python ./plugins/sglang-metrics
/tmp/sglang-report-env/bin/aiperf-sglang-report --demo --output ./artifacts/sglang-demo
```

The demo is synthetic and clearly labeled; it is not a performance claim.

## Use with a real SGLang run

1. Start SGLang workers with `--enable-metrics` and verify each direct worker's
   `/metrics` endpoint is reachable from the AIPerf collector. Do not use a
   load-balanced metrics URL that alternates workers.
2. Copy `examples/pd-report.yaml`, replacing endpoint URLs and instance names.
   These URLs map roles; **they do not configure collection**.
3. Enable the plugin with an absolute readable config path, and pass the same
   endpoints to AIPerf. Retain your existing model, dataset, length and load
   arguments. **`--slice-duration 1` is required to produce trend timeslices.**

```bash
export AIPERF_SGLANG_METRICS_CONFIG="$PWD/plugins/sglang-metrics/examples/pd-report.yaml"

# Replace the endpoint/model and retain your real workload arguments.
.venv/bin/aiperf profile \
  --url http://gateway:8000 \
  --model kimi-k3 --endpoint-type chat --streaming \
  --request-count 32 --concurrency 2 \
  --server-metrics http://prefill-0:8000/metrics http://decode-0:8000/metrics \
  --slice-duration 1 \
  --artifact-dir artifacts/pd-smoke
```

Open `artifacts/pd-smoke/sglang-metrics/report.html` after completion. No network
access, CDN, Prometheus availability or external JavaScript is required to view
it. The browser can export its normalized JSON. Unset
`AIPERF_SGLANG_METRICS_CONFIG` to disable the plugin.

For Kubernetes, install the plugin in the image performing final export and
mount the YAML at the configured path in that container. A local installation
alone does not install the package into remote pods. Automatic upload tools
that run in the same deferred stage may need an explicit post-completion
upload of this directory; Kubernetes/operator integration is not tested here.

## Configuration

```yaml
schema_version: 1
endpoints:
  - {url: 'http://prefill-0:8000/metrics', role: prefill, name: p0}
  - {url: 'http://decode-0:8000/metrics', role: decode, name: d0}
max_series: 1000
max_points: 20000
```

Roles are `prefill`, `decode`, `transfer`, or `unknown`. Exact endpoint mapping
wins over exporter `role`/`disaggregation_mode` labels. Unmapped endpoints stay
unknown rather than being guessed from their hostname. Credentials and URL
query parameters are not accepted in mapping URLs; configure authentication in
AIPerf instead. Use distinct direct endpoint URLs.

All `sglang:` metric families remain available, including unknown families in
Other. Custom exporter metrics require an exact-name override under `metrics`:

```yaml
metrics:
  my_pd_gather_seconds:
    title: Gather latency
    group: transfer
    unit: ms
    scale: 1000
```

Available groups are `prefill`, `decode`, `transfer`, `kvcache`, `other`.
Overrides are display metadata, not PromQL. The example's gather/RDMA/scatter/
staging names are **illustrative custom instrumentation**, not promised SGLang
metrics. Instrument the engine separately if these are unavailable.

## Artifacts and measurement semantics

Each run writes `sglang-metrics/` alongside native AIPerf artifacts:

| File | Contents |
| --- | --- |
| `report.html` | Self-contained trend explorer, defaulting to profiling |
| `metrics.json` | Versioned normalized series, phase windows, roles, labels, units, warnings |
| `source.json` | Sanitized completed server-metric summaries used by this plugin |
| `manifest.json` | Publication marker, run identity, time bounds and file list |

The plugin consumes the completed **in-memory** server-metrics result. It never
races an incomplete JSONL or other exporter's output. AIPerf remains responsible
for collection, counter resets and time-slice construction. Source data here
means **interval summaries**, not raw scrapes; native JSONL/Parquet remains
unchanged. The source snapshot can be regenerated with:

```bash
.venv/bin/aiperf-sglang-report \
  --source artifacts/pd-smoke/sglang-metrics/source.json \
  --config plugins/sglang-metrics/examples/pd-report.yaml \
  --output ./artifacts/regenerated-report
```

Use the plugin's `source.json`, not `server_metrics_export.json`, with `--source`.
The schemas are different. Regenerate into a different directory or back up the
source first. Output overwrite invalidates only the plugin's four owned files.

Key correctness rules:

- Use exact concrete phase windows where available; otherwise use the reported
  aggregation window. Warmup and profiling are separate series. Cross-boundary
  intervals are excluded with a warning, never prorated or silently blended.
- Counter charts use AIPerf's interval **rate**, not lifetime totals. Gauge charts
  use interval averages/min/max. Percent ratios are scaled only by the catalog.
- Histogram mean is interval sum/count. P50/P95/P99 use interval bucket deltas,
  cumulative across `le`. Missing, inconsistent, empty or +Inf-censored
  quantiles remain missing. **Never average worker P95s.**
- Keep every endpoint/rank/label series independent. Shared TP metrics are not
  summed, cache hit rates are not unweighted-averaged, and L1/L2/L3 are not
  invented. Logical prompt token throughput is not uncached prefill throughput.
- `sglang:kv_transfer_latency_ms` already uses milliseconds. Transfer speed in
  GB/s is not total NIC bandwidth; token-pool usage is not total GPU HBM usage.
- Missing timestamps, endpoints or timeslices produce visible warnings, not
  zeros or fabricated flat lines. Last partial slices are marked on hover.
- UTC origin and integer-nanosecond bounds are retained, with browser-relative
  seconds computed before serialization. EMA/log controls affect display only.
- Bounds (`max_series`, `max_points`) fail explicitly rather than silently
  dropping data. Increase slice duration for long/high-cardinality experiments.

## Safety and validation

The exporter is opt-in and deferred: it runs after native results are written,
not in the measured request path. Native AIPerf records deferred failures without
turning a successful benchmark into a load-generator failure. Partial results
are explicitly marked. Files use atomic replacement and manifest-last
publication; a failed rerun removes stale successful plugin artifacts.

Endpoint credentials/query strings and common credential-bearing labels are
redacted. Full request bodies and benchmark configuration are not copied. This
is not a general PII detector: inspect your exporter labels before sharing any
report. HTML escapes embedded data, uses `textContent` for labels and a CSP
with `connect-src 'none'`. Reports must be treated as private operational data.

Run focused tests from the package directory:

```bash
uv pip install --python ../../.venv/bin/python --no-deps -e .
../../.venv/bin/python -m pytest -q

# Optional browser checks (after installing playwright and its Chromium):
AIPERF_SGLANG_BROWSER_TESTS=1 ../../.venv/bin/python -m pytest -q
```

`CHROMIUM_EXECUTABLE` can point at an existing Chromium binary. Tests include
phase filtering, units, histogram semantics, missing data, atomic artifacts,
XSS, mobile layout and exporter protocol behavior. The actual AIPerf-model and
registry test **skips when AIPerf is absent**; test doubles are not an end-to-end
SGLang deployment. This package does not claim a GPU-cluster validation.
