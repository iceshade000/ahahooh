# Ahahooh - Claude Code 记忆增强工具

Ahahooh 让 Claude Code 拥有跨会话的持久记忆。对话摘要、计划和执行记录自动保存，新会话可一键恢复上下文。

## 工作原理

```
三层自动化架构：

1. Hooks（自动）    PostToolUse → 自动捕获 Write/Edit/Bash → 保存执行记录
                   Stop → 同步会话历史 + 补捕计划文件 → 重建压缩索引
2. CLAUDE.md（主动） 指令引导 Claude 调用 MCP 工具 → 保存对话摘要和计划
3. MCP Server（工具） 提供 save/search/resume 工具给 Claude Code 调用
```

数据全部存储在项目目录下的 `.ahahooh/` 中，基于 SQLite + FTS5 全文搜索，无需外部服务。

### 设计原则

- **零干扰**：Hook 全局异常捕获，任何错误只写 stderr，绝不阻塞 Claude Code 操作
- **轻量高效**：SQLite/FTS 存摘要（省 token），Markdown 文件存完整内容（可追溯）
- **智能降噪**：自动跳过低价值命令（git status、ls 等），连续同目标操作去重
- **精准恢复**：支持按关键词/计划聚焦恢复，动态字符上限（最大 3000 字符）

## 安装

### 前置要求

- Python >= 3.10
- Claude Code CLI

### 安装步骤

```bash
# 进入项目目录
cd /path/to/ahahooh

# 用 pip 安装（可编辑模式）
pip install -e .
```

安装完成后会注册 `ahahooh` 命令行工具。

## 在项目中启用

在你的项目根目录运行：

```bash
ahahooh init
```

初始化完成后会自动启动 Claude Code，无需手动运行 `claude` 命令。

> **性能修复**：`ahahooh init` 会自动在 `.claude/settings.local.json` 中设置 `CLAUDE_CODE_ATTRIBUTION_HEADER=0`，修复 Claude Code 的缓存 hash bug，避免使用 API Key 时生成速度暴跌的问题。

这会生成以下文件：

```
<你的项目>/
├── .ahahooh/
│   └── data/
│       ├── db.sqlite              # 元数据 + FTS5 全文搜索
│       ├── records/               # 执行记录（自动生成）
│       ├── conversations/         # 对话摘要（Claude 主动保存）
│       ├── plans/                 # 计划文件（Claude 主动保存）
│       └── index.md               # 压缩索引（自动/手动重建）
├── .claude/
│   ├── CLAUDE.md                  # 追加了 ahahooh 行为指令
│   ├── settings.local.json        # Hook 配置
│   └── commands/                  # 斜杠命令
│       ├── aharesume.md
│       ├── save-conversation.md
│       ├── save-plan.md
│       └── search-memory.md
└── .mcp.json                      # MCP Server 注册
```

## 使用方式

### 自动捕获

`ahahooh init` 配置了 Claude Code 的 PostToolUse Hook。启动 Claude Code 后，所有 Write、Edit、Bash 操作会被自动记录到 `.ahahooh/data/records/`，无需任何手动操作。

**Plan Mode 自动捕获**：当使用 Claude Code 的 `/plan` 模式（Shift+Tab）编写计划时，ahahooh 会自动检测对 `~/.claude/plans/` 的写入，提取计划目标和任务列表，保存为独立计划。同一份计划多次编辑会自动更新，不会重复创建。由于 plan mode 内部写入计划文件时绕过了 Write tool，PostToolUse hook 可能无法触发，因此 Stop hook 和 `get_resume_context` 也会扫描 `~/.claude/plans/`，补捕遗漏的计划文件。

**会话历史自动同步**：Claude Code 本身会将完整的会话历史保存在 `~/.claude/projects/<slug>/<session-id>.jsonl`。ahahooh 在 Stop hook 中自动解析这些 JSONL 文件，保留所有用户消息，提取每条助手回复的末尾部分（基于规则，不消耗 token），合并生成摘要，同步到 `.ahahooh/data/conversations/`。已同步的会话不会重复处理。即使关闭终端窗口导致 Stop hook 未触发，下次 `/aharesume` 时也会自动同步新会话。

### MCP 工具

Claude Code 启动时会自动加载 `.mcp.json` 中注册的 MCP Server，获得以下工具：

| 工具 | 用途 |
|------|------|
| `save_conversation(summary, key_decisions, topics)` | 保存对话摘要 |
| `save_plan(goal, tasks, plan_id?)` | 保存或更新计划 |
| `update_plan_task(plan_id, task_index, status)` | 更新计划中的任务状态 |
| `search_memory(query, type?, limit?)` | 搜索历史记忆（FTS5 全文搜索，无结果时自动 OR 回退） |
| `list_plans()` | 列出所有计划及完成状态 |
| `get_resume_context(focus?)` | 获取上下文用于恢复会话，可选 focus 参数按关键词/计划聚焦 |

CLAUDE.md 中的指令会引导 Claude 在合适时机主动调用这些工具。

### 斜杠命令

在 Claude Code 中可以直接使用：

- `/aharesume` — 恢复上一次会话的上下文
- `/aharesume <关键词或计划ID>` — 按关键词/计划聚焦恢复
- `/save-conversation` — 手动触发保存对话摘要
- `/save-plan` — 手动触发保存计划
- `/search-memory` — 搜索历史记忆

### CLI 命令

```bash
ahahooh init       # 在当前项目初始化
ahahooh status     # 查看记忆状态
ahahooh compress   # 手动重建压缩索引
ahahooh serve      # 启动 MCP Server（通常不需要手动运行）
```

## 典型工作流

```
1. 在项目目录运行 ahahooh init（自动启动 Claude Code）
2. Hook 自动记录每次 Write/Edit/Bash 操作
3. Claude 在关键决策点自动调用 save_conversation
4. Claude 在形成计划时自动调用 save_plan
5. 会话结束时，Stop hook 自动同步历史会话并重建索引
6. 下次运行 ahahooh init 或 claude，输入 /aharesume 恢复上下文
```

## 会话结束行为

- **Ctrl+C 两次退出**：Stop hook 正常触发，自动同步新会话并重建索引。
- **直接关闭终端窗口**：Stop hook 不会触发，索引可能不是最新。但下次 `/aharesume` 时 `get_resume_context` 会自动同步新会话并重建索引，不会丢失数据。执行记录在每次操作时已实时写入，不受影响。

## 查看状态

```bash
$ ahahooh status

Project root: D:\my-project
Database: 12 records, 3 conversations, 1 plans
Index: 1024 bytes
MCP config: configured
Hooks: configured
```

## 数据说明

- 所有数据存储在项目目录的 `.ahahooh/data/` 中，纯本地，不上传
- 数据库为 SQLite，记录文件为 Markdown，可用任何工具查看
- FTS5 支持全文搜索，使用 unicode61 tokenizer 支持中英文，无结果时自动拆词 OR 回退
- 记忆文件为不可变记录，不会被修改
- 执行记录双轨存储：SQLite 存摘要用于快速检索，Markdown 文件存完整内容用于回溯
- Hook 执行有超时保护（PostToolUse 快速失败，Stop hook 10 秒超时）

## 卸载

删除以下内容即可完全清除：

```bash
rm -rf .ahahooh
rm .mcp.json
# 并清理 .claude/ 中的 ahahooh 相关配置
```

## 项目结构

```
ahahooh/
├── pyproject.toml
├── src/ahahooh/
│   ├── cli.py            # CLI 命令
│   ├── server.py         # MCP Server（6 个工具）
│   ├── storage.py        # SQLite 存储层
│   ├── models.py         # 数据模型
│   ├── index.py          # 索引生成
│   ├── config.py         # 路径常量
│   ├── session_sync.py   # 会话历史同步
│   ├── plan_sync.py      # 计划文件同步（补捕 plan mode 遗漏）
│   └── hook_handler.py   # Hook 处理入口
└── templates/
    ├── claude_md_fragment.md
    └── commands/          # 斜杠命令模板
```
