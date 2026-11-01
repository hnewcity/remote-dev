<div align="center">

# Remote Dev

**让编程 Agent 用上你已有的远程算力。**

本地 CPU、内存或磁盘吃紧时，把开发任务分配给经过你授权的 SSH 主机。

[![Tests](https://github.com/hnewcity/remote-dev/actions/workflows/tests.yml/badge.svg)](https://github.com/hnewcity/remote-dev/actions/workflows/tests.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](#快速开始)
[![MIT](https://img.shields.io/badge/License-MIT-green.svg)](../LICENSE)
[![Hosts: opt-in](https://img.shields.io/badge/Hosts-opt--in-6F42C1)](#权限与隐私)

[![English](https://img.shields.io/badge/English-555555?style=for-the-badge)](../README.md)
[![简体中文](https://img.shields.io/badge/简体中文-selected-1f6feb?style=for-the-badge)](README.zh-CN.md)

[快速开始](#快速开始) · [安装为 Skill](#安装为-skill) · [使用示例](#使用示例) · [工作原理](#工作原理)

</div>

## 为什么需要它

同时运行几个编程 Agent，会让笔记本的内存、CPU 和依赖缓存迅速吃紧。你可能还有一台 Linux 主机、闲置 Mac 或虚拟机，但仅有 SSH 地址，并不能告诉 Agent 这台机器是否适合、是否允许执行当前任务。

Remote Dev 提供引导式配置和可复用的主机清单。由你决定哪些机器可以接收项目源码、可以执行哪些任务，再让 Agent 检查兼容性和实时资源，发送代码快照、执行命令并取回结果。

它分担的是**工具执行**：构建、测试、基准测试和分析脚本。它不会加快远程模型本身的推理。如果同步成本高于收益，简单检查仍可留在本地。

> 默认不启用任何主机。发现 SSH 别名或成功登录，只说明存在候选机器，不代表获得了开发任务执行授权。

## 你会得到什么

- **引导式设置：** 扫描 SSH Config 中的明确别名，检查候选主机，记住允许和禁止使用的机器。
- **英文 JSON 配置：** 保存连接信息、任务用途、源码传输权限、限制和资源门槛。
- **任务选机：** 按系统、架构、工具、可用内存与磁盘筛选，识别同一机器的多个别名。
- **当前代码快照：** 包含未提交修改和未被 Git 忽略的新文件，不 stash、不重置本地工作区。
- **受管理的执行过程：** 独立任务目录、共享并发槽位、超时处理、日志和明确退出码。
- **结果取回与清理：** 将指定产物下载到新的本地目录，再删除本工具创建的已完成任务。
- **Skill 和 CLI 两种方式：** 标准 `SKILL.md` 加 Python 标准库脚本，无需安装远程常驻服务。

Agent 仍需判断运行时版本、项目要求、数据限制和传输成本。脚本能检查工具是否存在，但不会自动推断所有项目的兼容性要求。

## 快速开始

发起端需要 **macOS 或 Linux、Python 3.9+、OpenSSH 和 Git**。目标主机也需要 macOS/Linux、Python 3.9+，以及任务要求的工具。先配置并验证正常的 SSH 访问。

```bash
git clone https://github.com/hnewcity/remote-dev.git
cd remote-dev
python3 skills/remote-dev/scripts/remote_dev.py --help
python3 skills/remote-dev/scripts/remote_dev.py init
python3 skills/remote-dev/scripts/remote_dev.py discover
```

`init` 在 `~/.config/agent-remote/hosts.json` 创建权限为 `0600` 的空配置，保留已有配置。`discover` 读取明确别名及 `Include` 文件，通过 `ssh -G` 解析有效配置，检查密钥路径是否存在。它不会连接目标、扫描网段或查找命令历史。

安装 Skill 后，可以直接告诉 Agent：

> 用 remote-dev 帮我选择哪些 SSH 主机可以作为开发环境。在我选择各台机器的权限前，保持全部禁用。

## 安装为 Skill

### 方式一：让 Agent 帮你安装

把下面这段话复制给有终端和文件访问能力的 Codex、Claude Code 或其他 Agent：

```text
请帮我安装这个 Skill：https://github.com/hnewcity/remote-dev 。根据 README，将完整的 skills/remote-dev 文件夹（包括 scripts/、references/、assets/ 和 agents/）安装到我当前 Agent 的用户级 skills 目录。保留已有本地修改和主机权限。安装后，用 Python 3.9+ 运行安装目录中的 scripts/remote_dev.py --help 验证。安装期间不要启用任何主机或在主机上执行任务，并告诉我安装路径。
```

### 方式二：使用 curl 安装

需要 `curl` 和 `tar`。下面默认使用 Codex 的用户级目录；Claude Code 用户将 `skill_dir` 改成 `"$HOME/.claude/skills"`。部分已有 Codex 安装使用 `~/.codex/skills`，请沿用当前 Agent 能发现的目录，避免重复安装。

命令下载 `main` 分支，只提取 Skill 文件夹。重复安装会覆盖同名技能文件，请先保留本地修改。独立的主机配置不在源码包内。

```bash
skill_dir="$HOME/.agents/skills"
mkdir -p "$skill_dir"
curl -fsSL https://github.com/hnewcity/remote-dev/archive/refs/heads/main.tar.gz |
  tar -xz -C "$skill_dir" --strip-components=2 remote-dev-main/skills/remote-dev
python3 "$skill_dir/remote-dev/scripts/remote_dev.py" --help
```

### 方式三：从 Git 克隆目录复制

将 [`skills/remote-dev`](../skills/remote-dev) **整个文件夹**复制到对应位置：

| Agent | 用户级目录 | 项目级目录 |
| --- | --- | --- |
| Codex | `~/.agents/skills/remote-dev/` | `.agents/skills/remote-dev/` |
| Claude Code | `~/.claude/skills/remote-dev/` | `.claude/skills/remote-dev/` |

```bash
# Codex；当前安装使用其他发现目录时，请沿用它。
mkdir -p ~/.agents/skills
cp -R skills/remote-dev ~/.agents/skills/

# 或者 Claude Code。
mkdir -p ~/.claude/skills
cp -R skills/remote-dev ~/.claude/skills/
```

发现规则见 [Codex 文档](https://developers.openai.com/codex/skills/)和 [Claude Code 文档](https://code.claude.com/docs/en/skills)。核心脚本不依赖两者的专有 API；`agents/openai.yaml` 仅提供可选的 Codex 界面元数据。Agent 应用自身的权限和网络访问限制仍然生效，安装 Skill 不会自动把云端 Agent 接入本机网络。

## 使用示例

以下 SSH 别名均为虚构示例。命令从仓库根目录运行，也可替换为已安装脚本的路径。

### 1. 检查候选主机并选择权限

```bash
python3 skills/remote-dev/scripts/remote_dev.py probe --host linux-builder

# 只在用户明确选择这些权限后记录。
python3 skills/remote-dev/scripts/remote_dev.py allow \
  --host linux-builder --tasks build,test --source-sync \
  --max-jobs 1 --memory-gib 2 --disk-gib 10 \
  --constraints '仅限个人项目，不接收客户数据'
python3 skills/remote-dev/scripts/remote_dev.py deny \
  --host production --reason '生产主机'
```

探测只读取系统信息，不安装软件、不写入远程文件。首次遇到或发生变化的 SSH 主机指纹，需要通过正常的 SSH 流程核验；工具不会自动接受。

### 2. 为任务选择环境

```bash
python3 skills/remote-dev/scripts/remote_dev.py select \
  --task test --os Linux --arch x86_64 --tools python3 \
  --memory-gib 4 --disk-gib 12
```

只检查已启用的主机，返回候选列表或具体拒绝原因，不会立即执行任务。内存、磁盘数值是任务启动前的门槛，并不是硬性配额。虚拟机按分配的逻辑 CPU 数量判断，不能把显示的 CPU 型号当作可独占的整颗处理器。

在会话里也可以直接说：

> 用 remote-dev 为当前项目的测试选择合适的已授权主机。检查兼容性，适合的话就远程执行，并把测试结果取回来。

### 3. 生成快照、运行和取回结果

```bash
python3 skills/remote-dev/scripts/remote_dev.py snapshot \
  --repo /path/to/project --output /path/to/task/input.tar.gz
```

传输前检查 `input.tar.gz.manifest.json`。将实际任务写入本地 `commands.sh`，例如：

```sh
python3 -m unittest discover -s tests -v
```

目标主机应已具备所需工具，或已保存的权限允许为项目安装局部依赖。然后执行：

```bash
python3 skills/remote-dev/scripts/remote_dev.py run \
  --host linux-builder --task test --os Linux --arch x86_64 --tools python3 \
  --memory-gib 4 --disk-gib 12 --jobs 2 --timeout 1200 \
  --snapshot /path/to/task/input.tar.gz --command-file /path/to/task/commands.sh

# JOB_ID 替换为运行时返回的任务标识。
python3 skills/remote-dev/scripts/remote_dev.py fetch \
  --host linux-builder --job JOB_ID --paths stdout.log,stderr.log \
  --output /path/to/task/results
python3 skills/remote-dev/scripts/remote_dev.py cleanup \
  --host linux-builder --job JOB_ID
```

结果包含任务状态、退出码和日志末尾。测试失败会如实保留。SSH 连接中断时，先运行 `status --host linux-builder --job JOB_ID`，确认任务是否仍在运行，再决定是否重试。

## 工作原理

1. **发现和授权。** SSH 别名只是候选；用户的允许、禁止决定与探测到的硬件信息分开保存。
2. **匹配任务。** 检查系统、架构、工具路径、可用内存和磁盘、并发槽位及用户限制；旧硬件信息仅作为参考。
3. **捕获当前代码。** 使用 Git 提供的已跟踪和未忽略路径，保留当前文件内容和删除状态，不改动源工作区。
4. **提交前复核。** 重新读取权限、检查 SSH 连接绑定并核验快照摘要。刷新操作不能恢复刷新期间被撤销的授权。
5. **执行并返回。** 通过 SSH 临时发送 Python worker，预留并发槽位、创建有归属标记的任务目录、验证快照，使用 `/bin/sh -eu` 运行并记录结果。不会安装常驻服务。
6. **按任务清理。** 只取回指定文件到新的本地目录，只清理归属标记匹配且已完成的任务。

## 配置与命令

初始配置为：

```json
{
  "version": 1,
  "hosts": []
}
```

每台主机记录 `alias`、`enabled`、`decision`、`allowed_tasks`、`allow_source_sync`、`constraints`、`workspace_root`、`limits`、`connection`、`facts` 和 `approved_at`。详见[配置字段说明](../skills/remote-dev/references/registry.md)和[引导流程](../skills/remote-dev/references/setup.md)。

可以使用 `AGENT_REMOTE_CONFIG`，或将 `--config /path/to/hosts.json` 放在**子命令之前**，指定独立配置。不要把授权配置放进源代码仓库。

| 命令 | 用途 |
| --- | --- |
| `init`、`list` | 创建空配置或查看已保存状态 |
| `discover` | 读取明确的 SSH 别名，不连接主机 |
| `probe --host ALIAS` | 检查候选机器能力，不授予权限 |
| `allow`、`deny` | 记录用户明确的允许或禁止决定 |
| `refresh --host ALIAS` | 更新已启用主机的信息，不改变权限 |
| `select --task build\|test\|analysis` | 筛选适合任务的已授权主机 |
| `snapshot` | 生成当前 Git 工作区快照和清单 |
| `run` | 提交已获准的前台任务 |
| `status`、`fetch`、`cleanup` | 查询状态、取回指定文件、清理已完成任务 |

CLI 输出 JSON。成功的工具操作退出码为 `0`；远程命令非零退出或超时，`run` 返回 `1`；配置、传输或校验错误返回 `2`。没有可用候选时，会报告 `remote_execution_possible: false`。

## 权限与隐私

项目源码**会发送到你授权的目标主机**。SSH 私钥留在发起端，不转发 SSH Agent，对配置的跳板连接也保持严格的主机密钥校验。

默认快照排除 Git 内部目录、常见凭据文件、依赖缓存、符号链接和包含私钥头的文件。这不是完整的秘密扫描器，需要结合实际项目审阅清单和数据。仓库内容不能替用户授予主机权限。

个人主机配置、快照、运行产物和密钥不属于公开 Skill。仓库中的配置模板为空；示例和测试仅使用虚构主机及临时本地数据。

## 限制

- 当前支持 macOS/Linux，不支持 Windows 发起端或目标主机。
- 用于前台开发任务，不提供生产运维、部署流程、守护进程管理或长期后台服务。
- 它不是操作系统沙箱，命令拥有 SSH 账户本身的权限。不可信代码需要另行配置受限账户、容器或虚拟机。
- 资源门槛不是硬性配额；并发槽位只协调相同工作目录下本工具管理的任务。外部进程仍会占用资源，工具也可能忽略并发环境变量。
- 源码快照和产物下载各限制为 64 MiB，快照最多 50,000 个普通文件。子模块、未实体化的 Git LFS 文件需要明确的传输方案；符号链接默认略过。
- 不会自动转换平台。依赖、原生二进制、SDK 版本、GPU 和设备需按任务检查，发现阶段不会自动安装缺失工具。
- 缺失、无效、空白或禁用的配置不能执行远程任务。SSH 连接绑定发生变化后需要重新授权，未知指纹不会被绕过。

## 开发与许可

```bash
python3 -m unittest discover -s skills/remote-dev/scripts -p 'test_*.py' -v
python3 skills/remote-dev/scripts/remote_dev.py --help
```

测试完全离线运行，使用临时本地仓库和工作目录，覆盖权限检查、预检中撤销授权、代码快照、压缩包校验、超时、并发冲突、产物取回和清理，不会连接真实 SSH 主机。GitHub Actions 在 Linux 和 macOS 上运行测试。

贡献前请阅读 [CONTRIBUTING.md](../CONTRIBUTING.md)。如果你想判断换一台更快的本地电脑是否有帮助，可以参考 [Agent Speedup Estimator](https://github.com/hnewcity/agent-speedup-estimator)。

代码采用 [MIT 许可证](../LICENSE)。© 2026 hnewcity。本项目独立开发，与 OpenAI、Anthropic 无隶属关系，产品名称归各自所有者所有。
