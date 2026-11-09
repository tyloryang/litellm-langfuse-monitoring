# WSL 渡渡鸟换源与数据保留记录

执行日期：2026-11-09。环境：Windows / `Ubuntu-24.04` WSL，Docker 29.8.0，`linux/amd64`。

**本机 9 个相关容器中，5 个已实际从渡渡鸟拉取同应用版本镜像，并重建、启动和验证；另外 4 个因镜像站缺少对应版本而保留。** LiteLLM 使用现有 Python venv 和用户服务，不属于本次容器换源。Strata 推理进程继续运行。

## 已切换的组件

实际仓库前缀是 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/`，不是 `docker.aityp.com`。完整 tag、manifest digest 和上游映射见 [WSL 镜像清单](../offline/ddn-runtime-wsl.amd64.tsv)。

| 组件 | 迁移前应用版本 | 实际 DDN tag | 保留的数据 |
| --- | --- | --- | --- |
| Prometheus | 3.15.1 | `docker.io/prom/prometheus:v3.15.1` | `llm-observability_prometheus-data` |
| Grafana | 13.3.0 | `docker.io/grafana/grafana:13.3.0-distroless` | `llm-observability_grafana-data` |
| PostgreSQL | 17.11，`17.11-1.pgdg13+2` | `docker.io/postgres:17.11-trixie` | `llm-observability_langfuse_postgres_data` |
| Redis | 7.4.12 | `docker.io/library/redis:7.4.12-bookworm` | `llm-observability_langfuse_redis_data` |
| Nginx | 1.28.3 | `docker.io/library/nginx:1.28.3-alpine` | 原有配置、证书及日志 bind mount |

5 个镜像均读取了远端 manifest / config、核对摘要与 amd64 平台，并在本机完成完整 pull、rootfs 层核对及版本命令验证。Grafana 迁移前后的构建提交均为 `6f8ce3c693b493043ebcd4787566d2b6b118b0f0`；Prometheus revision 均为 `bae3eced0a5369e050e83ea73bc381d7c4b1a719`。Grafana 使用 distroless 变体，诊断应使用 HTTP API 或镜像内的 Grafana 可执行文件，不能假设存在 shell。

运行 Compose 文件的 `image` 均采用 `tag@sha256:manifest摘要`。这次是实际下载和使用 DDN 镜像，未通过给旧镜像添加 DDN 名称代替换源。

## 尚未切换的组件及原因

| 组件 | 本机保留版本 | DDN 核查结果 |
| --- | --- | --- |
| Langfuse Web | 4.73.0 | 精确版本查询无同步记录，已知仓库的 `4.73.0` manifest 返回 404 |
| Langfuse Worker | 4.73.0 | 同上；必须与 Web 成对选取相同版本 / revision |
| ClickHouse | 26.3.62.3 | 对应 manifest 返回 404；已同步的 26.3.21.7 版本更旧 |
| MinIO | `RELEASE.2026-10-23T20-06-51Z`，Chainguard 构建 | 未找到此构建；DDN 的 `docker.io/chainguard/minio:latest` config 创建于 2026-02-04 |

DDN 的 Langfuse 浮动 `:4` 也不能直接使用：本次读到的 Web 实为 **4.9.0**，Worker 实为 **4.19.0**，两者既不匹配，也比本机旧。仓库新装候选中的 4.63.0、ClickHouse 26.3.21.7、MinIO 2025 版本不作为当前数据卷的降级方案。

这 4 项的实际运行镜像未更换，运行 Compose 已固定其原有仓库 digest，防止后续浮动 tag 意外更新。完成全量换源还需镜像站提供对应发行物，再核对 manifest、平台、应用版本和 Web / Worker revision，经过副本验证后替换。核查入口：[DDN 索引 API](https://docker.aityp.com/api/v1/image)、[公共接口说明](https://docker.aityp.com/manage/api)。

## 配置和启动方式

本次只修改以下实际拥有容器的配置，没有用仓库的新装 Compose 覆盖旧部署：

| Compose | project | 本次重建服务 |
| --- | --- | --- |
| `/home/yys/llm-observability/docker-compose.monitoring.yml` | `llm-observability` | `prometheus`、`grafana` |
| `/home/yys/llm-observability/docker-compose.langfuse.yml` | `llm-observability` | `postgres`、`redis` |
| `/home/yys/litellm-langfuse-monitoring/docker-compose.local.yml` | `llm-gateway` | `nginx` |

保留原 project 名、网络 `llm-observability_default`、端口、命名卷及 bind mount。数据库停止前先停止 Langfuse Web / Worker，并执行 Redis `SAVE`；数据库健康后启动原 Web / Worker。ClickHouse、MinIO 和无关的 kind 容器未重启，推理模型未重新加载。

发现旧容器的实际环境与磁盘 `.env` 不一致：部分密码、数据库 URL、Langfuse 加密 / 签名密钥、S3 参数和初始化项目参数只存在于原容器中。首次重建时 Redis 使用了 Compose 默认认证值，造成短暂的连接认证失败；随后从迁移前的容器快照恢复原值，并逐项验证所有 Langfuse Compose 服务的有效 environment 与旧容器一致。恢复的是旧值，不是重新生成密钥；原账户、数据库密码和已加密内容继续使用。

已将这些值补齐到权限为 `0600` 的 `/home/yys/llm-observability/.env`，备份也只保存在 WSL。不要输出完整 `docker inspect` / `docker compose config`，或将此 `.env` 提交到 GitHub。

后续维护应显式指定项目目录与环境文件，例如在 **WSL 终端**执行：

```bash
docker compose --project-directory /home/yys/llm-observability \
  --env-file /home/yys/llm-observability/.env -p llm-observability \
  -f /home/yys/llm-observability/docker-compose.monitoring.yml \
  up -d --no-deps --pull never --no-build prometheus grafana
```

数据库服务使用同样参数，将 `-f` 改为 `docker-compose.langfuse.yml`，服务名改为 `postgres redis`；操作前先停止 Web / Worker。Nginx 使用其自身目录、`.env`、`llm-gateway` project 和 `docker-compose.local.yml`。只需重启已存在容器时可使用 `docker restart <容器名>`，其配置和镜像保持不变。

## 数据保留验收

所有数据卷都复用原名称。替换前对停止后的 4 个卷生成 tar，分别恢复到隔离卷，实际启动 DDN Prometheus、Grafana、PostgreSQL、Redis 验证后，再替换正式容器；隔离验证容器和卷已清理。

| 检查 | 结果 |
| --- | --- |
| Langfuse 用户 / 项目 | 原 1 个用户、1 个项目，用户 ID 摘要和项目 ID 列表一致 |
| Langfuse 历史记录 | 迁移前 143 条 trace、147 个 observation 的事件 ID 全部保留；数据来源为 v4 的 `events_core FINAL`，不以空的旧 `traces` / `observations` 表判断数据丢失 |
| MinIO | 验证时原 146 个对象、20,010,568 字节一致；后续验证请求会增加对象 |
| Grafana | 原 2 个看板、1 个数据源、1 个用户和对应 ID / 角色保留 |
| Prometheus | 迁移前固定时间段的历史 `up` 查询结果 SHA-256 完全一致；维护窗口的停采仍会体现为空档 |
| Redis | 72 个无过期时间的键与迁移前 RDB 副本逐项比对逻辑值一致；过期键数量可以随时间变化，HASH 的序列化字节顺序不作为内容一致的判断依据 |
| 容器 | 9 个相关容器运行，所有已有 healthcheck 健康；4 个 Prometheus 目标 UP |
| 入口代理 | HTTP 网关、HTTPS 网关、HTTPS Langfuse、metrics 代理均返回 200；HTTPS 使用原证书校验，未关闭证书验证 |
| 指标 | 78 条 Grafana PromQL 执行成功，关键硬件 / 缓存指标有数值；低流量窗口内分位数可能为 NaN，未部署的 vLLM / SGLang 指标可以为空 |
| 流式请求 | 实际生成 72 个 completion token，Langfuse 中输入、输出、推理 usage、TTFT、TPOT 验证通过 |
| 工具链 | 新运行 `add_numbers(17, 25)` 返回 42，两个 GENERATION、TOOL、AGENT 的父子关系和 Session 正确 |

验收报告位于工作区 `.runtime/post-ddn-validation.json`、`.runtime/post-ddn-final-validation.json`。迁移前后私有数据基线及保留检查位于下面的 WSL 备份目录，不公开业务内容或 API Key。

## 备份与回退

本次备份目录：

```text
/home/yys/llm-observability/backups/ddn-migration-20261109T115856Z/
  images-pre-ddn.tar                   原 5 个镜像，可离线 load
  langfuse-postgres.dump               PostgreSQL 自定义格式逻辑备份
  postgres-globals.sql                 数据库角色等全局设置
  grafana-pre-ddn.sqlite               SQLite 在线备份，integrity_check=ok
  llm-observability_*.tar              停止后的原 4 个数据卷备份
  configs/...                         迁移前 Compose / .env 私有副本
  configs-after/...                   完成换源并补齐原认证后的私有配置
  containers-before.private.json      迁移前容器配置，包括原认证值
  data-{before,after}.private.json     数据基线
  retention-checks.json                数据保留比对结果
  migration-state.json                 镜像、数据卷备份及迁移状态
  rollback-{monitoring,databases,nginx}.yml
  SHA256SUMS                          固定备份文件的 SHA-256
```

目录权限 `0700`、备份文件 `0600`。原镜像另外保留为 `llm-rollback/<服务名>:pre-ddn-20261109`，没有执行镜像 / 卷清理。备份不进入 GitHub。

需要回退同版本镜像时，可在上述启动命令中追加第二个 `-f <备份目录>/rollback-monitoring.yml`；数据库和 Nginx 分别使用对应 override。override 放在正式 Compose 后面，所有服务仍使用当前已修正的 `.env` 和原数据卷。原镜像不存在时先执行 `docker image load -i <备份目录>/images-pre-ddn.tar`。回退后继续使用同一 override 维护，或将其镜像引用写回正式 Compose，避免下次直接启动基础配置又切回 DDN。

数据恢复应先在新卷中解包 / 导入验证，再停服务切换。不要把备份直接解包到运行中的数据库卷，也不要使用 `down -v`。本次未对正式数据卷执行回退恢复；已执行的是备份副本恢复启动及迁移前后比对。

## 与麒麟离线部署的关系

这份 5 项清单记录本机同版本换源，可以用 `bash ops/pull-from-ddn.sh offline/ddn-runtime-wsl.amd64.tsv linux/amd64` 在联网准备机重新取得镜像；它不是完整离线部署清单。完整的新装候选仍见 [DDN 准备流程](ddn-images.md) 和[麒麟离线手册](offline-deployment-baseline.md)。4 个缺失版本补齐、完整镜像 / RPM / wheel 封包和麒麟实机断网冷装验收仍需独立完成。
