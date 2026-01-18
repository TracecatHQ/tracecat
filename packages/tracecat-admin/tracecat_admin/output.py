"""Output formatting utilities for CLI."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from tracecat_admin.schemas import (
    OrgRead,
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionRead,
    UserRead,
)

console = Console()
error_console = Console(stderr=True)


def format_datetime(dt: str | datetime | None) -> str:
    """Format a datetime string for display."""
    if dt is None:
        return "-"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def print_json(data: Any) -> None:
    """Print data as formatted JSON."""
    console.print_json(json.dumps(data, default=str, indent=2))


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]{message}[/green]")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[red]Error:[/red] {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[yellow]Warning:[/yellow] {message}")


def print_users_table(users: list[UserRead]) -> None:
    """Print users in a table format."""
    table = Table(title="Users")
    table.add_column("ID", style="dim")
    table.add_column("Email", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="magenta")
    table.add_column("Superuser", style="green")
    table.add_column("Active", style="blue")
    table.add_column("Last Login")

    for user in users:
        name = " ".join(filter(None, [user.first_name, user.last_name])) or "-"
        table.add_row(
            str(user.id),
            user.email,
            name,
            user.role,
            "Yes" if user.is_superuser else "No",
            "Yes" if user.is_active else "No",
            format_datetime(user.last_login_at),
        )

    console.print(table)


def print_user_detail(user: UserRead) -> None:
    """Print user details."""
    table = Table(title="User Details", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    name = " ".join(filter(None, [user.first_name, user.last_name])) or "-"

    table.add_row("ID", str(user.id))
    table.add_row("Email", user.email)
    table.add_row("Name", name)
    table.add_row("Role", user.role)
    table.add_row("Superuser", "Yes" if user.is_superuser else "No")
    table.add_row("Active", "Yes" if user.is_active else "No")
    table.add_row("Verified", "Yes" if user.is_verified else "No")
    table.add_row("Last Login", format_datetime(user.last_login_at))
    table.add_row("Created", format_datetime(user.created_at))

    console.print(table)


def print_orgs_table(orgs: list[OrgRead]) -> None:
    """Print organizations in a table format."""
    table = Table(title="Organizations")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Slug", style="magenta")
    table.add_column("Active", style="green")
    table.add_column("Created")

    for org in orgs:
        table.add_row(
            str(org.id),
            org.name,
            org.slug,
            "Yes" if org.is_active else "No",
            format_datetime(org.created_at),
        )

    console.print(table)


def print_org_detail(org: OrgRead) -> None:
    """Print organization details."""
    table = Table(title="Organization Details", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("ID", str(org.id))
    table.add_row("Name", org.name)
    table.add_row("Slug", org.slug)
    table.add_row("Active", "Yes" if org.is_active else "No")
    table.add_row("Created", format_datetime(org.created_at))
    table.add_row("Updated", format_datetime(org.updated_at))

    console.print(table)


def print_registry_status(status: RegistryStatusResponse) -> None:
    """Print registry status."""
    console.print("[bold]Registry Status[/bold]")
    console.print(f"  Total repositories: {status.total_repositories}")
    console.print(f"  Last sync: {format_datetime(status.last_sync_at)}")
    console.print()

    if status.repositories:
        table = Table(title="Repositories")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="cyan")
        table.add_column("Origin", style="magenta")
        table.add_column("Commit SHA", style="dim")
        table.add_column("Last Synced")

        for repo in status.repositories:
            sha = repo.commit_sha or "-"
            if sha != "-":
                sha = sha[:8]  # Truncate SHA
            table.add_row(
                str(repo.id),
                repo.name,
                repo.origin,
                sha,
                format_datetime(repo.last_synced_at),
            )

        console.print(table)


def print_sync_result(result: RegistrySyncResponse) -> None:
    """Print registry sync result."""
    status = "[green]Success[/green]" if result.success else "[red]Failed[/red]"
    console.print(f"Sync status: {status}")
    console.print(f"Synced at: {format_datetime(result.synced_at)}")

    if result.repositories:
        console.print()
        table = Table(title="Sync Results")
        table.add_column("Repository", style="cyan")
        table.add_column("Status")
        table.add_column("Version", style="magenta")
        table.add_column("Actions", style="dim")
        table.add_column("Error")

        for repo in result.repositories:
            status_text = "[green]OK[/green]" if repo.success else "[red]Failed[/red]"
            table.add_row(
                repo.repository_name,
                status_text,
                repo.version or "-",
                str(repo.actions_count) if repo.actions_count is not None else "-",
                repo.error or "-",
            )

        console.print(table)


def print_registry_versions(versions: list[RegistryVersionRead]) -> None:
    """Print registry versions in a table format."""
    table = Table(title="Registry Versions")
    table.add_column("ID", style="dim")
    table.add_column("Repository ID", style="dim")
    table.add_column("Version", style="cyan")
    table.add_column("Commit SHA", style="magenta")
    table.add_column("Created")

    for version in versions:
        sha = version.commit_sha or "-"
        if sha != "-":
            sha = sha[:8]  # Truncate SHA
        table.add_row(
            str(version.id),
            str(version.repository_id),
            version.version,
            sha,
            format_datetime(version.created_at),
        )

    console.print(table)
