# Agent Worklog · 给 Codex 加一本工作日记

简体中文 · [English](README_en.md)

在 Codex 里忙了一下午，晚上却想不起改了什么、跑了哪些测试？用 Agent Worklog，你可以让 Codex 把进展记在本地，收工时再把你选中的几份日志整理成一份简报。

你在 Codex 对话框里说「开始记」「把刚才的进展记下来」「帮我整理今天的简报」就行。开档后，想写入新的进展时记得告诉 Codex。

## 第一步：安装一次

准备好 Codex 和 Python 3.9 或更高版本。在 Linux、macOS 或 Windows 的 WSL 终端里运行 `python3 --version`，可以查看版本。

1. [下载 ZIP](https://github.com/CrapxyZ/diary-log/archive/refs/heads/main.zip) 并解压。已经用 Git 克隆过的话，直接使用克隆的文件夹。
2. 在终端里进入解压后的 `diary-log-main` 文件夹。运行 `ls`，看到 `SKILL.md`、`scripts` 和 `references` 就说明位置对了。
3. 把下面两行复制到**终端**里执行。它们会把 Skill 放到 Codex 能找到的位置。

```sh
mkdir -p ~/.agents/skills/agent-worklog
cp -R SKILL.md scripts references ~/.agents/skills/agent-worklog/
```

## 第二步：试着记一条日志

打开一个 Codex 对话。下面这些是要发给 **Codex 对话框**的消息，每次发一条。

1. **开始记。** 发 `$agent-worklog 开始记录今天的工作，日志叫 first-log。` 等 Codex 告诉你日志已创建。
2. **记一条进展。** 做完一件事后，发 `$agent-worklog 把刚才的进展记下来。` 如果工作是在 Codex 对话外做的，就顺手告诉它结果，例如「我修好了登录按钮，跑了 3 项测试，都通过了」。
3. **结束记录。** 发 `$agent-worklog 结束这份日志。` Codex 会把这份日志封存。以后想继续记，可以再开一份。

## 第三步：生成第一份简报

发 `$agent-worklog 帮我整理今天的简报。` Codex 会先列出今天开启的日志。看到列表后，回复「只用 first-log」或「用今天列出的全部日志」。

Codex 只会读取你选中的日志正文。简报会显示在对话里，同时存成一个 Markdown 文件。如果只想在对话里看，可以说「只显示，不用保存」。

## 常见问题

**日志和简报存在哪里？** 默认都在本机的 `~/.agent-worklog/` 目录。打开 `sessions/` 看原始日志，打开 `briefs/` 看简报。

**我同时开了多个 Codex 对话，怎么分开记？** 如果它们用的是同一份项目目录，开档时给每个对话一个不同的会话键，例如 `$agent-worklog 开始记测试日志，会话键用 tests。` 之后更新、结束或选简报来源时，用同一个键。

**昨天开的日志，今天还能用吗？** 可以继续更新。自动列出的候选按日志的开启日期查找，所以做今天的简报时，可以把昨天日志的文件路径一并发给 Codex。

**Codex 找不到 Skill 或写不了文件怎么办？** 安装后若找不到 Skill，先重新打开 Codex，再检查 `~/.agents/skills/agent-worklog/` 里是否有 `SKILL.md`、`scripts/` 和 `references/`。如果提示没有写入权限，可以在启动 Codex 的环境里设置 `WORKLOG_DIR`，指向一个它可写的目录。

更多细节见 [Skill 说明](SKILL.md)、[日志格式和 CLI](references/ir_schema.md) 与 [简报模板](references/brief_template.md)。开发测试可在仓库根目录运行 `python3 -m unittest discover -s tests -v`。
