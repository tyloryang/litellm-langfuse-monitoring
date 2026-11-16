# 云端 Strata 262144 上下文与参数调整记录

记录日期：2026-11-16（UTC+8）。部署方报告：Strata 已加载并报告 `max_context=262144`；经正式 CPA 的合成检索测试最高达到 **261840 输入 Token**，正确找回开头标记。原 Claude 完整载荷和四路短请求冒烟均返回成功。驻留候选包生成触发磁盘保留保护，候选目录已清理，**resident experts 未启用**；完整 262144 输入、冷缓存性能及稳定性尚未验收。本文根据部署方信息和本机公开源码整理，未独立连接云机，也未读取私有配置或密钥。

## 1. 背景与起点

用户要求在既有云端推理服务上启用 262144 上下文并调整优化参数，保持 Windows CPA → 云端 Nginx → LiteLLM → Strata → Langfuse 的调用链；Grafana 从 Prometheus 指标采集链路读取观测数据。既有接入与历史短请求见 [CPA 接入记录](cloud-strata-cpa-access-20261116.md)，此前 32768 上下文的云端模型与推理证据见 [真实推理记录](cloud-real-inference-20261116.md)。

| 项目 | 变更前信息或本轮已报告状态 |
| --- | --- |
| 引擎 / GPU | Strata 0.1.45，CUDA 12，2 × A100 40 GB；此前模型已使用双卡运行 |
| 模型 | Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS；网关别名 `qwen-strata`，CPA 别名 `cloud-qwen-strata` |
| 原配置 | `--max-context 32768`、`--kv int8`；此前短请求和 Langfuse 事件通过，不能证明 262144 可用 |
| 变更前资源快照 | 系统内存总计约 94 GiB、可用约 39 GiB，磁盘剩余约 52 GiB |
| 本轮操作 | 部署方报告已在容器启动前备份配置，只重建 Strata；当前模型已加载，健康接口报告 `loaded=true`、`max_context=262144` |
| 加载后资源快照 | 初次两张 A100 显存占用分别为 27915 / 33983 MiB、主机可用内存约 27 GiB；长上下文测试时显存约 27985 / 34065 MiB、主机可用内存约 26 GiB。根盘可用约 51.46 GiB；均为当时快照 |

上下文容量是输入、模板、工具参数和输出共同占用的上限。此次已报告 261840 输入 Token 的近边界合成检索成功；健康接口和单次测试仍不代表完整 262144 输入、六路并发、冷缓存性能或稳定性通过。

## 2. 路径、备份与操作范围

部署方报告，调整前已保存下列备份：

```text
/opt/llm-observability-deploy/src/monitoring/docker-compose.cloud.yml.before-262k-20261116
/opt/llm-observability-deploy/strata-data/config/strata-iq3_xxs.json.before-262k-20261116
```

第二个 JSON 含认证信息，Compose 文件也应按可能含敏感信息处理；不要打印、上传、提交、展开或把内容复制到工单。保留原文件的访问权限和备份；在恢复前核对准确挂载、权限、文件身份以及当前运行状态。现场仅重建 Strata 的具体 Compose 命令、执行时间、退出码和脱敏日志尚待执行方补充，不能把下方检查命令写成已执行的部署命令。

健康观察只读示例，在云主机执行；模型加载未完成前端口可能暂不响应：

```bash
docker ps -a --filter name=llm-a100-strata --format 'table {{.Names}}\t{{.Status}}'
docker exec llm-a100-strata-1 curl -fsS http://127.0.0.1:8080/health
nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv
free -h
df -h /opt/llm-observability-deploy
docker stats --no-stream llm-a100-strata-1
```

`/health` 返回 HTTP 200 时仍须检查 `loaded=true`；`max_context` 反映服务报告值，需与引擎实际参数及请求测试交叉验证。`free` 和 `df` 只反映执行时的视图；若容器受 cgroup 内存、swap 或独立挂载配额约束，还须检查当前容器及其祖先限制、模型 / pack / Docker 数据目录所在文件系统。避免因加载耗时而反复中断模型重载。

备份存在性可只检查文件元数据，不读取内容：

```bash
test -f /opt/llm-observability-deploy/src/monitoring/docker-compose.cloud.yml.before-262k-20261116 && printf 'Compose 备份存在\n'
test -f /opt/llm-observability-deploy/strata-data/config/strata-iq3_xxs.json.before-262k-20261116 && printf 'Strata 配置备份存在\n'
```

存在性不等于备份内容正确、权限正确或恢复已验证；不要因一次健康检查失败直接覆盖配置或重建其他服务。若需回滚，应由执行方核对这两份备份、当前文件及其 bind mount 关系，在维护窗口恢复，并分别验收 Strata 和网关链路；目前尚无本次恢复演练结果。

## 3. 本轮配置目标

以下为部署方报告的新配置；模型启动设置已列出这些目标 flags，但**列出参数不等于每项功能均实际生效**。功能状态须经脱敏运行日志、服务状态和对应测试核对；只导出允许展示的参数名和值，不输出整份配置、进程环境或鉴权头。

```text
--max-context 262144
--kv k8v4
--kv-resident 32768
--prefill 8192
--prefill-pipe 1
--prompt-cache-every 8192
--prompt-cache-tail
--short-read 256
--spec 4
--spec-min-p 0.5
--suffix-draft 3
--batch 4
--pipeline-windows 2
```

服务配置目标：`parallel: 6`、`fit_max_tokens: true`、`reasoning_budget_tokens: 4096`、`reasoning_close_retry: true`、`reasoning_effort: medium`。采样配置目标：`temperature: 1`、`top_k: 20`、`top_p: 0.95`、`experimental_speed_projection: true`。请求自身的参数和服务共享设置可能覆盖默认值，测试报告须分别记录默认值与请求实际值。

部署方报告，CPA 模型的 `max-context-length` 和 `~/.claude/settings.json` 中 `cpa/cloud-qwen-strata` 的 `autoCompactWindow` 已从 32768 同步至 262144，并已备份；具体备份路径及变更时间待补充。不要输出或提交这些私有配置。客户端窗口值、服务报告值与实际测试输入长度应分别记录。

部署方另报告新增项目级 `E:/project/YOUR_CLIENT_PROJECT/.claude/settings.local.json`，设置 Claude Code `MAX_CONTEXT_TOKENS=262144`、`MAX_OUTPUT_TOKENS=16384`。这两个值影响该项目内使用的**所有模型**，不只是 `cpa/cloud-qwen-strata`；全局同名值未修改。已运行的前端需新会话或重启后才可能读取新设置，实际读取状态应单独确认。本文未读取该本地配置。

环境项由部署方简写为 `STRATA_PF_FUSED1`、`HEADROOM2`、`ROUTE_TAIL_SKIP0`。结合该版本源码，本记录将其理解为 `STRATA_PF_FUSED=1`、`STRATA_RESIDENT_HEADROOM_GIB=2`、`STRATA_ROUTE_TAIL_SKIP=0`；**完整变量名及其实际注入结果仍需部署方核对**，不要将这一推断当作容器实测结果。

## 4. 静态兼容性核对

本机公开 Strata 工作树 HEAD 为 `477ac13cbe8e582c20484871ce459d20d82df09d`，与 [云端记录](cloud-real-inference-20261116.md)所记版本来源一致；源码行为不是当前云端已运行二进制和服务状态的证明。

1. **`parallel: 6` 与 `--batch 4`：**源码的 `parallel_args` 发现 `args` 中已有 `--batch` 或 `--slots` 就不再按 `parallel` 追加批大小。部署方确认当前以 `batch 4` 为实际限制，实时指标为 `live.parallel=4`、`slots=4`；四路短请求冒烟结果见下文，不能将其写成六槽或六路并发通过。源码位置：`serve/server.py:2679`。
2. **`--pipeline-windows 2` 与 batch：**同提交引擎源码在有 batch slots 时关闭 pipeline windows，并给出相应原因；现场二进制若与该行为一致，`--batch 4` 会使该项失效，须查脱敏启动日志确认。它与 `--prefill-pipe 1` 是不同选项，不能混为一谈。源码位置：`src/program/generate.cpp:4021`。
3. **`--prompt-cache-tail` 与双卡：**同提交源码的额外尾部 checkpoint 路径要求 `!multi_gpu`；定期 checkpoint 另有多卡路径。目标参数存在不代表双卡尾部 checkpoint 实际开启，需现场确认。
4. **`--mtp-window`：**部署方报告当前编译引擎的 help 未列出该参数，本轮未写入。本机同提交源码的参数解析分支出现该名称，但源码与 help 显示并非同一证据，不能据此断言现场二进制支持或不支持；需核对实际二进制版本、构建信息和可控测试。`--spec 4` 也不自动证明每个并发请求均启用 MTP。
5. **resident experts：**本轮原生 pack 没有 `experts.bin`，`--resident-experts` 尚未启用；`STRATA_RESIDENT_HEADROOM_GIB=2` 不能当作 resident 模式已生效的证据。原按布局估算 experts 约 39.97 GiB、dense 副本约 1.43 GiB，实际独立候选生成和磁盘保护结果见第 5.3 节。固定版本源码也描述部分 native GGUF 原位映射路径，不能仅凭缺少单一文件对全部低内存模式下结论。
6. **精度与输出行为：**`k8v4` 更改 KV 精度，需比较质量与稳定性；`STRATA_ROUTE_TAIL_SKIP=0` 表示要求关闭路由尾部跳过。`experimental_speed_projection` 是改变模型输出行为的实验选项，配置默认值为 true 不足以证明控制向量已加载或双卡路径可用；本机源码明确标注控制向量及多卡适用条件，须以现场日志和输出检查为准。不同输出和缓存状态不能直接作为优化收益。
7. **资源与配置项：**`--kv-resident 32768`、较长上下文、批槽及 prompt cache 均可能改变 GPU / 主机内存需求；`fit_max_tokens: true` 是对可用输出空间的适配，并不证明任意超长输入都会成功。启动前约 39 GiB 可用内存不是容器重载后可用内存。

## 5. 验收计划与证据

部署方已报告模型加载完成及上文资源快照；实时 `/metrics` 中 `engine` 报告上下文 `262144`、KV `k8v4`、`kv_resident=32768`，`live.parallel=4`、`slots=4`。另有四路短请求冒烟；指标快照和单次冒烟不代表持续并发负载已验收。仍需补充 UTC+8 时间、GPU 分层、pipeline 和 tail 实际行为、MTP、pack 和采样设置的**脱敏**证据；不记录配置文件内容、密钥或完整进程环境。

### 5.1 Claude 完整载荷重放

部署方报告，使用 Claude 截图首次请求的 **28177 输入 Token、36 个工具**的完整载荷，经正式 CPA `8317` 重放；请求的 `max_tokens` 为 **128000**。结果 HTTP 200，返回包含 thinking 和两个 `tool_use`，`stop_reason=tool_use`，耗时 **10.42 秒**。这是一次完整载荷的功能冒烟，说明该次请求获得响应；不是 262144 输入验收、工具已实际执行的证明或性能基准。服务若对输出上限进行了适配，应以实际响应和日志确认有效输出上限，不能仅凭请求值推断已生成 128000 Token。请求内容及私有配置不写入本文。

### 5.2 长上下文实际测试

部署方报告，以下请求均经 **正式 CPA → Nginx → LiteLLM → Strata**。测试使用重复 `test` 内容，并要求找回位于开头的标记 `ORANGE-RIVER-4821`；四次均 HTTP 200 且准确找回。Token 数、缓存和耗时均为现场单次结果：

| 输入 Token | HTTP / 结果 | 耗时 | 缓存复用 Token |
| ---: | --- | ---: | ---: |
| 40040 | 200 / 正确找回 | 8.31 秒 | 0 |
| 131040 | 200 / 正确找回 | 17.94 秒 | 32768 |
| 250040 | 200 / 正确找回 | 25.5 秒 | 122880 |
| 261840 | 200 / 正确找回 | 6.11 秒 | 245760 |

261840 输入的请求设置 `max_tokens=8192`，输入加请求的输出预算超过 262144，但仍返回正确结果；这与 `fit_max_tokens` 对过大输出预算进行适配的预期一致。**实际裁剪后的输出上限尚未提供，不能仅凭 HTTP 200 推断精确裁剪值或证明生成了 8192 Token。** 最后三次使用大量缓存，尤其近边界请求复用 245760 Token；耗时不是对应长度的冷启动预填充基准。这一合成重复内容检索验证了本次给定场景，不能外推为任意长文、真实多轮或全部边界条件均已通过。

### 5.3 驻留候选包与磁盘保护

按用户要求，部署方使用独立容器生成候选包，报告 `experts.bin` 最终大小 **42912972800 字节**（约 39.966 GiB），`dense.bin` 为 **1538035200 字节**（约 1.432 GiB）。监控脚本检测到剩余 **8496095232 字节**（约 7.913 GiB），低于 **9.5 GiB** 保留阈值，触发 `STOPPING_FOR_DISK_RESERVE`。

现场日志同时出现生成成功和保护停止，二者顺序存在竞争；不能将此次结果概括为安全完成、有效完整 pack 或 resident 模式已就绪。部署方报告已确认候选容器停止，并仅删除本次候选目录；原 pack 未触碰。清理后可用空间恢复至约 **55142293504 字节**（约 51.355 GiB），与此前约 51.46 GiB 是不同时间和计量精度的快照。

由于磁盘保护，`--resident-experts` 未启用。后续须扩容根盘或提供经过容量及挂载核验的独立存储，再重新规划生成、文件完整性、来源 sidecar、内存与运行验收；原候选已删除，不存在可直接启用的候选包证据。

### 5.4 四路短请求与 Claude Code

部署方报告四路并发短请求均 HTTP 200，答案分别为 `42`、`43`、`44`、`45`，总耗时 **37.22 秒**。这是一次功能冒烟，不是吞吐、时延分位或持续并发稳定性基准；实际 `live.parallel=4`、`slots=4` 及上下文 / KV 指标见第 5 节。

部署方使用 Claude Code **2.1.325**，在 `E:/project/YOUR_CLIENT_PROJECT` 进行无工具、无图形界面的 headless 调用，模型为 `cpa/cloud-qwen-strata`，返回 `42`、退出码 0。该结果证明当次项目环境的 headless 路径获得回答；不是截图中的图形界面交互验收，也不单独证明新建会话后所有项目设置均已按预期加载。

### 5.5 后续验证

1. **短请求连通性：**经正式 CPA 模型别名分别完成非流式和流式真实调用，检查状态、可见答案、usage 和流式结束标记；按实际 Langfuse Trace ID 核对输入、输出、用量及时间。CPA 过往未原样透传 `traceparent`，不得把客户端 ID 默认当作 Langfuse ID。
2. **长上下文扩展：**在已报告 40040、131040、250040 和 261840 Token 测试基础上，继续以实际服务端 tokenizer / usage 为准，验证冷缓存、不同内容和近边界条件。保留模板、工具和预期输出所需余量，记录实际输入、输出、缓存命中、完成状态及答案；如接近边界需准确说明总上下文与输出预留。验证失败或资源不足即记录实际边界。
3. **性能与质量：**区分冷前缀、缓存复用、单请求和并发；分别记录实际输入 / 新增 / 缓存 Token、输出 Token、首 Token 延迟、预填充、解码速度、总耗时和完成率，比较相同模型、引擎、参数与请求。采样和实验投影可能改变输出，需单独说明，不把短请求或单次值外推为 262K 吞吐。
4. **多路与稳定性：**在已完成一次四路短请求冒烟的基础上，按实际 batch 槽测试持续并发、排队和错误，并观察内存、显存、swap、磁盘及服务恢复；`parallel: 6` 不能代替六路并发通过。压力测试须避免与其他业务请求混跑。
5. **指标与恢复：**检查 Prometheus 四个目标和 `strata_extra_up`、双 GPU / Token / TTFT / TPOT 样本，以及 Grafana 实际展示；历史累计值不代表本轮单请求。异常恢复及使用两份备份回滚后重新加载 / 推理仍需单独演练和记录。

| 事项 | 当前状态 |
| --- | --- |
| 容器重载与模型健康 | 部署方报告已加载；`loaded=true`、`max_context=262144` |
| 目标参数与双卡实际生效 | `/metrics` 报告上下文 262144、KV k8v4、驻留 32768、parallel / slots 均为 4；pipeline 和 tail 等实际行为待验收 |
| 原 Claude 完整载荷 | CPA 8317 重放 28177 Token / 36 工具载荷 HTTP 200，返回 thinking 和两个 `tool_use`；Langfuse 逐请求核对尚待补充 |
| 长上下文与边界 | 合成检索最高 261840 输入 Token 成功；完整 262144 输入、冷缓存和其他边界仍待验收 |
| 四路并发 | 一次短请求冒烟四个结果均正确；持续负载与性能待验收 |
| resident experts | 独立候选触发磁盘保护并已清理；未启用，待扩容或独立存储后重做 |
| Claude Code 客户端 | 项目级限制已设置；headless 短请求成功，截图 UI 与新会话设置读取待验收 |
| 性能、质量和资源稳定性 | 待验收 |
| 备份恢复演练与目标机离线部署 | 待验收 |

后续在此追加执行方提供的时间、状态和可公开证据，区分现场观测、源码推断、未验证项；不得写入秘密或用户请求原文。
