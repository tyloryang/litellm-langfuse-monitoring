# 最后一次云端离线部署验证总结

记录日期：2026-11-16。云服务器已退还，本记录依据会话中最后一次现场输出、本地保留的物料和已提交的验证文档整理；不再声称可以复查云端状态。

## 结论和范围

**最后一次按完整手册新建隔离环境的验证未完成。** 主包传输和校验通过，补充包校验失败，流程在解压、数据库恢复和新项目启动之前结束。不能据此声明云端二次离线部署或麒麟 V10 完全离线验收成功。

此前已完成并分别验证过：云端运行栈的真实模型及 Langfuse 工具链；单独的隔离网络 mock 上游链路；WSL 隔离环境中的 PostgreSQL、ClickHouse、Redis 和 MinIO 数据恢复。它们是不同阶段的证据，不能合并成“全新麒麟机器断网部署全部通过”。

## 一、交付架构和物料

业务路径：客户端 → Nginx → LiteLLM → Strata → Qwen，模型回复沿原路返回。LiteLLM 的观测回调异步发送 Generation 到 Langfuse；客户端只有真实执行工具并埋点后，才会出现 Agent / Tool。Prometheus 和 Grafana 是独立的指标采集与展示支路。Nginx 和 Strata 没有因为部署而自动产生各自的内部 span。

离线主包复用目标机已有 Docker、Strata 和模型，含九个 `linux/amd64` 镜像：Nginx、LiteLLM、Langfuse Web、Langfuse Worker、PostgreSQL、ClickHouse、Redis、MinIO 和 MinIO Client。先前现场核对这九个云端运行镜像的完整 image ID 与包内清单一致，镜像归档的 SHA-256 也一致。主包含 Compose 与网关配置；Prometheus 2.45.1、Grafana 10.1.5 及看板在单独监控物料中。

主包 **不包含** Strata 镜像、模型权重、GPU 驱动或目标麒麟 V10 的完整 RPM 依赖闭包；这些需要现场已有或另行按目标系统准备。截图显示目标机为麒麟 V10、x86_64 和两张 A100 40 GB，但具体 SP / Build、可用组件与网络必须现场重新核对。

| 文件 | 内容与校验 |
| --- | --- |
| `kylin-v10-gateway-reuse-amd64-20261116.tar.part01` 至 `part05` | Release 五分卷；脱敏后合并为 2,101,688,320 字节 |
| `BUNDLE-PARTS-SHA256SUMS`、完整 tar 的 `.sha256` | 分卷和合并包校验 |
| `offline-full-chain-update-20261116.tar.gz` 及其 `.sha256` | 手册、配置示例、真实工具链验收脚本；约 1 MB |
| `cloud-history-20261116.tar.gz.gpg` 及其 `.sha256` | 加密的云端历史数据及私有配置，19,481,280 字节 |
| `kylin-v10-complete-offline-operations-20261116.md` 及其 `.sha256` | 单文件操作指南 |
| `history-decryption.key` | 独立转运的解密密钥，不在 GitHub |

联网准备机从 [GitHub Release](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-reuse-amd64-20261116) 获取附件。完整离线操作指南见 [麒麟 V10 完整离线部署操作指南](kylin-v10-complete-offline-operations-20261116.md)。本地仍可找到合并主包、补充包、加密历史包和密钥；本次没有重新生成它们。

## 二、最后一次云端操作的实际进度

1. 只读检查发现云服务器约 94 GiB 内存、2.3 GiB 可用内存、约 2.5 GiB swap 已使用，根盘约 51 GiB 可用；原业务栈与此前的隔离测试栈都在运行。计划使用独立目录、项目名和资源限制，避免改动现有服务，但没有进入启动阶段。
2. 原业务容器和 Strata 均记录为运行中；此前隔离测试网络的 `internal=true`。现场机器、容器状态和临时目录在退机后均无法再次核验。
3. 本次创建 `/opt/llm-offline-verification-20261116/`，把已合并的主包、补充包、手册和独立密钥传入其受限目录，并从云端既有加密备份复制历史包。主包 SHA-256 校验显示成功。
4. 随后执行补充包 `.sha256` 校验时，云端校验工具报告无法读取清单里的文件名；本次未完成补充包、历史包的后续云端校验，也没有执行解压、导入、数据恢复或新服务启动。原业务栈与数据没有被本次验证重建或修改。
5. 临时目录曾包含一个 root 可访问的密钥副本；当时目录为 `0700`、文件为 `0600`。机器已退还，无法确认该副本或整台实例的数据擦除情况，应按云资源退还流程向提供方确认。

## 三、校验问题与内网处理方式

本地复核发现：原 Release 的分卷清单、部分 `.sha256` 清单使用 Windows 的 CRLF 换行，包含 `\r`。云端当时的错误表现与旧版校验工具把 `\r` 当成文件名一部分相符；当前 WSL 的 `sha256sum` 可直接兼容 CRLF。本地重新计算后，补充包与加密历史包的 SHA-256 均与已发布摘要一致，因此不能仅凭云端的文件名错误认定压缩包损坏。云服务器已退还，无法再次在原机复现或确认其 coreutils 的具体行为。

公开前已重新发布脱敏后的五个分卷。合并后的 `kylin-v10-gateway-reuse-amd64-20261116.tar` 为 2,101,688,320 字节，SHA-256 为 `8b50cf74306f1c455487f257b91587ccffad17df1a6046d339cd1b474a8b48ae`。原先针对旧字节制作的 LF 修复包（2,110,976,000 字节，SHA-256 `45cbd4df7e66dca3f7298b8dbc75b666772f0c746dd1716d64f7128be61f52d1`）和 `kylin-v10-gateway-lf-patch-20261116.json` 只对应脱敏前的归档，不能再套到新分卷上。新包内部校验清单已按脱敏后的文件重算。

为兼容不同 Linux 版本，在文件所在目录执行下列命令。先检查五个分卷，按完整手册顺序合并后，再检查其余文件：

```bash
set -euo pipefail
cd /实际/离线介质目录
tr -d '\r' < BUNDLE-PARTS-SHA256SUMS | sha256sum -c -

# 完整 tar 必须已经按 part01 → part05 的顺序合并后再执行。
tr -d '\r' < kylin-v10-gateway-reuse-amd64-20261116.tar.sha256 | sha256sum -c -
tr -d '\r' < offline-full-chain-update-20261116.tar.gz.sha256 | sha256sum -c -
tr -d '\r' < cloud-history-20261116.tar.gz.gpg.sha256 | sha256sum -c -
tr -d '\r' < kylin-v10-complete-offline-operations-20261116.md.sha256 | sha256sum -c -
```

命令只规范化传给校验程序的清单流，不修改压缩包；如果转换后仍失败，应停止并重新获取文件。`BUNDLE-PARTS-SHA256SUMS` 要求五个分卷实际存在；若只携带已经合并且校验过的完整 tar，跳过分卷校验。

## 四、先前实际通过的验证

| 验证项 | 实际范围和结果 |
| --- | --- |
| 云端真实推理及追踪 | 在当时运行的云端栈中，经 Nginx、LiteLLM 和真实 Strata 进行两次模型调用；客户端实际执行 `add_numbers`，最终回答 42。同一 Langfuse Trace 检查到 1 个 Agent、2 个 Generation、1 个 Tool，以及模型、输入输出、用量和时间。 |
| 隔离网络 mock | 之前的独立测试项目使用 `internal=true` 网络，通过 mock 上游返回 HTTP 200，并在 ClickHouse `events_full` 中找到对应事件。该结果不是实际模型的离线冷安装验收。 |
| PostgreSQL / ClickHouse | WSL 隔离容器中恢复 PostgreSQL 84 张 public 表、ClickHouse 18 张表，并查询到 166 条 `events_full` 事件；测试 ClickHouse 版本与云端小版本不同。 |
| Redis | WSL 隔离容器先以 RDB 模式恢复，再开启 AOF；162 个未过期键在 AOF 重启后仍存在。过期键数会随时间变化。 |
| MinIO | WSL 隔离恢复单节点 MinIO 后可列出 147 个对象；没有逐个读取验证。原云端采集 MinIO 数据目录时服务仍运行，存在跨组件一致性边界。 |

历史备份窗口为 **2026-11-16 12:27:26–12:28:30 UTC**，不包含窗口之后新增的记录。跨组件的直接写入、延迟任务和对象完整性仍需目标机恢复后按业务核对。

## 五、内网现场接续顺序

1. 核对麒麟 SP / Build、x86_64、磁盘、Docker、现有 Strata 地址、模型 ID 和认证；至少按手册预留主包、展开 tar、镜像层、历史数据及备份空间。
2. 通过受控介质携带完整主包、补充包、加密历史包、手册和独立密钥；先按第三节校验，再按完整手册逐层解压。
3. 使用新的独立目录与项目名，解密历史包，保留原 `SALT`、`ENCRYPTION_KEY`、`NEXTAUTH_SECRET`、数据库 / 对象存储凭据和 Langfuse 项目密钥；将云端 Strata 地址、URL、端口、可信代理和证书改为目标现场值。
4. 恢复 MinIO、来源用量 SQLite、Redis、PostgreSQL 和 ClickHouse。Redis 先按手册以 RDB 模式启动，确认加载后开启 AOF 并等待重写完成，之后再切回正常 AOF 启动。
5. 启动 Langfuse、LiteLLM 和 Nginx；检查健康状态后，抽查历史 Trace 和对象，再经目标 Nginx 分别发送非流式、流式和真实工具调用，在 Langfuse 中核对同一 Trace 下的 Agent、两个 Generation、Tool、模型及 Token。
6. 如要复用现有 Prometheus / Grafana，先备份并按现场网络配置抓取和导入看板；本主栈不会自动部署或修改已有监控。所有检查在目标环境断网条件下记录结果后，才能给出现场验收结论。

完整命令、入口地址、配置编辑、数据恢复、故障排查和链路验证见 [LF 版单文件离线部署手册](kylin-v10-complete-offline-operations-20261116-lf.md)。旧版手册和原包保留历史含义；遇到对应文件名错误时可按第三节对旧清单使用兼容校验。
