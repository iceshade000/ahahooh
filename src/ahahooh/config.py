"""Path constants and project root finder."""

from pathlib import Path

AHAHOOH_DIR = ".ahahooh"
DATA_DIR = "data"
DB_NAME = "db.sqlite"
RECORDS_DIR = "records"
CONVERSATIONS_DIR = "conversations"
PLANS_DIR = "plans"
INDEX_FILE = "index.md"

CLAUDE_DIR = ".claude"
CLAUDE_MD = "CLAUDE.md"
SETTINGS_LOCAL = "settings.local.json"
MCP_JSON = ".mcp.json"
COMMANDS_DIR = "commands"


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from start directory to find project root with .ahahooh."""
    current = start or Path.cwd()
    while current != current.parent:
        if (current / AHAHOOH_DIR).is_dir():
            return current
        current = current.parent
    # Check root as well
    if (current / AHAHOOH_DIR).is_dir():
        return current
    return None


def get_ahahooh_dir(project_root: Path) -> Path:
    """Get .ahahooh directory path."""
    return project_root / AHAHOOH_DIR


def get_data_dir(project_root: Path) -> Path:
    """Get .ahahooh/data directory path."""
    return get_ahahooh_dir(project_root) / DATA_DIR


def get_db_path(project_root: Path) -> Path:
    """Get database file path."""
    return get_data_dir(project_root) / DB_NAME


def get_records_dir(project_root: Path) -> Path:
    return get_data_dir(project_root) / RECORDS_DIR


def get_conversations_dir(project_root: Path) -> Path:
    return get_data_dir(project_root) / CONVERSATIONS_DIR


def get_plans_dir(project_root: Path) -> Path:
    return get_data_dir(project_root) / PLANS_DIR


def get_index_path(project_root: Path) -> Path:
    return get_data_dir(project_root) / INDEX_FILE
