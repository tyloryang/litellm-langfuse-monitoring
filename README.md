# LiteLLM + Langfuse 离线部署与运维

此仓库用于管理 LiteLLM 统一网关、Langfuse 自部署、现有 Prometheus/Grafana，以及 vLLM、SGLang、Strata GPU 虚拟机的监控方案。

## 部署架构

- VM-01：LiteLLM、Langfuse Web/Worker、PostgreSQL、ClickHouse（Langfuse 内部依赖）、Redis、MinIO。
- VM-02：现有 Prometheus，采集 LiteLLM `/metrics`，可选采集 GPU 节点指标。
- VM-03：现有 Grafana。
- GPU VM：vLLM、SGLang、Strata；GPU/KV Cache 指标须由引擎 Metrics 或 Exporter 提供。

## 状态

仓库已初始化。完整部署手册、Compose 配置与拓扑图尚待上传；当前内容不可视为已经验证的生产部署包。

## 关键限制

- IP 与 Token 的逐请求关联需要可信代理注入元数据与请求级记录。
- 准确 TPOT 需要流式计时和可靠的输出 Token 计数。
- GPU/KV Cache 内部指标无法仅从 LiteLLM 或 Langfuse 获得。
- Langfuse 自部署的 ClickHouse 是其内部依赖，不是单独建设的分析平台。
