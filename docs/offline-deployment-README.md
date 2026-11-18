# 离线部署 README：给内网运维人员

**当前推荐：** 使用重新发布的五个分卷，合并为脱敏后的 `kylin-v10-gateway-reuse-amd64-20261116.tar`。不要再运行旧的 LF 字节补丁；那个补丁只对应脱敏前的归档。操作步骤见 [麒麟 V10 离线操作指南](kylin-v10-offline-operations-step-by-step.md)。

目标：复用内网已有 Docker、Strata 与 Qwen 模型，离线部署或重部署 Nginx、LiteLLM、Langfuse；最后通过真实请求在 Langfuse 中看到调用链。本文是入口，逐条命令与故障处理在随包 `manual/docs/kylin-v10-offline-operations-step-by-step.md`。

**目标麒麟 V10 尚未完成本项目的断网冷安装验收。** 网关主包和手册更新包不包含 Strata 镜像、模型、GPU 驱动、真实账号或目标系统完整 RPM 依赖；这些需要已有或另行准备。不要以云端验证代替内网实际验收。

若还需要迁移云端已有历史 Trace 和账号，请另外携带 Release 的加密 `cloud-history-20261116.tar.gz.gpg`、其 `.sha256`、独立的 `cloud-history-offline-restore-20261116-v3.md` 和通过受控渠道单独提供的解密密钥；按 [历史数据恢复手册](cloud-history-offline-restore-20261116.md) 使用新的独立目录恢复。此前发布的手册更新包为固定快照，历史恢复手册作为单独附件提供。

## 1. 携带哪些文件，解压后在哪里

联网准备机从私有 GitHub Release `offline-reuse-amd64-20261116` 获取五个大包分卷、`BUNDLE-PARTS-SHA256SUMS` 和完整 tar 的 `.sha256`，依照操作指南合并为 `kylin-v10-gateway-reuse-amd64-20261116.tar`。另外下载新版 `offline-full-chain-update-20261116.tar.gz` 和其 `.sha256`。把这四个文件拷贝到内网：

```text
kylin-v10-gateway-reuse-amd64-20261116.tar
kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
offline-full-chain-update-20261116.tar.gz
offline-full-chain-update-20261116.tar.gz.sha256
```

如果拿到的是五个分卷而非完整 tar，先在联网准备机按逐步指南校验、合并；不能只复制一个分卷。完整 tar 为 2,101,688,320 字节，SHA-256 `8b50cf74306f1c455487f257b91587ccffad17df1a6046d339cd1b474a8b48ae`。校验值也应通过可信渠道核对。内网机进入实际介质目录，执行：

```bash
set -euo pipefail
cd /实际/离线介质目录
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
sha256sum -c offline-full-chain-update-20261116.tar.gz.sha256
test ! -e /opt/llm-handoff
test ! -e /opt/llm-offline-full-chain-update-20261116
tar -xf kylin-v10-gateway-reuse-amd64-20261116.tar -C /opt
tar -xzf offline-full-chain-update-20261116.tar.gz -C /opt
cd /opt/llm-handoff
sha256sum -c HANDOFF-SHA256SUMS
cd /opt/llm-offline-full-chain-update-20261116
sha256sum -c MANUAL-UPDATE-SHA256SUMS
```

大包解压后的结构：

```text
/opt/llm-handoff/
├── README.txt                  # 原始包的说明
├── HANDOFF-SHA256SUMS          # 原包内部文件清单
├── downloads/
│   ├── nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz
│   ├── monitoring-amd64-20261116.tar
│   ├── source-and-docs-20261116-validated.tar.gz
│   └── SHA256SUMS、MONITORING-SHA256SUMS、VALIDATED-SHA256SUMS
├── manual/                     # 打包当时的旧文档及脚本
├── patches/                    # Grafana TTFT 看板及校验
└── system/                     # Compose / RPM 候选，非目标麒麟已验证依赖闭包
```

新版补充包独立解压，不覆盖上面的原始文件或破坏原有校验清单：

```text
/opt/llm-offline-full-chain-update-20261116/
├── README.md                   # 本入口文档
├── MANUAL-UPDATE-SHA256SUMS    # 更新包内部文件校验
└── manual/
    ├── docs/
    │   ├── kylin-v10-offline-operations-step-by-step.md    # 逐条操作指南
    │   ├── kylin-v10-full-offline-deployment-20261116.md  # 完整手册，附录 A 含 Strata JSON / 大页
    │   ├── cloud-real-inference-20261116.md              # 云端真实链路证据
    │   ├── grafana-formulas.md                           # 看板指标与公式
    │   └── kylin-v10-offline-assets/                     # 历史示例截图
    ├── examples/
    │   ├── offline_tool_trace_check.py                   # 完整工具链真实验收
    │   └── strata-iq3_xxs-cloud-4slot.example.json       # 脱敏参考配置
    ├── deploy/strata/docker-compose.example.yml           # Strata 容器参考配置
    ├── litellm/、nginx/、deploy/cloud-monitoring/        # 参考配置
    └── ops/                                              # 适配与系统清查脚本
```

`monitoring-amd64-20261116.tar` 是 Prometheus / Grafana 扩展包；若要求现有监控不动，不导入或启动它。`source-and-docs` 是旧源码与文档快照；以新版补充包为运维文档入口。`patches/` 只有在明确升级监控看板时使用。`system/` 的 CentOS / NVIDIA RPM 未完成目标麒麟兼容验证，已有 Docker 时不要安装或覆盖。

## 2. 再解压哪个包，安装哪些镜像

在目标机确认 Bash、Python 3、OpenSSL、tar、gzip、sha256sum、Docker 均可用；CPU 必须为 `x86_64`。确认磁盘能同时容纳原包、约 5.71 GB 的恢复 tar、解压的镜像包、Docker 镜像层、业务数据和备份。建议至少预留 30 GiB，再按真实数据量预留额外空间。

按 `manual/docs/kylin-v10-offline-operations-step-by-step.md` 第 3～4 节恢复 `downloads/nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz` 为 tar，核对 SHA-256 后解压。不要直接将 `.tar.gz` 当作镜像导入。成功后得到：

```text
/opt/llm-offline/nginx-litellm-langfuse/
├── SHA256SUMS
├── images/images.tar           # 9 个核心镜像的 docker load 输入
├── images.env                  # 9 个镜像的本地标签
├── source.tar                  # 网关与 Langfuse 配置源码快照
├── config/prepare.sh           # 仅用于创建新部署目录
├── config/docker-compose.yml
└── host/compose/docker-compose # Compose 2.29.7 二进制，不是 Docker Engine
```

9 个镜像是：Nginx、LiteLLM、Langfuse Web、Langfuse Worker、PostgreSQL、ClickHouse、Redis、MinIO、MinIO Client。新建主栈时执行 `docker load -i images/images.tar`；**现有实例只重部署时，先检查其镜像已在本机且 ID 与正在运行的一致，已存在则跳过导入**。导入会写入全部九个镜像和标签，不会自动启动容器，但可能影响其他项目使用的同名标签。

## 3. 目标机需要改什么，哪些先不要动

先用 `docker ps -a`、`docker inspect` 或 systemd 确认原服务归属、Compose 项目、所有配置文件、挂载、网络、镜像版本和已有端口。`manual/docs/kylin-v10-offline-operations-step-by-step.md` 第 3、5、6 节包含具体检查和备份命令。

| 位置 | 要核对或调整的内容 |
| --- | --- |
| 现有 Strata | 记录可从 Docker 网络访问的真实 IP / 端口、模型 ID 和 API key；确认 `/health` 为 `loaded=true`。复用时不替换其配置、不重启、不另启同模型容器。 |
| `/opt/llm-gateway/.env`（仅新建） | `HOST_IP`、`NEXTAUTH_URL`、`LANGFUSE_MEDIA_PUBLIC_ENDPOINT`、真实 `STRATA_API_KEY` 和可信代理 CIDR 按目标网络配置；新生成的账号、项目 key、盐和加密密钥保存在私有环境。 |
| `/opt/llm-gateway/litellm/config.yaml` | `qwen-strata` 的 `api_base` 改成容器可达的真实 `http://IP:端口/v1`；上游模型名与 `/v1/models` 一致。已有配置先备份并保留回调，未部署的示例模型按现场移除。 |
| `/opt/llm-gateway/nginx/` | 目标域名、证书 / 私钥、端口、可信代理与访问控制按现场确定。准备脚本生成的自签证书没有 SAN，应更换为目标 CA 和实际域名 / IP 匹配的证书。 |
| `docker-compose.yml` / `offline-override.yml` | 新部署根据目标端口和挂载目录审阅；首次启动须同时加载两文件。已有部署继续使用原项目、原文件和原网络。 |

**不应重新生成或覆盖**已有 Langfuse 的数据库连接、项目公私钥、`SALT`、`ENCRYPTION_KEY`、`NEXTAUTH_SECRET`、历史数据与 SQLite 来源用量账本；不能把较新 Langfuse 的数据库挂给旧镜像降级。不要在已有 `/opt/llm-gateway` 上运行 `prepare.sh`，不要执行 `docker compose down -v`。首次新建与复用现有服务是两条不同路线，不能混用。

## 4. 如何启动：先选现场实际情况

### 已有实例，仅重部署三个组件

本路线只重建 Nginx、LiteLLM、Langfuse Web 和 Worker；Strata、模型、数据库及监控继续使用原实例。先备份 Compose、私有 `.env`、证书、回调、日志和数据库一致性快照，并核对容器可写层是否有额外安装的依赖。

依次执行操作指南第 6 节的实际项目命令：用本地离线 Compose 程序检查 `config --quiet`，核对 Compose 展开后镜像 ID 与当前容器一致，再依次重建 Langfuse、LiteLLM、Nginx；每步只用 `--no-deps --no-build --pull never --force-recreate` 指定服务。Langfuse 和 LiteLLM 的就绪检查通过后才能进入下一步；最后 `nginx -t`。服务会短暂中断，原数据库、模型及监控不可被 `up` 顺带重建。

如果 LiteLLM 是 systemd 服务（例如某些 WSL 布局），不能把这个 Compose 例子当作它的重启命令；先核对该机器实际服务单元、配置与回滚方式。

### 全新独立主栈

只有现场明确允许新建独立 PostgreSQL、ClickHouse、Redis、MinIO、数据目录及网络时才使用。按操作指南第 7 节：检查项目名、网络、端口及 `/opt/llm-gateway` 未被占用，导入九镜像，用 `config/prepare.sh /opt/llm-gateway` 生成新配置，设置现有 Strata 地址 / 密钥并换证书；最后使用随包 Compose 同时加载 `docker-compose.yml` 和 `offline-override.yml`，带 `--pull never --no-build` 启动。此路线不会迁移原有数据库和历史数据。

## 5. 如何看到完整调用链

业务数据路径是 **客户端 → Nginx → LiteLLM → Strata → LiteLLM → Nginx → 客户端**；LiteLLM 回调把 Generation 异步上报 Langfuse，Prometheus / Grafana 另行采集指标。Langfuse 不位于每个推理 Token 的返回路径。

第一步执行操作指南第 8 节的非流式及流式真实请求，检查 HTTP 200、答案、usage、流结束标记，并在 Langfuse Tracing 找到同次请求。第二步执行 `manual/examples/offline_tool_trace_check.py`：该脚本通过 Nginx 调用真实模型，客户端执行 `add_numbers` 后再次调用模型，并通过 Langfuse Observations v2 核对 1 个 AGENT、2 个不同的 GENERATION、1 个 TOOL、父子关系，以及两个 Generation 的模型、输入输出、用量和时间。

操作指南第 8.1 节提供可复制的 `docker exec` 命令；它复用离线 LiteLLM 镜像中已有的 Langfuse SDK 和该实例现有项目 key，不需要目标机联网或 `pip install`。使用 HTTPS 时必须提供容器可验证的目标 CA 和匹配域名；不要关闭证书校验。Trace 是异步写入的，脚本会等待最多 180 秒；在 Langfuse 中按打印的 `trace_id` 查询，必要时将环境筛选切到全部。

这条测试验证的是 **Agent → 模型调用 → 工具 → 模型调用** 的逻辑追踪。Nginx 和 Strata 在此交付中没有分别承诺独立的服务内部 span；排查每一跳应结合 Nginx 日志、网关回调、Strata 日志及相同请求时间 / ID。只有真实上报的 span 才能出现在 Langfuse，不能把示意图当作已采集的 span。

## 6. 验收完成的标准

在目标机记录：包与内部 SHA 全通过；原服务 / 数据备份可恢复；预期服务正常且其他服务启动时间未变；网关非流式 / 流式响应正确；Langfuse 的两个 Generation 及真实 Tool 父子关系和 usage 可见；必要时核对来源 IP、Prometheus / Grafana；最后在断网条件下完成重启与恢复演练。云端已经做过真实推理和完整工具链验证，但内网目标机必须重新实测并填写 `manual/docs/offline-acceptance-template.md`。
