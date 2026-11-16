# 麒麟 V10 离线宿主工具准备与现场检查

记录日期：2026-11-16。目标截图给出 Kylin Linux Advanced Server V10 (Sword)、x86_64、内核 `4.19.90 v2101.ky10`、两张 A100 40 GB 和驱动 `535.104.12`。**SP、Build、已安装软件和 Docker/Compose 版本尚未取得；不能 SSH 到目标机。**截图信息仍需现场核对。

本次复用目标已有 Docker、驱动、GPU runtime、Strata 和模型；离线网关与监控应用均运行在容器中。宿主仅检查 Bash、Python 3 标准库、OpenSSL、tar、sha256sum，以及可用的 Docker 和 Compose。Python 版本仍需按实际交付的宿主脚本要求确认；本文件中的 NVIDIA 和 CentOS RPM 仅是候选，不是本次安装前置条件，不代表麒麟兼容或依赖闭包完整。

## 已准备物料和验证范围

仓库准备机上的目录为 `offline-bundle/kylin-v10-system-rpm-candidates-20261116/`，它被 Git 忽略。复用已有 Strata 的综合离线包已将这些候选复制到 `/opt/llm-handoff/system/kylin-v10-system-rpm-candidates-20261116/`，配套脚本在 `/opt/llm-handoff/manual/ops/`。目录内 `SHA256SUMS` 覆盖 15 个现有文件，拷贝后在目录根运行 `sha256sum -c SHA256SUMS`。该清单只验证文件一致性；应通过独立可信渠道核对清单自身：SHA-256 `fa813af2d36c88f4ad8b594a6a81e976e2242b47472a3ca1da45e85984269d2f`。

| 文件 | 字节 | SHA-256 | 当前结论 |
|---|---:|---|---|
| `centos7/nginx-litellm-langfuse/host/compose/docker-compose` | 63,785,037 | `383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0` | v2.29.7，x86-64 静态 ELF；WSL 仅执行 `version` 成功。哈希与 [Docker 官方 v2.29.7 校验文件](https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64.sha256)一致；目标机运行、Docker API 和业务 Compose 兼容性未验证。其 `LICENSE` 同目录。 |
| `centos7/nginx-litellm-langfuse/host/rpms-centos7/containerd.io-1.6.7-3.1.el7.x86_64.rpm` | 34,672,172 | `e12ba6721449efca52f6da4cd2e1295455ed3f00863555a48c7b84ef5680e018` | 来自本地 CentOS 7 候选归档，非麒麟闭包。 |
| 同目录 `docker-ce-20.10.17-3.el7.x86_64.rpm` | 23,442,736 | `34e29c3f691fbb0520d76c78e3370561618cb3ddebddf2708cc345b655482bb8` | 同上。 |
| 同目录 `docker-ce-cli-20.10.17-3.el7.x86_64.rpm` | 30,839,424 | `715eacf13d7178578d074e458a2bc5416912eb39c588955e90f649f32d7ad26c` | 同上。 |
| `nvidia/libnvidia-container-tools-1.17.8-1.x86_64.rpm` | 41,320 | `c526a1b56fc8dd334361416066fdda3ba44f5beb33af90f1f7fd7eb766ec98c5` | NVIDIA 上游候选。 |
| `nvidia/libnvidia-container1-1.17.8-1.x86_64.rpm` | 1,024,276 | `00d1317babed764d285f76ccd9a12c8b71185023fa2244c38a59d895a7d2e38b` | 同上。 |
| `nvidia/nvidia-container-toolkit-1.17.8-1.x86_64.rpm` | 1,296,800 | `033e880003290b6ece6b807019e4931c1fa15092ec9827376fd54d72b120b87f` | 同上。 |
| `nvidia/nvidia-container-toolkit-base-1.17.8-1.x86_64.rpm` | 6,037,748 | `6b5d40b6bdda4473f92d3545ab91ec0b8cde97a2ce4a3fbad529856000742041` | 同上。 |

CentOS 7 文件取自本地 `offline-bundle/nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar`，其原归档声明的哈希与提取文件一致；仓库内还保留原要求列表及 Docker 公钥，但本次未在 RPM 环境完成签名检查。Compose 官方校验文件可从表中链接取得，本机实际取得并比对相等。

NVIDIA 四包来自 [NVIDIA 官方 stable RPM 仓库](https://nvidia.github.io/libnvidia-container/stable/rpm/x86_64/)，版本与 [1.17.8 官方安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.8/install-guide.html)中的四包版本一致。已从上游取得仓库 `repomd.xml`、签名、公钥和 primary 元数据；本机 `gpgv` 报 Good signature，签名密钥指纹 `C95B321B61E88C1809C4F759DDCAE044F796ECB0`，并验证了元数据到四个包的哈希链。公钥来自同一上游，实际使用前仍须通过独立可信渠道核对指纹；该仓库配置为验证仓库元数据，不能把哈希链表述为四个 RPM 独立签名均已验证。未验证麒麟、535 驱动或运行中服务与此版本组合。

复核 NVIDIA 哈希链（Python 3 标准库，离线）：

```bash
python3 /opt/llm-handoff/manual/ops/kylin-verify-nvidia-metadata.py /opt/llm-handoff/system/kylin-v10-system-rpm-candidates-20261116/nvidia
```

该脚本不验证签名。持有已独立核验的 NVIDIA 公钥时，可另行用 `gpg --dearmor` 和 `gpgv` 验证 `nvidia/repodata/repomd.xml.asc` 对 `repomd.xml` 的签名；两个步骤均需要，不能用哈希链替代公钥信任。

## 目标机必须采集的信息

将 `ops/kylin-target-inventory.sh` 单独复制到目标机，使用有权访问 Docker socket 的现场账号执行：

```bash
bash /opt/llm-handoff/manual/ops/kylin-target-inventory.sh > kylin-inventory.txt 2>&1
```

脚本只读取信息，不安装包、不重启或更改服务。它记录发行版标识、内核、glibc、RPM 数据库、cgroup、宿主工具、Docker 客户端/服务端、容器运行时及 GPU 摘要。现场先审阅输出再分享：完整 RPM 清单和发行版信息可能包含内部环境信息；不需要发送 `.repo`、环境变量、Docker inspect 环境字段、配置文件或凭据。Docker socket 权限不足或命令不存在时输出会标明失败，不能当作未安装的证明。

必须由现场补全并记录：① 产品、SP、Build、架构及安装介质版本；② RPM 版本与已装 RPM 清单；③ Bash、Python 3 及其 `hashlib/json/ssl/tarfile`、OpenSSL、tar、sha256sum 是否可用；④ Docker 服务端/客户端、Compose、containerd/runc、cgroup、存储驱动及权限；⑤ 两张 GPU、驱动、NVIDIA runtime 现状；⑥ 实际交付镜像及 Compose 文件、架构和版本。还需现场确认既有容器工作正常以及离线业务镜像已准备齐全；本次没有 SSH 或现场验证证据。

## 检测与缺失分支

1. `uname -m` 应为 `x86_64`；确认 `cat /etc/os-release`、`cat /etc/kylin-release` 中实际 SP / Build，不以内核字符串推断。记录 cgroup 和服务状态，不改动现有运行环境。
2. 逐项查看采集结果的 `MISSING` 和失败信息。`python3 -c 'import hashlib,json,ssl,tarfile; print("OK")'` 须通过，且 Python 版本应满足所交付实际脚本；仅有解释器不等于模块齐全。`openssl version`、`tar --version`、`sha256sum --version` 应分别成功。若缺失，优先复用同版本系统已有工具；否则按下一节从同版麒麟授权源补齐所缺包及依赖，在克隆环境验收后交付。不要在生产机覆盖系统 Python、glibc 或强制安装。
3. 检查 `docker version` 两端、`docker info` 和现场业务状态。可正常运行的原 Docker 直接复用；不存在、服务不可访问或 API 不兼容时，记录原因，先解决权限或在同版克隆机评估，不能用本目录 CentOS RPM 直接替换现有 Docker/containerd/runc。
4. 优先使用现有 `docker compose version`。若无插件，可在独立介质解包目录直接运行随包二进制，避免覆盖现有插件：

   ```bash
   COMPOSE=/介质/kylin-v10-system-rpm-candidates-20261116/centos7/nginx-litellm-langfuse/host/compose/docker-compose
   chmod u+x "$COMPOSE"   # 仅修改该介质副本的执行位
   "$COMPOSE" version
   "$COMPOSE" -f /现场/实际/docker-compose.yml config -q
   ```

   `config -q` 可能需要现场私有 `.env` 和必要变量；不要打印展开后的配置。版本命令与配置解析通过仍不等于目标 Docker API 和所有容器已通过验收。若版本命令不能运行或配置解析不兼容，保留现状并选择经实际环境验证的 Compose 构建。
5. 已有 GPU runtime 与 Strata 直接复用。本次网关监控容器如果仅通过网络访问既有 Strata，则不需要给网关容器安装或重新配置 NVIDIA Toolkit。是否有其他监控组件需要直接访问 GPU，由其实际部署方式决定；不要执行 `nvidia-ctk runtime configure` 或重启 Docker 来完成本次检查。
6. 在受控断网条件下核验交付包哈希和镜像；现场按现有部署手册运行本地镜像及 `--pull never --no-build` 的 Compose，再验证网关、监控采集、Trace、内网 Strata 请求及重启恢复。不要把当前工具检查当作业务容器验收。

## 仅在宿主工具缺失时准备 RPM

优先取得与目标 **产品、SP、Build、架构、补丁和已装 RPM 状态相匹配**的官方安装 ISO、补丁源或经授权内部镜像，确认来源、签名、有效期和权限。麒麟官方 [系统管理员手册](https://kylinos.cn/upload/product/20241209/5fa0ad37e49171b69f3af2a0ea6849d2.pdf)提及利用系统光盘建立本地源；镜像获取应向麒麟或设备供应商确认。本机没有取得目标同版 ISO/软件源或证实其可达。不要把未知 SP 的第三方 URL 当成指定源。

在与目标匹配的**联网准备克隆机**，先按现场缺失项确定真实 RPM 包名与精确版本，建立纯文本 `top-level.txt`，每行一个包规格、不得混入其他容器运行时。确认 `dnf download` 插件或 `yumdownloader` 已在准备机存在，明确允许使用的仓库 ID，然后执行二选一：

```bash
TOOLS=/实际离线包解压位置/llm-handoff/manual/ops
bash "$TOOLS/kylin-download-rpms.sh" dnf /srv/kylin-rpms top-level.txt KYLIN_BASE_REPO_ID KYLIN_UPDATE_REPO_ID
# 或：bash "$TOOLS/kylin-download-rpms.sh" yum /srv/kylin-rpms top-level.txt KYLIN_BASE_REPO_ID KYLIN_UPDATE_REPO_ID
```

替换仓库 ID 为实际经授权的同版源，目录必须为空；脚本只下载、生成 NEVRA 和 SHA-256，不安装。`--resolve --alldeps` 尽力包含已安装依赖，但受仓库元数据、弱依赖、冲突和准备机状态影响，**下载成功不等于闭包完整**。记录仓库身份及元数据快照，但不复制可能含令牌的 `.repo` 内容。签名公钥须通过独立可信渠道核对。

将选定事务所需全部 RPM 放入一个独立目录，按实际来源建立 `SHA256SUMS`。在与目标初始 RPM 数据库一致的隔离麒麟克隆机离线运行：

```bash
bash /实际离线包解压位置/llm-handoff/manual/ops/kylin-rpm-offline-check.sh /介质/selected-rpms
```

脚本读取哈希、NEVRA、RPM 签名状态及依赖，运行 `rpm -Uvh --test`，不安装软件；必须人工审查 `NOKEY`、无签名、冲突、降级与 RPM 脚本需求。NVIDIA 候选可另用已验签仓库元数据建立来源证据；不要忽略签名缺口。测试使用该克隆机当前 RPM 数据库，不能证明另一台机器；测试成功也不证明真实安装、脚本行为或运行时功能。按现场变更流程在同版隔离克隆机完成真实无网安装、工具执行和业务验证后，才能认定该特定初始状态的闭包。不要使用 `--nodeps`、`--force` 掩盖缺失。

## 未完成项目

- 目标 SP / Build、宿主工具、现有 Docker 与 Compose 的实际信息和现场运行结果。
- 同版麒麟官方源或安装 ISO 的授权获取、签名及可达性；针对实际缺失项解析 RPM 和无网克隆安装验证。
- 所选 Compose 与实际 Docker API、完整应用镜像的兼容性和断网业务验收。
- NVIDIA 候选的目标兼容性及依赖闭包；本次复用路径不要求安装这些候选。
