# 渡渡鸟 DDN 镜像清单与准备流程

核查日期：2026-11-09。用途：在联网麒麟准备机从渡渡鸟镜像站获取发行物，完成依赖 / 运行验收后制作离线包。目标机导入 tar 并使用 `llm-offline/…` 本地别名，运行期间不访问 DDN 或其他公网仓库。

## 实际地址与核查范围

- 索引站：[docker.aityp.com](https://docker.aityp.com/)。它不是本项目的 Docker registry 地址。
- [公共 API 说明](https://docker.aityp.com/manage/api)：`GET /api/v1/image?search=精确上游引用&platform=linux/amd64`，按响应的 `source` 和 `platform` 精确匹配，使用其 `mirror` 字段。
- 本次 9 个核心镜像的实际镜像仓库为 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/…`。没有使用把上游域名简单替换为 `docker.aityp.com` 的方式。
- 已读取每个镜像的 registry manifest 与 config blob，核实 manifest digest 和 `linux/amd64`；Web / Worker 的应用版本和 revision 一致。未下载完整镜像层，也未在麒麟上运行这些容器。

`.env.example` 和 [offline/images.example.env](../offline/images.example.env) 的 9 个 IMAGE 变量保持一致，全部采用 `实际DDN路径:tag@sha256:manifest摘要`。digest 固定内容，tag 用于辨认版本；即使镜像站刷新同名 tag，也不会静默换包。完整地址 / digest / 上游映射见 [ddn-image-map.amd64.tsv](../offline/ddn-image-map.amd64.tsv)。manifest digest 与 Docker image ID 是不同对象，离线封包后仍核对完整 image ID。

## 当前候选版本

| 组件 | DDN 镜像路径（省略共同 registry / ddn-k8s 前缀） | tag |
| --- | --- | --- |
| Nginx | `docker.io/library/nginx` | `1.28-alpine` |
| PostgreSQL | `docker.io/postgres` | `17.11` |
| ClickHouse | `docker.io/clickhouse/clickhouse-server` | `26.3.21.7` |
| Redis | `docker.io/redis` | `7.4.11` |
| MinIO | `docker.io/minio/minio` | `RELEASE.2025-09-07T16-13-09Z-cpuv1` |
| MinIO Client | `docker.io/minio/mc` | `RELEASE.2025-05-21T01-59-54Z-cpuv1` |
| Langfuse Web | `docker.io/langfuse/langfuse` | `4.63.0` |
| Langfuse Worker | `docker.io/langfuse/langfuse-worker` | `4.63.0` |
| LiteLLM | `ghcr.io/berriai/litellm` | `v1.106.6` |

Langfuse 两个镜像的 config labels 均为版本 `4.63.0`、revision `88c02eae5d0087422343e0f8d96998d9d98cb992`。本次按精确来源查询 4.73.0 时 API 未返回对应记录，因此选择已有的配对 4.63.0 作为**全新安装候选**。这不证明它等价于已在 WSL 验证的 4.73.0，也不构成已有数据库的降级方案。若必须复现 4.73.0，先通过站点补齐该版本的同步记录，重新读取实际 mirror / manifest / config 并更新整套锁后再打包，不用两个浮动的 `:4` 代替配对版本。

这次将旧模板未锁定的版本具体化：PostgreSQL `17` → `17.11`、Redis `7.4` → `7.4.11`、ClickHouse 旧示例 `24.8` → 候选 `26.3.21.7`，并补齐 MinIO / mc、LiteLLM 的明确版本。它们是新装准备候选，既有数据库升级、降级和数据迁移均需独立验证。仓库模板变更不代表本机服务已升级。

## 联网准备机拉取与封包

在源码目录运行，下列默认清单仅适用于 amd64：

```bash
cp offline/images.example.env offline/images.local.env
bash ops/pull-from-ddn.sh offline/ddn-image-map.amd64.tsv linux/amd64
```

脚本先验证映射表的平台与格式，然后为每个镜像执行带 `--platform` 的 DDN pull，检查实际本地平台，再添加表内的上游别名。拉取地址始终是表中 DDN digest 引用，上游别名仅用于本地关联，不触发上游下载。SWR 返回拒绝 / 404 或平台不符时直接失败；不要删掉 digest 或改为 `latest` 规避。

在正式封包前，用所选镜像完成 MinIO / mc 的 shell / UID / 桶初始化、LiteLLM 回调依赖与真实 loader、Langfuse 迁移 / OTLP / 媒体、真实推理 / 工具链、重启持久化测试。MinIO 的 `cpuv1` tag 也不自动证明其 `/bin/sh` 和 entrypoint 匹配；LiteLLM 容器不自动继承本机 venv 中的依赖。若需构建含回调依赖的最终 LiteLLM 镜像，以此 DDN 镜像作为基底，在联网准备阶段构建 / 无网验证，将最终本地镜像写入 images.local.env 并记录构建来源。

通过后按照[麒麟离线手册](offline-deployment-baseline.md)准备系统包 / wheel / 缓存，再用以下命令导出已经在本地的最终镜像；`BUNDLE`、`PYTHON312` 沿用该手册的准备环境：

```bash
"$PYTHON312" ops/offline_bundle.py images \
  --images-env offline/images.local.env --platform linux/amd64 --bundle "$BUNDLE"
```

生成的 images.lock.json 保留 DDN 来源、RepoDigests 和完整 image ID，images.env 改为离线本地别名。目标机使用该 images.env，`docker compose up` 仍带 `--pull never --no-build`。其余步骤与[验收模板](offline-acceptance-template.md)一致。

## ARM 和可选组件

arm64 必须向 API 显式传 `platform=linux/arm64` 并精确匹配 `source`，读取返回的真实 mirror，核对 registry config 的 `os/architecture`。不要假设 amd64 / arm64 共用 tag、仓库路径或 digest，也不要只改文件名。制作首行为 `# platform=linux/arm64` 的独立 TSV 及对应 images.local.env，再向拉取脚本传入新表和 `linux/arm64`；没有原生发行物的组件必须先解决物料，不能用 QEMU 的启动结果替代麒麟 ARM 实机验收。

Prometheus / Grafana 默认复用既有服务。需要新装时，先选择与配置兼容的版本，再通过相同 API / manifest / config 流程获取 DDN 地址并加入自有映射表、images.local.env 和离线包。DCGM 另需匹配 NVIDIA 驱动 / Container Toolkit / GPU 架构。模板中这些可选 IMAGE 行仍为注释占位，不作为已验证的镜像；本次也未查询到 Grafana 13.3.0 的精确同步记录，不杜撰其 mirror。现有服务器可用版本不等于 DDN 已同步版本。

**交付状态：9 个核心 DDN 镜像地址、manifest digest 和 amd64 元数据已核查；整套麒麟运行兼容性、完整镜像下载 / 离线封包与干净断网部署仍按主手册验收。**
