# 云端 Strata 配置修复和锁页验证

记录时间：2026-11-16，UTC+8。目标为已有云 GPU 环境的 `llm-a100` Compose 项目；目标麒麟 V10 环境未执行本次变更。本记录不包含密钥。

## 变更和备份

2026-11-16 18:24:47 左右，以只修改已有 JSON 对应字段的方式应用四槽修正版：顶层 `parallel` 改为 4，保留 `--batch 4` 和 262144 上下文；删除 batch 下关闭的 `--pipeline-windows 2`、双 GPU 下无效的 `--prompt-cache-tail`，以及关闭驻留会话缓存时不起作用的三个调节参数；删除当前未启用 resident experts 时使用不到的 `STRATA_RESIDENT_HEADROOM_GIB`。未启用 `--batch-mtp`。其他未审计的 JSON 字段及现有密钥保留。

在 `docker-compose.cloud.yml` 的 `strata` 服务设置 `ulimits.memlock.soft: -1`、`hard: -1`。Compose 校验通过后，仅重建 Strata：`up -d --no-build --pull never --no-deps --force-recreate strata`。宿主的数据和模型 bind mount 未更改。

原始配置、两个 Compose 文件和原引擎日志保存在权限受限的目录：

```text
/opt/llm-observability-deploy/backups/strata-fix-20261116T102447Z/
├── docker-compose.yml
├── docker-compose.cloud.yml
├── strata-iq3_xxs.json
└── strata-iq3_xxs.log
```

配置示例见 [四槽 JSON 模板](../examples/strata-iq3_xxs-cloud-4slot.example.json)。其中 `api_key` 为占位符；实际部署是修改云端已有配置，并未用占位符覆盖密钥。

## 启动结果

- 容器启动时间为 2026-11-16 18:24:58（UTC+8），重启次数 0。Docker 和引擎进程均显示 memlock 软硬上限为 `unlimited`。
- 新引擎日志记录 `PLE table locked in RAM`、MTP draft layer 已加载以及 session up。模型完成加载后 `/health` 报告 `loaded=true`、`max_context=262144`；运行指标报告 `parallel=4`、4 个槽、`kv=k8v4`、`kv_resident=32768`。
- 引擎进程 `VmLck=28,125,136 KiB`，约 26.82 GiB，证实本次 PLE 锁页有效。启动时间和资源值均为本次快照。
- **内存余量很低**：锁页后宿主 `MemAvailable` 约 1.10 GiB，已用 swap 约 1.25 GiB；此前分别约 25.97 GiB 和 0.08 GiB。引擎 `VmSwap=0`，不能将其他进程的 swap 全归因于引擎。所检查 cgroup 的 `under_oom=0`、`memory.failcnt=0` 是采样时状态，不代表后续不会发生 OOM。容器内存占用由 `docker stats` 报告约 71.18 GiB。当前设置不应视为已通过持续负载或长期稳定性验收，应优先扩充内存或评估降低驻留资源、恢复未锁页配置。

## 推理和 Langfuse 验证

2026-11-16 18:28（UTC+8），由云主机以 HTTP 回环请求 Nginx 8800，经过 LiteLLM `qwen-strata` 别名到 Strata：

| 请求 | 结果 | API usage | Langfuse Trace ID |
|---|---|---|---|
| 非流式 | HTTP 200，回答 `42`，finish reason `stop` | 输入 26、输出 37、总计 63 | `110d2d01d769ca055f2beb2829796ca0` |
| 流式 | HTTP 200，回答 `42`，收到 `[DONE]` | 输入 26、输出 37、总计 63 | `777183da4e4351034627fcf20fb1c7da` |

两条 trace 均在 Langfuse 的 ClickHouse `events_full` 查到 `GENERATION`，模型为 `qwen3.8-flash-next-iq3_xxs`，用量总数均为 63。流式事件中的输入 5、缓存输入 21、可见输出 11、推理输出 26 为拆分项。此次通过存储查询验证链路，未在 UI 复核两条记录；也未进行长上下文、持续并发或性能对照测试。

## 现场检查及回滚

```bash
cd /opt/llm-observability-deploy/src/monitoring
docker inspect llm-a100-strata-1 --format '{{.State.Status}} {{json .HostConfig.Ulimits}}'
docker exec llm-a100-strata-1 curl -fsS http://127.0.0.1:8080/health
docker exec llm-a100-strata-1 sh -c "grep -E 'PLE table (locked in RAM|loaded \(not locked\))' /opt/strata/strata-iq3_xxs.log"
free -h
```

如因内存压力需要回退本次配置，在确认之后没有需要保留的配置变更且业务允许中断后，恢复 JSON 和 Compose，再只重建 Strata（会重新加载模型）：

```bash
cd /opt/llm-observability-deploy/src/monitoring
BACKUP=/opt/llm-observability-deploy/backups/strata-fix-20261116T102447Z
cp -p "$BACKUP/strata-iq3_xxs.json" /opt/llm-observability-deploy/strata-data/config/strata-iq3_xxs.json
cp -p "$BACKUP/docker-compose.cloud.yml" docker-compose.cloud.yml
COMPOSE=/opt/llm-observability-deploy/downloads/docker-compose-linux-x86_64
"$COMPOSE" -p llm-a100 -f docker-compose.yml -f docker-compose.cloud.yml config --quiet
"$COMPOSE" -p llm-a100 -f docker-compose.yml -f docker-compose.cloud.yml up -d --no-build --pull never --no-deps --force-recreate strata
```

回滚后仍需等待 `/health` 返回 `loaded=true`，再执行真实推理和 Langfuse 验证。恢复旧 JSON 也会恢复原先不一致但部分未生效的参数；如只想取消锁页，可在备份当前 Compose 后仅移除 `ulimits.memlock`，重建并验证。
