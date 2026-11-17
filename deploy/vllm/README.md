# Qwen3.6-27B：vLLM 双 GPU 部署模板

本目录是公开、脱敏的模板。模型使用本地 BF16 权重，vLLM 通过 OpenAI 兼容接口提供文本服务。基础配置为 8192 token；`docker-compose.context-262k.yml` 是 262144 token 的候选覆盖文件，**尚未在目标服务器上验收**。

## 验证记录与边界

2026-11-17 在 CentOS 7、两张 A800 40GB、驱动 550.78 上，vLLM `v0.24.0-cu129` 的 8192 配置完成加载，`/health` 返回 200。直连 `qwen3.6-27b` 短推理成功，耗时约 4.68 秒，输入 20、输出 5 token；经 LiteLLM 的 `qwen-vllm` 短推理返回 200，耗时约 2.22 秒，输入 24、输出 9 token。模型权重加载约 25.24 GiB/卡。现场使用 `--api-key` 参数，本模板使用 `VLLM_API_KEY` 环境变量；vLLM 0.24 源码支持该环境变量，模板自身尚未在现场验收。这些结果不代表长上下文、公开 HTTPS 或 Langfuse trace 已通过。

模型配置包含 16 层全注意力、48 层线性注意力，最大位置数 262144。按 BF16、TP=2 估算，262144 token 的全注意力 KV 约 8 GiB/卡，实际还需线性注意力状态、运行时内存及缓存对齐空间。候选配置是否能加载、是否能完成真实的长输入与输出，必须在目标服务器上实测；上下文上限包含输入和输出。

## 1. 准备

需要 Docker、可用的 NVIDIA Container Toolkit、两张可用 GPU、`/usr/bin/docker-compose` 以及已在本地导入的 `vllm/vllm-openai:v0.24.0-cu129` 镜像。离线环境需提前准备完整模型和 tokenizer 文件。将本目录的 Compose 基础配置、262K 覆盖配置、环境示例、README 和 `context_probe.py` 共五个文件复制到服务器，例如 `/data/qwen-vllm/deploy`，然后执行：

```bash
cd /data/qwen-vllm/deploy || exit 1
docker image inspect vllm/vllm-openai:v0.24.0-cu129 >/dev/null
test -r /data/qwen-vllm/models/Qwen3.6-27B/config.json
nvidia-smi
```

若模型路径不同，修改私有环境文件中的 `VLLM_MODEL_DIR`。镜像已设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`，不会自动下载缺失文件。需要持久化 Hugging Face 缓存时，创建目录并取消基础 Compose 中缓存卷的注释。

## 2. 设置私有配置

```bash
cd /data/qwen-vllm/deploy || exit 1
umask 077
cp -n vllm.env.example .env
```

编辑 `.env`，设置独立随机的 `VLLM_API_KEY`，并确认模型目录、监听 IP 和端口。不要把实际密钥提交到仓库。默认 `127.0.0.1:8000` 仅供宿主机访问；独立 Docker 容器中的 LiteLLM 不能通过它访问宿主机。跨容器调用时，将 `VLLM_BIND_IP` 改为 LiteLLM 可路由到的宿主机专用地址，并在 LiteLLM 中使用该地址的 `http://地址:8000/v1`。API Key 不保护所有 vLLM 路径，应限制该监听地址的网络访问。

如需在服务器生成密钥，可执行以下命令（会覆盖私有文件里的现有密钥）：

```bash
VLLM_NEW_KEY=$(openssl rand -hex 32)
sed -i "s/^VLLM_API_KEY=.*/VLLM_API_KEY=${VLLM_NEW_KEY}/" .env
unset VLLM_NEW_KEY
chmod 600 .env
```

## 3. 启动 8192 配置

以下项目名为 `qwen-vllm`；相同项目名会复用和重建该项目的容器。确认 GPU 上的其他任务已经停止后执行：

```bash
cd /data/qwen-vllm/deploy || exit 1
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml config --quiet
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml up -d --pull never --no-build
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml logs -f --tail=100 vllm
```

`config --quiet` 只检查配置，不证明模型已经可用。日志中出现启动完成后，另开终端验证：

```bash
curl -fsS http://127.0.0.1:8000/health && echo ' health OK'
nvidia-smi
```

如果配置的绑定地址或端口不是默认值，把上述地址改为服务器本机能够访问的绑定地址和端口。`/health` 不等于推理成功；在服务器上继续执行以下鉴权和短推理检查：

```bash
cd /data/qwen-vllm/deploy || exit 1
set -a
. ./.env
set +a
python3 - <<'PY'
import json, os, urllib.request

base = 'http://%s:%s' % (os.environ['VLLM_BIND_IP'], os.environ['VLLM_PORT'])
headers = {'Authorization': 'Bearer ' + os.environ['VLLM_API_KEY']}
with urllib.request.urlopen(urllib.request.Request(base + '/v1/models', headers=headers), timeout=30) as response:
    models = json.load(response)
print('models:', [model['id'] for model in models['data']])
payload = json.dumps({'model': 'qwen3.6-27b', 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 32, 'chat_template_kwargs': {'enable_thinking': False}}).encode()
headers['Content-Type'] = 'application/json'
request = urllib.request.Request(base + '/v1/chat/completions', data=payload, headers=headers)
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
content = result['choices'][0]['message'].get('content') or ''
if not content.strip():
    raise SystemExit('Short inference returned empty content')
print('content:', content, 'finish_reason:', result['choices'][0]['finish_reason'], 'usage:', result.get('usage'))
PY
unset VLLM_API_KEY
```

LiteLLM 使用相同上游 Key 和模型名 `openai/qwen3.6-27b`，还需通过 LiteLLM 发起一次独立的推理请求验证链路。

## 4. 测试 262144 候选

仅在确认当前工作负载允许重建 vLLM 时执行。覆盖文件会替换完整 `command`，保留基础文件的镜像、GPU、卷、网络和密钥配置；必须依次传入两个文件：

```bash
cd /data/qwen-vllm/deploy || exit 1
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml -f docker-compose.context-262k.yml config --quiet
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml -f docker-compose.context-262k.yml up -d --pull never --no-build --force-recreate
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml -f docker-compose.context-262k.yml logs -f --tail=100 vllm
```

候选参数为 BF16、TP=2、`gpu-memory-utilization=0.90`、`max-num-seqs=1`、每轮最多 4096 token、分块 prefill 和 eager 模式。启动成功后先测短推理，再使用以下命令通过 LiteLLM 测试接近上限的请求。请交互输入服务器本机可达的 vLLM 地址和 LiteLLM 网关地址；协议和端口必须与现场实际配置一致，例如确认本地网关使用 HTTP 之后才输入 `http://127.0.0.1:8800/v1`。网关 Key 使用交互输入，不显示在终端：

```bash
cd /data/qwen-vllm/deploy || exit 1
set -a
. ./.env
set +a
read -r -p 'vLLM 地址（例如 http://127.0.0.1:8000）: ' VLLM_PROBE_URL
read -r -p 'LiteLLM 网关地址（以 /v1 结尾）: ' LITELLM_PROBE_URL
read -r -s -p 'LiteLLM API Key: ' LITELLM_API_KEY
printf '\n'
export VLLM_API_KEY LITELLM_API_KEY
python3 context_probe.py \
  --tokenizer-url "$VLLM_PROBE_URL" \
  --gateway-url "$LITELLM_PROBE_URL" \
  --target-input-tokens 262080 \
  --max-output-tokens 64 \
  --output context-probe-result.json
unset VLLM_API_KEY LITELLM_API_KEY VLLM_PROBE_URL LITELLM_PROBE_URL
```

脚本目标输入约 262080 token，输出最多 64 token；实际输出可能少于 64。应根据结果中的真实 token 使用量判断实际达到的长度，只能将通过的请求称为近上限测试。容量、检索结果、耗时、显存与日志应分别检查；单次成功不代表精度和并发性能已经验证。

回退 8192 配置：

```bash
cd /data/qwen-vllm/deploy || exit 1
/usr/bin/docker-compose -p qwen-vllm --env-file .env \
  -f docker-compose.bf16.yml up -d --pull never --no-build --force-recreate
```

## 5. 排查

- 配置检查失败：检查 `.env` 是否填写非空 Key，两个 `-f` 的顺序以及模型路径。
- GPU 无法识别：在宿主机先运行 `nvidia-smi`，再检查 NVIDIA Container Toolkit 和 Docker 的 GPU 设备配置。
- 离线加载报缺失文件：核对完整模型、tokenizer 和镜像；该配置不会联网补齐。
- 显存不足或服务退出：查看 `docker-compose ... logs` 和 `nvidia-smi`；先回退 8192。262144 只是候选，降低每轮 token 数并不能消除完整上下文 KV 的需求。
- LiteLLM 返回连接失败：确认绑定地址是 LiteLLM 容器可达的宿主机地址，而非容器自己的 `127.0.0.1`；检查端口、上游 Key 和模型名。
