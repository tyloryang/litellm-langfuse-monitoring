# 云端 Strata JSON 与日志核对

核查时间：2026-11-16 约 17:38（UTC+8）。本次为只读检查，没有修改配置或重启服务；输出仅保留参数、聚合计数和脱敏路径，不包含密钥或请求正文。数值和日志是当时的快照。

## 1. JSON 和服务状态

- Strata 配置文件能解析为 JSON 对象。核查参数列表未发现重复 flag 或本次审阅范围之外的 flag；这不能代替对所有业务请求参数的逐项测试。
- 当时 `/health` 返回 `loaded=true`、`max_context=262144`；指标报告 KV 为 `k8v4`、`kv_resident=32768`，实际 `live.parallel=4`、四个槽位。模型、pack、两片 GGUF、profile、MTP、tokenizer、引擎和工作目录均存在。
- Strata 容器最近三小时、最多 15,000 条日志的抽样中，未命中 JSON 解析异常、未知参数、上下文超限、OOM 或通用错误关键字。日志关键字检查有覆盖边界，不能证明所有请求均成功。
- Nginx 最近 20,000 条以内的访问日志抽样为 1,906 条，全部能解析为 JSON 对象，8 个预期字段均存在；`status` 为整数、`request_time` 为数字、`upstream_time` 为字符串。它们属于访问日志，并不验证请求体中的 JSON 或所有上游日志。

## 2. 配置中需要注意的地方

| 配置 | 本次核对 | 影响或建议 |
| --- | --- | --- |
| `parallel: 6` 与 `--batch 4` | 生效进程命令含 `--batch 4`；指标为四槽。 | 固定版本服务优先使用显式 batch；当前并非六槽。若目标是四槽，可将 JSON 的 `parallel` 与其统一以减少歧义；若目标是六槽，需先评估资源再安排受控测试。 |
| `--pipeline-windows 2` 与 `--batch 4` | 两者都出现在进程参数。 | 固定版本引擎源码在 batch 槽位模式下关闭 `pipeline-windows`；这不代表其他 batch 分组流水机制都关闭。若保持 batch，可移除无效选项以减少歧义。 |
| `--conversation-cache-mib 0`、`--conversation-cache-slots 64` | 进程中预算为 0。 | 固定版本的独立会话缓存要求预算和槽位均非零，因此这些槽位及最小 token 门槛不会启用该缓存。其他 prompt checkpoint 或槽位前缀复用是独立机制，不能据此认定所有缓存都关闭。 |
| `sampling.experimental_speed_projection: true` | JSON 设置为 true；核查的参数未发现显式控制向量。 | 固定版本只有加载控制向量后才会应用相应请求开关；未取得实际向量加载证据，不能认定此项已经生效或提速。 |
| `--spec 4`、`--mtp /data/mtp/rt` | 参数存在，MTP 路径存在。 | 文件存在和设置 spec 不证明 MTP 加载及接收率；并发 batch 的每槽位 MTP 还涉及独立设置，须以运行日志和实测为准。 |
| `--resident-experts` | 核查的 JSON 参数及进程参数中未出现该开关。 | 不应将其描述为已经启用；这与 GPU expert cache 或操作系统缓存不是同一概念。 |
| `fit_max_tokens: true` | JSON 中存在。 | 固定版本按引擎实际窗口减去 8 个预留 token 和 prompt token 计算输出空间，必要时缩短输出上限；它不扩大窗口或截断超长输入。 |

固定版本源码：[服务并发和输出预算](https://github.com/Niko1221/Strata/blob/477ac13cbe8e582c20484871ce459d20d82df09d/serve/server.py)、[引擎参数与 batch](https://github.com/Niko1221/Strata/blob/477ac13cbe8e582c20484871ce459d20d82df09d/src/program/generate.cpp)。`--prefill-pipe` 与 `--pipeline-windows` 是不同机制，不能因为后者在 batch 下关闭，就断定前者也无效。

## 3. 历史请求错误

LiteLLM 最近三小时日志中有 `BadRequestError` 和 HTTP 400 相关记录；对错误行及相邻行的脱敏分类命中了上下文上限和 `max_tokens` 字样。Nginx 所检查的访问日志里有 18 次 HTTP 400，其中 6 次命中 `/v1/chat/completions`，最后一次发生在 16:19:55（UTC+8）。分类窗口可能重复计数，不能将关键字命中次数直接当作独立请求数，也不能仅凭关键字断言 18 次全由同一原因造成。此前已定位过旧 32K 窗口和过大输出预算导致的失败；当前窗口变更后，仍须按实际请求的输入 token、输出预算和请求时间确认单次失败原因。

Langfuse Web 有历史 404、Grafana 有少量错误级别日志；本次未发现 JSON 解析关键字，也未逐条证明它们的根因。没有因为这些历史记录而修改正在使用的服务。

## 4. 现场复查和变更边界

以不包含密钥的方式核对当前健康状态、实际指标与请求后最新日志；不要打印原始私有 JSON、完整容器环境、请求正文或令牌。修改并发、缓存、KV 或流水参数会影响内存、吞吐或单请求延迟，应先备份、确认业务空闲并做同一工作负载下的前后对比。本次仅提出检查结论，未改变用户的运行配置。
