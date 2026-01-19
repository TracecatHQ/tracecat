"""Database migration commands."""

from __future__ import annotations

import os
import subprocess
from typing import Annotated

import typer

from tracecat_admin.config import get_config
from tracecat_admin.output import print_error, print_success, print_warning

app = typer.Typer(no_args_is_help=True)


def _get_alembic_dir() -> str | None:
    """Find the alembic directory relative to tracecat installation."""
    try:
        import tracecat

        tracecat_dir = os.path.dirname(tracecat.__file__)
        # Alembic is typically at the repo root, one level up from tracecat package
        repo_root = os.path.dirname(tracecat_dir)
        alembic_dir = os.path.join(repo_root, "alembic")
        if os.path.exists(alembic_dir):
            return repo_root
    except ImportError:
        pass
    return None


def _run_alembic(args: list[str], db_uri: str | None = None) -> int:
    """Run alembic command with proper environment."""
    config = get_config()

    # Use provided db_uri or fall back to config
    effective_db_uri = db_uri or config.db_uri
    if not effective_db_uri:
        print_error(
            "TRACECAT__DB_URI environment variable is required for migration commands"
        )
        return 1

    alembic_dir = _get_alembic_dir()
    if not alembic_dir:
        print_error(
            "Could not find alembic directory. "
            "Ensure tracecat is installed and alembic/ exists."
        )
        return 1

    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = effective_db_uri

    cmd = ["alembic", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=alembic_dir,
            env=env,
            capture_output=False,
        )
        return result.returncode
    except FileNotFoundError:
        print_error("alembic command not found. Install with: pip install alembic")
        return 1


@app.command("upgrade")
def upgrade(
    revision: Annotated[
        str,
        typer.Argument(help="Target revision (use 'head' for latest)"),
    ] = "head",
    db_uri: Annotated[
        str | None,
        typer.Option("--db-uri", help="Database URI (overrides TRACECAT__DB_URI)"),
    ] = None,
) -> None:
    """Upgrade database to a specific revision.

    Examples:
        tracecat migrate upgrade head      # Upgrade to latest
        tracecat migrate upgrade +1        # Upgrade one revision
        tracecat migrate upgrade abc123    # Upgrade to specific revision
    """
    typer.echo(f"Upgrading database to revision: {revision}")
    returncode = _run_alembic(["upgrade", revision], db_uri=db_uri)
    if returncode == 0:
        print_success("Database upgrade completed successfully")
    else:
        print_error("Database upgrade failed")
        raise typer.Exit(returncode)


@app.command("downgrade")
def downgrade(
    revision: Annotated[
        str,
        typer.Argument(help="Target revision (use '-1' for previous)"),
    ],
    db_uri: Annotated[
        str | None,
        typer.Option("--db-uri", help="Database URI (overrides TRACECAT__DB_URI)"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Downgrade database to a specific revision.

    WARNING: This can result in data loss. Use with caution.

    Examples:
        tracecat migrate downgrade -1      # Downgrade one revision
        tracecat migrate downgrade base    # Downgrade to initial state
        tracecat migrate downgrade abc123  # Downgrade to specific revision
    """
    if not yes:
        print_warning("Database downgrade can result in data loss!")
        confirm = typer.confirm("Are you sure you want to proceed?")
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(0)

    typer.echo(f"Downgrading database to revision: {revision}")
    returncode = _run_alembic(["downgrade", revision], db_uri=db_uri)
    if returncode == 0:
        print_success("Database downgrade completed successfully")
    else:
        print_error("Database downgrade failed")
        raise typer.Exit(returncode)


@app.command("status")
def status(
    db_uri: Annotated[
        str | None,
        typer.Option("--db-uri", help="Database URI (overrides TRACECAT__DB_URI)"),
    ] = None,
) -> None:
    """Show current database migration status."""
    returncode = _run_alembic(["current", "-v"], db_uri=db_uri)
    if returncode != 0:
        raise typer.Exit(returncode)


@app.command("history")
def history(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Limit number of revisions shown"),
    ] = None,
    db_uri: Annotated[
        str | None,
        typer.Option("--db-uri", help="Database URI (overrides TRACECAT__DB_URI)"),
    ] = None,
) -> None:
    """Show migration history."""
    args = ["history", "-v"]
    if limit:
        args.extend(["-r", f"-{limit}:"])
    returncode = _run_alembic(args, db_uri=db_uri)
    if returncode != 0:
        raise typer.Exit(returncode)
