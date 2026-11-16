# Strata 源码及云端配置

源码快照位于 [`source/strata`](../../source/strata/)，上游项目 [Niko1221/Strata](https://github.com/Niko1221/Strata)，固定版本 `v0.1.45`、提交 `477ac13cbe8e582c20484871ce459d20d82df09d`。上游 MIT 许可和版权声明见 [LICENSE](../../source/strata/LICENSE)。这是一份普通源码快照，不是 Git 子模块，克隆本仓库后即可查看代码。

2026-11-16 与云端 `local/strata:0.1.45-cuda12` 镜像中的源码按 SHA-256 核对：纳入的 3,285 个上游文件中，3,275 个与 Git blob 一致；其余 10 个 Windows `.bat` 使用 CRLF，云端文件与本快照的 CRLF 版本一致。云端镜像无 Git 元数据；逐文件比较不证明其可执行文件由该源码和当前构建环境完整复现。另行保存的 [Dockerfile.cuda12](../../source/strata/Dockerfile.cuda12) 取自运行中的云端镜像。

仓库包含引擎、Python 服务、工具、测试、上游文档和必要的小型 profile / tokenizer 辅助资源；不包含模型分片、模型权重、pack、运行时数据及真实凭据。上游 `data/experimental-speed-projection/*.gguf` 控制向量文件也未包含；若需要该可选功能，须另行取得并验证。本地上游工作树中的未跟踪个人测试和记录也未纳入。

配置和现场记录：

- [双 A100、262144 上下文、4 槽 JSON 模板](../../examples/strata-iq3_xxs-cloud-4slot.example.json)：`api_key` 是占位符。将配置合并进目标已有文件时应保留原密钥与其他现场字段，路径需按挂载修改。
- [独立 Compose 示例](docker-compose.example.yml)：复用已有 `/data` pack、MTP 和 `/models` 模型分片，密钥使用私有环境变量传入；要接入 LiteLLM，需与网关加入同一个 Docker 网络。示例中同时设置 `API_KEY` 和 `STRATA_API_KEY`，前者供首次 setup，后者供服务鉴权；应与客户端配置一致。
- [云端变更、锁页、调用链验证和回滚](../../docs/cloud-strata-memlock-deployment-20261116.md)。
- [完整离线部署与复用入口](../../docs/kylin-v10-reuse-offline-bundle-20261116.md)。

## CUDA 12 镜像构建示例

在已安装 Docker 的联网构建机上，从仓库根目录执行：

```bash
docker build -f source/strata/Dockerfile.cuda12 \
  --build-arg 'CUDA_ARCHITECTURES=80' \
  --build-arg BUILD_VISION=0 \
  -t local/strata:0.1.45-cuda12 source/strata
```

`80` 对应本例 A100 架构，其他 GPU 需按目标架构和 CUDA 编译器重新选择。Dockerfile 包含基础镜像、系统包、Python 依赖和 llama.cpp 获取步骤，上述构建需要事先准备这些依赖或联网；**源码快照本身不是完整离线构建包**。目标麒麟 V10 的驱动、容器运行时、既有模型和 pack 仍须分别核对，不能将云端通过结果视为麒麟验收。

Compose 示例仅展示 Strata 组件，不自动发布端口。准备好私有配置及挂载路径后，在测试环境执行 `docker compose -f deploy/strata/docker-compose.example.yml config --quiet` 校验；启动和接入应遵循目标环境的网络和认证配置。锁页会显著占用不可回收内存：云端启用后可用内存曾降至约 1.1 GiB，部署前需评估资源并在启动后检查日志及 `VmLck`。

## Linux 大页与锁页

Linux 的 HugeTLB 大页池是宿主机的内存设置，不是 JSON 中的 `MALLOC_MMAP_THRESHOLD_` 或容器的 `memlock`。本版本在 CPU expert arena 的分配路径尝试 2 MiB `MAP_HUGETLB`；池不足、权限或 cgroup 条件不满足时会回退并可能请求透明大页。成功预留大页池不等于 Strata 已使用，也不能保证提速；日志中的 `hugetlb 2 MB pages`、进程 `/proc/<pid>/status` 的 `HugetlbPages` 可以用来核查。截图所示 20,480 个 2 MiB 页共 40 GiB，但该截图的池状态为全部空闲，仅能证明当时已预留。

`memlock` 控制可锁定内存的限额；本云端的 `--ple-io ram` 锁定的是 PLE 表，与 expert arena 使用大页是不同的分配路径。大页池可能减少普通内存余量；本云机内存已经紧张，不应直接照搬截图的 40 GiB 池。性能应按同一版本、输入、缓存和并发状态比较首 token、解码速度、内存和 swap。关于内核池语义和 NUMA 分配，参考 [Linux HugeTLB 文档](https://docs.kernel.org/admin-guide/mm/hugetlbpage.html)。
