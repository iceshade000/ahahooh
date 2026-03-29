"""Scan ~/.claude/plans/ for plan files not yet captured by ahahooh.

Claude Code's plan mode writes to ~/.claude/plans/ directly, bypassing the
Write tool, so the PostToolUse hook never fires.  This module is called from
the Stop hook and get_resume_context to pick up those missed plans.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config
from .hook_handler import _extract_plan_from_content
from .storage import save_plan


def _get_existing_planmode_ids(project_root: Path) -> set[str]:
    """Return plan_ids already in the database that start with 'planmode_'."""
    db_path = config.get_db_path(project_root)
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT plan_id FROM plans WHERE plan_id LIKE 'planmode_%'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def sync_plans(project_root: Path) -> int:
    """Scan ~/.claude/plans/ for uncaptured plan files. Returns count of newly synced."""
    plans_dir = config.get_claude_plans_dir()
    if not plans_dir.is_dir():
        return 0

    existing = _get_existing_planmode_ids(project_root)
    count = 0

    for md_file in sorted(plans_dir.glob("*.md")):
        plan_id = f"planmode_{md_file.stem}"
        if plan_id in existing:
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        if not content.strip():
            continue

        plan = _extract_plan_from_content(content)
        save_plan(
            project_root=project_root,
            goal=plan["goal"],
            tasks=plan["tasks"],
            plan_id=plan_id,
        )
        count += 1

    return count
