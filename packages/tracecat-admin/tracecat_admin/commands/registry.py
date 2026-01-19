"""Registry management commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    print_error,
    print_registry_status,
    print_registry_versions,
    print_sync_result,
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
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Trigger registry sync for all or a specific repository."""
    try:
        async with AdminClient() as client:
            result = await client.sync_registry(repository_id=repository_id)

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
