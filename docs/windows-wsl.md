# Windows / WSL 复用部署与验收

验收日期：2026-11-09。源码位于 `E:\project\YOUR_PROJECT\litellm-langfuse-monitoring`，运行环境为 `Ubuntu-24.04`，用户为 `YOUR_USER`。以下路径对应本机；其他机器需要调整目录、容器名称和推理地址。

## 已复用的组件

| 组件 | 当前版本或服务 | WSL 内入口 |
| --- | --- | --- |
| LiteLLM | 1.106.6，用户服务 `litellm-gateway.service` | `127.0.0.1:4000` |
| Strata | 既有服务 `strata-iq3-xxs-full-context.service` | `127.0.0.1:8080` |
| Grafana | 13.3.0，`llm-observability-grafana` | `127.0.0.1:3002` |
| Prometheus | `llm-observability-prometheus` | `127.0.0.1:9090` |
| Langfuse | 4.73.0，既有 Web / Worker | `127.0.0.1:3001` |
| Langfuse SDK | 4.18.0 | 共享 Python 虚拟环境 |
| Strata 补充导出器 | 新增用户服务 `strata-metrics-extra.service` | Docker bridge 主机地址上的 `9402` |

PostgreSQL、ClickHouse、Redis、MinIO、Nginx 沿用既有容器和数据。Strata 使用 RTX 3080 20 GiB、`qwen3.8-flash-next-iq3_xxs`、262144 上下文；这次部署不重新加载模型。

```text
Windows 源码 /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring
                    │ 部署项目拥有的文件
WSL 运行目录 /home/YOUR_USER/litellm-langfuse-monitoring
  ├── .env                         既有密钥与 OTEL / 离线价格表开关
  ├── litellm/config.local.yaml     保留既有路由，只调整观测回调
  ├── litellm/observability_callback.py
  ├── exporters/{strata_extra.py,run-local.sh}
  └── backups/observability-*/manifest.json

共享依赖 /home/YOUR_USER/llm-observability/.venv
Prometheus /home/YOUR_USER/llm-observability/prometheus.yml
规则 /home/YOUR_USER/llm-observability/prometheus-rules
Grafana /home/YOUR_USER/llm-observability/grafana/provisioning
```

## 重新部署

确认 WSL 和现有服务。`--dry-run` 只发现容器挂载、Grafana provider、bridge 地址和运行目录：

```powershell
wsl --list --verbose
wsl -d Ubuntu-24.04 -- systemctl --user status litellm-gateway.service strata-metrics-extra.service --no-pager
wsl -d Ubuntu-24.04 -- docker ps --format "{{.Names}} {{.Status}}"
wsl -d Ubuntu-24.04 -- env LITELLM_LOCAL_MODEL_COST_MAP=True /home/YOUR_USER/llm-observability/.venv/bin/python /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/deploy_wsl.py --dry-run
```

本机依赖已存在，版本清单在 `requirements-observability.txt`。其他环境准备依赖时再执行 `python -m pip install -r requirements-observability.txt`，避免无目的升级共享虚拟环境。

应用更新：

```powershell
wsl -d Ubuntu-24.04 -- env LITELLM_LOCAL_MODEL_COST_MAP=True /home/YOUR_USER/llm-observability/.venv/bin/python /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/deploy_wsl.py
```

脚本依次备份被覆盖的文件、验证 LiteLLM 自定义文件加载、保留其他回调和模型路由、关闭全局 OTEL V2 并启用内置价格表、合并 Strata 抓取与规则、更新看板。先执行 `promtool check config`，再启动补充导出器、重启网关并检查存活，最后对 Prometheus 发送 HUP。Grafana provider 自动读取更新。

Prometheus 主配置是单文件 bind mount，脚本原位写入以保留 inode。Strata 认证文件由宿主用户持有、组为 Prometheus 的 65534，权限 0640；使用缓存的现有 Prometheus 镜像执行一次无网络权限修正。运行需要既有 Docker 访问权限。

可通过 `--runtime-root`、`--prometheus`、`--grafana` 调整目录和容器名。端口 8080 / 9402、共享 venv、systemd 单元名和 deployment 标签按本机布局设置，跨机器迁移时应检查脚本。

## 客户端接入

OpenAI 兼容入口是 `http://localhost:4000/v1`，模型为 `qwen-strata`，认证沿用已有 `LITELLM_MASTER_KEY`。运行配置还保留了原有长模型别名。

流式请求建议带上 usage 和业务 Session：

```json
{
  "model": "qwen-strata",
  "stream": true,
  "stream_options": {"include_usage": true},
  "messages": [{"role": "user", "content": "你好"}],
  "metadata": {
    "session_id": "业务会话标识",
    "trace_user_id": "业务用户标识",
    "trace_name": "chat"
  }
}
```

也可通过 `x-session-id` / `x-litellm-session-id`、`langfuse_trace_user_id`、`langfuse_trace_name` 传递信息。OpenAI 请求的 `user` 可作为用户标识。回调只选取需要的 metadata，不把内部凭证或完整请求头写入 Span。

客户端执行工具时，应用需要创建 AGENT / TOOL 观测，并把当前父节点传播给网关：

```text
traceparent: 00-<32位 trace_id>-<16位父 span_id>-01
```

`examples/traced_tool_agent.py` 提供完整实现。它使用与网关相同的 Langfuse 项目，实际执行 `17 + 25`，模型的两次生成都归属于 Agent；工具节点记录参数、结果和耗时。`metadata.existing_trace_id` / `trace_id` 和 `parent_observation_id` 也可用于延续，但优先使用 W3C 头，并提供真实存在的父节点。

## 验收与启动

纯读取检查不会触发模型生成：

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/check.sh
```

完整验证执行一次小型真实流式请求，并可核对已存在的工具链：

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/ops/check.sh --exercise --trace-id a9880c8931c965de38f2bdcebca77c78
```

脚本对 78 条面板查询执行真实 PromQL 检查，要求关键硬件与缓存有数值、必需目标健康。`--exercise` 核对 Langfuse 输入输出、usage 和 TTFT / TPOT；`--stream-trace-id` 复查既有流式记录。完整 JSON 在工作区 `.runtime/validation.json`，失败返回非零。

回归测试：

```powershell
wsl -d Ubuntu-24.04 -- env LITELLM_LOCAL_MODEL_COST_MAP=True /home/YOUR_USER/llm-observability/.venv/bin/python -m unittest discover -s /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/tests -v
```

手动启动：

```powershell
wsl -d Ubuntu-24.04 -- systemctl --user start litellm-gateway.service strata-metrics-extra.service
```

新导出器已通过 `systemctl --user enable` 注册。Windows 重启后仍需要启动 WSL；本次不另建 Windows 计划任务。服务问题可在 WSL 中查看 `journalctl --user -u litellm-gateway.service -u strata-metrics-extra.service --since -10min`。日志可能包含业务消息，按需读取。

原有 `~/.docker/cli-plugins/docker-compose` 是不完整下载，执行时段错误。本次备份旧文件并从 [Docker Compose 官方 v5.7.0 release](https://github.com/docker/compose/releases/tag/v5.7.0) 重新下载，核对官方 API 提供的大小和 SHA-256 后修复；现在 `docker compose version` 可用。复用部署本身不依赖 Compose 启停，也没有重新创建任何既有容器。

模板检查不加载运行密钥，使用示例环境变量并跳过服务 `.env` 展开：

```powershell
wsl -d Ubuntu-24.04 -- docker compose --project-directory /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring --env-file /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/.env.example -f /mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/docker-compose.yml config --no-env-resolution -q
```

源码中的 `.gitattributes` 固定 Shell / Python 文件使用 LF，避免 Windows Git 的 CRLF 转换导致 WSL 脚本语法错误。

## 备份与回滚

部署输出的 `backups/observability-时间/manifest.json` 是回滚入口。以下在 **WSL 终端** 执行，替换为需要恢复的那次清单；按清单原位写入，保留 Docker 单文件挂载：

```bash
/home/YOUR_USER/llm-observability/.venv/bin/python - /home/YOUR_USER/litellm-langfuse-monitoring/backups/observability-时间/manifest.json <<'PY'
import json
from pathlib import Path
import sys
for entry in json.loads(Path(sys.argv[1]).read_text()):
    Path(entry["original"]).write_bytes(Path(entry["backup"]).read_bytes())
PY
docker exec llm-observability-prometheus promtool check config /etc/prometheus/prometheus.yml
systemctl --user daemon-reload
systemctl --user restart litellm-gateway.service strata-metrics-extra.service
docker kill --signal=HUP llm-observability-prometheus
```

清单只记录覆盖前已存在的文件。恢复首次部署之前的状态时，额外执行 `systemctl --user disable --now strata-metrics-extra.service` 停止新增导出器。未覆盖的新文件可以留存，不会被恢复后的旧配置调用。

自动健康检查失败时，脚本会恢复旧 LiteLLM YAML 并重启网关；这只是配置恢复，不是全量事务回滚。恢复回调代码、`.env`、规则和看板需使用完整备份。备份含运行密钥，应留在 WSL 私有目录。
