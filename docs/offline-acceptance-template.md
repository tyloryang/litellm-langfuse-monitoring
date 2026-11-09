# 麒麟 V10 完全离线部署验收记录

本模板在**离线目标现场**复制到私有验收目录后填写。不得将生产密码、API Key、私钥或展开后的完整 Compose 配置放进报告。未执行的项填“待验收”；文件哈希通过不自动代表整体部署通过。

## 交付与环境

| 项目 | 实际记录 |
| --- | --- |
| 验收日期、人员、目标机器 | 待填写 |
| 包版本、Git commit、SHA256SUMS 的独立摘要 | 待填写 |
| 麒麟产品、V10 SP / Build、ISO / 仓库快照版本 | 待填写 |
| CPU / 架构 / 指令集、内核、glibc、systemd | 待填写 |
| Docker / containerd / runc / Compose 精确版本、复用或新装 | 待填写 |
| storage driver / snapshotter、data-root / backing filesystem、XFS ftype | 待填写 |
| cgroup 版本 / driver、SELinux 模式 / 标签策略、firewalld 规则 | 待填写 |
| 独立 Python 3.12 路径 / ABI / OpenSSL、pip 兼容 tag 报告 | 待填写 |
| RPM 精确 NEVRA 清单、签名发行方 / 公钥指纹 | 待填写 |
| 部署范围：复用 / 新装的组件与每个模型 ID / 别名 | 待填写 |
| 内网拓扑、端口、DNS / NTP / CA | 待填写 |
| 外网阻断开始时间、ACL / 出口审计证据位置 | 待填写 |
| 同版干净麒麟机器、初始 RPM / 缓存基线 | 待填写 |
| 报告与日志位置（位于已封包目录外） | 待填写 |

## 分项结果

| 检查项 | 通过标准 | 状态 | 证据路径 / 实际结果 |
| --- | --- | --- | --- |
| 传输完整性 | 全部 SHA-256 / 大小一致，清单来源已确认 | 待验收 | |
| 镜像 | 所需 9 镜像及新增组件齐全，架构 / 完整 image ID 与锁一致 | 待验收 | |
| 系统包 | 仅本地同版 RPM / 已验证运行时安装；禁用全部 YUM / DNF repo，签名 / 依赖完整，无强制安装 | 待验收 | |
| 宿主兼容 | CPU、内核、seccomp、cgroup、存储 / XFS、SELinux Enforcing（启用时）、firewalld 下真实容器正常 | 待验收 | |
| Python | 独立 3.12 / SSL / 原生扩展可用，保留系统 Python；空 venv 本地 wheel 安装、pip check 成功 | 待验收 | |
| Tokenizer / 模型 | 完整缓存 / 权重，无网首启与首次请求无下载 | 待验收 | |
| LiteLLM 容器回调 | network none 下真实 loader 能加载自定义回调 | 待验收 | |
| MinIO / mc | entrypoint、UID、桶创建、重启幂等均通过 | 待验收 | |
| 依赖与迁移 | PostgreSQL / ClickHouse / Redis 健康，Web / Worker 迁移与消费成功 | 待验收 | |
| TLS 与登录 | SAN / CA 校验、本地管理员登录、项目 Keys、媒体访问通过 | 待验收 | |
| 非流式推理 | 每个部署模型真实成功且有 usage | 待验收 | |
| 流式推理 | 每个部署模型真实成功、有多帧和最终 usage | 待验收 | |
| Langfuse generation | input / output / 模型 / Session / User 完整，TTFT 与 metadata 对齐 | 待验收 | |
| usage / 成本 | 互斥 Token 分量总和正确；费用已核价或明确 unconfigured | 待验收 | |
| 工具执行链 | 同一 Trace 下 AGENT + TOOL + 两次 GENERATION，17+25=42 | 待验收 | |
| Prometheus | 必需 jobs UP、16 recording rule / 9 alerts 加载 | 待验收 | |
| Grafana | 51 面板 / 78 查询通过，必需 GPU / Token / 时延序列有限且可见 | 待验收 | |
| 计算公式 | TPOT 分母 / P95 聚合 / 固定窗口 / 字节换算等口径核查 | 待验收 | |
| 异常与告警 | 实验环境的中断 / 失败 / exporter 断开与恢复可观察 | 待验收 | |
| 断网冷启动 | 从安装到请求始终无互联网，pull never / no-build，无外部兜底 | 待验收 | |
| 重启与持久化 | 麒麟重启后系统级服务恢复，无需下载，历史数据 / 新请求 / SELinux 标签 / 网络 / CA 均通过 | 待验收 | |
| 备份恢复 | 原镜像 / 加密键的一致性恢复成功，历史 Trace 可查 | 待验收 | |

## 真实请求与链路证据

| 模型 / 场景 | request / trace / session ID | usage | TTFT / TPOT | 结果 |
| --- | --- | --- | --- | --- |
| 非流式（每个模型一行） | 待填写 | | | |
| 流式（每个模型一行） | 待填写 | | | |
| add_numbers 工具链 | 待填写 | | | |
| 重启后的流式与工具链 | 待填写 | | | |

## 结论

- 通过：仅在部署矩阵内所有必需项通过、全部外网阻断证据齐备后填写。
- 未通过 / 待验收：列出缺失物料、失败步骤、影响范围和准备机需补充的内容。
- 可选未部署组件：逐项写明，不能以“可选”为由排除已承诺的指标或工具链。

最终状态：**待验收**。

验收人 / 复核人：待填写。
