# 云端 Strata 页面代理与 Windows CPA 接入记录

记录日期：2026-11-16（UTC+8）。用户已选择将云端公网 `13258` 由 HTTPS 切为 HTTP，并允许原 HTTPS 中断。部署方报告：正式 CPA 已直连该 HTTP 入口，流式和非流式推理成功，Langfuse 的输入、输出和 usage 与请求匹配。**HTTP 明文传输独立 CPA token 和请求内容，不提供传输加密。** 本页记录部署方提供的结果，未独立连接云机复验。

## 1. 接入路径与边界

```text
Windows 客户端
  → Windows CLIProxyAPI（CPA），模型别名 cloud-qwen-strata
  → http://203.0.113.30:13258/v1 云端 Nginx（HTTP 明文）
  → LiteLLM（qwen-strata）→ Strata 0.1.45 / 双 A100
                                  └─ LiteLLM 回调 → Langfuse

浏览器 → https://203.0.113.30:13261
       → 云主机 8803 → 独立 Nginx → Strata 页面及 API
```

页面代理和推理网关是不同入口：`13261` 到 Strata，`13258` 到 LiteLLM。经过页面代理直接调用 Strata 并不能证明请求经过 LiteLLM 或产生 Langfuse 记录。本轮配置与执行细节由部署方提供；本文编写时没有连接云机，也没有验证正在运行的进程。

## 2. Strata 页面代理

仓库新增配置为 [strata-proxy.conf](../deploy/cloud-monitoring/strata-proxy.conf) 和 [docker-compose.yml](../deploy/cloud-monitoring/docker-compose.yml)。本地静态核对结果：

| 项目 | 配置或已报告信息 |
| --- | --- |
| Compose 服务 | `strata-proxy`，独立 Nginx 容器，加入 `backend` 网络 |
| 监听及映射 | 容器 HTTPS `8803`，默认宿主机端口 `8803`；部署方报告公网端口 `13261` 转发至云主机 `8803` |
| 上游 | `http://strata:8080`；实际部署需确认服务名解析和网络连通 |
| 证书 | Compose 挂载既有证书目录，只读；代理启用 TLS 1.2/1.3 |
| 页面及流式支持 | 转发 Host、来源地址、协议和 Upgrade；HTTP/1.1、关闭代理缓冲、读取超时 600 秒 |

部署方提供的云端实际操作如下。部署前的 Compose 文件备份为同目录 `docker-compose.yml.before-strata-proxy-20261116`；下面的 `up` 会创建或更新服务，只应在需要部署时执行。命令沿用现场目录中的 Compose 环境，不打印展开后的配置或凭据。

```bash
cd /opt/llm-observability-deploy/monitoring-addon-20261116/deploy/cloud-monitoring
COMPOSE=/opt/llm-observability-deploy/downloads/docker-compose-linux-x86_64

"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml config --quiet
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml run --rm --no-deps --entrypoint nginx strata-proxy -t
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml up -d --no-build --pull never --no-deps strata-proxy
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml ps strata-proxy
```

部署方报告上述 Nginx 检查输出 `syntax is ok` 和 `test is successful`。从公网访问 `https://203.0.113.30:13261/` 时，首页和 `/web/app.js` 均返回 HTTP 200，`/health` 返回 `loaded=true`，未授权访问 `/metrics` 返回 HTTP 401。浏览器自动化因无法可靠确定当前 URL 而终止，**截图未完成**；浏览器交互和登录后功能仍待验收。

以下是供复查的公网 HTTP 状态和健康值命令，执行位置为能访问公网入口的终端。这里 `-k` 仅用于 Strata 页面入口的自签证书下检查 HTTP 行为，不验证证书身份：

```bash
curl -skS -o /dev/null -w '首页 HTTP %{http_code}\n' https://203.0.113.30:13261/
curl -skS -o /dev/null -w 'JS HTTP %{http_code}\n' https://203.0.113.30:13261/web/app.js
curl -skS https://203.0.113.30:13261/health
curl -skS -o /dev/null -w '未授权 metrics HTTP %{http_code}\n' https://203.0.113.30:13261/metrics
```

## 3. Windows TLS 桥接与 CPA 配置（历史）

**历史记录：本节 HTTPS 桥接方案已停用。当前正式路径和验收结果见第 6 节；不要再使用本节证书检查或 18858 监听作为当前路径的验收方法。**

部署方当时提供的历史配置如下：

| 项目 | 信息及当前状态 |
| --- | --- |
| 桥接程序 | `E:\CLIProxyAPI_6.10.9_windows_amd64\cloud-nginx-tls-bridge.py`；本地已确认文件存在 |
| 本地监听 | 历史使用 `127.0.0.1:18858`；旧桥接进程已停止，当前 CPA 不依赖该端口 |
| 上游 | 云端 Nginx `203.0.113.30:13258` |
| 证书校验 | 自签证书 CN 为 `127.0.0.1`；桥接脚本内固定使用经 SSH 独立核对的云端证书 SHA-256 指纹，部署方报告已通过该 pin 成功转发真实请求 |
| CPA provider | `cloud-nginx-litellm`；配置变更由部署方报告，本文未读取含认证信息的 CPA 配置 |
| CPA 模型 | `cloud-qwen-strata`；热更新未生效后，部署方短暂重启 CPA 正式进程，报告新别名已在 `8317` 生效。云端 LiteLLM 模型别名为 `qwen-strata` |
| 配置备份 | `E:\CLIProxyAPI_6.10.9_windows_amd64\config.yaml.backup-20261116-145844-before-cloud-nginx`；本地已确认文件存在，未读取内容 |
| 认证 | LiteLLM 的 `/key/generate` 因数据库未连接返回 HTTP 500 `DB not connected`；当时 HTTPS 桥接方案复用既有 master key。当前 HTTP 方案已改为下文所述的路径受限独立凭据。本文不记录认证值 |

历史上证书 pin 与公开 IP 不匹配的 CN 问题由桥接处理；部署方报告旧桥曾通过固定指纹成功转发真实请求。当前 `8800` 和 `13258` 已切为 HTTP，此历史证书命令不能用于检查当前网关：

若未来恢复 HTTPS 桥接，应先确认监听协议，再经可信渠道安全核对证书和指纹；切换客户端与服务端后重新验收，不能直接沿用旧结果。

历史登录启动项已改名为以下禁用文件并保留：

```text
C:\Users\可乐\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\CPA-CloudGateway-Bridge.vbs.disabled
```

该历史启动项原调用 `D:\python312\pythonw.exe` 运行 `E:\CLIProxyAPI_6.10.9_windows_amd64\cloud-nginx-tls-bridge.py`。桥接进程已停；登录启动项不会作为当前直连方案的依赖，其历史注销登录自启也未验收。

Windows 本地只读检查示例：

```powershell
# 核对旧桥端口是否仍有监听；当前直连不依赖该端口。
Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 18858 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess

# 备份文件存在性；不读取配置内容。
Test-Path -LiteralPath 'E:\CLIProxyAPI_6.10.9_windows_amd64\config.yaml.backup-20261116-145844-before-cloud-nginx'
```

监听存在只证明本地端口被占用。历史 CPA 热更新未生效后，部署方曾短暂重启正式进程使旧别名生效；此历史记录不代表本次 HTTP 切换的执行步骤。

## 4. HTTPS 桥接时期的验证（历史）

[云端真实推理记录](cloud-real-inference-20261116.md)记载，在本轮 CPA 接入之前，云主机经 Nginx 进入 LiteLLM，非流式与流式真实请求均返回 `42` 和 usage，并在 Langfuse 查到对应 Generation；该记录也单独验证过一次 Agent/Tool 链。它证明的是**当时云主机回环路径**，不能代替 Windows CPA、TLS 桥接或公网来源路径的本轮验收。

| 历史检查 | 当时结果 | 需要保留的脱敏证据 |
| --- | --- | --- |
| Strata 页面代理配置 | 部署方报告按上文实际命令部署，`nginx -t` 输出 `syntax is ok`、`test is successful` | 启动时间及容器状态原始脱敏输出待补充 |
| Strata 页面公网访问 | 部署方报告首页、`/web/app.js` 为 200，`/health` 中 `loaded=true`，未授权 `/metrics` 为 401 | 浏览器自动化未完成、无截图；交互与证书信任结果待补充 |
| 本地 TLS 桥接 | 当时固定证书指纹后成功转发真实请求；现已停用，启动文件改名保留 | 不再作为当前直连方案的运行条件 |
| CPA 模型生效 | 热更新未生效；短暂重启正式进程后 `8317` 的 `cloud-qwen-strata` 已生效 | 具体重启时间与 provider/别名映射的脱敏记录待补充 |
| CPA → 云端真实推理 | 部署方报告 Windows 正式 CPA 于约 15:02:16 发起流式和非流式请求，均 HTTP 200，答案均为 `42` | 精确时间、流式帧数和结束标记等原始脱敏证据待补充 |
| 历史 Langfuse Generation | 部署方报告两条事件的输入均为当次 CPA 提示，输出均含 `42`，Token usage 与 CPA 一致；事件时间也匹配 | 请求确切时间和 usage 明细等脱敏原始证据待补充 |

历史桥接时期的两次请求及已核对的 Langfuse 事件如下；总 Token 数为部署方报告的网关返回值。两条事件的输入均为 `What is 17 plus 25? Reply only with the number.`，输出均含 `42`，Token usage 与 CPA 一致。

| 请求 | HTTP / 答案 | 网关返回总 Token | 实际 Langfuse Trace ID |
| --- | --- | ---: | --- |
| 流式 | 200 / `42` | 109 | `bf60db4b2d65da3f47a76f111e573d3f` |
| 非流式 | 200 / `42` | 118 | `0b69efca0da195449cd238d83009434a` |

本次从 CPA 发出的 W3C `traceparent` **没有原样透传**，表中是实际 Langfuse 事件的 Trace ID，不能视为客户端原始 Trace ID，也不能将两者当成相同标识。凭据不得放进命令行参数、终端输出、截图或本文。

## 5. 页面与历史记录待补充项

本节留给后续收到的实际结果，不把下面项目写成已完成：

- Strata 代理启动时间和容器状态的原始脱敏输出。
- 公网 `13261` 的检查时间、证书信任、浏览器交互及截图；当前自动化未完成截图。
- 旧桥接方案详细握手的脱敏诊断记录（如需追溯）；该方案现已停用。
- CPA 重启时间、provider 路由和别名映射的脱敏证据。
- 两种请求的确切时间、usage 明细、流式帧数及结束标记等脱敏原始证据。
- 若失败，记录发生层、HTTP 状态、脱敏错误与后续处理；`DB not connected` 的 key 生成失败不等同于既有 master key 的推理结果。

## 6. 当前正式方案：CPA 直接访问 HTTP 网关

用户已明确选择切换并允许中断原 HTTPS。部署方报告当前正式 CPA 使用 provider `cloud-nginx-litellm`，其 `base-url` 为 `http://203.0.113.30:13258/v1`，对外模型别名为 `cloud-qwen-strata`，映射到 LiteLLM 模型 `qwen-strata`。旧的本地 `127.0.0.1:18858` 桥接进程已停止，启动文件以 `.vbs.disabled` 后缀保留，当前路径不依赖它。

云端主网关容器为 `llm-a100-nginx-1`，容器内 `8443` 已改为 **HTTP**，映射关系为容器 `8443` → 宿主 `8800` → 公网 `13258`。现场配置为 `/opt/llm-observability-deploy/src/monitoring/nginx/nginx.conf`，已有以下备份：

```text
/opt/llm-observability-deploy/src/monitoring/nginx/nginx.conf.before-cpa-direct-http-20261116
/opt/llm-observability-deploy/src/monitoring/nginx/nginx.conf.before-cpa-route-restriction-20261116
E:\CLIProxyAPI_6.10.9_windows_amd64\config.yaml.backup-20261116-153501-before-direct-http-switch
```

LiteLLM 没有连接数据库，`/key/generate` 无法创建数据库管理的 key。为避免将 master key 在 HTTP 链路上直接发送，部署方在 Nginx 设置独立 CPA token 的私有路径映射：允许模型列表、`chat/completions`、`completions`、`responses` 等共 8 条配置的推理路径，在内部将其转换为 master key；该 token 访问其他路径返回 403。**这是路径级限制；未提供模型级限制证据，不能声称仅允许某个模型。** 8 条路径的完整清单以现场私有配置为准，本文不臆造。

私有映射及 token 分别位于 `nginx/certs/cpa-route-map.conf`、`nginx/certs/cpa-token-check.conf`、`nginx/certs/.cpa-http-token`。这些文件为私有材料，不展示、打印、提交或放入公开归档；备份时仍需保护其访问权限。本次 HTTP 链路中独立 token、请求及响应内容均可能被链路上的观察者读取或篡改，**不提供 TLS 加密，也不能获得 HTTPS 的服务端身份验证**。

### 当前直连验收

部署方报告，直接访问 `/v1/models` 返回 HTTP 200；使用该独立 CPA token 访问 `/key/generate` 返回 HTTP 403。原桥接进程停止后，正式 CPA 发起两次真实请求，流式和非流式均 HTTP 200 且回答 `42`。对应 Langfuse 事件的输入、输出及 usage 均与各自请求匹配：

| 请求 | 总 Token | Langfuse Trace ID |
| --- | ---: | --- |
| 流式 | 114 | `8ff8e1a4aa51233a181c2af6b56fa2ac` |
| 非流式 | 118 | `b4c8fb39e39b0d7989a4af6b7fd8632f` |

部署方另报告 Langfuse、Grafana 和 Strata 的 HTTP 入口健康检查仍为 200；具体检查地址、时间和原始脱敏响应待补充。以上是现场反馈，本文没有独立复验。第 4 节的 `109/118` 和旧 Trace ID 属于之前的 HTTPS 桥接测试，不是这次 HTTP 直连结果。当前两条直连测试的客户端 `traceparent` 与按输入、时间及用量匹配到的 Langfuse Trace ID 不同；不要将客户端 ID 与 Langfuse ID 假定相同。

**2026-11-16 15:41:35–36（UTC+8）追加测试：**部署方从正式 CPA `8317` 调用 `cloud-qwen-strata`，两次均 HTTP 200、答案 `42`，Langfuse 按输入、输出及 usage 匹配到相应事件：

| 请求 | 输入 / 输出 / 总 Token | 耗时 | Langfuse Trace ID |
| --- | --- | --- | --- |
| 非流式 | 68 / 40 / 108 | 0.84 秒 | `d1a1ff8936383474a41ee31baa64a449` |
| 流式 | 68 / 40 / 108，收到 `[DONE]` | 0.44 秒 | `6045c75f5d098174ebdb0b3d62cdbeea` |

同期网关、Langfuse 和 Grafana 健康检查返回 HTTP 200，Strata `/health` 返回 `loaded=true`、`max_context=32768`。两次均为含缓存的短请求，耗时仅是本次冒烟结果，**不是性能基准或长上下文验收**。

随后 Prometheus 检查显示 `litellm`、`prometheus`、`strata`、`strata-extra` 四个采集目标均为 UP；TTFT 和 TPOT 的 histogram count 各为 9，输入和输出 Token counter 分别为 2889、1108，GPU 0 和 GPU 1 显存序列均有样本。这些计数是检查时的**累计值**，不能当作某一次请求的独立指标；Prometheus 查询有样本也不证明 Grafana 页面已完成视觉验收。

### 不展示凭据的检查

在云端检查容器、端口映射和 Nginx 语法；命令不展开 Nginx 配置或私有文件。`nginx -t` 检查语法，不代表每条访问控制规则均已验证：

```bash
docker ps --filter name=llm-a100-nginx-1 --format 'table {{.Names}}\t{{.Status}}'
docker port llm-a100-nginx-1 8443/tcp
docker exec llm-a100-nginx-1 nginx -t
curl -sS -o /dev/null -w '网关存活检查 HTTP %{http_code}\n' http://127.0.0.1:8800/health/liveliness
```

在 Windows 上检查 CPA 与旧桥端口，不读取 CPA 配置：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8317,18858 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
Test-Path -LiteralPath 'C:\Users\可乐\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\CPA-CloudGateway-Bridge.vbs.disabled'
Test-Path -LiteralPath 'E:\CLIProxyAPI_6.10.9_windows_amd64\config.yaml.backup-20261116-153501-before-direct-http-switch'
```

需要再次验证 `/v1/models` 的 200 和 `/key/generate` 的 403 时，应由执行方以受控方式提供独立 CPA token，只记录 HTTP 状态；不要把 token 写入命令行参数、输出、截图或文档。一次 403 仅证明该次请求受限，应在现场逐一核对实际允许的 8 条路径及其他路径的拒绝行为。

### 备份与恢复

以下命令供后续实际运维使用，**本次文档更新未执行这些操作**。恢复旧网关配置会改变 `13258` 的协议或路径限制；恢复前先确认目标备份对应状态、现有 Compose 挂载和 Nginx include 依赖，并协调 CPA 的 base-url、凭据与必要的桥接。不要仅恢复其中一端后就宣称链路可用。不得展示私有映射或令牌内容。

云端：`nginx.conf` 可能以单文件 bind mount 挂入容器，恢复必须对现有文件**原位写入，保持 inode**；不能使用先写临时文件再 `mv` / `os.replace` 的方式。以下示例先创建不覆盖旧文件的私有备份，再选择切换 HTTP 前的备份恢复；语法检查通过后才 reload。备份可能包含敏感配置，必须按私有文件管理：

```bash
python3 - <<'PY'
import datetime, fcntl, os, pathlib, subprocess, sys

directory = pathlib.Path('/opt/llm-observability-deploy/src/monitoring/nginx')
config = directory / 'nginx.conf'
restore = directory / 'nginx.conf.before-cpa-direct-http-20261116'
backup = directory / ('nginx.conf.before-restore-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))

def write_in_place(f, data, identity):
    if (os.stat(config).st_dev, os.stat(config).st_ino) != identity:
        raise RuntimeError('nginx.conf inode changed')
    f.seek(0); f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())
    if (os.stat(config).st_dev, os.stat(config).st_ino) != identity:
        raise RuntimeError('nginx.conf inode changed')

def nginx(*args):
    return subprocess.run(['docker', 'exec', 'llm-a100-nginx-1', 'nginx', *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

try:
    desired = restore.read_bytes()
    if not desired:
        raise RuntimeError('empty restore point')
    with config.open('r+b') as current:
        fcntl.flock(current.fileno(), fcntl.LOCK_EX)
        st = os.fstat(current.fileno()); identity = (st.st_dev, st.st_ino)
        before = current.read()
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as saved:
            saved.write(before); saved.flush(); os.fsync(saved.fileno())
        try:
            write_in_place(current, desired, identity)
            if not nginx('-t'):
                raise RuntimeError('nginx test failed')
            if not nginx('-s', 'reload'):
                raise RuntimeError('nginx reload failed')
        except Exception:
            write_in_place(current, before, identity)
            if nginx('-t'):
                nginx('-s', 'reload')
            raise
    print('RESTORE_OK; private pre-restore backup created')
except Exception as exc:
    print('RESTORE_FAILED:', type(exc).__name__, file=sys.stderr)
    sys.exit(1)
PY
```

若恢复过程失败，脚本尽力原位写回原配置、检查并 reload；错误时应检查服务状态并人工处理，不能仅凭磁盘文件认定运行中的配置已经恢复。脚本用 inode 检查防止意外替换，但操作前仍需用 `docker inspect` 的 `Mounts` 核对宿主路径和容器挂载；变更期间应避免其他进程同时写配置。恢复 `nginx.conf.before-cpa-route-restriction-20261116` 是另一状态，不能假定保留当前 token 的路径限制。恢复后应验证协议、认证、路由和相关服务健康，再单独完成真实请求与 Langfuse 关联验证。

Windows：先备份当前 CPA 配置，再恢复 HTTP 切换前的版本。配置和备份均含敏感信息，仅保存在受限目录；不要打印内容。此操作本身不会确保运行进程已重新加载配置，也不会启动旧桥：

```powershell
$cpaDir = 'E:\CLIProxyAPI_6.10.9_windows_amd64'
$cpaConfig = Join-Path $cpaDir 'config.yaml'
$restorePoint = Join-Path $cpaDir 'config.yaml.backup-20261116-153501-before-direct-http-switch'
$currentBackup = Join-Path $cpaDir ('config.yaml.before-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (-not (Test-Path -LiteralPath $restorePoint)) { throw 'CPA 恢复文件不存在' }
if (Test-Path -LiteralPath $currentBackup) { throw '当前配置备份文件已存在' }
Copy-Item -LiteralPath $cpaConfig -Destination $currentBackup -ErrorAction Stop
Copy-Item -LiteralPath $restorePoint -Destination $cpaConfig -Force -ErrorAction Stop
```

恢复后核对文件访问权限和 CPA 的实际加载状态，并与云端协议同步；旧启动项为禁用状态，若决定恢复历史 HTTPS 桥接，需重新核验固定指纹与启动配置。当前直连的完整流式帧数、各项 usage 明细、8 条路径逐项拒绝/允许结果及恢复演练仍待补充。

## 7. 从已有服务复现 HTTP 直连

本节是根据现场脚本整理的**复现方案，不是已执行命令的逐字日志**。以下步骤会创建私有令牌、改网关监听协议并 reload，需在有维护窗口和目标机权限时由部署人员执行；本文编写过程没有执行。已有私有文件或备份时自动退出，不覆盖现场状态。本方案假定基础网关、Strata、Langfuse 和 Docker 网络已按云端记录部署，且容器名与路径一致；非同一环境先调整并核验。

### 7.1 节点、端口和职责

| 节点 | 本次地址或端口 | 职责与边界 |
| --- | --- | --- |
| Windows CPA | `8317`；客户端模型 `cloud-qwen-strata` | 接收客户端请求并转发至上游；自身客户端认证与云端独立 token 是两套凭据 |
| 云端主 Nginx | 公网 `13258` → 主机 `8800` → 容器 `8443`，现为 HTTP | 按独立 token 和 8 个路径映射内部 master key；其他 token 按原有规则转发给 LiteLLM，不能认为所有凭据都受到该 token 的限制 |
| LiteLLM | Docker 网络 `litellm:4000`；模型别名 `qwen-strata` | 鉴权、路由、生成网关指标和 Langfuse 回调 |
| Strata | Docker 网络 `strata:8080`；引擎模型 `qwen3.8-flash-next-iq3_xxs` | 真实推理；当前健康检查报告最大上下文 32768 |
| Langfuse | 公网 `13259` → 主机 `8801` → 网关 Nginx `8444` → Web `3000` | 保存回调生成的 Trace / Generation；不在请求响应的必经路径上 |
| Prometheus / Grafana | Prometheus 内部 `9090`；Grafana 公网 `13260` → 主机 `8802` → 代理 → Grafana `3000` | Prometheus 抓取网关、Strata 和补充 exporter；Grafana 查询指标，不直接接收每次推理请求 |
| Strata 页面 | 公网 `13261` → 主机 / 独立代理 `8803` → `strata:8080` | 页面和 Strata API 单独入口；仍使用 HTTPS，不等同于 CPA 的 HTTP 网关入口 |

主网关使用 HTTP，独立 token、提示词和回答均明文经过链路。路径限制不约束模型或请求内容；其他拥有 master key 等更高权限的凭据仍按 LiteLLM 配置生效。应在受控网络和访问规则下使用，并记录这一选择。

### 7.2 云机生成私有映射并安装

允许路径按现场两个脚本的代码为：

```text
/v1/models                 /models
/v1/chat/completions       /chat/completions
/v1/completions            /completions
/v1/responses              /responses
```

先确认 Nginx 的 `nginx.conf` 和 `certs` 目录实际分别挂载到容器 `/etc/nginx/nginx.conf` 与 `/etc/nginx/certs`，确保修改宿主文件会被容器看到。以下只打印挂载路径和类型，不展示配置内容：

```bash
docker inspect llm-a100-nginx-1 --format '{{range .Mounts}}{{println .Type .Source "->" .Destination}}{{end}}'
docker ps --filter name=llm-a100-litellm-1 --format 'table {{.Names}}\t{{.Status}}'
```

以下命令针对原始仓库配置的特定锚点：它检查 `listen 8443 ssl;`、HTTPS 转发头、LiteLLM upstream 和插入位置；与现场不同则失败退出。它仅从运行中的 LiteLLM 容器读取 master key 到子进程内存，在云机生成独立随机 token 和两个 Nginx 私有 include；不通过 stdout、命令行参数或本文显示凭据。三份私有文件创建为 `0400`，配置备份为 `0600`；应由有权限的管理员运行，并确认 Nginx master 进程能读取 include。该示例对 `nginx.conf` 采用原位写入与 inode 校验，不更换单文件 bind mount 的 inode。

```bash
umask 077
python3 - <<'PY'
import datetime, fcntl, os, pathlib, re, secrets, subprocess, sys

root = pathlib.Path('/opt/llm-observability-deploy/src/monitoring/nginx')
config = root / 'nginx.conf'
certs = root / 'certs'
token_file = certs / '.cpa-http-token'
route_file = certs / 'cpa-route-map.conf'
check_file = certs / 'cpa-token-check.conf'
backup = root / ('nginx.conf.before-cpa-http-rebuild-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
paths = ['/v1/chat/completions', '/chat/completions', '/v1/completions', '/completions',
         '/v1/models', '/models', '/v1/responses', '/responses']

def private_create(path, content, mode):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(content); f.flush(); os.fsync(f.fileno())
    os.chmod(path, mode)

def write_in_place(f, data, identity):
    st = os.stat(config)
    if (st.st_dev, st.st_ino) != identity:
        raise RuntimeError('config inode changed')
    f.seek(0); f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())
    st = os.stat(config)
    if (st.st_dev, st.st_ino) != identity:
        raise RuntimeError('config inode changed')

def nginx(*args):
    return subprocess.run(['docker', 'exec', 'llm-a100-nginx-1', 'nginx', *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

try:
    if not certs.is_dir() or config.is_symlink():
        raise RuntimeError('unexpected paths')
    if any(p.exists() for p in (token_file, route_file, check_file, backup)):
        raise RuntimeError('private file or backup already exists')
    with config.open('r+b') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        st = os.fstat(f.fileno()); identity = (st.st_dev, st.st_ino)
        before = f.read()
        original = before.decode('utf-8')
        listener = '    listen 8443 ssl;'
        marker = '  # Prometheus only.'
        anchor = '  limit_req_zone $binary_remote_addr zone=llm_api:20m rate=20r/s;'
        if original.count(listener) != 1 or original.count(marker) != 1 or original.count(anchor) != 1:
            raise RuntimeError('unexpected nginx layout')
        start = original.index(listener); end = original.index(marker, start)
        segment = original[start:end]
        forward = 'proxy_set_header X-Forwarded-Proto https;'
        upstream = '      proxy_pass http://litellm:4000;'
        if segment.count(forward) != 1 or segment.count(upstream) != 1 or segment.count('    location / {') != 1:
            raise RuntimeError('unexpected gateway section')
        master = subprocess.check_output(['docker', 'exec', 'llm-a100-litellm-1',
                                          'printenv', 'LITELLM_MASTER_KEY'],
                                         stderr=subprocess.DEVNULL).decode().strip()
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', master):
            raise RuntimeError('unsupported master key format')
        token = 'sk-cpa-' + secrets.token_hex(32)
        route = ''.join('"Bearer ' + token + ':' + p + '" "Bearer ' + master + '";\n' for p in paths)
        check = '"Bearer ' + token + '" 1;\n'
        maps = ('\n  map_hash_bucket_size 256;\n'
                '  map "$http_authorization:$uri" $cpa_gateway_authorization {\n'
                '    default $http_authorization;\n'
                '    include /etc/nginx/certs/cpa-route-map.conf;\n  }\n'
                '  map $http_authorization $cpa_restricted {\n'
                '    default 0;\n    include /etc/nginx/certs/cpa-token-check.conf;\n  }\n'
                '  map "$cpa_restricted:$uri" $cpa_deny {\n    default 0;\n    ~^1: 1;\n' +
                ''.join('    "1:' + p + '" 0;\n' for p in paths) + '  }')
        segment = segment.replace(listener, '    listen 8443;', 1).replace(
            forward, 'proxy_set_header X-Forwarded-Proto http;', 1).replace(
            upstream, '      proxy_set_header Authorization $cpa_gateway_authorization;\n' + upstream, 1).replace(
            '    location / {', '    location / {\n      if ($cpa_deny) { return 403; }', 1)
        updated = (original[:start] + segment + original[end:]).replace(anchor, anchor + maps, 1)
        private_create(backup, before, 0o600)
        private_create(token_file, (token + '\n').encode(), 0o400)
        private_create(route_file, route.encode(), 0o400)
        private_create(check_file, check.encode(), 0o400)
        try:
            write_in_place(f, updated.encode(), identity)
            if not nginx('-t'):
                raise RuntimeError('nginx test failed')
            if not nginx('-s', 'reload'):
                raise RuntimeError('nginx reload failed')
        except Exception:
            write_in_place(f, before, identity)
            if nginx('-t'):
                nginx('-s', 'reload')
            raise
    print('INSTALL_OK: private files created, nginx tested and reloaded')
except Exception as exc:
    print('INSTALL_FAILED:', type(exc).__name__, file=sys.stderr)
    sys.exit(1)
PY
```

失败时脚本尽力原位恢复原配置；若生成中断，已新建的私有文件和备份可能保留，后续运行会因文件已存在而停止。不要直接删除或覆盖：先在受控环境核实文件归属、权限、实际 Nginx 状态及是否需要保留，再进行恢复。`flock` 仅协调使用相同锁的进程；运维期间仍须避免其他部署进程同时修改配置。详细失败日志和运行状态需在不暴露凭据的环境下排查。

### 7.3 脱敏验证路由

在云端执行以下验证：令牌仅在 Python 进程中读取，响应体不输出。先验证允许的模型列表和拒绝的管理接口；仅当两项分别为 200 / 403 才返回成功。该检查使用云主机 `8800`，公网 `13258` 也应由受控客户端分别验证，不能把回环结果当成公网结果。

```bash
python3 - <<'PY'
import pathlib, sys, urllib.error, urllib.request

try:
    token = pathlib.Path('/opt/llm-observability-deploy/src/monitoring/nginx/certs/.cpa-http-token').read_text().strip()
    if not token:
        raise ValueError('empty credential')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def status(path, method):
        data = b'{}' if method == 'POST' else None
        request = urllib.request.Request('http://127.0.0.1:8800' + path, data=data, method=method,
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=15) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
    models, admin = status('/v1/models', 'GET'), status('/key/generate', 'POST')
    print('models HTTP', models, 'admin HTTP', admin)
    if (models, admin) != (200, 403):
        sys.exit(1)
except Exception as exc:
    print('CHECK_FAILED:', type(exc).__name__, file=sys.stderr)
    sys.exit(1)
PY
```

完整路径策略还需在受控环境逐条验证其余允许路径、非允许路径、请求方法和实际 LiteLLM 鉴权；一条管理接口 403 不能证明模型级隔离、所有方法和路径均正确。不要在 Nginx 中启用会记录 Authorization 或请求体的访问日志，也不要打印私有 include 或配置展开内容。

### 7.4 Windows CPA 配置

以下示例通过交互式 SCP 在已核验 SSH 主机身份后，将新环境生成的独立 token 放入当前用户的专用目录；命令行仅出现文件路径，不包含凭据。执行前检查 OpenSSH `scp` 可用，使用有权读取云端该私有文件的账号；不要关闭主机密钥校验。目录必须是新建的，并由当前 Windows 用户限制访问，失败时停止：

```powershell
$ErrorActionPreference = 'Stop'
$privateDir = Join-Path $env:LOCALAPPDATA 'CPA-CloudGateway-Rebuild-Private'
if (Test-Path -LiteralPath $privateDir) { throw '私有目录已存在，请先人工核查；不会覆盖' }
New-Item -ItemType Directory -Path $privateDir -ErrorAction Stop | Out-Null
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $privateDir /inheritance:r /grant:r "*${currentSid}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw '无法设置私有目录权限，停止传输' }
$tokenFile = Join-Path $privateDir 'cpa-http-token.txt'
if (Test-Path -LiteralPath $tokenFile) { throw '令牌目标已存在，不会覆盖' }
& scp.exe -P 13212 'root@203.0.113.30:/opt/llm-observability-deploy/src/monitoring/nginx/certs/.cpa-http-token' $tokenFile
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) { throw '安全传输失败' }
```

授权人员在受控编辑器中从该私有文件取值填入 CPA 配置，**不要在终端查看、打印或复制到公开记录**；确认 CPA 配置和备份也限制为授权账号可读。确认已安全配置、无需保留临时副本后，按现场私有材料处置流程处理此文件。若目标环境使用其他 SSH 用户、端口或容器名，须先核对并修改命令；SCP 需能读取云端 `0400` 文件。

CPA 配置有其他 provider 时，仅修改现有 `api-keys.openai-compatibility` 数组中的对应项，勿用下例覆盖整个 `config.yaml`。结构参考本机 `config.example.yaml` 和本次脚本；占位符不是有效凭据，必须由管理员通过可信渠道取得云端生成的独立 token，并在受控编辑器中填入，不能通过终端输出或命令行参数传递：

```yaml
api-keys:
  openai-compatibility:
    - name: "cloud-nginx-litellm"
      base-url: "http://203.0.113.30:13258/v1"
      keys:
        - api-key: "<在受限配置文件中填入独立CPA令牌>"
      models:
        - name: "qwen-strata"
          alias: "cloud-qwen-strata"
```

现场使用的切换前备份路径见第 6 节。复现前先按第 6 节的 PowerShell 方式备份现有 `config.yaml`，并保护配置和备份的 Windows ACL。可将候选配置复制到受限目录，在确认不会与正式进程端口冲突后，以独立 `127.0.0.1:18317` 验证；现场候选脚本曾使用此端口。候选验证通过后再按实际运维方式更新正式配置。现场历史热更新未生效，正式进程重启后生效，不能假定新环境一定自动热加载。任何候选配置、令牌文件和程序日志都按私有资料处理。

从正式 CPA `8317` 使用真实客户端分别发送非流式和流式 `cloud-qwen-strata` 请求；认证值应经客户端私有配置或交互输入提供，不能拼入可见命令行。核对回答、输入 / 输出 usage、流式 `[DONE]` 和 Langfuse 的输入输出及 usage。请求会产生真实模型调用与观测记录；如 CPA 不透传 `traceparent`，应按实际 Langfuse ID 和请求内容、时间、usage 关联，不冒认客户端 ID。

### 7.5 监控与离线边界

Prometheus 配置见 [prometheus.yml](../deploy/cloud-monitoring/prometheus.yml)，要求 `litellm`、`prometheus`、`strata`、`strata-extra` 四个 job 健康；Strata 原生指标须 `format=prometheus`，补充 exporter 依赖 Strata JSON 及认证。Grafana 使用 [详细看板](../grafana/dashboards/litellm-gpu-overview.json)，选择 Prometheus 数据源并分别检查 TTFT/TPOT、Token、GPU 0/1 和 `strata_extra_up`。Prometheus counter 为累计值；采样、规则更新和真实请求需要时间。自动路由未启用时第 08 区可无数据。Grafana 视觉呈现仍需人工核对。

既有离线候选包和已发布监控扩展包**不包含本次 CPA HTTP 直连及私有令牌映射配置**；迁移时需要在目标环境重新准备并安全生成私有配置，不能把现场凭据打包。麒麟 V10 干净机器的断网部署、依赖闭包、冷启动及恢复，以及本次模型长上下文和并发性能，均未验收。历史备份回滚命令见第 6 节，恢复过程及重新验证也尚未实测。
