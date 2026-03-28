"""Data models for ahahooh records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExecutionRecord:
    """A single tool execution record."""
    timestamp: str
    tool_name: str
    file_path: str = ""
    command: str = ""
    input_summary: str = ""
    response_summary: str = ""
    record_file: str = ""
    session_id: str = ""


@dataclass
class Conversation:
    """A conversation summary."""
    timestamp: str
    summary: str
    key_decisions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    file_path: str = ""
    session_id: str = ""


@dataclass
class Plan:
    """A plan with tasks."""
    plan_id: str
    timestamp: str
    goal: str
    tasks: list[PlanTask] = field(default_factory=list)
    file_path: str = ""
    session_id: str = ""


@dataclass
class PlanTask:
    """A task within a plan."""
    description: str
    status: str = "pending"  # pending | in_progress | completed | blocked
