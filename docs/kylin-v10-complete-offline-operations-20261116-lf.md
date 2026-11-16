# 麒麟 V10 内网离线部署：LF 校验版完整操作指南

本手册对应 2026-11-16 LF 修复版：主包内部下载清单及监控包内校验清单均已修复，镜像和业务数据内容保持不变。Release 复用原包的五个分卷，并通过经过哈希校验的小修复文件生成完整 LF 主包；本机离线交付目录已经包含修好的完整主包。

本手册面向运维人员，覆盖：GitHub 下载与校验、五分卷合并、逐层解压、九镜像导入、历史备份解密、适配目标机、恢复数据、启动服务和验证工具调用链。命令按本文顺序在 **同一个 Bash 会话**执行。若更换终端，要重新设置命令中使用的变量。

适用目标：`x86_64` 麒麟 V10，已有可用 Docker、本机或网络可达的 Strata 和 Qwen 模型；本手册使用新目录和独立 Compose 项目 `llm-history` 恢复 2026-11-16 的云端历史数据。已有数据库或已有网关的数据不能与本流程混用。安装前确认端口和资源允许新建 PostgreSQL、ClickHouse、Redis、MinIO、Langfuse、LiteLLM 和 Nginx。

**验收边界：**历史数据包已在隔离 WSL 分别测试 PostgreSQL、ClickHouse、Redis 与 MinIO 恢复；恢复后的云端真实推理与 Langfuse 工具链也已验证。目标麒麟机、MinIO 全量对象读取以及整套服务的断网恢复尚未现场验收。历史快照只覆盖 2026-11-16 12:27:26–12:28:30 UTC 的备份窗口；MinIO 在复制数据目录时仍在运行，其他直连存储写入和未处理的延迟任务可能造成跨组件差异，目标机须抽样读取对象并核对业务数据。

业务请求路径是 `客户端 → Nginx → LiteLLM → Strata → Qwen`，答案沿原路返回。LiteLLM 回调异步上报 Langfuse；真实工具执行必须由客户端埋点。Nginx 和 Strata 的独立内部 span 不会仅凭部署而自动出现，须结合日志核对。

## 1. 准备物料

私有 [GitHub Release](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-reuse-amd64-20261116) 中需要：

| 文件 | 用途 |
| --- | --- |
| `kylin-v10-gateway-reuse-amd64-20261116.tar.part01` 至 `part05` | 五个原始分卷，先合并成原包 |
| `BUNDLE-PARTS-SHA256SUMS`、`kylin-v10-gateway-reuse-amd64-20261116.tar.sha256` | 原分卷及合并后原包的校验 |
| `kylin-v10-gateway-lf-patch-20261116.json`、`apply_offline_bundle_lf.py` 及各自 `.sha256` | 本地生成 LF 修复版的修复数据和脚本 |
| `kylin-v10-gateway-reuse-amd64-20261116-lf.tar.sha256` | 合并后主包校验 |
| `offline-full-chain-update-20261116.tar.gz` 和同名 `.sha256` | 手册、配置示例和真实工具链测试脚本 |
| `cloud-history-20261116.tar.gz.gpg` 和同名 `.sha256` | 加密的云端历史数据及原始私有配置 |
| `kylin-v10-complete-offline-operations-20261116-lf.md` 和同名 `.sha256` | 本手册，随介质携带 |

另行通过受控渠道携带 `history-decryption.key`，该密钥不在 GitHub。加密历史包含生产账号、密钥、证书、调用内容和历史记录，必须按私有资料管理。

主包含 Nginx、LiteLLM、Langfuse Web / Worker、PostgreSQL、ClickHouse、Redis、MinIO 和 MinIO Client 共九个 `linux/amd64` 镜像、Compose 2.29.7 和配置；监控为单独物料。**不包含** Strata 和模型、GPU 驱动、目标麒麟完整 RPM 依赖闭包。已有 Docker、GPU、Strata 和模型由现场复用；如缺 GnuPG、Python 或其他工具，提前按目标麒麟 SP / Build 准备离线依赖，不能用 CentOS 7 候选包直接覆盖现有运行时。

磁盘须同时容纳原始包、约 5.71 GB 的展开 tar、镜像层、恢复数据和备份；建议先预留至少 30 GiB，再按实际数据与备份规模增加。检查 `/opt` 和 Docker 数据目录所在文件系统的可用空间。

## 2. 联网准备机：下载、校验、合并

**已从交付目录取得完整 `-lf.tar` 的运维人员**：无需重新下载或合并分卷；确认同目录有主包 `.sha256`、补充包、历史包和本手册各自的 `.sha256`，并单独取得解密密钥，然后直接从第 3 节开始。

准备机需要 Python 3.6 或更高版本、`sha256sum`、`cat`，以及已登录且有仓库访问权限的 `gh`；也可在 Release 网页逐一下载同名文件。以下仅在联网准备机执行：

```bash
set -euo pipefail
mkdir -p /srv/llm-offline-download
gh release download offline-reuse-amd64-20261116 \
  -R tyloryang/litellm-langfuse-monitoring \
  -D /srv/llm-offline-download \
  -p 'kylin-v10-gateway-reuse-amd64-20261116.tar.part*' \
  -p 'BUNDLE-PARTS-SHA256SUMS' \
  -p 'kylin-v10-gateway-reuse-amd64-20261116.tar.sha256' \
  -p 'kylin-v10-gateway-lf-patch-20261116.json*' \
  -p 'apply_offline_bundle_lf.py*' \
  -p 'kylin-v10-gateway-reuse-amd64-20261116-lf.tar.sha256' \
  -p 'offline-full-chain-update-20261116.tar.gz*' \
  -p 'cloud-history-20261116.tar.gz.gpg*' \
  -p 'kylin-v10-complete-offline-operations-20261116-lf.md*'
cd /srv/llm-offline-download
sha256sum -c BUNDLE-PARTS-SHA256SUMS
sha256sum -c kylin-v10-gateway-lf-patch-20261116.json.sha256
sha256sum -c apply_offline_bundle_lf.py.sha256
sha256sum -c offline-full-chain-update-20261116.tar.gz.sha256
sha256sum -c cloud-history-20261116.tar.gz.gpg.sha256
sha256sum -c kylin-v10-complete-offline-operations-20261116-lf.md.sha256
test ! -e kylin-v10-gateway-reuse-amd64-20261116.tar
cat kylin-v10-gateway-reuse-amd64-20261116.tar.part01 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part02 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part03 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part04 \
    kylin-v10-gateway-reuse-amd64-20261116.tar.part05 \
    > kylin-v10-gateway-reuse-amd64-20261116.tar
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
python3 apply_offline_bundle_lf.py \
  --source kylin-v10-gateway-reuse-amd64-20261116.tar \
  --patch kylin-v10-gateway-lf-patch-20261116.json \
  --output kylin-v10-gateway-reuse-amd64-20261116-lf.tar
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116-lf.tar.sha256
```

合并包应为 `2,110,976,000` 字节，SHA-256 为 `45cbd4df7e66dca3f7298b8dbc75b666772f0c746dd1716d64f7128be61f52d1`。通过受控渠道核对校验值。把以下文件一起拷入内网介质；已合并后无需再带分卷：

```text
kylin-v10-gateway-reuse-amd64-20261116-lf.tar
kylin-v10-gateway-reuse-amd64-20261116-lf.tar.sha256
offline-full-chain-update-20261116.tar.gz
offline-full-chain-update-20261116.tar.gz.sha256
cloud-history-20261116.tar.gz.gpg
cloud-history-20261116.tar.gz.gpg.sha256
kylin-v10-complete-offline-operations-20261116-lf.md
kylin-v10-complete-offline-operations-20261116-lf.md.sha256
```

另外单独携带 `history-decryption.key`。不能取得密钥时只能新装空实例，不能恢复本历史包。

例如，密钥位于交付人的 Windows 账户目录时，可在 Windows PowerShell 中把它复制到**另一份受控介质**；提示时填写密钥介质的实际目录，不要把密钥提交到 GitHub：

```powershell
$keySource = Join-Path $env:USERPROFILE '.codex\private-cloud-history-20261116\history-decryption.key'
$keyMedia = Read-Host '密钥介质目录，例如 X:\secure'
if (!(Test-Path -LiteralPath $keySource -PathType Leaf)) { throw '找不到密钥文件' }
if (!(Test-Path -LiteralPath $keyMedia -PathType Container)) { throw '密钥介质目录不存在' }
Copy-Item -LiteralPath $keySource -Destination (Join-Path $keyMedia 'history-decryption.key')
```

## 3. 离线目标机：检查环境与已有 Strata


以有 Docker 管理权限的受控管理员执行；后续命令为 Bash。确认使用本机 Docker，不要无意连接远端 daemon：

```bash
cat /etc/os-release
uname -m
docker version
docker info --format 'root={{.DockerRootDir}} server={{.ServerVersion}}'
docker context show
for tool in bash python3 openssl tar gzip sha256sum gpg grep tr seq vi; do
  command -v "$tool" || { echo "缺少工具：$tool" >&2; exit 1; }
done
df -h / /opt "$(docker info --format '{{.DockerRootDir}}')"
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo '本机没有 nvidia-smi；若 Strata 在本机运行，请先核对 GPU 驱动'
fi
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

## 4. 离线目标机：校验并逐层解压


将第 2 节的文件复制到一个目录，以下使用占位路径，先改成实际挂载路径；不要覆盖已有 `/opt/llm-handoff` 或 `/opt/llm-offline-full-chain-update-20261116`：

```bash
set -euo pipefail
cd /实际/离线介质目录
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116-lf.tar.sha256
sha256sum -c offline-full-chain-update-20261116.tar.gz.sha256
test ! -e /opt/llm-handoff
test ! -e /opt/llm-offline-full-chain-update-20261116
tar -xf kylin-v10-gateway-reuse-amd64-20261116-lf.tar -C /opt
tar -xzf offline-full-chain-update-20261116.tar.gz -C /opt
cd /opt/llm-handoff
sha256sum -c HANDOFF-SHA256SUMS
cd /opt/llm-offline-full-chain-update-20261116
sha256sum -c MANUAL-UPDATE-SHA256SUMS
```

此时没有安装镜像或启动容器。继续使用当前这份 LF 版手册；主包和补充包中的旧文档为历史快照，可能仍引用旧版包名和校验值。查看 Strata JSON、Compose 与大页时，可阅读补充包中完整手册的附录 A。

接下来只恢复**网关候选包的 tar**，不展开监控包：

```bash
set -euo pipefail
cd /opt/llm-handoff/downloads
grep 'candidate-20261116-v3.tar.gz$' SHA256SUMS | sha256sum -c -
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
gzip -t nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz
gzip -dc nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz \
  > nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
printf '%s  %s\n' \
  627e4b44ba9fcf4da8a7f9f3d1227348815ca021d153c978236758ced206b702 \
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

得到的 `$BUNDLE` 是**离线物料目录**，不是正在运行的服务目录。其 `images/images.tar` 为九个镜像，`images.env` 为导入后的镜像名，`config/prepare.sh` 用于创建新的独立部署目录，`host/compose/docker-compose` 为离线 Compose 2.29.7 二进制；不能把它当作 Docker Engine。下一步按本文恢复历史数据。


解压后的主要目录如下，所有命令以实际文件为准：

```text
/opt/llm-handoff/                         原包及 HANDOFF-SHA256SUMS
  downloads/                              网关压缩包、监控包、源码快照
  manual/                                 原打包时文档
  patches/                                Grafana 看板及公式补丁
  system/                                 尚未验证的宿主工具 / RPM 候选
/opt/llm-offline/nginx-litellm-langfuse/  恢复出的九镜像物料目录
  images/images.tar                       docker load 输入
  images.env、images.lock.json            镜像本地别名与 ID
  config/、source.tar                     新装配置示例与源码
  host/compose/docker-compose             离线 Compose 2.29.7
/opt/llm-offline-full-chain-update-20261116/
  manual/examples/offline_tool_trace_check.py  工具链验收脚本
```

本手册以恢复历史数据为主，`monitoring-amd64-20261116.tar` 和 `patches/` 不会自动启动；如果现场复用既有 Prometheus / Grafana，不要覆盖它们。

## 5. 解密历史数据与原始配置

解密后 `$HISTORY` 包含以下文件；`cloud-private-config.tar` 解出到独立 `$WORK`：

| 文件 | 内容 |
| --- | --- |
| `postgres-langfuse.dump`、`postgres-globals.sql` | PostgreSQL 数据库和全局角色信息；本流程不自动覆盖角色 |
| `clickhouse-default.zip` | ClickHouse 原生备份 |
| `redis.rdb` | Redis 快照；须先恢复 RDB 再生成 AOF |
| `minio-single-node-data.tar` | 单节点 MinIO 对象及内部数据 |
| `source-usage.sqlite3` | 来源 IP 与 Token 用量账本 |
| `cloud-private-config.tar` | 云端 Compose、Nginx / LiteLLM 配置、私有 `.env` 和证书 |
| `snapshot-start-utc.txt`、`snapshot-end-utc.txt` | 云端快照时间范围 |


目标机应有 Bash、Docker、tar、gzip、sha256sum、Python 3 和 `gpg`。如麒麟机器没有 GnuPG，应提前准备适配该 SP / Build 的离线依赖，或在受信任准备机解密后通过受控介质转运；不要在内网运行联网安装命令。确认待恢复路径不存在且磁盘空间充足，使用有 Docker 管理权限的管理员执行：

```bash
set -euo pipefail
umask 077
cd /实际/离线介质目录
sha256sum -c cloud-history-20261116.tar.gz.gpg.sha256
printf '%s  %s\n' 7d6f8b1ba4344a58e1890622fb45bc36f0778c65851be93e7d50ef2cad05b544 cloud-history-20261116.tar.gz.gpg | sha256sum -c -
HISTORY=/opt/llm-cloud-history-20261116
WORK=/opt/llm-history-gateway-20261116
read -r -p '受控密钥文件的绝对路径：' KEY
test -r "$KEY"
test ! -e "$HISTORY"
test ! -e "$WORK"
mkdir -m 700 "$HISTORY" "$WORK"
GPG_PIN=()
if gpg --dump-options | grep -x -- '--pinentry-mode' >/dev/null; then GPG_PIN=(--pinentry-mode loopback); fi
gpg --batch "${GPG_PIN[@]}" --passphrase-file "$KEY" --decrypt cloud-history-20261116.tar.gz.gpg | tar -xz -C "$HISTORY"
tar -xf "$HISTORY/cloud-private-config.tar" -C "$WORK"
chmod 700 "$HISTORY" "$WORK"
chmod 600 "$WORK/.env"
ls -lh "$HISTORY"
```

解密后的目录含生产凭据、证书私钥和历史请求，需保持受控权限。请先检查目标机磁盘、备份与已有服务；此流程不得指向已有数据目录，也不要用 `docker compose down -v` 删除任何卷。

## 6. 修改目标机配置并导入镜像


`$WORK` 中是云端原始配置，不可直接认为适合目标内网。检查并修改：

1. `.env` 中 `NEXTAUTH_URL`、`LANGFUSE_MEDIA_PUBLIC_ENDPOINT`、`HOST_IP`、可信代理 CIDR、Strata 认证及域名按目标机实际情况调整。保留原来的 PostgreSQL / ClickHouse / Redis / MinIO 账号密码，以及 `SALT`、`ENCRYPTION_KEY`、`NEXTAUTH_SECRET`、原 Langfuse 项目公私钥；否则已有数据或密钥可能无法使用。
2. `litellm/config.yaml` 的 Strata `api_base` 改为 LiteLLM 容器可访问的目标地址，例如 `http://目标IP:8080/v1`，模型 ID 改为目标 `/v1/models` 实际值。原 `strata` Compose 服务属于云端，目标机复用已有 Strata，不要照搬启动。
3. `docker-compose.yml` 的端口绑定按现场修改；快照原始值中网关为宿主 8800 HTTP、Langfuse 为宿主 8801 HTTPS，MinIO 为宿主回环 9090。云端自签证书及其域名不一定适合目标机，按现场替换 `nginx/certs/`；同时核对 `nginx/nginx.conf`、反向代理信任链及访问控制。
4. `.env` 已含生产密钥，不应打印、提交或放在普通共享目录。为避免 Compose 优先使用宿主同名环境变量，以下示例使用 `env -i` 和本机默认 Docker socket。非默认 socket / rootless 环境需由现场管理员适配。

### 6.1 交互式配置上游 Strata

先根据第 3 节 `/v1/models` 返回值确定真实模型 ID，地址必须是 LiteLLM 容器可访问的 `http://IPv4:端口/v1`。以下代码只处理这次云端快照中唯一的 `qwen-strata` 配置；真实文件格式不同则停止并手工检查。API key 留空可复用快照中的旧值；非空 key 不回显并替换旧值。写入前备份两个文件，不打印原密钥。

```bash
set -euo pipefail
cd "$WORK"
test ! -e .env.before-target && test ! -e litellm/config.yaml.before-target
cp -p .env .env.before-target
cp -p litellm/config.yaml litellm/config.yaml.before-target
chmod 600 .env.before-target litellm/config.yaml.before-target
read -r -p 'Strata API base，例如 http://192.168.1.20:8080/v1：' STRATA_API_BASE
read -r -p 'Strata 实际模型 ID：' STRATA_MODEL_ID
export STRATA_API_BASE STRATA_MODEL_ID
python3 - <<'PY'
import getpass, ipaddress, os, re
from pathlib import Path
from urllib.parse import urlsplit
base = os.environ['STRATA_API_BASE']
model = os.environ['STRATA_MODEL_ID']
try:
    url = urlsplit(base)
    ip = ipaddress.IPv4Address(url.hostname)
    if (url.scheme != 'http' or not url.port or url.path != '/v1' or url.query or url.fragment
            or url.username or url.password or ip.is_loopback or ip.is_unspecified or ip.is_multicast):
        raise ValueError('invalid URL')
    if not re.fullmatch(r'[A-Za-z0-9._/-]+', model):
        raise ValueError('invalid model ID')
except (TypeError, ValueError) as exc:
    raise SystemExit('Invalid input: ' + str(exc))
config, env = Path('litellm/config.yaml'), Path('.env')
text, envtext = config.read_text(encoding='utf8'), env.read_text(encoding='utf8')
old_base = 'api_base: http://strata:8080/v1'
old_model = 'model: openai/qwen3.8-flash-next-iq3_xxs'
if text.count(old_base) != 1 or text.count(old_model) != 1:
    raise SystemExit('Unexpected config; inspect manually')
key = getpass.getpass('Strata API key（留空复用旧值）: ')
if key:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
        raise SystemExit('Unsupported key characters; configure manually')
    if key != getpass.getpass('再次输入 Strata API key: '):
        raise SystemExit('Keys differ; files unchanged')
    envtext, count = re.subn(r'^STRATA_API_KEY=.*$', lambda _: 'STRATA_API_KEY=' + key,
                              envtext, count=1, flags=re.M)
    if count != 1:
        raise SystemExit('STRATA_API_KEY missing')
text = text.replace(old_base, 'api_base: ' + base, 1).replace(old_model, 'model: openai/' + model, 1)
config.write_text(text, encoding='utf8')
env.write_text(envtext, encoding='utf8')
env.chmod(0o600)
print('Configured Strata; key omitted')
PY
unset STRATA_API_BASE STRATA_MODEL_ID
```

上面使用两个独立文件写入，如中断需通过备份核对。模型 ID 应原样填写 Strata 返回的值；脚本会在该值之前加上 LiteLLM 的 `openai/` 提供商前缀。

### 6.2 调整域名、端口和证书

按现场网络执行下面的编辑命令（`vi` 中编辑后输入 `:wq` 保存）。至少确认 `.env` 的 `NEXTAUTH_URL`、`LANGFUSE_MEDIA_PUBLIC_ENDPOINT` 和可信代理，`docker-compose.yml` 的宿主端口，以及 `nginx/nginx.conf` 的监听和转发；换上与实际访问地址匹配的证书。不要改动原 `SALT`、`ENCRYPTION_KEY`、`NEXTAUTH_SECRET`、数据库 / 对象存储密码和 Langfuse 项目 key，否则历史数据可能无法读取。

```bash
cd "$WORK"
vi .env
vi docker-compose.yml
vi nginx/nginx.conf
ls -l nginx/certs
```

原云配置常用宿主 8800 为网关 HTTP、8801 为 Langfuse HTTPS；如改变端口，还须同步客户端 URL、反向代理和防火墙。不要直接使用云端公网映射。HTTP 可在现场允许的受控网络中使用；HTTPS 客户端需要信任 CA 且证书 SAN 与访问地址一致。

### 6.3 导入镜像并生成恢复覆盖文件

先在目标机按原始包的 `SHA256SUMS` 校验并执行 `docker load -i images/images.tar`。`docker load` 会导入九个标签，应先核对同名标签，避免影响其他项目。确认后执行：

```bash
set -euo pipefail
BUNDLE=/opt/llm-offline/nginx-litellm-langfuse
cd "$BUNDLE"
sha256sum -c SHA256SUMS
docker load -i images/images.tar
```

以下代码只替换 `.env` 中九个镜像引用，保留其余配置与密码：

```bash
set -euo pipefail
BUNDLE=/opt/llm-offline/nginx-litellm-langfuse
WORK=/opt/llm-history-gateway-20261116
HISTORY=/opt/llm-cloud-history-20261116
test -f "$BUNDLE/images.env"
cd "$WORK"
cp -p .env .env.before-image-aliases
python3 - "$BUNDLE/images.env" <<'PY'
import re,sys
from pathlib import Path
aliases = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        if not re.fullmatch(r'[A-Z_]+_IMAGE', k) or not v.strip():
            raise SystemExit('Unexpected image alias line')
        aliases[k] = v.strip()
if len(aliases) != 9:
    raise SystemExit('Expected 9 image aliases')
p = Path('.env')
text = p.read_text()
for key, value in aliases.items():
    pattern = r'^' + re.escape(key) + r'=.*$'
    text, count = re.subn(pattern, lambda _: key + '=' + value, text, flags=re.M)
    if count != 1:
        raise SystemExit('Expected one assignment for ' + key)
p.write_text(text)
p.chmod(0o600)
print('Nine image aliases updated; secrets omitted')
PY
cat > restore-override.yml <<'YAML'
services:
  langfuse-web:
    env_file:
      - .env
YAML
cat > restore-redis-bootstrap.yml <<'YAML'
services:
  redis:
    command: ["sh", "-c", "exec redis-server --appendonly no --requirepass \"$$REDIS_AUTH\""]
YAML
COMPOSE="$BUNDLE/host/compose/docker-compose"
PROJECT=llm-history
test -x "$COMPOSE"
test -z "$(docker ps -aq --filter label=com.docker.compose.project=$PROJECT)"
if docker network inspect "${PROJECT}_backend" >/dev/null 2>&1; then
  echo 'Project network exists; stop and inspect' >&2; exit 1
fi
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml config --quiet
```

`restore-override.yml` 仅提供云端 Langfuse Web 所需的 `env_file`，不加入云端 Strata 服务；所有请求上游要改成目标机可达地址。启动前还须核对端口未被占用、可信 CA 和依赖资源充足。

## 7. 恢复历史数据并启动服务


以下命令要求 `$WORK/data` 不存在、项目名未占用，目标是独立的新数据库，不能覆盖已有项目。先放入 MinIO、Redis 和 SQLite；MinIO 单节点归档只适用于与云端相同的单节点 MinIO 版本和目录布局。

```bash
set -euo pipefail
cd "$WORK"
test ! -e data
mkdir -p data/{postgres,clickhouse,clickhouse-logs,redis,minio,source-usage,nginx-logs}
tar -xf "$HISTORY/minio-single-node-data.tar" -C data/minio
cp "$HISTORY/redis.rdb" data/redis/dump.rdb
cp "$HISTORY/source-usage.sqlite3" data/source-usage/usage.sqlite3
```

Redis 必须先以 RDB 模式启动，再生成 AOF。直接以 `--appendonly yes` 首次启动会忽略本次 RDB；这是本次恢复试验发现的实际问题。以下命令只在独立项目中操作：

```bash
set -euo pipefail
cd "$WORK"
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml -f restore-redis-bootstrap.yml up -d --no-deps --pull never --no-build redis
for i in $(seq 1 60); do
  if docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli ping' 2>/dev/null | grep -qx PONG; then break; fi
  sleep 2
done
docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli ping' | grep -qx PONG
docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli DBSIZE'
docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli CONFIG SET appendonly yes' | grep -q OK
for i in $(seq 1 60); do
  if docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli INFO persistence' | tr -d '\r' | grep -qx 'aof_rewrite_in_progress:0'; then break; fi
  sleep 2
done
docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli INFO persistence' | tr -d '\r' | grep -qx 'aof_last_bgrewrite_status:ok'
docker exec "${PROJECT}-redis-1" sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli INFO persistence' | tr -d '\r' | grep -qx 'aof_rewrite_in_progress:0'
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml -f restore-redis-bootstrap.yml stop redis
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml up -d --no-deps --pull never --no-build postgres clickhouse redis minio
```

等待 PostgreSQL、ClickHouse、Redis 健康和 MinIO 就绪后，确认数据库是全新空库，再恢复 PostgreSQL 和 ClickHouse；以下不导入 `postgres-globals.sql`，避免覆盖目标实例角色。恢复使用新容器中的数据库管理员账号：

```bash
set -euo pipefail
for i in $(seq 1 90); do
  pg=$(docker inspect "${PROJECT}-postgres-1" --format '{{.State.Health.Status}}')
  ch=$(docker inspect "${PROJECT}-clickhouse-1" --format '{{.State.Health.Status}}')
  rd=$(docker inspect "${PROJECT}-redis-1" --format '{{.State.Health.Status}}')
  [ "$pg" = healthy ] && [ "$ch" = healthy ] && [ "$rd" = healthy ] && break
  sleep 2
done
test "$pg" = healthy && test "$ch" = healthy && test "$rd" = healthy
pg_tables=$(docker exec "${PROJECT}-postgres-1" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d langfuse -Atqc "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = current_schema()"')
ch_tables=$(docker exec "${PROJECT}-clickhouse-1" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "SELECT count() FROM system.tables WHERE database = currentDatabase()"')
test "$pg_tables" = 0 && test "$ch_tables" = 0 || { echo 'Database is not empty; stop' >&2; exit 1; }
docker exec -i "${PROJECT}-postgres-1" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -U "$POSTGRES_USER" -d langfuse --no-owner --no-privileges --exit-on-error' < "$HISTORY/postgres-langfuse.dump"
docker exec --user root "${PROJECT}-clickhouse-1" sh -c 'mkdir -p /var/lib/clickhouse/backups && chown clickhouse:clickhouse /var/lib/clickhouse/backups'
docker cp "$HISTORY/clickhouse-default.zip" "${PROJECT}-clickhouse-1:/var/lib/clickhouse/backups/cloud-history.zip"
docker exec --user root "${PROJECT}-clickhouse-1" chmod 644 /var/lib/clickhouse/backups/cloud-history.zip
docker exec "${PROJECT}-clickhouse-1" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "RESTORE DATABASE default FROM File('\''cloud-history.zip'\'')"'
docker exec "${PROJECT}-clickhouse-1" sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "SELECT count() FROM default.events_full"'
```

最后启动 MinIO 初始化、Langfuse、LiteLLM 和 Nginx，查看全部服务状态与日志，确认使用的镜像均为本地别名：

```bash
set -euo pipefail
cd "$WORK"
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml up -d --pull never --no-build
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml ps -a
docker exec "${PROJECT}-nginx-1" nginx -t
```

## 8. 访问与全链路验收


先检查新项目的容器、资源和 Nginx：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker exec ${PROJECT}-nginx-1 nginx -t
free -h
df -h / /opt
```

原云快照的默认入口示例为 `http://目标IP:8800/v1`（LiteLLM API）、`http://目标IP:8800/ui`（LiteLLM UI，实际可用性以该版本页面为准）、`https://目标域名:8801/`（Langfuse）；实际端口、协议和证书以第 6 节现场修改结果为准。正式环境使用可信 CA 和匹配 SAN 的证书；客户端实际端口和外部映射按现场填写，不能假设云端端口映射存在。既有实例使用原账号和密钥，新实例的初始化信息在受控 `.env` 中；不要把真实密码粘贴到终端记录或截图。

恢复历史数据后使用云端原 Langfuse 账户登录。受控配置 `$WORK/.env` 中的 `LANGFUSE_INIT_USER_EMAIL`、`LANGFUSE_INIT_USER_PASSWORD` 仅是初始化凭据；如果账户后来修改过密码，应使用修改后的密码。网关测试使用 `$WORK/.env` 中的 `LITELLM_MASTER_KEY` 或已授权的虚拟密钥；可在受控终端用 `vi "$WORK/.env"` 查看，测试脚本会通过不回显提示读取密钥。

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
LITELLM_CONTAINER=${PROJECT}-litellm-1
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

## 9. 故障处理与验收记录

| 问题 | 检查方法 |
| --- | --- |
| SHA 不符、解密失败 | 对照可信校验值、文件名和独立密钥；不要继续解压或启动。 |
| `docker load` 失败 | 查看架构、包是否完整、Docker 数据盘空余；不要用联网拉取兜底。 |
| Redis 恢复后键数为 0 | 查看是否先按 RDB 模式启动、生成 AOF 并确认重写完成；不要覆盖已有数据目录重试。 |
| Nginx 502 / 模型调用失败 | 检查 LiteLLM、Strata 的容器网络、真实模型 ID 和 API key；容器内 `127.0.0.1` 不是宿主机。 |
| 历史记录为空 | 核对进入的是正确项目、原加密密钥和数据库、备份截止时间、ClickHouse `events_full`、Redis / MinIO 状态。 |
| 新请求没有 Trace / Tool | 查看回调、Langfuse Web / Worker / Redis、项目 key、环境筛选；客户端必须真实执行并上报工具节点。 |

日志中的环境变量、请求内容和数据库可能包含私有信息。遇到失败，先保留原始加密包与私有配置，在确认备份和目标数据状态后再按现场恢复流程处理。

```bash
cd "$WORK"
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml ps -a
env -i PATH="$PATH" HOME="$HOME" "$COMPOSE" -p "$PROJECT" -f docker-compose.yml -f restore-override.yml logs --tail 100 nginx litellm langfuse-web langfuse-worker
free -h
df -h / /opt
```

在断网条件下记录包校验、镜像 ID、服务状态、历史 Trace 抽查、新的非流式和流式请求、工具链 Trace、目标机 IP、访问地址和恢复演练结果。Redis 过期键的数量会随时间变化，延迟队列可能重放；应由业务方确认数据截止时间和任务状态。Grafana / Prometheus 的复用或单独部署需另按其现场配置执行，本流程不会自动更改已有监控。
