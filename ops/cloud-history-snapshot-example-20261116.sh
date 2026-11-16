#!/usr/bin/env bash
# Snapshot procedure adapted from the historical cloud backup; review host paths,
# services, keys and impact before reuse.
set -Eeuo pipefail
umask 077
BASE=/opt/llm-observability-deploy/src/monitoring
ROOT=/opt/llm-observability-deploy/backups/cloud-history-20261116
KEY=/root/.llm-offline-history-20261116.key
CHPATH=/var/lib/clickhouse/backups/backups/codex-cloud-history-20261116.zip
test -r "$KEY"
test ! -e "$ROOT"
docker exec llm-a100-clickhouse-1 sh -c "test ! -e '$CHPATH'"
for c in nginx litellm langfuse-web langfuse-worker minio; do
  test "$(docker inspect -f '{{.State.Running}}' "llm-a100-$c-1")" = true
done
mkdir -p "$ROOT/stage"
chmod 700 "$ROOT" "$ROOT/stage"
STARTED=0
restore_services() {
  local failed=0
  if [ "$STARTED" = 1 ]; then
    echo 'RESTORING_SERVICES'
    for c in minio langfuse-web langfuse-worker litellm nginx; do
      docker start "llm-a100-$c-1" >/dev/null || failed=1
    done
    if [ "$failed" -eq 0 ]; then STARTED=0; fi
  fi
  if [ "$failed" -ne 0 ]; then echo 'SERVICE_RESTORE_FAILED'; return 1; fi
}
on_exit() {
  rc=$?
  trap - EXIT
  restore_services || rc=1
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP
queues_empty() {
  local count
  count=$(docker exec llm-a100-redis-1 sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli --raw EVAL "local c=\"0\"; local n=0; repeat local r=redis.call(\"SCAN\",c,\"MATCH\",\"bull:*\",\"COUNT\",1000); c=r[1]; for _,k in ipairs(r[2]) do if string.match(k,\":wait$\") or string.match(k,\":active$\") then if redis.call(\"TYPE\",k).ok==\"list\" then n=n+redis.call(\"LLEN\",k) end end end until c==\"0\"; return n" 0')
  [ "$count" = 0 ]
}
wait_queue() {
  local tries
  for tries in $(seq 1 30); do
    if queues_empty; then sleep 3; queues_empty && return 0; fi
    sleep 3
  done
  echo 'QUEUE_DRAIN_TIMEOUT'
  return 1
}
printf 'encryption-self-test' | gpg --batch --yes --passphrase-file "$KEY" --symmetric --cipher-algo AES256 --s2k-digest-algo SHA256 --s2k-mode 3 --s2k-count 65011712 --compress-algo none 2>/dev/null | gpg --batch --yes --passphrase-file "$KEY" --decrypt 2>/dev/null | grep -qx 'encryption-self-test'
echo 'ENCRYPTION_PREFLIGHT_OK'
if queues_empty; then echo 'QUEUE_PREFLIGHT_EMPTY'; else echo 'QUEUE_PREFLIGHT_PENDING'; fi
echo 'BEGIN_WRITE_PAUSE'
date -u +%FT%TZ > "$ROOT/stage/snapshot-start-utc.txt"
STARTED=1
docker stop -t 90 llm-a100-nginx-1 >/dev/null
docker stop -t 90 llm-a100-litellm-1 >/dev/null
wait_queue
docker stop -t 45 llm-a100-langfuse-web-1 >/dev/null
wait_queue
docker stop -t 45 llm-a100-langfuse-worker-1 >/dev/null
docker stop -t 45 llm-a100-minio-1 >/dev/null
echo 'CREATING_SNAPSHOTS'
docker exec llm-a100-postgres-1 sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d langfuse --format=custom' > "$ROOT/stage/postgres-langfuse.dump"
docker exec llm-a100-postgres-1 sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$ROOT/stage/postgres-globals.sql"
docker exec llm-a100-clickhouse-1 sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "BACKUP DATABASE default TO File('\''backups/codex-cloud-history-20261116.zip'\'')"'
docker cp "llm-a100-clickhouse-1:$CHPATH" "$ROOT/stage/clickhouse-default.zip"
docker exec llm-a100-redis-1 sh -c 'REDISCLI_AUTH="$REDIS_AUTH" redis-cli --rdb /tmp/codex-offline-history-20261116.rdb' >/dev/null
docker cp llm-a100-redis-1:/tmp/codex-offline-history-20261116.rdb "$ROOT/stage/redis.rdb"
tar -C "$BASE/data/minio" -cf "$ROOT/stage/minio-single-node-data.tar" .
tar -C "$BASE" -cf "$ROOT/stage/cloud-private-config.tar" .env docker-compose.yml docker-compose.cloud.yml litellm nginx
python3 - "$BASE/data/source-usage/usage.sqlite3" "$ROOT/stage/source-usage.sqlite3" <<'PY'
import sqlite3,sys
src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
dst=sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
dst.close(); src.close()
PY
date -u +%FT%TZ > "$ROOT/stage/snapshot-end-utc.txt"
restore_services
echo 'SERVICES_RESTARTED'
tar -C "$ROOT/stage" -czf "$ROOT/cloud-history-20261116.tar.gz" .
gpg --batch --yes --passphrase-file "$KEY" --symmetric --cipher-algo AES256 --s2k-digest-algo SHA256 --s2k-mode 3 --s2k-count 65011712 --compress-algo none -o "$ROOT/cloud-history-20261116.tar.gz.gpg" "$ROOT/cloud-history-20261116.tar.gz"
(cd "$ROOT" && sha256sum cloud-history-20261116.tar.gz.gpg > cloud-history-20261116.tar.gz.gpg.sha256)
echo 'ENCRYPTED_HISTORY_READY'
ls -lh "$ROOT/cloud-history-20261116.tar.gz.gpg"
docker exec llm-a100-nginx-1 nginx -t
for c in nginx litellm langfuse-web langfuse-worker minio strata; do
 docker inspect "llm-a100-$c-1" --format '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
done
