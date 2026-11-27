# Agent Worklog · 工作日志与每日简报

简体中文 · [English](README_en.md)

Agent Worklog 是一个在本地运行的 Coding Agent Skill。它能把对话中的工作进展写入简洁的会话日志，再从你选中的日志生成每日简报。Agent 负责理解上下文，Python CLI 负责校验和保存文件。

## 安装与使用

需要 Python 3.9+ 和本地文件系统，支持 Linux、macOS 和 WSL。运行时 Git 可选。

下载或克隆本仓库后，在仓库根目录执行以下命令，将 Skill 安装到 Codex 的用户目录。

```sh
mkdir -p ~/.agents/skills/agent-worklog
cp -R SKILL.md scripts references ~/.agents/skills/agent-worklog/
```

打开 Codex 会话，输入 `$agent-worklog` 并描述要执行的操作。如果使用其他能运行本地命令的 Agent，请加载 [SKILL.md](SKILL.md)，并保留同目录下的 `scripts/` 和 `references/`。

### 使用示例

- `$agent-worklog 为当前任务开一个名为 api-debug 的日志，session_key 用 api。`
- `$agent-worklog 把刚才的进展同步到 api 日志。`
- `$agent-worklog 结束并冻结 api 日志。`
- `$agent-worklog 列出今天的简报候选。` 收到候选后，再选择会话键、文件或全部候选。
- `$agent-worklog 生成今天的简报，只使用 api 和 tests 两个会话键的日志。`

支持自然语言触发。客户端若能将 `/wl start`、`/wl update`、`/wl end`、`/wl brief` 作为文本交给 Agent，也可以使用这些简写。

每个 Git worktree 有独立的活跃日志。同一个 worktree 有多个独立对话时，为它们指定不同的 `session_key`。在 Git 仓库外，请在同一个工作目录中执行后续操作。日志按本地启动日期归档，跨午夜可以继续更新。若要把前一天启动的会话纳入当天简报，请显式选择该文件。对话里找不到上次同步的位置时，提供缺失区间或你想补记的具体进展。

## 日志位置与开发

会话日志默认保存到 `~/.agent-worklog/sessions/`，简报默认保存到 `~/.agent-worklog/briefs/`。设置 `WORKLOG_DIR` 可以指定可写的会话目录，此时简报保存到该目录下的 `briefs/`。如果 Agent 的沙盒限制写入，请选择其可写目录。请将实际日志存放在本仓库之外，并避免写入凭据或敏感原始输出。

会话状态以 Markdown 文件为准。中断后，`status` 可以根据日志重建本地 `.state.json` 索引。如果自动结束旧会话并新开的操作报告部分失败，先用 `status` 检查当前状态。命令、数据格式和恢复步骤见 [CLI 与格式规范](references/ir_schema.md)，简报结构和选源规则见 [简报模板](references/brief_template.md)。

在仓库根目录运行测试。

```sh
python3 -m unittest discover -s tests -v
```
