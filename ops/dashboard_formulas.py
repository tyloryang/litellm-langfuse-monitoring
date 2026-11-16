"""Human-readable formulas shared by Grafana tooltips and the generated reference."""

REFERENCE_URL = "https://github.com/tyloryang/litellm-langfuse-monitoring/blob/main/docs/grafana-formulas.md"

# Each formula describes the actual collector/query, including its limitations.
# PromQL is taken from the dashboard targets instead of maintained a second time.
FORMULAS = {
    "来源 IP Token 用量 · 所选时段": (
        "对每个来源 IP 分别计算 `I=Σ increase(source_tokens_total{token_type=\"input\"}[R])`、`O=Σ increase(...{token_type=\"output\"}[R])`、`Total=I+O`、`Requests=Σ increase(source_requests_total[R])`。",
        "R 为所选时间跨度，表格使用当前时点 instant 查询并按 source_ip 关联列，模型和来源 IP 筛选均生效。输入包含缓存、输出包含推理，细分不可再次加到总量。increase 为采样估算，窗口边界及首次抓取会影响结果；精确的启用后累计量见旁边累计表。",
    ),
    "来源 IP Token 累计 · 启用以来": (
        "每个来源 IP：`I=Σ source_tokens_total{token_type=\"input\"}`、`O=Σ ...{token_type=\"output\"}`、`Total=I+O`、`Requests=Σ source_requests_total`。",
        "SQLite 持久化累计值从来源统计开始时间计数，重启不清零；此表不受看板时间范围过滤，模型和来源 IP 筛选仍生效。只统计经过网关且收到最终 usage 的计量，缺失 usage 的请求单独计数；失败请求有有效 usage 时也计入。历史未采集来源 IP 的请求不能回溯归属。",
    ),
    "来源 IP 输入 / 输出 Token 速率": (
        "每 IP 分别计算 `Σ rate(source_tokens_total{token_type=\"input\"}[W])` 与 `Σ rate(...{token_type=\"output\"}[W])`。",
        "W=$__rate_interval，单位 Token/s，先对每条 counter 求 rate 再按来源 IP 求和；输入包含缓存、输出包含推理。请求结束时累计最终 usage，长请求会在完成时形成阶梯。",
    ),
    "来源 IP 缓存 / 推理 Token 速率": (
        "每 IP 分别计算 `Σ rate(source_tokens_total{token_type=\"input_cached\"}[W])` 和 `Σ rate(...{token_type=\"output_reasoning\"}[W])`。",
        "缓存与推理分别是输入和输出子集，不能重复加总。上游缺少细分字段时累计值保持 0，是否实际提供细分以 Langfuse usage_raw 为准。",
    ),
    "来源 IP usage 缺失请求": (
        "每 IP `Missing=Σ increase(llm_gateway_source_usage_missing_total[R])`。",
        "最终 prompt/input 与 completion/output Token 数缺失或无效时加 1；不使用帧数估计 Token。请求数仍记录，Token 累计可能低于实际消耗。",
    ),
    "来源统计持久化状态": (
        "`persistent=1` 当 GATEWAY_SOURCE_USAGE_DB 为磁盘数据库，否则 `persistent=0`。",
        "必须把数据库目录放在持久化挂载或 WSL 数据目录，并保留读写权限。来源 IP / 模型明细默认上限 1024 组，额外来源合并至 source_ip=overflow、model=all；unknown 表示无法取得有效传输来源。",
    ),
    "来源统计开始时间": (
        "`started_display=1000 × 首次创建来源计量数据库时的 Unix 时间戳（秒）`，转换成 Grafana 日期格式所需的毫秒。",
        "创建时间存入 SQLite，服务重启时沿用，删除计量数据库才会重新开始。它标记累计表覆盖的起点，不表示旧历史已补算。",
    ),
    "来源统计写入失败": (
        "`Errors=increase(llm_gateway_source_usage_errors_total[R])`。",
        "来源计量准备或终结写库失败时加 1。计量失败不会中止模型请求，非零需结合日志确认缺失范围；进程 counter 重启归零由 increase 处理。",
    ),
    "网关可用性": (
        "`UP_gateway = min(up{job=\"litellm\"})`。",
        "任一已抓取的网关目标为 0，则结果为 0；没有目标时为空。这里只验证 Prometheus 抓取成功，不等价于一次推理成功。",
    ),
    "解码速度 · 最近请求": (
        "`decode_tok_s = engine_generated / (decode_ms / 1000)`，Strata 将结果保留 1 位小数。",
        "直接读取 `requests[0].decode_tok_s`；engine_generated ≥ 2 且有 decode_ms 时才有值。包含推理 Token，空闲时保留最近完成请求；其 N/T 与 TPOT 的 T/(N−1) 不能直接取倒数，engine_generated 与 output_tokens 的计数边界也可能不同。",
    ),
    "GPU 利用率": (
        "`Util_display = Util_driver`，保留驱动提供的 0–100 百分数；归一化按 `(deployment, gpu)` 取最大值去重。",
        "直接读取 Strata / DCGM 的 GPU 忙碌采样，不用显存占比、Token 速度或功耗推导。多卡分别展示，不把百分数相加。",
    ),
    "显存占用率": (
        "`VRAM% = 100 × memory_used_bytes / memory_total_bytes`。",
        "分子分母按相同 deployment / gpu 匹配，每张卡单算；包含驻留模型和其他显存分配，不等价于 KV 使用率。分母缺失或为 0 时没有有效百分比。",
    ),
    "TTFT · 引擎近5分钟 P50": (
        "`TTFT_P50_5m = histogram_quantile(0.50, Σ_by_le rate(engine_ttft_bucket[5m]))`；仅当近 5 分钟有新样本时展示。",
        "单位秒；使用引擎服务 histogram 的首输出时间，按桶估计中位数。它不是最近单个请求的原始首 Token 时间；部分 Strata 版本没有 first_engine_token_s，不能伪造该字段。",
    ),
    "最近 TPOT · 引擎均值": (
        "`TPOT_last_engine = (decode_ms / 1000) / (output_tokens − 1)`，要求 `output_tokens > 1` 且有 decode_ms。",
        "单个最近请求的平均 Token 间隔，包含推理 Token；缺少有效分母时为 NaN。不是逐 Token 时间戳，也不是所有请求的平均值。",
    ),
    "实时解码 / Prefill 速度": (
        "Decode：每个运行请求在最近 2 秒样本内取 `Δgenerated / Δtime`，再对并发请求求和；样本跨度不足 0.25 秒时用 `generated / max(0.25, now − first_token)`。\n\nPrefill：直接读取引擎 `live.prefill_tok_s_mean`，其口径为新读取 Token / 处理秒数，排除缓存前缀。",
        "两条都是 Strata 的实时 gauge，非累计 counter 的 rate；空闲为 0。实时 Prefill 不从最近完成请求或输出 Token 推导，也不保证是多请求吞吐之和。",
    ),
    "GPU / 显存占用趋势": (
        "GPU 曲线：`Util_driver`；显存曲线：`100 × memory_used_bytes / memory_total_bytes`。",
        "两条曲线都显示 0–100%，分别表示计算忙碌与显存容量占用，不能将二者视为同一指标。",
    ),
    "请求运行 / 排队": (
        "Strata：`running = live.running`（缺失时按 reading / generating 状态回退到 0 或 1）；`waiting = live.queued + live.waiting`。",
        "直接展示当前 gauge，不求 rate。其他引擎分别映射其 running / queue gauge；排队可能来自等待槽位或引擎控制通道。",
    ),
    "TTFT · 网关 P50 / P95 / P99": (
        "每请求 `TTFT = t_first_valid_delta − t0`；面板对该 histogram 计算 `Q(0.50)`、`Q(0.95)`、`Q(0.99)`。",
        "t0 为网关 pre-call hook 的单调时钟；有效增量包括正文、推理或工具名 / 参数，排除 role、空帧及 usage。仅流式有样本；已产生首输出后失败的请求仍可有 TTFT。时间戳在网关迭代器取样，不是客户端实际收包时间。",
    ),
    "TPOT · 网关请求均值 P50 / P95 / P99": (
        "每请求 `TPOT = (t_last_valid_delta − t_first_valid_delta) / (completion_tokens − 1)`；再对每请求均值的 histogram 计算 `Q(0.50/0.95/0.99)`。",
        "只收成功流式、最终 usage 有整数输出数 N > 1、至少两帧有效输出且 last > first 的样本。分母包含推理 Token，不能用 SSE 帧数代替。每请求权重为 1；不是逐 Token ITL 分布，也不能用 P95(E2E)−P95(TTFT) 算。",
    ),
    "首段可见答案 · P50 / P95": (
        "每请求 `TTFA = t_first_visible_content − t0`；对 histogram 计算 `Q(0.50)`、`Q(0.95)`。",
        "只以非空正文 content / output_text.delta 作为首次可见答案；纯推理与工具参数不算。长思考时 TTFA 可大于 TTFT，无正文则无该样本。",
    ),
    "TTFT · 引擎 P50 / P95": (
        "Strata 每请求写入 `TTFT_hist = st.first_token − request.started`；对归一化 histogram 计算 `Q(0.50)`、`Q(0.95)`。",
        "保留引擎服务原始首输出标记，不在 Grafana 重建。该标记可能受到服务端解析 / 缓冲影响，与最近 first_engine_token_s 的原始 Token 标记不相同。vLLM / SGLang 使用其各自原生 histogram。",
    ),
    "ITL · 引擎 P50 / P95": (
        "Strata 每请求计算 `gap_mean = decode_seconds / (output_tokens − 1)`，以 `output_tokens − 1` 次写入 histogram，再计算 `Q(0.50)`、`Q(0.95)`。",
        "Strata 按 Token 间隔数加权，每请求会重复同一个平均间隔；这只是加权均值分布估计，无法反映该请求内部的逐 Token 抖动。vLLM / SGLang 的原生观测语义由对应引擎决定。",
    ),
    "端到端延迟 · P50 / P95 / P99": (
        "每请求 `E2E = max(0, t_record_callback − t0)`；对 histogram 计算 `Q(0.50/0.95/0.99)`。",
        "合并所选模型的 stream=true / false 与成功 / 失败样本；从网关 pre-call hook 计时，到终止回调记录。包含流式消费及回调调度时间，不包含 hook 之前的处理、客户端网络与页面渲染。",
    ),
    "LiteLLM 上游 API 延迟 P95": (
        "每请求 `API_latency = end_time − api_call_start_time`（秒）；对 LiteLLM 原生 histogram 计算 `Q(0.95)`。",
        "以 LiteLLM 1.106.6 的 API 计时为准，不是自定义回调的 TTFT。时间戳不完整的样本由 LiteLLM 排除。",
    ),
    "LiteLLM 网关额外开销 P95": (
        "每请求 `overhead_seconds = hidden_params.litellm_overhead_time_ms / 1000`；对该 histogram 计算 `Q(0.95)`。",
        "直接采用 LiteLLM 提供的 overhead 属性；未提供时无样本。没有用两个 P95 相减：分位数不可相减来还原某段耗时。",
    ),
    "TPOT 被排除的请求": (
        "`excluded(reason) = Σ increase(llm_gateway_tpot_skipped_total{reason}[R])`，R 为看板选择的时间范围。",
        "按 failed、non_stream、missing_usage、too_few_tokens、coalesced_or_no_output 分类。increase 对边界外推，可能显示小数；这些请求不会被填成 0 秒混入 TPOT。",
    ),
    "输入 Token / s · 网关": (
        "`input_tok_s(model) = Σ rate(litellm_input_tokens_metric_total[5m])`，按 requested_model 聚合。",
        "这是 recording rule 固定的 5 分钟平均速率，每 15 秒计算一次；不随 Grafana 的 $__rate_interval 变化。输入总量包含上游 usage 中的缓存输入。",
    ),
    "输出 Token / s · 网关": (
        "`output_tok_s(model) = Σ rate(litellm_output_tokens_metric_total[5m])`，按 requested_model 聚合。",
        "固定 5 分钟平均速率；输出总量已包含 reasoning Token，不应再把推理速率加到输出速率上。",
    ),
    "输入 / 输出 Token · 引擎": (
        "每条引擎序列分别为 `rate(prompt_tokens_total[5m])` 与 `rate(generation_tokens_total[5m])`，再将 model_name 映射为 model。",
        "固定 5 分钟窗口，保留引擎 / 实例标签，不跨实例求和；Strata 在请求结束时累计 totals，因此速率可呈阶梯。直接访问引擎的请求也会计入。",
    ),
    "请求 QPS": (
        "`QPS(model) = Σ rate(litellm_proxy_total_requests_metric_total[5m])`，按 requested_model 聚合。",
        "固定 5 分钟平均请求数 / 秒，每 15 秒更新 recording rule。不是当前 in-flight 数，也不是瞬时到达率。",
    ),
    "失败率": (
        "`failure% = 100 × (Σ rate(failed[W]) 或 0) / max(Σ rate(total[W]), 10⁻⁶)`，W = $__rate_interval。",
        "在所选网关模型范围内先合计失败和总请求，再相除；不是各模型失败百分比的算术平均。无失败序列回退到 0，分母下限仅用于数值防护；无流量时的 0 不能说明服务已验证成功。",
    ),
    "网关并发请求": (
        "`in_flight = Σ litellm_in_flight_requests`。",
        "直接合计当前 gauge。当前指标没有 requested_model 标签，因此显示整个网关的并发，网关模型筛选不作用于该面板。",
    ),
    "期间输入 / 输出 Token": (
        "分别计算 `Σ increase(input_tokens_total[R])`、`Σ increase(output_tokens_total[R])`，R = $__range。",
        "采用重置修正和边界外推，可能为非整数。曲线每个求值点都是此前 R 的滚动增量；最右侧点才对应当前选择区间，不要对曲线再次求和。",
    ),
    "缓存读取 / 推理 Token 速率": (
        "分别为 `Σ rate(input_cached_tokens_total[W])`、`Σ rate(output_reasoning_tokens_total[W])`，W = $__rate_interval。",
        "缓存读取是输入子集，推理是输出子集；字段取决于上游 usage。两条细分曲线不可再加到完整输入 / 输出总量上。",
    ),
    "工具调用提议 / s": (
        "每次模型响应按 `Σ len(choice.message.tool_calls)` 增加 counter；面板按 model 计算 `Σ rate(tool_proposals_total[W])`。",
        "单位次 / 秒，一次模型响应可提出多个工具调用。这里只统计模型提议，实际执行数量、耗时和结果由客户端 Langfuse TOOL Span 记录。",
    ),
    "前缀缓存命中率 · Token 加权": (
        "`hit_ratio = Σ rate(prefix_cache_hits_total[W]) / max(Σ rate(prefix_cache_queries_total[W]), 10⁻⁶)`。",
        "Strata 分子来自 totals.reused，分母来自 totals.prompt_tokens。先加 Token 再相除，按 Token 加权；不是命中请求数 / 请求数，也不是各请求命中率平均。ratio=0.8 在 Grafana 显示 80%。",
    ),
    "专家 VRAM 缓存命中率 · 最近请求": (
        "`expert_hit_ratio = hits / lookups`，Strata 对最近请求结果保留 3 位小数；无 lookups 时未知。",
        "读取 requests[0].hit_rate；只衡量专家 VRAM 缓存查找，不是 prompt 前缀缓存。PCIe offloaded 路由不在该 lookups 分母内，不能据此单独推断全部专家路由开销。",
    ),
    "MTP 草稿接受率": (
        "`accept_ratio = Σ rate(accepted_tokens_total[W]) / max(Σ rate(draft_tokens_total[W]), 10⁻⁶)`。",
        "分母是提出的草稿 Token 数，不是请求数；先合计计数再相除。无草稿流量时的 0 / 无数据不代表性能退化，也不能单凭接受率推算加速倍数。",
    ),
    "Strata 上下文占用": (
        "运行时 `context_ratio = round(min(1, (live.prompt_tokens + live.generated) / max_context), 4)`；非 reading / generating 时分子为 0，max_context 缺失时输出 0。",
        "直接对应 Strata 的兼容指标 vllm:kv_cache_usage_perc。是 live 上下文视图的逻辑位置占比，不是物理 KV 字节或所有并发槽位占比之和。",
    ),
    "最大上下文 / GPU 驻留 KV 位置": (
        "两条原始 gauge：`max_context = engine.max_context`；`resident_positions = engine.kv_resident`。",
        "单位均为 Token 位置，但含义不同：配置上限与每层驻留位置。未据此计算字节占用或把层数乘进 Token 数。",
    ),
    "并行槽位状态": (
        "`slots(state) = count(slot in live.slots where slot.state == state)`，分别统计 idle / reading / generating。",
        "直接展示当前状态计数，不求速率；只有引擎实际提供 slots 列表时才有对应样本。",
    ),
    "会话缓存容量 / 预算": (
        "`used_bytes = conversation_cache.bytes`；`budget_bytes = conversation_cache.budget_mib × 2²⁰`。",
        "两条是字节量，不是百分比；Grafana 自动选择 KiB / MiB / GiB 展示。预算为 0 或未配置时不能推导容量利用率。",
    ),
    "会话缓存恢复 / 驱逐速率": (
        "分别为 `rate(conversation_cache_restores_total[W])`、`rate(conversation_cache_evictions_total[W])`。",
        "原始 JSON 是进程生命周期累计值，导出为 gauge 家族但语义按单调 counter 使用 rate，下降会被 Prometheus 解释为重置。单位次 / 秒；不是缓存命中率。",
    ),
    "专家缓存大小": (
        "`expert_cache_bytes = engine.expert_cache_mib × 2²⁰`。",
        "读取引擎报告的专家缓存容量；不是整个 GPU 显存使用量，也没有用总显存减模型大小来估算。",
    ),
    "GPU 显存 已用 / 总量": (
        "Strata 直接取 memory_used_bytes / memory_total_bytes；DCGM 已用为 `FB_USED × 2²⁰`，总量为 `(FB_USED + FB_FREE) × 2²⁰`。",
        "同 deployment / gpu 去重后分别展示已用和总量，不对多卡相加。DCGM 字段以 MiB 转为字节，Strata 原生已是字节。",
    ),
    "GPU 温度": (
        "`temperature = strata:gpu_temp_celsius`，或无同标签 Strata 序列时用 `DCGM_FI_DEV_GPU_TEMP`。",
        "直接硬件采样，单位 °C；归一化按 deployment / gpu 取最大值去重。不是请求期间平均温度。",
    ),
    "GPU 功耗 / 功率上限": (
        "功耗直接取 Strata / DCGM 的 W 采样；功率上限取 `hardware.gpu_power_limit`（W）。",
        "没有计算能耗 kWh；积分功耗才是能量。当前功率上限来自 Strata JSON 的模型级视图，不是通用的逐 GPU DCGM limit。",
    ),
    "PCIe 接收 / 发送": (
        "`RX_Bps = hardware.gpu_pcie_rx_mb × 2²⁰`；`TX_Bps = hardware.gpu_pcie_tx_mb × 2²⁰`。",
        "Strata 这些 _mb 字段实际单位为 MiB/s，因此乘 1048576。它们已经是速率，不再调用 rate；方向按 GPU 硬件视角。",
    ),
    "CPU 使用率": (
        "`CPU% = psutil.cpu_percent(interval=None)` 的主机采样值，原样显示 0–100%。",
        "是主机总 CPU 百分比，不是 LiteLLM / Strata 进程 CPU，也不按核心数相加；Grafana 不从 Token 数推导 CPU 使用率。",
    ),
    "主机 RAM 已用 / 总量": (
        "`used_bytes = virtual_memory.total − virtual_memory.available`；`total_bytes = virtual_memory.total`。",
        "Strata 用主机可用内存口径，包含缓存可回收性的影响；不是简单 total−free，也不是进程 RSS。",
    ),
    "磁盘读 / 写速率": (
        "`read_Bps = hardware.disk_read_mb × 2²⁰`；`write_Bps = hardware.disk_write_mb × 2²⁰`。",
        "JSON 原始值是 MiB/s，展示前换算为 B/s；它们已经是采样速率，不对其再求 rate，不等价于模型文件独占 IO。",
    ),
    "最近请求 Prefill 吞吐": (
        "`prefill_tok_s = max(0, prompt_tokens − reused_tokens) / (prompt_ms / 1000)`，要求 prompt_ms > 0。",
        "只计算实际新读取的 prompt Token，排除缓存复用；无有效处理时间时为 NaN。不能用完整 prompt_tokens 除以 prompt_ms 来夸大 Prefill 吞吐。",
    ),
    "最近请求输入 / 输出 / 复用 Token": (
        "三条原始 gauge：`requests[0].prompt_tokens`、`requests[0].output_tokens`、`requests[0].reused`。",
        "最近完成请求的计数，空闲保留；复用 Token 是输入子集，不能把输入+输出+复用当总 Token。中断请求的 prompt_tokens 可能是实际已处理部分。",
    ),
    "采集目标 UP": (
        "每个已发现目标 `up = 1` 表示本次 scrape 成功，`up = 0` 表示失败。",
        "不做合计，也不为不存在的可选引擎生成 1。HTTP / 文本格式抓取成功不保证 JSON 内部数据新鲜，须结合 JSON poll UP 和数据年龄。",
    ),
    "Strata JSON 采集健康 / 数据年龄": (
        "健康：最近一次 JSON 轮询成功为 1、失败为 0；`age_seconds = time() − last_success_timestamp_seconds`。",
        "两条序列分别是 0/1 与秒。轮询间隔 3 秒，网络请求超时 5 秒，Prometheus 抓取与查询还会增加可见年龄；用 UP 与年龄一起判定，不把 6 秒当绝对超时阈值。失败时旧数据清空，首次未成功前时间戳 0 使年龄很大。",
    ),
    "Langfuse 上报失败 / 本地构造错误": (
        "远程失败：`increase(otel_exports_total{status=\"failure\"}[R])`；本地错误：`increase(observation_errors_total[R])`。",
        "远程按 OTLP HTTP 批次导出调用计数，不是缺失 Span 数或失败推理请求数；本地按构造异常次数计数。R = $__range，异步队列过载丢弃还需结合日志。",
    ),
    "未配置价格的请求": (
        "`unpriced = Σ increase(llm_gateway_unpriced_requests_total[R])`；回调在两项单价或有效 usage 不完整时每请求加 1。",
        "R = $__range。此 counter 表示回调无法完整核算的请求数，不是费用；配置 GATEWAY 单价也不等价于已配置 LiteLLM spend 所需的 model_info 价格。",
    ),
    "LiteLLM 费用 · USD": (
        "`cost_USD = Σ increase(litellm_spend_metric_total[R])`。",
        "累计金额由 LiteLLM 依模型价格和 usage 计算，Grafana 只取区间增量；需要单独配置 LiteLLM model_info 的价格。无价格时的 0 不代表免费，也不自动读取自定义回调的 Langfuse cost_details。",
    ),
    "vLLM / SGLang KV Cache 占用": (
        "直接读取 `vllm:kv_cache_usage_perc{job=\"vllm\"}` 或 `sglang:token_usage{job=\"sglang\"}`，比值 0–1 在 Grafana 显示为 0–100%。",
        "沿用各引擎自己的 KV / Token 池 gauge，跨引擎内部语义可能不同。明确排除 Strata 的同名兼容指标，Strata 单引擎环境无该样本是预期行为。",
    ),

    "自动路由请求速率 · 上下文档位": (
        "`rate_tier = Σ rate(llm_gateway_auto_routes_total[W])`，按 `context_tier` 分组。",
        "只统计 LiteLLM 写出 routing_decision 的已完成请求。context_tier 由自定义 classifier 按输入和预留输出 Token 选为 CONTEXT_32K / 64K / 128K / 256K，并决定 routed_model。启发式档位不参与选模。W = $__rate_interval。模型名、档位和原因只保留有限枚举，提示词原文不会成为标签；枚举外的值合并为 other。",
    ),
    "自动路由请求速率 · 路由模型": (
        "`rate_model = Σ rate(llm_gateway_auto_routes_total[W])`，按 `routed_model` 分组。",
        "routed_model 是实际转发的模型，当前为 qwen-32k / qwen-64k / qwen-128k / qwen-256k。请求能放进哪个窗口，就进哪个模型；放不下时由 LiteLLM 的 context-window escalation 升档。不要用启发式 COMPLEX 解释为已经改派到更大的模型。",
    ),
    "启发式档位速率": (
        "`rate_heuristic = Σ rate(llm_gateway_auto_routes_total[W])`，按 `heuristic_tier` 分组。",
        "heuristic_tier 是官方 ComplexityRouter 的 SIMPLE / MEDIUM / COMPLEX / REASONING，只描述内容特征。它和上下文档位是两套结果：短请求可以同时是 CONTEXT_32K 和 COMPLEX。没有启发式结果时记为 unknown，不补成 SIMPLE。",
    ),
    "启发式分数 P50 / P95 / P99": (
        "`score = Σ(dimension_score × dimension_weight)`。默认权重为 tokenCount 0.10、codePresence 0.30、reasoningMarkers 0.25、technicalTerms 0.25、simpleIndicators 0.05、multiStepPatterns 0.03、questionComplexity 0.02。分界为 simple/medium 0.15、medium/complex 0.35、complex/reasoning 0.60。面板对该 histogram 计算 `Q(0.50)`、`Q(0.95)`、`Q(0.99)`。",
        "只把 score ≥ 0 的自动路由请求观察进 histogram。simpleIndicators 的维度分可以为 −1，总分小于 0 时不进入本图，但仍计入路由次数和信号贡献。分数解释内容，不选择模型。桶内分位是估计值，样本少时不稳定。",
    ),
    "已触发信号次数": (
        "`firings_signal = Σ increase(llm_gateway_route_signal_firings_total[R])`，按官方维度名分组。",
        "只有 signal 非空的维度加 1。未触发的维度保留在权重表中，但不会出现在本图。允许的维度名只有 tokenCount、codePresence、reasoningMarkers、technicalTerms、simpleIndicators、multiStepPatterns、questionComplexity。R = $__range，曲线最右端就是所选时段，不要再把整条曲线求和。",
    ),
    "已触发信号平均贡献": (
        "`avg_contribution_signal = Δ(llm_gateway_route_signal_contribution[R]) / max(Σ increase(firings_signal[R]), 10⁻⁶)`，其中单次 `contribution = dimension_score × weight`。",
        "分子是带符号 gauge 的窗口增量，simpleIndicators 可以是负数，所以不能使用 Prometheus counter。分母只包含已触发次数。结果是该窗口内已触发信号的平均贡献，不是配置权重；权重见旁边的维度权重面板。进程重启后累计值从 0 重新开始。",
    ),
    "路由原因分布": (
        "`routes_cause = Σ increase(llm_gateway_auto_routes_total[R])`，按 `route_cause` 分组。",
        "route_cause 取 LiteLLM routing_decision.cause 的有限枚举。classifier_plugin 表示自定义 token classifier 选出了档位；不在 LiteLLM 1.106.6 枚举中的原因合并为 other。曲线最右端是所选时段的次数，increase 可能因采样边界而不是整数。",
    ),
    "自动路由结果": (
        "每个 `(context_tier, routed_model, heuristic_tier, route_cause)` 组合计算 `requests = Σ increase(llm_gateway_auto_routes_total[R])`。",
        "当前时点的 instant 表，按请求数降序。四个标签一起标识一组路由结果，用来对照“内容落在哪个窗口”和“启发式认为内容有多复杂”。启发式档位不是选模依据。increase 是采样估算，窗口边界和首次抓取会使结果出现小数。",
    ),
    "启发式维度权重": (
        "官方默认 `weight(tokenCount)=0.10`、`weight(codePresence)=0.30`、`weight(reasoningMarkers)=0.25`、`weight(technicalTerms)=0.25`、`weight(simpleIndicators)=0.05`、`weight(multiStepPatterns)=0.03`、`weight(questionComplexity)=0.02`。`score = Σ(dimension_score × weight)`。",
        "面板读取进程内官方 scorer 的当前配置，不是从历史流量反推。首次完成自动路由时写入全部维度，包括尚未触发的维度。LiteLLM 升级或权重配置变化后，以新进程实际值为准；默认值只是 1.106.6 的对照，不在查询里写死。",
    ),
}

UNITS = {
    "s": "秒；Grafana 可自动显示为毫秒等子单位",
    "suffix:tok/s": "Token / 秒",
    "percent": "百分数 0–100，表达式已经乘过 100 或原始值本来是百分数",
    "percentunit": "原始比值 0–1，由 Grafana 格式化为百分比，不再乘 100",
    "bytes": "字节，Grafana 自动选择 KiB / MiB / GiB",
    "Bps": "字节 / 秒，不是 bit / 秒",
    "watt": "瓦（W）",
    "celsius": "摄氏度（°C）",
    "reqps": "请求 / 秒",
    "ops": "次 / 秒",
    "currencyUSD": "美元（USD）",
    "short": "计数或原始 gauge；具体单位见该序列口径",
    "dateTimeAsIso": "Unix 毫秒，由 Grafana 显示为日期时间",
}


def panel_description(title, unit, notes, expressions):
    formula, scope = FORMULAS[title]
    details = f"{scope}\n\n{notes}" if notes else scope
    queries = "\n\n".join(f"查询 {chr(65 + i)}：\n```promql\n{expr}\n```" for i, expr in enumerate(expressions))
    return (f"**计算公式**\n\n{formula}\n\n**统计口径与边界**\n\n{details}"
            f"\n\n**单位**：{UNITS[unit]}。\n\n**实际 PromQL**\n\n{queries}")


INTRODUCTION = """# Grafana 看板计算公式与统计口径

**本页覆盖全部 {metric_count} 个指标面板、{query_count} 条实际 PromQL。每个面板的公式也写入 Grafana 的说明，点击标题旁的信息图标即可查看。** 看板顶部另提供本页入口。公式、查询和本页由 `ops/build_dashboard.py` 一起生成，避免文档与看板各自维护。

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
| 自动路由模型 | 自定义 classifier 按 Token 选择 `CONTEXT_*`，再映射到 `qwen-*` | 启发式分数只解释内容，不改变选中的模型 |
| 启发式分数 | `Σ(dimension_score × weight)`，分界 0.15 / 0.35 / 0.60 | 只把已触发信号计入贡献；simpleIndicators 可以是负贡献 |
| 信号平均贡献 | `Δ(signed contribution[R]) / increase(firings[R])` | 未触发的维度不进入平均；配置权重单独看维度权重面板 |

## 符号、窗口和聚合

- `t0`：自定义回调创建 RequestState 的单调时钟；不包含 hook 之前的鉴权 / 网络等待，也不包含客户端网络和页面渲染。
- `W = $__rate_interval`：Grafana 根据查询分辨率和数据源 scrape 设置动态选择的速率窗口，通常为 `max($__interval + scrape_interval, 4 × scrape_interval)`；以实际展开值为准。在线验收脚本用 1m 替换 W。
- `R = $__range`：看板选中的完整时间跨度。`increase(counter[R])` 在每个求值点计算此前 R 的滚动增量；最右端值对应所选区间，不要再对整条曲线求和。
- `rate` / `increase` 会处理累计值下降的重置并对窗口边界外推，所以请求数 / Token 增量可能不是整数。`rate` 至少需要两个样本；没有请求或样本不足时可以无值 / NaN，不能填成 0 秒延迟。
- `Q(q)` 在本页统一表示 `histogram_quantile(q, sum by (le) (rate(<metric>_bucket[W])))`。本看板的延迟分位按所选模型及其全部实例合并 bucket；不保留逐模型 / 实例曲线。桶内使用分布估计，精度受 bucket 和样本数限制。
- **网关输入 / 输出 Token/s、QPS 和引擎 Token/s 的 recording rule 使用固定 `[5m]`，每 15 秒求值。** 它们不会因为 Grafana 切换时间范围而变成 1m 或瞬时速率。
- 网关筛选使用 `requested_model`（自定义时延与来源计量使用 `model`）；原生引擎使用 `model_name`，归一化后为 `model`；GPU 按 `deployment, gpu` 筛选。来源 IP 筛选只作用于第 07 区的来源用量 / 速率面板，其他面板仍保留原聚合范围。第 08 区使用网关模型、路由模型和上下文档位筛选；启发式档位、分数和信号权重不反向影响路由。
- GPU recording rule 先按 `(deployment, gpu)` 取 max 去重，再以 PromQL `or` 优先选 Strata、缺失时选 DCGM；不是把两个采集源相加。引擎归一化只复制模型标签并选择对应引擎指标，不改变 histogram 的计数语义。
- `percent` 接收 0–100，`percentunit` 接收 0–1 并显示为百分数；容量基础单位为字节，`Bps` 表示字节 / 秒。MiB/s 到 B/s 的换算固定乘 2²⁰，与 Grafana 自动选择的显示前缀分开理解；`W × s` 才是能量，功耗 W 不是电量 kWh。
- “最近请求”是 gauge，空闲仍保留最后请求值；不是“此刻正在生成”的值。timeseries 图例 `Last` / `Max` 是曲线值的最近 / 最大值；P95 曲线的 Max 只是窗口 P95 的最大值，不是请求最大延迟。
- Strata 最近原始引擎 TTFT 使用 `first_engine_token_s`；引擎 TTFT histogram 使用服务的 `st.first_token − started`。两种内部计时标记不同，应分别解释，不保证数值相同。

## 全部面板的公式与实际查询

以下按看板原顺序列出；PromQL 原样保留 Grafana 变量，可在 Grafana 查询检查器查看展开值。
"""


def write_reference(root, dashboard):
    metrics = [p for p in dashboard["panels"] if p["type"] != "row"]
    if set(FORMULAS) != {p["title"] for p in metrics}:
        raise ValueError("Formula definitions must cover exactly the metric panels")
    introduction = INTRODUCTION.replace("{metric_count}", str(len(metrics))).replace("{query_count}", str(sum(len(p.get("targets", [])) for p in metrics)))
    lines = [introduction.rstrip(), ""]
    for panel in dashboard["panels"]:
        if panel["type"] == "row":
            lines += [f"## {panel['title']}", ""]
            continue
        lines += [f"### {panel['title']}", "", f"面板 ID：`{panel['id']}`；类型：`{panel['type']}`。", "",
                  panel["description"], ""]
    rules = (root / "prometheus/recording-rules.yml").read_text(encoding="utf-8")
    lines += ["## 归一化与 recording rule 的完整公式", "",
              "以下从仓库规则文件直接生成。`label_replace` 只复制 model_name 到 model；`or` 选择存在的序列，不做求和。", "",
              "```yaml", rules.rstrip(), "```", "", "## 公式对应的采集实现", "",
              "- [网关取样与 TPOT 有效性判断](../litellm/observability_callback.py)：`output_delta`、`tpot_value`、`_record`。",
              "- [Strata JSON 补充指标](../exporters/strata_extra.py)：`FIELDS` 的单位换算与 `update` 的 TPOT / Prefill。",
              "- [归一化规则](../prometheus/recording-rules.yml)：固定窗口、模型标签与 GPU 去重。",
              "- [面板公式说明源](../ops/dashboard_formulas.py) 与 [看板生成器](../ops/build_dashboard.py)：一起生成本页和面板说明。",
              "- Strata 当前运行源码的 `serve/prometheus.py` 与 `serve/server.py`：histogram 加权、最近 / 实时速度、上下文和专家命中率。其他版本可能改变语义。",
              "- LiteLLM 1.106.6 的 `integrations/prometheus.py`：API latency、overhead 与 Token / spend 原生计数。", "",
              "重新生成：`python ops/build_dashboard.py`。本页及 JSON 均为生成产物，修改公式请先编辑上述公式源或生成器。", ""]
    output = root / "docs/grafana-formulas.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
