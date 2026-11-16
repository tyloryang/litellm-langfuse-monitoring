# 2026-11-16 离线候选包：下载、适配与验收

本候选包适用于准备 **linux/amd64 的 Nginx、LiteLLM、Langfuse 及其存储依赖**。先准备并核验镜像、Docker / Compose 和目标机依赖，再进行断网部署。它没有包含 Strata 镜像、模型权重、Prometheus、Grafana、现场数据或密码；麒麟 V10 的完整 RPM 依赖闭包和干净机器断网冷安装还没有完成。

从 [GitHub Release](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-candidate-amd64-20261116) 下载下列全部文件到同一目录。四个压缩包分卷按编号顺序合并；`SHA256SUMS` 和 `manifest.json` 用于核验。

| 下载文件 | 内容 |
| --- | --- |
| `nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz.part01` 至 `part04` | 同一个 gzip 压缩包的四段，必须全部下载 |
| `SHA256SUMS` | 四个分卷、合并后的 gzip 和解压后的 tar 的 SHA-256 |
| `manifest.json` | 各分卷大小、SHA-256、gzip 和原始 tar 信息 |
| `source-and-docs.tar.gz` | 本次发布时的仓库源码与最新迁移手册，包含登录页截图和自动路由、Grafana 说明；不同于主包内的较早源码快照 |

在具备 `sha256sum` 的 Linux 准备机上恢复并核验：

```bash
cd /path/to/downloads
# 完整下载四段后，先核对每一段。
grep '\.part0[1-4]$' SHA256SUMS | sha256sum -c -
cat nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz.part01 \
    nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz.part02 \
    nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz.part03 \
    nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz.part04 \
    > nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz
grep 'candidate-20261116-v3.tar.gz$' SHA256SUMS | sha256sum -c -
gzip -dc nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz > nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
grep 'candidate-20261116-v3.tar$' SHA256SUMS | sha256sum -c -
tar -xf nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
cd nginx-litellm-langfuse
sha256sum -c SHA256SUMS
```

合并后的 gzip 大小为 **1,745,394,303 字节**，SHA-256 为 `18d0c72ba47b918951927e5aae37b59f1a6aa39e31121c7ca6a1c5dd4ddaaf3d`；原始 tar 大小为 **5,714,677,760 字节**，SHA-256 为 `627e4b44ba9fcf4da8a7f9f3d1227348815ca021d153c978236758ced206b702`。下载和合并期间需要同时存放分卷、合并文件和解压结果；还要预留 Docker 镜像层、持久化数据和备份空间。

## 包含的文件与前置依赖

| 物料 | 当前情况 |
| --- | --- |
| 九个镜像 | Nginx 1.28-alpine、LiteLLM 1.106.6、Langfuse Web / Worker 4.63.0、PostgreSQL 17.11、ClickHouse 26.3.21.7、Redis 7.4.11、MinIO 与 mc；位于 `images/images.tar`，名称及 ID 见 `images.env` / `images.lock.json` |
| 配置与源码 | `source.tar` 是提交 `9aeb9ce` 的快照；`config/docker-compose.yml` 另含单节点 ClickHouse 修正，由 `config/prepare.sh` 覆盖到新部署目录 |
| 容器工具 | `host/compose/docker-compose` 为 Compose v2.29.7，附许可证；`host/rpms-centos7/` 有 Docker CE / CLI 20.10.17、containerd.io 1.6.7 的 CentOS 7 x86_64 候选 RPM、依赖清单和 Docker GPG 公钥 |
| 文档 | 包含封包时的云端记录和登录页截图。后续勘误、自动路由和 Grafana 指导以本页及仓库最新文档为准 |
| 未包含 | 麒麟 V10 特定 SP / Build 的 RPM 全依赖集、系统 Python 3、目标服务证书 / 密钥、模型权重、Strata 镜像、Prometheus / Grafana 镜像及其历史数据 |

`host/rpms-centos7` 是候选，不保证适配麒麟 V10。先在与目标相同的麒麟 V10 SP / Build、CPU 架构、内核、glibc、cgroup 和 SELinux 条件下，核对 Docker、containerd、runc、网络与存储驱动的兼容性，并补齐离线 RPM 及其传递依赖；执行包签名与 SHA 校验。准备脚本调用宿主 `bash`、`python3`、`openssl`、`tar` 和 `sha256sum`，这些也必须在目标机已有或预先离线准备。全部应用使用容器时，不应把宿主 wheelhouse 当作应用容器依赖；若还要运行 Strata 补充导出器或仓库宿主 Python 脚本，则还要准备匹配 Python / libc / 架构的完整 wheelhouse，并在空环境离线安装验证。

## 从候选包准备容器服务

下列是基于候选包的部署流程；目标机必须先具备经本机验证的 Docker 和 Python 3。目标目录必须不存在或为空，先备份计划复用的配置和数据。`HOST_IP`、公开 URL、模型端点、可信代理 CIDR 和 TLS 证书都应改为现场实际值。

```bash
# 在解压后的 nginx-litellm-langfuse 目录中，先核验。
sha256sum -c SHA256SUMS
docker load -i images/images.tar
host/compose/docker-compose version

# 使用目标机实际地址；以下地址仅为示例。
export HOST_IP=127.0.0.1
export PUBLIC_LANGFUSE_URL=https://127.0.0.1:8444
export PUBLIC_MEDIA_URL=http://127.0.0.1:9090
bash config/prepare.sh /opt/llm-gateway
```

准备脚本生成 `/opt/llm-gateway/.env`、自签证书、持久化目录、`offline-override.yml` 和初始化账号；不要将生成的 `.env`、私钥或数据卷放入 GitHub。编辑 `/opt/llm-gateway/litellm/config.yaml`、`.env` 和 Nginx 配置，设置实际模型 API 地址 / ID / Key、来源 IP 信任边界、外部 URL 和证书；包中的示例上游地址不能直接代表现场服务。该包默认沿用仓库原端口，并未打入云端 8800 / 8801 / 8804 映射。

```bash
cd /opt/llm-gateway
COMPOSE=/path/to/nginx-litellm-langfuse/host/compose/docker-compose
"$COMPOSE" -p llm-gateway -f docker-compose.yml -f offline-override.yml config --quiet
"$COMPOSE" -p llm-gateway -f docker-compose.yml -f offline-override.yml up -d --pull never --no-build
"$COMPOSE" -p llm-gateway -f docker-compose.yml -f offline-override.yml ps
```

`CLICKHOUSE_CLUSTER_ENABLED=false` 仅针对候选包内单节点 ClickHouse；云端首次迁移时因缺少该设置而报 ZooKeeper 配置错误，增加后 Web 和 Worker 得以启动。复用已有 ClickHouse 集群时应按实际拓扑重新设置，不要直接覆盖已有集群的配置或数据。

## 自动路由：接入前必须适配

[token_router_config.yaml](../litellm/token_router_config.yaml) 是 LiteLLM 1.106.6 的 **mock 试验配置**，不是已经完成现场部署的生产配置。`qwen-auto` 使用 `auto_router/complexity_router` 和项目的 [context_tier_classifier.py](../litellm/context_tier_classifier.py)，映射到 `qwen-32k`、`qwen-64k`、`qwen-128k`、`qwen-256k`。示例中的 `mock_response`、`local-trial`、关闭指标认证和允许无 master key 的选项都必须在实际接入前替换或移除。

当前 classifier 对消息使用 `gpt-4o-mini` 的 LiteLLM tokenizer，失败时回退到字符数估算，并固定预留 1024 输出 token；它不是 Qwen tokenizer，也不是按每次请求的 `max_tokens` 精确预留。以输入估计加 1024 选择首个能容纳的 32768 / 65536 / 131072 / 262144 档；超限则插件返回最高档，由 LiteLLM 的上下文检查 / escalation 和后端实际限制处理。多模态、工具参数、请求输出上限和实际模型 context window 应在迁移时针对业务模型校准，不能只按配置中的档位宣称不会溢出。

容器化接入时，需要一起部署 `token_router_config.yaml`、`context_tier_classifier.py`、`token_router.py`、`routing_decision_log.py` 和 `observability_callback.py`，并确认 LiteLLM loader 可导入这些文件。`routing_decision_log.py` 当前日志目录硬编码为本机 WSL 路径，需改成目标容器可写的挂载路径；该 JSONL 记录内容预览和摘要，应按现场数据策略决定是否启用。四个模型的上游地址、可用上下文和鉴权必须替换为真实后端；候选包未包含四个真实模型。

启发式分数 `Σ(维度分 × 权重)` 及其信号只作为解释信息，当前配置中选模由上下文档位决定。Langfuse 可查看 `routed_model`、`context_tier`、`heuristic_tier`、`heuristic_score`、`weight_details`，是否可筛选取决于目标 Langfuse 版本和字段索引；不要把启发式分数描述成请求分流权重。迁移验收要用短请求、各边界附近请求、超过最大窗口的请求分别核对实际上游模型、usage 和 Trace；不能用 mock 结果替代真实路由验收。

## 复用 Prometheus / Grafana

候选包的 `source.tar` 含 [Grafana JSON](../grafana/dashboards/litellm-gpu-overview.json)、[查询和公式说明](grafana-formulas.md)、[抓取配置样例](../prometheus/litellm-scrape.yml)、recording rule 和告警，但不含 Prometheus / Grafana 镜像。将新增 job / rule 合并到现有配置，按实际地址、认证文件和 Nginx 白名单设置；用**目标版本** `promtool check config` 和 `promtool check rules` 检查后再 reload，保留原数据与配置备份。选择现有 Prometheus 数据源导入 JSON；来源 IP 筛选作用于第 07 区，自动路由分区使用路由模型与上下文档位筛选。

已有 WSL 试验使用 Prometheus 2.45.1 和 Grafana 10.1.5：目标版本 `promtool` 通过配置及规则检查，Grafana API 返回含 68 个指标面板的看板，相关目标可抓取。历史检查还出现过乱序样本和规则同时间戳冲突；不能据此断言所有面板渲染和目标环境兼容已完成。应在实际环境执行全部 105 条 PromQL、检查 recording rule 健康与目标状态，人工检查来源 IP 表、GPU / TTFT / TPOT 和第 08 区曲线及表格，并记录数据样本与公式。没有启用 Auto Router 或没有流量时，第 08 区无样本属于待确认状态，不应填造数据。

主要路由指标为 `llm_gateway_auto_routes_total`、`llm_gateway_heuristic_score_bucket`、`llm_gateway_route_signal_firings_total`、`llm_gateway_route_signal_contribution` 和 `llm_gateway_route_signal_weight`。标签不包含提示词原文，但当前允许的模型 / 档位标签是代码内有限枚举，新增模型需同步适配。指标为进程内数据，进程重启和多 worker 场景需要额外处理；分数及贡献的实际统计口径、权重公式、PromQL 与限制见 [Grafana 公式](grafana-formulas.md)。

## 验收记录与未完成项

| 项目 | 已有证据 | 当前限制 |
| --- | --- | --- |
| 包文件 | 云端及 Windows 本地 v3 归档 SHA-256 一致；包内 `sha256sum -c` 通过；9 镜像在云机执行过 `docker load` | 分卷下载后应重新核对每卷和整包；目标机需重新导入 |
| 容器启动 | CentOS 7.9 / Docker 20.10.17、Compose 2.29.7 的云机上启动网关、Langfuse 与存储容器 | 不是麒麟 V10 目标冷启动验收 |
| 隔离请求 | 同一云机独立 Docker `internal=true` 网络、`--pull never --no-build`，mock 请求 HTTP 200，ClickHouse `events_full` 出现 `chat offline-mock` | mock 上游不证明真实模型、Token、工具链或吞吐；Docker 20 的 internal 网络测试通过容器内部 IP 访问 |
| 实际推理 | Strata 0.1.45 CUDA 12 镜像编译完成，容器内识别两张 A100 | 截至本记录模型仍在下载；真实加载、流式 / 非流式响应和对应 Trace 待验收 |
| 浏览器 | 已有同一 Langfuse 容器的登录页面截图 | 截图未证明已登录后的 Trace 页面和工具链展示 |
| 麒麟 V10 | 已列出需要按 SP / Build 准备的系统物料和验收步骤 | 尚无目标机完整依赖、断网冷安装、重启、持久化或恢复验收记录 |

正式交付前，按 [麒麟离线部署基线](offline-deployment-baseline.md) 和 [验收模板](offline-acceptance-template.md) 在目标同版本干净机器上阻断互联网，完成安装、镜像导入、服务健康、真实流式 / 非流式推理、Langfuse Trace / 工具链、来源 IP、监控和路由验证、重启及备份恢复。将每项命令、实际结果、失败原因和证据留档；云端 mock 演练不能替代上述目标机验收。
