"""Registry management commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    format_datetime,
    print_error,
    print_registry_status,
    print_registry_versions,
    print_sync_result,
    print_version_promote_result,
)

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@app.command("sync")
@async_command
async def sync_registry(
    repository_id: Annotated[
        str | None,
        typer.Option(
            "--repository-id",
            "-r",
            help="Specific repository ID to sync (syncs all if not specified)",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force", "-f", help="Force sync by deleting existing version first"
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Trigger registry sync for all or a specific repository."""
    if force and not json_output:
        confirm = typer.confirm(
            "Force sync will delete the existing version and re-sync. Continue?"
        )
        if not confirm:
            typer.echo("Operation cancelled.")
            raise typer.Exit(0)

    try:
        async with AdminClient() as client:
            result = await client.sync_registry(
                repository_id=repository_id, force=force
            )

        if json_output:
            import json

            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print_sync_result(result)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("status")
@async_command
async def registry_status(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get registry sync status and health."""
    try:
        async with AdminClient() as client:
            status = await client.get_registry_status()

        if json_output:
            import json

            typer.echo(json.dumps(status.model_dump(mode="json"), indent=2))
        else:
            print_registry_status(status)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("versions")
@async_command
async def list_versions(
    repository_id: Annotated[
        str | None,
        typer.Option("--repository-id", "-r", help="Filter by repository ID"),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="Maximum number of versions to show")
    ] = 50,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List registry versions."""
    try:
        async with AdminClient() as client:
            versions = await client.list_registry_versions(
                repository_id=repository_id, limit=limit
            )

        if json_output:
            import json

            typer.echo(
                json.dumps([v.model_dump(mode="json") for v in versions], indent=2)
            )
        else:
            if not versions:
                typer.echo("No versions found.")
            else:
                print_registry_versions(versions)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("select-version")
@async_command
async def select_version() -> None:
    """Interactively select and promote a registry version."""
    console = Console()

    try:
        async with AdminClient() as client:
            # Step 1: Fetch repositories
            status = await client.get_registry_status()

            if not status.repositories:
                typer.echo("No repositories found.")
                raise typer.Exit(0)

            # Step 2: Display numbered list of repositories
            console.print("\n[bold]Repositories:[/bold]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("#", style="dim", width=4)
            table.add_column("Origin", style="cyan")
            table.add_column("Last Synced")
            table.add_column("Commit SHA", style="dim")

            for i, repo in enumerate(status.repositories, 1):
                sha = repo.commit_sha[:8] if repo.commit_sha else "-"
                table.add_row(
                    f"[{i}]",
                    repo.origin,
                    format_datetime(repo.last_synced_at),
                    sha,
                )

            console.print(table)

            # Step 3: Prompt user to select repository
            repo_choice = typer.prompt(
                f"\nSelect repository [1-{len(status.repositories)}]",
                type=int,
            )

            if repo_choice < 1 or repo_choice > len(status.repositories):
                print_error(
                    f"Invalid selection. Please choose between 1 and {len(status.repositories)}"
                )
                raise typer.Exit(1)

            selected_repo = status.repositories[repo_choice - 1]

            # Step 4: Fetch versions for selected repository
            versions = await client.list_registry_versions(
                repository_id=str(selected_repo.id), limit=50
            )

            if not versions:
                typer.echo(f"No versions found for repository '{selected_repo.origin}'")
                raise typer.Exit(0)

            # Step 5: Display numbered list of versions
            console.print(f"\n[bold]Versions for '{selected_repo.origin}':[/bold]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("#", style="dim", width=4)
            table.add_column("Version", style="cyan")
            table.add_column("Commit SHA", style="magenta")
            table.add_column("Created")
            table.add_column("Status")

            for i, version in enumerate(versions, 1):
                sha = version.commit_sha[:8] if version.commit_sha else "-"
                is_current = (
                    selected_repo.current_version_id is not None
                    and version.id == selected_repo.current_version_id
                )
                status_text = "[green]CURRENT[/green]" if is_current else ""
                table.add_row(
                    f"[{i}]",
                    version.version,
                    sha,
                    format_datetime(version.created_at),
                    status_text,
                )

            console.print(table)

            # Step 6: Prompt user to select version
            version_choice = typer.prompt(
                f"\nSelect version [1-{len(versions)}]",
                type=int,
            )

            if version_choice < 1 or version_choice > len(versions):
                print_error(
                    f"Invalid selection. Please choose between 1 and {len(versions)}"
                )
                raise typer.Exit(1)

            selected_version = versions[version_choice - 1]

            # Check if already current
            if (
                selected_repo.current_version_id is not None
                and selected_version.id == selected_repo.current_version_id
            ):
                typer.echo(
                    f"Version '{selected_version.version}' is already the current version."
                )
                raise typer.Exit(0)

            # Step 7: Confirm selection
            confirm = typer.confirm(
                f"\nPromote version '{selected_version.version}' to current?"
            )

            if not confirm:
                typer.echo("Operation cancelled.")
                raise typer.Exit(0)

            # Step 8: Call promote endpoint
            result = await client.promote_registry_version(
                repository_id=str(selected_repo.id),
                version_id=str(selected_version.id),
            )

            # Step 9: Display result
            console.print()
            print_version_promote_result(result)

    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
