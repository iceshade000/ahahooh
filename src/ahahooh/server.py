"""FastMCP MCP Server with 5 tools for Claude Code."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from . import config
from .storage import (
    get_resume_context as _get_resume_context,
    save_conversation as _save_conversation,
    save_plan as _save_plan,
    search as _search,
    update_plan_task as _update_plan_task,
)

mcp = FastMCP("ahahooh")


def _get_root() -> Path:
    """Find project root."""
    root = config.find_project_root()
    if root is None:
        raise RuntimeError("Ahahooh is not initialized. Run 'ahahooh init' first.")
    return root


@mcp.tool()
def save_conversation(
    summary: str,
    key_decisions: list[str] | None = None,
    topics: list[str] | None = None,
) -> str:
    """Save a conversation summary to memory.

    Call this when a discussion reaches a key decision point or natural stopping point.

    Args:
        summary: Brief summary of the conversation (1-3 sentences)
        key_decisions: List of key decisions made during the conversation
        topics: List of topics discussed
    """
    root = _get_root()
    filename = _save_conversation(root, summary, key_decisions, topics)
    return f"Conversation saved to {filename}"


@mcp.tool()
def save_plan(
    goal: str,
    tasks: list[dict] | None = None,
    plan_id: str | None = None,
) -> str:
    """Save or update a plan in memory.

    Call this when forming a concrete plan with actionable tasks.

    Args:
        goal: The plan's goal (1-2 sentences)
        tasks: List of task dicts with 'description' and optional 'status' fields
        plan_id: Optional plan ID. Auto-generated if not provided. Use same ID to update.
    """
    root = _get_root()
    pid = _save_plan(root, goal, tasks, plan_id)
    return f"Plan '{pid}' saved"


@mcp.tool()
def update_plan_task(
    plan_id: str,
    task_index: int,
    status: str,
) -> str:
    """Update a task's status within a plan.

    Call this when a task is completed, blocked, or starts.

    Args:
        plan_id: The plan ID to update
        task_index: Zero-based index of the task in the plan
        status: New status - one of: pending, in_progress, completed, blocked
    """
    root = _get_root()
    valid = {"pending", "in_progress", "completed", "blocked"}
    if status not in valid:
        return f"Invalid status '{status}'. Must be one of: {', '.join(valid)}"
    ok = _update_plan_task(root, plan_id, task_index, status)
    if ok:
        return f"Task {task_index} in plan '{plan_id}' updated to '{status}'"
    return f"Failed to update: plan '{plan_id}' not found or task index out of range"


@mcp.tool()
def search_memory(
    query: str,
    type: str = "all",
    limit: int = 10,
) -> str:
    """Search memory for past records, conversations, and plans.

    Use this when you need historical context about past decisions or code changes.

    Args:
        query: Search terms (supports FTS5 syntax)
        type: Type filter - 'all', 'execution', 'conversation', or 'plan'
        limit: Maximum results to return (default 10)
    """
    root = _get_root()
    results = _search(root, query, type, limit)
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):\n"]
    for r in results:
        rtype = r["type"]
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        if rtype == "execution":
            lines.append(f"[{ts}] EXECUTION {r['tool_name']}: {r.get('file_path') or r.get('command', '')}")
        elif rtype == "conversation":
            lines.append(f"[{ts}] CONVERSATION: {r['summary'][:100]}")
            if r.get("key_decisions"):
                for d in r["key_decisions"]:
                    lines.append(f"  - Decision: {d}")
        elif rtype == "plan":
            lines.append(f"[{ts}] PLAN [{r['plan_id']}]: {r['goal'][:100]}")
            lines.append(f"  - {r['completed']} completed, {r['pending']} pending")

    return "\n".join(lines)


@mcp.tool()
def get_resume_context() -> str:
    """Get compressed context to resume a previous session.

    Call this at the start of a new session if .ahahooh/ exists in the project.
    Returns active plans, recent conversations, and recent execution records.
    Also rebuilds the index to ensure it's up-to-date (handles cases where the
    previous session was terminated by closing the terminal window).
    """
    root = _get_root()

    # Sync plan-mode files that bypassed the Write tool
    from .plan_sync import sync_plans
    sync_plans(root)

    # Rebuild index as a safety net — the Stop hook may not fire if the user
    # closed the terminal window directly instead of pressing Ctrl+C twice.
    from .index import build_index
    build_index(root)

    ctx = _get_resume_context(root)

    # Return compact structured text — Claude should output this verbatim
    lines = []

    plans = ctx.get("active_plans", [])
    if plans:
        lines.append("Plans:")
        for p in plans:
            lines.append(f"- {p['plan_id']}: \"{p['goal']}\" ({p['pending']} pending, {p['completed']} done)")

    convs = ctx.get("recent_conversations", [])
    if convs:
        lines.append("Recent talks:")
        for c in convs[:3]:
            ts = c["timestamp"][:10]
            decisions = ", ".join(c.get("key_decisions", []))
            line = f"- [{ts}] {c['summary']}"
            if decisions:
                line += f" -- decided: {decisions}"
            lines.append(line)

    records = ctx.get("recent_records", [])
    if records:
        lines.append("Recent actions:")
        for r in records:
            desc = r.get("file_path") or r.get("command") or r.get("summary", "")
            lines.append(f"- {r['tool_name']}: {desc}")

    if not lines:
        return "No previous session data found."

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
