# 观测指标与 Langfuse 缺失调查

调查与验收日期：2026-11-09。本机以 Strata 为推理引擎，LiteLLM 为网关，复用 Langfuse v4、Prometheus 和 Grafana。下文区分网关观察、引擎计算与客户端实际执行。

## Grafana 缺失原因与修复

| 发现 | 原因 | 已实施的修复 |
| --- | --- | --- |
| GPU、显存、引擎时延未展示 | Prometheus 只抓 LiteLLM 与自身，未抓 Strata | 接入 Strata 原生 `/metrics?format=prometheus`，使用 Bearer 认证 |
| PCIe、磁盘、会话缓存等字段缺失 | 原生 Prometheus 比 Monitor JSON 暴露得少 | 新增 JSON 导出器，补齐真实字段，不重复导出原生指标 |
| 默认 `/metrics` 解析失败 | Strata 默认返回 JSON | 抓取配置显式设置 `params.format: [prometheus]` |
| 查询模型标签不匹配 | 网关 `requested_model` 与引擎 `model_name` 不同 | 分别筛选网关 / 引擎模型，16 条 recording rule 统一引擎与 GPU 指标 |
| TPOT 易算错 | SSE 帧数不等于 Token，role / 空帧也不是首输出 | 识别内容、推理、工具增量，使用最终 usage 作分母 |
| Strata KV 指标被当作物理显存 | 兼容名称 `vllm:kv_cache_usage_perc` 实际是上下文位置占用 | 独立展示 Strata context；物理 KV 面板与 vLLM KV 告警限定对应引擎 |
| 吞吐单位偏差 | PCIe / 磁盘 `_mb` 字段实际是 MiB/s | 乘 1048576 后按 B/s 展示 |
| 来源 IP / 按 IP Token 缺失 | Nginx 有转发头，但回调未导出来源；旧 Token 指标没有 IP 维度 | 校验连接与可信代理，写入 Langfuse metadata、SQLite 用量账本与第 07 分区 |

JSON 导出器每 3 秒轮询，Prometheus 每 5 秒抓取 Strata。轮询失败即清空旧数据、置 `strata_extra_up=0`；最后成功时间用于判断数据年龄。

## 指标参考与口径

**各面板计算公式见 [Grafana 看板计算公式与统计口径](grafana-formulas.md)**：包含全部 59 面板的原始计算、94 条实际 PromQL、固定 / 动态窗口、聚合权重及单位换算，同步显示在 Grafana 面板说明中。

GitHub star 是 2026-11-09 API 快照，用于选择生态参考：

| 项目 | 当日 star | 参考指标 |
| --- | ---: | --- |
| [vLLM](https://github.com/vllm-project/vllm) | 96,588 | TTFT / ITL / E2E、输入输出 Token、运行 / 排队、KV、前缀命中、投机解码 |
| [SGLang](https://github.com/sgl-project/sglang) | 38,605 | 首 Token、Token 间隔、吞吐、运行 / 队列、Token 池使用率 |
| [NVIDIA dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) | 1,924 | GPU 利用率、显存、温度、功耗与硬件诊断 |
| [Langfuse](https://github.com/langfuse/langfuse) | 37,178 | OTLP generation、Agent / Tool、Session / User、usage 和成本 |

本机直接使用 Strata 在 WSL 中采到的 GPU 数据，避免重复安装 DCGM。归一化规则保留 DCGM / vLLM / SGLang 接入，但这些目标未在本机部署。

| 指标 | 采集 / 计算 | 解释边界 |
| --- | --- | --- |
| 网关 TTFT | 接收请求 → 首个有效内容、推理或工具增量 | 仅流式，包含网关和上游路径；排除 role / 空 / usage 帧 |
| 首段可见答案 TTFA | 请求到首个可见文本 | 内部推理不代表用户已看到答案 |
| 网关请求均值 TPOT | `(last - first) / (completion_tokens - 1)` | 成功流式、usage 完整、至少 2 个 Token 且多帧输出；包括推理 Token，受网络缓冲影响 |
| 引擎 TTFT | Strata 首引擎 Token 时间与原生 histogram | 与网关观察边界不同 |
| 引擎 ITL | Strata 将 decode 平均间隔按 Token 间隔数加权 | 不是逐 Token 时间戳，也不是每请求 TPOT 分布 |
| 最近引擎 TPOT | `decode_ms / 1000 / (output_tokens - 1)` | 对齐 Monitor 请求表平均 decode 时间 |
| Prefill | 新处理 prompt Token / prompt 处理时间 | 最近请求扣除 reused，不把缓存复用计为 Prefill 算力 |
| 输入 / 输出 Token | 网关与引擎分别计数、求速率和期间增量 | 绕过网关的请求只计入引擎 |
| 推理 / 缓存 Token | 上游 usage details | 字段缺失保留未知；reasoning 属于输出总量子集 |
| 前缀命中率 | 命中 Token / 查询 Token | 与专家 VRAM hit rate、会话缓存恢复分开 |
| MTP 接受率 | accepted / draft Token counter 速率 | 无草稿流量时不能推断收益 |
| GPU | utilization、显存 used / total、温度、功耗 / 上限、PCIe RX / TX | 模型驻留时显存可长期高占用；W、B/s 各用正确单位 |
| KV 与上下文 | Strata context / 驻留位置，其他引擎 KV 单列 | 不混用各引擎的分配语义 |
| 工具提议 | 模型输出 `tool_calls` 数 | 不代表已执行，执行结果到 Langfuse TOOL 查看 |
| 成本 | 可选真实 USD / Token 价格 | 本地模型默认 unconfigured，0 不代表免费 |

物理服务器还可经 DCGM 增加 SM / Tensor 活跃度、DRAM 活跃度、PCIe / NVLink、ECC / XID 等诊断。本机 Strata / WSL 未提供的字段不伪造。增加来源 IP 统计后实际接入 59 个指标面板，7 个 row 仅为分隔。

P50 / P95 / P99 是抓取窗口中 histogram 的分位估计，少样本时受 bucket 精度限制。重启后只抓到第一条 counter 样本，或窗口内没有新请求时，速率 / 分位可以没有值；最近请求引擎值仍保留，不能填成 0 延迟。vLLM / SGLang KV 面板在本机 Strata 单引擎环境无数据是预期行为。

## Langfuse 缺失根因

### 输入输出为空

旧 `.env` 为 `LITELLM_OTEL_V2=true`、`LITELLM_OTEL_LANGFUSE_SPAN_SCOPE=llm_only`，没有设置 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`。LiteLLM 1.106.6 的 `integrations/otel/model/config.py` 中 `capture_message_content` 默认 `NO_CONTENT`，与旧记录有 Token / metadata 却无 input / output 的现象一致。`llm_only` 控制 Span 范围，不能开启内容采集。

全局 OTEL V2 还产生自动 HTTP Span 与默认控制台 exporter，日志出现大量额外 Span。现在关闭该全局路径，由项目独立 OTLP provider 显式上报 generation 的输入输出、usage、模型参数、状态和流式时间，避免多个 generation 回调重复工作。

### 实际修改的配置

本机生效文件是 WSL 运行目录下的 `/home/YOUR_USER/litellm-langfuse-monitoring/.env` 与 `litellm/config.local.yaml`。配置调整如下，项目密钥与 Langfuse Web / Worker、数据库配置沿用原值：

| 配置项 | 调整前 | 当前实际状态 |
| --- | --- | --- |
| `LITELLM_OTEL_V2` | `true` | `false`，关闭全局 V2 自动埋点 |
| `LITELLM_LOCAL_MODEL_COST_MAP` | 未设置 | `True`，使用 LiteLLM 内置价格表，避免离线环境获取远程表；这不代表本地模型已有价格 |
| `litellm_settings.callbacks` | `prometheus`、`langfuse_otel` | `prometheus`、`observability_callback.observability` |
| `LITELLM_OTEL_LANGFUSE_SPAN_SCOPE` | `llm_only` | 运行环境仍保留旧值，但全局 V2 已关闭，不再由该路径负责上报 |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | 未设置 | 仍未设置；输入输出由自定义回调显式采集 |
| `LANGFUSE_TRACING_ENVIRONMENT` | 未设置 | 本机仍未设置，回调默认记录为 `wsl-local` |

网关加载的 YAML 为：

```yaml
litellm_settings:
  callbacks:
    - prometheus
    - observability_callback.observability
```

新增的 `litellm/observability_callback.py` 使用既有 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`，向 `${LANGFUSE_HOST}/api/public/otel/v1/traces` 上报，带 `x-langfuse-ingestion-version: 4`。内容采集由该代码完成，不是仅靠切换环境变量。WSL 的 `litellm-gateway.service` 通过 `litellm/run-local.sh` 读取 `.env` 和本机 YAML，重启网关后配置生效。

仓库中的 `.env.example` 是其他环境部署用的模板，其 `LANGFUSE_TRACING_ENVIRONMENT=production` 不代表本机已经设为 production。Compose 模板另增加回调文件挂载；本机实际运行的网关采用 systemd 与共享 venv。

### 工具链路未展示

`tool_calls` 只表示模型提出执行请求。工具通常在 IDE、Agent 或客户端执行，网关不知道开始 / 结束时间、结果及异常。旧客户端没有真实 TOOL Span，也未传播同一条 Trace 的父节点。

示例在应用端建立 Agent、实际执行 Tool，用标准 `traceparent` 关联两次 generation，保持 Session / User 一致。实际验收结构为：

```text
AGENT tool-chain-validation
  ├── GENERATION chat qwen-strata      提出 add_numbers
  ├── TOOL add_numbers                {a:17,b:25} → {result:42}
  └── GENERATION chat qwen-strata      最终答案 42
```

其他客户端需要按示例接入 SDK 或兼容的 OpenTelemetry 工具埋点。网关无法补出客户端没有发送的执行 Span；同一 Session 也不自动代表同一 Trace。

### API 返回 404 或缺少时延字段

本机 Langfuse 4.73.0 使用 events-only 路径。旧 `/api/public/traces`、`/api/public/observations` 返回 404 并提示迁移，不能据此判断写入失败。改用 `/api/public/v2/observations` 查询，写入为 `/api/public/otel/v1/traces`，带 `x-langfuse-ingestion-version: 4`。

v2 查询还需显式选择字段组。只取 `core,basic,io,usage,metadata,model` 时不返回 `timeToFirstToken`；加入 `time,metrics` 后才返回 `completionStartTime`、TTFT 和 latency。验收已修正，部分字段响应不能解释成数据库缺失。datetime 属性按 Langfuse SDK 格式编码。

### Session、用户、推理用量与成本

旧请求未提供业务标识，网关不能猜测 Session / User。现在接受 metadata / 头和 OpenAI `user`，并沿父 Trace 传播。usage 保留上游原始包含关系，发送到 Langfuse 时互斥细分：输入扣除 cached，输出扣除 reasoning，另列 cached / reasoning，确保不重复计入总量。

本地模型缺少实际价格，记录 `pricing_status=unconfigured` 并计数。核算成本时设置 `GATEWAY_INPUT_COST_PER_TOKEN`、`GATEWAY_OUTPUT_COST_PER_TOKEN`；LiteLLM spend 指标还需单独配置模型价格，不能从 GPU 利用率推导。

### 错误、中断与上报健康

失败和流式中断记录 ERROR，TPOT 排除失败样本并标记原因。终止回调去重以避免重复 generation。本地构造错误、远程 OTLP 成功 / 失败和最后成功时间都有指标与告警。异步队列极端过载丢弃还需结合日志，未做压力验收。

## 来源 IP 与用量补充

新增 `source_ip`、`network_peer_ip`、`source_ip_origin` 到 GENERATION / Trace metadata；按来源和模型累计真实 usage。`general_settings.use_x_forwarded_for=false` 与空 `FORWARDED_ALLOW_IPS` 保留连接 peer，`GATEWAY_TRUSTED_PROXY_CIDRS` 只信任明确代理；Nginx 覆盖客户端转发头。累计值写入 SQLite，所选时段用 Prometheus `increase` 外推，缓存 / 推理是输入 / 输出子集，不能重复相加。历史没有来源的请求无法准确补回。配置、公式、查看位置和恢复步骤见 [来源统计说明](source-ip-usage.md)。

## 本机初次验收证据（增加来源 IP 之前）

- `litellm`、`prometheus`、`strata`、`strata-extra` 四个目标 UP；配置、16 条 recording rule、9 条 alert rule 均通过 `promtool`。
- 78 条面板 PromQL 均在真实 Prometheus 上成功执行；GPU、显存、引擎时延、并发和会话缓存有采样，已检查 Grafana 页面。
- 流式 Trace `3a7fe4af1ac93dc1bb7728148bfd9ed1`：输入 70、输出 72（推理 41、文本 31）、总量 142；Langfuse TTFT 2.193 s、网关 TPOT 66.08 ms。
- 缓存复用 Trace `8419032a1d22c24286980f277d509983`：输入 70（缓存 65）、输出 72（推理 40、文本 32）、总量仍 142；TTFT 0.465 s、TPOT 73.86 ms。两个实际请求不构成缓存加速的基准结论。
- 工具 Trace `a9880c8931c965de38f2bdcebca77c78`：一个 Agent、两次 generation、一个真实 TOOL，参数 / 结果、父链、Session / User 校验通过，最终答案 42。
- 回归测试覆盖首输出、帧与 Token 区分、usage 守恒、并发隔离、父链传播、凭证排除、错误 / 中断去重、单位和模型切换。

本次按 WSL 单 worker 网关验收。全新 Compose、多 worker 指标合并、其他引擎在线兼容性、业务客户端工具埋点和压力容量尚未验收。历史记录原本未上传的内容不会回填。
