"""PostToolUse hook entry point.

Reads JSON from stdin, saves execution records.
Called by Claude Code hooks system.

Usage:
    python -m ahahooh.hook_handler          # PostToolUse
    python -m ahahooh.hook_handler --stop   # Stop hook (rebuild index)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config
from .storage import save_execution_record, save_plan, save_conversation


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _extract_tool_info(data: dict) -> dict:
    """Extract relevant info from hook input based on tool name."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")

    file_path = ""
    command = ""
    input_summary = ""
    response_summary = ""

    if tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        input_summary = f"Write to {file_path}"
        if content:
            # Show first few lines
            lines = content.strip().split("\n")
            preview = "\n".join(lines[:5])
            if len(lines) > 5:
                preview += f"\n... ({len(lines)} lines total)"
            input_summary = f"Write to {file_path}:\n{preview}"
        if tool_response:
            response_summary = "Success"

    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        old_text = tool_input.get("old_string", "")
        new_text = tool_input.get("new_string", "")
        input_summary = f"Edit {file_path}"
        if old_text:
            input_summary += f"\n- Replace: {_truncate(old_text, 200)}"
        if new_text:
            input_summary += f"\n- With: {_truncate(new_text, 200)}"
        if tool_response:
            response_summary = "Success"

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        input_summary = f"Execute: {command}" if command else ""
        if tool_response:
            response_summary = _truncate(str(tool_response), 500)

    else:
        # Generic capture
        input_summary = _truncate(json.dumps(tool_input, ensure_ascii=False), 500) if tool_input else ""
        response_summary = _truncate(str(tool_response), 500) if tool_response else ""

    return {
        "tool_name": tool_name,
        "file_path": file_path,
        "command": command,
        "input_summary": input_summary,
        "response_summary": response_summary,
    }


def _is_plan_file(file_path: str) -> bool:
    """Check if a file path is a Claude Code plan file."""
    # Claude Code writes plans to ~/.claude/plans/<slug>.md
    # On Windows this could be C:\Users\<user>\.claude\plans\<slug>.md
    parts = Path(file_path).parts
    return ".claude" in parts and "plans" in parts


def _extract_plan_from_content(content: str) -> dict:
    """Extract goal and tasks from plan markdown content."""
    lines = content.strip().split("\n")
    goal = ""
    tasks = []

    for line in lines:
        line = line.strip()
        # First heading or non-empty line is likely the goal
        if not goal and line and not line.startswith("-") and not line.startswith("*"):
            goal = line.lstrip("#").strip()
        # Bullet points are tasks
        if line.startswith("- ") or line.startswith("* "):
            task_text = line.lstrip("-* ").strip()
            if task_text:
                tasks.append({"description": task_text, "status": "pending"})

    if not goal:
        goal = "Untitled plan"

    return {"goal": goal, "tasks": tasks}


def handle_post_tool_use(data: dict) -> None:
    """Handle PostToolUse hook."""
    project_root = config.find_project_root()
    if project_root is None:
        # Not in an ahahooh project, silently skip
        return

    info = _extract_tool_info(data)
    if not info["tool_name"]:
        return

    session_id = data.get("session_id", "")

    save_execution_record(
        project_root=project_root,
        tool_name=info["tool_name"],
        file_path=info["file_path"],
        command=info["command"],
        input_summary=info["input_summary"],
        response_summary=info["response_summary"],
        session_id=session_id,
    )

    # Capture plan mode writes: if Write/Edit targets ~/.claude/plans/, save to ahahooh
    if info["tool_name"] in ("Write", "Edit") and info["file_path"]:
        if _is_plan_file(info["file_path"]):
            # For Write, content is in tool_input.content
            # For Edit, read the file on disk to get the latest full content
            if info["tool_name"] == "Write":
                content = data.get("tool_input", {}).get("content", "")
            else:
                try:
                    content = Path(info["file_path"]).read_text(encoding="utf-8")
                except (OSError, FileNotFoundError):
                    content = ""
            if content and content.strip():
                plan = _extract_plan_from_content(content)
                # Use the plan file stem as plan_id for uniqueness and upsert
                plan_id = f"planmode_{Path(info['file_path']).stem}"
                save_plan(
                    project_root=project_root,
                    goal=plan["goal"],
                    tasks=plan["tasks"],
                    plan_id=plan_id,
                    session_id=session_id,
                )


def handle_stop(data: dict | None = None) -> None:
    """Handle Stop hook - sync sessions and rebuild index."""
    project_root = config.find_project_root()
    if project_root is None:
        return

    # Sync new sessions from Claude Code's own history
    from .session_sync import sync_sessions
    sync_sessions(project_root)

    # Sync plan-mode files that bypassed the Write tool
    from .plan_sync import sync_plans
    sync_plans(project_root)

    from .index import build_index
    build_index(project_root)


def main():
    is_stop = "--stop" in sys.argv

    # Read JSON from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            if is_stop:
                handle_stop(None)
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, EOFError):
        if is_stop:
            handle_stop(None)
        return

    if is_stop:
        handle_stop(data)
    else:
        handle_post_tool_use(data)


if __name__ == "__main__":
    main()
