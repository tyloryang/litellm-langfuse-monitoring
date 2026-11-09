# 自定义改动与迁移适配清单

更新日期：2026-11-09。本页用于迁移本项目的观测能力，覆盖需要随官方组件一起部署的自定义文件、配置、数据及客户端改动。系统安装与离线物料按[麒麟离线手册](offline-deployment-baseline.md)执行，当前 WSL 具体路径按 [WSL 说明](windows-wsl.md)核对。

## 1. 先确定迁移范围与版本

Langfuse 的 Trace / Metadata / 筛选能力来自原生服务；来源校验、usage 归一化、流式计时、SQLite 累计和 OTLP generation 上报由 `litellm/observability_callback.py` 实现。真实工具执行节点来自业务客户端 SDK。迁移这些功能必须同时包含对应代码和配置。

| 组件 / 范围 | 已验证 WSL 基线 | 目标环境需要确认 |
| --- | --- | --- |
| 宿主解释器 | Python 3.12、既有 venv | 目标平台 / libc 的独立解释器与完整依赖；不用 Ubuntu venv 替代麒麟发行物 |
| LiteLLM | 1.106.6 | CustomLogger hook、`requester_ip_address`、响应 usage、流式终止回调与真实 loader 行为 |
| Langfuse 服务 | 4.73.0 OSS | OTLP v4 写入、`/api/public/v2/observations`、时间字段选择与 Metadata 展示 |
| Langfuse Python SDK | 4.18.0 | `propagate_attributes`、AGENT / TOOL API、当前父节点传播；SDK 与服务版本分别锁定 |
| Prometheus / Grafana | 3.15.1 / 13.3.0 | 规则 / PromQL、内置面板及 table transformations 与 provisioning 可用 |
| DDN 新装候选 | Web / Worker 4.63.0 等，见独立清单 | 完整组合尚需实测；不能用候选覆盖 WSL 4.73.0 数据做降级 |

`requirements-observability.txt` 记录宿主、示例和验证工具的直接依赖；`requirements-callback.txt` 用于检查 / 补齐 LiteLLM 容器内回调依赖。镜像不会继承宿主 venv，挂载 Python 文件也不会安装依赖。离线准备需解析完整传递依赖，缺依赖时在准备阶段构建最终镜像并重新锁定。

## 2. 网关自定义回调与配置

需要迁移 `litellm/observability_callback.py` 和加载它的配置。Compose 模板将文件挂载为 `/app/observability_callback.py`，由 `observability_callback.observability` 加载；宿主部署放在实际 YAML 可定位的目录。加载行为用 LiteLLM 的 `get_instance_fn` 验证，普通 `import` 成功不等于真实加载路径正确。无网镜像验证命令见[离线手册 4.2](offline-deployment-baseline.md#42-选版本验证镜像导出)。

必须核对：

| 配置 | 项目要求 | 适配点 |
| --- | --- | --- |
| `litellm_settings.callbacks` | `prometheus` + `observability_callback.observability` | 移除本项目替代的 `langfuse` / `langfuse_otel` generation 回调，检查其他回调是否重复上报 |
| `LITELLM_OTEL_V2` | `false` | 内容由本项目回调显式上报；不要同时启用旧全局 V2 路径 |
| `LITELLM_LOCAL_MODEL_COST_MAP` | `True` | 使用内置价格表；本地模型实际成本仍需单独设置 |
| `LANGFUSE_HOST` / `LANGFUSE_BASE_URL` | 网关实际可达的 Langfuse 地址 | 同网 Compose 可用 `http://langfuse-web:3000`；宿主 / 外部客户端使用其可达入口。两项均设置时本回调优先 HOST |
| 项目 public / secret key | 与客户端同一 Langfuse 项目 | 原值私下迁移，不进源码或公开日志；新项目需重新取得 Key |
| `LANGFUSE_TRACING_ENVIRONMENT` | 明确填写目标环境 | 本机未设置时回调默认 `wsl-local`；模板为 `production`，需现场确定 |
| `GATEWAY_INPUT_COST_PER_TOKEN` / `GATEWAY_OUTPUT_COST_PER_TOKEN` | 可选真实 USD / Token 价格 | 缺失时为 unconfigured；LiteLLM spend 的价格配置另行核对 |
| 模型路由 / API Key | 指向实际模型 ID 与 `/v1` 地址 | `qwen-strata` 是本机网关别名，不能当作所有目标环境的真实上游 ID |

本回调发送 `x-langfuse-ingestion-version: 4`，写入 `/api/public/otel/v1/traces`；验证脚本查询 v2 observations 并显式选择 `time,metrics`。切到其他 Langfuse 主版本、LiteLLM 或 SDK 时，重新核对接口，不只改镜像 tag。原生列表没有自动生成的独立 IP 列；本机 4.73.0 已验证 Metadata 字段与筛选。

回调显式采集输入输出；账本仅存累计数据，但 Langfuse 会保存请求内容。目标环境需要按实际业务要求确认采集字段、脱敏位置、保留周期和访问权限。回调只上报白名单业务 metadata，不会把完整请求头或内部凭证字典直接导出；这不代替对业务正文的字段脱敏。

## 3. 来源 IP、代理与 SQLite 账本

### 3.1 代理配置按现场重新发现

保留 `general_settings.use_x_forwarded_for: false` 和空 `FORWARDED_ALLOW_IPS`，让回调先判断真实连接 peer 是否可信，再解析转发头。`GATEWAY_TRUSTED_PROXY_CIDRS` 使用实际代理 `/32`、IPv6 `/128`，或仅代理使用的受控网段；不能信任整个业务网络。

仓库 Nginx 已用 `$remote_addr` 覆盖 `X-Forwarded-For` / `X-Real-IP`。新增负载均衡或多级代理时，每一级都要按信任边界适配；不能简单把客户端自带的链完整透传。Nginx 重建换 IP 后重新发现并更新网关信任配置；WSL 脚本依赖 Nginx 与 Prometheus 的共享 Docker 网络，目标拓扑不同就调整发现逻辑。

NAT 丢失的客户端地址无法在网关还原，`source_ip` 也不等同于业务用户身份。多个用户共享 Agent 服务时可见来源可能相同，需配合 Session / User；客户端自报的 `metadata.source_ip` 不作为用量归属。

### 3.2 迁移累计周期与数据

| 项目 | 迁移要求 |
| --- | --- |
| `GATEWAY_SOURCE_USAGE_DB` | 指向持久化 SQLite；未配置时使用内存，重启不保留累计 |
| Compose 数据目录 | `./data/source-usage:/var/lib/llm-observability`，数据库为该目录的 `usage.sqlite3` |
| WSL 实际路径 | `/home/YOUR_USER/litellm-langfuse-monitoring/data/source-usage/usage.sqlite3`；迁到其他用户 / 前缀时同步改环境变量 |
| UID / GID、SELinux | 目录允许运行用户读写 / 遍历及创建 WAL / SHM，按目标策略设置标签；不使用 `chmod 777` |
| 在线备份 / 恢复 | 活跃 WAL 模式使用 SQLite backup API；目标停网关后在空的专用目录恢复，避免旧 WAL 混入，核对起点 / 累计再启动 |
| 规模与模型维度 | 默认最多 1024 个 IP / 模型组合，超限归 overflow；模型重命名会出现新累计键，历史不会自动重分类 |
| worker / 多实例 | 当前只按单 worker 验收；增加 worker 需适配本地状态、Prometheus 多进程指标与账本聚合，不能抓取同一累计账本后简单相加 |

只有到终止回调、并含有效 usage 的请求才增加 Token；usage 缺失单独计数。历史未采集来源的用量无法补回。SQLite 累计值与 Prometheus 历史时间序列是两份数据，迁移账本不会自动补齐旧监控曲线；需要保留历史曲线时还需按 Prometheus 自身备份 / 恢复流程迁移数据。

完整字段、Token 分项对账和 SQLite 备份示例见[来源用量说明](source-ip-usage.md)。

## 4. 业务客户端的工具链适配

`examples/traced_tool_agent.py` 证明完整链路可工作，不会自动给其他 Agent、IDE 或工具执行器增加埋点。业务端应在实际执行位置创建 AGENT / TOOL，记录参数、结果、异常和耗时，并把当前父节点通过 `traceparent` 传给网关。代理需保留该头，网关和客户端使用同一 Langfuse 项目。

```text
业务 AGENT
  ├── 网关 GENERATION：模型提出 tool_calls
  ├── 业务 TOOL：实际执行并记录结果
  └── 网关 GENERATION：模型处理工具结果
```

Session 用于会话分组，不代替 Trace / 父节点；模型提议工具调用也不证明已经执行。跨服务的多次 generation 来源可以不同，应以各 generation 的 `source_ip` 为准。TOOL 节点如需来源字段，应在其执行位置定义和采集对应来源。

迁移示例时核对 `GATEWAY_URL`、`GATEWAY_MODEL`、`LANGFUSE_HOST`、Key 和可信 CA；示例中的 `wsl-local` 环境标签、256 Token 限额与 240 秒请求超时是示例设置，需要按业务调整。真实模型必须支持所需工具调用及 `tool_choice`；模型没有返回工具调用时验收失败，不用模拟输出代替。

## 5. Strata / GPU 与 Grafana 适配

| 位置 | 必须检查的内容 |
| --- | --- |
| `exporters/strata_extra.py` | `FIELDS` 对应的 Monitor JSON schema、模型标签、最近请求结构；字段变更先调整映射与测试 |
| exporter 环境 | `STRATA_METRICS_URL`、`STRATA_API_KEY`、`STRATA_EXPORTER_HOST` / `PORT`、轮询间隔；默认 URL / loopback 不一定能被容器访问 |
| `prometheus/litellm-scrape.yml` | 这是抓取片段；合并主配置，改真实 targets / 认证文件并补容器挂载，删除未部署 jobs |
| `prometheus/recording-rules.yml` | engine / model_name / requested_model / gpu / deployment 标签及单位；vLLM / SGLang / DCGM 当前为预留兼容 |
| `ops/build_dashboard.py` | 按实际指标改查询 / 变量与表格；Strata 专有面板在其他引擎上要替换或明确缺省 |
| `ops/dashboard_formulas.py` | 与面板同时修改公式、单位、窗口、权重和边界，再生成 JSON 与公式文档 |
| Grafana 配置 | 绑定真实 Prometheus 数据源、导入 UID / provisioning 目录，保留已用的原看板 / 数据源与权限 |

Strata 原生抓取显式指定 `format=prometheus`。JSON 导出器按默认 3 秒轮询，失败会清除旧样本并设置 `strata_extra_up=0`；网关 / 引擎延迟的观察边界不同。SSE 帧数不用作 Token；MiB/s 转 B/s 乘 1048576；Strata 上下文占用不当作物理 KV 显存利用率。更换 GPU / NPU 或监控发行版时重新检查实际可用字段、设备标签、驱动与导出器。

修改后在源码目录运行 `python ops/build_dashboard.py`，把看板 JSON、公式文档与生成代码作为同一提交发布。当前为 59 面板 / 94 查询；目标环境有不同指标时同步更新验证矩阵，不能只让空查询返回成功。

## 6. WSL 脚本中需要改代码的位置

`ops/deploy_wsl.py` 是针对已存在 WSL 部署的更新器，不负责麒麟安装或容器换源。迁移前读完整脚本，`--dry-run` 可检查发现结果；它不会证明所有平台假设已适配。

| 项目 | 当前实现 | 迁移方式 |
| --- | --- | --- |
| 运行目录、容器名 | 提供 `--runtime-root`、`--prometheus`、`--grafana`、`--nginx` | 按现场传参，并保证需要的 bind mount / provider 可发现 |
| 共享 venv | 固定 `Path.home() / "llm-observability/.venv/bin/python"` | 路径 / 解释器不同需改脚本，检查真实 LiteLLM CLI |
| 采集布局 | Strata 8080、导出器 9402；使用 Prometheus 网络的 bridge 主机地址 | 改实际监听 / 路由 / 防火墙，跨 VM 用实际内网地址 |
| 服务管理 | 用户级 systemd；`litellm-gateway.service`、`strata-metrics-extra.service` | 麒麟常用系统级服务或 Compose，按目标替换启动方式 / 用户 / ExecStart |
| deployment 标签 | `wsl-rtx3080` | 改实际部署标签，核对 Grafana 变量和查询 |
| Prometheus 读文件组 | 65534；复用缓存镜像执行 chgrp | 核对目标 UID / GID 和工具，保持认证文件权限及 SELinux 标签 |
| LiteLLM 数据库 | runner 清除 `DATABASE_URL` / `DIRECT_URL` / 只读副本地址，并禁止 DEV dotenv 重载 | 此为本机无数据库网关策略；有 LiteLLM 数据库时需要专用配置 / 迁移，不能借用 Langfuse 数据库 |
| 备份 / 恢复 | 覆盖前文件清单、SQLite backup；启动失败仅恢复旧网关 YAML 后重启 | 完整回滚还需按备份清单恢复回调、环境与相关配置，新增服务 / 文件单独处理 |

写单文件 bind mount 配置时脚本原位更新保留 inode；改成原子替换需验证容器是否仍读取旧 inode。执行部署会重启网关和导出器，需先安排业务请求排空；不能当成只读检查执行。`ops/check.sh` 可用 `OBSERVABILITY_PYTHON` 适配解释器，验证脚本可指定 endpoint、`--required-job`、`--critical-panel`、模型和 `--ca-file`。

## 7. 镜像、数据与离线目标

`offline/ddn-image-map.amd64.tsv` 是九项新装候选，`offline/ddn-runtime-wsl.amd64.tsv` 是本机五项实际换源。已有数据迁移保留原 Compose project、网络、卷名、密码、SALT / ENCRYPTION_KEY / NEXTAUTH_SECRET 和项目 Key；先备份并验证副本，镜像按架构、应用版本与 digest 核对。Langfuse Web / Worker 成对选择，数据库升级 / 降级单独验证。

MinIO / mc 的 shell、entrypoint、UID 和桶初始化，LiteLLM 镜像的回调依赖，以及 Langfuse schema 迁移 / OTLP 都必须在所选发行物中验证。对象存储内部 endpoint 与浏览器媒体 endpoint 分开，HTTPS 页面使用可达、可信 CA 且 CORS 正确的 HTTPS 媒体入口；仓库默认 Nginx 没有代理 MinIO，实际方案见[离线手册 5.3](offline-deployment-baseline.md#53-解压配置生成现场密钥与证书)。

麒麟需记录实际 V10 SP / Build、CPU 架构、内核、glibc / GLIBCXX、Docker / Compose、cgroup / 存储和 SELinux。按同产品 / SP / 架构准备完整 RPM、Python wheel / 原生库、镜像、Tokenizer、模型和 CA / 配置物料。ARM 使用独立清单，不能用本机 amd64 下载或镜像能启动证明兼容。

## 8. 迁移顺序与证据

1. 记录源 / 目标版本、拓扑和范围；备份原配置、Key、镜像及一致性数据副本，在隔离环境验证恢复。
2. 先在准备 / 验证环境适配回调依赖与真实 loader、代理信任、账本挂载，再调整引擎指标、业务工具埋点、看板和校验参数。
3. 部署并验证健康、非流式 / 流式真实请求、Langfuse 输入输出 / usage / 时延、完整工具链；让两个真实来源执行已知 usage，对比累计差值并验证伪造来源无效。
4. 检查当前全部面板查询及现场必需数据；重新生成公式并检查单位 / 统计口径。安排维护窗口验证重启后累计 / 起点与历史数据保留、备份恢复。
5. 离线交付在干净同版麒麟机器先阻断公网，再从冷安装完成上述流程，使用 `--pull never --no-build`，归档[验收记录](offline-acceptance-template.md)。

WSL 已验证真实链路与指标；来源账本只完成一致性备份 / 新进程重开副本验证，启用后未为持久化测试再次重启在线网关。完整麒麟物料包、断网冷装、目标重启和恢复仍待执行，不能以 WSL 通过替代。
