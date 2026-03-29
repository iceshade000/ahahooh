"""Parse Claude Code session JSONL files and sync summaries to ahahooh.

Claude Code stores session history in:
    ~/.claude/projects/<project-slug>/<session-id>.jsonl

Each line is a JSON object with type "user" or "assistant".
This module reads those files and generates searchable summaries.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import config
from .storage import save_conversation


def _get_claude_project_dir(project_root: Path) -> Path | None:
    """Find the Claude Code project directory for a given project root.

    Claude Code maps paths like D:\\claudecode\\ahahooh -> D--claudecode-ahahooh
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.is_dir():
        return None

    # Build slug: drive root (e.g. "D:\") -> "D--", then join rest with "-"
    # e.g. D:\claudecode\ahahooh -> D--claudecode-ahahooh
    #      /home/user/project -> -home-user-project (Unix)
    parts = list(project_root.parts)
    if parts and (parts[0].endswith(":\\") or parts[0].endswith(":/")):
        # Windows drive root like "D:\"
        drive_letter = parts[0][0]  # "D"
        rest = parts[1:]
        slug = drive_letter + "--" + ("-".join(rest) if rest else "")
    elif parts and parts[0] == "/":
        # Unix root
        slug = "-" + "-".join(parts[1:])
    else:
        slug = "-".join(parts)

    for s in (slug, slug.lower()):
        candidate = claude_dir / s
        if candidate.is_dir():
            return candidate

    # Fallback: scan dirs, check if any jsonl references our cwd
    target_cwd = str(project_root).replace("\\", "/")
    for d in claude_dir.iterdir():
        if not d.is_dir():
            continue
        for jsonl in d.glob("*.jsonl"):
            try:
                with open(jsonl, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("cwd", "").replace("\\", "/") == target_cwd:
                            return d
                        break
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _extract_text(content) -> str:
    """Extract plain text from message content (string or content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
    return str(content) if content else ""


def parse_session(jsonl_path: Path) -> dict | None:
    """Parse one session JSONL. Returns dict with messages or None."""
    user_msgs = []
    assistant_msgs = []
    session_id = ""
    first_ts = ""

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = data.get("type", "")
                if t == "user":
                    msg = data.get("message", {})
                    content = _extract_text(msg.get("content", ""))
                    if content and not content.startswith('[{"tool_use_id"'):
                        user_msgs.append(content[:200])
                    if not session_id:
                        session_id = data.get("sessionId", "")
                    if not first_ts:
                        first_ts = data.get("timestamp", "")

                elif t == "assistant":
                    msg = data.get("message", {})
                    content = _extract_text(msg.get("content", ""))
                    if content:
                        assistant_msgs.append(content[:300])
                    if not session_id:
                        session_id = data.get("sessionId", jsonl_path.stem)

    except OSError:
        return None

    if not user_msgs and not assistant_msgs:
        return None

    return {
        "session_id": session_id or jsonl_path.stem,
        "user_messages": user_msgs,
        "assistant_messages": assistant_msgs,
        "timestamp": first_ts,
    }


def build_summary(parsed: dict) -> str:
    """Build a one-line summary from parsed session."""
    parts = []
    if parsed["user_messages"]:
        parts.append(f"User: {parsed['user_messages'][0]}")
    if parsed["assistant_messages"]:
        # Last assistant msg usually summarizes the session
        parts.append(f"Result: {parsed['assistant_messages'][-1]}")
    return " | ".join(parts) if parts else ""


def extract_topics(parsed: dict) -> list[str]:
    """Extract topic keywords from messages."""
    text = " ".join(parsed["user_messages"] + parsed["assistant_messages"]).lower()
    keywords = [
        "implement", "fix", "bug", "refactor", "error", "test",
        "config", "setup", "deploy", "update", "add", "create",
        "delete", "change", "debug", "optimize", "design",
        "review", "install", "build", "commit", "plan",
    ]
    return [kw for kw in keywords if kw in text][:5]


def _get_indexed_session_ids(project_root: Path) -> set[str]:
    """Get session IDs already in ahahooh."""
    db_path = config.get_db_path(project_root)
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM conversations WHERE session_id != ''"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def sync_sessions(project_root: Path) -> int:
    """Sync all new Claude Code sessions into ahahooh.

    Scans JSONL files, skips sessions already indexed,
    parses and saves summaries. Returns count of new sessions.
    """
    claude_project_dir = _get_claude_project_dir(project_root)
    if claude_project_dir is None:
        return 0

    indexed = _get_indexed_session_ids(project_root)
    count = 0

    for jsonl_path in sorted(claude_project_dir.glob("*.jsonl")):
        session_id = jsonl_path.stem
        if session_id in indexed:
            continue

        parsed = parse_session(jsonl_path)
        if parsed is None:
            continue

        summary = build_summary(parsed)
        if not summary:
            continue

        topics = extract_topics(parsed)
        save_conversation(
            project_root=project_root,
            summary=summary,
            topics=topics,
            session_id=session_id,
        )
        count += 1

    return count
