# 云端历史数据离线迁移与恢复

本手册用于把 2026-11-16 云端 Nginx、LiteLLM、Langfuse 的历史状态恢复到**新的独立部署目录**。目标机复用已有 Docker、Strata 和模型；先按 [离线部署 README](offline-deployment-README.md) 下载原始五分卷、校验、合并并解压出 `/opt/llm-offline/nginx-litellm-langfuse`。本手册须与加密历史包、其校验文件及单独转运的解密密钥一起携带。

本次云端运行的九个镜像完整 ID 与已发布的原始离线包全部一致，`images/images.tar` 在云端和包中的 SHA-256 均为 `9adc3bf347234e3f57c1dff4ea66071aadd227b32fb9716ebdc9a194cd1fde51`。无需重复下载另一套镜像。历史包存放在 [同一 Release](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-reuse-amd64-20261116) 的 `cloud-history-20261116.tar.gz.gpg` 和同名 `.sha256`；解密密钥不在 GitHub，需要从交付方受控传递。归档的校验值为 `7d6f8b1ba4344a58e1890622fb45bc36f0778c65851be93e7d50ef2cad05b544`。

备份时间为 **2026-11-16 12:27:26–12:28:30 UTC**。期间停止 Nginx 和 LiteLLM，等待 Redis 中 Bull 队列的 wait / active 列表清空，停止 Langfuse Web / Worker，再进行快照；随后四个服务已恢复，Strata 持续运行。无法据此保证外部直连数据库或对象存储的其他写入也已停止，延迟重试任务和各存储的精确跨库时间点需要现场核对。恢复后应核查指定 Trace 和数据范围，不能只看服务状态。

基于此次操作整理的快照流程和恢复保护可查阅 [历史备份脚本](../ops/cloud-history-snapshot-example-20261116.sh)，Release 也提供该脚本及独立 SHA-256。它固定了云端路径和服务名，会中断服务、停止 MinIO 后复制其数据，并产生明文暂存；任何复用都须先按现场审阅并安排停写。此次已在加密包校验和上传后清除脚本产生的临时明文副本，仅保留加密包。

## 一、文件内容和验证情况

解密后包含：

| 文件 | 用途 |
| --- | --- |
| `postgres-langfuse.dump` | PostgreSQL `langfuse` 数据库的 `pg_dump` 自定义格式备份 |
| `postgres-globals.sql` | 数据库全局角色信息；含敏感信息，不要直接覆盖目标已有角色 |
| `clickhouse-default.zip` | ClickHouse `default` 数据库的原生 BACKUP |
| `redis.rdb` | Redis 队列和状态的 RDB；恢复时需先按本指南生成 AOF |
| `minio-single-node-data.tar` | 云端单节点 MinIO 数据目录快照，包含对象及内部元数据 |
| `source-usage.sqlite3` | 来源 IP / Token 用量账本，使用 SQLite 在线备份接口生成 |
| `cloud-private-config.tar` | 云端原始 `.env`、Compose、Nginx / LiteLLM 配置和证书，包含真实密钥与私钥 |
| `snapshot-start-utc.txt`、`snapshot-end-utc.txt` | 备份窗口 |

加密包已完成 SHA-256、解密、ClickHouse ZIP 和 SQLite 完整性检查。隔离 WSL 测试使用 PostgreSQL 17 和 ClickHouse 26.3.62.3（云端为 26.3.21.7），恢复了 PostgreSQL 的 84 张 public 表、ClickHouse 的 18 张表并查询到 166 条 `events_full` 事件。Redis RDB 在 Redis 7 中恢复出 162 个未过期键，生成 AOF 后重启仍有 162 个键；过期键会随时间减少。隔离恢复单节点 MinIO 数据后可列出 147 个对象。**整套 Langfuse UI、对象逐个读取和麒麟目标机尚未做完整恢复验收。**

## 二、校验、解密与检查目录

目标机应有 Bash、Docker、tar、gzip、sha256sum、Python 3 和 `gpg`。如麒麟机器没有 GnuPG，应提前准备适配该 SP / Build 的离线依赖，或在受信任准备机解密后通过受控介质转运；不要在内网运行联网安装命令。确认待恢复路径不存在且磁盘空间充足，使用有 Docker 管理权限的管理员执行：

```bash
set -euo pipefail
umask 077
cd /实际/离线介质目录
sha256sum -c cloud-history-20261116.tar.gz.gpg.sha256
printf '%s  %s\n' 7d6f8b1ba4344a58e1890622fb45bc36f0778c65851be93e7d50ef2cad05b544 cloud-history-20261116.tar.gz.gpg | sha256sum -c -
HISTORY=/opt/llm-cloud-history-20261116
WORK=/opt/llm-history-gateway-20261116
KEY=/实际/受控密钥目录/history-decryption.key
test -r "$KEY"
test ! -e "$HISTORY"
test ! -e "$WORK"
mkdir -m 700 "$HISTORY" "$WORK"
GPG_PIN=()
if gpg --dump-options | grep -qx -- '--pinentry-mode'; then GPG_PIN=(--pinentry-mode loopback); fi
gpg --batch "${GPG_PIN[@]}" --passphrase-file "$KEY" --decrypt cloud-history-20261116.tar.gz.gpg | tar -xz -C "$HISTORY"
tar -xf "$HISTORY/cloud-private-config.tar" -C "$WORK"
chmod 700 "$HISTORY" "$WORK"
chmod 600 "$WORK/.env"
ls -lh "$HISTORY"
```

解密后的目录含生产凭据、证书私钥和历史请求，需保持受控权限。请先检查目标机磁盘、备份与已有服务；此流程不得指向已有数据目录，也不要用 `docker compose down -v` 删除任何卷。

## 三、调整目标机配置

`$WORK` 中是云端原始配置，不可直接认为适合目标内网。检查并修改：

1. `.env` 中 `NEXTAUTH_URL`、`LANGFUSE_MEDIA_PUBLIC_ENDPOINT`、`HOST_IP`、可信代理 CIDR、Strata 认证及域名按目标机实际情况调整。保留原来的 PostgreSQL / ClickHouse / Redis / MinIO 账号密码，以及 `SALT`、`ENCRYPTION_KEY`、`NEXTAUTH_SECRET`、原 Langfuse 项目公私钥；否则已有数据或密钥可能无法使用。
2. `litellm/config.yaml` 的 Strata `api_base` 改为 LiteLLM 容器可访问的目标地址，例如 `http://目标IP:8080/v1`，模型 ID 改为目标 `/v1/models` 实际值。原 `strata` Compose 服务属于云端，目标机复用已有 Strata，不要照搬启动。
3. `docker-compose.yml` 的端口绑定按现场修改；快照原始值中网关为宿主 8800 HTTP、Langfuse 为宿主 8801 HTTPS，MinIO 为宿主回环 9090。云端自签证书及其域名不一定适合目标机，按现场替换 `nginx/certs/`；同时核对 `nginx/nginx.conf`、反向代理信任链及访问控制。
4. `.env` 已含生产密钥，不应打印、提交或放在普通共享目录。为避免 Compose 优先使用宿主同名环境变量，以下示例使用 `env -i` 和本机默认 Docker socket。非默认 socket / rootless 环境需由现场管理员适配。

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

## 四、恢复数据并启动

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

## 五、验收和限制

1. 确认 Redis 重启后键数正常、MinIO 可列出预期对象、PostgreSQL 和 ClickHouse 使用原项目数据，且其他项目未被重建。
2. 使用云端原项目账户登录目标 Langfuse，按时间及已知 Trace ID 检查历史数据和对象附件；源 IP 用量账本还应核对文件属主及指标读取权限。
3. 按 [操作指南](kylin-v10-offline-operations-step-by-step.md) 第 8 节经目标 Nginx 发起新请求，第 8.1 节验收 Agent / Generation / Tool；检查新老 Trace、usage、输入输出与时间。只验证 PostgreSQL / ClickHouse 恢复不等于全链路已验收。
4. 检查 Redis 延迟 / 失败队列，历史任务可能被重新执行；不能只根据键数断言任务已经完成。必要时由业务方指定截止时间再做一致性比对。

当前数据库、Redis 和 MinIO 对象列表已在隔离容器中做过恢复试验；整套云端配置在麒麟 V10 目标机的恢复流程仍需实测，MinIO 对象应在现场抽样读取；Docker、GnuPG 等麒麟依赖应与目标 SP / Build 匹配。
