# Qwen3.6-27B vLLM 启动参数

记录日期：2026-11-18。本文记录双 A800 40GB、vLLM `v0.24.0-cu129` 和 Qwen3.6-27B 的启动参数。现场 8192 token 配置已完成短推理；262144 token 配置是候选，尚未应用或实测。记录时实例已关机或到期，以下命令需在服务器恢复可用且环境准备完成后执行。

模型位于宿主机 `/data/qwen-vllm/models/Qwen3.6-27B`，以只读方式挂载到容器 `/models/Qwen3.6-27B`。本文的 `vllm serve` 命令使用的是**容器内路径**，必须在已配置 NVIDIA GPU、镜像及模型挂载的 vLLM 容器环境内运行，不能直接在 CentOS 宿主机照搬。

## 验证状态

- 8192：现场 `/health` 返回 200；直连 `qwen3.6-27b` 短推理成功，经 LiteLLM 的 `qwen-vllm` 短推理返回 200。
- 262144：参数保存在本文和公开模板中，未在目标机器上启动，也未完成长上下文推理验收。
- 上下文长度是单个请求的输入与输出 token 之和。配置最大长度或健康检查成功，不能证明该长度的推理已成功。
- 当前实例不可用，本文不表示服务当前正在运行。

## 主要参数

| 参数 | 8192 基础配置 | 262144 候选配置 | 说明 |
| --- | --- | --- | --- |
| 镜像 | `vllm/vllm-openai:v0.24.0-cu129` | 同左 | 需事先导入镜像 |
| 模型路径 | `/models/Qwen3.6-27B` | 同左 | 容器内只读挂载路径 |
| 对外模型名 | `qwen3.6-27b` | 同左 | 请求中的上游模型名 |
| `--tensor-parallel-size` | `2` | `2` | 使用两张 GPU 张量并行 |
| `--dtype` | `bfloat16` | `bfloat16` | BF16 权重和计算 |
| `--max-model-len` | `8192` | `262144` | 输入加输出的最大 token 数 |
| `--gpu-memory-utilization` | `0.90` | `0.90` | vLLM 的 GPU 显存预算比例 |
| `--max-num-seqs` | 使用默认值 | `1` | 限制每轮处理的序列数 |
| `--max-num-batched-tokens` | 使用默认值 | `4096` | 每轮调度的 token 上限 |
| `--enable-chunked-prefill` | 使用默认值 | 启用 | 将长输入拆分为多轮处理 |
| `--enforce-eager` | 使用默认值 | 启用 | 候选禁用 CUDA graph 执行 |
| `--language-model-only` | 启用 | 启用 | 文本模型服务 |
| `--reasoning-parser` | `qwen3` | `qwen3` | Qwen 推理内容解析 |
| Compose `restart` | `no` | `no` | 容器退出后不自动重启 |

`0.90`、单序列和分块 prefill 不是保证 262144 能放入显存的证明。模型、运行时、混合注意力缓存和请求执行都会占用显存，需根据启动日志和实际请求验证。

## 容器内执行：8192 参数

以下命令展示容器内完整启动参数。在已准备好 GPU 和模型挂载、尚未运行服务的容器交互式 shell 中执行，通过静默输入设置密钥。命令与现场原部署一样使用 `--api-key` 参数，从环境变量取值；文档和命令历史中不包含实际密钥。独立部署模板使用 vLLM 0.24 支持的 `VLLM_API_KEY` 环境变量，详见 [部署说明](README.md)。

```bash
read -r -s -p 'vLLM API Key: ' VLLM_API_KEY
printf '\n'
export VLLM_API_KEY HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1

vllm serve /models/Qwen3.6-27B \
  --served-model-name qwen3.6-27b \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key "${VLLM_API_KEY:?请先设置VLLM_API_KEY}" \
  --tensor-parallel-size 2 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --language-model-only \
  --reasoning-parser qwen3

unset VLLM_API_KEY
```

这里的 `0.0.0.0` 是容器内监听地址；宿主机是否可访问取决于容器端口映射。默认公开模板将宿主机端口绑定到 `127.0.0.1`，跨容器的 LiteLLM 接入需使用受限网络内可达的宿主机地址。vLLM API Key 不保护所有路径，应限制网络访问。

## 容器内执行：262144 候选参数

下面是待验收配置，不应与已加载的 GPU 服务同时启动。容器需要和前一节相同的镜像、两张 GPU 和模型挂载。

```bash
read -r -s -p 'vLLM API Key: ' VLLM_API_KEY
printf '\n'
export VLLM_API_KEY HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1

vllm serve /models/Qwen3.6-27B \
  --served-model-name qwen3.6-27b \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key "${VLLM_API_KEY:?请先设置VLLM_API_KEY}" \
  --tensor-parallel-size 2 \
  --dtype bfloat16 \
  --max-model-len 262144 \
  --gpu-memory-utilization 0.90 \
  --language-model-only \
  --reasoning-parser qwen3 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 4096 \
  --enable-chunked-prefill \
  --enforce-eager

unset VLLM_API_KEY
```

启动后应先检查显存、服务日志、`/health` 和短推理，再执行接近上限的实际请求。[长上下文探测脚本](context_probe.py)和[部署说明](README.md)提供探测方法。目标输入为 262080、输出最多 64 token；须以实际返回的输入和输出 token 数为准，不应把输出预算当作已生成的 token。

## 恢复现场已有的 8192 Compose

服务器恢复后，如 `/data/qwen-vllm` 中的现场文件仍在、镜像已导入且两张 GPU 可用，可使用原文件恢复 **8192** 配置。该命令不应用本文 262144 候选参数。

```bash
cd /data/qwen-vllm || exit 1
/usr/bin/docker-compose -p qwen-vllm \
  --env-file vllm-bf16.env \
  -f docker-compose.bf16-candidate.yml \
  up -d --pull never --no-build vllm
```

查看状态和日志：

```bash
cd /data/qwen-vllm || exit 1
/usr/bin/docker-compose -p qwen-vllm \
  --env-file vllm-bf16.env \
  -f docker-compose.bf16-candidate.yml ps
/usr/bin/docker-compose -p qwen-vllm \
  --env-file vllm-bf16.env \
  -f docker-compose.bf16-candidate.yml logs --tail=100 -f vllm
nvidia-smi
```

## 使用公开模板测试 262144 候选

这些公开模板尚未由本次操作部署到现场。应先将本目录中的 `docker-compose.bf16.yml`、`docker-compose.context-262k.yml`、`vllm.env.example`、`README.md` 和 `context_probe.py` 复制到 `/data/qwen-vllm/deploy`，并按[部署说明](README.md)创建私有 `.env`、设置 Key 和可达地址。使用相同项目名会重建该项目容器；执行前确认允许中断现有服务。

```bash
cd /data/qwen-vllm/deploy || exit 1
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml \
  -f docker-compose.context-262k.yml \
  config --quiet
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml \
  -f docker-compose.context-262k.yml \
  up -d --pull never --no-build --force-recreate vllm
```

上面必须按顺序使用两个 `-f`：第二个文件覆盖基础配置的启动参数。`config --quiet` 只检查配置；启动完成、实际推理及近上限请求均需分别验证。如果启动失败，保存日志后按现场实际情况回退到上一节的 8192 配置。
