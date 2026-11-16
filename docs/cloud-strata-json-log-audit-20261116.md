# 云端 Strata 配置及启动日志逐项核对

核查日期：2026-11-16；对象为云 GPU 环境的容器 `llm-a100-strata-1` 和其内 `/data/config/strata-iq3_xxs.json`、`/opt/strata/strata-iq3_xxs.log`。本次只读核查，未改配置、未重启。以下路径是**容器内路径**；具体数值属于当时快照。

依据分三层：容器中实际引擎的 `--help`；本次启动日志和 `/health`、指标、进程信息；本地 Strata 固定提交 [477ac13c](https://github.com/Niko1221/Strata/tree/477ac13cbe8e582c20484871ce459d20d82df09d) 源码。云端二进制未与本地源码做构建身份对照，所以仅由源码支持的推论不能等同云端实测。引擎 logfile 为 stderr；READY 等 stdout 协议行不一定出现在其中。

## 1. 首要发现

1. `parallel: 6` 与 `--batch 4` 不一致。日志第 23、24 行记录两个阶段均为 4 个 batch slot，指标报告 `live.parallel=4`。当前不是六路。
2. `--pipeline-windows 2` 与 batch 槽位冲突。日志第 10 行明确写出 `--pipeline-windows 2 is off: not with --batch slots`。引擎继续启动；它和下面已工作的 `--prefill-pipe` 是不同机制。
3. `--ple-io ram` 有部分落空：日志第 9 行为 `PLE table loaded (not locked)`，加载耗时 7.7 秒。进程 `VmLck=0`、软硬 `Max locked memory` 均为 65,536 字节；这个限制是锁页失败的重要疑点，但未读取系统调用错误码，不能单凭它断言唯一根因。当前约束下表可能被换出，不能称 PLE 已锁页。
4. `--conversation-cache-mib 0` 关闭独立的驻留会话缓存，因而 `slots 64`、`min-tokens 4096` 和 `park-gain 2048` 当前不发挥驻留缓存作用。这不意味着 prompt checkpoint 或 batch 槽位前缀复用被关闭。
5. `--prompt-cache-tail` 的 help 明确只支持单 GPU；本次实际是双 GPU 分层，按固定源码的条件不会建立额外尾部 checkpoint。`--prompt-cache-every 8192` 是另一项设置。
6. MTP draft layer 在日志第 26 行已加载；进程未携带 `--batch-mtp`，且 `STRATA_BATCH_MTP` 未设置。按照固定源码，并发 batch 槽位默认不使用每槽 MTP；这不否定单请求路径使用普通 MTP 的可能性，也不等于已经验证投机接受率。
7. `experimental_speed_projection: true` 不负责加载 control vector；本次进程参数和启动日志未发现已加载向量的证据，不能称此设置已经带来加速。`STRATA_RESIDENT_HEADROOM_GIB=2` 对应的 resident 专家模式开关未配置，不能称已启用 resident experts。

## 2. 引擎参数逐项对照

按实际 JSON `args` 顺序列出。合规表示与当前 help、取值或现有日志不冲突，不代表其性能最优。

| # | 参数 | 说明与结果 |
|---|---|---|
| 1 | `--pack /data/packs/iq3_xxs` | help 要求 pack 目录；目录存在，服务已加载。存在不等于完整性已逐文件校验。 |
| 2 | `--native /models/...00001-of-00002.gguf` | help 要求原模型首分片以启用 native 路径；首片存在，填法符合说明。 |
| 3 | `--ple-gguf /models/...00002-of-00002.gguf` | help 要求 PLE 表所在分片；第二片存在，启动记录 PLE 已加载但未锁页。help 对未指定该项时的措辞前后有差异；不能据此假定缺少第二片安全。 |
| 4 | `--expert-profile /opt/strata/data/expert-profile.bin` | 用于专家预装，路径存在；日志记录双 GPU 层切分和专家缓存初始化。文件存在本身不证明 profile 性能最优。 |
| 5 | `--expert-cache auto` | help 主条目只写 `N`，未完整列出 `auto`；本地固定源码支持，云端第 35 行实际记录 `expert cache auto`，不应判作错误参数。 |
| 6 | `--prefill 8192` | 固定请求 chunk 大小，依赖 `--native`；不是自动调优。实际分块及显存借用仍受运行条件影响。 |
| 7 | `--spec 4` | MTP 验证窗口；help 给出的常用配置为 4。与 `--mtp` 配合；接受率和速度需实测。 |
| 8 | `--spec-min-p 0.5` | draft 扩展阈值；help 给出 0.5，属于合理范围，并非采样的 `top_p`。 |
| 9 | `--mtp /data/mtp/rt` | help 在其他条目引用 `--mtp`，但缺少独立 `--mtp DIR` 条目；路径存在，日志证明 draft layer 加载。 |
| 10 | `--max-context 262144` | KV/状态容量配置，health 返回相同值；输入和输出共享窗口，还需预留空间。容量配置和健康状态本身不能证明任意 256K 请求的效果。 |
| 11 | `--kv k8v4` | help 列出的混合 KV 格式，可配合 streaming；指标报告 `k8v4`。 |
| 12 | `--ple-io ram` | 合法枚举，意图内存驻留并锁页；**实际日志显示加载但未锁页**，需检查 memlock 限制和内存余量后安排变更。 |
| 13 | `--prefill-pipe 1` | help：双阶段 prompt 分块采用均衡规则，1 不代表经过本机最佳性能校准。日志有 59 条两阶段分块记录，属于请求期事件，不是 59 次启动。 |
| 14 | `--prompt-cache-every 8192` | 每处理指定数量的新 prompt token 尝试建立 checkpoint；能否建立还受实际请求与边界约束。 |
| 15 | `--short-read 256` | 最多 256 个新文本 token 走 decode 读取路径；不是上下文或生成 token 上限。比 help 的默认 64 更大，性能需比较。 |
| 16 | `--suffix-draft 3` | 对匹配的上下文重复片段尝试草稿，help 默认 3；是否有收益取决于请求文本。 |
| 17 | `--kv-resident 32768` | 每层 QSA 的显存 KV 保留 cells；大于 help 所列最小 20480，日志第 19 行显示 KV streaming。RAM/VRAM 消耗随运行情况测量。 |
| 18 | `--conversation-cache-mib 0` | help 明确 0 为关闭独立驻留会话缓存；属于有效配置，但与后续三项的预期应保持一致。 |
| 19 | `--conversation-cache-slots 64` | 最大驻留会话数；预算为 0 时不能仅靠该值启用缓存。 |
| 20 | `--conversation-cache-min-tokens 4096` | 驻留会话的最低 token 门槛；当前预算为 0，不发挥作用。 |
| 21 | `--conversation-cache-park-gain 2048` | 重新驻留前会话增长门槛；当前预算为 0，不发挥作用。 |
| 22 | `--batch 4` | help 的常规槽位范围为 2～8；4 合法，实际日志和指标均为 4 槽；与顶层 `parallel: 6` 不一致。 |
| 23 | `--pipeline-windows 2` | 请求单会话的跨 GPU 窗口重叠；实际第 10 行明确因 batch slots 被关闭，是当前无效配置。 |
| 24 | `--prompt-cache-tail` | help 限单 GPU 且要求 prompt cache/every 开启；当前双 GPU 分层，按本地固定源码不会产生额外尾部 checkpoint。 |

`--mtp-window`、`--resident-experts` 均未出现在这份实际云端 `args` 中；它们是未设置，不能称为错误参数或已启用。此前用户提供的另一份 JSON 使用不同路径及参数，应以当前实际运行配置为准。

## 3. 服务层字段与环境变量

这些不是 `args` 内的引擎 flag，不能仅根据引擎 `--help` 验证所有业务语义。

| 字段 | 判断 |
|---|---|
| `cuda: 12`、`exe`、`cwd`、`lib_dirs` | 服务启动配置；引擎、工作目录存在且已运行。库目录存在并不单独证明每个库版本。 |
| `gpu: [0,1]`、`gpus_asked: true`、`layer_split: auto` | 实际日志显示两阶段 GPU 切分；不是两张各自独立的模型实例。 |
| `host: 0.0.0.0`、`port: 8080` | 容器内服务监听地址和端口；对外访问还由容器端口映射和反向代理决定。 |
| `model_name`、`tokenizer` | 模型名用于服务标识；tokenizer 路径存在，需通过实际请求验证分词和模板匹配。 |
| `log`、`api_key` | 引擎日志路径存在；配置中有 API 密钥。不要将密钥写入文档、截图或版本库。 |
| `parallel: 6` | 与显式 `--batch 4` 不一致；当前实测是 4 槽。固定源码中显式 batch 优先。 |
| `fit_max_tokens: true` | 固定源码会根据上下文剩余空间和 8 token 余量缩小输出预算；不会扩大上下文或自动截断过长输入。 |
| `reasoning_effort: medium`、`reasoning_budget_tokens: 4096`、`reasoning_close_retry: true` | 服务端默认值和特定场景补偿；请求可以覆盖默认设置，重试也有触发条件。 |
| `sampling.temperature: 1.0`、`top_k: 20`、`top_p: 0.95` | 采样默认值，请求可覆盖；取值本身不能证明适合所有任务。 |
| `sampling.experimental_speed_projection: true` | 控制已加载的 control vector；本次未发现对应加载证据，不证明加速。 |
| `MALLOC_MMAP_THRESHOLD_=16777216` | 分配器环境变量，不是 Strata CLI；对吞吐的影响未作对照测量。 |
| `STRATA_PF_FUSED=1` | 请求启用 fused prefill 相关路径，实际使用取决于构建、GPU 和模型；本次未独立证明每次都命中。 |
| `STRATA_RESIDENT_HEADROOM_GIB=2` | 本地源码中与 resident 模式相关；未配置 `--resident-experts` 或 `--resident-budget-gib`，不能据此认定该项在此模式中起效。GPU expert cache 是独立概念。 |
| `STRATA_ROUTE_TAIL_SKIP=0` | 关闭对应 tail-skip 优化；速度与输出差异需要相同负载的对照，不能推断为最优。 |

## 4. 启动日志、内存与错误边界

- 引擎日志共有一次启动边界，时间字符串 `2026-11-16 07:59:39`，此处未核实其时区。第 48 行记录 session up；同期 health 报告 `loaded=true`、`max_context=262144`。session up 本身不是最终 READY 的全部证明。
- 启动加载完后日志显示两卡分别约剩 12,411 MiB 和 6,343 MiB 显存，**仅为启动时快照**。第 26 行 MTP draft layer 已加载，第 35 行 expert cache auto。
- 进程当前 RSS 约 68.3 GiB，容器 cgroup 用量约 72.3 GiB；宿主 `MemAvailable` 约 25.9 GiB。当时 cgroup v1 读取到非常大的近似无限值；这不能代替核查全部祖先 cgroup 限制，也不能视为可用 RAM 均可安全分配。
- PLE `loaded (not locked)` 与 64 KiB memlock 限额同时存在，是明确需要处理的部署风险。若决定锁页，应先测 PLE 表大小、RAM 和祖先 cgroup，检查容器 memlock/能力设置并按业务窗口重建容器；随后复核日志是否出现 `locked in RAM`、进程 `VmLck` 是否增加并测试真实请求。不要仅因修改 `ulimits` 就认定成功。
- logfile 中宽泛的 `error` 关键字命中 12 条；当前安全分类未能确认具体含义，不能视为 12 次启动错误，也不能说日志没有错误。服务 stdout 与 stderr 通道不同，需按具体请求时间和脱敏上下文进一步调查。
- 早先最近三小时 LiteLLM 日志存在 BadRequest 与上下文、`max_tokens` 邻近关键词；已抽样 Nginx 访问记录有历史 HTTP 400，但不能把所有 400 归为同一原因。

## 5. 建议的变更顺序

1. 确认目标是单请求延迟还是多请求吞吐。保留 4 槽时，将顶层 `parallel` 与其对齐，并清理当前无效的 `--pipeline-windows`；若选择单请求流水，需在备份配置后单独评估关闭 batch 的影响。
2. 清理双 GPU 无效的 `--prompt-cache-tail`；决定是否需要独立驻留会话缓存，若需要，先按每会话内存和可用 RAM 评估非零预算，不能直接认为 64 个槽可用。
3. 调查 PLE 锁页限制，先测量和备份；安排维护窗口调整容器 memlock 后重新核对日志与进程，再做长时间请求和内存压力测试。
4. 如希望并发槽使用 MTP，可评估 `--batch-mtp` 的显存开销和实际接受率；与普通 MTP 及 batch groups 的兼容条件以该版本 help 和受控测试为准。
5. 固定相同模型、输入长度、并发、缓存状态与采样，比较 TTFT、TPOT、吞吐、显存/RAM、错误率和输出质量；不要以单次数字证明配置最优。

## 6. 可复查资料

仓库相关手册：[云端部署记录](cloud-container-deployment-20261116.md)、[262K 配置记录](cloud-strata-262k-tuning-20261116.md)、[复用已有 Strata 的离线部署](kylin-v10-reuse-offline-bundle-20261116.md)。本报告为仓库更新，已发布离线包是较早快照，以包内文件时间和校验和为准。
