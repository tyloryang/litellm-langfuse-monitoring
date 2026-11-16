# 云端双 A100 真实推理与 Langfuse 验收记录

记录时间：2026-11-16 12:45（UTC+8）。本次在已有 CentOS 7.9 云服务器上验证 Nginx → LiteLLM → Strata → Langfuse；与[同机隔离网络的 mock 演练](cloud-container-deployment-20261116.md)是两次不同的验证。**这不是麒麟 V10 干净机器的断网安装验收。**

## 环境和模型

| 项目 | 实际结果 |
| --- | --- |
| GPU / 驱动 | 2 × NVIDIA A100-SXM4-40GB，驱动 550.78；运行时日志显示 GPU 0、1 自动分层，检查时显存占用分别约 26,319 / 32,391 MiB |
| Strata | 容器镜像 `local/strata:0.1.45-cuda12`，引擎使用 CUDA 12，配置上下文 32,768；模型健康接口返回 `loaded: true` |
| 模型 | `Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS`，经 LiteLLM 别名 `qwen-strata` 访问 |
| 两片 GGUF | 第一片 47,039,860,096 字节，SHA-256 `219ea929900dfa9ef091f3aa473fdba6874b65fcb36526d7d851ac9e95856d15`；第二片 28,800,138,432 字节，SHA-256 `316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113`。两片下载后均通过完整校验 |
| MTP | 主模型之外的 31 个 MTP 张量经固定版本 SHA-256 校验，使用 Strata 镜像内的工具生成运行文件；此前未完成的目录保留为备份 |
| 服务 | Nginx、LiteLLM、Langfuse Web / Worker、存储依赖及 Strata 均以容器运行。Langfuse Web 版本为 4.63.0 OSS |

首次启动失败的原因是安装器默认按 CUDA 13 检查，要求更高版本的驱动；当前镜像已经构建 CUDA 12 引擎。现场在 `/opt/llm-observability-deploy/src/monitoring/docker-compose.cloud.yml` 的 `strata.environment` 增加 `STRATA_CUDA: "12"`，备份原配置为 `docker-compose.cloud.yml.before-cuda12`，执行 Compose 配置检查后只重建 Strata。之后日志显示使用 `/opt/strata/engine-cuda12/strata`，双卡加载完成。迁移时应结合目标驱动、镜像和 GPU 重新核对，不能把本次配置当作其他驱动或架构的兼容性证明。

## 两次真实请求

从云主机通过 Nginx 宿主端口 8800 的 HTTPS 入口请求 `qwen-strata`，输入均为 `17 + 25`。请求使用独立 W3C `traceparent`；自签证书仅在本次主机回环测试中跳过验证。时间、耗时和缓存状态属于单次冒烟记录，不是性能基准。

| 验证 | HTTP / 回答 | API 返回 usage | 关联 Trace ID |
| --- | --- | --- | --- |
| 非流式 | 200 / `42` | 输入 68、输出 32、总计 100 | `7e90ccbb3b8eb25e5be914f946bf3ce2` |
| 流式 | 200 / `42`；34 个 SSE 数据帧，收到 `[DONE]` | 输入 68、输出 34、总计 102；其中缓存输入 63、推理输出 24 | `f0933133da789ca02355dd60f053db72` |

两条 Trace ID 均在 Langfuse ClickHouse `events_full` 中找到对应的 `GENERATION`，模型名为 `qwen3.8-flash-next-iq3_xxs`。经 Langfuse Web 的 `/api/public/v2/observations?traceId=...` 查询，两条均返回 HTTP 200 和一条对应 observation；浏览器 Tracing 页面也显示两条记录，流式详情显示输入、答案 `42`、102 tokens 和约 0.20 秒 TTFT。流式用量在事件中拆分为非缓存输入 5、缓存输入 63、可见输出 10、推理输出 24，总数仍为 102，不能将这些分项再与原始总量重复相加。

当前模型未配置价格，页面显示无费用；这不是模型免费或成本为零的结论。本次请求从云主机回环进入网关，Trace 中的来源地址是容器网络地址，**没有验收公网客户端真实来源 IP**。本次未验收长上下文、并发负载或四档自动路由。

## 真实工具链验证

随后在 LiteLLM 容器内运行仓库 `examples/traced_tool_agent.py` 的云端适配副本，模型请求仍经 Nginx 进入网关；测试端对该容器网络内的自签证书跳过验证。真实模型提出 `add_numbers` 调用，客户端实际执行 `17 + 25` 得到 `42`，第二次模型调用回答 `42`。这是合成的单次工具链验证，不代表业务侧已经完成埋点。

Langfuse Observations v2 按 Trace ID `93cdfbb9317bfcb2dd977e91b4a2f685` 返回四个节点：一个 `AGENT`（`c66ef8a30df6efb6`）、两个 `GENERATION` 和一个 `TOOL`（`add_numbers`）；两个 Generation 与 Tool 的 `parentObservationId` 均为该 Agent ID。应用侧负责实际工具执行及 Agent / Tool 节点，网关回调负责 Generation；只收到模型的 `tool_calls` 不能证明工具已经执行。现场脱敏输出位于云机 `/opt/llm-observability-deploy/logs/real-tool-validation-20261116.log`。

## Trace API 版本差异

本环境的 Langfuse v4 使用 `events_only`。现场访问旧的 `/api/public/traces/{id}` 返回 404，而 Observations v2 按 `traceId` 查询返回 200。旧接口 404 不能单独作为数据丢失的依据；先检查 v2 接口、界面和 `events_full`。Langfuse 的 [v4 兼容性说明](https://langfuse.com/docs/compatibility)也将旧 Trace 查询的迁移路径列为 Observations API v2。

## 现场复查命令

以下命令针对本次云机容器名和目录，其他环境需替换。健康检查中的 `loaded` 必须为 `true`；容器存在或 HTTP 200 本身不足以证明模型完成加载。

```bash
docker ps --filter name=llm-a100-strata --format 'table {{.Names}}\t{{.Status}}'
docker exec llm-a100-strata-1 curl -fsS http://127.0.0.1:8080/health
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv
docker logs --tail 50 llm-a100-strata-1
```

仓库中的 [验证脚本](../ops/verify_cloud_chain.py) 与本次实际使用的脚本内容一致，仅适用于本次容器名、回环端口和模型别名。它在本机从 LiteLLM 容器环境读取网关密钥，不打印密钥；会生成两次真实测试请求，并按 Trace ID 查询 ClickHouse。运行前应确认这些固定值与现场一致，且允许在 Langfuse 写入测试数据。

```bash
# 在已部署该脚本的云主机上；会产生两条真实推理记录。
python3 /opt/llm-observability-deploy/verify_cloud_chain.py
```

现场原始脱敏验证输出位于云机 `/opt/llm-observability-deploy/logs/real-chain-validation-20261116.log`。该文件是本次云机证据，不随离线候选包交付。离线镜像包、下载方法、Grafana / 自动路由迁移说明及麒麟 V10 待验收项见[离线候选包说明](offline-release-20261116.md)。
