"""SQLite + file storage layer for ahahooh."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import Conversation, ExecutionRecord, Plan, PlanTask


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    file_path TEXT DEFAULT '',
    command TEXT DEFAULT '',
    input_summary TEXT DEFAULT '',
    response_summary TEXT DEFAULT '',
    record_file TEXT NOT NULL,
    session_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    summary TEXT NOT NULL,
    summary_short TEXT DEFAULT '',
    key_decisions TEXT DEFAULT '[]',
    topics TEXT DEFAULT '[]',
    file_path TEXT NOT NULL,
    session_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL,
    goal TEXT NOT NULL,
    tasks_json TEXT DEFAULT '[]',
    file_path TEXT NOT NULL,
    session_id TEXT DEFAULT ''
);

-- FTS5 virtual tables (unicode61 tokenizer for CJK and better word boundary)
CREATE VIRTUAL TABLE IF NOT EXISTS fts_records USING fts5(
    timestamp, tool_name, file_path, command, input_summary, response_summary,
    content=execution_records, content_rowid=id, tokenize="unicode61 categories 'L* N* Co'"
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_conversations USING fts5(
    timestamp, summary, key_decisions, topics,
    content=conversations, content_rowid=id, tokenize="unicode61 categories 'L* N* Co'"
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_plans USING fts5(
    plan_id, goal, tasks_json,
    content=plans, content_rowid=id, tokenize="unicode61 categories 'L* N* Co'"
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON execution_records BEGIN
    INSERT INTO fts_records(rowid, timestamp, tool_name, file_path, command, input_summary, response_summary)
    VALUES (new.id, new.timestamp, new.tool_name, new.file_path, new.command, new.input_summary, new.response_summary);
END;

CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON execution_records BEGIN
    INSERT INTO fts_records(fts_records, rowid, timestamp, tool_name, file_path, command, input_summary, response_summary)
    VALUES ('delete', old.id, old.timestamp, old.tool_name, old.file_path, old.command, old.input_summary, old.response_summary);
END;

CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON execution_records BEGIN
    INSERT INTO fts_records(fts_records, rowid, timestamp, tool_name, file_path, command, input_summary, response_summary)
    VALUES ('delete', old.id, old.timestamp, old.tool_name, old.file_path, old.command, old.input_summary, old.response_summary);
    INSERT INTO fts_records(rowid, timestamp, tool_name, file_path, command, input_summary, response_summary)
    VALUES (new.id, new.timestamp, new.tool_name, new.file_path, new.command, new.input_summary, new.response_summary);
END;

CREATE TRIGGER IF NOT EXISTS conv_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO fts_conversations(rowid, timestamp, summary, key_decisions, topics)
    VALUES (new.id, new.timestamp, new.summary, new.key_decisions, new.topics);
END;

CREATE TRIGGER IF NOT EXISTS conv_ad AFTER DELETE ON conversations BEGIN
    INSERT INTO fts_conversations(fts_conversations, rowid, timestamp, summary, key_decisions, topics)
    VALUES ('delete', old.id, old.timestamp, old.summary, old.key_decisions, old.topics);
END;

CREATE TRIGGER IF NOT EXISTS conv_au AFTER UPDATE ON conversations BEGIN
    INSERT INTO fts_conversations(fts_conversations, rowid, timestamp, summary, key_decisions, topics)
    VALUES ('delete', old.id, old.timestamp, old.summary, old.key_decisions, old.topics);
    INSERT INTO fts_conversations(rowid, timestamp, summary, key_decisions, topics)
    VALUES (new.id, new.timestamp, new.summary, new.key_decisions, new.topics);
END;

CREATE TRIGGER IF NOT EXISTS plans_ai AFTER INSERT ON plans BEGIN
    INSERT INTO fts_plans(rowid, plan_id, goal, tasks_json)
    VALUES (new.id, new.plan_id, new.goal, new.tasks_json);
END;

CREATE TRIGGER IF NOT EXISTS plans_ad AFTER DELETE ON plans BEGIN
    INSERT INTO fts_plans(fts_plans, rowid, plan_id, goal, tasks_json)
    VALUES ('delete', old.id, old.plan_id, old.goal, old.tasks_json);
END;

CREATE TRIGGER IF NOT EXISTS plans_au AFTER UPDATE ON plans BEGIN
    INSERT INTO fts_plans(fts_plans, rowid, plan_id, goal, tasks_json)
    VALUES ('delete', old.id, old.plan_id, old.goal, old.tasks_json);
    INSERT INTO fts_plans(rowid, plan_id, goal, tasks_json)
    VALUES (new.id, new.plan_id, new.goal, new.tasks_json);
END;
"""


def init_db(db_path: Path) -> None:
    """Initialize the database with schema."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    # Migration: add summary_short column if missing
    try:
        conn.execute("SELECT summary_short FROM conversations LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE conversations ADD COLUMN summary_short TEXT DEFAULT ''")
        rows = conn.execute("SELECT id, summary FROM conversations").fetchall()
        for row in rows:
            short = _make_short_summary(row[1])  # row[1] = summary
            conn.execute("UPDATE conversations SET summary_short = ? WHERE id = ?", (short, row[0]))
        conn.commit()
    # Migration: rebuild FTS indexes if tokenizer changed (unicode61)
    _rebuild_fts_if_needed(conn)
    conn.close()


def _rebuild_fts_if_needed(conn: sqlite3.Connection) -> None:
    """Rebuild FTS virtual tables to apply new tokenizer settings.

    Checks if the existing FTS tables use the new unicode61 tokenizer.
    If not, drops and recreates them (content is re-synced via triggers).
    """
    for table in ("fts_records", "fts_conversations", "fts_plans"):
        try:
            # Check if table exists and has the new tokenizer
            schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if schema and "unicode61" not in (schema[0] or ""):
                # Rebuild: drop and recreate, then re-populate
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.commit()
        except sqlite3.OperationalError:
            pass
    # Re-run schema to recreate any dropped FTS tables
    conn.executescript(_SCHEMA)

    # Re-populate FTS from base tables
    for base, fts, cols in [
        ("execution_records", "fts_records",
         "timestamp, tool_name, file_path, command, input_summary, response_summary"),
        ("conversations", "fts_conversations",
         "timestamp, summary, key_decisions, topics"),
        ("plans", "fts_plans",
         "plan_id, goal, tasks_json"),
    ]:
        try:
            # Check if FTS table is empty
            count = conn.execute(f"SELECT count(*) FROM {fts}").fetchone()[0]
            base_count = conn.execute(f"SELECT count(*) FROM {base}").fetchone()[0]
            if count < base_count:
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _make_short_summary(summary: str, max_len: int = 150) -> str:
    """Extract a short version of a structured summary for compact display."""
    if not summary:
        return ""
    # Extract Intent and Result from structured format
    intent = ""
    result = ""
    for part in summary.split(" | "):
        if part.startswith("Intent: "):
            intent = part[8:]
        elif part.startswith("Result: "):
            result = part[8:]
    if intent:
        short = intent
        if result:
            short += f" => {result}"
        return short[:max_len]
    # Fallback: plain truncation
    return summary[:max_len]


def _get_conn(project_root: Path) -> sqlite3.Connection:
    db_path = config.get_db_path(project_root)
    conn = sqlite3.connect(str(db_path), timeout=3)
    conn.row_factory = sqlite3.Row
    # Ensure schema exists (idempotent via IF NOT EXISTS)
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Execution records
# ---------------------------------------------------------------------------

def save_execution_record(
    project_root: Path,
    tool_name: str,
    file_path: str = "",
    command: str = "",
    input_summary: str = "",
    response_summary: str = "",
    session_id: str = "",
    full_input: str = "",
    full_response: str = "",
) -> str:
    """Save an execution record to markdown file and SQLite.

    Args:
        input_summary/response_summary: truncated text for SQLite/FTS (token-efficient)
        full_input/full_response: complete content for Markdown file (full fidelity)
    """
    ts = _now_iso()
    short = _short_ts()

    # Build markdown content (uses full content for completeness)
    slug = tool_name.lower()
    filename = f"{short}_{slug}.md"
    records_dir = config.get_records_dir(project_root)
    records_dir.mkdir(parents=True, exist_ok=True)
    record_path = records_dir / filename

    md_input = full_input or input_summary
    md_response = full_response or response_summary

    md_lines = [
        f"# Execution Record: {tool_name}",
        f"",
        f"- **Timestamp**: {ts}",
        f"- **Tool**: {tool_name}",
    ]
    if file_path:
        md_lines.append(f"- **File**: `{file_path}`")
    if command:
        md_lines.append(f"- **Command**: `{command}`")
    if session_id:
        md_lines.append(f"- **Session**: {session_id}")
    md_lines.append("")
    if md_input:
        md_lines.append("## Input")
        md_lines.append("")
        md_lines.append(md_input)
        md_lines.append("")
    if md_response:
        md_lines.append("## Response")
        md_lines.append("")
        md_lines.append(md_response)
        md_lines.append("")

    record_path.write_text("\n".join(md_lines), encoding="utf-8")

    # Insert into DB
    conn = _get_conn(project_root)
    try:
        conn.execute(
            """INSERT INTO execution_records
               (timestamp, tool_name, file_path, command, input_summary, response_summary, record_file, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, tool_name, file_path, command, input_summary, response_summary, filename, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return filename


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def save_conversation(
    project_root: Path,
    summary: str,
    key_decisions: list[str] | None = None,
    topics: list[str] | None = None,
    session_id: str = "",
    summary_short: str = "",
) -> str:
    """Save a conversation summary to markdown file and SQLite."""
    ts = _now_iso()
    short = _short_ts()

    key_decisions = key_decisions or []
    topics = topics or []
    if not summary_short:
        summary_short = _make_short_summary(summary)

    filename = f"{short}_conversation.md"
    conv_dir = config.get_conversations_dir(project_root)
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv_path = conv_dir / filename

    md_lines = [
        "# Conversation Summary",
        "",
        f"- **Timestamp**: {ts}",
    ]
    if session_id:
        md_lines.append(f"- **Session**: {session_id}")
    md_lines.append("")
    md_lines.append("## Summary")
    md_lines.append("")
    md_lines.append(summary)
    md_lines.append("")

    if key_decisions:
        md_lines.append("## Key Decisions")
        md_lines.append("")
        for d in key_decisions:
            md_lines.append(f"- {d}")
        md_lines.append("")

    if topics:
        md_lines.append("## Topics")
        md_lines.append("")
        for t in topics:
            md_lines.append(f"- {t}")
        md_lines.append("")

    conv_path.write_text("\n".join(md_lines), encoding="utf-8")

    conn = _get_conn(project_root)
    try:
        conn.execute(
            """INSERT INTO conversations
               (timestamp, summary, summary_short, key_decisions, topics, file_path, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, summary, summary_short, json.dumps(key_decisions), json.dumps(topics), filename, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return filename


def update_conversation_by_session(
    project_root: Path,
    session_id: str,
    summary: str,
    topics: list[str] | None = None,
) -> bool:
    """Update an existing conversation record identified by session_id.

    Rewrites both the markdown file and the SQLite row. Returns True if updated.
    """
    topics = topics or []
    ts = _now_iso()

    conn = _get_conn(project_root)
    try:
        row = conn.execute(
            "SELECT id, file_path FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return False

        row_id = row["id"]
        old_file = row["file_path"]

        # Update markdown file
        conv_dir = config.get_conversations_dir(project_root)
        conv_path = conv_dir / old_file
        if conv_path.exists():
            md_lines = [
                "# Conversation Summary",
                "",
                f"- **Timestamp**: {ts}",
                f"- **Session**: {session_id}",
                "",
                "## Summary",
                "",
                summary,
                "",
            ]
            if topics:
                md_lines.append("## Topics")
                md_lines.append("")
                for t in topics:
                    md_lines.append(f"- {t}")
                md_lines.append("")
            conv_path.write_text("\n".join(md_lines), encoding="utf-8")

        # Update DB row (triggers conv_au keep FTS in sync)
        summary_short = _make_short_summary(summary)
        conn.execute(
            """UPDATE conversations
               SET timestamp=?, summary=?, summary_short=?, topics=?
               WHERE id=?""",
            (ts, summary, summary_short, json.dumps(topics), row_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

def save_plan(
    project_root: Path,
    goal: str,
    tasks: list[dict] | None = None,
    plan_id: str | None = None,
    session_id: str = "",
) -> str:
    """Save or update a plan. Returns plan_id."""
    ts = _now_iso()
    tasks = tasks or []
    plan_id = plan_id or f"plan_{_short_ts()}"

    # Build markdown
    filename = f"{plan_id}.md"
    plans_dir = config.get_plans_dir(project_root)
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / filename

    md_lines = [
        f"# Plan: {plan_id}",
        "",
        f"- **Timestamp**: {ts}",
        f"- **Goal**: {goal}",
    ]
    if session_id:
        md_lines.append(f"- **Session**: {session_id}")
    md_lines.append("")

    if tasks:
        md_lines.append("## Tasks")
        md_lines.append("")
        for i, t in enumerate(tasks):
            status = t.get("status", "pending")
            desc = t.get("description", "")
            marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}.get(status, "[ ]")
            md_lines.append(f"{i+1}. {marker} {desc} ({status})")
        md_lines.append("")

    plan_path.write_text("\n".join(md_lines), encoding="utf-8")

    conn = _get_conn(project_root)
    try:
        # Upsert: try insert, on conflict update
        existing = conn.execute(
            "SELECT id FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE plans SET timestamp=?, goal=?, tasks_json=?, file_path=?, session_id=?
                   WHERE plan_id=?""",
                (ts, goal, json.dumps(tasks, ensure_ascii=False), filename, session_id, plan_id),
            )
        else:
            conn.execute(
                """INSERT INTO plans
                   (plan_id, timestamp, goal, tasks_json, file_path, session_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (plan_id, ts, goal, json.dumps(tasks, ensure_ascii=False), filename, session_id),
            )
        conn.commit()
    finally:
        conn.close()

    return plan_id


def update_plan_task(
    project_root: Path,
    plan_id: str,
    task_index: int,
    status: str,
) -> bool:
    """Update a task status in a plan. Returns True if successful."""
    conn = _get_conn(project_root)
    try:
        row = conn.execute(
            "SELECT tasks_json, file_path FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return False

        tasks = json.loads(row["tasks_json"])
        if task_index < 0 or task_index >= len(tasks):
            return False

        tasks[task_index]["status"] = status

        ts = _now_iso()
        conn.execute(
            "UPDATE plans SET tasks_json=?, timestamp=? WHERE plan_id=?",
            (json.dumps(tasks, ensure_ascii=False), ts, plan_id),
        )
        conn.commit()

        # Rewrite markdown file
        filename = row["file_path"]
        plans_dir = config.get_plans_dir(project_root)
        plan_path = plans_dir / filename

        # Get goal for rewriting
        goal_row = conn.execute(
            "SELECT goal, session_id FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()

        md_lines = [
            f"# Plan: {plan_id}",
            "",
            f"- **Timestamp**: {ts}",
            f"- **Goal**: {goal_row['goal']}",
        ]
        if goal_row["session_id"]:
            md_lines.append(f"- **Session**: {goal_row['session_id']}")
        md_lines.append("")
        md_lines.append("## Tasks")
        md_lines.append("")
        for i, t in enumerate(tasks):
            s = t.get("status", "pending")
            desc = t.get("description", "")
            marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}.get(s, "[ ]")
            md_lines.append(f"{i+1}. {marker} {desc} ({s})")
        md_lines.append("")

        plan_path.write_text("\n".join(md_lines), encoding="utf-8")
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    project_root: Path,
    query: str,
    record_type: str = "all",
    limit: int = 10,
) -> list[dict]:
    """Search across records using FTS5 with OR fallback."""
    conn = _get_conn(project_root)
    results = []

    try:
        # Try exact phrase match first
        safe_query = query.replace('"', '""')
        fts_query = f'"{safe_query}"'

        results = _do_fts_search(conn, fts_query, record_type, limit)

        # Fallback: if no results, try OR search with individual words
        if not results:
            words = query.strip().split()
            if len(words) > 1:
                or_parts = [w.replace('"', '""') for w in words if len(w) >= 2]
                if or_parts:
                    fts_query = " OR ".join(f'"{w}"' for w in or_parts)
                    results = _do_fts_search(conn, fts_query, record_type, limit)
    finally:
        conn.close()

    # Sort all results by timestamp descending
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results[:limit]


def _do_fts_search(conn, fts_query: str, record_type: str, limit: int) -> list[dict]:
    """Execute FTS search across tables with a given FTS query string."""
    results = []

    if record_type in ("all", "execution"):
        try:
            rows = conn.execute(
                """SELECT r.id, r.timestamp, r.tool_name, r.file_path, r.command,
                          r.input_summary, r.response_summary, r.record_file
                   FROM fts_records f
                   JOIN execution_records r ON r.id = f.rowid
                   WHERE fts_records MATCH ?
                   ORDER BY r.timestamp DESC LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "type": "execution",
                    "timestamp": r["timestamp"],
                    "tool_name": r["tool_name"],
                    "file_path": r["file_path"],
                    "command": r["command"],
                    "summary": (r["input_summary"] or r["response_summary"])[:100],
                    "record_file": r["record_file"],
                })
        except sqlite3.OperationalError:
            pass

    if record_type in ("all", "conversation"):
        try:
            rows = conn.execute(
                """SELECT c.id, c.timestamp, c.summary, c.summary_short, c.key_decisions, c.topics, c.file_path
                   FROM fts_conversations f
                   JOIN conversations c ON c.id = f.rowid
                   WHERE fts_conversations MATCH ?
                   ORDER BY c.timestamp DESC LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "type": "conversation",
                    "timestamp": r["timestamp"],
                    "summary": (r["summary_short"] or r["summary"])[:100],
                    "key_decisions": json.loads(r["key_decisions"]),
                    "topics": json.loads(r["topics"]),
                    "file_path": r["file_path"],
                })
        except sqlite3.OperationalError:
            pass

    if record_type in ("all", "plan"):
        try:
            rows = conn.execute(
                """SELECT p.id, p.plan_id, p.timestamp, p.goal, p.tasks_json, p.file_path
                   FROM fts_plans f
                   JOIN plans p ON p.id = f.rowid
                   WHERE fts_plans MATCH ?
                   ORDER BY p.timestamp DESC LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            for r in rows:
                tasks = json.loads(r["tasks_json"])
                completed = sum(1 for t in tasks if t.get("status") == "completed")
                pending = sum(1 for t in tasks if t.get("status") != "completed")
                results.append({
                    "type": "plan",
                    "plan_id": r["plan_id"],
                    "timestamp": r["timestamp"],
                    "goal": r["goal"],
                    "completed": completed,
                    "pending": pending,
                    "file_path": r["file_path"],
                })
        except sqlite3.OperationalError:
            pass

    return results


# ---------------------------------------------------------------------------
# Resume context
# ---------------------------------------------------------------------------

def get_resume_context(project_root: Path, focus: str = "") -> dict:
    """Get context for resuming a session.

    Args:
        focus: Optional keyword or plan_id to prioritize relevant results.
    """
    conn = _get_conn(project_root)
    try:
        # Active plans
        plans = conn.execute(
            """SELECT plan_id, timestamp, goal, tasks_json, file_path
               FROM plans ORDER BY timestamp DESC LIMIT 5"""
        ).fetchall()

        active_plans = []
        for p in plans:
            tasks = json.loads(p["tasks_json"])
            has_incomplete = any(t.get("status") != "completed" for t in tasks)
            if has_incomplete:
                completed = sum(1 for t in tasks if t.get("status") == "completed")
                pending = len(tasks) - completed
                active_plans.append({
                    "plan_id": p["plan_id"],
                    "goal": p["goal"],
                    "completed": completed,
                    "pending": pending,
                    "file_path": p["file_path"],
                })

        # Recent conversations - use summary_short when available
        convs = conn.execute(
            """SELECT timestamp, summary, summary_short, key_decisions, topics, file_path
               FROM conversations ORDER BY timestamp DESC LIMIT 5"""
        ).fetchall()

        recent_conversations = []
        focus_lower = focus.lower() if focus else ""
        for c in convs:
            conv_dict = {
                "timestamp": c["timestamp"],
                "summary": c["summary"],
                "summary_short": c["summary_short"] if c["summary_short"] else _make_short_summary(c["summary"]),
                "key_decisions": json.loads(c["key_decisions"]),
                "topics": json.loads(c["topics"]),
                "file_path": c["file_path"],
                "relevance": 0,
            }
            # Compute relevance score if focus is set
            if focus_lower:
                text = (c["summary"] + " " + c["topics"]).lower()
                conv_dict["relevance"] = text.count(focus_lower)
            recent_conversations.append(conv_dict)

        # Sort by relevance (if focus) then by timestamp
        if focus_lower:
            recent_conversations.sort(key=lambda x: (-x["relevance"], x["timestamp"]), reverse=False)
            # Re-sort: high relevance first, then recent
            recent_conversations.sort(key=lambda x: (x["relevance"], x["timestamp"]), reverse=True)

        # Recent execution records grouped by target file/command
        records = conn.execute(
            """SELECT timestamp, tool_name, file_path, command, input_summary, response_summary, record_file
               FROM execution_records ORDER BY timestamp DESC LIMIT 50"""
        ).fetchall()

        # Group by (tool_name, target) and count edits
        grouped: dict[str, dict] = {}
        for r in records:
            target = r["file_path"] or r["command"] or ""
            key = f"{r['tool_name']}:{target}"
            if key not in grouped:
                summary = r["input_summary"] or r["response_summary"] or ""
                # Extract last action description
                last_action = summary[:100] if summary else target
                grouped[key] = {
                    "timestamp": r["timestamp"],
                    "tool_name": r["tool_name"],
                    "file_path": r["file_path"],
                    "command": r["command"],
                    "count": 1,
                    "last_action": last_action,
                }
            else:
                grouped[key]["count"] += 1
                grouped[key]["timestamp"] = r["timestamp"]

        # Take top 5 groups by most recent timestamp
        recent_records = sorted(
            grouped.values(), key=lambda x: x["timestamp"], reverse=True
        )[:5]

        return {
            "active_plans": active_plans,
            "recent_conversations": recent_conversations,
            "recent_records": recent_records,
        }
    finally:
        conn.close()
