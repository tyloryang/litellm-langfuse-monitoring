# 来源 IP 与 Token 用量

## 为什么之前看不到

Nginx 原来已经设置 `X-Real-IP` / `X-Forwarded-For`，但网关回调没有把经过校验的来源写入 Langfuse metadata。既有 LiteLLM Token 指标只有模型等维度，也没有按来源 IP 统计的指标、持久化账本和看板。仅增加转发头不会自动补齐这些数据。

本次增加经过代理信任校验的 `source_ip`、按 IP / 模型累计的 SQLite 账本和 Prometheus 指标，并提供第 07 分区。只统计经过该 LiteLLM 网关的请求；绕过网关直连模型不在此表内。历史请求没有采集来源时，不能从模型 Token 总量准确补回每个 IP 的用量。

## 在哪里查看

- Grafana：[推理观测看板](http://localhost:3002/d/litellm-gpu-overview)，页面底部 **07 · 来源 IP 与 Token 用量**。顶部“来源 IP”和“网关模型”可以筛选此分区；来源筛选不影响其他分区。
- **来源 IP Token 用量 · 所选时段**：所选时间范围内的输入、输出、总 Token 和请求数。
- **来源 IP Token 累计 · 启用以来**：账本启用以来的持久累计值；更改右上角时间范围不重置累计值。
- Langfuse：打开一条修复后产生的 Trace / GENERATION，查看 Metadata 中的 `source_ip`、`network_peer_ip`、`source_ip_origin`。Trace metadata 也会写入来源；有多次网关调用的 Agent 链路应以各 GENERATION 的来源为准。

同时提供每个 IP 的输入 / 输出速率、缓存 / 推理速率、usage 缺失请求，以及账本持久化状态、开始时间和写入错误。正在执行的请求先建立零值序列，完成后才计入 Token；长请求未完成时零值不是用量丢失。

## 按来源 IP 统计是怎样实现的

```text
客户端 → Nginx / 直连 → LiteLLM pre-call：校验来源、绑定 IP / 模型 / 内部请求 ID
                             ↓ 请求到达终止回调
                       读取模型响应中的真实 usage
                             ├─ 去重 → SQLite 累计 → /metrics → Prometheus → Grafana
                             └─ 来源 + usage → OTLP → Langfuse Trace / GENERATION
```

实现位于 [网关回调](../litellm/observability_callback.py)：

1. `source_address()` 从连接 peer 和可信代理头确定来源；具体信任规则见下文。客户端不需要提交 IP。
2. `async_pre_call_hook()` 保存此次请求的来源、请求模型和内部随机 ID，并预留零值序列；此时尚未结算 Token。
3. `_record()` 在请求结束后调用 `normalize_usage()`，读取响应中的 `prompt_tokens` / `completion_tokens`，兼容 `input_tokens` / `output_tokens`。流式请求使用最终汇总响应的 usage，不按 SSE 帧数计算。
4. `SourceUsageCollector.record()` 用内部请求 ID 防止重复结算，在 SQLite 事务内按 `(source_ip, model)` 累加输入、输出、缓存子集、推理子集及请求状态。账本不保存请求正文。
5. `collect()` 把账本导出为 `llm_gateway_source_tokens_total{source_ip,model,token_type}` 等指标；[看板生成器](../ops/build_dashboard.py) 再按 IP 聚合选中的模型。Langfuse 与指标共用同一次回调确认的来源，Grafana 统计不依赖查询 Langfuse 数据库。

例如测试请求的来源是 `127.0.0.2`，响应 usage 为输入 57、输出 28，则该 IP / 模型累计输入增加 57、输出增加 28、总量增加 85。重复终止回调不会再次增加同一笔用量。

## Langfuse 中的来源字段与筛选

**已经写入的是可查询的 Metadata 字段。** 本机 v4.73.0 OSS 已实测 Trace / GENERATION 详情和 Tracing 筛选；其他版本应在对应部署中核对 UI。

| 字段 | 含义 |
| --- | --- |
| `source_ip` | 按可信代理规则确定的来源 IP，也是用量归属 IP |
| `network_peer_ip` | 网关实际连接 peer；经过 Nginx 时通常为 Nginx 地址，无法获取时省略 |
| `source_ip_origin` | `transport_peer` 为直连 peer；`trusted_forwarded_for` / `trusted_real_ip` 为已验证代理头；`unavailable` 为来源不可获取 |

回调发送 `langfuse.observation.metadata`、`langfuse.trace.metadata`，并设置 OTLP `client.address`。测试请求在详情 **Attributes → Metadata** 中显示：

```json
{
  "source_ip": "127.0.0.2",
  "network_peer_ip": "127.0.0.2",
  "source_ip_origin": "transport_peer"
}
```

在 **Tracing → 左侧 Metadata → Add condition**，填写 key `source_ip`、运算符 `equals`、value 为实际 IP；该条件会同步为搜索表达式：

```text
metadata.source_ip:127.0.0.2
```

本机已验证该条件只返回对应测试请求（57 输入 / 28 输出 / 85 总量）。可收藏带筛选条件的地址，或使用 **My Views → Create Custom View** 保存常用条件；本次没有创建保存视图。Columns 中的 **Metadata** 显示整份对象，详情适合逐字段查看。

**Metadata 字段不会自动变成独立列表列。** 当前 v4.73.0 的 Columns 菜单没有自定义 `metadata.source_ip` 列的入口。[该版本 EventsTable 源码](https://github.com/langfuse/langfuse/blob/v4.73.0/web/src/features/events/components/EventsTable.tsx#L1313) 注册的是整份 Metadata 列。若要求列表中单独显示名为“来源 IP”的列，需要扩展 Langfuse 前端的行值读取、列定义、默认顺序和可见性，并重新构建 Web 镜像；仅调整 LiteLLM 或环境变量不会生成这列。本次未修改 Langfuse 前端或数据库 schema。

来源字段属于网关 GENERATION 的请求属性。一个 Agent Trace 可以包含来自不同服务的多次调用，应以各 GENERATION 的 `source_ip` 为准；客户端 SDK 创建的 AGENT / TOOL 节点如也要逐节点显示来源，需要在其实际执行位置单独采集，不能把网关的连接 IP 当成所有工具的执行来源。

### 与 Langfuse Token 展示的对账口径

网关账本使用供应商的包含子集的原始用量；Langfuse 的 usage 明细按互斥分项上报，原始值另存于 `metadata.usage_raw`：

| 网关累计 / 原始 usage | 对应 Langfuse usage 明细 |
| --- | --- |
| 输入 `prompt_tokens` | `input + input_cached_tokens` |
| 输出 `completion_tokens` | `output + output_reasoning_tokens` |
| 总量 | 上述四项之和，即原始输入 + 原始输出 |

例如原始输入 100（缓存 40）、输出 30（推理 10），Grafana 显示输入 100、输出 30、总量 130；Langfuse 明细为 `input=60`、`input_cached_tokens=40`、`output=20`、`output_reasoning_tokens=10`，总量同为 130。供应商未返回的缓存 / 推理细分不能推算；本例仅用于说明分项口径。

## 计算公式与统计口径

令 `C(ip, model, type)` 为供应商返回 usage 经去重后写入的累计 Token。`input` 包含缓存读取，`output` 包含推理 Token；`input_cached`、`output_reasoning` 是子集，不能再次加入总量。

| 展示项 | 计算公式 | 说明 |
| --- | --- | --- |
| 累计输入 | `sum by (source_ip) (C{token_type="input"})` | 汇总筛选的模型，SQLite 持久累计 |
| 累计输出 | `sum by (source_ip) (C{token_type="output"})` | 包含推理 Token |
| 累计总量 | `sum by (source_ip) (C{token_type=~"input\|output"})` | 输入 + 输出，子集不重复相加 |
| 时段用量 | `sum by (source_ip) (increase(C{...}[$__range]))` | Prometheus 根据抓取样本外推，可能出现小数 |
| Token / s | `sum by (source_ip) (rate(C{...}[$__rate_interval]))` | 采样窗口内速率，不是单请求生成速度 |
| 时段请求数 | `sum by (source_ip) (increase(llm_gateway_source_requests_total{...}[$__range]))` | 计入成功及已到终止回调的失败请求 |
| usage 缺失 | `sum by (source_ip) (increase(llm_gateway_source_usage_missing_total{...}[$__range]))` | 缺少完整、非负整数输入 / 输出用量的请求 |

完整实际 PromQL 与看板一致，见 [全部面板公式](grafana-formulas.md)。所选时段表属于监控估算：首次抓取之前完成的请求、时间边界、抓取中断会影响 `increase`；不能把它作为逐请求精确计费账单。累计表从账本启用开始逐次累加返回的真实 usage，重启保留；删除账本或更换挂载则开始新的累计周期。启动时间面板给出这一周期的起点。

失败请求若仍有完整 usage，会记录其用量；无 usage 时增加缺失计数，不用 SSE 帧数猜 Token。不经过 pre-call hook 的 SDK 调用来源为 `unknown`。仅到达终止回调的请求才计数，鉴权拒绝和进程被强制终止前尚未结算的请求不属于完整用量账本。

## 来源 IP 的可信边界

```text
客户端 → Nginx（覆盖外来 XFF / X-Real-IP）→ LiteLLM
         ↑ 实际远端地址                 ↑ 实际连接 peer
```

1. `general_settings.use_x_forwarded_for: false`，`FORWARDED_ALLOW_IPS` 为空，保留真实连接 peer。
2. 回调使用 LiteLLM 根据连接写入的 `requester_ip_address`；忽略客户端提交的 `metadata.source_ip`。
3. 只有 peer 匹配 `GATEWAY_TRUSTED_PROXY_CIDRS` 时才解析转发头，按从右到左剥离可信代理；格式不正确则退回连接 peer。
4. 仓库 Nginx 用 `$remote_addr` 覆盖两个转发头，避免客户端自报 IP。多级代理部署需要在每一级明确配置可信来源与头覆盖规则。

`GATEWAY_TRUSTED_PROXY_CIDRS` 必须填真实代理的 `/32`（IPv6 用 `/128`）或仅代理使用的受控网络，不能填 `0.0.0.0/0`。WSL 部署脚本自动发现现有 Nginx 在共享 Docker 网络中的地址；Nginx 重建换 IP 后应重新部署以更新配置。空配置只记录直连 peer，经过代理的请求会显示代理地址。

NAT / Windows WSL 转发可能把多个客户端折叠成同一个网关地址，观测系统只能记录入口实际可见的地址。需要区分 NAT 后面的业务用户时，应同时使用 `trace_user_id` / Session；单凭服务端已丢失的信息无法还原原始公网或局域网 IP。

## 配置、持久化与离线部署

| 配置 | 用途 |
| --- | --- |
| `GATEWAY_TRUSTED_PROXY_CIDRS` | 允许提供转发来源的代理地址列表，逗号分隔 |
| `GATEWAY_SOURCE_USAGE_DB` | SQLite 路径；未配置时为内存模式 |
| `GATEWAY_SOURCE_MAX_SERIES=1024` | 最多保留 1024 个 IP / 模型组合；超限归入 `source_ip=overflow,model=all` |
| `FORWARDED_ALLOW_IPS=` | 禁止 Uvicorn 提前把未校验的转发头变成连接 peer |

WSL 账本位于 `/home/yys/litellm-langfuse-monitoring/data/source-usage/usage.sqlite3`。Compose 挂载 `./data/source-usage:/var/lib/llm-observability`，容器路径为 `/var/lib/llm-observability/usage.sqlite3`。启动前按实际 LiteLLM 镜像 UID / GID 创建可写目录，设置麒麟 SELinux 标签；SQLite 还需要在同目录创建 WAL / SHM，不能只允许写单个数据库文件。不要使用 `chmod 777`。

账本只保存来源、模型、累计值和短期随机去重 ID，不保存正文或 API Key。它使用 Python 标准库 `sqlite3`，不增加镜像、pip 包或外部数据库依赖；离线 Python 必须包含可用的 sqlite3 模块。默认按单 worker 验收，多实例不能简单抓取同一账本后相加，否则累计值会重复。

备份运行中的账本使用 SQLite backup API，不单独复制活跃 WAL 模式的主文件：

```bash
# 按实际部署路径替换，备份写入独立私有目录。
/opt/llm-observability/.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
source = Path('/opt/llm-gateway/data/source-usage/usage.sqlite3')
target = Path('/私有备份目录/source-usage.sqlite3')
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
target.chmod(0o600)
PY
```

恢复时先停止网关，把现有整个 `source-usage` 目录移入备份位置，在空的专用目录放入一致性备份并恢复 UID / GID / SELinux 标签，然后启动网关；避免旧 WAL 与恢复文件混用。恢复到旧备份会回退到备份时点，Prometheus 对计数器回退的处理不能恢复丢失用量。

离线验收应让两个真实来源各完成已知 usage 的请求，对比累计差值、Langfuse 来源和 Grafana 表格；同时发送伪造 XFF / `metadata.source_ip` 证明无法改写归属。再重启网关，核对累计值与启用时间不变并完成新请求。更完整的现场步骤见 [离线部署手册](offline-deployment-baseline.md) 和 [验收记录](offline-acceptance-template.md)。

## 本机验证记录（2026-11-09）

本机来源账本启用时间为北京时间 21:59:55。59 个指标面板 / 94 条 PromQL 查询执行成功，4 个采集目标 UP；28 项回归测试通过。Grafana 第 07 分区已检查真实行值、全部列和单 IP 筛选，Langfuse Attributes → Metadata 可见相同来源。

| 场景 | 实际结果 |
| --- | --- |
| 直连来源验证 | Trace `18664e53f78ce5097422b9cfb47d2ca5`：连接来源 `127.0.0.2`，输入 57 / 输出 28 / 总量 85；账本与 Langfuse 一致，伪造 XFF / X-Real-IP / metadata 未覆盖来源 |
| Nginx 代理验证 | Trace `87bff24a552c71d36c491c1c6aed6855`：HTTP 200，输入 55 / 输出 26 / 总量 81；`source_ip_origin=trusted_forwarded_for`。从 WSL 本机经 Docker 映射访问时，来源被 NAT 为 bridge 地址，连接 peer 为 Nginx；伪造头未生效 |
| 业务来源 | 局域网实际来源与 NAT bridge 地址已分行累计；报告不公开业务请求正文和客户端用量快照 |
| 持久化 | 在线 SQLite backup API 取得一致性副本，两个独立新进程重开副本，13 条 Token / 起点样本一致；持久化指标=1，写入错误=0 |
| 回归 | 既有真实 AGENT / TOOL / 两次 GENERATION 链路，以及既有流式 usage / TTFT / TPOT 核对通过 |

本次部署完成了一次网关重启以加载来源功能。启用账本后仍有业务并发长请求，因此未为持久化测试再次重启在线网关；上述持久化证据仅证明一致性备份与进程重开，在线网关二次重启及麒麟断网冷启动仍需在维护窗口 / 目标测试机完成。
