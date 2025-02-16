import asyncio
import os
from collections import defaultdict
from enum import StrEnum
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
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repository import Repository

app = typer.Typer(name="tc", help="Validate action templates", no_args_is_help=True)
console = Console()


class ValidateMode(StrEnum):
    DEFAULT = "default"
    ISOLATED = "isolated"
    DB = "db"


async def validate_action_templates(
    path: Path,
    *,
    mode: ValidateMode,
    ra_service: RegistryActionsService | None = None,
) -> None:
    if mode == ValidateMode.DEFAULT:
        origin = DEFAULT_REGISTRY_ORIGIN
        repo = Repository(origin=origin)
        await repo.load_from_origin()
        # Count udfs
        console.print(
            f"Using '{DEFAULT_REGISTRY_ORIGIN}' as reference repository",
            style="bold blue",
        )
        n_udfs = sum(1 for action in repo.store.values() if action.type == "udf")
        n_templates = sum(
            1 for action in repo.store.values() if action.type == "template"
        )
        console.print(f"Prepared {len(repo.store)} actions:", style="bold blue")
        console.print(f"- {n_udfs} UDFs", style="bold blue")
        console.print(f"- {n_templates} Templates", style="bold blue")
    else:
        origin = f"file://{path.as_posix()}"
        repo = Repository(origin=origin)
        console.print("Using blank repository", style="bold blue")
    if path.is_dir():
        n_loaded = repo.load_template_actions_from_path(path=path, origin=origin)
    else:
        ta = repo.load_template_action_from_file(path, origin)
        if ta:
            n_loaded = 1
        else:
            console.print(
                f"Error: Could not load template action from {path}", style="bold red"
            )
            raise typer.Exit(code=1)
    console.print(
        f"Adding {n_loaded} template actions from {path}. Any incoming actions with the same name will overwrite the existing ones.",
        style="bold blue",
    )
    val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)

    for action_name in sorted(repo.store.keys()):
        action = repo.store[action_name]
        if not action.is_template:
            continue
        if errs := await validate_action_template(
            action,
            repo,
            check_db=mode == ValidateMode.DB,
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


@app.command(name="template", help="Validate action template(s)", no_args_is_help=True)
def template(
    path: Path = typer.Argument(
        help="Path to template YAML file or directory containing templates",
        exists=True,
    ),
    mode: ValidateMode = typer.Option(
        ValidateMode.DEFAULT,
        "--mode",
        help=(
            "Default: Validate actions against actions in `tracecat_registry`. "
            "Isolated: Validate templates only. "
            "Db: Validate templates and actions against a live Tracecat DB."
        ),
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

    async def main():
        if mode == ValidateMode.DB:
            from tracecat.api.common import bootstrap_role

            # Needed to override the default database URI used in Docker networking
            config.TRACECAT__DB_URI = db_uri

            console.print(
                f"Checking against database at '{db_uri}'.",
                style="bold blue",
            )
            async with RegistryActionsService.with_session(
                role=bootstrap_role()
            ) as service:
                await validate_action_templates(path, mode=mode, ra_service=service)
        else:
            console.print("Skipping database check", style="bold blue")
            await validate_action_templates(path, mode=mode)

    try:
        asyncio.run(main())
    except Exception as e:
        # Suppress stack trace and exit with error code 1
        if "nodename nor servname provided, or not known" in str(e):
            console.print(
                "The database URI is invalid. Please check your TRACECAT__DB_URI environment variable.",
                style="bold red",
            )
        else:
            console.print(f"Error: {e}", style="bold red")
        raise typer.Exit(code=1) from e
