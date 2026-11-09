# LiteLLM / Langfuse / Strata 推理观测

Windows + Ubuntu 24.04 WSL 的运行配置、Grafana 看板和 Langfuse 工具链路示例。2026-11-09 已复用本机现有服务完成真实推理、指标抓取和工具执行链路验证。

本次复用既有 LiteLLM 虚拟环境、Strata 推理服务、Grafana、Prometheus、Langfuse 及其数据库和数据卷。新增一个轻量 Strata JSON 指标导出器，并更新网关回调、抓取规则和看板。

## 本机入口

| 服务 | Windows 访问地址 | 用途 |
| --- | --- | --- |
| Grafana | [推理观测看板](http://localhost:3002/d/litellm-gpu-overview) | 51 个指标面板、6 个分区 |
| Langfuse | [项目观测列表](http://localhost:3001/project/7e8bc81c-0f6b-4377-bd85-9e06624fbafb/traces) | 输入输出、推理 Token、工具执行和 Session |
| LiteLLM | `http://localhost:4000/v1` | 模型别名 `qwen-strata` |
| Strata | [Strata Monitor](http://localhost:8080) | 推理引擎及原始监控，需要既有认证 |

登录账号与 API Key 沿用既有配置，保存在 WSL 运行目录的 `.env`，不随仓库分发。

## 看板内容

| 分区 | 已接入的指标 |
| --- | --- |
| 实时总览 | Decode / Prefill、GPU、显存、最近 TTFT / TPOT、运行与排队请求 |
| 延迟 | 网关 TTFT、TPOT、首段可见答案、端到端延迟、引擎 TTFT / ITL、LiteLLM API 延迟与额外开销 |
| Token 与质量 | 输入 / 输出速率、累计 Token、缓存读取 / 推理 Token、QPS、并发、错误率、工具提议数 |
| 缓存与上下文 | 前缀命中率、专家缓存、会话缓存、MTP 接受率、上下文占用、槽位、驻留 KV 位置 |
| 硬件 | 显存容量、GPU 温度与功耗、PCIe、CPU、RAM、磁盘吞吐 |
| 数据质量 | 抓取健康、JSON 数据年龄、Langfuse 上报错误、价格配置状态、其他引擎 KV 指标 |

指标参考 [vLLM](https://github.com/vllm-project/vllm)、[SGLang](https://github.com/sgl-project/sglang)、[NVIDIA dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter)，并以本机 Strata Monitor 的实际实现和采样数据校正。详见 [指标定义与调查结果](docs/observability-findings.md)。

TPOT 使用成功流式请求的 `(最后输出时间 - 首次输出时间) / (completion_tokens - 1)`。SSE 帧数不等于 Token 数；缺少 usage、仅一帧或失败时会排除。Strata 的引擎 ITL 是按 Token 间隔数加权的均值估计，Strata 上下文占用也不能当作物理 KV 显存利用率。

## Langfuse 改进

旧环境的 OTEL V2 默认不采集消息内容；客户端工具执行也没有埋点及父 Span 传播。本项目回调现在显式上报完整 generation 数据，并保留 `traceparent`、Session、用户、推理与缓存用量。客户端通过 SDK 创建真实 AGENT / TOOL 节点，网关生成记录自动关联到同一条链路。

可运行的示例会调用真实模型，执行 `add_numbers(17, 25)`，再把结果交回模型得到 `42`：

```powershell
wsl -d Ubuntu-24.04 -- /home/YOUR_USER/llm-observability/.venv/bin/python /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/examples/traced_tool_agent.py --env-file /home/YOUR_USER/litellm-langfuse-monitoring/.env
```

回调不会把模型提出的 `tool_calls` 伪装成已经执行的工具。实际客户端也需要按 [接入说明](docs/windows-wsl.md#客户端接入) 创建工具 Span；既有历史记录缺失的输入输出无法从当前网关补回。

## 部署与检查

在这台已存在组件的机器上，先预览发现的运行目录，再更新：

```powershell
wsl -d Ubuntu-24.04 -- env LITELLM_LOCAL_MODEL_COST_MAP=True /home/YOUR_USER/llm-observability/.venv/bin/python /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/deploy_wsl.py --dry-run
wsl -d Ubuntu-24.04 -- env LITELLM_LOCAL_MODEL_COST_MAP=True /home/YOUR_USER/llm-observability/.venv/bin/python /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/deploy_wsl.py
wsl -d Ubuntu-24.04 -- bash /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/check.sh
```

部署脚本保留既有模型、密钥和其他回调，在改动前生成备份清单，只重启网关及新增导出器，并热加载 Prometheus。运行目录、依赖、启动、验收和回滚步骤见 [Windows WSL 部署说明](docs/windows-wsl.md)。

```text
litellm/observability_callback.py       网关时延、usage、OTLP 回调
exporters/strata_extra.py              Strata Monitor JSON 补充指标
prometheus/recording-rules.yml         指标归一化
prometheus/alerts.yml                  9 条告警规则
ops/build_dashboard.py                生成 51 面板 Grafana JSON
ops/deploy_wsl.py                      发现并更新现有 WSL 部署
ops/verify_runtime.py                  在线抓取 / PromQL / 真实链路验收
examples/traced_tool_agent.py          真实客户端工具链路
tests/test_observability.py            流式语义、父子关联、异常和采样测试
```

当前网关回调按单 worker 验收；多 worker 的 Prometheus 聚合需要额外配置。vLLM / SGLang / DCGM 为预留兼容接入，本机没有部署这些目标。仓库中的 [离线多 VM 基线](docs/offline-deployment-baseline.md) 保留作参考，全新 Compose 部署没有在本次复用环境中验收。
