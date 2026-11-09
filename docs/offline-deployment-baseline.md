# 完全离线部署：物料、准备、实施与验收

更新日期：2026-11-09。目标基线：Ubuntu 24.04、Linux amd64、Python 3.12；Windows 使用 WSL2。arm64 必须单独准备镜像、wheel、系统包和模型运行时，不混用 amd64 的包。

**交付验收以“干净目标机在禁止互联网出口、允许部署内网通信时，完成冷安装、真实推理、Trace / 工具链与 Grafana 展示”为准。文件校验通过、Compose 语法通过和容器启动都只是中间步骤。**

目前已完成的是既有 WSL 环境的真实链路验收；尚未制作并交付一套完整的离线物料，也尚未在干净断网机完成全新 Compose 验收。下表和末尾验收记录用于后续逐项收口，不将复用部署结果当作新装成功。

| 范围 | 当前证据 | 后续发布门槛 |
| --- | --- | --- |
| WSL 复用部署 | LiteLLM 1.106.6、Langfuse 4.73.0、真实 Strata 推理与工具链已验证 | 断网条件下重新检查并记录证据 |
| 看板 | 51 指标面板、78 查询、16 recording rule、9 告警已验证 | 目标 Prometheus / Grafana 配置与在线数据复核 |
| Python | 直接依赖版本已列明 | 生成该目标平台的完整传递依赖锁与 wheelhouse，在空 venv 无网安装成功 |
| 镜像 | 当前运行镜像可作为版本取证参考 | 选定全部 9 个兼容镜像、检查回调依赖和 MinIO shell 后锁 image ID 并导出 |
| 全新离线部署 | **待验收** | 断网冷安装、冷启动、重启与持久化、回滚均通过 |

## 1. 部署范围与固定入口

默认复用现有 VM-02 Prometheus、VM-03 Grafana 和 GPU 推理服务；VM-01 新装网关与 Langfuse。完全离线允许这些机器在内网通信，不允许访问 GitHub、镜像仓库、PyPI、Hugging Face、模型下载站或外部认证服务。

VM-01 可按 16 vCPU、48–64 GiB RAM、300 GiB SSD 起步规划，再根据保留时间与吞吐调整。磁盘预算同时包含镜像 tar、导入层、数据库 / 对象数据、备份和临时空间，模型权重另算。还需核对选定镜像的 CPU 指令集最低要求；架构同为 amd64 不代表所有旧 CPU 都能运行同一 ClickHouse / 推理二进制。

```text
客户端 ── HTTPS :8443 ── Nginx ── LiteLLM :4000 ── 内网推理引擎 /v1
管理员 ── HTTPS :8444 ── Nginx ── Langfuse Web :3000
                                      │ Worker / PostgreSQL / ClickHouse / Redis / MinIO
VM-02 Prometheus ── HTTP :9401 /metrics ── Nginx ── LiteLLM
                 └─ GPU VM 原生指标 / Strata JSON exporter :9402
VM-03 Grafana ── 已有 Prometheus 数据源 ── 51 面板
```

| 访问方向 | 目标 | 配置位置 / 要求 |
| --- | --- | --- |
| 客户端 → VM-01 | `https://VM01:8443/v1` | Nginx TLS、Master / Virtual Key；宿主机不发布 4000 |
| 管理员 → VM-01 | `https://VM01:8444` | `NEXTAUTH_URL` 必须与此入口一致；宿主机不发布 3000 |
| Prometheus → VM-01 | `http://VM01:9401/metrics` | Nginx 修改 `allow` 为真实 Prometheus 来源 IP，并携带指标 Bearer Token |
| 浏览器 → MinIO | 对象存储的实际 HTTPS 入口 | `LANGFUSE_MEDIA_PUBLIC_ENDPOINT` 与实际可达地址一致；9090 HTTP 仅用于内网 API / 排障，HTTPS UI 的媒体访问需避免混合内容 |
| VM-01 → GPU VM | 模型 `/v1/models`、`/v1/chat/completions` | `litellm/config.yaml` 中路由、模型 ID、Key 必须对应目标环境 |
| Prometheus → GPU VM | 原生 `/metrics`、Strata `?format=prometheus`、补充导出器 9402 | 认证文件挂入 Prometheus；仅保留真实部署的 jobs |
| 内部依赖 | PostgreSQL 5432、ClickHouse 8123/9000、Redis 6379、MinIO 9000 | Compose backend 网络，默认不发布数据库端口 |

旧版手册中 4000 / 3000 的宿主机直连命令已被上述 Nginx 入口替代。本机既有 WSL 的 4000 / 3001 / 3002 是另一套复用布局，见 [Windows / WSL 说明](windows-wsl.md)，不要混用两套端口。

## 2. 必须准备的物料清单

### 2.1 系统、Docker 和 Windows / WSL

| 物料 | 必须包含 | 验证方法 |
| --- | --- | --- |
| Linux 基础系统 | 相同发行版 / 架构的安装介质或干净 rootfs，记录 `/etc/os-release` | 目标机 OS、架构和 glibc 与准备机一致 |
| Windows / WSL（仅 Windows） | 支持 WSL2 的 Windows、WSL 离线安装包 / 内核、Ubuntu rootfs tar；提前启用虚拟化与相关功能 | 不依赖 Microsoft Store；`wsl --version`、`wsl -l -v`；必要重启已完成 |
| Docker Engine | 选定的一套包：Ubuntu 的 `docker.io` + `containerd` + `runc`，或官方 `docker-ce` / `docker-ce-cli` / `containerd.io`；含完整系统依赖 | 不混装两套；`docker version`、`docker info`；daemon 可启动 |
| Docker 静态发行版（若选此方式） | 完整官方 tar 中的 daemon、CLI、containerd、shim、runc、docker-proxy、docker-init 和 systemd 单元 | 不能只拷贝一个 `docker` CLI；保留发行版本与校验值 |
| Compose 插件 | 对应架构的完整 `docker-compose` 二进制或发行版插件包 | `docker compose version`；可执行权限与文件 SHA-256 正确 |
| Python | `python3.12`、`python3.12-venv` / ensurepip、pip 及其系统依赖 | 空 venv 能创建，离线安装并执行 `pip check` |
| 基础工具 | `ca-certificates`、`openssl`、`curl`、`tar`、`gzip`、`coreutils`、`iproute2`、防火墙 / iptables 相关依赖；NTP 内网源 / 时区配置 | CA、SAN、时钟、磁盘与网络可验证；不在目标机运行在线 apt update |
| 运维工具 | `promtool` 随现有 Prometheus / 镜像提供；Git 仅准备机打包需要 | 目标机使用源码 tar 即可，不要求 git clone |

本机取证版本为 Docker 29.8.0、Compose v5.7.0、Python 3.12.3；这不是所有目标机的最低版本承诺。目标发行包应选一套已冷装验证的版本并记录。

系统 deb 的完整依赖要在**与目标初始 dpkg 状态相同的干净 VM / rootfs**中解析和下载。联网机器已经安装的依赖可能不会再次下载；只复制 `/var/cache/apt/archives` 或只下载顶层几个 deb，不能证明闭包完整。准备后在克隆的断网目标上执行 `apt-get --no-download install /介质/host/debs/*.deb` 验证；缺包即回到准备机补齐，不允许切回在线仓库兜底。若交付已装好工具的干净 rootfs，则记录其包清单，并验证导入后不需额外 apt 下载。

WSL 可在准备机导出专门的干净发行版，再用 `wsl --import LLM-Offline D:\WSL\LLM-Offline ubuntu24.04-rootfs.tar --version 2` 导入。不要为制作安装包而终止或导出当前正在提供推理的生产发行版；rootfs 内也不应夹带现有业务数据库和密钥。数据目录建议放在 WSL ext4 内，Windows 磁盘用于源码 / 交付介质。

### 2.2 全部容器镜像

**VM-01 共 9 个必需镜像，Nginx 与 MinIO Client 都要打包。** 初次创建桶的 `minio-init` 一样依赖镜像，不能只导出长期运行容器。

| Compose 服务 | 典型来源 | 版本 / 兼容性要求 |
| --- | --- | --- |
| postgres | `postgres` | 固定具体 tag / digest；与 Langfuse 迁移版本配套；本机是 PostgreSQL 17 系列 |
| clickhouse | `clickhouse/clickhouse-server` | 按该 Langfuse 发布版本的参考 Compose 验证；本机 26.3.62.3，旧示例 24.8 不是此次已验收版本 |
| redis | `redis` | 固定具体版本或源 digest；密码 / AOF 配置匹配 |
| minio | `minio/minio` 或经验证的替代发行版 | 本仓库 entrypoint 使用 `sh -c`，所选镜像必须有 `/bin/sh` 并兼容目录权限 / server 命令 |
| minio-init | `minio/mc` | 含 `/bin/sh` 和 `mc`，与服务端兼容；验证创建桶 / 重复启动的幂等行为 |
| langfuse-web | `docker.langfuse.com/langfuse/langfuse` | 与 Worker 使用同一发布版本；本机应用版本 4.73.0 |
| langfuse-worker | `docker.langfuse.com/langfuse/langfuse-worker` | 同上；验证 PostgreSQL / ClickHouse 迁移与异步入库 |
| litellm | `ghcr.io/berriai/litellm` 或自行构建的离线发行镜像 | 本机 1.106.6 是 venv，**尚无已验收的对应容器镜像**；需通过下文回调依赖检查 |
| nginx | `nginx` | 本机缓存 `nginx:1.28-alpine`；正式包锁 image ID，验证 TLS 与 SSE |

若不复用 VM-02 / VM-03，另外打包 Prometheus、Grafana 镜像和它们的完整主配置、持久化目录规划、数据源 / dashboard provisioning。需要 DCGM 时再打包对应架构的 dcgm-exporter 和 NVIDIA Container Toolkit / 驱动物料；Strata 的本机 WSL 监控不要求安装 DCGM。

当前 MinIO 运行的是 `cgr.dev/chainguard/minio`，其启动方式与仓库 MinIO shell 模板不同，**不能因为本机运行正常就直接替换 `MINIO_IMAGE`**。准备机必须验证所选 MinIO / mc 的 entrypoint 和权限，或提交一套相匹配的 Compose 配置后一起打包。

镜像源清单模板：[offline/images.example.env](../offline/images.example.env)。它刻意保留待选版本，不能原样作为发布清单。镜像浮动源即使已下载，也要在导出时锁定 image ID；目标机仅使用本地离线别名，启动命令必须带 `--pull never --no-build`。

### 2.3 Python 包、完整传递依赖和运行缓存

| 文件 / 组件 | 固定直接依赖 | 用途 |
| --- | --- | --- |
| `requirements-observability.txt` | `litellm[proxy]==1.106.6`、`langfuse==4.18.0`、`prometheus-client==0.26.0`、`opentelemetry-exporter-otlp-proto-http==1.45.2`、`PyYAML==6.0.3`、`python-dotenv==1.2.4` | WSL / 宿主 venv、部署与验收脚本、工具示例 |
| `requirements-callback.txt` | Prometheus client 0.26.0、OTEL API / SDK / OTLP HTTP exporter 1.45.2 | 检查 / 补齐所选 LiteLLM 容器内自定义回调依赖，不覆盖该容器的 LiteLLM 版本 |
| `requirements.lock.txt` | 准备机空 venv 无网安装后 `pip freeze` 得到的**完整传递依赖精确版本** | 包括实际解析出的 SDK、HTTP、protobuf、模型 / tokenizer、Proxy 等依赖，不能仅带上面几个顶层 wheel |
| `wheelhouse/*.whl` | 锁文件的全部 wheel，以及 pip / setuptools / wheel 的离线安装工具 | 对应 CPython 3.12、CPU 架构与 libc；glibc wheel 不等于 Alpine musl wheel |
| Tokenizer 缓存 | 实际模型和 LiteLLM 代码路径需要的 tiktoken / Hugging Face 文件 | 在准备机预热实际编码器，并保留缓存路径；禁止首次请求再上网下载 |
| 价格表 | LiteLLM 内置价格表 | `LITELLM_LOCAL_MODEL_COST_MAP=True`；本地模型成本另配真实价格，不从公网取表 |
| 前端与插件 | Langfuse / Grafana 镜像内已构建资源、实际需要的 Grafana 插件 zip / 解压目录 | 不在目标机 npm install / yarn / 在线插件安装；本看板仅依赖内置面板和 Prometheus 数据源 |

`litellm[proxy]` 的依赖不等于 PostgreSQL / Prisma 路径已离线准备好。本项目网关未使用 LiteLLM 自身数据库；WSL 启动会清除 `DATABASE_URL`。若要新增 LiteLLM 数据库功能，需要额外准备其 schema / Prisma 引擎、迁移与对应平台二进制，并重新验收，不能借用 Langfuse 的数据库配置直接启动。

### 2.4 配置、证书、模型与数据

| 物料 | 必备内容 |
| --- | --- |
| 源码快照 | Compose、`.env.example`、`litellm/config.yaml`、`observability_callback.py`、Nginx 配置、exporter、ops、tests、全部 docs 和 Grafana JSON；用同一 Git commit 打包 |
| 镜像锁 | `images.lock.json` 的源引用 / digest / image ID / 架构；`images.env` 的本地别名；`images/images.tar` |
| 运行 `.env` | 在离线目标现场生成；服务密码、SALT、64 位十六进制 ENCRYPTION_KEY、NEXTAUTH_SECRET、Master Key、项目 Keys、模型 Keys、实际 IP / URL |
| TLS 与信任 | server.crt、server.key、完整 CA 链 / 内部根证书，SAN 包含实际 DNS / IP；系统和浏览器信任已导入；单独交付私钥 |
| 模型配置 | `model_name` 是网关别名，`openai/` 后为真实上游 ID；api_base 含 `/v1`，未部署的路由删除；请求列表与验收矩阵一致 |
| Prometheus | 完整主配置的合并方案、scrape 片段、16 recording rule、9 alert、Token 文件及其容器挂载 / UID；不覆盖既有主配置 |
| Grafana | 51 面板 JSON、数据源 UID / 变量、provisioning 或导入步骤、已有 Alertmanager 通知路由 |
| Strata 补充导出器 | Python wheel、`strata_extra.py`、服务单元、STRATA_API_KEY、JSON 地址、监听地址 / 防火墙；默认 9402 |
| GPU 推理运行时 | 复用服务则记录版本 / 模型 ID / 内网地址；新装则带全部模型权重分片、tokenizer、配置、引擎二进制 / 镜像、依赖库和 GPU 驱动 / CUDA / CANN 等对应物料 |
| 模型校验 | 权重和 tokenizer 全部文件的大小 / SHA-256；本地路径、许可文件、上下文 / 量化 / 参数配置；不允许启动时自动下载缺失分片 |
| 数据 | 新装空目录与 UID/GID / 空间规划；迁移另带一致性备份和原镜像锁，禁止用运行中的数据目录简单复制当可恢复备份 |

离线物料包与生产密钥分开交付。`offline_bundle.py` 拒绝包目录中的 `.env`、`.key` 和 `secrets/`；它不替代任意归档内容 / 文本的人工凭据审查。准备源码请用 `git archive`，不把工作目录、`.runtime`、数据和备份一起打包。

## 3. 标准离线包结构

```text
llm-offline-ubuntu24.04-amd64-py312/
├── source.tar                       同一个 Git commit 的源码
├── images/images.tar                9 个核心镜像，另含实际新增组件
├── images.lock.json                 源 digest、image ID、架构、大小
├── images.env                       Compose 使用的本地锁定别名，仅 IMAGE 变量
├── requirements.lock.txt            完整精确版本锁
├── wheelhouse/*.whl                 包括全部传递依赖和安装工具
├── cache/tokenizers/                宿主 / 容器实际使用的预热缓存
├── host/README.md                   OS、架构、系统安装方式与既有依赖声明
├── host/debs/ 或 rootfs/             新目标需要的完整系统物料
├── models/README.md                 复用哪台内网服务，或权重 / 运行时路径与校验
├── models/weights/                  仅部署包承诺新装推理服务时必需
├── config/README.md                 拓扑、端口、配置合并与证书 / 密钥交付方式
├── config/source-revision.txt       git rev-parse HEAD
├── evidence/                        准备机的冷装 / 无网导入 / 依赖检查记录
├── manifest.json                    全物料大小与 SHA-256
└── SHA256SUMS                       含 manifest 自身的校验清单
```

该结构的三个 README 是实质性交付声明：系统包是否齐全、模型是复用还是新装、IP / 证书如何配置。不能只建空 README 就标记物料完整。封包工具检查清单和哈希，不证明 deb / wheel 的依赖闭包或模型能加载；这些由下面的无网安装和运行验收证明。

## 4. 联网准备机操作

以下在与目标平台一致的 **Linux / WSL** 运行。不要在 Windows Python 中直接下载 Linux wheel。用独立准备目录和 venv，不修改既有业务 venv。

### 4.1 选版本、验证镜像、导出

```bash
cd /源码/litellm-langfuse-monitoring
cp offline/images.example.env offline/images.local.env
# 编辑所有 REPLACE；Web / Worker 同版本；先核对该发布的官方 Compose。
editor offline/images.local.env

# 仅在联网准备阶段拉取。实际源如需镜像站，先确认 tag/digest 映射。
python3 - <<'PY'
import subprocess, sys
from pathlib import Path
sys.path.insert(0, "ops")
from offline_bundle import image_env
for image in image_env(Path("offline/images.local.env")).values():
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", image], check=True)
PY
```

若通过 DDN / 其他镜像站获取，参考 `ops/pull-from-ddn.sh` 的 TSV 映射接口。原脚本提到的示例 TSV 并未随本仓库提供，需要自己创建：每行“已验证镜像站引用 TAB 正式源引用”；不要把下载索引页面直接当作 Docker registry。

**打包前先完成三类无网镜像检查**：

1. `minio` / `mc` 镜像内的 `/bin/sh`、server / mc 命令和数据 UID/GID 可用，且能创建桶。镜像启动方式不同就更新并验证整套 Compose，不能只换镜像名。
2. LiteLLM 镜像内能导入 `litellm`、`prometheus_client`、OTEL API / SDK / HTTP exporter，能用真实 `get_instance_fn` 加载自定义回调。
3. 在与目标相同的干净实验环境运行所选 Web / Worker / ClickHouse / PostgreSQL 组合，完成迁移、OTLP v4 入库、媒体上传和重启。正式物料不得仅依据“镜像能 pull”通过。

LiteLLM 回调检查示例，`TESTED_LITELLM_IMAGE` 指向已选定镜像：

```bash
docker run --rm --pull never --network none --entrypoint python \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -e LITELLM_OTEL_V2=false \
  -v "$PWD/litellm:/work:ro" "$TESTED_LITELLM_IMAGE" -c \
  'import prometheus_client, opentelemetry.sdk.trace; from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter; from litellm.proxy.types_utils.utils import get_instance_fn; get_instance_fn(value="observability_callback.observability", config_file_path="/work/config.yaml"); print("CALLBACK_IMPORT_OK")'
```

若镜像缺依赖，在联网准备机按其 Python / libc 下载 `requirements-callback.txt` 的完整 wheel，在该镜像上使用 `pip install --no-index --find-links ...` 构建最终镜像。把最终镜像写回清单；目标机不临时 pip install、不临时 build。某些镜像未使用 `python` 这个可执行名时，先确定其解释器路径并将检查命令一起记录。

导出已在本地的镜像，工具**不会 pull**，缺镜像或架构不符直接失败：

```bash
export BUNDLE=/srv/offline/llm-offline-ubuntu24.04-amd64-py312
mkdir -p "$BUNDLE"
python3 ops/offline_bundle.py images \
  --images-env offline/images.local.env --platform linux/amd64 --bundle "$BUNDLE"
```

工具生成 `llm-offline/<service>:<image-ID前16位>-amd64` 本地别名，保存完整 image ID / 原 repo digest，并导出包含别名的 tar。这样避免 `docker save/load` 后 repo digest 引用未被保留而导致 Compose 尝试访问仓库；目标机仍须验证完整 image ID，不能只看 tag 存在。

### 4.2 下载完整 wheelhouse 并进行空环境安装

```bash
mkdir -p "$BUNDLE/wheelhouse" "$BUNDLE/evidence"
python3.12 -m venv /srv/offline-build-venv
PREP_PY=/srv/offline-build-venv/bin/python

# 联网阶段允许获取包；only-binary 缺 wheel 就失败，不隐式带入源码构建。
"$PREP_PY" -m pip download --only-binary=:all: \
  --dest "$BUNDLE/wheelhouse" -r requirements-observability.txt pip setuptools wheel

# 即使准备机联网，安装阶段也禁用索引；该空 venv 尚未安装应用依赖。
PIP_DISABLE_PIP_VERSION_CHECK=1 "$PREP_PY" -m pip install \
  --no-index --find-links "$BUNDLE/wheelhouse" -r requirements-observability.txt
"$PREP_PY" -m pip check > "$BUNDLE/evidence/pip-check.txt"
"$PREP_PY" -m pip freeze > "$BUNDLE/requirements.lock.txt"
```

还需在无网 / 无 pip 缓存的克隆环境，按这个锁文件再安装一次并运行导入检查。若准备环境已经存在 `/srv/offline-build-venv`，改用新的空目录，避免已有包把缺 wheel 问题遮住。若必须从 sdist 构建，联网阶段先在匹配目标的编译环境构建 wheel，把结果及其全部依赖放入 wheelhouse；目标机不用编译器联网补包。

### 4.3 预热 Tokenizer、模型与宿主物料

```bash
mkdir -p "$BUNDLE/cache/tokenizers"
export TIKTOKEN_CACHE_DIR="$BUNDLE/cache/tokenizers"
"$PREP_PY" - <<'PY'
import tiktoken
for name in ("cl100k_base", "o200k_base"):
    tiktoken.get_encoding(name).encode("offline tokenizer readiness")
PY
```

上面只覆盖两个常见编码器；**实际模型 / 自定义编码器需要另行预热**。使用 Hugging Face 的场景应保存模型和 tokenizer 的完整本地文件与对应 cache；目标运行时设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 并指向本地目录，禁止自动远程下载。LiteLLM 容器也要放入 / 挂载适用于其自身环境的缓存，例如只读挂到 `/opt/tokenizer-cache`，设置 `TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache`；宿主缓存不自动出现在容器里。

按 2.1 准备完整系统物料。若 GPU 服务也要新装，按 2.4 准备所有权重、运行时与驱动，并在断网条件验证模型加载；若复用，在 `models/README.md` 明确模型服务内网地址 / 版本 / ID、认证和负责人。联网准备机上的每一个额外下载动作都要进入物料清单，不能留给目标机首启。

### 4.4 打包同一版源码并封包

```bash
mkdir -p "$BUNDLE/host" "$BUNDLE/models" "$BUNDLE/config"
# 先完成相应 README 和实际系统 / 模型物料，不用空文件代替。
editor "$BUNDLE/host/README.md" "$BUNDLE/models/README.md" "$BUNDLE/config/README.md"
git rev-parse HEAD > "$BUNDLE/config/source-revision.txt"
git archive --format=tar -o "$BUNDLE/source.tar" HEAD

python3 ops/offline_bundle.py seal \
  --bundle "$BUNDLE" --platform linux/amd64 --python-version 3.12
python3 ops/offline_bundle.py verify --bundle "$BUNDLE"
```

`git archive` 只包含已提交文件，打包前要求 Git 工作区干净且当前提交就是批准版本。封包后不要再改配置模板或增加包；如有改动重新封包。输出的 `checksum_file_sha256` 通过独立受控渠道记录，避免传输中清单与文件一起被错误替换。SHA-256 用于完整性检查，不等价于发布者数字签名。

将整个目录复制到离线介质；运行密钥和 TLS 私钥由单独渠道到目标机。目标验收报告写到包目录之外，保持封包内容不变。

## 5. 完全离线目标机实施

### 5.1 先阻断外网，再导入和安装

选择独立干净实验机 / WSL 发行版进行首次验收；不要清空当前生产 Docker、pip 缓存或数据卷来模拟“干净”。外网出口从冷安装开始就阻断，只允许部署矩阵中的内网地址、DNS、NTP 和管理通道。Docker 发布端口与出站策略可能绕过主机 ufw，需以实际 Docker 网桥 / 上游网络策略为准。

留存出口 ACL / 路由、测试时间、内网白名单与外网拒绝日志。单独 curl 几个公网域名失败，不足以证明完全隔离；由网络策略和请求审计共同证明。不要把 Docker backend 直接设为 `internal: true` 后还期待它能访问外部 GPU VM；应按内网访问需求实施出口策略。

```bash
export BUNDLE=/介质/llm-offline-ubuntu24.04-amd64-py312
# 先使用已离线安装的 python3 校验；不需要 PyYAML、dotenv 或网络。
python3 /可信交付目录/ops/offline_bundle.py verify --bundle "$BUNDLE" \
  --report /var/tmp/offline-artifacts-before-load.json
cd "$BUNDLE"
sha256sum -c SHA256SUMS
docker load -i images/images.tar
python3 /可信交付目录/ops/offline_bundle.py verify --bundle "$BUNDLE" \
  --loaded-images --report /var/tmp/offline-artifacts-after-load.json
```

校验工具本身应随受控交付目录预先提供；首次解压源码前先以独立记录的 SHA256SUMS 摘要确认来源。上述路径不是联网下载地址，不能先 git clone 再开始“离线验收”。

安装系统包 / 导入 rootfs 后，使用无索引方式安装验收 venv：

```bash
python3.12 -m venv /opt/llm-observability/.venv
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 /opt/llm-observability/.venv/bin/python \
  -m pip install --no-index --find-links "$BUNDLE/wheelhouse" -r "$BUNDLE/requirements.lock.txt"
/opt/llm-observability/.venv/bin/python -m pip check
```

### 5.2 解压配置、生成现场密钥与证书

```bash
mkdir -p /opt/llm-gateway
tar -xf "$BUNDLE/source.tar" -C /opt/llm-gateway
cd /opt/llm-gateway
cp .env.example .env
chmod 600 .env
mkdir -p nginx/certs data/nginx-logs
chmod 700 nginx/certs
# 拷贝企业 CA 签发的 server.crt / server.key，SAN 包含实际 IP / DNS。
chmod 600 nginx/certs/server.key
editor .env litellm/config.yaml nginx/nginx.conf
```

必须完成以下配置，逐项记录到现场配置清单：

- `HOST_IP` 是 VM-01 实际地址；`NEXTAUTH_URL=https://VM01:8444`，对象存储公开 endpoint 必须从实际浏览器可达。
- 使用 `openssl rand -hex 24` 生成数据库 / Redis / MinIO 密码，避免拼入 DATABASE_URL 时需要转义的 `@:/#` 等字符；`ENCRYPTION_KEY` 用 `openssl rand -hex 32`，SALT / NEXTAUTH_SECRET 另行生成。迁移既有数据时保留原加密键。
- `LITELLM_OTEL_V2=false`、`LITELLM_LOCAL_MODEL_COST_MAP=True`，回调只能是 `prometheus` 与 `observability_callback.observability`；不要同时启用 `langfuse_otel` 重复上报。项目 Keys 允许在首次创建项目前为空，创建后必须补齐。
- `LANGFUSE_HOST=http://langfuse-web:3000` 是网关容器内部地址；宿主验收客户端使用 8444，不能把内部 DNS 交给外部客户端。模型 Keys / Master Key 在目标现场填写，删除未部署的模型路由。
- Nginx :9401 的 `allow` 改为真实 VM-02 来源；证书路径、SSE `proxy_buffering off`、长请求超时、内网 DNS / 时钟正确。
- 选择是否配置本地模型实际费用。未配价格必须标为 unconfigured；不得把 0 当成已验收的免费成本。
- 若目标 LiteLLM 使用 tokenizer 缓存，在本地 Compose override 增加只读挂载及 `TIKTOKEN_CACHE_DIR`；override 也记录 SHA-256。生产配置修改后不回写已封包的介质。

**媒体访问需要额外核查 HTTPS**：当前模板的 MinIO 主机映射 9090 为 HTTP，而 Langfuse UI 为 HTTPS。实际需要图片 / 附件时，应通过内部 HTTPS 对象存储或准备机已验证的 Nginx TLS 入口提供媒体地址，并配置允许 Langfuse UI 来源的 CORS；不能把浏览器 mixed-content / CORS 失败当作离线网络故障忽略。仓库默认 Nginx 未代理 MinIO，若采用下列独立端口方案，把它连同本地 override 一起交付并验收：

```nginx
# 加入 nginx.conf 的 http 内，使用与入口匹配的证书。
server {
  listen 9443 ssl;
  ssl_certificate /etc/nginx/certs/server.crt;
  ssl_certificate_key /etc/nginx/certs/server.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  location / {
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_pass http://minio:9000;
  }
}
```

```yaml
# docker-compose.local.yml 的必要端口补充；其余配置由主文件合并。
services:
  nginx:
    ports:
      - "${HOST_IP}:9443:9443"
```

此时现场设置 `LANGFUSE_MEDIA_PUBLIC_ENDPOINT=https://VM01:9443`，放行实际浏览器的内网访问并信任 CA。CORS 按所选 MinIO 发布版配置，保留上传 / 读取 / OPTIONS 的真实浏览器证据；检查 Nginx 全局请求体上限与附件大小。服务内部 event endpoint 仍为 `http://minio:9000`，不要改成浏览器入口。

Compose 始终使用两个 env 文件：先现场 `.env`，再用包的 `images.env` 覆盖镜像引用。`env_file: [.env]` 仍负责注入现场运行密钥。

```bash
docker compose --env-file .env --env-file "$BUNDLE/images.env" config -q
docker compose --env-file .env --env-file "$BUNDLE/images.env" config --images
```

不要将完整 `docker compose config` 输出存入公开报告，它会展开密码；`config -q` 和 `config --images` 足以检查语法 / 镜像列表。若有本地 override，后续每条 Compose 命令都使用相同 `-f docker-compose.yml -f docker-compose.local.yml` 参数。

### 5.3 启动 Langfuse、创建项目 Keys、加载网关

```bash
docker compose --env-file .env --env-file "$BUNDLE/images.env" up -d --pull never --no-build
docker compose --env-file .env --env-file "$BUNDLE/images.env" ps -a
docker compose --env-file .env --env-file "$BUNDLE/images.env" exec nginx nginx -t
```

首次全部启动是为了让 Nginx 能解析网关 / Langfuse 的内部 DNS 并提供 UI。网关回调懒初始化，项目 Keys 尚未补齐时**不要发送推理请求**。访问 `https://VM01:8444`，创建管理员、Organization / Project 和项目 Keys；没有预置默认管理员。离线采用本地账户 / 内部认证服务，不依赖公网 OAuth、邮件验证码或云端账户激活。

将项目 Keys 写入现场 `.env`，确认 `langfuse-worker` 正常消费，重新创建网关注入新环境：

```bash
docker compose --env-file .env --env-file "$BUNDLE/images.env" up -d \
  --pull never --no-build --no-deps --force-recreate litellm
docker compose --env-file .env --env-file "$BUNDLE/images.env" exec nginx nginx -t
docker compose --env-file .env --env-file "$BUNDLE/images.env" exec nginx nginx -s reload
curl --cacert /证书/internal-ca.crt -fsS https://VM01:8444/api/public/health
```

Nginx 当前使用静态 `proxy_pass` 主机名，重新创建网关后需 reload，重新解析可能变化的容器 IP；否则 UI 正常时 API 仍可能返回 502。后续重新创建其他上游服务时同样处理。

检查 minio-init 退出码为 0、桶 `langfuse` 存在，PostgreSQL / Redis / ClickHouse 健康，Web / Worker 没有迁移或对象存储错误。迁移失败不能跳过健康检查继续称为成功。

### 5.4 合并 Prometheus、导入 Grafana、部署补充 exporter

1. 在 VM-02 合并 [scrape 配置](../prometheus/litellm-scrape.yml)，保留 `job_name: litellm`；修改地址、认证文件、GPU deployment 标签，删除未部署的引擎 job。Strata 必须使用 `/metrics?format=prometheus`，默认 JSON 不能按 Prometheus 文本抓取。
2. 把 `recording-rules.yml` 和 `alerts.yml` 放入已有规则目录，并在主配置 `rule_files` 中加入；不要只加载告警而漏掉 `llm:*` recording rule。
3. 凭据文件的路径、宿主权限和容器内 UID 可读性一致；推荐独立 secrets 目录，不将 Token 放入 PromQL。`promtool check rules` 与 `promtool check config` 通过后按现有方式 HUP / reload。
4. 在 VM-03 导入 `grafana/dashboards/litellm-gpu-overview.json`，绑定已有 Prometheus 数据源；若 provisioning，保持 UID `litellm-gpu-overview`，确认自动加载目录 / 文件权限。
5. 在可访问 Strata JSON 的主机部署 `exporters/strata_extra.py`，用离线 venv 的 prometheus-client 运行；设置 STRATA_METRICS_URL、STRATA_API_KEY、STRATA_EXPORTER_HOST 和端口 9402，注册 systemd 自动启动。仅向 Prometheus 开放。不是 Strata 的环境无需部署它。

补充 exporter 的环境文件示例（现场私有文件）：

```dotenv
STRATA_METRICS_URL=http://GPU_VM:8008/metrics
STRATA_API_KEY=现场密钥
STRATA_EXPORTER_HOST=该主机可被Prometheus访问的地址
STRATA_EXPORTER_PORT=9402
STRATA_POLL_INTERVAL=3
```

systemd 的 ExecStart 使用目标 Python 的绝对路径和 exporter 源码绝对路径，EnvironmentFile 指向该私有文件。先以前台方式确认 9402 `/metrics` 中 `strata_extra_up=1`，再 enable 服务；不要原样运行本机路径固定的 `ops/deploy_wsl.py` 来覆盖多 VM 的现有配置。

**Grafana 公式离线可读**：每个面板的信息说明已包含公式 / 单位 / 实际 PromQL，另随包提供 [完整公式文档](grafana-formulas.md)。看板顶部 GitHub 链接在断网时不可访问，不影响面板与嵌入公式；需要文档入口时改为内部文档站。

## 6. 完全离线验收步骤与通过标准

使用 [离线验收记录模板](offline-acceptance-template.md) 逐项保存证据。最终状态由实际结果填写，物料脚本始终输出 `deployment_accepted: false`，避免把哈希检查冒充全链路验收。

### 6.1 包与系统

- 目标机在开始安装前已禁止外网，内网白名单与出口日志有记录；使用的 OS / CPU / Python ABI / libc 与物料一致。
- `SHA256SUMS` 全部通过；加载后每个离线镜像的完整 image ID 和架构与锁文件一致。
- 空 venv 仅用本地 wheel 安装成功，`pip check` 通过；Tokenizer 无网初始化成功；容器内回调无网加载通过。
- 容器启动统一使用 `--pull never --no-build`，日志 / 网络审计没有成功访问外部仓库、PyPI、Tokenizer / 模型站点。

### 6.2 真实推理、Langfuse 与工具链

先对部署矩阵里的**每个模型**完成一次非流式、一次带 usage 的流式请求，并等至少两次 scrape / recording rule 更新。记录真实请求 ID，不把模型列表或 readiness 200 当作推理成功。

宿主验收用独立私有 `validation.env`，只含现有 Master Key、项目 Public / Secret Key，以及宿主可达的 Langfuse 地址；不用容器内部 `langfuse-web` 地址。导入企业 CA 后，运行：

```bash
PY=/opt/llm-observability/.venv/bin/python
"$PY" ops/verify_runtime.py \
  --env-file /私有目录/validation.env \
  --gateway https://VM01:8443 --langfuse https://VM01:8444 \
  --prometheus http://VM02:9090 --ca-file /证书/internal-ca.crt \
  --model qwen-strata --required-job litellm --required-job strata --required-job strata-extra \
  --exercise --output /验收目录/runtime-validation.json
```

`--model` 按实际别名；`--required-job` 重复填写本次必须存在且 UP 的 job。非 Strata 环境还需用 `--critical-panel` 替换默认 Strata 最近请求 / 缓存面板，例如 GPU 利用率、显存占用率、请求运行 / 排队等本次承诺的关键面板；不能为了让检查通过而略掉部署矩阵内必须有的 GPU / Token / 时延指标。

工具执行验收使用真实模型与本地 add_numbers，客户端和网关必须在同一项目：

```bash
export SSL_CERT_FILE=/证书/internal-ca.crt
export REQUESTS_CA_BUNDLE=/证书/internal-ca.crt
export GATEWAY_URL=https://VM01:8443
export GATEWAY_MODEL=qwen-strata
# validation.env 中 LANGFUSE_HOST 使用 https://VM01:8444。
"$PY" examples/traced_tool_agent.py --env-file /私有目录/validation.env
# 将上一步输出的真实 trace_id 加到上述 verify_runtime.py 参数中：
# --trace-id 实际traceID
```

必须看到同一 Trace 下的一个 AGENT、真实执行的 TOOL（17+25=42）、两次 GENERATION，input / output / parent / Session / User 齐全。仅 `tool_calls` 文本不算执行链通过。Langfuse v4 读 `/api/public/v2/observations`，字段组包含 `time,metrics`；旧 observations / traces 接口的 404 不是入库失败的证明。

### 6.3 Grafana 指标与公式

- 51 面板已导入、数据源有效；78 条 PromQL 查询全部执行成功，实际部署指标的关键序列为有限数值。未部署的可选引擎可无数据，但必需 job 不得缺失。
- 网关有请求与输入 / 输出 Token 增量，真实流式有 TTFT / TPOT；Langfuse TTFT 与回调 metadata 一致，usage 互斥细分相加等于 raw total。
- 公式已核查：`TPOT=(last-first)/(N−1)`；P95 来自合并 bucket；网关 Token/s 和 QPS 是固定 `[5m]`；VRAM 为 `100×used/total`；前缀 / MTP 用 Token 加权；MiB/s 乘 1048576；SSE 帧不当作 Token。
- Strata context occupancy 与物理 KV 分开；Strata ITL 是按间隔数加权的平均值估计。空闲 / 冷启没有分位样本时保持无值，不强制补 0 秒。
- 断开一个测试 exporter 验证 UP / 告警与恢复，只在实验环境执行；不修改现有 Alertmanager receiver。异常 / 中断请求在 Langfuse 可见，OTLP 失败 / 本地构造错误有记录。

### 6.4 冷启动、持久化与恢复

- 外网仍受限时重启测试主机 / WSL，确认 Docker / systemd 自动启动、模型可用、原有 Trace / 用户 / 仪表盘 / 数据不丢失；WSL 自动启动策略单独记录，不能把 Linux enable 当成 Windows 开机会自动启动 WSL。
- 用新的请求再次验证推理、Langfuse、Token / TTFT / TPOT、GPU、缓存和工具链；没有首次请求下载依赖的等待或外网回退。
- 实验环境执行一致性备份 / 恢复，使用原镜像锁、配置和加密键；确认历史 Trace 可查、模型路由可用。
- 记录包版本 / checksum 摘要、实际镜像 ID、平台、CA、内网拓扑、请求 / Trace ID、全部报告和人工页面检查结果，才可签署“完全离线部署通过”。

## 7. 备份、回滚和常见阻断

迁移 / 升级前先保存原镜像锁、配置、证书、加密键和一致性数据备份。停写后再做 PostgreSQL / ClickHouse / Redis / MinIO 的一致性快照或使用各自原生备份。恢复时按原版本先依赖后应用，核对 UID/GID。不要在没有备份时执行 `down -v`、删除 data 或修改 ENCRYPTION_KEY。

| 阻断 | 处理与重新验收 |
| --- | --- |
| 镜像 tag 存在但启动仍 pull | 统一使用生成的本地 images.env，完整 image ID 对比通过，所有 up 带 `--pull never --no-build` |
| MinIO 报 sh 不存在 / 权限失败 | 发行版与 entrypoint 不兼容；联网准备机改为经过验证的镜像 / Compose，重新封包并冷装 |
| 回调 import 缺 OTEL / Prometheus | 最终 LiteLLM 镜像未包含回调依赖；按其 Python / libc 构建，目标机不联网补包 |
| 首次请求尝试下载编码器 | 缓存不完整 / 未挂入实际运行容器；准备机预热实际路径，再以 network none 重测 |
| pip 缺包 / no matching distribution | wheelhouse 闭包、CPython、CPU 或 musl / glibc 不匹配；回准备机补齐，重新空 venv 验证 |
| Langfuse UI 正常但没有 Trace | 检查 Worker、桶、OTLP v4 header、项目 Keys、LANGFUSE_HOST、回调错误指标；读取 v2 API |
| 8443 / 8444 TLS 失败 | SAN、CA 链、时钟、Nginx 配置、客户端信任；验收不以 `-k` 跳过证书验证 |
| Prometheus 403 / 401 | 分别核对 Nginx allow 的真实来源与 Bearer Token / 容器文件权限 |
| Grafana 指标空白 | 先验证目标 UP、16 条规则、模型标签与数据源；可选 / 无流量指标和真正采集失败分开判定 |
| 离线登录 / 媒体依赖公网 | 替换为本地账号、内部 DNS / NTP / CA / 对象存储，验证实际浏览器访问 |

官方参考用于准备机选版本审计，实施时所需配置和说明必须已经在包中：

- [Langfuse Compose 部署](https://langfuse.com/self-hosting/deployment/docker-compose)
- [Langfuse 官方 Compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)（必须取选定发布对应内容，不能把最新 main 当兼容性锁）
- [LiteLLM Prometheus](https://docs.litellm.ai/docs/proxy/prometheus)
- [本仓库的实际配置修复与边界](observability-findings.md)

**发布状态：本手册、物料锁定 / 校验工具和验收流程已提供；完整离线物料制作与干净断网机全链路验收仍需按本手册执行并保存记录。**
