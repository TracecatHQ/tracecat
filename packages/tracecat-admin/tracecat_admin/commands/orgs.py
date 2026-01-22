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
    print_org_registry_repositories_table,
    print_org_registry_sync_result,
    print_org_registry_versions_table,
    print_org_version_promote_result,
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


# Org Registry subcommand group
registry_app = typer.Typer(no_args_is_help=True)
app.add_typer(registry_app, name="registry", help="Organization registry management.")


@registry_app.command("list")
@async_command
async def list_org_repositories(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List registry repositories for an organization.

    If org_id is not provided, shows interactive selection.
    """
    try:
        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            repos = await client.list_org_repositories(org_id)

        if json_output:
            import json

            typer.echo(json.dumps([r.model_dump(mode="json") for r in repos], indent=2))
        else:
            if not repos:
                typer.echo("No registry repositories found for this organization.")
            else:
                print_org_registry_repositories_table(repos)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@registry_app.command("versions")
@async_command
async def list_org_repository_versions(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    repository_id: Annotated[
        str | None, typer.Argument(help="Repository ID (UUID)")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List versions for a specific repository in an organization.

    If org_id and repository_id are not provided, shows interactive selection.
    """
    try:
        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            # Interactive repo selection if not provided
            if repository_id is None:
                repos = await client.list_org_repositories(org_id)
                if not repos:
                    print_error("No repositories found for this organization.")
                    raise typer.Exit(1)

                items = [(repo.origin, str(repo.id)) for repo in repos]
                repository_id = _select_from_list("Select repository number", items)
                if repository_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            versions = await client.list_org_repository_versions(org_id, repository_id)

        if json_output:
            import json

            typer.echo(
                json.dumps([v.model_dump(mode="json") for v in versions], indent=2)
            )
        else:
            if not versions:
                typer.echo("No versions found for this repository.")
            else:
                print_org_registry_versions_table(versions)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@registry_app.command("select-version")
@async_command
async def select_org_repository_version(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    repository_id: Annotated[
        str | None, typer.Argument(help="Repository ID (UUID)")
    ] = None,
    version_id: Annotated[str | None, typer.Argument(help="Version ID (UUID)")] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Select a version to be the current version for a repository.

    If org_id, repository_id, and version_id are not provided, shows interactive selection.
    """
    try:
        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            # Interactive repo selection if not provided
            if repository_id is None:
                repos = await client.list_org_repositories(org_id)
                if not repos:
                    print_error("No repositories found for this organization.")
                    raise typer.Exit(1)

                items = [(repo.origin, str(repo.id)) for repo in repos]
                repository_id = _select_from_list("Select repository number", items)
                if repository_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            # Interactive version selection if not provided
            if version_id is None:
                versions = await client.list_org_repository_versions(
                    org_id, repository_id
                )
                if not versions:
                    print_error("No versions found for this repository.")
                    raise typer.Exit(1)

                # Find current version to mark it and display it
                repos = await client.list_org_repositories(org_id)
                current_version = None
                current_repo = None
                for repo in repos:
                    if str(repo.id) == repository_id:
                        current_repo = repo
                        if repo.current_version_id:
                            current_version = next(
                                (
                                    v
                                    for v in versions
                                    if str(v.id) == str(repo.current_version_id)
                                ),
                                None,
                            )
                        break

                # Display current version info
                if current_repo:
                    typer.echo(f"\nRepository: {current_repo.origin}")
                    if current_version:
                        sha = (
                            current_version.commit_sha[:8]
                            if current_version.commit_sha
                            else "no sha"
                        )
                        typer.echo(
                            f"Current version: {current_version.version} ({sha})"
                        )
                    else:
                        typer.echo("Current version: none")
                    typer.echo("")

                items = []
                for v in versions:
                    label = f"{v.version} ({v.commit_sha[:8] if v.commit_sha else 'no sha'})"
                    if (
                        current_version is not None
                        and v.version == current_version.version
                    ):
                        label += " [bold green]← current[/bold green]"
                    items.append((label, str(v.id)))

                version_id = _select_from_list("Select version number", items)
                if version_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            result = await client.promote_org_repository_version(
                org_id, repository_id, version_id
            )

        if json_output:
            import json

            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print_org_version_promote_result(result)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


def _select_from_list(prompt: str, items: list[tuple[str, str]]) -> str | None:
    """Interactive selection using numbered list.

    Args:
        prompt: The prompt to display
        items: List of (display_text, value) tuples

    Returns:
        Selected value or None if cancelled
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4)
    table.add_column("Option")

    for i, (display, _) in enumerate(items, 1):
        table.add_row(str(i), display)

    console.print(table)

    while True:
        try:
            choice = typer.prompt(prompt, default="1")
            if choice.lower() in ("q", "quit", "exit"):
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx][1]
            typer.echo(f"Please enter a number between 1 and {len(items)}")
        except ValueError:
            typer.echo("Please enter a valid number (or 'q' to quit)")


@registry_app.command("sync")
@async_command
async def sync_org_repository(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    repository_id: Annotated[
        str | None, typer.Argument(help="Repository ID (UUID)")
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
    """Sync a registry repository for an organization.

    If org_id and repository_id are not provided, shows interactive selection.
    """
    try:
        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            # Interactive repo selection if not provided
            if repository_id is None:
                repos = await client.list_org_repositories(org_id)
                if not repos:
                    print_error("No repositories found for this organization.")
                    raise typer.Exit(1)

                items = [(repo.origin, str(repo.id)) for repo in repos]
                repository_id = _select_from_list("Select repository number", items)
                if repository_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            # Force confirmation
            if force and not json_output:
                confirm = typer.confirm(
                    "Force sync will delete the existing version and re-sync. Continue?"
                )
                if not confirm:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            from rich.console import Console

            console = Console()
            with console.status("[bold green]Syncing repository...") as status:
                if force:
                    status.update(
                        "[bold yellow]Force sync: deleting existing version..."
                    )
                result = await client.sync_org_repository(
                    org_id, repository_id, force=force
                )

        if json_output:
            import json

            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print_org_registry_sync_result(result)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
