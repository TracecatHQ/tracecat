import asyncio
import os
from collections import defaultdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from tracecat import config
from tracecat.registry.actions.models import RegistryActionValidationErrorInfo
from tracecat.registry.actions.service import (
    RegistryActionsService,
    validate_action_template,
)
from tracecat.registry.repository import Repository

app = typer.Typer(name="tc", help="Validate action templates", no_args_is_help=True)
console = Console()


async def validate_action_templates(
    path: Path,
    *,
    check_db: bool = False,
    ra_service: RegistryActionsService | None = None,
) -> None:
    origin = f"file://{path.as_posix()}"
    repo = Repository(origin=origin)
    if path.is_dir():
        n_loaded = repo.load_template_actions_from_path(path=path, origin=origin)
    else:
        repo.load_template_action_from_file(path, origin)
        n_loaded = 1
    console.print(f"Loaded {n_loaded} template actions from {path}", style="bold blue")
    val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)

    for action in sorted(repo.store.values(), key=lambda a: a.action):
        if not action.is_template:
            continue
        if errs := await validate_action_template(
            action,
            repo,
            check_db=check_db,
            ra_service=ra_service,
        ):
            val_errs[action.action].extend(errs)
            console.print(
                f"✗ {action.action} ({len(errs)} errors)",
                style="bold red",
            )
        else:
            console.print(f"✓ {action.action}", style="bold green")
    if val_errs:
        # Show this in a table
        table = Table(title="Validation Errors")
        table.add_column("Action")
        table.add_column("Details")
        table.add_column("Location [dim](Details)[/dim]")
        for action, errs in val_errs.items():
            for err in errs:
                table.add_row(
                    action,
                    "\n".join(err.details),
                    f"{err.loc_primary} [dim]({err.loc_secondary})[/dim]"
                    if err.loc_secondary
                    else err.loc_primary,
                )
        console.print(table)
    else:
        console.print("No validation errors found", style="bold green")


@app.command(name="template", help="Validate action template(s)")
def template(
    path: Path = typer.Argument(
        help="Path to template YAML file or directory containing templates",
        exists=True,
    ),
    check_db: bool = typer.Option(
        False,
        "--db",
        help="Check against the database to ensure the action is registered",
    ),
    db_uri: str = typer.Option(
        lambda: os.getenv(
            "TRACECAT__DB_URI",
            # Points to a locally running Tracecat DB
            "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        ),
        help="The database URI to use for validation. Defaults to TRACECAT__DB_URI if set, otherwise points to a locally running Tracecat DB.",
    ),
) -> None:
    """Validate action template YAML files."""

    # Needed to override the default database URI used in Docker networking
    config.TRACECAT__DB_URI = db_uri

    async def main():
        if check_db:
            from tracecat.api.common import bootstrap_role

            console.print(
                f"Checking against database at '{db_uri}'.",
                style="bold blue",
            )
            async with RegistryActionsService.with_session(
                role=bootstrap_role()
            ) as service:
                await validate_action_templates(path, check_db=True, ra_service=service)
        else:
            console.print(
                "Skipping database check.",
                style="bold blue",
            )
            await validate_action_templates(path, check_db=False)

    try:
        asyncio.run(main())
    except Exception as e:
        # Suppress stack trace and exit with error code 1
        if "nodename nor servname provided, or not known" in str(e):
            console.print(
                "The database URI is invalid. Please check your TRACECAT__DB_URI environment variable.",
                style="bold red",
            )
        raise typer.Exit(code=1) from e
