"""Click CLI: init, status, serve, compress."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from . import config
from .storage import init_db


def _get_templates_dir() -> Path:
    """Get the templates directory bundled with the package."""
    # When installed, templates are next to the package src
    # In dev: D:/claudecode/ahahooh/templates/
    # In install: <site-packages>/ahahooh-*.data/... or use importlib
    try:
        import ahahooh
        pkg_dir = Path(ahahooh.__file__).parent
        # Try going up to find templates
        candidate = pkg_dir.parent.parent.parent / "templates"
        if candidate.is_dir():
            return candidate
    except Exception:
        pass
    # Fallback: relative to this file
    return Path(__file__).parent.parent.parent / "templates"


def _init_directories(project_root: Path) -> None:
    """Create .ahahooh directory structure."""
    dirs = [
        config.get_data_dir(project_root),
        config.get_records_dir(project_root),
        config.get_conversations_dir(project_root),
        config.get_plans_dir(project_root),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def _init_database(project_root: Path) -> None:
    """Initialize the SQLite database."""
    db_path = config.get_db_path(project_root)
    init_db(db_path)


def _generate_mcp_json(project_root: Path) -> None:
    """Generate .mcp.json for MCP server registration."""
    mcp_path = project_root / config.MCP_JSON
    mcp_config = {
        "mcpServers": {
            "ahahooh": {
                "command": "python",
                "args": ["-m", "ahahooh.server"],
            }
        }
    }
    if mcp_path.exists():
        existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        existing.setdefault("mcpServers", {})["ahahooh"] = mcp_config["mcpServers"]["ahahooh"]
        mcp_config = existing
    mcp_path.write_text(json.dumps(mcp_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _init_hooks(project_root: Path) -> None:
    """Configure hooks in .claude/settings.local.json."""
    claude_dir = project_root / config.CLAUDE_DIR
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / config.SETTINGS_LOCAL

    hook_config = {
        "hooks": {
            "PostToolUse": [{
                "matcher": "Write|Edit|Bash",
                "hooks": [{
                    "type": "command",
                    "command": "python -m ahahooh.hook_handler"
                }]
            }],
            "Stop": [{
                "hooks": [{
                    "type": "command",
                    "command": "python -m ahahooh.hook_handler --stop"
                }]
            }]
        },
        "env": {
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"
        }
    }

    if settings_path.exists():
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
        # Merge hooks
        if "hooks" not in existing:
            existing["hooks"] = {}
        existing["hooks"].update(hook_config["hooks"])
        # Merge env
        if "env" not in existing:
            existing["env"] = {}
        existing["env"].update(hook_config["env"])
        hook_config = existing
    else:
        settings_path.write_text(json.dumps(hook_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    settings_path.write_text(json.dumps(hook_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _inject_claude_md(project_root: Path) -> None:
    """Append ahahooh instructions to CLAUDE.md."""
    claude_dir = project_root / config.CLAUDE_DIR
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md_path = claude_dir / config.CLAUDE_MD

    templates_dir = _get_templates_dir()
    fragment_path = templates_dir / "claude_md_fragment.md"

    if not fragment_path.exists():
        click.echo("Warning: CLAUDE.md fragment template not found, skipping", err=True)
        return

    fragment = fragment_path.read_text(encoding="utf-8")

    if claude_md_path.exists():
        existing = claude_md_path.read_text(encoding="utf-8")
        if "Ahahooh Memory System" in existing:
            click.echo("  CLAUDE.md already contains Ahahooh instructions, skipping")
            return
        claude_md_path.write_text(existing.rstrip() + "\n" + fragment, encoding="utf-8")
    else:
        claude_md_path.write_text(fragment.lstrip("\n"), encoding="utf-8")


def _init_commands(project_root: Path) -> None:
    """Create slash command files in .claude/commands/."""
    claude_dir = project_root / config.CLAUDE_DIR
    commands_dir = claude_dir / config.COMMANDS_DIR
    commands_dir.mkdir(parents=True, exist_ok=True)

    templates_dir = _get_templates_dir()
    template_commands = templates_dir / config.COMMANDS_DIR

    if not template_commands.is_dir():
        click.echo("Warning: Command templates not found, skipping", err=True)
        return

    for cmd_file in template_commands.glob("*.md"):
        dest = commands_dir / cmd_file.name
        if not dest.exists():
            shutil.copy2(cmd_file, dest)


@click.group()
def cli():
    """Ahahooh - Claude Code memory enhancement tool."""
    pass


@cli.command()
def init():
    """Initialize ahahooh in the current project directory."""
    project_root = Path.cwd()

    click.echo(f"Initializing Ahahooh in {project_root} ...")

    # 1. Create directory structure
    _init_directories(project_root)
    click.echo("  Created .ahahooh/data/ directories")

    # 2. Initialize database
    _init_database(project_root)
    click.echo("  Initialized SQLite database with FTS5")

    # 3. Generate .mcp.json
    _generate_mcp_json(project_root)
    click.echo("  Generated .mcp.json")

    # 4. Configure hooks
    _init_hooks(project_root)
    click.echo("  Configured hooks in .claude/settings.local.json")

    # 5. Inject CLAUDE.md
    _inject_claude_md(project_root)
    click.echo("  Injected instructions into .claude/CLAUDE.md")

    # 6. Create slash commands
    _init_commands(project_root)
    click.echo("  Created slash commands in .claude/commands/")

    click.echo("")
    click.echo("Done! Launching Claude Code ...")
    sys.stdout.flush()
    try:
        subprocess.run(["claude"], cwd=str(project_root), shell=True)
    except FileNotFoundError:
        click.echo("Error: 'claude' command not found. Please install Claude Code CLI first.")
    except KeyboardInterrupt:
        pass


@cli.command()
def status():
    """Show ahahooh status for the current project."""
    project_root = config.find_project_root()
    if project_root is None:
        click.echo("Ahahooh is not initialized in this project.")
        click.echo("Run 'ahahooh init' in the project root directory.")
        return

    click.echo(f"Project root: {project_root}")

    # Check database
    db_path = config.get_db_path(project_root)
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        records_count = conn.execute("SELECT COUNT(*) FROM execution_records").fetchone()[0]
        convs_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        plans_count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        conn.close()
        click.echo(f"Database: {records_count} records, {convs_count} conversations, {plans_count} plans")
    else:
        click.echo("Database: not found")

    # Check index
    index_path = config.get_index_path(project_root)
    if index_path.exists():
        click.echo(f"Index: {index_path.stat().st_size} bytes")
    else:
        click.echo("Index: not built (run 'ahahooh compress')")

    # Check MCP
    mcp_path = project_root / config.MCP_JSON
    click.echo(f"MCP config: {'configured' if mcp_path.exists() else 'missing'}")

    # Check hooks
    settings_path = project_root / config.CLAUDE_DIR / config.SETTINGS_LOCAL
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        has_hooks = "hooks" in settings and "PostToolUse" in settings.get("hooks", {})
        click.echo(f"Hooks: {'configured' if has_hooks else 'missing'}")
    else:
        click.echo("Hooks: missing")


@cli.command()
def serve():
    """Start the Ahahooh MCP server."""
    from .server import mcp
    mcp.run()


@cli.command()
def compress():
    """Rebuild the compressed index from stored data."""
    project_root = config.find_project_root()
    if project_root is None:
        click.echo("Error: Ahahooh is not initialized. Run 'ahahooh init' first.")
        sys.exit(1)

    from .index import build_index
    build_index(project_root)
    click.echo("Index rebuilt successfully.")


if __name__ == "__main__":
    cli()
