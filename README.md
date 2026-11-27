# Agent Worklog

简体中文 · [English](README_en.md)

面向 Coding Agent 的本地工作日志 Skill，支持会话内进展记录、历史条目修订，以及跨会话的每日简报生成。已在真实 Codex 会话中完成生命周期与简报来源隔离验收。

Agent 负责整理语义和撰写简报，Python CLI 负责状态管理、格式校验与文件读写。运行仅依赖 Python 标准库。

## 功能

- **会话日志**：记录工作进展、指标变化、排查过程和关键决策。
- **原位修订**：根据新进展更新历史条目的状态和结论，保留简短修订痕迹。
- **上下文隔离**：以同步锚点和前序会话排除列表约束记录范围，支持同一对话多次开档。
- **并行记录**：按 worktree 和 `session_key` 区分活跃会话。
- **每日简报**：只读取用户选中的日志，按项目汇总、去重并输出 Markdown。

## 安装

需要 Python 3.9+，支持 Linux、macOS 和 WSL 的本地文件系统。Git 可用于识别项目和 worktree。

克隆仓库后，将 Skill 安装到 Codex 的用户目录。

```sh
git clone https://github.com/CrapxyZ/diary-log.git
cd diary-log
mkdir -p ~/.agents/skills/agent-worklog
cp -R SKILL.md scripts references ~/.agents/skills/agent-worklog/
```

也可下载仓库 ZIP，解压后在仓库根目录执行最后两行命令。其他支持本地命令的 Agent 可加载 [SKILL.md](SKILL.md)，并保留相邻的脚本与参考文档。

## 使用

在 Codex 对话中通过 `$agent-worklog` 调用。以下示例使用同一个会话键 `api`。

| 操作 | 示例 |
| --- | --- |
| 开始记录 | `$agent-worklog 开始记录接口调试，日志名 api-debug，会话键 api。` |
| 同步进展 | `$agent-worklog 将刚才的进展同步到 api 日志。` |
| 结束记录 | `$agent-worklog 结束并冻结 api 日志。` |
| 查看简报来源 | `$agent-worklog 列出今天的简报候选。` |
| 生成简报 | `$agent-worklog 生成今天的简报，只使用 api 和 tests 两个会话键的日志。` |

开档后，在需要保存进展时显式请求同步。日志从开档请求开始记录。冻结后再次开档会创建新文件。

简报未指定来源时，Agent 会先列出候选，等待选择会话键、文件路径或全部候选后再读取正文。简报默认同时显示在对话中并保存到本地，也可要求只显示。

自然语言请求同样可以触发 Skill。`/wl start`、`/wl update`、`/wl end` 和 `/wl brief` 可作为文本别名使用，前提是客户端将这些文本传递给 Agent。

## 存储与会话边界

| 内容 | 默认路径 |
| --- | --- |
| 会话日志 | `~/.agent-worklog/sessions/` |
| 每日简报 | `~/.agent-worklog/briefs/` |

可通过环境变量 `WORKLOG_DIR` 指定会话目录。覆盖后，简报存入该目录下的 `briefs/`。Agent 需要对存储目录有写入权限。

不同 worktree 默认独立。同一 worktree 中的并行对话应使用不同的 `session_key`，后续更新和结束操作沿用原键。Git 仓库外的会话以工作目录区分。

日志按启动日归档，跨午夜可继续更新。要将前一天启动的日志纳入当天简报，请显式选择其文件路径。若上下文压缩或对话切换导致同步锚点不可定位，Agent 会请求补充缺失区间或明确的增量内容。

## 开发与参考

```sh
python3 -m unittest discover -s tests -v
```

- [Skill 操作说明](SKILL.md)
- [日志格式与 CLI 接口](references/ir_schema.md)
- [简报模板与来源选择规则](references/brief_template.md)
