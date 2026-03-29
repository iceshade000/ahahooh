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

# Track last recorded (session_id, tool_name, file_path/command) to deduplicate
_last_record_key: tuple[str, str, str] | None = None


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _extract_tool_info(data: dict) -> dict:
    """Extract relevant info from hook input based on tool name.

    Returns dict with:
      - input_summary / response_summary: truncated versions for SQLite/FTS
      - full_input / full_response: complete content for Markdown files
    """
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")

    file_path = ""
    command = ""
    input_summary = ""
    response_summary = ""
    full_input = ""
    full_response = ""

    if tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        input_summary = f"Write to {file_path}"
        if content:
            lines = content.strip().split("\n")
            preview = "\n".join(lines[:5])
            if len(lines) > 5:
                preview += f"\n... ({len(lines)} lines total)"
            input_summary = f"Write to {file_path}:\n{preview}"
        full_input = f"Write to {file_path}:\n{content}" if content else f"Write to {file_path}"
        if tool_response:
            response_summary = "Success"
            full_response = str(tool_response)

    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        old_text = tool_input.get("old_string", "")
        new_text = tool_input.get("new_string", "")
        input_summary = f"Edit {file_path}"
        if old_text:
            input_summary += f"\n- Replace: {_truncate(old_text, 200)}"
        if new_text:
            input_summary += f"\n- With: {_truncate(new_text, 200)}"
        full_input = f"Edit {file_path}\n- old_string:\n{old_text}\n- new_string:\n{new_text}"
        if tool_response:
            response_summary = "Success"
            full_response = str(tool_response)

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        input_summary = f"Execute: {command}" if command else ""
        full_input = f"Execute: {command}" if command else ""
        if tool_response:
            response_summary = _truncate(str(tool_response), 500)
            full_response = str(tool_response)

    else:
        # Generic capture
        input_summary = _truncate(json.dumps(tool_input, ensure_ascii=False), 500) if tool_input else ""
        response_summary = _truncate(str(tool_response), 500) if tool_response else ""
        full_input = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
        full_response = str(tool_response) if tool_response else ""

    return {
        "tool_name": tool_name,
        "file_path": file_path,
        "command": command,
        "input_summary": input_summary,
        "response_summary": response_summary,
        "full_input": full_input,
        "full_response": full_response,
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
    """Handle PostToolUse hook. All errors are caught to never interfere with Claude Code."""
    global _last_record_key
    try:
        project_root = config.find_project_root()
        if project_root is None:
            return

        info = _extract_tool_info(data)
        if not info["tool_name"]:
            return

        session_id = data.get("session_id", "")

        # Skip blacklisted Bash commands
        if info["tool_name"] == "Bash" and info["command"]:
            cmd_stripped = info["command"].strip()
            # Match base command (first token or first two tokens)
            parts = cmd_stripped.split(None, 1)
            base_cmd = parts[0] if parts else ""
            two_token = cmd_stripped.split(None, 2)
            two_part = " ".join(two_token[:2]) if len(two_token) >= 2 else base_cmd
            if base_cmd in config.BASH_SKIP_COMMANDS or two_part in config.BASH_SKIP_COMMANDS:
                return

        # Deduplicate: skip consecutive identical (session, tool, target) records
        target = info["file_path"] or info["command"] or ""
        record_key = (session_id, info["tool_name"], target)
        if record_key == _last_record_key:
            return
        _last_record_key = record_key

        save_execution_record(
            project_root=project_root,
            tool_name=info["tool_name"],
            file_path=info["file_path"],
            command=info["command"],
            input_summary=info["input_summary"],
            response_summary=info["response_summary"],
            session_id=session_id,
            full_input=info.get("full_input", ""),
            full_response=info.get("full_response", ""),
        )

        # Capture plan mode writes: if Write/Edit targets ~/.claude/plans/, save to ahahooh
        if info["tool_name"] in ("Write", "Edit") and info["file_path"]:
            if _is_plan_file(info["file_path"]):
                if info["tool_name"] == "Write":
                    content = data.get("tool_input", {}).get("content", "")
                else:
                    try:
                        content = Path(info["file_path"]).read_text(encoding="utf-8")
                    except (OSError, FileNotFoundError):
                        content = ""
                if content and content.strip():
                    plan = _extract_plan_from_content(content)
                    plan_id = f"planmode_{Path(info['file_path']).stem}"
                    save_plan(
                        project_root=project_root,
                        goal=plan["goal"],
                        tasks=plan["tasks"],
                        plan_id=plan_id,
                        session_id=session_id,
                    )
    except Exception as e:
        print(f"ahahooh hook error: {e}", file=sys.stderr)


def handle_stop(data: dict | None = None) -> None:
    """Handle Stop hook - sync sessions and rebuild index.

    Runs with a 10-second timeout to avoid blocking session shutdown.
    All errors are caught to never interfere with Claude Code.
    """
    import threading

    def _do_stop():
        project_root = config.find_project_root()
        if project_root is None:
            return

        from .session_sync import sync_sessions
        sync_sessions(project_root)

        from .plan_sync import sync_plans
        sync_plans(project_root)

        from .index import build_index
        build_index(project_root)

    try:
        t = threading.Thread(target=_do_stop, daemon=True)
        t.start()
        t.join(timeout=10)
        if t.is_alive():
            print("ahahooh: stop hook timed out after 10s", file=sys.stderr)
    except Exception as e:
        print(f"ahahooh stop hook error: {e}", file=sys.stderr)


def main():
    is_stop = "--stop" in sys.argv

    try:
        # Read JSON from stdin
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
    except Exception as e:
        print(f"ahahooh: error reading input: {e}", file=sys.stderr)
        return

    if is_stop:
        handle_stop(data)
    else:
        handle_post_tool_use(data)


if __name__ == "__main__":
    main()
