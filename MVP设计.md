# Ahahooh MVP 实现计划文档

## 一、项目最终目标

**Ahahooh** 是为 Claude Code 设计的会话记忆与上下文管理系统。最终目标实现：

1. **关键信息保存**：完整保存对话、计划、代码/命令及执行结果，原始数据永不修改，确保可追溯。
2. **关键信息检索**：通过自动生成的摘要和索引，支持快速语义检索历史信息。
3. **快速恢复与任务切换**：一键恢复上次工作状态，支持按任务切换上下文。

**MVP（最小可行产品）** 聚焦基础能力，为后续迭代奠定可靠的数据基础：

- 完整录制 Claude Code 会话，保存原始数据。
- 结构化存储对话、计划、代码/命令。
- 提供 `ahahooh resume` CLI 命令和 `/resume` 斜杠命令，实现会话快速恢复。

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                       用户交互层                            │
│  ┌───────────────────┐      ┌──────────────────────────┐  │
│  │   CLI 命令        │      │   Claude Code 斜杠命令   │  │
│  │ ahahooh start    │      │   /resume                │  │
│  │ ahahooh resume   │      │                          │  │
│  └─────────┬─────────┘      └───────────┬──────────────┘  │
│            │                            │                  │
│            ▼                            ▼                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                   控制与恢复模块                     │  │
│  │  - 生成摘要文件                                      │  │
│  │  - 启动/恢复 Claude Code 会话                       │  │
│  └─────────────────────────┬───────────────────────────┘  │
└─────────────────────────────┼──────────────────────────────┘
                              │
┌─────────────────────────────┼──────────────────────────────┐
│                          核心存储层                         │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  录制器（tmux）  →  原始日志 →  解析器（离线）       │  │
│  └─────────────────────────┬───────────────────────────┘  │
│                            ▼                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  存储层（.ahahooh/）                                 │  │
│  │  ├── raw/logs/       原始终端日志                    │  │
│  │  ├── raw/code/       提取的代码文件                  │  │
│  │  ├── sessions/       会话摘要（Markdown）           │  │
│  │  ├── db.sqlite       结构化元数据                    │  │
│  │  └── session_state.json  当前会话元数据             │  │
│  └─────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

**数据流向**：
1. 用户执行 `ahahooh start` → 启动 tmux 会话，实时写入日志。
2. 用户使用 Claude Code 工作，所有输入输出被记录。
3. 会话结束后（或手动触发）运行解析器，提取对话、计划、代码/命令，存入数据库，保存代码文件，生成摘要。
4. 恢复时，CLI 或斜杠命令读取摘要，构建上下文，启动新会话或注入当前会话。

---

## 三、整体设计思路

- **原始数据优先**：所有原始内容（终端日志、代码文件）原样保存，数据库仅存元数据和指针，保证完整性和可审计性。
- **离线解析**：避免实时解析增加延迟，MVP 采用会话结束后一次性解析，简化实现。
- **结构化存储**：使用 SQLite 存储元数据，便于后续快速查询和扩展。
- **摘要独立生成**：摘要文件用于恢复，可随时重新生成，不依赖原始数据。
- **用户友好恢复**：提供两种方式：
  - CLI `ahahooh resume`：启动全新 Claude Code 会话并加载摘要。
  - Claude Code 内 `/resume`：当前会话直接读取摘要继续，无需退出。
- **模块化设计**：各模块职责清晰，便于后续迭代（如流式解析、向量检索）。

---

## 四、模块职责与实现思路

### 1. 录制器（Recorder）

**职责**：启动 tmux 会话，实时记录终端输出到日志文件。

**实现思路**：
- 使用 `tmux` 的 `pipe-pane` 将窗格输出重定向到文件。
- 生成日志文件路径：`.ahahooh/raw/logs/YYYYMMDD_HHMMSS.log`。
- 会话启动时，自动执行 `tmux new-session` 并启动 Claude Code。
- 提供 `ahahooh stop`（可选）以优雅停止录制（发送 `exit` 或 `Ctrl+D`）。

**关键命令示例**：
```bash
# 启动新会话（脚本内部）
LOG_FILE="$(pwd)/.ahahooh/raw/logs/$(date +%Y%m%d_%H%M%S).log"
tmux new-session -d -s "ahahooh_$$" \
    "claude code; echo 'exit' >&2"
tmux pipe-pane -t "ahahooh_$$" -o "cat >> $LOG_FILE"
tmux attach -t "ahahooh_$$"
```

**注意**：需要确保 `tmux` 已安装，并提供友好提示。

---

### 2. 解析器（Parser）

**职责**：解析原始日志，提取对话、计划、代码/命令，写入数据库，保存代码文件，生成摘要。

**实现思路**：
- 输入：原始日志文件路径。
- 输出：更新 SQLite 数据库，保存代码文件到 `raw/code/`，生成摘要到 `sessions/`。
- 采用**逐行扫描 + 状态机**方式识别不同内容块。

**识别规则（MVP）**：
- **对话**：通过行首特征判断角色（如 `>` 为用户输入，无特殊前缀为 Claude 输出）。连续行合并为一条消息。
- **计划**：匹配正则 `计划[：:]|任务[：:]|待办|^- \[.\]`，提取行内容作为计划条目，状态默认为 `pending`。
- **代码**：识别 Markdown 代码块（\`\`\` 开头，\`\`\` 结束），保存为文件，记录路径。
- **命令**：识别行首 `$ ` 的命令，后续非空行直到下一个 `$ ` 或提示符为输出。

**解析器运行时机**：
- 会话结束后自动触发（用户退出 tmux 时）。
- 用户也可手动执行 `ahahooh parse` 重新解析。

---

### 3. 存储层（Storage）

**职责**：管理目录结构、数据库操作、文件保存。

**目录结构**（项目根目录下的 `.ahahooh/`）：
```
.ahahooh/
├── raw/
│   ├── logs/
│   │   └── 2026-03-28_14-30-00.log
│   └── code/
│       └── 2026-03-28_14-30-00/
│           ├── snippet_1.py
│           └── ...
├── sessions/
│   └── 2026-03-28_14-30-00_summary.md
├── db.sqlite
└── session_state.json
```

**数据库表结构（SQLite）**：
```sql
-- 会话表
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT,
    log_file TEXT NOT NULL,
    summary_file TEXT
);

-- 消息表（对话）
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    timestamp TEXT,
    role TEXT CHECK(role IN ('user', 'assistant')),
    content TEXT,
    raw_line_start INTEGER,
    raw_line_end INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 计划表
CREATE TABLE plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    timestamp TEXT,
    content TEXT,
    status TEXT CHECK(status IN ('pending', 'done', 'blocked')) DEFAULT 'pending',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 代码/命令执行表
CREATE TABLE exec_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    timestamp TEXT,
    type TEXT CHECK(type IN ('code', 'command')),
    content TEXT,
    result TEXT,
    file_path TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```

**索引**：为 `session_id`、`timestamp` 等字段创建索引，加速查询。

---

### 4. 恢复器（Resumer）

#### 4.1 CLI 恢复：`ahahooh resume`

**职责**：生成摘要，启动新 Claude Code 会话并加载摘要。

**实现思路**：
1. 从数据库中读取最近一次会话的信息（按 `start_time` 降序取第一条）。
2. 提取该会话的：
   - 未完成计划（`status='pending'`）
   - 最近 3 条代码/命令（按时间倒序）
   - 最近 5 条对话（可选）
3. 生成 Markdown 摘要文件，保存到 `sessions/` 或临时文件。
4. 启动 Claude Code：
   - 如果支持 `--prompt`，直接传入摘要内容。
   - 否则，将摘要文件路径告知用户，提示手动加载。

**摘要格式示例**：
```markdown
# 会话恢复摘要（2026-03-28 14:30:00）

## 未完成计划
- [ ] 实现 PDF 解析模块
- [ ] 编写单元测试

## 最近代码/命令
- 代码文件：`raw/code/2026-03-28_14-30-00/snippet_1.py`
  内容：实现 `extract_text` 函数
  执行命令：`python snippet_1.py test.pdf`
  输出：成功提取 150 行文本

## 最近对话
（最近 3 条对话记录...）

## 提示
请继续完成上述计划，并参考已有的代码。
```

#### 4.2 Claude Code 内恢复：`/resume`

**职责**：在现有会话中加载最近一次会话的摘要。

**实现方式**：利用 Claude Code 的斜杠命令（Slash Commands）功能。

**步骤**：
1. 在项目根目录创建 `.claude/commands/resume.md`，内容如下：
   ```markdown
   ---
   description: 恢复上一次会话的上下文
   ---
   请读取 `~/.ahalooh/resume_context.md` 文件（或项目本地的 `.ahalooh/sessions/最新摘要.md`），并基于其中的内容继续之前未完成的工作。

   包括：
   - 未完成的计划
   - 最近生成的代码位置
   - 上次对话的关键决策

   请先确认你理解了上下文，然后继续推进任务。
   ```
2. 当用户输入 `/resume` 时，Claude 会读取该文件并执行指令。
3. 需要确保摘要文件路径对 Claude 可见（可以使用绝对路径，或让 Claude 通过 `cat` 命令读取）。

**优化**：`ahahooh resume` CLI 命令可以同时更新一个全局符号链接 `~/.ahalooh/latest_summary.md`，指向最近一次会话的摘要，这样 `/resume` 命令只需固定读取该文件。

---

### 5. 命令接口（CLI）

使用 Python + Click 库实现命令行工具，提供以下子命令：

| 命令                         | 描述                                 |
| ---------------------------- | ------------------------------------ |
| `ahahooh start`              | 启动新会话（录制）                   |
| `ahahooh parse [session_id]` | 解析指定会话（默认最新）             |
| `ahahooh resume`             | 恢复上一次会话（启动新 Claude Code） |
| `ahahooh init`               | 在当前目录初始化 `.ahahooh/` 结构    |
| `ahahooh status`             | 显示当前会话状态（可选）             |

**安装**：通过 `pip install ahalooh` 安装，提供 `ahahooh` 可执行文件。

---

## 五、MVP 开发计划

| 阶段 | 任务                   | 预估时间 | 产出                                                         |
| ---- | ---------------------- | -------- | ------------------------------------------------------------ |
| 1    | 目录结构与数据库初始化 | 0.5 天   | `.ahahooh/` 创建脚本，建表 SQL                               |
| 2    | 录制器（tmux 包装）    | 1 天     | `ahahooh start` 可工作，日志正常写入                         |
| 3    | 解析器（核心逻辑）     | 2 天     | 能解析日志，提取对话/计划/代码/命令，存入数据库，保存代码文件 |
| 4    | 摘要生成与恢复（CLI）  | 1 天     | `ahahooh resume` 生成摘要并启动 Claude Code                  |
| 5    | `/resume` 斜杠命令支持 | 0.5 天   | 创建 `.claude/commands/resume.md`，用户可在 Claude Code 内使用 |
| 6    | 测试与文档             | 1 天     | 端到端测试，编写用户文档                                     |

**总计**：约 6 天。

---

## 六、风险与应对

| 风险                               | 应对策略                                                    |
| ---------------------------------- | ----------------------------------------------------------- |
| tmux 未安装或版本不兼容            | 检测依赖，提供安装指南；备选方案使用 `script` 录制          |
| 日志解析准确率低（尤其计划识别）   | MVP 仅用关键词，允许用户手动标记；后续迭代引入 LLM 辅助     |
| Claude Code 不支持 `--prompt` 参数 | 提供手动加载指南；通过斜杠命令恢复可弥补                    |
| 数据库性能问题                     | MVP 数据量小，SQLite 足够；后续可优化索引或迁移到更强大后端 |

---

## 七、后续迭代方向（V2/V3）

- **流式解析**：实时解析，即时保存代码，避免离线解析延迟。
- **智能计划识别**：使用 LLM 提取计划，提高准确率。
- **全文与语义检索**：基于摘要的向量化检索，支持自然语言查询。
- **多项目支持**：自动识别项目根目录，切换数据库。
- **动态上下文窗口**：根据当前任务自动注入相关历史。
- **云端同步**：支持将数据同步到远程存储，实现跨设备使用。

---

## 八、附录

### 关键正则表达式示例

```python
# 用户输入识别（行首 > 或 $）
user_pattern = re.compile(r'^>\s|^\$\s')

# 计划识别
plan_pattern = re.compile(r'计划[：:]|任务[：:]|待办|^- \[\]')

# 代码块识别
code_block_start = re.compile(r'^```(\w*)$')
code_block_end = re.compile(r'^```$')
```

### tmux 会话管理参考

- 创建会话：`tmux new-session -d -s <name>`
- 发送按键：`tmux send-keys -t <name> "claude code" Enter`
- 管道输出：`tmux pipe-pane -t <name> -o "cat >> <file>"`
- 关闭会话：`tmux kill-session -t <name>`

---

本计划文档为 Ahalooh MVP 的完整蓝图，后续可按此逐步实现。如有任何调整需求，可进一步细化各模块的技术细节。