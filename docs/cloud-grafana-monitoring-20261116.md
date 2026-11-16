# 云 GPU 服务器 Grafana 监控部署记录

## 环境与访问

本记录针对 2026-11-16 已部署的云 GPU 主机：两张 A100，现有服务包括 Nginx、LiteLLM、Langfuse、Strata 0.1.45 与 Qwen3.8-Flash-Next IQ3_XXS。此处新增独立监控 Compose 项目，加入现有 `llm-a100_backend` 网络，没有重建原推理或 Langfuse 容器，也没有覆盖原有服务的数据卷。

Grafana 入口是 [https://203.0.113.30:13260/](https://203.0.113.30:13260/)，云平台映射为公网 `13260` → 主机 `8802` → 独立 Nginx 代理 → Grafana `3000`。该代理复用现有证书；如果客户端不信任该证书，应由管理员配置可信证书或在客户端处理信任。账号为 `admin`。密码在云服务器私有配置中生成，不存入仓库。在已登录云主机的授权终端查看：

```bash
cd /opt/llm-observability-deploy/monitoring-addon-20261116/deploy/cloud-monitoring
python3 -c 'import shlex; print(shlex.split(open(".env").read().split("GRAFANA_ADMIN_PASSWORD=",1)[1].splitlines()[0])[0])'
```

首次登录后打开 **Dashboards → LLM 网关 → LLM 推理观测 · Strata / LiteLLM / GPU**，数据源选择默认的 `Prometheus`。也可直接访问 [看板](https://203.0.113.30:13260/d/litellm-gpu-overview/)。看板由文件自动配置，`allowUiUpdates: false`：需要持久更新时修改仓库模板并部署该文件。

## 架构与指标

```text
公网 13260 → 主机 8802 → grafana-proxy (Nginx/TLS) → Grafana 10.1.5
                                                         ↓
                                                Prometheus 2.45.1
                                                  ↓    ↓       ↓
                                              LiteLLM Strata  strata-extra
```

- Grafana 和 Prometheus 分别使用独立持久卷 `llm-a100-monitoring_grafana_data`、`llm-a100-monitoring_prometheus_data`。新增的 Prometheus 从部署时开始采集，不包含原有监控历史。
- Prometheus 抓取 LiteLLM 网关、Strata 原生 Prometheus 接口、Strata JSON 指标补充导出器以及自身；抓取认证 token 放在目标机的私有文件中。`strata-extra` 使用现有 LiteLLM 镜像运行仓库内 Python 导出器。
- 看板使用仓库原始 [litellm-gpu-overview.json](../grafana/dashboards/litellm-gpu-overview.json)，含 8 个分区和 68 个指标面板，涵盖网关、Token、TTFT、TPOT、双卡 GPU、来源 IP 和自动路由等。每个面板说明包含计算公式、单位、口径及实际 PromQL；集中说明见 [Grafana 公式手册](grafana-formulas.md)。
- 自动路由面板是模板能力；云机目前未启用四档 Auto Router，也没有对应流量，因此该区域没有样本，并不代表四档路由已完成验收。费用指标需要实际价格配置。引擎不提供的字段和其他引擎专属面板可能为空，补充导出器中仅带模型标签的 PCIe 指标不能当成双卡分别统计。

## 本次部署方式

目标机的文件目录为 `/opt/llm-observability-deploy/monitoring-addon-20261116/`，配置在其 `deploy/cloud-monitoring/` 下。配置引用同一目录树中的仓库 dashboard、Prometheus 规则及 `exporters/strata_extra.py`。下列命令假设现有云端容器名、网络、证书路径与本次相同；迁移时先按目标环境修改 `prepare.py` 中的现有容器名以及网络和证书参数。准备脚本会读取现有容器中的服务 token，生成新的 Grafana 管理员密码，并拒绝覆盖已有 `.env` 或 `secrets/`。

```bash
cd /opt/llm-observability-deploy/monitoring-addon-20261116/deploy/cloud-monitoring
python3 prepare.py \
  --grafana-root-url https://203.0.113.30:13260/ \
  --certs-dir /opt/llm-observability-deploy/src/monitoring/nginx/certs \
  --host-port 8802

COMPOSE=/opt/llm-observability-deploy/downloads/docker-compose-linux-x86_64
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml config --quiet
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml run --rm --no-deps \
  --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml run --rm --no-deps \
  --entrypoint /bin/promtool prometheus check rules \
  /etc/prometheus/rules/recording-rules.yml /etc/prometheus/rules/alerts.yml
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml up -d --no-build --pull never
"$COMPOSE" -p llm-a100-monitoring -f docker-compose.yml ps
```

不要在已准备的实例上重复执行 `prepare.py`；日常启动从 `config --quiet` 开始。镜像必须提前存在：`docker.m.daocloud.io/prom/prometheus:v2.45.1`、`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/grafana:10.1.5`，以及当前 LiteLLM 和 Nginx 容器对应镜像。新环境应预先导出、传输、校验并导入所有镜像，并按目标端口调整外网映射与 `GRAFANA_ROOT_URL`。只备份配置不足以保存历史：还需按停机一致性要求备份两个持久卷及私有配置，并妥善保护密钥。

## 现场验收结果与边界

2026-11-16 云机验证结果：

1. Prometheus 2.45.1 的 `promtool` 校验配置通过，载入 16 条记录规则和 9 条告警规则；`prometheus`、`litellm`、`strata`、`strata-extra` 四个目标均为 UP，`strata_extra_up=1`，规则健康状态正常。
2. 查到 GPU 0、GPU 1 两条显存及利用率序列，也查到 Strata 输入 / 输出 Token 和网关 TTFT / TPOT 样本。Grafana 10.1.5 的健康 API 正常，默认数据源及 68 个指标面板已载入。
3. 将看板 105 条 PromQL 的变量替换为全选后直接查询 Prometheus，105 条执行成功，其中 90 条有样本。没有样本的内容包括自动路由、vLLM / SGLang 独有指标，以及 Strata 当前未提供的部分最近请求和槽位字段。此检查不等同于逐面板浏览器视觉验收。
4. 通过 Nginx → LiteLLM → Strata 重新发送非流式和流式请求，均返回 HTTP 200 和答案 `42`；对应两条新 Generation 均在 Langfuse 中查到。

本次监控扩展、Prometheus / Grafana 镜像**尚未加入**已发布的四卷离线候选包。离线候选包范围见 [离线候选发布说明](offline-release-20261116.md)。麒麟 V10 干净目标机断网安装及验收仍待完成；本云端部署结果不能替代该验收。
