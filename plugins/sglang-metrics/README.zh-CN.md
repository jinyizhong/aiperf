<!-- SPDX-License-Identifier: Apache-2.0 -->
# SGLang 实验指标报告插件

[English](README.md) | 中文

给**单次 AIPerf 实验**增加 P、D、PD 传输、KV Cache 和多级缓存读写的离线指标趋势。
通过 `aiperf.plugins` 的 `data_exporter` 扩展点启用，不修改发压器，不额外采集，不依赖数据库或报告服务器。
页面借鉴 MiMo 的指标浏览方式，不使用其代码或素材；生成额外 HTML 文件，不向原 Dash 注入标签页，也不提供实时监控。

## 1. 安装

建议 Python 3.12。插件必须装到**真正执行 AIPerf 最终导出的同一环境**。

### 当前 feature 分支（基于 main）

```bash
git clone --branch feature/sglang-metrics https://github.com/jinyizhong/aiperf.git
cd aiperf
uv sync
uv pip install --python .venv/bin/python -e ./plugins/sglang-metrics
```

### 保持 v0.12.0，不升级宿主

不要切换到 tag 后再寻找插件目录：该 tag 本身不包含插件。保留 feature 工作区，将宿主单独检出。

```bash
# 在上面 feature 分支工作区中执行
git fetch origin tag v0.12.0
git worktree add --detach ../aiperf-host-v012 v0.12.0
uv venv ../aiperf-v012-env --python 3.12
uv pip install --python ../aiperf-v012-env/bin/python \
  -e ../aiperf-host-v012 -e ./plugins/sglang-metrics

../aiperf-v012-env/bin/aiperf --version
```

插件不声明强制升级 AIPerf 的依赖。兼容测试固定两个宿主源码：

| 宿主 | 固定 commit |
| --- | --- |
| main（0.13.0） | `23a63a493604bc4016388543316934afa35e1c56` |
| v0.12.0 | `be53bf2953d30e46c500e6a80fc1f8b6f84bc718` |

后续 main 变化并不自动继承测试结论；升级后应重跑本文件第 6 节的主流程测试。

## 2. 配置并运行

SGLang 各 worker 开启 `--enable-metrics`，从采集端确认每个直接 `/metrics` URL 可访问。
HiCache/L3、Mamba、投机解码等指标是否存在，取决于服务版本和实际启用的功能；插件不会替你启用缓存后端。

```bash
cp plugins/sglang-metrics/examples/pd-report.yaml /tmp/pd-report.yaml
# 编辑 /tmp/pd-report.yaml，改成实际 P、D 的直接 metrics 地址
export AIPERF_SGLANG_METRICS_CONFIG=/tmp/pd-report.yaml
```

最小插件配置：

```yaml
schema_version: 1
title: SGLang P/D 实验观测
endpoints:
  - {url: 'http://prefill-0:8000/metrics', role: prefill, name: p0}
  - {url: 'http://decode-0:8000/metrics', role: decode, name: d0}
max_series: 1000
max_points: 20000
```

这里的 endpoint **只用于角色映射，不负责采集**。原 AIPerf 压测命令仍须加相同的采集地址和时间片：

```bash
.venv/bin/aiperf profile \
  --url http://gateway:8000 \
  --model kimi-k3 --endpoint-type chat --streaming \
  --tokenizer /models/Kimi-K3 --tokenizer-trust-remote-code \
  --request-count 32 --concurrency 2 \
  --server-metrics http://prefill-0:8000/metrics http://decode-0:8000/metrics \
  --slice-duration 1 \
  --artifact-dir artifacts/pd-smoke
```

保留你已有的数据集、输入输出、预热和 SLO 参数。使用 v0.12.0 环境时，替换命令中的可执行文件路径。
`--slice-duration 1` 生成一秒时间片；没有时间片不会伪造趋势。不要通过会轮询不同 worker 的负载均衡 URL 抓指标。

Kubernetes 中插件与 YAML 必须进入执行导出的镜像/容器；本机安装不等于远端 Pod 安装。
删除环境变量即可禁用：`unset AIPERF_SGLANG_METRICS_CONFIG`。

## 3. 新增的覆盖范围

### 队列与请求阶段

覆盖调度等待、Grammar 编译等待、P bootstrap、P inflight、D preallocation、D transfer。
同时保留 running、暂停、回退事件，并通过 `per_stage_req_latency_seconds{stage=...}` 展示实际暴露的请求阶段耗时。
`num_retracted_reqs` 是回退事件统计，**不是 D retracted 等待队列长度**。
页面可勾选“只看队列”，或搜索 `stage` / `category` / rank。

### 四个缓存搬运方向

L1 = GPU/device；L2 = 本机主机 DRAM；L3 = storage backend。
L3 可能是 Mooncake 远端内存，不能一律叫 SSD。

下表省略公共前缀 `sglang:`，`*_total` 按区间差分速率显示。

| 方向 | 已核实的原生指标 | 解释 |
| --- | --- | --- |
| L1→L2 | `hicache_backup_tokens_total`、`hicache_backup_bytes_total`、`hicache_backup_duration_seconds` | GPU 到 Host 备份的逻辑 token、物理字节、合并拷贝耗时 |
| L2→L1 | `load_back_tokens_total`、`load_back_bytes_total`、`load_back_duration_seconds` | Host 回载 GPU；duration 不覆盖完整排队/fence 等待 |
| L2→L3 | `backuped_tokens_total`、`backup_pgs`、`backup_bandwidth` | Host 到 Storage 写入；保留上游 `backuped` 拼写 |
| L3→L2 | `prefetched_tokens_total`、`prefetch_pgs`、`prefetch_bandwidth` | Storage 预取到 Host；已预取不等于最终实际复用 |

物理 bytes 是各 rank 分片流量；逻辑 token 可在 TP rank 重复。插件默认均保留单独序列，不盲目求和。
L3 bandwidth 是**每操作 wire 带宽分布**，不是整段实验的总 MB/s；不能用平均页数除以平均带宽反推精确延迟。

### 容易形成瓶颈的其他指标

- L1/full-attention、SWA、Mamba/SSM 各池压力，空闲/可驱逐/活跃槽位，L2 容量、会话持有 token。
- 按 `cache_source=device/host/storage` 分层的复用 token；旧版 `total` 回退口径不与分层数据相加。
- L3 查询命中但未兑现、预取延后、辅助池分配失败、备份前丢弃、无备份驱逐，保留 `reason/pool`。
- PD bootstrap/分配等待、传输大小、耗时、失败与重试；毫秒指标不重复乘 1000。
- Scheduler 各阶段墙钟秒/秒、CPU 秒/秒、Forward 时间、CUDA Graph 模式、投机接受情况。

原生目录核对来源：
[SGLang collector 固定版本](https://github.com/sgl-project/sglang/blob/76f9213a411018547f4fd6a75f36feaa4d6bed58/python/sglang/srt/observability/metrics_collector.py)。
旧版服务不保证具有全部名称。所有其他 `sglang:` 指标仍保留在“其他指标”。

### 不能凭插件补齐的部分

当前核对的 collector 未完整提供：D retracted 独立队列、L1↔L2 拷贝队列及 fence 等待、L2↔L3 读写队列，
以及 PD gather/RDMA/scatter/staging-wait 的全链路拆分。页面“指标覆盖与埋点缺口”明确列出，**不画零值占位线**。
L1 内 attention kernel 的 KV 物理读写也不是层级搬运字节，需 profiler 或引擎新埋点。

已有自定义埋点可按真实名字接入，例如下面是**示意自定义名称，不是 SGLang 原生指标**：

```yaml
metrics:
  my_hicache_read_queue_depth:
    title: 自定义 Storage 读等待队列
    group: kvcache
    unit: requests
    scale: 1
```

配置仅定义展示语义，不执行 PromQL。四方向也会读取合法的 `direction` 标签；没有方向不猜测。

## 4. 看结果

实验结束打开 `artifacts/pd-smoke/sglang-metrics/report.html`。
支持 P/D 角色、实验阶段、四个缓存方向、队列筛选、指标搜索、时间范围联动和明暗主题。
覆盖表按**端点和阶段**区分：已观测、未采集、无时间片、有时间片但无观测值。
因此 P 的成功采集不会掩盖 D 缺失，warmup 的观测也不会冒充 profiling。

| 文件 | 内容 |
| --- | --- |
| `report.html` | 完全离线趋势页面，不需要 CDN、Prometheus 或 Web 服务 |
| `metrics.json` | 标准化序列、`coverage` 覆盖表、原生埋点缺口、阶段边界和宿主版本 |
| `source.json` | 脱敏的完成态时间片汇总，供重生成，不是原始 scrape |
| `manifest.json` | 最后写入的完成标记、文件清单、实验标识与时间边界 |

原生 AIPerf 文件不改动。离线重生成使用插件自己的 `source.json`，不能替换成不同 schema 的 `server_metrics_export.json`：

```bash
.venv/bin/aiperf-sglang-report \
  --source artifacts/pd-smoke/sglang-metrics/source.json \
  --config /tmp/pd-report.yaml \
  --output artifacts/pd-regenerated
```

## 5. 统计和失败边界

正式测量与预热分开；跨阶段时间片被排除并提示。Counter 用区间速率，Gauge 用时间片统计。
Histogram P50/P95/P99 从该时间片的桶增量估算：空样本、不一致桶、无穷桶中的分位数显示缺失。
不平均不同节点的 P95，不把阶段 P95 相加。平滑只改显示，不改数据。

原始零值和数据缺失严格区分。`available` 只说明采到可画的数据，不证明功能健康或所有 TP rank 均已采到。
`complete` 是报告生成状态，不代表所有可选缓存功能都有埋点；请同时查看 `coverage`。

报告导出是延后步骤，不进入测量请求路径。失败保留原生结果并记录日志；重跑失败不遗留旧成功报告。
所有文件原子替换，manifest 最后发布。URL 凭证和常见敏感标签被去除；这不是通用隐私检测，分享前仍须检查标签。

## 6. 测试与兼容验收

[双版本 CI](https://github.com/jinyizhong/aiperf/actions/workflows/sglang-metrics-compat.yml)
分别安装固定 main/tag 宿主和同一个 feature 插件，实际启动 `aiperf profile`。
本地可重复：

```bash
uv pip install --python .venv/bin/python -e './plugins/sglang-metrics[test]'
(cd plugins/sglang-metrics && ../../.venv/bin/python -m pytest -q)
.venv/bin/python plugins/sglang-metrics/tools/compat_smoke.py --output artifacts/compat-main

# v0.12.0 的同一脚本、不同宿主环境
../aiperf-v012-env/bin/python plugins/sglang-metrics/tools/compat_smoke.py \
  --output artifacts/compat-v012
```

主流程夹具是真实 HTTP SSE 和两个 Prometheus 端点，tokenizer 在本地构建并放入隔离 HF cache，**无需下载模型**。
验证插件关闭、开启、错误配置三种路径：实际请求→采集→时间片聚合→原生导出→插件报告。
检查 P/D、四方向、阶段边界、原生产物和失败隔离，保存日志、运行版本与 JSON 结果。
这是 AIPerf 插件兼容测试，**不是 SGLang/GPU/真实多级缓存性能验收**。

浏览器检查需安装 Playwright Chromium；`AIPERF_SGLANG_BROWSER_TESTS=1` 启用，
`CHROMIUM_EXECUTABLE` 可指定已有 Chromium。升级宿主或服务版本后，应重新执行 CI 并对照真实 `/metrics`。
