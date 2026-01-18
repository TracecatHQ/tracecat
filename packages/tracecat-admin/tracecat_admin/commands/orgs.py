"""Organization management commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    print_error,
    print_org_detail,
    print_orgs_table,
    print_success,
)

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@app.command("list")
@async_command
async def list_orgs(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List all organizations."""
    try:
        async with AdminClient() as client:
            orgs = await client.list_organizations()

        if json_output:
            import json

            typer.echo(json.dumps([o.model_dump(mode="json") for o in orgs], indent=2))
        else:
            if not orgs:
                typer.echo("No organizations found.")
            else:
                print_orgs_table(orgs)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("create")
@async_command
async def create_org(
    name: Annotated[str, typer.Option("--name", "-n", help="Organization name")],
    slug: Annotated[
        str,
        typer.Option(
            "--slug",
            "-s",
            help="Organization slug (lowercase alphanumeric with hyphens)",
        ),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Create a new organization."""
    try:
        async with AdminClient() as client:
            org = await client.create_organization(name=name, slug=slug)

        if json_output:
            import json

            typer.echo(json.dumps(org.model_dump(mode="json"), indent=2))
        else:
            print_success(f"Organization '{name}' created successfully")
            print_org_detail(org)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("get")
@async_command
async def get_org(
    org_id: Annotated[str, typer.Argument(help="Organization ID (UUID)")],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get details for a specific organization."""
    try:
        async with AdminClient() as client:
            org = await client.get_organization(org_id)

        if json_output:
            import json

            typer.echo(json.dumps(org.model_dump(mode="json"), indent=2))
        else:
            print_org_detail(org)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("update")
@async_command
async def update_org(
    org_id: Annotated[str, typer.Argument(help="Organization ID (UUID)")],
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="New organization name")
    ] = None,
    slug: Annotated[
        str | None,
        typer.Option(
            "--slug",
            "-s",
            help="New organization slug (lowercase alphanumeric with hyphens)",
        ),
    ] = None,
    active: Annotated[
        bool | None,
        typer.Option("--active/--inactive", help="Set organization active status"),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Update an organization."""
    if name is None and slug is None and active is None:
        print_error(
            "At least one of --name, --slug, or --active/--inactive is required"
        )
        raise typer.Exit(1)

    try:
        async with AdminClient() as client:
            org = await client.update_organization(
                org_id, name=name, slug=slug, is_active=active
            )

        if json_output:
            import json

            typer.echo(json.dumps(org.model_dump(mode="json"), indent=2))
        else:
            print_success(f"Organization '{org.name}' updated successfully")
            print_org_detail(org)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("delete")
@async_command
async def delete_org(
    org_id: Annotated[str, typer.Argument(help="Organization ID (UUID)")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Delete an organization."""
    try:
        async with AdminClient() as client:
            # Get org details for confirmation
            org = await client.get_organization(org_id)

            if not force:
                confirm = typer.confirm(
                    f"Are you sure you want to delete organization '{org.name}' ({org.slug})?"
                )
                if not confirm:
                    typer.echo("Aborted.")
                    raise typer.Exit(0)

            await client.delete_organization(org_id)
            print_success(f"Organization '{org.name}' deleted successfully")
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
