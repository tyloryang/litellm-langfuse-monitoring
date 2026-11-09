# Grafana 看板计算公式与统计口径

**本页覆盖全部 51 个指标面板、78 条实际 PromQL。每个面板的公式也写入 Grafana 的说明，点击标题旁的信息图标即可查看。** 看板顶部另提供本页入口。公式、查询和本页由 `ops/build_dashboard.py` 一起生成，避免文档与看板各自维护。

## 先看这些容易误读的公式

| 指标 | 计算公式 | 必须同时理解的条件 |
| --- | --- | --- |
| 网关 TTFT | `t_first_valid_delta − t0` | t0 是网关 pre-call hook；首有效输出可为正文、推理或工具增量，不是客户端收包时刻 |
| 网关 TPOT | `(t_last_valid_delta − t_first_valid_delta) / (completion_tokens − 1)` | 只收成功有效流式；分母含推理 Token，不能用 SSE 帧数 |
| 最近引擎 TPOT | `decode_ms / 1000 / (output_tokens − 1)` | 单个最近请求均值，非逐 Token 测量 |
| 最近解码速度 | `engine_generated / (decode_ms / 1000)` | N/T 与 T/(N−1) 不能直接取倒数；两个 N 的计数来源也可能不同 |
| Strata ITL histogram | `decode_seconds / (N−1)`，重复计入 `N−1` 次 | 以间隔数加权的请求均值估计，无法展示单请求内每个 Token 的抖动 |
| P50 / P95 / P99 | `histogram_quantile(q, sum by (le) (rate(bucket[W])))` | 合并所选样本的 bucket，再估计分位；不是平均各实例 P95，也不可把两个 P95 相减 |
| 网关 Token/s 与 QPS | `Σ rate(counter[5m])` | recording rule 固定 5 分钟，非瞬时速率 |
| 显存占用率 | `100 × used_bytes / total_bytes` | 每 GPU 计算；驻留模型包含在内，不能当作 KV 使用率 |
| 前缀命中率 / MTP 接受率 | `Σ rate(命中或接受 Token[W]) / max(Σ rate(查询或草稿 Token[W]), 10⁻⁶)` | Token 加权，非请求加权；无流量时的零没有质量判定意义 |
| PCIe / 磁盘 | `MiB/s × 1048576 → B/s` | 原始值已经是速率，不能再次求 rate |

## 符号、窗口和聚合

- `t0`：自定义回调创建 RequestState 的单调时钟；不包含 hook 之前的鉴权 / 网络等待，也不包含客户端网络和页面渲染。
- `W = $__rate_interval`：Grafana 根据查询分辨率和数据源 scrape 设置动态选择的速率窗口，通常为 `max($__interval + scrape_interval, 4 × scrape_interval)`；以实际展开值为准。在线验收脚本用 1m 替换 W。
- `R = $__range`：看板选中的完整时间跨度。`increase(counter[R])` 在每个求值点计算此前 R 的滚动增量；最右端值对应所选区间，不要再对整条曲线求和。
- `rate` / `increase` 会处理累计值下降的重置并对窗口边界外推，所以请求数 / Token 增量可能不是整数。`rate` 至少需要两个样本；没有请求或样本不足时可以无值 / NaN，不能填成 0 秒延迟。
- `Q(q)` 在本页统一表示 `histogram_quantile(q, sum by (le) (rate(<metric>_bucket[W])))`。本看板的延迟分位按所选模型及其全部实例合并 bucket；不保留逐模型 / 实例曲线。桶内使用分布估计，精度受 bucket 和样本数限制。
- **网关输入 / 输出 Token/s、QPS 和引擎 Token/s 的 recording rule 使用固定 `[5m]`，每 15 秒求值。** 它们不会因为 Grafana 切换时间范围而变成 1m 或瞬时速率。
- 网关筛选使用 `requested_model`（自定义时延使用 `model`）；原生引擎使用 `model_name`，归一化后为 `model`；GPU 按 `deployment, gpu` 筛选。四类筛选器分别作用于对应查询，不是全局所有面板过滤器。
- GPU recording rule 先按 `(deployment, gpu)` 取 max 去重，再以 PromQL `or` 优先选 Strata、缺失时选 DCGM；不是把两个采集源相加。引擎归一化只复制模型标签并选择对应引擎指标，不改变 histogram 的计数语义。
- `percent` 接收 0–100，`percentunit` 接收 0–1 并显示为百分数；容量基础单位为字节，`Bps` 表示字节 / 秒。MiB/s 到 B/s 的换算固定乘 2²⁰，与 Grafana 自动选择的显示前缀分开理解；`W × s` 才是能量，功耗 W 不是电量 kWh。
- “最近请求”是 gauge，空闲仍保留最后请求值；不是“此刻正在生成”的值。timeseries 图例 `Last` / `Max` 是曲线值的最近 / 最大值；P95 曲线的 Max 只是窗口 P95 的最大值，不是请求最大延迟。
- Strata 最近原始引擎 TTFT 使用 `first_engine_token_s`；引擎 TTFT histogram 使用服务的 `st.first_token − started`。两种内部计时标记不同，应分别解释，不保证数值相同。

## 全部面板的公式与实际查询

以下按看板原顺序列出；PromQL 原样保留 Grafana 变量，可在 Grafana 查询检查器查看展开值。

## 01 · 实时总览 / 与 Strata Monitor 对齐

### 网关可用性

面板 ID：`2`；类型：`stat`。

**计算公式**

`UP_gateway = min(up{job="litellm"})`。

**统计口径与边界**

任一已抓取的网关目标为 0，则结果为 0；没有目标时为空。这里只验证 Prometheus 抓取成功，不等价于一次推理成功。

1=Prometheus 能抓取网关。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
min(up{job="litellm"})
```

### 解码速度 · 最近请求

面板 ID：`3`；类型：`stat`。

**计算公式**

`decode_tok_s = engine_generated / (decode_ms / 1000)`，Strata 将结果保留 1 位小数。

**统计口径与边界**

直接读取 `requests[0].decode_tok_s`；engine_generated ≥ 2 且有 decode_ms 时才有值。包含推理 Token，空闲时保留最近完成请求；其 N/T 与 TPOT 的 T/(N−1) 不能直接取倒数，engine_generated 与 output_tokens 的计数边界也可能不同。

最近完成请求的引擎解码速度；空闲时保留最近值。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
strata:last_decode_tok_s{model_name=~"$engine_model",job="strata"}
```

### GPU 利用率

面板 ID：`4`；类型：`stat`。

**计算公式**

`Util_display = Util_driver`，保留驱动提供的 0–100 百分数；归一化按 `(deployment, gpu)` 取最大值去重。

**统计口径与边界**

直接读取 Strata / DCGM 的 GPU 忙碌采样，不用显存占比、Token 速度或功耗推导。多卡分别展示，不把百分数相加。

来自 Strata 实际硬件采样；DCGM 部署可复用相同面板。

**单位**：百分数 0–100，表达式已经乘过 100 或原始值本来是百分数。

**实际 PromQL**

查询 A：
```promql
llm:gpu_utilization_percent{deployment=~"$deployment",gpu=~"$gpu"}
```

### 显存占用率

面板 ID：`5`；类型：`stat`。

**计算公式**

`VRAM% = 100 × memory_used_bytes / memory_total_bytes`。

**统计口径与边界**

分子分母按相同 deployment / gpu 匹配，每张卡单算；包含驻留模型和其他显存分配，不等价于 KV 使用率。分母缺失或为 0 时没有有效百分比。

驻留模型也占显存，空闲时显存通常不会清零。

**单位**：百分数 0–100，表达式已经乘过 100 或原始值本来是百分数。

**实际 PromQL**

查询 A：
```promql
100 * llm:gpu_memory_used_bytes{deployment=~"$deployment",gpu=~"$gpu"} / llm:gpu_memory_total_bytes{deployment=~"$deployment",gpu=~"$gpu"}
```

### 最近 TTFT · 引擎

面板 ID：`6`；类型：`stat`。

**计算公式**

`TTFT_last_engine = t_first_raw_engine_token − t_enter_engine_queue`，即 `requests[0].first_engine_token_s`。

**统计口径与边界**

单位秒，包含队列与 Prefill；Strata 记录到毫秒精度。此字段与引擎 histogram 的 st.first_token 使用不同标记，也与网关首个可输出增量不同。

引擎队列到原始首 Token；区别于服务 histogram 和网关迭代器的首输出标记。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
strata_extra_last_request_ttft_seconds{model=~"$engine_model"}
```

### 最近 TPOT · 引擎均值

面板 ID：`7`；类型：`stat`。

**计算公式**

`TPOT_last_engine = (decode_ms / 1000) / (output_tokens − 1)`，要求 `output_tokens > 1` 且有 decode_ms。

**统计口径与边界**

单个最近请求的平均 Token 间隔，包含推理 Token；缺少有效分母时为 NaN。不是逐 Token 时间戳，也不是所有请求的平均值。

decode_ms / 1000 / (output_tokens - 1)，包含推理 Token；不是逐 Token ITL。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
strata_extra_last_request_engine_tpot_seconds{model=~"$engine_model"}
```

### 实时解码 / Prefill 速度

面板 ID：`8`；类型：`timeseries`。

**计算公式**

Decode：每个运行请求在最近 2 秒样本内取 `Δgenerated / Δtime`，再对并发请求求和；样本跨度不足 0.25 秒时用 `generated / max(0.25, now − first_token)`。

Prefill：直接读取引擎 `live.prefill_tok_s_mean`，其口径为新读取 Token / 处理秒数，排除缓存前缀。

**统计口径与边界**

两条都是 Strata 的实时 gauge，非累计 counter 的 rate；空闲为 0。实时 Prefill 不从最近完成请求或输出 Token 推导，也不保证是多请求吞吐之和。

直接对应 Strata Monitor Speed / Prefill；空闲为 0。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
strata:live_tok_s{model_name=~"$engine_model",job="strata"}
```

查询 B：
```promql
strata:live_prefill_tok_s_mean{model_name=~"$engine_model",job="strata"}
```

### GPU / 显存占用趋势

面板 ID：`9`；类型：`timeseries`。

**计算公式**

GPU 曲线：`Util_driver`；显存曲线：`100 × memory_used_bytes / memory_total_bytes`。

**统计口径与边界**

两条曲线都显示 0–100%，分别表示计算忙碌与显存容量占用，不能将二者视为同一指标。

**单位**：百分数 0–100，表达式已经乘过 100 或原始值本来是百分数。

**实际 PromQL**

查询 A：
```promql
llm:gpu_utilization_percent{deployment=~"$deployment",gpu=~"$gpu"}
```

查询 B：
```promql
100 * llm:gpu_memory_used_bytes{deployment=~"$deployment",gpu=~"$gpu"} / llm:gpu_memory_total_bytes{deployment=~"$deployment",gpu=~"$gpu"}
```

### 请求运行 / 排队

面板 ID：`10`；类型：`timeseries`。

**计算公式**

Strata：`running = live.running`（缺失时按 reading / generating 状态回退到 0 或 1）；`waiting = live.queued + live.waiting`。

**统计口径与边界**

直接展示当前 gauge，不求 rate。其他引擎分别映射其 running / queue gauge；排队可能来自等待槽位或引擎控制通道。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
llm:engine_requests_running{model=~"$engine_model"}
```

查询 B：
```promql
llm:engine_requests_waiting{model=~"$engine_model"}
```

## 02 · 首输出、每 Token 时间与请求延迟

### TTFT · 网关 P50 / P95 / P99

面板 ID：`12`；类型：`timeseries`。

**计算公式**

每请求 `TTFT = t_first_valid_delta − t0`；面板对该 histogram 计算 `Q(0.50)`、`Q(0.95)`、`Q(0.99)`。

**统计口径与边界**

t0 为网关 pre-call hook 的单调时钟；有效增量包括正文、推理或工具名 / 参数，排除 role、空帧及 usage。仅流式有样本；已产生首输出后失败的请求仍可有 TTFT。时间戳在网关迭代器取样，不是客户端实际收包时间。

从网关接收到第一个有效内容、推理或工具增量。仅统计流式。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm_gateway_ttft_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm_gateway_ttft_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 C：
```promql
histogram_quantile(0.99, sum by (le) (rate(llm_gateway_ttft_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

### TPOT · 网关请求均值 P50 / P95 / P99

面板 ID：`13`；类型：`timeseries`。

**计算公式**

每请求 `TPOT = (t_last_valid_delta − t_first_valid_delta) / (completion_tokens − 1)`；再对每请求均值的 histogram 计算 `Q(0.50/0.95/0.99)`。

**统计口径与边界**

只收成功流式、最终 usage 有整数输出数 N > 1、至少两帧有效输出且 last > first 的样本。分母包含推理 Token，不能用 SSE 帧数代替。每请求权重为 1；不是逐 Token ITL 分布，也不能用 P95(E2E)−P95(TTFT) 算。

每请求 (last-first)/(completion_tokens-1)。仅成功、usage 完整且至少两帧有效输出的流式请求；网络合并帧会影响估计。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm_gateway_tpot_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm_gateway_tpot_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 C：
```promql
histogram_quantile(0.99, sum by (le) (rate(llm_gateway_tpot_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

### 首段可见答案 · P50 / P95

面板 ID：`14`；类型：`timeseries`。

**计算公式**

每请求 `TTFA = t_first_visible_content − t0`；对 histogram 计算 `Q(0.50)`、`Q(0.95)`。

**统计口径与边界**

只以非空正文 content / output_text.delta 作为首次可见答案；纯推理与工具参数不算。长思考时 TTFA 可大于 TTFT，无正文则无该样本。

排除纯推理及工具增量，长思考模型的该值通常高于 TTFT。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm_gateway_first_answer_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm_gateway_first_answer_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

### TTFT · 引擎 P50 / P95

面板 ID：`15`；类型：`timeseries`。

**计算公式**

Strata 每请求写入 `TTFT_hist = st.first_token − request.started`；对归一化 histogram 计算 `Q(0.50)`、`Q(0.95)`。

**统计口径与边界**

保留引擎服务原始首输出标记，不在 Grafana 重建。该标记可能受到服务端解析 / 缓冲影响，与最近 first_engine_token_s 的原始 Token 标记不相同。vLLM / SGLang 使用其各自原生 histogram。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm:engine_ttft_seconds_bucket{model=~"$engine_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm:engine_ttft_seconds_bucket{model=~"$engine_model"}[$__rate_interval])))
```

### ITL · 引擎 P50 / P95

面板 ID：`16`；类型：`timeseries`。

**计算公式**

Strata 每请求计算 `gap_mean = decode_seconds / (output_tokens − 1)`，以 `output_tokens − 1` 次写入 histogram，再计算 `Q(0.50)`、`Q(0.95)`。

**统计口径与边界**

Strata 按 Token 间隔数加权，每请求会重复同一个平均间隔；这只是加权均值分布估计，无法反映该请求内部的逐 Token 抖动。vLLM / SGLang 的原生观测语义由对应引擎决定。

vLLM/SGLang 的引擎 ITL；Strata 当前由 decode 平均间隔按 Token 间隔数加权估算。不要当作请求级 TPOT。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm:engine_itl_seconds_bucket{model=~"$engine_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm:engine_itl_seconds_bucket{model=~"$engine_model"}[$__rate_interval])))
```

### 端到端延迟 · P50 / P95 / P99

面板 ID：`17`；类型：`timeseries`。

**计算公式**

每请求 `E2E = max(0, t_record_callback − t0)`；对 histogram 计算 `Q(0.50/0.95/0.99)`。

**统计口径与边界**

合并所选模型的 stream=true / false 与成功 / 失败样本；从网关 pre-call hook 计时，到终止回调记录。包含流式消费及回调调度时间，不包含 hook 之前的处理、客户端网络与页面渲染。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.5, sum by (le) (rate(llm_gateway_e2e_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 B：
```promql
histogram_quantile(0.95, sum by (le) (rate(llm_gateway_e2e_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

查询 C：
```promql
histogram_quantile(0.99, sum by (le) (rate(llm_gateway_e2e_seconds_bucket{model=~"$gateway_model"}[$__rate_interval])))
```

### LiteLLM 上游 API 延迟 P95

面板 ID：`18`；类型：`timeseries`。

**计算公式**

每请求 `API_latency = end_time − api_call_start_time`（秒）；对 LiteLLM 原生 histogram 计算 `Q(0.95)`。

**统计口径与边界**

以 LiteLLM 1.106.6 的 API 计时为准，不是自定义回调的 TTFT。时间戳不完整的样本由 LiteLLM 排除。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.95, sum by (le) (rate(litellm_llm_api_latency_metric_bucket{requested_model=~"$gateway_model"}[$__rate_interval])))
```

### LiteLLM 网关额外开销 P95

面板 ID：`19`；类型：`timeseries`。

**计算公式**

每请求 `overhead_seconds = hidden_params.litellm_overhead_time_ms / 1000`；对该 histogram 计算 `Q(0.95)`。

**统计口径与边界**

直接采用 LiteLLM 提供的 overhead 属性；未提供时无样本。没有用两个 P95 相减：分位数不可相减来还原某段耗时。

**单位**：秒；Grafana 可自动显示为毫秒等子单位。

**实际 PromQL**

查询 A：
```promql
histogram_quantile(0.95, sum by (le) (rate(litellm_overhead_latency_metric_bucket{requested_model=~"$gateway_model"}[$__rate_interval])))
```

### TPOT 被排除的请求

面板 ID：`20`；类型：`timeseries`。

**计算公式**

`excluded(reason) = Σ increase(llm_gateway_tpot_skipped_total{reason}[R])`，R 为看板选择的时间范围。

**统计口径与边界**

按 failed、non_stream、missing_usage、too_few_tokens、coalesced_or_no_output 分类。increase 对边界外推，可能显示小数；这些请求不会被填成 0 秒混入 TPOT。

非流式、少 Token、usage 缺失、帧合并及失败分别计数。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
sum by (reason) (increase(llm_gateway_tpot_skipped_total{model=~"$gateway_model"}[$__range]))
```

## 03 · Token、吞吐和请求质量

### 输入 Token / s · 网关

面板 ID：`22`；类型：`timeseries`。

**计算公式**

`input_tok_s(model) = Σ rate(litellm_input_tokens_metric_total[5m])`，按 requested_model 聚合。

**统计口径与边界**

这是 recording rule 固定的 5 分钟平均速率，每 15 秒计算一次；不随 Grafana 的 $__rate_interval 变化。输入总量包含上游 usage 中的缓存输入。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
llm:gateway_input_tokens_per_second{requested_model=~"$gateway_model"}
```

### 输出 Token / s · 网关

面板 ID：`23`；类型：`timeseries`。

**计算公式**

`output_tok_s(model) = Σ rate(litellm_output_tokens_metric_total[5m])`，按 requested_model 聚合。

**统计口径与边界**

固定 5 分钟平均速率；输出总量已包含 reasoning Token，不应再把推理速率加到输出速率上。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
llm:gateway_output_tokens_per_second{requested_model=~"$gateway_model"}
```

### 输入 / 输出 Token · 引擎

面板 ID：`24`；类型：`timeseries`。

**计算公式**

每条引擎序列分别为 `rate(prompt_tokens_total[5m])` 与 `rate(generation_tokens_total[5m])`，再将 model_name 映射为 model。

**统计口径与边界**

固定 5 分钟窗口，保留引擎 / 实例标签，不跨实例求和；Strata 在请求结束时累计 totals，因此速率可呈阶梯。直接访问引擎的请求也会计入。

引擎包含绕过 LiteLLM 的直接请求，因此数值可能与网关不同。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
llm:engine_input_tokens_per_second{model=~"$engine_model"}
```

查询 B：
```promql
llm:engine_output_tokens_per_second{model=~"$engine_model"}
```

### 请求 QPS

面板 ID：`25`；类型：`timeseries`。

**计算公式**

`QPS(model) = Σ rate(litellm_proxy_total_requests_metric_total[5m])`，按 requested_model 聚合。

**统计口径与边界**

固定 5 分钟平均请求数 / 秒，每 15 秒更新 recording rule。不是当前 in-flight 数，也不是瞬时到达率。

**单位**：请求 / 秒。

**实际 PromQL**

查询 A：
```promql
llm:gateway_requests_per_second{requested_model=~"$gateway_model"}
```

### 失败率

面板 ID：`26`；类型：`timeseries`。

**计算公式**

`failure% = 100 × (Σ rate(failed[W]) 或 0) / max(Σ rate(total[W]), 10⁻⁶)`，W = $__rate_interval。

**统计口径与边界**

在所选网关模型范围内先合计失败和总请求，再相除；不是各模型失败百分比的算术平均。无失败序列回退到 0，分母下限仅用于数值防护；无流量时的 0 不能说明服务已验证成功。

零失败可显示 0；没有请求时不生成虚假的延迟样本。

**单位**：百分数 0–100，表达式已经乘过 100 或原始值本来是百分数。

**实际 PromQL**

查询 A：
```promql
100 * (sum(rate(litellm_proxy_failed_requests_metric_total{requested_model=~"$gateway_model"}[$__rate_interval])) or vector(0)) / clamp_min(sum(rate(litellm_proxy_total_requests_metric_total{requested_model=~"$gateway_model"}[$__rate_interval])), 0.000001)
```

### 网关并发请求

面板 ID：`27`；类型：`timeseries`。

**计算公式**

`in_flight = Σ litellm_in_flight_requests`。

**统计口径与边界**

直接合计当前 gauge。当前指标没有 requested_model 标签，因此显示整个网关的并发，网关模型筛选不作用于该面板。

当前 LiteLLM gauge 无 requested_model 标签，为整个网关并发。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
sum(litellm_in_flight_requests)
```

### 期间输入 / 输出 Token

面板 ID：`28`；类型：`timeseries`。

**计算公式**

分别计算 `Σ increase(input_tokens_total[R])`、`Σ increase(output_tokens_total[R])`，R = $__range。

**统计口径与边界**

采用重置修正和边界外推，可能为非整数。曲线每个求值点都是此前 R 的滚动增量；最右侧点才对应当前选择区间，不要对曲线再次求和。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
sum(increase(litellm_input_tokens_metric_total{requested_model=~"$gateway_model"}[$__range]))
```

查询 B：
```promql
sum(increase(litellm_output_tokens_metric_total{requested_model=~"$gateway_model"}[$__range]))
```

### 缓存读取 / 推理 Token 速率

面板 ID：`29`；类型：`timeseries`。

**计算公式**

分别为 `Σ rate(input_cached_tokens_total[W])`、`Σ rate(output_reasoning_tokens_total[W])`，W = $__rate_interval。

**统计口径与边界**

缓存读取是输入子集，推理是输出子集；字段取决于上游 usage。两条细分曲线不可再加到完整输入 / 输出总量上。

是否提供细分 usage 取决于上游；无字段时不伪造数据。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
sum(rate(litellm_input_cached_tokens_metric_total{requested_model=~"$gateway_model"}[$__rate_interval]))
```

查询 B：
```promql
sum(rate(litellm_output_reasoning_tokens_metric_total{requested_model=~"$gateway_model"}[$__rate_interval]))
```

### 工具调用提议 / s

面板 ID：`30`；类型：`timeseries`。

**计算公式**

每次模型响应按 `Σ len(choice.message.tool_calls)` 增加 counter；面板按 model 计算 `Σ rate(tool_proposals_total[W])`。

**统计口径与边界**

单位次 / 秒，一次模型响应可提出多个工具调用。这里只统计模型提议，实际执行数量、耗时和结果由客户端 Langfuse TOOL Span 记录。

模型返回的工具调用提议，不代表工具已经执行。执行结果到 Langfuse 工具 span 查看。

**单位**：次 / 秒。

**实际 PromQL**

查询 A：
```promql
sum by (model) (rate(llm_gateway_tool_proposals_total{model=~"$gateway_model"}[$__rate_interval]))
```

## 04 · Strata 缓存、上下文和 MTP

### 前缀缓存命中率 · Token 加权

面板 ID：`32`；类型：`timeseries`。

**计算公式**

`hit_ratio = Σ rate(prefix_cache_hits_total[W]) / max(Σ rate(prefix_cache_queries_total[W]), 10⁻⁶)`。

**统计口径与边界**

Strata 分子来自 totals.reused，分母来自 totals.prompt_tokens。先加 Token 再相除，按 Token 加权；不是命中请求数 / 请求数，也不是各请求命中率平均。ratio=0.8 在 Grafana 显示 80%。

**单位**：原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100。

**实际 PromQL**

查询 A：
```promql
sum(rate(vllm:prefix_cache_hits_total{model_name=~"$engine_model",job="strata"}[$__rate_interval])) / clamp_min(sum(rate(vllm:prefix_cache_queries_total{model_name=~"$engine_model",job="strata"}[$__rate_interval])), 0.000001)
```

### 专家 VRAM 缓存命中率 · 最近请求

面板 ID：`33`；类型：`timeseries`。

**计算公式**

`expert_hit_ratio = hits / lookups`，Strata 对最近请求结果保留 3 位小数；无 lookups 时未知。

**统计口径与边界**

读取 requests[0].hit_rate；只衡量专家 VRAM 缓存查找，不是 prompt 前缀缓存。PCIe offloaded 路由不在该 lookups 分母内，不能据此单独推断全部专家路由开销。

与 Strata VRAM hit rate 相同，不是前缀缓存或物理 KV 占用。

**单位**：原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100。

**实际 PromQL**

查询 A：
```promql
strata:last_hit_rate{model_name=~"$engine_model",job="strata"}
```

### MTP 草稿接受率

面板 ID：`34`；类型：`timeseries`。

**计算公式**

`accept_ratio = Σ rate(accepted_tokens_total[W]) / max(Σ rate(draft_tokens_total[W]), 10⁻⁶)`。

**统计口径与边界**

分母是提出的草稿 Token 数，不是请求数；先合计计数再相除。无草稿流量时的 0 / 无数据不代表性能退化，也不能单凭接受率推算加速倍数。

**单位**：原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100。

**实际 PromQL**

查询 A：
```promql
sum(rate(vllm:spec_decode_num_accepted_tokens_total{model_name=~"$engine_model",job="strata"}[$__rate_interval])) / clamp_min(sum(rate(vllm:spec_decode_num_draft_tokens_total{model_name=~"$engine_model",job="strata"}[$__rate_interval])), 0.000001)
```

### Strata 上下文占用

面板 ID：`35`；类型：`timeseries`。

**计算公式**

运行时 `context_ratio = round(min(1, (live.prompt_tokens + live.generated) / max_context), 4)`；非 reading / generating 时分子为 0，max_context 缺失时输出 0。

**统计口径与边界**

直接对应 Strata 的兼容指标 vllm:kv_cache_usage_perc。是 live 上下文视图的逻辑位置占比，不是物理 KV 字节或所有并发槽位占比之和。

运行中 prompt+generated / max_context；空闲为 0。不是物理 KV 显存利用率。

**单位**：原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100。

**实际 PromQL**

查询 A：
```promql
llm:strata_context_usage_ratio{model=~"$engine_model"}
```

### 最大上下文 / GPU 驻留 KV 位置

面板 ID：`36`；类型：`timeseries`。

**计算公式**

两条原始 gauge：`max_context = engine.max_context`；`resident_positions = engine.kv_resident`。

**统计口径与边界**

单位均为 Token 位置，但含义不同：配置上限与每层驻留位置。未据此计算字节占用或把层数乘进 Token 数。

两者单位都为 Token 位置，驻留 KV 为每层驻留位置。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
strata:engine_max_context{model_name=~"$engine_model",job="strata"}
```

查询 B：
```promql
strata_extra_kv_resident_tokens{model=~"$engine_model"}
```

### 并行槽位状态

面板 ID：`37`；类型：`timeseries`。

**计算公式**

`slots(state) = count(slot in live.slots where slot.state == state)`，分别统计 idle / reading / generating。

**统计口径与边界**

直接展示当前状态计数，不求速率；只有引擎实际提供 slots 列表时才有对应样本。

idle / reading / generating。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
strata:live_slots{model_name=~"$engine_model",job="strata"}
```

### 会话缓存容量 / 预算

面板 ID：`38`；类型：`timeseries`。

**计算公式**

`used_bytes = conversation_cache.bytes`；`budget_bytes = conversation_cache.budget_mib × 2²⁰`。

**统计口径与边界**

两条是字节量，不是百分比；Grafana 自动选择 KiB / MiB / GiB 展示。预算为 0 或未配置时不能推导容量利用率。

**单位**：字节，Grafana 自动选择 KiB / MiB / GiB。

**实际 PromQL**

查询 A：
```promql
strata_extra_conversation_cache_bytes{model=~"$engine_model"}
```

查询 B：
```promql
strata_extra_conversation_cache_budget_bytes{model=~"$engine_model"}
```

### 会话缓存恢复 / 驱逐速率

面板 ID：`39`；类型：`timeseries`。

**计算公式**

分别为 `rate(conversation_cache_restores_total[W])`、`rate(conversation_cache_evictions_total[W])`。

**统计口径与边界**

原始 JSON 是进程生命周期累计值，导出为 gauge 家族但语义按单调 counter 使用 rate，下降会被 Prometheus 解释为重置。单位次 / 秒；不是缓存命中率。

**单位**：次 / 秒。

**实际 PromQL**

查询 A：
```promql
rate(strata_extra_conversation_cache_restores_total{model=~"$engine_model"}[$__rate_interval])
```

查询 B：
```promql
rate(strata_extra_conversation_cache_evictions_total{model=~"$engine_model"}[$__rate_interval])
```

### 专家缓存大小

面板 ID：`40`；类型：`timeseries`。

**计算公式**

`expert_cache_bytes = engine.expert_cache_mib × 2²⁰`。

**统计口径与边界**

读取引擎报告的专家缓存容量；不是整个 GPU 显存使用量，也没有用总显存减模型大小来估算。

**单位**：字节，Grafana 自动选择 KiB / MiB / GiB。

**实际 PromQL**

查询 A：
```promql
strata_extra_expert_cache_bytes{model=~"$engine_model"}
```

## 05 · GPU 与主机硬件 / Strata Monitor

### GPU 显存 已用 / 总量

面板 ID：`42`；类型：`timeseries`。

**计算公式**

Strata 直接取 memory_used_bytes / memory_total_bytes；DCGM 已用为 `FB_USED × 2²⁰`，总量为 `(FB_USED + FB_FREE) × 2²⁰`。

**统计口径与边界**

同 deployment / gpu 去重后分别展示已用和总量，不对多卡相加。DCGM 字段以 MiB 转为字节，Strata 原生已是字节。

**单位**：字节，Grafana 自动选择 KiB / MiB / GiB。

**实际 PromQL**

查询 A：
```promql
llm:gpu_memory_used_bytes{deployment=~"$deployment",gpu=~"$gpu"}
```

查询 B：
```promql
llm:gpu_memory_total_bytes{deployment=~"$deployment",gpu=~"$gpu"}
```

### GPU 温度

面板 ID：`43`；类型：`timeseries`。

**计算公式**

`temperature = strata:gpu_temp_celsius`，或无同标签 Strata 序列时用 `DCGM_FI_DEV_GPU_TEMP`。

**统计口径与边界**

直接硬件采样，单位 °C；归一化按 deployment / gpu 取最大值去重。不是请求期间平均温度。

**单位**：摄氏度（°C）。

**实际 PromQL**

查询 A：
```promql
llm:gpu_temperature_celsius{deployment=~"$deployment",gpu=~"$gpu"}
```

### GPU 功耗 / 功率上限

面板 ID：`44`；类型：`timeseries`。

**计算公式**

功耗直接取 Strata / DCGM 的 W 采样；功率上限取 `hardware.gpu_power_limit`（W）。

**统计口径与边界**

没有计算能耗 kWh；积分功耗才是能量。当前功率上限来自 Strata JSON 的模型级视图，不是通用的逐 GPU DCGM limit。

**单位**：瓦（W）。

**实际 PromQL**

查询 A：
```promql
llm:gpu_power_watts{deployment=~"$deployment",gpu=~"$gpu"}
```

查询 B：
```promql
strata_extra_gpu_power_limit_watts{model=~"$engine_model"}
```

### PCIe 接收 / 发送

面板 ID：`45`；类型：`timeseries`。

**计算公式**

`RX_Bps = hardware.gpu_pcie_rx_mb × 2²⁰`；`TX_Bps = hardware.gpu_pcie_tx_mb × 2²⁰`。

**统计口径与边界**

Strata 这些 _mb 字段实际单位为 MiB/s，因此乘 1048576。它们已经是速率，不再调用 rate；方向按 GPU 硬件视角。

**单位**：字节 / 秒，不是 bit / 秒。

**实际 PromQL**

查询 A：
```promql
strata_extra_gpu_pcie_rx_bytes_per_second{model=~"$engine_model"}
```

查询 B：
```promql
strata_extra_gpu_pcie_tx_bytes_per_second{model=~"$engine_model"}
```

### CPU 使用率

面板 ID：`46`；类型：`timeseries`。

**计算公式**

`CPU% = psutil.cpu_percent(interval=None)` 的主机采样值，原样显示 0–100%。

**统计口径与边界**

是主机总 CPU 百分比，不是 LiteLLM / Strata 进程 CPU，也不按核心数相加；Grafana 不从 Token 数推导 CPU 使用率。

**单位**：百分数 0–100，表达式已经乘过 100 或原始值本来是百分数。

**实际 PromQL**

查询 A：
```promql
strata:cpu{model_name=~"$engine_model",job="strata"}
```

### 主机 RAM 已用 / 总量

面板 ID：`47`；类型：`timeseries`。

**计算公式**

`used_bytes = virtual_memory.total − virtual_memory.available`；`total_bytes = virtual_memory.total`。

**统计口径与边界**

Strata 用主机可用内存口径，包含缓存可回收性的影响；不是简单 total−free，也不是进程 RSS。

**单位**：字节，Grafana 自动选择 KiB / MiB / GiB。

**实际 PromQL**

查询 A：
```promql
strata:ram_used_bytes{model_name=~"$engine_model",job="strata"}
```

查询 B：
```promql
strata:ram_total_bytes{model_name=~"$engine_model",job="strata"}
```

### 磁盘读 / 写速率

面板 ID：`48`；类型：`timeseries`。

**计算公式**

`read_Bps = hardware.disk_read_mb × 2²⁰`；`write_Bps = hardware.disk_write_mb × 2²⁰`。

**统计口径与边界**

JSON 原始值是 MiB/s，展示前换算为 B/s；它们已经是采样速率，不对其再求 rate，不等价于模型文件独占 IO。

**单位**：字节 / 秒，不是 bit / 秒。

**实际 PromQL**

查询 A：
```promql
strata_extra_disk_read_bytes_per_second{model=~"$engine_model"}
```

查询 B：
```promql
strata_extra_disk_write_bytes_per_second{model=~"$engine_model"}
```

### 最近请求 Prefill 吞吐

面板 ID：`49`；类型：`timeseries`。

**计算公式**

`prefill_tok_s = max(0, prompt_tokens − reused_tokens) / (prompt_ms / 1000)`，要求 prompt_ms > 0。

**统计口径与边界**

只计算实际新读取的 prompt Token，排除缓存复用；无有效处理时间时为 NaN。不能用完整 prompt_tokens 除以 prompt_ms 来夸大 Prefill 吞吐。

(prompt_tokens-reused)/prompt_seconds，与 Strata 请求表计算一致。

**单位**：Token / 秒。

**实际 PromQL**

查询 A：
```promql
strata_extra_last_request_prefill_tokens_per_second{model=~"$engine_model"}
```

### 最近请求输入 / 输出 / 复用 Token

面板 ID：`50`；类型：`timeseries`。

**计算公式**

三条原始 gauge：`requests[0].prompt_tokens`、`requests[0].output_tokens`、`requests[0].reused`。

**统计口径与边界**

最近完成请求的计数，空闲保留；复用 Token 是输入子集，不能把输入+输出+复用当总 Token。中断请求的 prompt_tokens 可能是实际已处理部分。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
strata_extra_last_request_prompt_tokens{model=~"$engine_model"}
```

查询 B：
```promql
strata_extra_last_request_output_tokens{model=~"$engine_model"}
```

查询 C：
```promql
strata_extra_last_request_reused_tokens{model=~"$engine_model"}
```

## 06 · 采集健康、计费配置及其他引擎

### 采集目标 UP

面板 ID：`52`；类型：`timeseries`。

**计算公式**

每个已发现目标 `up = 1` 表示本次 scrape 成功，`up = 0` 表示失败。

**统计口径与边界**

不做合计，也不为不存在的可选引擎生成 1。HTTP / 文本格式抓取成功不保证 JSON 内部数据新鲜，须结合 JSON poll UP 和数据年龄。

0=采集异常；不存在的部署不会伪装成正常。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
up{job=~"litellm|strata|strata-extra|vllm|sglang|gpu-dcgm"}
```

### Strata JSON 采集健康 / 数据年龄

面板 ID：`53`；类型：`timeseries`。

**计算公式**

健康：最近一次 JSON 轮询成功为 1、失败为 0；`age_seconds = time() − last_success_timestamp_seconds`。

**统计口径与边界**

两条序列分别是 0/1 与秒。轮询间隔 3 秒，网络请求超时 5 秒，Prometheus 抓取与查询还会增加可见年龄；用 UP 与年龄一起判定，不把 6 秒当绝对超时阈值。失败时旧数据清空，首次未成功前时间戳 0 使年龄很大。

结合轮询间隔、HTTP 超时和抓取周期解释数据年龄。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
strata_extra_up
```

查询 B：
```promql
time() - strata_extra_last_success_timestamp_seconds
```

### Langfuse 上报失败 / 本地构造错误

面板 ID：`54`；类型：`timeseries`。

**计算公式**

远程失败：`increase(otel_exports_total{status="failure"}[R])`；本地错误：`increase(observation_errors_total[R])`。

**统计口径与边界**

远程按 OTLP HTTP 批次导出调用计数，不是缺失 Span 数或失败推理请求数；本地按构造异常次数计数。R = $__range，异步队列过载丢弃还需结合日志。

分别追踪远程 OTLP HTTP 导出失败与本地构造错误；异步队列的超量丢弃需结合网关日志。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
increase(llm_gateway_otel_exports_total{status="failure"}[$__range])
```

查询 B：
```promql
increase(llm_gateway_observation_errors_total[$__range])
```

### 未配置价格的请求

面板 ID：`55`；类型：`timeseries`。

**计算公式**

`unpriced = Σ increase(llm_gateway_unpriced_requests_total[R])`；回调在两项单价或有效 usage 不完整时每请求加 1。

**统计口径与边界**

R = $__range。此 counter 表示回调无法完整核算的请求数，不是费用；配置 GATEWAY 单价也不等价于已配置 LiteLLM spend 所需的 model_info 价格。

本地模型无官方单价时标为未配置。应先设置真实每 Token 成本，再解释费用为零。

**单位**：计数或原始 gauge；具体单位见该序列口径。

**实际 PromQL**

查询 A：
```promql
sum(increase(llm_gateway_unpriced_requests_total{model=~"$gateway_model"}[$__range]))
```

### LiteLLM 费用 · USD

面板 ID：`56`；类型：`timeseries`。

**计算公式**

`cost_USD = Σ increase(litellm_spend_metric_total[R])`。

**统计口径与边界**

累计金额由 LiteLLM 依模型价格和 usage 计算，Grafana 只取区间增量；需要单独配置 LiteLLM model_info 的价格。无价格时的 0 不代表免费，也不自动读取自定义回调的 Langfuse cost_details。

需要单独配置 LiteLLM model_info 的价格，未配置时 0 不代表免费。

**单位**：美元（USD）。

**实际 PromQL**

查询 A：
```promql
sum(increase(litellm_spend_metric_total{requested_model=~"$gateway_model"}[$__range]))
```

### vLLM / SGLang KV Cache 占用

面板 ID：`57`；类型：`timeseries`。

**计算公式**

直接读取 `vllm:kv_cache_usage_perc{job="vllm"}` 或 `sglang:token_usage{job="sglang"}`，比值 0–1 在 Grafana 显示为 0–100%。

**统计口径与边界**

沿用各引擎自己的 KV / Token 池 gauge，跨引擎内部语义可能不同。明确排除 Strata 的同名兼容指标，Strata 单引擎环境无该样本是预期行为。

本机仅部署 Strata 时该面板无样本；Strata 上下文占用在单独面板。

**单位**：原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100。

**实际 PromQL**

查询 A：
```promql
llm:engine_kv_usage_ratio{model=~"$engine_model"}
```

## 归一化与 recording rule 的完整公式

以下从仓库规则文件直接生成。`label_replace` 只复制 model_name 到 model；`or` 选择存在的序列，不做求和。

```yaml
groups:
  - name: llm-normalized
    interval: 15s
    rules:
      - record: llm:gateway_requests_per_second
        expr: sum by (requested_model) (rate(litellm_proxy_total_requests_metric_total[5m]))
      - record: llm:gateway_input_tokens_per_second
        expr: sum by (requested_model) (rate(litellm_input_tokens_metric_total[5m]))
      - record: llm:gateway_output_tokens_per_second
        expr: sum by (requested_model) (rate(litellm_output_tokens_metric_total[5m]))
      - record: llm:engine_requests_running
        expr: |
          label_replace(vllm:num_requests_running{job=~"strata|vllm"}, "model", "$1", "model_name", "(.+)")
          or label_replace(sglang:num_running_reqs{job="sglang"}, "model", "$1", "model_name", "(.+)")
      - record: llm:engine_requests_waiting
        expr: |
          label_replace(vllm:num_requests_waiting{job=~"strata|vllm"}, "model", "$1", "model_name", "(.+)")
          or label_replace(sglang:num_queue_reqs{job="sglang"}, "model", "$1", "model_name", "(.+)")
      - record: llm:engine_input_tokens_per_second
        expr: |
          label_replace(rate(vllm:prompt_tokens_total{job=~"strata|vllm"}[5m]), "model", "$1", "model_name", "(.+)")
          or label_replace(rate(sglang:prompt_tokens_total{job="sglang"}[5m]), "model", "$1", "model_name", "(.+)")
      - record: llm:engine_output_tokens_per_second
        expr: |
          label_replace(rate(vllm:generation_tokens_total{job=~"strata|vllm"}[5m]), "model", "$1", "model_name", "(.+)")
          or label_replace(rate(sglang:generation_tokens_total{job="sglang"}[5m]), "model", "$1", "model_name", "(.+)")
      - record: llm:engine_ttft_seconds_bucket
        expr: |
          label_replace(vllm:time_to_first_token_seconds_bucket{job=~"strata|vllm"}, "model", "$1", "model_name", "(.+)")
          or label_replace(sglang:time_to_first_token_seconds_bucket{job="sglang"}, "model", "$1", "model_name", "(.+)")
      - record: llm:engine_itl_seconds_bucket
        expr: |
          label_replace(vllm:inter_token_latency_seconds_bucket{job=~"strata|vllm"}, "model", "$1", "model_name", "(.+)")
          or label_replace(sglang:inter_token_latency_seconds_bucket{job="sglang"}, "model", "$1", "model_name", "(.+)")
      - record: llm:engine_kv_usage_ratio
        expr: |
          label_replace(vllm:kv_cache_usage_perc{job="vllm"}, "model", "$1", "model_name", "(.+)")
          or label_replace(sglang:token_usage{job="sglang"}, "model", "$1", "model_name", "(.+)")
      # Strata's vllm:kv_cache_usage_perc is context occupancy, not physical KV
      # allocation. Keep it in a separate series and explicitly labelled panel.
      - record: llm:strata_context_usage_ratio
        expr: label_replace(vllm:kv_cache_usage_perc{job="strata"}, "model", "$1", "model_name", "(.+)")
      - record: llm:gpu_utilization_percent
        expr: |
          max by (deployment, gpu) (strata:gpu_util{job="strata"})
          or max by (deployment, gpu) (DCGM_FI_DEV_GPU_UTIL{job="gpu-dcgm"})
      - record: llm:gpu_memory_used_bytes
        expr: |
          max by (deployment, gpu) (strata:gpu_mem_used_bytes{job="strata"})
          or max by (deployment, gpu) (DCGM_FI_DEV_FB_USED{job="gpu-dcgm"} * 1048576)
      - record: llm:gpu_memory_total_bytes
        expr: |
          max by (deployment, gpu) (strata:gpu_mem_total_bytes{job="strata"})
          or max by (deployment, gpu) ((DCGM_FI_DEV_FB_USED{job="gpu-dcgm"} + DCGM_FI_DEV_FB_FREE{job="gpu-dcgm"}) * 1048576)
      - record: llm:gpu_temperature_celsius
        expr: |
          max by (deployment, gpu) (strata:gpu_temp_celsius{job="strata"})
          or max by (deployment, gpu) (DCGM_FI_DEV_GPU_TEMP{job="gpu-dcgm"})
      - record: llm:gpu_power_watts
        expr: |
          max by (deployment, gpu) (strata:gpu_power_watts{job="strata"})
          or max by (deployment, gpu) (DCGM_FI_DEV_POWER_USAGE{job="gpu-dcgm"})
```

## 公式对应的采集实现

- [网关取样与 TPOT 有效性判断](../litellm/observability_callback.py)：`output_delta`、`tpot_value`、`_record`。
- [Strata JSON 补充指标](../exporters/strata_extra.py)：`FIELDS` 的单位换算与 `update` 的 TPOT / Prefill。
- [归一化规则](../prometheus/recording-rules.yml)：固定窗口、模型标签与 GPU 去重。
- [面板公式说明源](../ops/dashboard_formulas.py) 与 [看板生成器](../ops/build_dashboard.py)：一起生成本页和面板说明。
- Strata 当前运行源码的 `serve/prometheus.py` 与 `serve/server.py`：histogram 加权、最近 / 实时速度、上下文和专家命中率。其他版本可能改变语义。
- LiteLLM 1.106.6 的 `integrations/prometheus.py`：API latency、overhead 与 Token / spend 原生计数。

重新生成：`python ops/build_dashboard.py`。本页及 JSON 均为生成产物，修改公式请先编辑上述公式源或生成器。
