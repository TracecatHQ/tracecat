import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from tracecat.registry.actions.models import RegistryActionValidationErrorInfo
from tracecat.registry.actions.service import validate_action_template
from tracecat.registry.repository import Repository

app = typer.Typer(name="tc", help="Validate action templates", no_args_is_help=True)
console = Console()


async def validate_action_templates(path: Path, check_db: bool = False) -> None:
    # We need bound registry action
    origin = f"file://{path.as_posix()}"
    repo = Repository(origin=origin)
    if path.is_dir():
        n_loaded = repo.load_template_actions_from_path(path=path, origin=origin)
    else:
        repo.load_template_action_from_file(path, origin)
        n_loaded = 1
    console.print(f"Loaded {n_loaded} template actions from {path}", style="bold green")
    # Show the loaded actions
    for action in repo.store.keys():
        console.print(f"  {action}", style="bold yellow")
    console.print(f"Checking db: {check_db}", style="bold yellow")
    val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)
    for action in repo.store.values():
        if not action.is_template:
            continue
        if errs := await validate_action_template(action, repo):
            val_errs[action.action].extend(errs)
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
    path: Annotated[
        Path,
        typer.Argument(
            help="Path to template YAML file or directory containing templates",
            exists=True,
        ),
    ],
    check_db: Annotated[
        bool,
        typer.Option(
            "--db",
            help="Check against the database to ensure the action is registered",
        ),
    ] = False,
) -> None:
    """Validate action template YAML files."""
    asyncio.run(validate_action_templates(path, check_db=check_db))
