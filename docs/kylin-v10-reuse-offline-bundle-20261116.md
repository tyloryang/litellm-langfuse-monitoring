# 麒麟 V10 复用已有 Strata：离线包使用入口

本文适用于用户确认复用目标机已有容器环境、Strata 和模型的场景。目标机照片显示 x86_64、两张 A100 40GB，已有 Strata 进程；**现场仍须核对实际版本、端口、模型、认证和容器网络**。本包交付网关、Langfuse、监控和宿主工具候选，不含 Strata 镜像、模型、NVIDIA 驱动或已验证的目标麒麟系统 RPM 依赖闭包；不能用于在空白 GPU 主机上独立安装模型。

## 1. 包含内容和前置条件

- 网关主包：Nginx、LiteLLM 1.106.6、Langfuse Web / Worker 4.63.0 及 PostgreSQL、ClickHouse、Redis、MinIO 和 mc，共九个镜像；Compose v2.29.7 在主包中。
- 监控包：Prometheus 2.45.1、Grafana 10.1.5、规则、导出器和原始看板；`patches/` 另含修复 TTFT 后的看板及计算公式。
- `manual/`：完整部署手册、截图、现场核查与验收表，以及复用宿主机 Strata 的监控适配脚本。
- `system/kylin-v10-system-rpm-candidates-20261116/`：独立 Compose 二进制、CentOS 7 Docker RPM 及 NVIDIA Toolkit RPM 候选和来源材料；它们尚未在目标麒麟机验证，也不构成完整 RPM 依赖闭包。正常复用路径不需要安装或覆盖这些组件。
- 目标机必须已有并可正常复用 Docker、GPU 驱动及 Strata；宿主需 Bash、Python 3 标准库、OpenSSL、tar、gzip、sha256sum 和可用空间。具体麒麟 SP / Build 和这些工具是否齐全尚未现场确认。需要 GPU 容器时再确认 NVIDIA Container Toolkit；仅通过网络复用既有 Strata 不要求新建 GPU 容器。

完整检查、配置及安全边界见 [麒麟 V10 离线部署主手册](kylin-v10-full-offline-deployment-20261116.md)。用户提供的截图含请求内容、来源 IP 和主机信息，随本私有交付包使用受控介质保存。

主手册附录 A 包含修复后的完整 Strata 启动 JSON、Docker Compose 中 GPU 与 memlock 的配置、麒麟主机大页的查看 / 设置 / 验证 / 回滚，以及云端锁页后内存余量与验收边界。离线归档是打包时的固定快照；旧版归档不会因仓库文档更新而自动更新，转运前须核对归档内手册是否包含附录 A，缺少时应另行携带最新版主手册及其依赖文件或重新制作并校验归档。

## 2. 在目标机解包

提前将离线归档 `kylin-v10-gateway-reuse-amd64-20261116.tar` 和同目录 `.sha256` 文件转入目标机；目标路径 `/opt/llm-handoff` 必须不存在。`tar` 内已经是 `llm-handoff/` 顶层目录。命令使用本地文件，**不联网下载**：

```bash
set -eu
cd /实际离线包所在目录
sha256sum -c kylin-v10-gateway-reuse-amd64-20261116.tar.sha256
test ! -e /opt/llm-handoff
tar -xf kylin-v10-gateway-reuse-amd64-20261116.tar -C /opt
cd /opt/llm-handoff
sha256sum -c HANDOFF-SHA256SUMS
cd system/kylin-v10-system-rpm-candidates-20261116
sha256sum -c SHA256SUMS
```

`downloads/` 中的主包为已校验的 gzip，需恢复其原始 tar；目标磁盘必须额外预留约 5.72 GB，以及解包、Docker 镜像层、数据库、监控数据和备份空间。以下先写临时文件，校验通过后再改名：

```bash
set -eu
cd /opt/llm-handoff/downloads
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
test ! -e nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
grep 'candidate-20261116-v3.tar.gz$' SHA256SUMS | sha256sum -c -
gzip -dc nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz > nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete
printf '%s  %s\n' '627e4b44ba9fcf4da8a7f9f3d1227348815ca021d153c978236758ced206b702' 'nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete' | sha256sum -c -
mv nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.incomplete nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar
sha256sum -c MONITORING-SHA256SUMS
sha256sum -c VALIDATED-SHA256SUMS
cd /opt/llm-handoff/patches
sha256sum -c PATCH-SHA256SUMS
```

这一步只恢复包文件；不启动容器或改变现有 Strata。包内 `HANDOFF-SHA256SUMS` 记录的是归档解出时的文件，恢复的主包 tar 和部署运行时生成的私有配置不在原清单中。

## 3. 按手册部署和验收

离线打开 `/opt/llm-handoff/manual/docs/kylin-v10-full-offline-deployment-20261116.md`，从第 5.2 节开始：

1. 检查麒麟版本、可用磁盘、Docker 和宿主工具；复用已有 Strata 时跳过新建 GPU 容器和模型文件导入，不重启已有进程。
2. 按第 6.3 节确认现有 Strata 的健康状态、模型 ID、认证和监听地址；主栈启动后再从 LiteLLM 容器验证到该地址的连通性。
3. 按第 6、7 节导入九个镜像，指定实际内网监听地址与证书，初始化网关及 Langfuse；用已有 Strata 的真实地址、模型 ID 和密钥配置 LiteLLM。
4. 按第 10 节导入监控镜像与 TTFT 看板补丁；复用宿主 Strata 时先运行包内 `manual/ops/adapt_monitoring_existing_strata.py`，再执行 `promtool` 和实际采集验证。
5. 按第 11、12 节用真实流式和非流式请求验证返回、usage、Langfuse Trace、Grafana、来源 IP，记录结果并执行重启与备份恢复验收。

目标系统版本、驱动、容器运行时、现有服务状态及断网验收结果均需现场填写；云端和 WSL 的历史测试不能替代麒麟目标机验收。如果缺少系统工具或版本不兼容，先在同 SP / Build 准备可信 RPM 及完整依赖并离线安装，不能将包内 CentOS 7 候选 RPM 视为已验证兼容。
