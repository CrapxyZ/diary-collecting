# Agent Worklog

A local Coding Agent skill for keeping compact session logs and writing a daily brief from user-selected sources. The Agent interprets conversation context. A Python CLI validates and stores the result.

## Install and use

You need Python 3.9+ on Linux, macOS, or WSL and a local filesystem. Git is optional at runtime.

For Codex, run these commands from the root of a checkout or extracted archive:

```sh
mkdir -p ~/.agents/skills/agent-worklog
cp -R SKILL.md scripts references ~/.agents/skills/agent-worklog/
```

Open a Codex session and invoke `$agent-worklog`. For another agent that supports local commands, load [SKILL.md](SKILL.md) and keep its scripts and references beside it.

Examples for Codex CLI:

- Use $agent-worklog to start a log named api-debug with key api.
- Use $agent-worklog to sync the api log with today's progress.
- Use $agent-worklog to end and freeze the api log.
- Use $agent-worklog to list brief candidates for 2026-11-27. When prompted, select session keys, files, or all candidates.
- Use $agent-worklog to make the 2026-11-27 brief using only the api and tests keys.

Natural language works when the host discovers the skill. The /wl start, /wl update, /wl end, and /wl brief forms are text aliases when passed through to the Agent.

Different Git worktrees have different active scopes. Use a distinct session key for each independent conversation sharing a worktree. Outside Git, use a consistent working directory. A session is archived by its local start date, and can continue across midnight. To include a prior-date session in today's brief, select its file explicitly. If the chat history no longer exposes the last sync boundary, supply the missing events or the specific delta you want recorded.

## Local data and development

Sessions default to ~/.agent-worklog/sessions/, and briefs to ~/.agent-worklog/briefs/. Set WORKLOG_DIR to a writable directory to override session storage. In that case briefs go into its briefs/ subdirectory. An agent sandbox may require a directory within its existing write permissions. Keep live data outside this repository and omit credentials or sensitive raw output from your worklogs.

The Markdown logs own session state. The local .state.json cache can be rebuilt by status after interruption. When a CLI operation reports a partial auto-end failure, inspect status before starting again. See the [format and CLI reference](references/ir_schema.md) for command input and recovery details.

Run the tests from this directory with:

    python3 -m unittest discover -s tests -v
