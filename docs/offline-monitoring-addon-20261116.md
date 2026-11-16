# Grafana 与 Prometheus 离线监控扩展包

本扩展包用于已有 Nginx、LiteLLM、Strata 和 Docker 网络的 **linux/amd64** 部署。包含云端验证过的 Grafana 10.1.5、Prometheus 2.45.1 镜像、仓库看板、Prometheus 采集与规则、Strata 补充导出器和 Grafana Nginx 代理配置。它与 [Nginx / LiteLLM / Langfuse 离线候选包](offline-release-20261116.md) 配合使用，不包含该候选包的九个镜像、Strata 镜像、模型、主机 Docker 及其依赖、证书、密钥或历史数据。

## 下载和校验

从 [GitHub 候选 Release](https://github.com/tyloryang/litellm-langfuse-monitoring/releases/tag/offline-candidate-amd64-20261116) 下载 `monitoring-amd64-20261116.tar` 和 `MONITORING-SHA256SUMS` 到同一目录，校验后解包：

```bash
sha256sum -c MONITORING-SHA256SUMS
mkdir -p /opt/llm-monitoring-offline
tar -xf monitoring-amd64-20261116.tar -C /opt/llm-monitoring-offline
cd /opt/llm-monitoring-offline/monitoring-amd64-20261116
sha256sum -c SHA256SUMS
gzip -t images.tar.gz
gzip -dc images.tar.gz | docker load
```

包内 `images.tar.gz` 包含以下镜像，名称与部署配置一致：

| 镜像 | 用途 |
| --- | --- |
| `docker.m.daocloud.io/prom/prometheus:v2.45.1` | Prometheus 采集和规则计算 |
| `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/grafana:10.1.5` | Grafana 可视化 |

`source/` 包含 `deploy/cloud-monitoring/`、`grafana/dashboards/`、`prometheus/recording-rules.yml`、`prometheus/alerts.yml`、`exporters/strata_extra.py` 和相关文档。具体版本可在包内 `MANIFEST.json` 中核对。

## 部署前准备

1. 目标机准备 Docker Engine、Compose、Python 3、`gzip`、`tar`、`sha256sum`，并确认 Docker 能创建容器和持久卷。麒麟 V10 必须根据具体 SP、架构、内核、glibc、cgroup、SELinux 和网络驱动准备系统依赖；CentOS 7 候选 RPM 不能自动视为兼容。
2. 先完成主服务及其镜像离线导入，并启动 LiteLLM、Nginx、Strata；检查它们实际容器名、API 密钥环境变量、容器网络名以及 DNS 别名。此扩展还复用运行中的 LiteLLM 镜像运行 `strata-extra`，复用 Nginx 镜像作为 Grafana 代理。
3. 准备 Grafana 可访问的 HTTPS 域名或地址、端口、`server.crt` 和 `server.key`；在主机防火墙和平台上配置对应端口。准备磁盘空间及数据备份。新 Prometheus 卷从启动时开始采集，不包含旧数据。
4. 此配置默认 `litellm:4000`、`strata:8080`、`llm-a100_backend` 网络；若目标不同，修改 `source/deploy/cloud-monitoring/prometheus.yml`、`docker-compose.yml`，以及 `prepare.py` 中 `llm-a100-litellm-1`、`llm-a100-strata-1`、`llm-a100-nginx-1` 和对应的密钥环境变量名。确保这些名称与目标容器一致，避免写入错误 token。`strata-extra` 的 `STRATA_METRICS_URL` 也需与实际地址一致。

## 导入配置并启动

以下命令以包内配置独立运行监控，示例端口为 `8802`。请将域名、证书目录、网络名换为目标值。证书目录须已有 `server.crt` 和 `server.key`，密码由脚本生成在本机私有 `.env`；脚本不会覆盖已有私有配置。

```bash
cd /opt/llm-monitoring-offline/monitoring-amd64-20261116/source/deploy/cloud-monitoring
python3 prepare.py \
  --grafana-root-url https://grafana.example.internal:8802/ \
  --certs-dir /opt/llm-certs \
  --network llm-a100_backend \
  --host-port 8802

# 如果使用主离线包自带的独立 Compose 二进制，改成其绝对路径。
COMPOSE=docker-compose
"$COMPOSE" -p llm-monitoring -f docker-compose.yml config --quiet
"$COMPOSE" -p llm-monitoring -f docker-compose.yml run --rm --no-deps \
  --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
"$COMPOSE" -p llm-monitoring -f docker-compose.yml run --rm --no-deps \
  --entrypoint /bin/promtool prometheus check rules \
  /etc/prometheus/rules/recording-rules.yml /etc/prometheus/rules/alerts.yml
"$COMPOSE" -p llm-monitoring -f docker-compose.yml up -d --no-build --pull never
"$COMPOSE" -p llm-monitoring -f docker-compose.yml ps
```

如果目标使用 `docker compose` 插件，可将上面 `"$COMPOSE"` 替换为 `docker compose`。先确保对应的 LiteLLM、Nginx 镜像在本机存在；`--pull never` 不会联网拉取。日常重启不要重复执行 `prepare.py`。账号默认 `admin`，在授权的目标机终端读取密码：

```bash
python3 -c 'import shlex; print(shlex.split(open(".env").read().split("GRAFANA_ADMIN_PASSWORD=",1)[1].splitlines()[0])[0])'
```

## 目标机验收

等待至少两个采集周期，在 Grafana 中打开 `LLM 网关 → LLM 推理观测 · Strata / LiteLLM / GPU`，数据源选择 `Prometheus`。按实际应用发出流式和非流式请求，核查响应、Langfuse 记录、Prometheus `up`、`strata_extra_up`、Token 计数、TTFT / TPOT 及每张 GPU 数据。原始看板有 68 个指标面板，公式和口径见 [Grafana 公式说明](grafana-formulas.md)。自动路由、来源 IP、费用、其他推理引擎等图表依赖相应配置、指标及真实流量，没有样本时不能视为已验收。

云端 CentOS 7.9 已运行上述版本，四个采集目标 UP、双卡及网关关键指标有样本；真实流式和非流式请求均返回 200 且进入 Langfuse。麒麟 V10 干净环境的断网导入、安装、采集、重启与数据恢复仍须在目标 SP / Build 上执行并记录结果。应另行备份 Prometheus / Grafana 数据卷及私有配置；本包不含历史数据。
