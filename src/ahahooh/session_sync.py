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
from .storage import save_conversation, update_conversation_by_session


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


def _tail_sentences(text: str, max_chars: int = 300) -> str:
    """Extract the concluding portion of text, trying to start at a sentence boundary."""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    # Try to cut at a paragraph or sentence break in the first half
    for sep in ("\n\n", ". ", "。", "! ", "? "):
        idx = tail.find(sep)
        if 0 < idx < len(tail) // 2:
            tail = tail[idx + len(sep):]
            break
    return tail.strip()


def _truncate_clean(text: str, max_chars: int) -> str:
    """Truncate text from the end, breaking at a sentence boundary when possible."""
    if len(text) <= max_chars:
        return text.strip()
    head = text[:max_chars]
    # Try to cut at a sentence/paragraph boundary in the last 40%
    for sep in (". ", "。", "! ", "? ", "\n", "; "):
        idx = head.rfind(sep)
        if idx > max_chars * 0.6:
            return head[:idx + len(sep)].strip()
    return head.rstrip()


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
                        user_msgs.append(content[:500])
                    if not session_id:
                        session_id = data.get("sessionId", "")
                    if not first_ts:
                        first_ts = data.get("timestamp", "")

                elif t == "assistant":
                    msg = data.get("message", {})
                    content = _extract_text(msg.get("content", ""))
                    if content:
                        assistant_msgs.append(_tail_sentences(content))
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
    """Build structured intent→result summary from parsed session.

    Format: Intent: <goal> | Steps: <key turns> | Result: <conclusion>
    Compact but FTS-searchable.
    """
    user_msgs = parsed["user_messages"]
    assistant_msgs = parsed["assistant_messages"]

    # Intent: first user message (the main request)
    intent = _truncate_clean(user_msgs[0], 200) if user_msgs else ""

    # Result: last assistant message (final conclusion)
    result = _truncate_clean(assistant_msgs[-1], 300) if assistant_msgs else ""

    parts = []
    if intent:
        parts.append(f"Intent: {intent}")

    # Middle steps: brief user messages (evolving requests)
    if len(user_msgs) > 1:
        steps = [_truncate_clean(m, 80) for m in user_msgs[1:6]]
        parts.append(f"Steps: {' | '.join(steps)}")

    if result:
        parts.append(f"Result: {result}")

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


def _get_synced_sessions(project_root: Path) -> dict[str, float]:
    """Get session IDs already in ahahooh, mapped to their last sync time (epoch).

    Returns dict of session_id -> timestamp epoch (parsed from DB timestamp field).
    """
    db_path = config.get_db_path(project_root)
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT session_id, timestamp FROM conversations WHERE session_id != ''"
        ).fetchall()
        result = {}
        for session_id, ts_str in rows:
            # Parse ISO timestamp to epoch for comparison with file mtime
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(ts_str)
                epoch = dt.timestamp()
            except (ValueError, TypeError):
                epoch = 0.0
            # Keep the latest timestamp if multiple records share a session_id
            if session_id not in result or epoch > result[session_id]:
                result[session_id] = epoch
        return result
    finally:
        conn.close()


def sync_sessions(project_root: Path) -> int:
    """Sync all new and updated Claude Code sessions into ahahooh.

    Scans JSONL files. New sessions are inserted; sessions whose JSONL
    has been modified since last sync (e.g. resumed sessions with new
    messages) are re-parsed and updated. Returns count of changes.
    """
    claude_project_dir = _get_claude_project_dir(project_root)
    if claude_project_dir is None:
        return 0

    synced = _get_synced_sessions(project_root)
    count = 0

    for jsonl_path in sorted(claude_project_dir.glob("*.jsonl")):
        session_id = jsonl_path.stem
        file_mtime = jsonl_path.stat().st_mtime

        if session_id in synced:
            # Already synced — check if JSONL has new content
            if file_mtime <= synced[session_id]:
                continue
            # File was modified after last sync (resumed session)
            parsed = parse_session(jsonl_path)
            if parsed is None:
                continue
            summary = build_summary(parsed)
            if not summary:
                continue
            topics = extract_topics(parsed)
            if update_conversation_by_session(
                project_root=project_root,
                session_id=session_id,
                summary=summary,
                topics=topics,
            ):
                count += 1
        else:
            # New session
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


def recompact_summaries(project_root: Path) -> int:
    """One-time migration: re-generate old-format summaries using new structured format.

    Only processes summaries starting with "User:" (old format).
    Once all are migrated this becomes a no-op. Returns count migrated.
    """
    claude_project_dir = _get_claude_project_dir(project_root)
    if claude_project_dir is None:
        return 0

    db_path = config.get_db_path(project_root)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT session_id FROM conversations"
            " WHERE session_id != '' AND summary LIKE 'User: %'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    count = 0
    for (session_id,) in rows:
        jsonl_path = claude_project_dir / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            continue
        parsed = parse_session(jsonl_path)
        if parsed is None:
            continue
        summary = build_summary(parsed)
        if not summary:
            continue
        topics = extract_topics(parsed)
        if update_conversation_by_session(
            project_root=project_root,
            session_id=session_id,
            summary=summary,
            topics=topics,
        ):
            count += 1

    return count
