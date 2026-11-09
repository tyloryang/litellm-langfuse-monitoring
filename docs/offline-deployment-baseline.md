# 完全离线部署：物料、准备、实施与验收

更新日期：2026-11-09。**离线目标为麒麟 V10 服务器环境，按类似 CentOS 7.9 的 RPM / YUM 运维方式准备，不再以 Ubuntu / WSL 为离线安装基线。** 现场需记录具体产品、SP / Build、内核、glibc 和 CPU 架构；“类似 CentOS 7.9”不代表能直接安装任意 el7 RPM，也不代表 glibc 一定是 2.17。

应用及验收工具使用 Python 3.12；麒麟自带的系统 Python 保留。`x86_64` 对应镜像平台 `linux/amd64`，`aarch64` 对应 `linux/arm64`，两套镜像、wheel、RPM 和 GPU 运行时分开准备。其他架构须单独适配，本仓库物料工具目前只接受这两个平台。

**交付验收以“干净目标机在禁止互联网出口、允许部署内网通信时，完成冷安装、真实推理、Trace / 工具链与 Grafana 展示”为准。文件校验通过、Compose 语法通过和容器启动都只是中间步骤。**

目前已完成的是既有 Ubuntu / WSL 环境的真实链路验收；**尚未在麒麟 V10 完成适配实测，也未制作并交付完整离线物料**。下表和末尾验收记录用于后续逐项收口，不将 WSL 复用部署结果当作麒麟新装成功。

| 范围 | 当前证据 | 后续发布门槛 |
| --- | --- | --- |
| 历史 WSL 复用部署 | LiteLLM 1.106.6、Langfuse 4.73.0、真实 Strata 推理与工具链已验证 | 在目标麒麟 SP / 架构上重新验证并记录证据 |
| 麒麟系统 | 已明确 RPM / YUM 交付路线 | 记录实际 SP / Build、内核 / glibc / 架构，锁定匹配的系统与 Docker 发行包 |
| 看板 | 59 指标面板、94 查询、16 recording rule、9 告警；含来源 IP 用量 | 目标 Prometheus / Grafana 配置与在线数据复核 |
| Python | 直接依赖版本已列明 | 生成该目标平台的完整传递依赖锁与 wheelhouse，在空 venv 无网安装成功 |
| 镜像 | 9 个 DDN 来源、manifest digest 与 amd64 配置元数据已核查 | 验证整套候选版本、回调依赖与 MinIO shell 后锁 image ID 并导出 |
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
VM-03 Grafana ── 已有 Prometheus 数据源 ── 59 面板
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

### 2.1 麒麟 V10 系统、Docker 与 RPM 依赖

先在目标机、联网准备机和断网验收机分别执行下列只读检查。准备机必须是同产品、同 SP / Build、同架构的麒麟环境，至少与目标初始 RPM 基线匹配；Ubuntu WSL 可以编辑文档，但不能用其 apt 包、Python 二进制或 wheel 下载结果代替麒麟发行物。

```bash
cat /etc/os-release
if [ -f /etc/kylin-release ]; then cat /etc/kylin-release; fi
if [ -f /etc/.productinfo ]; then cat /etc/.productinfo; fi
uname -m
uname -r
getconf GNU_LIBC_VERSION
rpm -q kylin-release glibc systemd openssl libseccomp selinux-policy container-selinux
rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort
getenforce
findmnt -T /var/lib
if command -v docker >/dev/null 2>&1; then
  docker version
  docker info
  docker compose version
fi
```

某个候选包尚未安装时 `rpm -q` 可返回非零，记录缺失后由物料清单补齐；不能据此跳过其依赖。记录命令的输出放在私有验收目录，特别核对 Docker context 指向当前目标机。

| 物料 | 必须包含 | 验证方法 |
| --- | --- | --- |
| 麒麟系统 | 实际麒麟 V10 产品 / SP / Build / 架构的 ISO、已启用仓库的版本快照、RPM 签名公钥及可信指纹 | `/etc/os-release`、`/etc/.productinfo`、内核和初始 RPM 清单一致 |
| Docker Engine | 优先选该麒麟版本适配的 `docker` / `docker-engine` 及配套 containerd / runc；或已实测兼容的 `docker-ce` / `docker-ce-cli` / `containerd.io` 组合，包含完整 RPM 闭包 | 记录精确 NEVRA；不混装两套、不直接装最新 el9 / el10 包；daemon 和容器实际可运行 |
| Docker 静态发行版（若选此方式） | 完整官方 tar 中的 daemon、CLI、containerd、shim、runc、docker-proxy、docker-init 和 systemd 单元 | 不能只拷贝一个 `docker` CLI；保留发行版本与校验值 |
| Docker 系统依赖 | 按选定包解析 `libseccomp`、`libgcc` / `libstdc++`、`iptables` 及 netfilter 库、`container-selinux` / SELinux policy / `policycoreutils`、`audit` 等；仅使用 device-mapper 路线时再带相应 device-mapper / LVM 依赖 | 以同 SP 的 RPM 依赖解析为准，不把候选列表当作完整闭包；SELinux Enforcing 时启动与读写成功 |
| Compose 插件 | 对应架构的完整 Compose V2 或后续经验证的插件二进制 / RPM；保留 `LICENSE` / 校验值 | `docker compose version`；支持双 `--env-file`、条件 depends_on、`--pull never` 与 `--no-build`；不能用旧 V1 命令替代 |
| Python 3.12 | 适配目标麒麟的独立前缀发行包 / 自制 RPM、ensurepip / venv、pip、配套 OpenSSL 和全部共享库 | 示例前缀 `/opt/llm-python`；`ssl`、`venv`、原生扩展导入和空 venv 无网安装成功 |
| Python 系统库 | 按实际构建解析 zlib、bzip2-libs、xz-libs、libffi、sqlite-libs、readline、ncurses-libs、libuuid、libgcc / libstdc++ 等 | 用 `ldd` / 导入测试与 RPM 闭包共同检查；不能只复制 Python 可执行文件 |
| 基础工具 | `ca-certificates`、`openssl`、`curl`、`tar`、`gzip`、`coreutils`、`iproute`、`util-linux`、`firewalld` / iptables、`chrony`，XFS 时需 `xfsprogs` | 具体包名以该 SP 为准；内网 NTP / 时区、CA、磁盘、网络可验证 |
| 准备机工具 | YUM 路线的 `yum-utils`（含 yumdownloader）或 DNF 路线的 `dnf-plugins-core`，需要本地 repo 时加 `createrepo` / `createrepo_c` | 先在联网准备机装齐；这些工具自身若需在目标使用，也必须进入离线闭包 |
| 运维工具 | `promtool` 随现有 Prometheus / 镜像提供；Git 仅准备机打包需要 | 目标机使用源码 tar 即可，不要求 git clone |

已有 Docker / Podman / containerd 时先核对版本、服务和现有业务。`podman-docker` 提供的兼容 CLI 不自动等价于本仓库要求的 Docker Engine；不要直接卸载现有容器运行时、替换 runc 或改动其数据目录。能满足本次配置和镜像运行验收的既有 Docker 可复用，不能满足时先准备独立测试环境和迁移方案。

历史 WSL 的 Docker 29.8.0、Compose v5.7.0、Python 3.12.3 仅是取证版本，**不能据此认定麒麟或 CentOS 7.9 内核可运行同样的最新版**。Docker 当前 CentOS 安装说明面向其列出的受维护版本，不是麒麟 V10 / CentOS 7.9 的支持承诺。Docker、containerd、runc、Compose 与宿主内核 / systemd / seccomp / cgroup 必须成套验证；发行商较旧的 Docker 也需实测能否运行本次选定的新镜像。

**RPM 闭包在 4.1 准备、5.1 断网安装。** 准备机已装依赖可能掩盖缺包问题，必须下载全部依赖并在与目标初始 RPM 状态一致的干净麒麟克隆机验证。不能从 CentOS 7.9 任意镜像站拼包，也不能用 `rpm --nodeps`、`--force` 或替换系统 glibc 的方式让依赖检查“通过”。

**Python 兼容门槛**：Python 3.12 的 `ssl` 模块要求 OpenSSL 1.1.1 或更新；应选厂商仍维护且已验证的版本。若现场是 1.0.2，只给应用前缀配套独立 OpenSSL，保留系统 OpenSSL / Python / YUM。系统提供 Python 2.7、3.6 等并不满足这些运维脚本。交付 Python 时包含其动态库 / RUNPATH 配置；在目标执行 `import ssl, venv, sqlite3, bz2, lzma, ctypes` 检查，不能仅看 `python --version`。

若需自建 Python 或 wheel，编译工作放在同版麒麟的联网准备机：准备支持 C11 的编译器、make、pkgconfig、patch、perl 及对应 zlib / bzip2 / xz / libffi / OpenSSL / readline / sqlite / ncurses / libuuid 的 devel 包；原生 wheel 需要的 C++ / Rust 工具链也在准备机备齐。将最终运行时和运行库打包，目标机不临时联网编译。若无法准备能在目标 glibc 上安装的完整依赖闭包，将该发行方案标为不通过；调整构建 / 版本组合并在同版麒麟重新验证后才能交付。

### 2.2 全部容器镜像

**VM-01 共 9 个必需镜像，Nginx 与 MinIO Client 都要打包。** 初次创建桶的 `minio-init` 一样依赖镜像，不能只导出长期运行容器。

以下列出上游项目标识用于归属核对；**实际拉取统一使用渡渡鸟 API 返回的 SWR 镜像地址**，完整地址与 digest 见 [DDN 镜像清单与版本说明](ddn-images.md)。`docker.aityp.com` 是索引 / API 站点，实际 registry 是 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/…`。

| Compose 服务 | 上游项目标识 | 版本 / 兼容性要求 |
| --- | --- | --- |
| postgres | `postgres` | 固定具体 tag / digest；与 Langfuse 迁移版本配套；本机是 PostgreSQL 17 系列 |
| clickhouse | `clickhouse/clickhouse-server` | 按该 Langfuse 发布版本的参考 Compose 验证；本机 26.3.62.3，旧示例 24.8 不是此次已验收版本 |
| redis | `redis` | 固定具体版本或源 digest；密码 / AOF 配置匹配 |
| minio | `minio/minio` 或经验证的替代发行版 | 本仓库 entrypoint 使用 `sh -c`，所选镜像必须有 `/bin/sh` 并兼容目录权限 / server 命令 |
| minio-init | `minio/mc` | 含 `/bin/sh` 和 `mc`，与服务端兼容；验证创建桶 / 重复启动的幂等行为 |
| langfuse-web | `docker.io/langfuse/langfuse` | DDN 新装候选 4.63.0，与 Worker 同版本 / revision；本机既有 4.73.0 不属于同一发行物 |
| langfuse-worker | `docker.io/langfuse/langfuse-worker` | 同上；验证 PostgreSQL / ClickHouse 迁移与异步入库 |
| litellm | `ghcr.io/berriai/litellm` 或自行构建的离线发行镜像 | 本机 1.106.6 是 venv，**尚无已验收的对应容器镜像**；需通过下文回调依赖检查 |
| nginx | `nginx` | 本机缓存 `nginx:1.28-alpine`；正式包锁 image ID，验证 TLS 与 SSE |

若不复用 VM-02 / VM-03，另外打包 Prometheus、Grafana 镜像和它们的完整主配置、持久化目录规划、数据源 / dashboard provisioning。需要 DCGM 时再打包对应架构的 dcgm-exporter 和 NVIDIA Container Toolkit / 驱动物料；Strata 的本机 WSL 监控不要求安装 DCGM。

当前 MinIO 运行的是 `cgr.dev/chainguard/minio`，其启动方式与仓库 MinIO shell 模板不同，**不能因为本机运行正常就直接替换 `MINIO_IMAGE`**。准备机必须验证所选 MinIO / mc 的 entrypoint 和权限，或提交一套相匹配的 Compose 配置后一起打包。

镜像源清单模板：[offline/images.example.env](../offline/images.example.env)。9 个核心镜像已写入具体 tag 与 DDN manifest digest，当前只核查了 `linux/amd64` 元数据，尚未验收整套容器运行。Web / Worker 4.63.0 是站点现有的配对候选，本次未查到 4.73.0 的精确同步记录；**已有 4.73.0 数据不能直接套用此清单降级启动**。复用旧组件时保留其实际版本和数据，迁移方案另验收；新装先按下文测试再作为正式物料。离线目标仍使用本地锁定别名，启动命令必须带 `--pull never --no-build`。

### 2.3 Python 包、完整传递依赖和运行缓存

| 文件 / 组件 | 固定直接依赖 | 用途 |
| --- | --- | --- |
| `requirements-observability.txt` | `litellm[proxy]==1.106.6`、`langfuse==4.18.0`、`prometheus-client==0.26.0`、`opentelemetry-exporter-otlp-proto-http==1.45.2`、`PyYAML==6.0.3`、`python-dotenv==1.2.4` | 独立宿主 venv、验收脚本、工具示例；版本源自 WSL，须在麒麟重新解析 / 安装验证 |
| `requirements-callback.txt` | Prometheus client 0.26.0、OTEL API / SDK / OTLP HTTP exporter 1.45.2 | 检查 / 补齐所选 LiteLLM 容器内自定义回调依赖，不覆盖该容器的 LiteLLM 版本 |
| `requirements.lock.txt` | 准备机空 venv 无网安装后 `pip freeze` 得到的**完整传递依赖精确版本** | 包括实际解析出的 SDK、HTTP、protobuf、模型 / tokenizer、Proxy 等依赖，不能仅带上面几个顶层 wheel |
| `wheelhouse/*.whl` | 锁文件的全部 wheel，以及 pip / setuptools / wheel 的离线安装工具 | 对应 CPython 3.12、CPU 架构与 libc；glibc wheel 不等于 Alpine musl wheel |
| Tokenizer 缓存 | 实际模型和 LiteLLM 代码路径需要的 tiktoken / Hugging Face 文件 | 在准备机预热实际编码器，并保留缓存路径；禁止首次请求再上网下载 |
| 价格表 | LiteLLM 内置价格表 | `LITELLM_LOCAL_MODEL_COST_MAP=True`；本地模型成本另配真实价格，不从公网取表 |
| 前端与插件 | Langfuse / Grafana 镜像内已构建资源、实际需要的 Grafana 插件 zip / 解压目录 | 不在目标机 npm install / yarn / 在线插件安装；本看板仅依赖内置面板和 Prometheus 数据源 |

如果现场 `getconf GNU_LIBC_VERSION` 为 2.17，宿主不能安装要求 `manylinux_2_28` / `manylinux_2_34` 的 wheel；如果现场为 2.28，也不能接受要求 2.34 的 wheel。CPython `cp312`、`abi3`、架构、glibc / musl、`GLIBCXX` 等限制需同时满足。在实际麒麟准备机用 `pip debug --verbose` 留存兼容 tag；不通过改 wheel 文件名、强行指定 `--platform` 或复制 Ubuntu venv 绕过检查。容器有自己的 libc，仍共享宿主内核；宿主 wheelhouse 与每种容器环境的 wheelhouse 分开锁定和验证。

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
| Grafana | 59 面板 JSON、数据源 UID / 变量、provisioning 或导入步骤、已有 Alertmanager 通知路由 |
| Strata 补充导出器 | Python wheel、`strata_extra.py`、服务单元、STRATA_API_KEY、JSON 地址、监听地址 / 防火墙；默认 9402 |
| GPU 推理运行时 | 复用服务则记录版本 / 模型 ID / 内网地址；新装则带全部模型权重分片、tokenizer、配置、引擎二进制 / 镜像、依赖库和 GPU 驱动 / CUDA / CANN 等对应物料 |
| 模型校验 | 权重和 tokenizer 全部文件的大小 / SHA-256；本地路径、许可文件、上下文 / 量化 / 参数配置；不允许启动时自动下载缺失分片 |
| 数据 | 新装空目录与 UID/GID / 空间规划；迁移另带一致性备份和原镜像锁，禁止用运行中的数据目录简单复制当可恢复备份 |

离线物料包与生产密钥分开交付。`offline_bundle.py` 拒绝包目录中的 `.env`、`.key` 和 `secrets/`；它不替代任意归档内容 / 文本的人工凭据审查。准备源码请用 `git archive`，不把工作目录、`.runtime`、数据和备份一起打包。

## 3. 标准离线包结构

```text
llm-offline-kylinv10-实际SP-Build-amd64-py312/
├── source.tar                       同一个 Git commit 的源码
├── images/images.tar                9 个核心镜像，另含实际新增组件
├── images.lock.json                 源 digest、image ID、架构、大小
├── images.env                       Compose 使用的本地锁定别名，仅 IMAGE 变量
├── requirements.lock.txt            完整精确版本锁
├── wheelhouse/*.whl                 包括全部传递依赖和安装工具
├── cache/tokenizers/                宿主 / 容器实际使用的预热缓存
├── host/README.md                   麒麟产品、SP / Build、内核、glibc、架构、安装路线
├── host/rpms/                       同版麒麟的完整运行 RPM 闭包
├── host/rpm-top-level.txt           选定顶层 RPM 的精确版本引用，每行一个
├── host/rpm-lock.txt                全部 RPM 的精确 NEVRA；文件哈希见 manifest
├── host/RPM-GPG-KEY-发行方           RPM 签名公钥，另记可信指纹；不是运行私钥
├── host/compose/                    已验证的 Compose 插件与 LICENSE
├── host/runtime/                    独立 Python / OpenSSL 或静态 Docker（采用时）
├── host/config/                     systemd、Docker 配置合并、SELinux / 网络方案
├── models/README.md                 复用哪台内网服务，或权重 / 运行时路径与校验
├── models/weights/                  仅部署包承诺新装推理服务时必需
├── config/README.md                 拓扑、端口、配置合并与证书 / 密钥交付方式
├── config/source-revision.txt       git rev-parse HEAD
├── evidence/                        准备机的冷装 / 无网导入 / 依赖检查记录
├── manifest.json                    全物料大小与 SHA-256
└── SHA256SUMS                       含 manifest 自身的校验清单
```

包名中的 SP / Build 与 amd64 需替换成实际环境，例如 aarch64 使用 arm64；不同 SP / 架构不共用系统包。该结构的三个 README 是实质性交付声明：系统包是否齐全、模型是复用还是新装、IP / 证书如何配置。不能只建空 README 就标记物料完整。封包工具检查清单和哈希，不证明 RPM / wheel 的依赖闭包、内核兼容或模型能加载；这些由下面的无网安装和运行验收证明。

## 4. 联网准备机操作

以下在与目标匹配的**联网麒麟 V10 准备机**运行，用独立目录和 venv。系统安装与服务配置步骤需要 root；镜像准备需要有 Docker 权限。`editor` 是现场文本编辑器的占位命令，可用已安装的 `vi` 替换。

### 4.1 固定平台并准备完整 RPM 物料

先按 2.1 采集信息、选定 Docker 组合和可用的独立 Python 3.12。在准备机安装其运行时后，设置下列变量。`KYLIN_BUILD` 必须填写实际值，镜像平台由本机架构映射，不使用 x86 模拟器代替 ARM 真机验收。

```bash
export KYLIN_BUILD=REPLACE_WITH_ACTUAL_SP_BUILD
test "$KYLIN_BUILD" != REPLACE_WITH_ACTUAL_SP_BUILD || exit 1
case "$(uname -m)" in
  x86_64) export TARGET_PLATFORM=linux/amd64 BUNDLE_ARCH=amd64 ;;
  aarch64) export TARGET_PLATFORM=linux/arm64 BUNDLE_ARCH=arm64 ;;
  *) echo '当前架构需要单独适配，停止制作通用包' >&2; exit 1 ;;
esac
export BUNDLE="/srv/offline/llm-offline-kylinv10-${KYLIN_BUILD}-${BUNDLE_ARCH}-py312"
export PYTHON312=/opt/llm-python/bin/python3.12
"$PYTHON312" -c 'import sys, ssl, venv, sqlite3, bz2, lzma, ctypes; assert sys.version_info[:2] == (3, 12); print(sys.version); print(ssl.OPENSSL_VERSION)'
mkdir -p "$BUNDLE/host/rpms" "$BUNDLE/host/compose" "$BUNDLE/host/runtime" "$BUNDLE/evidence"
```

在 `host/rpm-top-level.txt` 中写入本次选定的精确包版本引用，每行一个，无注释 / 空行，包含 Docker、Python 发行物与实际所需基础工具。不要把互斥的两套 Docker 都列进去。将自制 Python RPM 也加入待安装集合；若以 runtime tar 交付，则单独记录完整安装路径、运行库依赖和校验值。

YUM 路线在准备机使用 `yumdownloader --resolve --alldeps` 下载包括已安装包在内的依赖。只启用与目标匹配、已核对签名的麒麟 / 经验证供应商源；仓库配置和元数据版本记录到证据中。工具版本不支持 `--alldeps` 时先解决完整闭包下载方式，不能悄悄省略。

```bash
mapfile -t RPM_TOP_LEVEL < "$BUNDLE/host/rpm-top-level.txt"
test "${#RPM_TOP_LEVEL[@]}" -gt 0 || exit 1
yumdownloader --resolve --alldeps --destdir "$BUNDLE/host/rpms" "${RPM_TOP_LEVEL[@]}"
rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' \
  "$BUNDLE"/host/rpms/*.rpm | sort > "$BUNDLE/host/rpm-lock.txt"
rpm --checksig "$BUNDLE"/host/rpms/*.rpm > "$BUNDLE/evidence/rpm-signatures-preparation.txt"
```

实际采用 DNF 的 SP 可用 `dnf download --resolve --alldeps --destdir ...`，需先备好对应插件；不要混用另一发行版的仓库。在同版干净麒麟克隆机上按 5.1 禁用全部 repo 后安装，缺包即回准备机补齐。ISO 提供的基础包也要注明 ISO 版本并进入受控物料；仅复制 yum 缓存不算依赖闭包验证。

Compose RPM 不可用时，可把经验证的插件放入 `host/compose/docker-compose`，目标安装到 `/usr/local/lib/docker/cli-plugins/docker-compose` 并设可执行权限。保留原插件与配置的回滚副本，使用真实 CLI 验证插件发现路径，不默认调用 Python 版 docker-compose V1。

### 4.2 选版本、验证镜像、导出

```bash
cd /源码/litellm-langfuse-monitoring
cp offline/images.example.env offline/images.local.env
# 核对已选定的 amd64 候选、同版官方 Compose；有改动时同步修改映射表。
editor offline/images.local.env
bash ops/pull-from-ddn.sh offline/ddn-image-map.amd64.tsv "$TARGET_PLATFORM"
```

仓库已提供 [完整 DDN 映射表](../offline/ddn-image-map.amd64.tsv)。第一行声明平台，后续每行“带 manifest digest 的 DDN 引用 TAB 上游引用”。脚本只从 DDN 拉取，再增加上游别名方便核对；不会回退上游公网仓库。镜像清单与映射表必须匹配；默认 amd64 表遇到 `TARGET_PLATFORM=linux/arm64` 会在拉取前拒绝。ARM 需先按 [DDN 说明](ddn-images.md) 查询并核查实际 arm64 地址 / digest，制作独立映射表和 images.local.env 后替换命令参数。

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
"$PYTHON312" ops/offline_bundle.py images \
  --images-env offline/images.local.env --platform "$TARGET_PLATFORM" --bundle "$BUNDLE"
```

工具生成 `llm-offline/<service>:<image-ID前16位>-<架构>` 本地别名，保存完整 image ID / 原 repo digest，并导出包含别名的 tar。这样避免 `docker save/load` 后 repo digest 引用未被保留而导致 Compose 尝试访问仓库；目标机仍须验证完整 image ID，不能只看 tag 存在。ARM 目标必须为全部镜像找到原生 arm64 发行物；任何一个镜像不提供该架构就先解决发行物，不能用 `--platform linux/amd64` 当作适配完成。

### 4.3 下载完整 wheelhouse 并进行空环境安装

```bash
mkdir -p "$BUNDLE/wheelhouse" "$BUNDLE/evidence"
"$PYTHON312" -m venv /srv/offline-build-venv
PREP_PY=/srv/offline-build-venv/bin/python
"$PREP_PY" -m pip debug --verbose > "$BUNDLE/evidence/pip-compatible-tags.txt"

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

### 4.4 预热 Tokenizer、模型与宿主物料

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

### 4.5 打包同一版源码并封包

```bash
mkdir -p "$BUNDLE/host" "$BUNDLE/models" "$BUNDLE/config"
# 先完成相应 README 和实际系统 / 模型物料，不用空文件代替。
editor "$BUNDLE/host/README.md" "$BUNDLE/models/README.md" "$BUNDLE/config/README.md"
git rev-parse HEAD > "$BUNDLE/config/source-revision.txt"
git archive --format=tar -o "$BUNDLE/source.tar" HEAD

"$PYTHON312" ops/offline_bundle.py seal \
  --bundle "$BUNDLE" --platform "$TARGET_PLATFORM" --python-version 3.12
"$PYTHON312" ops/offline_bundle.py verify --bundle "$BUNDLE"
```

`git archive` 只包含已提交文件，打包前要求 Git 工作区干净且当前提交就是批准版本。封包后不要再改配置模板或增加包；如有改动重新封包。输出的 `checksum_file_sha256` 通过独立受控渠道记录，避免传输中清单与文件一起被错误替换。SHA-256 用于完整性检查，不等价于发布者数字签名。

将整个目录复制到离线介质；运行密钥和 TLS 私钥由单独渠道到目标机。目标验收报告写到包目录之外，保持封包内容不变。

## 5. 完全离线目标机实施

### 5.1 先阻断外网，再导入和安装

选择同产品、同 SP / Build、同架构的独立干净麒麟实验机进行首次验收；不要清空当前生产 Docker、pip 缓存或数据卷来模拟“干净”。外网出口从冷安装开始就阻断，只允许部署矩阵中的内网地址、DNS、NTP 和管理通道。Docker 发布端口与转发规则不一定受普通 firewalld INPUT 规则约束，需以实际 Docker 网桥 / 上游网络策略为准。

留存出口 ACL / 路由、测试时间、内网白名单与外网拒绝日志。单独 curl 几个公网域名失败，不足以证明完全隔离；由网络策略和请求审计共同证明。不要把 Docker backend 直接设为 `internal: true` 后还期待它能访问外部 GPU VM；应按内网访问需求实施出口策略。

```bash
export BUNDLE=/介质/llm-offline-kylinv10-实际SP-Build-amd64-py312
cd "$BUNDLE"
sha256sum SHA256SUMS
# 先与独立受控渠道的摘要核对一致，再检查下面的全部文件。
sha256sum -c SHA256SUMS
```

基础系统已具备 coreutils / RPM / YUM。核对随包公钥与受控渠道指纹一致后，导入对应 RPM 签名公钥，逐个验证 RPM 签名。包清单若带多家发行方，所有公钥均需核对。以下是现场安装示例，先审查事务内容，不能忽略 NOKEY / BAD 签名或依赖错误：

```bash
rpm --import "$BUNDLE"/host/RPM-GPG-KEY-*
rpm --checksig "$BUNDLE"/host/rpms/*.rpm
yum --noplugins --disablerepo='*' --setopt=localpkg_gpgcheck=1 \
  localinstall "$BUNDLE"/host/rpms/*.rpm
```

若现场是 DNF，则使用对应 `dnf --noplugins --disablerepo='*' --setopt=localpkg_gpgcheck=1 install ...` 并按已验证事务执行。自制 RPM 也需签名并交付可信公钥。复用组件时不要重复安装 / 替换已有业务包；只安装在克隆机验证过的事务。采用 runtime tar / 静态 Docker 的发行包，还需按包内已验证的安装说明落盘、安装服务单元与依赖库；不能只执行 RPM 步骤就省略这些物料。

完成 5.2 的内核、存储与安全策略检查后，启动 / 复用 Docker，再核对物料与导入镜像。校验工具需随可信交付目录预先提供，调用本次交付的独立 Python 3.12，**不是麒麟系统默认的 python / python3**；工具仅依赖标准库。上述路径不是下载地址，不能先联网 git clone 再开始“离线验收”。

```bash
export PYTHON312=/opt/llm-python/bin/python3.12
"$PYTHON312" -c 'import sys, ssl, venv, sqlite3, bz2, lzma, ctypes; assert sys.version_info[:2] == (3, 12); print(ssl.OPENSSL_VERSION)'
"$PYTHON312" /可信交付目录/ops/offline_bundle.py verify --bundle "$BUNDLE" \
  --report /var/tmp/offline-artifacts-before-load.json
docker load -i "$BUNDLE/images/images.tar"
"$PYTHON312" /可信交付目录/ops/offline_bundle.py verify --bundle "$BUNDLE" \
  --loaded-images --report /var/tmp/offline-artifacts-after-load.json
```

使用无索引方式安装验收 venv，不能复制准备机的整个 venv：

```bash
"$PYTHON312" -m venv /opt/llm-observability/.venv
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 /opt/llm-observability/.venv/bin/python \
  -m pip install --no-index --find-links "$BUNDLE/wheelhouse" -r "$BUNDLE/requirements.lock.txt"
/opt/llm-observability/.venv/bin/python -m pip check
```

### 5.2 麒麟宿主内核、Docker、存储和安全策略

本节的宿主检查在导入镜像前完成；配置和挂载策略在 5.3 解压业务文件后应用。既有 Docker 正常时复用其配置，不覆盖 `/etc/docker/daemon.json` 或重启其业务容器。

| 检查项 | 操作与通过标准 |
| --- | --- |
| 内核 / cgroup | 记录 `uname -r`、`stat -fc %T /sys/fs/cgroup`、`docker info` 的 Cgroup Driver / Version。按该麒麟 SP 和所选 Docker 组合验证，不盲目切换 cgroup v1 / v2；新镜像的线程 / syscall / seccomp 要真实运行通过 |
| 存储驱动 | 在 `docker info` 确认实际 data-root、Storage Driver / snapshotter。采用 overlay2 时，核对其要求与 backing filesystem；XFS 用 `xfs_info` 检查 `ftype=1`，ext4 也需实测。不要在运行中的数据盘重建文件系统或直接改 driver；不满足时用独立已准备的磁盘 / 测试机 |
| Docker 数据与日志 | 镜像导入空间、inode、数据库空间和日志轮转够用。合并配置保留原 data-root / storage / cgroup 设置；服务单元、配置、日志策略写入交付记录 |
| 转发与内网 | `sysctl net.ipv4.ip_forward` 应符合 Docker bridge 转发要求；需要的 bridge / netfilter 能力由同版内核提供。容器 DNS、容器到 GPU VM / NTP / DNS 可达，外网出口仍受限 |
| SELinux | 记录 `getenforce`。Enforcing 环境携带匹配的 container-selinux / policy，保持正常访问控制；为专用业务目录准备并验证持久标签或 Compose `:z` / `:Z`。共享目录用 `:z`，单容器私有目录才用 `:Z`，不要重标记整个 `/opt` / `/etc` 或关闭 SELinux 来代替适配 |
| 挂载权限 | 本仓库的 data / certs / litellm 配置均为 bind mount。按所选镜像实际 UID/GID 设置，保留只读标志；不能用 `chmod 777` 掩盖 UID 或 SELinux AVC 拒绝 |
| firewalld / iptables | 用 `firewall-cmd --get-active-zones`、`firewall-cmd --list-all` 和实际访问测试记录策略；仅开放部署矩阵内的源与端口。客户端 8443、管理端 8444、媒体 9443（启用时）、Prometheus 9401 / 9402 分别授权，数据库端口不对外发布。不因容器不通而停止 firewalld 或清空 iptables |
| CA 与时间 | 把可信根证书安装到 `/etc/pki/ca-trust/source/anchors/`，执行 `update-ca-trust extract`，通过 `chronyc tracking` / `timedatectl` 核对内网时钟。宿主、浏览器和需要访问 HTTPS 的容器分别配置信任 |

SELinux 持久标签方案示例，仅对**本次新建的专用目录**使用，并先确认已安装的策略含 `container_file_t`。`semanage` 所在包可能是 `policycoreutils-python` 或 `policycoreutils-python-utils`，在准备机按实际 SP 选定并打包：

```bash
# 在 5.3 创建专用目录后执行，不在现有共享数据目录上直接套用。
semanage fcontext -a -t container_file_t '/opt/llm-gateway/(data|litellm|nginx)(/.*)?'
restorecon -Rv /opt/llm-gateway/data /opt/llm-gateway/litellm /opt/llm-gateway/nginx
```

上述标签方式与经验证的 Compose `:z` / `:Z` 方案二选一，记录所用方案。已有 fcontext 规则时先检查和合并；不能忽略 `semanage` 的失败。容器拒绝读写时检查 `ausearch -m AVC -ts recent` 与实际 UID / 标签，不用 `--privileged`、关闭 seccomp 或 SELinux 作为正式验收条件。

新装 Docker 在合并好配置后使用 `systemctl enable --now docker`；原有 Docker 仅检查状态。确认 `docker compose version`、实际配置的 `config -q` 和无网容器运行均通过。机器重启后以 systemd 自动恢复并保留上述标签 / 网络 / 存储配置，不能只在当前 shell 里临时设置。

### 5.3 解压配置、生成现场密钥与证书

```bash
mkdir -p /opt/llm-gateway
tar -xf "$BUNDLE/source.tar" -C /opt/llm-gateway
cd /opt/llm-gateway
cp .env.example .env
chmod 600 .env
mkdir -p nginx/certs data/nginx-logs data/source-usage
chmod 700 nginx/certs
# 拷贝企业 CA 签发的 server.crt / server.key，SAN 包含实际 IP / DNS。
chmod 600 nginx/certs/server.key
editor .env litellm/config.yaml nginx/nginx.conf
```

必须完成以下配置，逐项记录到现场配置清单：

- `HOST_IP` 是 VM-01 实际地址；`NEXTAUTH_URL=https://VM01:8444`，对象存储公开 endpoint 必须从实际浏览器可达。
- 使用 `openssl rand -hex 24` 生成数据库 / Redis / MinIO 密码，避免拼入 DATABASE_URL 时需要转义的 `@:/#` 等字符；`ENCRYPTION_KEY` 用 `openssl rand -hex 32`，SALT / NEXTAUTH_SECRET 另行生成。迁移既有数据时保留原加密键。
- `LITELLM_OTEL_V2=false`、`LITELLM_LOCAL_MODEL_COST_MAP=True`，回调只能是 `prometheus` 与 `observability_callback.observability`；不要同时启用 `langfuse_otel` 重复上报。项目 Keys 允许在首次创建项目前为空，创建后必须补齐。
- 来源账本挂载 `data/source-usage`，按实际 LiteLLM 镜像 UID / GID 授予目录读写与遍历权限并设置 SELinux 标签。SQLite 需要同目录创建 WAL / SHM；使用标准库 sqlite3，不新增镜像或 pip 包。设置 `GATEWAY_SOURCE_USAGE_DB=/var/lib/llm-observability/usage.sqlite3`，单 worker 运行。
- `general_settings.use_x_forwarded_for=false`、空 `FORWARDED_ALLOW_IPS` 保留连接 peer。Nginx 启动后发现其实际 IP，将 `GATEWAY_TRUSTED_PROXY_CIDRS` 填为该 `/32`（IPv6 `/128`）并只重建 LiteLLM；也可使用准备机验证过的固定代理地址方案。Nginx 重建换 IP 后更新；不信任整个业务网络。验证覆盖外来 XFF / X-Real-IP，不能用请求自报的 `metadata.source_ip` 归属用量。
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

### 5.4 启动 Langfuse、创建项目 Keys、加载网关

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

### 5.5 合并 Prometheus、导入 Grafana、部署补充 exporter

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

systemd 的 ExecStart 使用目标 venv Python 的绝对路径和 exporter 源码绝对路径，EnvironmentFile 指向该私有文件。麒麟上安装为经验证的系统级 service，指定运行用户、文件权限、network-online 依赖与重启策略，执行 `systemctl daemon-reload` / `systemctl enable --now 实际服务名`；不照搬 WSL 的 `systemctl --user`。先以前台方式确认 9402 `/metrics` 中 `strata_extra_up=1`，再 enable 服务；不要原样运行本机路径固定的 `ops/deploy_wsl.py` 来覆盖多 VM 的现有配置。

**Grafana 公式离线可读**：每个面板的信息说明已包含公式 / 单位 / 实际 PromQL，另随包提供 [完整公式文档](grafana-formulas.md)。看板顶部 GitHub 链接在断网时不可访问，不影响面板与嵌入公式；需要文档入口时改为内部文档站。

## 6. 完全离线验收步骤与通过标准

使用 [离线验收记录模板](offline-acceptance-template.md) 逐项保存证据。最终状态由实际结果填写，物料脚本始终输出 `deployment_accepted: false`，避免把哈希检查冒充全链路验收。

### 6.1 包与系统

- 目标机在开始安装前已禁止外网，内网白名单与出口日志有记录；实际麒麟产品 / SP / Build、内核 / CPU、Python ABI / libc 与物料和兼容矩阵一致。
- 全部 RPM 签名与精确 NEVRA 已核对，禁用全部 YUM / DNF repo 后本地安装成功；系统 Python / YUM 仍可用，无强制安装与替换系统 glibc。
- Docker / containerd / runc / Compose 在现场内核、seccomp、cgroup、存储驱动与 SELinux / firewalld 配置下运行通过，目录 UID/GID 与持久标签符合所选镜像。
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

- 59 面板已导入、数据源有效；94 条 PromQL 查询全部执行成功，实际部署指标的关键序列为有限数值。未部署的可选引擎可无数据，但必需 job 不得缺失。
- 网关有请求与输入 / 输出 Token 增量，真实流式有 TTFT / TPOT；Langfuse TTFT 与回调 metadata 一致，usage 互斥细分相加等于 raw total。
- 公式已核查：`TPOT=(last-first)/(N−1)`；P95 来自合并 bucket；网关 Token/s 和 QPS 是固定 `[5m]`；VRAM 为 `100×used/total`；前缀 / MTP 用 Token 加权；MiB/s 乘 1048576；SSE 帧不当作 Token。
- Strata context occupancy 与物理 KV 分开；Strata ITL 是按间隔数加权的平均值估计。空闲 / 冷启没有分位样本时保持无值，不强制补 0 秒。
- 两个真实客户端来源分别完成已知 usage 的请求，Langfuse `source_ip` 与入口可见地址一致，按 IP 累计差值等于返回的输入 / 输出；伪造 XFF / `metadata.source_ip` 不改变归属。核对第 07 分区筛选、累计总量=input+output，缓存 / 推理不重复加入。所选时段 `increase` 是采样外推，允许小数及采样边界差异；NAT 后不能还原的来源按实际限制记录，不能填成原始客户端 IP。
- 断开一个测试 exporter 验证 UP / 告警与恢复，只在实验环境执行；不修改现有 Alertmanager receiver。异常 / 中断请求在 Langfuse 可见，OTLP 失败 / 本地构造错误有记录。

### 6.4 冷启动、持久化与恢复

- 外网仍受限时重启麒麟测试主机，确认 Docker、系统级 exporter service、模型服务自动恢复，原有 Trace / 用户 / 仪表盘 / 数据不丢失；SELinux 标签、firewalld、Docker 存储 / cgroup、CA 信任与实际版本保持一致。
- 用新的请求再次验证推理、Langfuse、Token / TTFT / TPOT、GPU、缓存和工具链；没有首次请求下载依赖的等待或外网回退。
- 来源账本持久化状态为 1、写入错误为 0；重启后累计值及统计开始时间不变，新请求继续增加。按 [来源账本备份恢复](source-ip-usage.md#配置持久化与离线部署) 使用 SQLite backup API 取得一致性副本并在实验环境恢复，不单独拷贝活跃 WAL 主文件。
- 实验环境执行一致性备份 / 恢复，使用原镜像锁、配置和加密键；确认历史 Trace 可查、模型路由可用。
- 记录包版本 / checksum 摘要、实际镜像 ID、平台、CA、内网拓扑、请求 / Trace ID、全部报告和人工页面检查结果，才可签署“完全离线部署通过”。

## 7. 备份、回滚和常见阻断

迁移 / 升级前先保存原镜像锁、配置、证书、加密键和一致性数据备份。停写后再做 PostgreSQL / ClickHouse / Redis / MinIO 的一致性快照或使用各自原生备份。恢复时按原版本先依赖后应用，核对 UID/GID。不要在没有备份时执行 `down -v`、删除 data 或修改 ENCRYPTION_KEY。

来源用量额外包含 `data/source-usage`：使用 SQLite backup API 或停止网关后备份整个目录，恢复时不要混入旧 WAL / SHM。无需额外镜像 / RPM / wheel，但独立 Python 及 LiteLLM 容器均须具备可用的 sqlite3。累计周期、代理信任和缺失 usage 的处理见 [来源 IP 与 Token 用量](source-ip-usage.md)。

| 阻断 | 处理与重新验收 |
| --- | --- |
| RPM 依赖 / 签名失败 | 核对麒麟产品 / SP / Build、NEVRA、架构、签名公钥与完整闭包；回同版准备机补齐，不强制安装其他 el 版本的包 |
| Python 报 GLIBC / GLIBCXX / OpenSSL 不满足 | 按实际 glibc / C++ ABI 重建运行时或 wheel；Python 使用独立前缀，不升级替换系统 glibc / Python / OpenSSL |
| 容器 syscall / seccomp / cgroup / illegal instruction 错误 | 检查宿主内核、CPU 指令集、Docker / containerd / runc 与镜像发行物兼容；先解决版本组合，在麒麟真机重新验收 |
| 挂载 permission denied / XFS driver 失败 | 核对 UID/GID、SELinux AVC / 持久标签、XFS ftype 与存储 driver；不要关闭 SELinux 或原地格式化业务盘 |
| 镜像 tag 存在但启动仍 pull | 统一使用生成的本地 images.env，完整 image ID 对比通过，所有 up 带 `--pull never --no-build` |
| MinIO 报 sh 不存在 / 权限失败 | 发行版与 entrypoint 不兼容；联网准备机改为经过验证的镜像 / Compose，重新封包并冷装 |
| 回调 import 缺 OTEL / Prometheus | 最终 LiteLLM 镜像未包含回调依赖；按其 Python / libc 构建，目标机不联网补包 |
| 首次请求尝试下载编码器 | 缓存不完整 / 未挂入实际运行容器；准备机预热实际路径，再以 network none 重测 |
| pip 缺包 / no matching distribution | wheelhouse 闭包、CPython、CPU 或 musl / glibc 不匹配；回准备机补齐，重新空 venv 验证 |
| Langfuse UI 正常但没有 Trace | 检查 Worker、桶、OTLP v4 header、项目 Keys、LANGFUSE_HOST、回调错误指标；读取 v2 API |
| 8443 / 8444 TLS 失败 | SAN、CA 链、时钟、Nginx 配置、客户端信任；验收不以 `-k` 跳过证书验证 |
| Prometheus 403 / 401 | 分别核对 Nginx allow 的真实来源与 Bearer Token / 容器文件权限 |
| Grafana 指标空白 | 先验证目标 UP、16 条规则、模型标签与数据源；可选 / 无流量指标和真正采集失败分开判定 |
| 来源只有代理 IP / Token 不增长 | 核对可信代理实际地址、转发覆盖、回调已加载、请求已完成及 usage 完整；检查账本目录 UID / SELinux、持久化与写入错误，不通过任意信任 XFF 绕过 |
| 离线登录 / 媒体依赖公网 | 替换为本地账号、内部 DNS / NTP / CA / 对象存储，验证实际浏览器访问 |

官方参考用于准备机选版本审计，实施时所需配置和说明必须已经在包中：

- [Langfuse Compose 部署](https://langfuse.com/self-hosting/deployment/docker-compose)
- [Langfuse 官方 Compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)（必须取选定发布对应内容，不能把最新 main 当兼容性锁）
- [LiteLLM Prometheus](https://docs.litellm.ai/docs/proxy/prometheus)
- [Docker CentOS 安装与支持范围](https://docs.docker.com/engine/install/centos/)（麒麟版本需以发行商支持与实际测试为准）
- [Docker overlay2 前提](https://docs.docker.com/engine/storage/drivers/overlayfs-driver/)
- [Python 3.12 构建依赖](https://docs.python.org/3.12/using/configure.html#build-requirements)
- [manylinux 与 glibc 兼容规范（PEP 600）](https://peps.python.org/pep-0600/)
- [本仓库的实际配置修复与边界](observability-findings.md)

**发布状态：麒麟 V10 的物料准备、RPM 安装、兼容门槛和验收流程已列明；具体 SP / Build / 架构的兼容矩阵、完整离线物料制作与干净麒麟断网机全链路验收尚待执行并保存记录。**
