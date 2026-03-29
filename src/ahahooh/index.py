"""Compressed index generation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config


def build_index(project_root: Path) -> None:
    """Build the compressed index.md from stored data."""
    db_path = config.get_db_path(project_root)
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")

    lines.append("# Ahahooh Memory Index")
    lines.append(f"Updated: {now}")
    lines.append("")

    # Active plans
    lines.append("## Active Plans")
    lines.append("")
    plans = conn.execute(
        "SELECT plan_id, goal, tasks_json, file_path FROM plans ORDER BY timestamp DESC"
    ).fetchall()

    has_plans = False
    for p in plans:
        tasks = json.loads(p["tasks_json"])
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        pending = len(tasks) - completed
        if pending > 0:
            has_plans = True
            lines.append(f"- [{p['plan_id']}] \"{p['goal']}\" - {pending} pending, {completed} completed -> {p['file_path']}")

    if not has_plans:
        lines.append("- (none)")
    lines.append("")

    # Recent conversations (5)
    lines.append("## Recent Conversations (5)")
    lines.append("")
    convs = conn.execute(
        "SELECT timestamp, summary, file_path FROM conversations ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    if convs:
        for c in convs:
            ts = c["timestamp"][:16].replace("T", " ")
            # Extract date part for short display
            date_part = ts[:10]
            summary = c["summary"]
            # Extract intent from structured format
            if summary.startswith("Intent: "):
                display = summary[8:].split(" | ")[0]
            else:
                display = summary
            display = display[:80] + ("..." if len(display) > 80 else "")
            lines.append(f"- [{date_part}] {display} -> {c['file_path']}")
    else:
        lines.append("- (none)")
    lines.append("")

    # Recent execution records (10)
    lines.append("## Recent Executions (10)")
    lines.append("")
    records = conn.execute(
        """SELECT timestamp, tool_name, file_path, command, input_summary, record_file
           FROM execution_records ORDER BY timestamp DESC LIMIT 10"""
    ).fetchall()

    if records:
        for r in records:
            ts = r["timestamp"][:16].replace("T", " ")
            date_part = ts[:10]
            tool = r["tool_name"]
            desc = ""
            if r["file_path"]:
                desc = r["file_path"]
            elif r["command"]:
                desc = r["command"][:60]
            elif r["input_summary"]:
                desc = r["input_summary"][:60]
            lines.append(f"- [{date_part}] {tool}: {desc} -> {r['record_file']}")
    else:
        lines.append("- (none)")
    lines.append("")

    conn.close()

    # Write index
    index_path = config.get_index_path(project_root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines), encoding="utf-8")
