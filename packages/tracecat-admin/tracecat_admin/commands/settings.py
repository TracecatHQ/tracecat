"""Platform settings commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import print_error, print_success

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@app.command("get")
@async_command
async def get_settings(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get platform registry settings."""
    try:
        async with AdminClient() as client:
            settings = await client.get_registry_settings()

        if json_output:
            import json

            typer.echo(json.dumps(settings.model_dump(mode="json"), indent=2))
        else:
            from rich.table import Table

            from tracecat_admin.output import console

            table = Table(title="Registry Settings", show_header=False)
            table.add_column("Setting", style="dim")
            table.add_column("Value")

            table.add_row("Git Repo URL", settings.git_repo_url or "-")
            table.add_row("Git Repo Package", settings.git_repo_package_name or "-")
            domains = (
                ", ".join(sorted(settings.git_allowed_domains))
                if settings.git_allowed_domains
                else "-"
            )
            table.add_row("Allowed Domains", domains)

            console.print(table)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("update")
@async_command
async def update_settings(
    git_repo_url: Annotated[
        str | None,
        typer.Option("--git-repo-url", help="Git repository URL for remote registry"),
    ] = None,
    git_repo_package_name: Annotated[
        str | None,
        typer.Option("--git-repo-package", help="Package name in the git repository"),
    ] = None,
    git_allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domain",
            help="Allowed git domains (can be specified multiple times)",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Update platform registry settings."""
    if (
        git_repo_url is None
        and git_repo_package_name is None
        and git_allowed_domains is None
    ):
        print_error(
            "At least one of --git-repo-url, --git-repo-package, or --allowed-domain is required"
        )
        raise typer.Exit(1)

    try:
        async with AdminClient() as client:
            domains_set = set(git_allowed_domains) if git_allowed_domains else None
            settings = await client.update_registry_settings(
                git_repo_url=git_repo_url,
                git_repo_package_name=git_repo_package_name,
                git_allowed_domains=domains_set,
            )

        if json_output:
            import json

            typer.echo(json.dumps(settings.model_dump(mode="json"), indent=2))
        else:
            print_success("Registry settings updated")
            from rich.table import Table

            from tracecat_admin.output import console

            table = Table(title="Registry Settings", show_header=False)
            table.add_column("Setting", style="dim")
            table.add_column("Value")

            table.add_row("Git Repo URL", settings.git_repo_url or "-")
            table.add_row("Git Repo Package", settings.git_repo_package_name or "-")
            domains = (
                ", ".join(sorted(settings.git_allowed_domains))
                if settings.git_allowed_domains
                else "-"
            )
            table.add_row("Allowed Domains", domains)

            console.print(table)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
