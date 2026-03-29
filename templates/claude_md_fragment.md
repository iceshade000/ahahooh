

## Ahahooh Memory System

This project uses Ahahooh for persistent memory across Claude Code sessions.

### Auto-captured (hooks)
- Execution records (Write/Edit/Bash) are captured automatically by hooks. No manual action needed.

### Manual save (MCP tools)
1. When reaching a key decision point, proactively call `save_conversation` with a summary.
2. When forming a plan, call `save_plan` with goal and tasks.
3. When a task is completed or blocked, call `update_plan_task` to update status.

### Session resume
- At the start of a new session, check if `.ahahooh/` exists. If it does, call `get_resume_context` to restore context.
- Use `/aharesume` command to get a structured summary of previous work.

### Rules
- Memory files in `.ahahooh/data/` are immutable records. Never modify them.
- Use `search_memory` when you need historical context about past decisions or code changes.
