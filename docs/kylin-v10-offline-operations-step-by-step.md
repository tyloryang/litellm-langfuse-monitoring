# 麒麟 V10 离线操作指南：Nginx、LiteLLM、Langfuse

适用范围：`x86_64` 麒麟 V10，复用目标机已有 Docker、Strata 和 Qwen 模型；按介质完成网关和 Langfuse 的离线部署或在现有实例上重部署。先确定采用第 6 节的**已有服务重部署**，还是第 7 节的**独立新安装**。

本指南只记录操作；目标麒麟机尚未由本项目完成断网部署、推理和恢复验收。不要把云端或 WSL 的结果当作目标机验收。已有业务中，原有 Strata、GPU、模型、Prometheus、Grafana 和数据库的配置与数据必须保留；已有 Langfuse 数据不能因安装低版本镜像而降级。

## 1. 先看：要拷贝什么、安装什么

从私有 GitHub Release [offline-reuse-amd64-20261116](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-reuse-amd64-20261116) 的 Assets 取得下列文件：

| 文件 | 用途 |
| --- | --- |
| `kylin-v10-gateway-reuse-amd64-20261116.tar.part01` 至 `part05` | 完整离线包的 5 个分卷，必须全部下载 |
| `BUNDLE-PARTS-SHA256SUMS` | 检查 5 个分卷 |
| `kylin-v10-gateway-reuse-amd64-20261116.tar.sha256` | 检查合并后的完整包 |
| `offline-full-chain-update-20261116.tar.gz` 和同名 `.sha256` | 新版部署 README、操作指南、完整主手册和真实工具链验收脚本 |

如果已有校验通过的完整 `kylin-v10-gateway-reuse-amd64-20261116.tar`，可跳过分卷下载和合并。完整包约 2.11 GB，展开后包含：

```text
llm-handoff/
├── downloads/
│   ├── nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz
│   ├── monitoring-amd64-20261116.tar
│   ├── source-and-docs-20261116-validated.tar.gz
│   └── SHA256SUMS、MONITORING-SHA256SUMS、VALIDATED-SHA256SUMS
├── manual/     # 打包时的历史手册与辅助工具；最新版另见手册更新包
├── patches/    # Grafana 看板补丁；本指南不安装监控
├── system/     # 麒麟依赖候选；不是已验证的麒麟 RPM 闭包
└── HANDOFF-SHA256SUMS
```

网关候选包恢复后约 5.71 GB，解压并导入其中的 `images/images.tar` 将获得 9 个镜像：Nginx、LiteLLM、Langfuse Web、Langfuse Worker、PostgreSQL、ClickHouse、Redis、MinIO、MinIO 客户端。**第 6 节只重建 Nginx、LiteLLM、Langfuse Web / Worker；不会启动或重建其余镜像对应的服务。** 第 7 节的新安装为了使 Langfuse 可用，需要创建独立的存储依赖，必须单独确认这些新增容器在现场允许。

包中还带 Prometheus / Grafana 独立监控包，但本指南不导入、不安装、不重建监控；也不包含 Strata 镜像、GGUF、模型 pack、驱动、历史数据或真实密码。宿主的 Docker、GPU 驱动和模型服务由现场复用。麒麟 V10 的 Docker RPM 和依赖未完成目标版本验收，不应直接安装 `system/` 中的 CentOS 7 候选 RPM。

## 2. 联网准备机：下载、校验并合并

此节只在能访问私有 GitHub 的准备机执行；目标内网机器不需要 GitHub、CLI 或互联网。也可以在网页逐个下载表中列出的文件。

```bash
set -euo pipefail
mkdir -p /srv/llm-offline-download
gh release download offline-reuse-amd64-20261116 \
  -R tyloryang/litellm-langfuse-monitoring \
  -D /srv/llm-offline-download \
  -p 'kylin-v10-gateway-reuse-amd64-20261116.tar.part*' \
  -p 'BUNDLE-PARTS-SHA256SUMS' \
  -p 'kylin-v10-gateway-reuse-amd64-20261116.tar.sha256' \
  -p 'offline-full-chain-update-20261116.tar.gz*'
cd /srv/llm-offline-download
sha256sum -c BUNDLE-PARTS-SHA256SUMS
sha256sum -c offline-full-chain-update-20261116.tar.gz.sha256
test ! -e kylin-v10-gateway-reuse-amd64-20261116.tar
cat kylin-v10-gateway-reuse-amd64-20261116.tar.part01 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part02 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part03 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part04 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part05 \
    > kylin-v10-gateway-reuse-amd64-20261116.tar
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
```

合并包应为 **2,101,688,320 字节**，SHA-256 应为 `8b50cf74306f1c455487f257b91587ccffad17df1a6046d339cd1b474a8b48ae`。将以下文件复制到离线介质：

```text
kylin-v10-gateway-reuse-amd64-20261116.tar
kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
offline-full-chain-update-20261116.tar.gz
offline-full-chain-update-20261116.tar.gz.sha256
```

新版操作指南位于补充包内；旧版同名 Release 附件及旧手册包是历史快照，不包含本版工具链验收脚本。校验清单本身须通过可信渠道确认；准备介质及目标磁盘需容纳完整包、展开文件、约 5.71 GB 的恢复 tar、镜像层、数据库和备份。建议至少预留 30 GiB 再加业务数据及备份空间，实际以镜像层与历史数据大小为准。

## 3. 离线目标机：先检查，不安装 RPM

以有 Docker 管理权限的受控管理员执行；后续命令为 Bash。确认使用本机 Docker，不要无意连接远端 daemon：

```bash
cat /etc/os-release
uname -m
docker version
docker info --format 'root={{.DockerRootDir}} server={{.ServerVersion}}'
docker context show
command -v bash python3 openssl tar gzip sha256sum
df -h / /opt "$(docker info --format '{{.DockerRootDir}}')"
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
nvidia-smi
```

`uname -m` 应为 `x86_64`。如果 Docker、工具或磁盘不足，停止并为目标麒麟的实际 SP / Build 准备匹配依赖，不要通过公网仓库补装，也不要用包内 CentOS 7 候选直接覆盖已有 Docker。

确认已有 Strata 的**实际可访问地址、端口、模型 ID 和 API key**，以及 LiteLLM 容器能到达的宿主地址；目标机容器里的 `127.0.0.1` 指容器本身，通常不是宿主 Strata。以下输入密钥不回显：

```bash
read -r -p '现有 Strata 地址，如 http://192.168.1.20:8080：' STRATA_CHECK_URL
export STRATA_CHECK_URL
python3 - <<'PY'
import getpass, json, os, urllib.request
base = os.environ['STRATA_CHECK_URL'].rstrip('/')
key = getpass.getpass('现有 Strata API key: ')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for path in ('/health', '/v1/models'):
    req = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + key})
    with opener.open(req, timeout=15) as r:
        data = json.load(r)
        if path == '/health':
            print('health HTTP', r.status, 'loaded', data.get('loaded'))
            if data.get('loaded') is not True:
                raise SystemExit('Strata is not loaded')
        else:
            ids = [x.get('id') for x in data.get('data', [])]
            print('model IDs:', ids)
            if not ids:
                raise SystemExit('No model ID returned')
PY
unset STRATA_CHECK_URL
```

记录模型 ID 和可从 Docker 网络访问的实际 IPv4 / 端口。若原服务只监听宿主回环，先由管理员设计受控可达路径；不要为了本指南擅自改变现有 Strata 监听或重启它。

## 4. 离线目标机：校验并解压

将第 2 节 4 个文件复制到一个目录，以下使用占位路径，先改成实际挂载路径；不要覆盖已有 `/opt/llm-handoff` 或 `/opt/llm-offline-full-chain-update-20261116`：

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

此时没有安装镜像或启动容器。先阅读 `/opt/llm-offline-full-chain-update-20261116/README.md`，新版完整手册位于其 `manual/docs/kylin-v10-full-offline-deployment-20261116.md`；查看 Strata JSON、Compose 与大页时读其中附录 A。原包的手册文件和校验清单不要用新版直接覆盖。

接下来只恢复**网关候选包的 tar**，不展开监控包：

```bash
set -euo pipefail
cd /opt/llm-handoff/downloads
tr -d '\r' < SHA256SUMS | grep 'candidate-20261116-v3\.tar\.gz$' | sha256sum -c -
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
gzip -t nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz
gzip -dc nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz \
  > nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
printf '%s  %s\n' \
  6a6a397a70bfb8bdca4230eabf005fdfa1fa1e2090b917d9e5bede7d72a748cb \
  nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete | sha256sum -c -
mv nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete \
   nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
test ! -e /opt/llm-offline
mkdir -p /opt/llm-offline
tar -xf nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar -C /opt/llm-offline
BUNDLE=/opt/llm-offline/nginx-litellm-langfuse
cd "$BUNDLE"
sha256sum -c SHA256SUMS
host/compose/docker-compose version
```

得到的 `$BUNDLE` 是**离线物料目录**，不是正在运行的服务目录。其 `images/images.tar` 为九个镜像，`images.env` 为导入后的镜像名，`config/prepare.sh` 用于创建新的独立部署目录，`host/compose/docker-compose` 为离线 Compose 2.29.7 二进制；不能把它当作 Docker Engine。下一步选择第 6 节或第 7 节。

## 5. 选择操作路线

| 现场状态 | 选择 | 数据和其他服务 |
| --- | --- | --- |
| 已有 Nginx、LiteLLM、Langfuse，要求仅重部署它们 | 第 6 节 | 复用原 Compose、环境、网络、存储与密钥；仅重建 4 个指定容器 |
| 没有现有实例，允许创建新的数据库等依赖 | 第 7 节 | 在新的独立目录和项目中新建主栈；这是额外创建依赖，不符合“其他服务不动”时不得执行 |
| 已有服务由 systemd 或其他编排管理 | 先识别真实管理方式 | 不套用 Compose 示例；尤其 WSL 的 LiteLLM 可能是 systemd，不要启动同端口的替代容器 |

安装离线镜像并不会自动启动容器，但 `docker load` 会导入包内全部 9 个镜像及标签。已有环境先确认所需镜像已经存在且 ID 与当前服务一致；若已经存在，跳过 `docker load`。若必须导入，先检查同名标签和现有部署，防止修改其他项目依赖的镜像标签。

## 6. 已有实例：只重部署 Nginx / LiteLLM / Langfuse

### 6.1 找到当前项目、文件、镜像和挂载

将下例四个容器名换为现场 `docker ps` 输出中的实际名称。以下会输出主机目录、镜像及启动时间，不显示容器环境变量：

```bash
docker inspect llm-a100-nginx-1 llm-a100-litellm-1 \
  llm-a100-langfuse-web-1 llm-a100-langfuse-worker-1 \
  --format '{{.Name}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}} workdir={{index .Config.Labels "com.docker.compose.project.working_dir"}} files={{index .Config.Labels "com.docker.compose.project.config_files"}} image={{.Config.Image}} image_id={{.Image}} started={{.State.StartedAt}}'
docker inspect llm-a100-litellm-1 --format '{{json .Mounts}}'
docker inspect llm-a100-langfuse-web-1 --format '{{json .Mounts}}'
```

确认四个服务确实属于同一项目，记录实际项目名、工作目录、所有 `-f` 文件、真实镜像和持久卷 / bind mount、网络、密钥来源和版本。没有 Compose 标签或应用属于 systemd 时停止，按其原管理工具处理；不要把 `.env`、`docker inspect` 的完整输出或密钥写入公开记录。不要把新包的 Langfuse 4.63.0 直接挂载到较新版本（例如 4.73）的现有数据库上。

以云端此前的路径为**例子**，并非所有机器通用：工作目录 `/opt/llm-observability-deploy/src/monitoring`，项目 `llm-a100`，文件 `docker-compose.yml` 与 `docker-compose.cloud.yml`；目标机须替换为上面实际输出。要备份原 Compose、环境文件、Nginx / LiteLLM 配置、证书、回调、原始容器日志和一致性的持久化数据快照，备份应为受控私有目录。`.env`、证书私钥、完整 inspect 可能含密钥。数据库和 SQLite 账本仍在运行时，应使用对应数据库在线备份 / 一致性快照，不能声称普通复制数据目录即得到一致性备份。

下面仅为**配置备份示例**：必须根据现场实际文件清单调整；若文件或服务名不同，停止后查明。它不备份数据库内容：

```bash
set -euo pipefail
WORKDIR=/opt/llm-observability-deploy/src/monitoring  # 改为实际工作目录
test -d "$WORKDIR"
umask 077
BACKUP=$(mktemp -d /root/llm-redeploy-backup-XXXXXXXX)
chmod 700 "$BACKUP"
cd "$WORKDIR"
test -f .env && test -f docker-compose.yml && test -f docker-compose.cloud.yml
tar -cf "$BACKUP/config-private.tar" .env docker-compose.yml docker-compose.cloud.yml nginx litellm
chmod 600 "$BACKUP/config-private.tar"
printf '配置备份：%s\n' "$BACKUP"
```

该命令将备份包含私有材料的 `nginx` 和 `litellm` 目录；根据现场大小和内容审阅，禁止将备份上传 GitHub。先另行确认原有 PostgreSQL / ClickHouse / MinIO / Redis、来源 usage SQLite 账本、Langfuse 盐 / 加密密钥及持久卷均有可恢复的一致性备份。**不要执行 `docker compose down -v`、删除数据目录或重新运行初始化脚本。**

### 6.2 使用本地镜像逐个重建

以下示例只适用于第 6.1 节已确认的云端式两个 Compose 文件及服务名；按实际结果替换工作目录、项目名、文件和服务名。`env -i` 使 Compose 使用目录中的 `.env`，要求目标使用本机默认 Docker socket；若现场依赖 rootless / 远端 context 或其他必要环境变量，应按现场受控保留，不能机械照搬。不会 pull、build、启动依赖或重建其他服务，但重建中的服务会短暂中断。

```bash
set -euo pipefail
cd /opt/llm-observability-deploy/src/monitoring  # 改为实际工作目录
COMPOSE=/opt/llm-offline/nginx-litellm-langfuse/host/compose/docker-compose
test -x "$COMPOSE"
PROJECT=llm-a100  # 改为实际项目名
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" \
  -f docker-compose.yml -f docker-compose.cloud.yml config --quiet

# 先确认下列四个镜像名均可由本机 docker image inspect 找到，且与现有运行 ID 一致。
for c in llm-a100-nginx-1 llm-a100-litellm-1 llm-a100-langfuse-web-1 llm-a100-langfuse-worker-1; do
  image=$(docker inspect "$c" --format '{{.Config.Image}}')
  running_id=$(docker inspect "$c" --format '{{.Image}}')
  local_id=$(docker image inspect "$image" --format '{{.Id}}')
  test "$running_id" = "$local_id" || { echo "Image mismatch: $c"; exit 1; }
  echo "Local image verified: $c"
done

# 还需核对 Compose 展开后指定的镜像与当前容器镜像 ID 一致；只输出布尔结果。
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" \
  -f docker-compose.yml -f docker-compose.cloud.yml config --format json | python3 -c '
import json, subprocess, sys
cfg = json.load(sys.stdin)
for service in ("nginx", "litellm", "langfuse-web", "langfuse-worker"):
    image = cfg["services"][service]["image"]
    name = "llm-a100-" + service + "-1"  # 实际容器名不同时修改映射
    local_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], universal_newlines=True).strip()
    running_id = subprocess.check_output(["docker", "inspect", name, "--format", "{{.Image}}"], universal_newlines=True).strip()
    if local_id != running_id:
        sys.exit("Compose image differs from current container: " + service)
    print("Compose image matches:", service)
'

env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" \
  -f docker-compose.yml -f docker-compose.cloud.yml up -d --no-deps --no-build --pull never --force-recreate langfuse-worker langfuse-web
```

从仍运行的 LiteLLM 容器在 backend 网络上检查新 Langfuse Web，最多等待 120 秒；如果现场容器名、Python 命令或网络别名不同，先按实际环境调整。`healthy` 不代表已完成 Trace 入库：

```bash
docker exec -i llm-a100-litellm-1 python - <<'PY'
import json, sys, time, urllib.request
for attempt in range(24):
    try:
        with urllib.request.urlopen('http://langfuse-web:3000/api/public/health', timeout=3) as r:
            data = json.load(r)
            if r.status == 200 and data.get('status') == 'OK':
                print('Langfuse HTTP 200, status OK')
                sys.exit(0)
    except Exception:
        pass
    time.sleep(5)
sys.exit('Langfuse is not ready; stop and inspect logs')
PY
docker ps --filter name=llm-a100-langfuse-worker-1 --format '{{.Names}} {{.Status}}'
test "$(docker inspect llm-a100-langfuse-worker-1 --format '{{.State.Running}}')" = true
free -h
```

确认 Langfuse 通过健康检查、Worker 仍在运行且内存和 swap 余量可接受后，重建 LiteLLM；确认 LiteLLM 就绪后最后重建 Nginx，让它重新解析上游地址。每一步失败则停止，查看日志并按备份 / 原镜像回滚，不要继续下一步：

```bash
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" \
  -f docker-compose.yml -f docker-compose.cloud.yml up -d --no-deps --no-build --pull never --force-recreate litellm
docker exec -i llm-a100-litellm-1 python - <<'PY'
import sys, time, urllib.request
for attempt in range(24):
    try:
        with urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=3) as r:
            if r.status == 200:
                print('LiteLLM liveliness HTTP 200')
                sys.exit(0)
    except Exception:
        pass
    time.sleep(5)
sys.exit('LiteLLM is not ready; stop and inspect logs')
PY
# 再核对实际上游配置与认证仍然正确。
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" \
  -f docker-compose.yml -f docker-compose.cloud.yml up -d --no-deps --no-build --pull never --force-recreate nginx
docker exec llm-a100-nginx-1 nginx -t
```

记录 Strata、数据库、Prometheus、Grafana 的容器 ID / 启动时间，前后应一致。实际服务可能通过启动脚本安装 Python 依赖或在容器可写层保存额外文件；重建前必须核对并备份这些变更，不能仅以镜像 ID 推断它们会保留。云端以前核查过部分 Python 依赖与干净镜像一致，**不能代替目标机核查**。接第 8 节进行请求和 Langfuse 追踪验收。

## 7. 首次创建独立主栈：只有明确允许新增存储依赖时执行

本节会创建 PostgreSQL、ClickHouse、Redis、MinIO 等独立容器及新数据目录。若本次要求“只重部署三类服务，其他不要动”，**不要执行本节**，返回第 6 节。也不要在现有 `/opt/llm-gateway`、同名 Compose 项目或现有数据目录上运行。

### 7.1 导入网关镜像并创建新配置

确认已审阅本地镜像标签和目标端口，且新项目名未被使用：

```bash
set -euo pipefail
BUNDLE=/opt/llm-offline/nginx-litellm-langfuse
cd "$BUNDLE"
sha256sum -c SHA256SUMS
test -z "$(docker ps -aq --filter label=com.docker.compose.project=llm-a100)" || { echo 'Project exists; stop'; exit 1; }
if docker network inspect llm-a100_backend >/dev/null 2>&1; then
  echo 'Project network already exists; stop and inspect'; exit 1
fi
test ! -e /opt/llm-gateway
ss -ltnp | grep -E ':(8080|8443|8444|9401|9090)[[:space:]]' || true
# 上述端口如已占用，先调整现场规划并在对应 Compose / 配置中一致修改。
docker load -i images/images.tar
```

`docker load` 导入 9 个镜像，不启动它们。确认目标机 IPv4 / 域名、可信 CA 证书、Strata 地址和认证已准备，再创建独立配置：

```bash
read -r -p '目标机要绑定的实际 IPv4：' HOST_IP
read -r -p 'Langfuse 客户端可访问 URL，例如 https://域名:8444：' PUBLIC_LANGFUSE_URL
read -r -p 'MinIO 媒体可访问 URL，例如 http://IP:9090：' PUBLIC_MEDIA_URL
export HOST_IP PUBLIC_LANGFUSE_URL PUBLIC_MEDIA_URL
unset GATEWAY_TRUSTED_PROXY_CIDRS
bash "$BUNDLE/config/prepare.sh" /opt/llm-gateway
unset HOST_IP PUBLIC_LANGFUSE_URL PUBLIC_MEDIA_URL
```

脚本会生成 `/opt/llm-gateway/.env`、`offline-override.yml`、回调、配置及独立数据目录；初始管理员邮箱为 `admin@offline.invalid`，密码、LiteLLM master key 和 Langfuse 项目 key 均随机生成在私有 `.env`。备份并通过受控方式查看，禁止公开或提交。原包自签证书没有 SAN，不能用于现代客户端的正式证书校验；部署前按主手册第 7 节替换为现场 CA 签发、SAN 覆盖实际域名 / IP 且证书私钥匹配的证书。生成证书先保留备份。

### 7.2 配置已有 Strata 地址与密钥

以下使用第 3 节实际结果。新部署默认上游是示例地址；必须改成 Docker 容器可达的真实地址。下例要求 IPv4，地址为 `http://IPv4:端口/v1`，不是 `/v1/chat/completions`：

```bash
set -euo pipefail
cd /opt/llm-gateway
umask 077
test ! -e .env.before-strata && test ! -e litellm/config.yaml.before-strata
cp -p .env .env.before-strata
cp -p litellm/config.yaml litellm/config.yaml.before-strata
chmod 600 .env.before-strata litellm/config.yaml.before-strata
read -r -p 'Strata API base，如 http://192.168.1.20:8080/v1：' STRATA_API_BASE
export STRATA_API_BASE
python3 - <<'PY'
import getpass, ipaddress, os, re
from pathlib import Path
from urllib.parse import urlsplit
base = os.environ['STRATA_API_BASE']
try:
    url = urlsplit(base)
    ip = ipaddress.IPv4Address(url.hostname)
    if (url.scheme != 'http' or not url.port or url.path != '/v1' or url.query or url.fragment
            or url.username or url.password or ip.is_loopback or ip.is_unspecified or ip.is_multicast):
        raise ValueError('invalid URL or unreachable address')
except (ValueError, TypeError) as exc:
    raise SystemExit('Invalid Strata API base: ' + str(exc))
env, config = Path('.env'), Path('litellm/config.yaml')
e, c = env.read_text(encoding='utf-8'), config.read_text(encoding='utf-8')
old = 'api_base: http://10.0.0.13:8008/v1'
if c.count(old) != 1 or len(re.findall(r'^STRATA_API_KEY=REPLACE[ \t]*\r?$', e, re.M)) != 1:
    raise SystemExit('Expected initial template not found; inspect configuration')
key = getpass.getpass('Existing Strata API key: ')
if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or key == 'REPLACE':
    raise SystemExit('Key unsupported by this example; configure manually')
if key != getpass.getpass('Repeat Strata API key: '):
    raise SystemExit('Keys do not match; files unchanged')
e = re.sub(r'^STRATA_API_KEY=REPLACE[ \t]*\r?$', lambda _: 'STRATA_API_KEY=' + key, e, count=1, flags=re.M)
c = c.replace(old, 'api_base: ' + base, 1)
env.write_text(e, encoding='utf-8')
config.write_text(c, encoding='utf-8')
env.chmod(0o600)
print('Strata endpoint and credential configured; secret omitted')
PY
unset STRATA_API_BASE
```

该脚本保留回调和其他模型配置，两个文件的写入不是原子事务；如中断，使用备份核对恢复。`qwen-strata` 的上游 `model: openai/qwen3.8-flash-next-iq3_xxs` 要与第 3 节真实模型 ID 一致；不一致时手动修改。`qwen-vllm`、`qwen-sglang` 只是示例，未部署时在备份后删除或按现场配置。可信代理 CIDR、源 IP、证书和端口映射由管理员按网络拓扑配置；不能直接照搬云端公网 HTTP 端口。

### 7.3 校验并离线启动主栈

执行前确认允许新建九镜像对应的主栈、目标端口空闲且系统资源足够。必须同时加载 `docker-compose.yml` 和 `offline-override.yml`；后者为 Langfuse Web 注入初始化变量。

```bash
set -euo pipefail
cd /opt/llm-gateway
COMPOSE=/opt/llm-offline/nginx-litellm-langfuse/host/compose/docker-compose
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p llm-a100 \
  -f docker-compose.yml -f offline-override.yml config --quiet
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p llm-a100 \
  -f docker-compose.yml -f offline-override.yml up -d --pull never --no-build
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p llm-a100 \
  -f docker-compose.yml -f offline-override.yml ps -a
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p llm-a100 \
  -f docker-compose.yml -f offline-override.yml exec nginx nginx -t
```

这些命令使用本机默认 Docker socket；若现场不是默认 socket，应适配真实运行环境。首次启动可产生数据库迁移、MinIO 初始化和新数据；这不复用原数据。不要因为服务加载慢就反复重启。启动成功后，从 LiteLLM 容器验证到实际 Strata IPv4 / 端口的 TCP 和认证；宿主成功访问不代表容器网络可达。

## 8. 访问和验收

先检查容器、资源和 Nginx；现有项目的容器名须以第 6 节现场实际名称为准：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker exec llm-a100-nginx-1 nginx -t
free -h
df -h / /opt
```

新安装的默认入口为 `https://目标域名:8443/v1`（LiteLLM API）、`https://目标域名:8443/ui`（LiteLLM UI，实际可用性以该版本页面为准）、`https://目标域名:8444/`（Langfuse）。正式环境使用可信 CA 和匹配 SAN 的证书；客户端实际端口和外部映射按现场填写，不能假设云端端口映射存在。既有实例使用原账号和密钥，新实例的初始化信息在受控 `.env` 中；不要把真实密码粘贴到终端记录或截图。

以下在受信客户端执行两次真实请求；测试会产生新的 usage 和 Langfuse 事件。HTTPS 输入符合证书 SAN 的域名；若 CA 已由系统信任或现场使用 HTTP，CA 路径留空。密钥从终端不回显读取，模型别名填写现场配置：

```bash
read -r -p '网关 URL，例如 https://llm.example.internal:8443：' GATEWAY_URL
read -r -p '可信 CA 文件绝对路径（可留空）：' GATEWAY_CA
read -r -p '网关模型别名，例如 qwen-strata：' GATEWAY_MODEL
export GATEWAY_URL GATEWAY_CA GATEWAY_MODEL
python3 - <<'PY'
import getpass, json, os, secrets, ssl, sys, urllib.request
key = getpass.getpass('LiteLLM master or authorized virtual key: ')
ctx = ssl.create_default_context(cafile=os.environ['GATEWAY_CA'] or None)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
url = os.environ['GATEWAY_URL'].rstrip('/') + '/v1/chat/completions'
for stream in (False, True):
    trace = secrets.token_hex(16)
    data = {'model':os.environ['GATEWAY_MODEL'],'messages':[{'role':'user','content':'What is 17 plus 25? Answer with the number.'}], 'max_tokens':128, 'stream':stream}
    if stream:
        data['stream_options'] = {'include_usage': True}
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method='POST', headers={
        'Content-Type':'application/json', 'Authorization':'Bearer '+key,
        'traceparent':'00-'+trace+'-'+secrets.token_hex(8)+'-01'})
    with opener.open(req, timeout=600) as r:
        text, usage, frames, done = '', None, 0, not stream
        if stream:
            for line in r:
                if not line.startswith(b'data:'): continue
                raw = line[5:].strip()
                if raw == b'[DONE]': done = True; break
                if not raw: continue
                obj = json.loads(raw); frames += 1; usage = obj.get('usage') or usage
                for choice in obj.get('choices') or []:
                    text += (choice.get('delta') or {}).get('content') or ''
        else:
            obj = json.load(r); usage = obj.get('usage')
            for choice in obj.get('choices') or []:
                text += (choice.get('message') or {}).get('content') or ''
        print('stream=', stream, 'status=', r.status, 'trace=', trace,
              'answer=', text, 'usage=', usage, 'frames=', frames, 'done=', done)
        if r.status != 200 or '42' not in text or not usage or not done or (stream and frames < 2):
            sys.exit('Inference acceptance failed')
PY
unset GATEWAY_URL GATEWAY_CA GATEWAY_MODEL
```

在 Langfuse 的 **Tracing** 按请求时间、模型、输入输出、usage 及实际 Trace ID 查找这两次测试。若客户端或代理没有透传 `traceparent`，客户端打印 ID 不一定是 Langfuse 的 ID；应综合时间和内容关联。流式 TTFT、TPOT、工具节点、来源 IP 和 Grafana 展示还须按主手册和实际功能逐项验证，健康页和 HTTP 200 不能替代这些检查。

### 8.1 验证真实 Agent → Generation → Tool → Generation

普通请求只能证明 Generation 可上报；工具节点需要客户端真实执行工具并埋点。下面通过 Nginx 发起两次模型调用，模型提出 `add_numbers` 后由脚本实际计算 `17 + 25`，然后将工具结果发回模型；脚本通过 Langfuse API 检查同一 Trace 中的 Agent、两个不同的 Generation、Tool 和父子关系，以及模型、输入输出、Token 和时间。测试产生真实调用和新的追踪数据。

先将容器名改为现场 LiteLLM 容器名；检查它具有 SDK 和访问 Langfuse 所需环境变量。下列检查只输出是否存在，不输出值。其他自定义镜像必须事先离线准备兼容 SDK，不能临时联网安装。

```bash
LITELLM_CONTAINER=llm-a100-litellm-1  # 改为现场实际容器名
export LITELLM_CONTAINER
docker exec -i "$LITELLM_CONTAINER" python - <<'PY'
import importlib.metadata as m, os
names = ('LITELLM_MASTER_KEY', 'LANGFUSE_HOST', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')
print({n: bool(os.environ.get(n)) for n in names})
print('Langfuse SDK:', m.version('langfuse'))
if not all(os.environ.get(n) for n in names):
    raise SystemExit('Missing required settings')
PY
```

`GATEWAY_URL` 必须从 LiteLLM 容器可访问，填写 Nginx 的 scheme、域名或 IP 和端口，不带 `/v1`；`GATEWAY_MODEL` 替换为实际网关模型别名。HTTPS 使用证书 SAN 匹配的地址，CA 文件为宿主机上的可信 CA 文件；若系统 CA 已受容器信任，或现场明确使用 HTTP，可留空。Langfuse 若使用 HTTPS，其 CA 也须由容器信任，必要时在容器内预先配置 `SSL_CERT_FILE`。

```bash
set -euo pipefail
TRACE_SCRIPT=/opt/llm-offline-full-chain-update-20261116/manual/examples/offline_tool_trace_check.py
test -f "$TRACE_SCRIPT"
read -r -p '容器可访问的 Nginx URL（不含 /v1）：' GATEWAY_URL
read -r -p '网关 CA 文件路径；系统已信任或 HTTP 留空：' GATEWAY_CA_PATH
read -r -p '网关模型别名，例如 qwen-strata：' GATEWAY_MODEL
GATEWAY_CA_PEM=''
if [ -n "$GATEWAY_CA_PATH" ]; then
  test -r "$GATEWAY_CA_PATH"
  GATEWAY_CA_PEM=$(cat "$GATEWAY_CA_PATH")
fi
export GATEWAY_URL GATEWAY_MODEL GATEWAY_CA_PEM
docker exec -i -e GATEWAY_URL -e GATEWAY_MODEL -e GATEWAY_CA_PEM \
  "$LITELLM_CONTAINER" python - < "$TRACE_SCRIPT"
unset GATEWAY_URL GATEWAY_MODEL GATEWAY_CA_PEM GATEWAY_CA_PATH
```

脚本最多等待 180 秒让异步事件入库；通过时记下 `trace_id`，在 Langfuse Tracing 搜索并展开树。验收应看到 1 个 AGENT、2 个不同的 GENERATION 和 1 个 TOOL，节点包含实际输入、输出、模型和 usage。SDK 验证节点使用 `offline-validation` 环境，网关 Generation 可能为 `production`；查询时选择全部环境。若查不到，确认容器内 `LANGFUSE_HOST` 可访问、项目 key 指向同一项目、回调已启用，查看 worker 和网关日志；不输出凭据。

这是逻辑调用链，不代表 Nginx 或 Strata 已自动产生独立内部 span。排查各服务时结合 Nginx 和 Strata 日志、请求时间或实际可关联的请求 ID；不能凭示意图判定每跳都已埋点。客户端自己的工具也需要在真实执行位置埋点并传播 Trace 上下文。

## 9. 失败时怎么处理

| 现象 | 先检查 |
| --- | --- |
| 分卷或包 SHA 不一致 | 不解压、不运行；核对文件完整性和可信来源，重新获取错误文件。 |
| `docker load` / Compose 失败 | CPU 架构、Docker 版本、磁盘及文件完整性；不使用联网拉取兜底。 |
| Nginx 502 | LiteLLM / Langfuse 是否就绪、网络别名和端口；上游容器重建后检查 Nginx 解析。 |
| LiteLLM 401、400 或连接失败 | 网关 key 与 Strata key 是否分别正确、模型名、地址及上下文 / 输出预算。 |
| Langfuse 无新记录 | 回调配置、项目 key、Web / Worker、Redis、ClickHouse、MinIO、实际 Trace；旧版本 Trace API 的 404 不能独立证明数据丢失。 |
| 内存不足或 swap 持续上涨 | 停止继续操作，核查进程与容器、PLE 锁页、大页池及 cgroup；不要重启 Strata 或删除数据作为默认处理。 |

已有实例重部署失败时，按第 6.1 节备份和事先确认的恢复顺序恢复原配置、镜像及服务；先核对备份后有无新的业务变更。全新实例失败时保留日志及私有配置，按现场恢复流程处理；不要使用删除卷或清空数据库的命令试错。Strata JSON、`memlock` 和 HugeTLB 大页的详细配置及回滚见新版完整主手册附录 A。
